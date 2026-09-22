"""
The trading strategy. One implementation, used by both the backtest and the
live bot.

This module exists because bot.py and live_bot.py each had their own copy of
the logic and they had already drifted: the backtest applied a 15% stop loss,
the live bot did not. A backtest that does not describe the running bot is
worse than no backtest, because it produces numbers you trust for the wrong
system.

The rules:
  entry  - short MA above long MA, AND price above the trend MA (bull filter)
  exit   - the entry condition stops holding, OR price falls STOP_LOSS_PCT
           below the entry price, whichever comes first
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import settings


@dataclass
class Position:
    """An open long. qty is the amount of BTC held."""
    qty: float
    entry_price: float

    def drawdown_from(self, price: float) -> float:
        """Negative when price is below entry. -0.15 means 15% underwater."""
        if self.entry_price <= 0:
            return 0.0
        return (price - self.entry_price) / self.entry_price


@dataclass
class Decision:
    action: str          # BUY | SELL | HOLD
    reason: str
    price: float
    sma_short: float
    sma_long: float
    sma_trend: float
    ma_crossover: bool
    bull_market: bool
    as_of: object        # date of the candle the decision was made on

    @property
    def signal(self) -> str:
        """Human-facing label, kept for the notification text."""
        return "BUY" if (self.ma_crossover and self.bull_market) else "HOLD/SELL"


def compute_indicators(close: pd.Series) -> pd.DataFrame:
    """Moving averages and the raw entry condition, for the whole series.

    Computed once for the backtest rather than recomputed per day, which is
    what made the original loop slow and hard to follow.
    """
    frame = pd.DataFrame({"close": close})
    frame["sma_short"] = close.rolling(window=settings.SMA_SHORT).mean()
    frame["sma_long"] = close.rolling(window=settings.SMA_LONG).mean()
    frame["sma_trend"] = close.rolling(window=settings.SMA_TREND).mean()
    frame["ma_crossover"] = frame["sma_short"] > frame["sma_long"]
    frame["bull_market"] = frame["close"] > frame["sma_trend"]
    frame["raw_signal"] = frame["ma_crossover"] & frame["bull_market"]
    return frame


def decide_from_row(row, as_of, position: Optional[Position]) -> Decision:
    """Decide what to do given one day's indicators and the current position.

    This is the ONLY place the entry/exit rules live. The backtest walks
    history calling this per day; the live bot calls it once for the latest
    completed candle. Identical code, so they cannot disagree.
    """
    price = float(row["close"])
    common = dict(
        price=price,
        sma_short=float(row["sma_short"]),
        sma_long=float(row["sma_long"]),
        sma_trend=float(row["sma_trend"]),
        ma_crossover=bool(row["ma_crossover"]),
        bull_market=bool(row["bull_market"]),
        as_of=as_of,
    )
    raw_signal = bool(row["raw_signal"])

    if position is None:
        if raw_signal:
            return Decision(action="BUY", reason="entry conditions met", **common)
        return Decision(
            action="HOLD",
            reason="flat; {}".format(
                "no MA crossover" if not common["ma_crossover"] else "not a bull market"
            ),
            **common
        )

    # Stop loss is checked BEFORE the signal exit. If price has collapsed
    # through the stop, that is the reason we are selling, and the log should
    # say so -- the two exits have very different meanings when reviewing
    # performance later.
    drawdown = position.drawdown_from(price)
    if drawdown < -settings.STOP_LOSS_PCT:
        return Decision(
            action="SELL",
            reason="stop loss hit: {:.1%} below entry ${:,.2f}".format(
                abs(drawdown), position.entry_price
            ),
            **common
        )

    if not raw_signal:
        return Decision(
            action="SELL",
            reason="exit signal: {}".format(
                "MA crossover ended" if not common["ma_crossover"] else "trend filter failed"
            ),
            **common
        )

    return Decision(
        action="HOLD",
        reason="holding, {:+.1%} vs entry ${:,.2f}".format(drawdown, position.entry_price),
        **common
    )


def decide_latest(close: pd.Series, position: Optional[Position]) -> Decision:
    """Decision for the most recent completed candle."""
    frame = compute_indicators(close)
    row = frame.iloc[-1]
    if row[["sma_short", "sma_long", "sma_trend"]].isna().any():
        raise ValueError(
            "not enough history to compute all moving averages "
            "({} candles, need {})".format(len(close), settings.SMA_TREND)
        )
    return decide_from_row(row, frame.index[-1].date(), position)
