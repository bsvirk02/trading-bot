"""
Long-run daily history for backtesting, with a local cache.

Kraken's public OHLC endpoint returns at most 720 candles -- under two years,
and the 200-day MA eats the first 200 of them. Backtesting on that window
means backtesting on one market regime, which is precisely the mistake that
makes a strategy look better than it is.

Coinbase serves 300 candles per request and accepts a time range, so paging
backwards reconstructs history to 2015. Results are cached to CSV because
re-downloading several thousand candles on every parameter sweep is rude to
Coinbase and slow for us.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import settings

CANDLES_URL = "https://api.exchange.coinbase.com/products/{}/candles"
GRANULARITY = 86400
MAX_CANDLES_PER_REQUEST = 300
HEADERS = {"User-Agent": "trading-bot/1.0"}
PAUSE_SECONDS = 0.35          # stay well inside Coinbase's public rate limit

CACHE_DIR = settings.PROJECT_DIR / "cache"
CACHE_PATH = CACHE_DIR / "btc_daily.csv"

EARLIEST = datetime(2015, 7, 20, tzinfo=timezone.utc)


def _fetch_window(start, end):
    response = requests.get(
        CANDLES_URL.format(settings.COINBASE_PRODUCT),
        params={
            "granularity": GRANULARITY,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("coinbase: unexpected payload {}".format(str(rows)[:200]))
    # [time, low, high, open, close, volume]
    return {int(r[0]): float(r[4]) for r in rows}


def download(start=EARLIEST, end=None, verbose=True):
    """Page backwards through Coinbase until the start date is reached."""
    end = end or datetime.now(timezone.utc)
    closes = {}
    cursor = end
    requests_made = 0

    while cursor > start:
        window_start = max(start, cursor - timedelta(days=MAX_CANDLES_PER_REQUEST))
        batch = _fetch_window(window_start, cursor)
        requests_made += 1

        if not batch:
            # No data this far back; nothing earlier will exist either.
            if verbose:
                print("  no candles before {}, stopping".format(cursor.date()))
            break

        closes.update(batch)
        if verbose:
            print("  {} to {}: {} candles (total {})".format(
                window_start.date(), cursor.date(), len(batch), len(closes)))

        cursor = window_start
        time.sleep(PAUSE_SECONDS)

    if not closes:
        raise RuntimeError("coinbase returned no history at all")

    index = pd.to_datetime(sorted(closes), unit="s", utc=True)
    series = pd.Series([closes[int(t.timestamp())] for t in index],
                       index=index, name="close")
    series = series[~series.index.duplicated(keep="last")].sort_index()

    if verbose:
        print("Downloaded {} candles in {} requests: {} to {}".format(
            len(series), requests_made, series.index[0].date(), series.index[-1].date()))
    return series


def load(refresh=False, verbose=True):
    """Cached daily closes. Pass refresh=True to re-download from scratch."""
    if CACHE_PATH.exists() and not refresh:
        frame = pd.read_csv(CACHE_PATH, index_col=0, parse_dates=True)
        series = frame["close"]
        series.index = pd.to_datetime(series.index, utc=True)
        if verbose:
            print("History from cache: {} candles, {} to {}".format(
                len(series), series.index[0].date(), series.index[-1].date()))
        return series

    series = download(verbose=verbose)
    os.makedirs(str(CACHE_DIR), exist_ok=True)
    series.to_frame("close").to_csv(CACHE_PATH)
    if verbose:
        print("Cached to {}".format(CACHE_PATH))
    return series


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download BTC daily history.")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    args = parser.parse_args()

    closes = load(refresh=args.refresh)
    print("\nrange:   {} to {}".format(closes.index[0].date(), closes.index[-1].date()))
    print("candles: {}".format(len(closes)))
    print("first:   ${:,.2f}".format(closes.iloc[0]))
    print("last:    ${:,.2f}".format(closes.iloc[-1]))
    gaps = closes.index.to_series().diff().dt.days.dropna()
    print("gaps > 1 day: {}".format(int((gaps > 1).sum())))
