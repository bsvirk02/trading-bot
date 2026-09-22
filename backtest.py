"""
Backtest and validation, replacing bot.py.

Two things bot.py did wrong beyond containing a duplicate of the strategy:

  1. no fees and no slippage, which flatters anything that trades
  2. one window (2020-2024), which was overwhelmingly a bull market

This runs the same strategy.decide_from_row() the live bot calls, executes
through the same fee maths as the paper broker, and supports splitting
history into periods and sweeping parameters so a result can be checked for
being an accident of one window.

  python backtest.py                    # whole cached history
  python backtest.py --periods          # named regimes, in and out of sample
  python backtest.py --sweep            # parameter sensitivity
  python backtest.py --start 2017-01-01 --end 2019-12-31
"""

import argparse
import itertools

import pandas as pd

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
            "high_water": 0.0,
            "starting_cash": float(settings.PAPER_STARTING_CASH),
            "trades": [],
        }

    def _save(self):
        pass


def run(close, params=None, verbose=False):
    params = params or strategy.Params()
    frame = strategy.compute_indicators(close, params)
    frame = frame.dropna(subset=["sma_short", "sma_long", "sma_trend"])
    if len(frame) < 2:
        raise ValueError(
            "not enough history: {} usable candles after a {}-day warm-up".format(
                len(frame), params.sma_trend)
        )

    broker = MemoryBroker()
    equity_curve = []

    for timestamp, row in frame.iterrows():
        price = float(row["close"])
        broker.mark(price)
        decision = strategy.decide_from_row(row, timestamp.date(),
                                            broker.position(), params)

        if decision.action == "BUY":
            broker.buy(price, decision.as_of, decision.reason)
            if verbose:
                print("{}  BUY  ${:>10,.2f}  {}".format(
                    decision.as_of, price, decision.reason))
        elif decision.action == "SELL":
            result = broker.sell(price, decision.as_of, decision.reason)
            if verbose:
                print("{}  SELL ${:>10,.2f}  P&L ${:>9,.2f}  {}".format(
                    decision.as_of, price, result["pnl"], decision.reason))

        equity_curve.append(broker.equity(price))

    equity = pd.Series(equity_curve, index=frame.index)
    prices = frame["close"]

    sells = [t for t in broker.state["trades"] if t["action"] == "SELL"]
    wins = [t for t in sells if t.get("realised_pnl", 0) > 0]
    realised = sum(t.get("realised_pnl", 0) for t in sells)
    open_position = broker.position() is not None

    return {
        "params": params,
        "start": frame.index[0].date(),
        "end": frame.index[-1].date(),
        "days": len(frame),
        "strategy_return": (equity.iloc[-1] / equity.iloc[0] - 1) * 100,
        "buy_hold_return": (prices.iloc[-1] / prices.iloc[0] - 1) * 100,
        "max_drawdown": (equity / equity.cummax() - 1).min() * 100,
        "buy_hold_max_drawdown": (prices / prices.cummax() - 1).min() * 100,
        "round_trips": len(sells),
        "win_rate": (len(wins) / len(sells) * 100) if sells else 0.0,
        "stop_loss_exits": len([t for t in sells if "stop loss" in t["reason"]]),
        "fees_paid": sum(t["fee"] for t in broker.state["trades"]),
        "realised_pnl": realised,
        "unrealised_pnl": equity.iloc[-1] - settings.PAPER_STARTING_CASH - realised,
        "ends_open": open_position,
        "final_equity": equity.iloc[-1],
        "equity": equity,
    }


def report(r):
    print("\n" + "=" * 60)
    print("BACKTEST  {} to {}  ({} days)".format(r["start"], r["end"], r["days"]))
    print("params: {}".format(r["params"].label()))
    print("=" * 60)
    print("Strategy return:       {:>10.1f}%".format(r["strategy_return"]))
    print("Buy & hold return:     {:>10.1f}%".format(r["buy_hold_return"]))
    print("Difference:            {:>10.1f}pp".format(
        r["strategy_return"] - r["buy_hold_return"]))
    print("-" * 60)
    print("Strategy max drawdown: {:>10.1f}%".format(r["max_drawdown"]))
    print("Buy&hold max drawdown: {:>10.1f}%".format(r["buy_hold_max_drawdown"]))
    print("-" * 60)
    print("Round trips:           {:>10d}".format(r["round_trips"]))
    print("  of which stop loss:  {:>10d}".format(r["stop_loss_exits"]))
    print("Win rate:              {:>10.1f}%".format(r["win_rate"]))
    print("Realised P&L:          {:>10,.2f}".format(r["realised_pnl"]))
    print("Unrealised P&L:        {:>10,.2f}{}".format(
        r["unrealised_pnl"], "   <-- position still open" if r["ends_open"] else ""))
    print("Fees paid:             {:>10,.2f}".format(r["fees_paid"]))
    print("Final equity:          {:>10,.2f}".format(r["final_equity"]))
    print("=" * 60)
    print("Costs: {:.2%} taker fee + {:.2%} slippage per side.".format(
        settings.TAKER_FEE_PCT, settings.SLIPPAGE_PCT))
    if r["round_trips"] < 10:
        print("WARNING: {} completed round trips is too few to be evidence "
              "of anything.".format(r["round_trips"]))


