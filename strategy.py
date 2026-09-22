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
  exit   - the entry condition stops holding, OR price falls stop_pct below
           the reference price, whichever comes first

Every function takes optional parameters defaulting to settings, so a
parameter sweep can vary them without mutating global state.
"""

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

import settings


@dataclass
class Params:
    """One configuration of the strategy."""
    sma_short: int = None
    sma_long: int = None
    sma_trend: int = None
    stop_pct: float = None
    stop_mode: str = None        # "entry" or "trailing"

    def __post_init__(self):
        if self.sma_short is None:
            self.sma_short = settings.SMA_SHORT
        if self.sma_long is None:
            self.sma_long = settings.SMA_LONG
        if self.sma_trend is None:
            self.sma_trend = settings.SMA_TREND
        if self.stop_pct is None:
            self.stop_pct = settings.STOP_LOSS_PCT
        if self.stop_mode is None:
            self.stop_mode = settings.STOP_MODE

    def label(self):
        return "{}/{}/{} stop {:.0%} {}".format(
            self.sma_short, self.sma_long, self.sma_trend,
            self.stop_pct, self.stop_mode)


@dataclass
class Position:
    """An open long. qty is the amount of BTC held."""
    qty: float
    entry_price: float
    high_water: float = 0.0      # highest price seen while holding

    def __post_init__(self):
        if not self.high_water:
            self.high_water = self.entry_price

    def stop_reference(self, mode: str) -> float:
        """The price the stop loss is measured down from.

        "entry"    - a fixed stop. Caps the loss on THIS trade, but not the
                     drawdown: a position up 30% can give back 20% from its
                     high and still sit above the stop.
        "trailing" - measured from the high water mark, so it protects
                     accumulated gains as well as the entry.
        """
        return self.high_water if mode == "trailing" else self.entry_price

    def drawdown_from(self, price: float, mode: str = "entry") -> float:
        reference = self.stop_reference(mode)
        if reference <= 0:
            return 0.0
        return (price - reference) / reference


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
    as_of: object

    @property
    def signal(self) -> str:
        return "BUY" if (self.ma_crossover and self.bull_market) else "HOLD/SELL"


def compute_indicators(close: pd.Series, params: Params = None) -> pd.DataFrame:
    """Moving averages and the raw entry condition, for the whole series.

    Computed once for the backtest rather than recomputed per day.
    """
    params = params or Params()
    frame = pd.DataFrame({"close": close})
    frame["sma_short"] = close.rolling(window=params.sma_short).mean()
    frame["sma_long"] = close.rolling(window=params.sma_long).mean()
    frame["sma_trend"] = close.rolling(window=params.sma_trend).mean()
    frame["ma_crossover"] = frame["sma_short"] > frame["sma_long"]
    frame["bull_market"] = frame["close"] > frame["sma_trend"]
    frame["raw_signal"] = frame["ma_crossover"] & frame["bull_market"]
    return frame


def decide_from_row(row, as_of, position: Optional[Position],
                    params: Params = None) -> Decision:
    """The ONLY place the entry/exit rules live.

    The backtest walks history calling this per day; the live bot calls it
    once for the latest completed candle. Identical code, so they cannot
    disagree.
    """
    params = params or Params()
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
    # say so -- the two exits mean very different things when reviewing
    # performance later.
    drawdown = position.drawdown_from(price, params.stop_mode)
    if drawdown < -params.stop_pct:
        reference = position.stop_reference(params.stop_mode)
        return Decision(
            action="SELL",
            reason="stop loss hit: {:.1%} below {} ${:,.2f}".format(
                abs(drawdown),
                "high" if params.stop_mode == "trailing" else "entry",
                reference,
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
        reason="holding, {:+.1%} vs entry ${:,.2f}".format(
            position.drawdown_from(price, "entry"), position.entry_price),
        **common
    )


def decide_latest(close: pd.Series, position: Optional[Position],
                  params: Params = None) -> Decision:
    """Decision for the most recent completed candle."""
    params = params or Params()
    frame = compute_indicators(close, params)
    row = frame.iloc[-1]
    if row[["sma_short", "sma_long", "sma_trend"]].isna().any():
        raise ValueError(
            "not enough history to compute all moving averages "
            "({} candles, need {})".format(len(close), params.sma_trend)
        )
    return decide_from_row(row, frame.index[-1].date(), position, params)
