"""
Daily BTC signal run.

Phase 1 scope: secrets removed. This file previously imported Kraken API
credentials and opened a krakenex connection it never used -- the bot has
never placed an order. Real execution arrives with broker.py (Phase 4).

Moving averages are read from settings.py so this file and backtest.py
cannot drift apart.
"""

from datetime import datetime

import data
import settings
from notify import send_notification


def get_current_signal():
    # data.get_closes() handles source selection, retries and validation, and
    # raises DataError rather than returning something the strategy would
    # silently misread.
    close = data.get_closes()

    sma_short = close.rolling(window=settings.SMA_SHORT).mean()
    sma_long = close.rolling(window=settings.SMA_LONG).mean()
    sma_trend = close.rolling(window=settings.SMA_TREND).mean()

    current_price = float(close.iloc[-1])
    current_short = float(sma_short.iloc[-1])
    current_long = float(sma_long.iloc[-1])
    current_trend = float(sma_trend.iloc[-1])

    ma_signal = current_short > current_long
    bull_market = current_price > current_trend

    return {
        "signal": "BUY" if (ma_signal and bull_market) else "HOLD/SELL",
        "price": current_price,
        "sma_short": current_short,
        "sma_long": current_long,
        "sma_trend": current_trend,
        "ma_crossover": ma_signal,
        "bull_market": bull_market,
        "as_of": close.index[-1].date(),
    }


def main():
    settings.validate()

    print("\n" + "=" * 50)
    print("TRADING BOT STATUS - {}".format(datetime.now().strftime("%Y-%m-%d %H:%M")))
    print("=" * 50)

    try:
        signal = get_current_signal()
    except data.DataError as exc:
        # Loud and non-zero, so run_daily.sh alerts and a later trigger retries.
        print("\nDATA ERROR: {}".format(exc))
        send_notification(
            title="Trading bot: no usable price data",
            message=str(exc)[:400],
            priority=1,
        )
        return 2

    print("\nCandle date:    {}".format(signal["as_of"]))
    print("BTC Price:      ${:,.2f}".format(signal["price"]))
    print("{} Day MA:      ${:,.2f}".format(settings.SMA_SHORT, signal["sma_short"]))
    print("{} Day MA:    ${:,.2f}".format(settings.SMA_LONG, signal["sma_long"]))
    print("{} Day MA:    ${:,.2f}".format(settings.SMA_TREND, signal["sma_trend"]))
    print("\nMA Crossover:   {}".format("YES" if signal["ma_crossover"] else "NO"))
    print("Bull Market:    {}".format("YES" if signal["bull_market"] else "NO"))
    print("\n>>> SIGNAL: {} <<<".format(signal["signal"]))

    emoji = "\U0001F7E2" if signal["signal"] == "BUY" else "\U0001F534"
    sent = send_notification(
        title="{} BTC Signal: {}".format(emoji, signal["signal"]),
        message=(
            "Price: ${:,.0f}\n"
            "{}MA: ${:,.0f}\n"
            "{}MA: ${:,.0f}\n"
            "{}MA: ${:,.0f}\n"
            "Crossover: {}\n"
            "Bull Market: {}".format(
                signal["price"],
                settings.SMA_SHORT, signal["sma_short"],
                settings.SMA_LONG, signal["sma_long"],
                settings.SMA_TREND, signal["sma_trend"],
                "yes" if signal["ma_crossover"] else "no",
                "yes" if signal["bull_market"] else "no",
            )
        ),
    )

    print("\nDone.")
    # A run that computed a signal nobody received is not a successful run.
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
