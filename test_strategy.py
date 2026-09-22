"""
Tests for the strategy and paper broker, run against synthetic price series.

These exist because the interesting behaviour -- the stop loss, the fee drag,
the entry/exit ordering -- only shows up in specific price paths that real
market data may not hand you when you happen to run the bot.
"""

import os
import sys
import tempfile

import numpy as np
import pandas as pd

import settings
import strategy
from broker import PaperBroker

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  PASS  {}".format(name))
    else:
        print("  FAIL  {}  {}".format(name, detail))
        FAILURES.append(name)


def series(values):
    index = pd.date_range("2024-01-01", periods=len(values), freq="D", tz="UTC")
    return pd.Series([float(v) for v in values], index=index)


def rising_then_crash(n_rise=320, crash_pct=0.35):
    """A long uptrend (so all MAs align and the strategy enters), then a
    sharp drop that should trip the stop loss before the slow 150-day MA
    could ever react."""
    rise = list(np.linspace(100, 300, n_rise))
    peak = rise[-1]
    crash = list(np.linspace(peak, peak * (1 - crash_pct), 12))
    return series(rise + crash)


print("\n--- strategy ---")

# 1. Flat + no signal -> HOLD
flat = series(np.linspace(300, 100, 260))
d = strategy.decide_latest(flat, None)
check("downtrend while flat -> HOLD", d.action == "HOLD", d.reason)

# 2. Uptrend while flat -> BUY
up = series(np.linspace(100, 300, 260))
d = strategy.decide_latest(up, None)
check("uptrend while flat -> BUY", d.action == "BUY", d.reason)

# 3. Stop loss fires, and is reported as a stop loss
prices = rising_then_crash()
entry = float(prices.iloc[-13])
pos = strategy.Position(qty=1.0, entry_price=entry)
d = strategy.decide_latest(prices, pos)
drawdown = (d.price - entry) / entry
check("crash beyond stop -> SELL", d.action == "SELL", d.reason)
check("stop loss named as the reason", "stop loss" in d.reason, d.reason)
check("drawdown really exceeds the stop",
      drawdown < -settings.STOP_LOSS_PCT,
      "drawdown {:.1%} vs stop {:.0%}".format(drawdown, settings.STOP_LOSS_PCT))

# 4. Stop loss takes precedence over the signal exit
check("stop checked before signal exit",
      "stop loss" in d.reason and "exit signal" not in d.reason, d.reason)

# 5. Small dip inside the stop while trend holds -> HOLD
pos_shallow = strategy.Position(qty=1.0, entry_price=float(up.iloc[-1]) * 1.02)
d = strategy.decide_latest(up, pos_shallow)
check("shallow dip -> HOLD", d.action == "HOLD", d.reason)


print("\n--- paper broker ---")

tmp = os.path.join(tempfile.mkdtemp(), "ledger.json")
b = PaperBroker(path=tmp)
start = b.state["cash"]

check("starts flat", b.position() is None)
check("equity equals starting cash", abs(b.equity(100.0) - start) < 1e-9)

buy = b.buy(100.0, "2024-01-01", "test entry")
check("buy fill is above the quote (slippage)", buy["fill"] > 100.0,
      "fill {:.4f}".format(buy["fill"]))
check("buy charged a fee", buy["fee"] > 0, "fee {:.2f}".format(buy["fee"]))
check("cash fully deployed", b.state["cash"] == 0.0)
check("position opened", b.position() is not None)

# Round trip at an unchanged price must LOSE money: two fees and two spreads.
sell = b.sell(100.0, "2024-01-02", "test exit")
check("sell fill is below the quote (slippage)", sell["fill"] < 100.0,
      "fill {:.4f}".format(sell["fill"]))
check("flat round trip loses money to costs", b.equity(100.0) < start,
      "equity {:.2f} vs start {:.2f}".format(b.equity(100.0), start))

expected_cost_pct = 2 * (settings.TAKER_FEE_PCT + settings.SLIPPAGE_PCT) * 100
actual_cost_pct = (1 - b.equity(100.0) / start) * 100
check("cost drag matches fee+slippage model",
      abs(actual_cost_pct - expected_cost_pct) < 0.05,
      "actual {:.3f}% vs expected {:.3f}%".format(actual_cost_pct, expected_cost_pct))

check("position closed", b.position() is None)
check("two trades recorded", len(b.state["trades"]) == 2)
check("realised P&L recorded on the sell", "realised_pnl" in b.state["trades"][1])

# Ledger survives a reload -- an open position must not be lost on restart.
b2 = PaperBroker(path=tmp)
b2.buy(100.0, "2024-01-03", "reload test")
b3 = PaperBroker(path=tmp)
check("open position survives reload", b3.position() is not None)
check("reloaded entry price matches",
      abs(b3.position().entry_price - b2.position().entry_price) < 1e-9)


print("\n--- guards ---")
try:
    b3.buy(100.0, "2024-01-04", "double buy")
    check("buying twice is refused", False, "no error raised")
except Exception as exc:
    check("buying twice is refused", True, str(exc))

b3.sell(100.0, "2024-01-05", "close")
try:
    b3.sell(100.0, "2024-01-06", "double sell")
    check("selling while flat is refused", False, "no error raised")
except Exception as exc:
    check("selling while flat is refused", True, str(exc))


print("\n" + "=" * 46)
if FAILURES:
    print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all tests passed")
