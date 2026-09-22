"""
Backtest, replacing bot.py.

The critical change from bot.py is that this file no longer contains a copy of
the strategy. It walks history calling the same strategy.decide_from_row() the
live bot calls, and executes through the same fee and slippage maths the paper
broker uses. If the backtest and the live bot ever disagree now, it is because
the market differed -- not because the code did.

bot.py also reported returns with no fees and no slippage, which flatters any
strategy that trades at all.
"""

import argparse

import pandas as pd

import data
import settings
import strategy
from broker import PaperBroker


class MemoryBroker(PaperBroker):
    """PaperBroker with persistence disabled, for simulation runs."""

    def __init__(self):
        self.path = None
        self.state = {
            "cash": float(settings.PAPER_STARTING_CASH),
            "qty": 0.0,
            "entry_price": 0.0,
            "starting_cash": float(settings.PAPER_STARTING_CASH),
            "trades": [],
        }

    def _save(self):
        pass


def run(close: pd.Series, verbose=False):
    frame = strategy.compute_indicators(close)
    # Skip the warm-up period where the long MAs are still NaN.
    frame = frame.dropna(subset=["sma_short", "sma_long", "sma_trend"])
    if frame.empty:
        raise ValueError(
            "not enough history: need more than {} candles".format(settings.SMA_TREND)
        )

    broker = MemoryBroker()
    equity_curve = []

    for timestamp, row in frame.iterrows():
        decision = strategy.decide_from_row(row, timestamp.date(), broker.position())

        if decision.action == "BUY":
            broker.buy(decision.price, decision.as_of, decision.reason)
            if verbose:
                print("{}  BUY  ${:>10,.2f}  {}".format(
                    decision.as_of, decision.price, decision.reason))
        elif decision.action == "SELL":
            result = broker.sell(decision.price, decision.as_of, decision.reason)
            if verbose:
                print("{}  SELL ${:>10,.2f}  P&L ${:>9,.2f}  {}".format(
                    decision.as_of, decision.price, result["pnl"], decision.reason))

        equity_curve.append(broker.equity(decision.price))

    equity = pd.Series(equity_curve, index=frame.index)
    prices = frame["close"]

    trades = [t for t in broker.state["trades"] if t["action"] == "SELL"]
    wins = [t for t in trades if t.get("realised_pnl", 0) > 0]
    fees = sum(t["fee"] for t in broker.state["trades"])
    stops = [t for t in trades if "stop loss" in t["reason"]]

    strategy_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    bh_return = (prices.iloc[-1] / prices.iloc[0] - 1) * 100
    max_dd = (equity / equity.cummax() - 1).min() * 100
    bh_max_dd = (prices / prices.cummax() - 1).min() * 100

    return {
        "start": frame.index[0].date(),
        "end": frame.index[-1].date(),
        "days": len(frame),
        "strategy_return": strategy_return,
        "buy_hold_return": bh_return,
        "max_drawdown": max_dd,
        "buy_hold_max_drawdown": bh_max_dd,
        "round_trips": len(trades),
        "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
        "stop_loss_exits": len(stops),
        "fees_paid": fees,
        "final_equity": equity.iloc[-1],
        "equity": equity,
    }


def report(result):
    print("\n" + "=" * 52)
    print("BACKTEST  {} to {}  ({} days)".format(
        result["start"], result["end"], result["days"]))
    print("=" * 52)
    print("Strategy return:      {:>10.1f}%".format(result["strategy_return"]))
    print("Buy & hold return:    {:>10.1f}%".format(result["buy_hold_return"]))
    print("Difference:           {:>10.1f}pp".format(
        result["strategy_return"] - result["buy_hold_return"]))
    print("-" * 52)
    print("Strategy max drawdown:{:>10.1f}%".format(result["max_drawdown"]))
    print("Buy&hold max drawdown:{:>10.1f}%".format(result["buy_hold_max_drawdown"]))
    print("-" * 52)
    print("Round trips:          {:>10d}".format(result["round_trips"]))
    print("  of which stop loss: {:>10d}".format(result["stop_loss_exits"]))
    print("Win rate:             {:>10.1f}%".format(result["win_rate"]))
    print("Fees paid:            {:>10,.2f}  (on ${:,.0f} starting capital)".format(
        result["fees_paid"], settings.PAPER_STARTING_CASH))
    print("Final equity:         {:>10,.2f}".format(result["final_equity"]))
    print("=" * 52)
    print("Costs included: {:.2%} taker fee, {:.2%} slippage per side.".format(
        settings.TAKER_FEE_PCT, settings.SLIPPAGE_PCT))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest the MA strategy.")
    parser.add_argument("--verbose", action="store_true", help="print every trade")
    args = parser.parse_args()

    closes = data.get_closes()
    report(run(closes, verbose=args.verbose))
