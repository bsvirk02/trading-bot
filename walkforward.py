"""
Walk-forward validation.

Every backtest run so far is contaminated. The parameters 5/150/200 were
chosen by looking at price history, and then judged against that same history.
A sweep across 25 combinations makes this worse, not better: with enough
combinations something always wins, and the winner's margin is mostly luck.

Walk-forward removes the contamination. Fit on a fixed window, test on the
NEXT window, roll forward, repeat. The test window is never used to choose
anything, so its results are what the strategy would actually have delivered.

Two questions this answers, which no single backtest can:

  1. Do the winning parameters persist? If the same pair wins window after
     window, the sweep found structure. If the winner changes every time, the
     surface is noise and further tuning is self-deception.

  2. What is realistic out-of-sample performance? The honest number for
     "what would this have made", as opposed to "what would it have made if
     I had known in advance which settings to use".

Each window is simulated with a lead-in of price history before its start date
so the moving averages are warm and any open position carries in, exactly as
the live bot would experience it. Results are measured only from the window's
start date onward.
"""

import argparse

import pandas as pd

import backtest
import settings
import strategy

IS_YEARS = 3          # fit window
OOS_YEARS = 1         # test window
STEP_YEARS = 1        # roll
LEAD_IN_DAYS = 320    # enough to warm a 200-day MA plus slack

GRID = [(s, l) for s in backtest.SWEEP_SHORT for l in backtest.SWEEP_LONG if s < l]


def _ts(value):
    """Timestamp in UTC, whether given a string or an already-aware value."""
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def evaluate(close, start, end, params):
    """Run a window with lead-in, then measure only from `start` onward."""
    window = close[(close.index >= _ts(start) - pd.Timedelta(days=LEAD_IN_DAYS))
                   & (close.index <= _ts(end))]
    try:
        result = backtest.run(window, params)
    except ValueError:
        return None

    equity = result["equity"]
    equity = equity[equity.index >= _ts(start)]
    prices = window[window.index >= _ts(start)]
    if len(equity) < 2 or len(prices) < 2:
        return None

    total = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    drawdown = (equity / equity.cummax() - 1).min() * 100
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    return {
        "return": total,
        "drawdown": drawdown,
        "calmar": (cagr / abs(drawdown / 100)) if drawdown else 0.0,
        "buy_hold": (prices.iloc[-1] / prices.iloc[0] - 1) * 100,
    }


def best_params(close, start, end):
    """The parameter pair that scored highest on Calmar over the fit window."""
    best, best_score = None, None
    for short, long_ in GRID:
        params = strategy.Params(sma_short=short, sma_long=long_)
        result = evaluate(close, start, end, params)
        if result is None:
            continue
        score = result["calmar"]
        if best_score is None or score > best_score:
            best, best_score = (short, long_), score
    return best, best_score


def bh_dd(close, start, end):
    prices = close[(close.index >= _ts(start)) & (close.index <= _ts(end))]
    if len(prices) < 2:
        return 0.0
    return (prices / prices.cummax() - 1).min() * 100


def run(close, verbose=True):
    first = close.index[0] + pd.Timedelta(days=LEAD_IN_DAYS)
    last = close.index[-1]

    windows = []
    fit_start = _ts("{}-01-01".format(first.year + 1))
    while True:
        fit_end = fit_start + pd.DateOffset(years=IS_YEARS)
        test_end = fit_end + pd.DateOffset(years=OOS_YEARS)
        if fit_end > last:
            break
        windows.append((fit_start, fit_end, min(test_end, last)))
        fit_start = fit_start + pd.DateOffset(years=STEP_YEARS)

    baseline = (settings.SMA_SHORT, settings.SMA_LONG)
    rows = []

    if verbose:
        print("\nFit {} years, test the following {}, roll {} year at a time.".format(
            IS_YEARS, OOS_YEARS, STEP_YEARS))
        print("{} parameter combinations evaluated per fit window.\n".format(len(GRID)))
        print("{:<22}{:>9}{:>11}{:>11}{:>11}{:>9}{:>9}".format(
            "TEST WINDOW", "IS BEST", "OOS chosen", "OOS 5/150", "OOS B&H",
            "DD 5/150", "DD B&H"))
        print("-" * 82)

    for fit_start, fit_end, test_end in windows:
        chosen, _ = best_params(close, fit_start, fit_end)
        if chosen is None:
            continue

        selected = evaluate(close, fit_end, test_end,
                            strategy.Params(sma_short=chosen[0], sma_long=chosen[1]))
        fixed = evaluate(close, fit_end, test_end,
                         strategy.Params(sma_short=baseline[0], sma_long=baseline[1]))
        if selected is None or fixed is None:
            continue

        rows.append({
            "test_start": fit_end.date(),
            "test_end": test_end.date(),
            "chosen": chosen,
            "oos_selected": selected["return"],
            "oos_fixed": fixed["return"],
            "oos_bh": selected["buy_hold"],
            "dd_selected": selected["drawdown"],
            "dd_fixed": fixed["drawdown"],
            "dd_bh": bh_dd(close, fit_end, test_end),
        })

        if verbose:
            print("{:<22}{:>9}{:>10.1f}%{:>10.1f}%{:>10.1f}%{:>8.1f}%{:>8.1f}%".format(
                "{} to {}".format(fit_end.date(), test_end.date()),
                "{}/{}".format(*chosen),
                selected["return"], fixed["return"], selected["buy_hold"],
                fixed["drawdown"], rows[-1]["dd_bh"]))

    return rows


