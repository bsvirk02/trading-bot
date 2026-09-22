"""
Price data with a primary source, a fallback, retries and validation.

On 19 September the bot died with:

    IndexError: single positional indexer is out-of-bounds

because yfinance returned an empty frame and the code went straight to
close.iloc[-1]. A bad download looked identical to a bad bug. Everything here
exists to make that class of failure loud, retried, and survivable.

Primary source is Kraken's public OHLC endpoint:
  - no authentication, so the bot needs no credential to compute a signal
  - it is the venue the strategy would actually trade, so the signal and the
    execution price come from the same book. yfinance is a scraper of a third
    party's view of a different set of exchanges.
yfinance stays as a fallback for the case where Kraken is unreachable.
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import settings

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
DAILY_INTERVAL_MINUTES = 1440

COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{}/candles"
DAILY_GRANULARITY_SECONDS = 86400

# Coinbase rejects requests without one.
HEADERS = {"User-Agent": "trading-bot/1.0"}

RETRIES = 3
BACKOFF_SECONDS = 2


class DataError(Exception):
    """Raised when no source could supply usable price data."""


def _validate(close: pd.Series, source: str) -> pd.Series:
    """Reject anything the strategy cannot safely be run against."""
    if close is None or len(close) == 0:
        raise DataError("{}: returned no data".format(source))

    if len(close) < settings.MIN_CANDLES:
        raise DataError(
            "{}: only {} candles, need at least {} to compute a {}-day MA".format(
                source, len(close), settings.MIN_CANDLES, settings.SMA_TREND
            )
        )

    tail = close.tail(settings.SMA_TREND)
    if tail.isna().any():
        raise DataError(
            "{}: {} NaN values in the last {} candles".format(
                source, int(tail.isna().sum()), settings.SMA_TREND
            )
        )

    if (tail <= 0).any():
        raise DataError("{}: non-positive prices in recent candles".format(source))

    # Measure staleness from when the newest candle CLOSED, not when it opened.
    newest_open = close.index[-1]
    newest_close = newest_open.to_pydatetime() + timedelta(
        hours=settings.CANDLE_DURATION_HOURS
    )
    age = datetime.now(timezone.utc) - newest_close
    if age > timedelta(hours=settings.MAX_CANDLE_AGE_HOURS):
        raise DataError(
            "{}: newest candle ({}) closed {:.1f}h ago, stale beyond the {}h limit".format(
                source,
                newest_open.date(),
                age.total_seconds() / 3600.0,
                settings.MAX_CANDLE_AGE_HOURS,
            )
        )

    if not close.index.is_monotonic_increasing:
        raise DataError("{}: candles are not in chronological order".format(source))

    return close


def _fetch_kraken() -> pd.Series:
    response = requests.get(
        KRAKEN_OHLC_URL,
        params={"pair": settings.KRAKEN_PAIR, "interval": DAILY_INTERVAL_MINUTES},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("error"):
        raise DataError("kraken: API error {}".format(payload["error"]))

    result = payload.get("result", {})
    key = next((k for k in result if k != "last"), None)
    if key is None:
        raise DataError("kraken: no OHLC series in response")

    rows = result[key]
    index = pd.to_datetime([int(r[0]) for r in rows], unit="s", utc=True)
    close = pd.Series([float(r[4]) for r in rows], index=index, name="close")

    return _drop_forming_candle(close)


def _drop_forming_candle(close: pd.Series) -> pd.Series:
    """Remove the current, still-forming daily candle.

    The backtest runs on completed daily closes. Including a partial candle
    makes the live signal flicker intraday against a strategy that was never
    tested that way.
    """
    if not settings.USE_COMPLETED_CANDLES_ONLY or len(close) <= 1:
        return close
    today = datetime.now(timezone.utc).date()
    if close.index[-1].date() >= today:
        close = close.iloc[:-1]
    return close


def _fetch_coinbase() -> pd.Series:
    """Coinbase Exchange public candles. No auth, max 300 daily candles."""
    response = requests.get(
        COINBASE_CANDLES_URL.format(settings.COINBASE_PRODUCT),
        params={"granularity": DAILY_GRANULARITY_SECONDS},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()

    if not isinstance(rows, list) or not rows:
        raise DataError("coinbase: unexpected payload {}".format(str(rows)[:200]))

    # Coinbase returns [time, low, high, open, close, volume], newest first.
    rows = sorted(rows, key=lambda r: r[0])
    index = pd.to_datetime([int(r[0]) for r in rows], unit="s", utc=True)
    close = pd.Series([float(r[4]) for r in rows], index=index, name="close")

    return _drop_forming_candle(close)


def _fetch_yfinance() -> pd.Series:
    import yfinance as yf

    frame = yf.download(
        settings.YF_TICKER, period="400d", interval="1d", progress=False
    )
    if frame is None or frame.empty:
        raise DataError("yfinance: empty frame")

    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)

    close = frame["Close"].squeeze()
    close.index = pd.to_datetime(close.index, utc=True)
    close.name = "close"

    return _drop_forming_candle(close)


def _with_retries(fetch, source: str) -> pd.Series:
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            return _validate(fetch(), source)
        except Exception as exc:
            last_error = exc
            print("  {} attempt {}/{} failed: {}".format(source, attempt, RETRIES, exc))
            if attempt < RETRIES:
                time.sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))
    raise DataError("{}: all {} attempts failed ({})".format(source, RETRIES, last_error))


# Ordered by trustworthiness. Kraken first because it is the venue the
# strategy would actually trade. Coinbase second because it is a real exchange
# API. yfinance last because it is a scraper of a third party's view and
# breaks whenever Yahoo changes its response shape -- as it currently has.
SOURCES = (
    ("kraken", _fetch_kraken),
    ("coinbase", _fetch_coinbase),
    ("yfinance", _fetch_yfinance),
)


def get_closes() -> pd.Series:
    """Daily closing prices, newest last. Raises DataError if nothing works."""
    errors = []
    for position, (name, fetch) in enumerate(SOURCES):
        try:
            close = _with_retries(fetch, name)
        except DataError as exc:
            errors.append(str(exc))
            print("{} unavailable: {}".format(name, exc))
            continue

        label = name if position == 0 else "{} FALLBACK".format(name)
        print("Price source: {} ({} candles, latest close {})".format(
            label, len(close), close.index[-1].date()))
        return close

    raise DataError(
        "No usable price data from any source.\n  " + "\n  ".join(errors)
    )


if __name__ == "__main__":
    closes = get_closes()
    print("\nlatest close: ${:,.2f} on {}".format(closes.iloc[-1], closes.index[-1].date()))
    print("candles:      {}".format(len(closes)))
    print("range:        {} to {}".format(closes.index[0].date(), closes.index[-1].date()))