# Named regimes. The 2020-2024 window bot.py used is included so the original
# claim can be compared directly against periods it never saw.
PERIODS = [
    ("2015-2017 early",        "2015-07-20", "2017-12-31"),
    ("2018-2019 BEAR",         "2018-01-01", "2019-12-31"),
    ("2020-2024 (original)",   "2020-01-01", "2024-12-31"),
    ("2025-today OUT OF SAMPLE", "2025-01-01", None),
    ("full history",           None,         None),
]


def slice_period(close, start, end):
    if start:
        close = close[close.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        close = close[close.index <= pd.Timestamp(end, tz="UTC")]
    return close


def run_periods(close, params=None):
    print("\n{:<26} {:>9} {:>9} {:>8} {:>8} {:>7} {:>6}".format(
        "PERIOD", "STRATEGY", "BUY&HOLD", "STRAT DD", "B&H DD", "TRADES", "STOPS"))
    print("-" * 82)
    for name, start, end in PERIODS:
        window = slice_period(close, start, end)
        try:
            r = run(window, params)
        except ValueError as exc:
            print("{:<26} {}".format(name, exc))
            continue
        print("{:<26} {:>8.1f}% {:>8.1f}% {:>7.1f}% {:>7.1f}% {:>7d} {:>6d}".format(
            name, r["strategy_return"], r["buy_hold_return"],
            r["max_drawdown"], r["buy_hold_max_drawdown"],
            r["round_trips"], r["stop_loss_exits"]))
    print("-" * 82)
    print("A strategy that only works in one row is fitted to that row.")


SWEEP_SHORT = [3, 5, 8, 10, 20]
SWEEP_LONG = [100, 120, 150, 180, 200]


def run_sweep(close):
    """Vary the MA pair around the chosen 5/150.

    If 5/150 wins but its neighbours lose, the parameters were fitted to this
    particular history and should not be trusted forward.
    """
    print("\nPARAMETER SWEEP - total return %, whole window")
    print("(rows = short MA, cols = long MA; chosen pair marked *)\n")
    header = "short\\long" + "".join("{:>10}".format(l) for l in SWEEP_LONG)
    print(header)
    print("-" * len(header))

    results = {}
    for short in SWEEP_SHORT:
        row = "{:>10}".format(short)
        for long_ in SWEEP_LONG:
            if short >= long_:
                row += "{:>10}".format("-")
                continue
            params = strategy.Params(sma_short=short, sma_long=long_)
            try:
                r = run(close, params)
            except ValueError:
                row += "{:>10}".format("n/a")
                continue
            results[(short, long_)] = r
            mark = "*" if (short == settings.SMA_SHORT and long_ == settings.SMA_LONG) else " "
            row += "{:>9.0f}{}".format(r["strategy_return"], mark)
        print(row)

    if results:
        values = [r["strategy_return"] for r in results.values()]
        chosen = results.get((settings.SMA_SHORT, settings.SMA_LONG))
        print("-" * len(header))
        print("best {:.0f}%   worst {:.0f}%   median {:.0f}%".format(
            max(values), min(values), pd.Series(values).median()))
        if chosen:
            better = sum(1 for v in values if v > chosen["strategy_return"])
            print("chosen {}/{} returns {:.0f}%, beaten by {} of {} combinations".format(
                settings.SMA_SHORT, settings.SMA_LONG,
                chosen["strategy_return"], better, len(values)))
        print("\nA robust edge shows a broad plateau of similar results.")
        print("A lone peak surrounded by poor neighbours is curve fitting.")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest and validate the strategy.")
    parser.add_argument("--verbose", action="store_true", help="print every trade")
    parser.add_argument("--periods", action="store_true", help="compare market regimes")
    parser.add_argument("--sweep", action="store_true", help="parameter sensitivity")
    parser.add_argument("--trailing", action="store_true", help="use a trailing stop")
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--end", help="YYYY-MM-DD")
    parser.add_argument("--live-window", action="store_true",
                        help="use the 720-candle live feed instead of cached history")
    args = parser.parse_args()

    if args.live_window:
        import data
        closes = data.get_closes()
    else:
        import history
        closes = history.load()

    params = strategy.Params(stop_mode="trailing" if args.trailing else None)

    if args.sweep:
        run_sweep(slice_period(closes, args.start, args.end))
    elif args.periods:
        run_periods(closes, params)
    else:
        report(run(slice_period(closes, args.start, args.end), params,
                   verbose=args.verbose))