def summarise(rows):
    if not rows:
        print("\nNot enough history for a single walk-forward window.")
        return

    print("-" * 82)

    def compound(key):
        total = 1.0
        for r in rows:
            total *= (1 + r[key] / 100)
        return (total - 1) * 100

    sel, fix, bh = compound("oos_selected"), compound("oos_fixed"), compound("oos_bh")

    print("\nCOMPOUNDED OUT-OF-SAMPLE, {} windows ({} to {})".format(
        len(rows), rows[0]["test_start"], rows[-1]["test_end"]))
    print("  walk-forward selected params : {:>10.1f}%".format(sel))
    print("  fixed 5/150                  : {:>10.1f}%".format(fix))
    print("  buy & hold                   : {:>10.1f}%".format(bh))

    worst_dd_fix = min(r["dd_fixed"] for r in rows)
    worst_dd_bh = min(r["dd_bh"] for r in rows)
    print("\nWORST DRAWDOWN IN ANY SINGLE WINDOW")
    print("  5/150      : {:>7.1f}%".format(worst_dd_fix))
    print("  buy & hold : {:>7.1f}%".format(worst_dd_bh))

    print("\nWORST SINGLE WINDOW RETURN")
    worst_sel = min(rows, key=lambda r: r["oos_selected"])
    worst_fix = min(rows, key=lambda r: r["oos_fixed"])
    print("  selected : {:>7.1f}%  ({})".format(worst_sel["oos_selected"], worst_sel["test_start"]))
    print("  5/150    : {:>7.1f}%  ({})".format(worst_fix["oos_fixed"], worst_fix["test_start"]))

    beat = sum(1 for r in rows if r["oos_selected"] > r["oos_bh"])
    beat_fixed = sum(1 for r in rows if r["oos_fixed"] > r["oos_bh"])
    print("\nWINDOWS BEATING BUY & HOLD")
    print("  selected : {} of {}".format(beat, len(rows)))
    print("  5/150    : {} of {}".format(beat_fixed, len(rows)))

    counts = {}
    for r in rows:
        counts[r["chosen"]] = counts.get(r["chosen"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])
    print("\nPARAMETER STABILITY")
    for pair, n in ordered:
        print("  {:>7}  won {} of {} fit windows".format("{}/{}".format(*pair), n, len(rows)))

    print("\nVERDICT INPUTS")
    if len(ordered) == 1:
        print("  One pair won every window: the parameter surface has real structure.")
    elif ordered[0][1] >= max(2, len(rows) * 0.5):
        print("  {} won {} of {} windows: some persistence, not conclusive.".format(
            "{}/{}".format(*ordered[0][0]), ordered[0][1], len(rows)))
    else:
        print("  {} different winners across {} windows: the surface is mostly noise,".format(
            len(ordered), len(rows)))
        print("  and picking parameters from a sweep is not justified by this data.")

    if sel <= fix:
        print("  Re-fitting parameters each year did NOT beat leaving them at 5/150.")
        print("  The sweep's apparent edge does not survive forward.")
    else:
        print("  Re-fitting each year beat fixed parameters by {:.1f}pp compounded,".format(sel - fix))
        if len(ordered) > 1:
            print("  but with {} different winners that margin is not something you could".format(len(ordered)))
            print("  have captured in advance -- you would have had to know which to pick.")

    if max(sel, fix) < bh:
        print("")
        print("  BUY AND HOLD BEAT BOTH on total return ({:.1f}% vs {:.1f}% vs {:.1f}%).".format(
            bh, sel, fix))
        print("  The strategy's case rests entirely on drawdown: {:.1f}% worst window".format(worst_dd_fix))
        print("  against {:.1f}% for buy and hold.".format(worst_dd_bh))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-forward validation.")
    parser.add_argument("--is-years", type=int, default=IS_YEARS)
    parser.add_argument("--oos-years", type=int, default=OOS_YEARS)
    args = parser.parse_args()

    IS_YEARS, OOS_YEARS = args.is_years, args.oos_years

    import history
    closes = history.load(verbose=False)
    summarise(run(closes))
