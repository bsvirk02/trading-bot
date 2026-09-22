"""
The daily run: fetch prices, decide, execute, report.

This file used to contain its own copy of the strategy and its own copy of the
notification code. Both had drifted from the versions they were copied from.
It now orchestrates and nothing more -- the rules live in strategy.py, the
execution in broker.py, the prices in data.py, the notifications in notify.py.
"""

from datetime import datetime

import broker as broker_module
import data
import settings
import strategy
from notify import send_notification


def _format_report(decision, position, equity, return_pct, executed):
    lines = [
        "Candle:  {}".format(decision.as_of),
        "Price:   ${:,.2f}".format(decision.price),
        "",
        "{}MA ${:,.0f} / {}MA ${:,.0f} / {}MA ${:,.0f}".format(
            settings.SMA_SHORT, decision.sma_short,
            settings.SMA_LONG, decision.sma_long,
            settings.SMA_TREND, decision.sma_trend,
        ),
        "Crossover: {}   Bull: {}".format(
            "yes" if decision.ma_crossover else "no",
            "yes" if decision.bull_market else "no",
        ),
        "",
        "Action:  {} ({})".format(decision.action, decision.reason),
    ]
    if executed:
        lines.append("Filled:  ${:,.2f}  fee ${:.2f}".format(
            executed["fill"], executed["fee"]))
        if "pnl" in executed:
            lines.append("Trade P&L: ${:,.2f}".format(executed["pnl"]))
    lines += [
        "",
        "Position: {}".format(
            "{:.6f} BTC @ ${:,.2f}".format(position.qty, position.entry_price)
            if position else "flat"
        ),
        "Equity:   ${:,.2f} ({:+.2f}%)".format(equity, return_pct),
    ]
    return "\n".join(lines)


def main():
    settings.validate()

    print("\n" + "=" * 52)
    print("TRADING BOT - {} MODE - {}".format(
        settings.MODE.upper(), datetime.now().strftime("%Y-%m-%d %H:%M")))
    print("=" * 52)

    try:
        broker = broker_module.get_broker()
    except broker_module.BrokerError as exc:
        print("\nBROKER ERROR: {}".format(exc))
        send_notification(
            title="Trading bot: broker unavailable", message=str(exc)[:400], priority=1
        )
        return 3

    try:
        closes = data.get_closes()
    except data.DataError as exc:
        # Loud and non-zero, so run_daily.sh alerts and a later trigger retries
        # rather than the day being silently lost.
        print("\nDATA ERROR: {}".format(exc))
        send_notification(
            title="Trading bot: no usable price data",
            message=str(exc)[:400],
            priority=1,
        )
        return 2

    position = broker.position()
    decision = strategy.decide_latest(closes, position)

    print("\nCandle:    {}".format(decision.as_of))
    print("Price:     ${:,.2f}".format(decision.price))
    print("{:>3} Day MA: ${:,.2f}".format(settings.SMA_SHORT, decision.sma_short))
    print("{:>3} Day MA: ${:,.2f}".format(settings.SMA_LONG, decision.sma_long))
    print("{:>3} Day MA: ${:,.2f}".format(settings.SMA_TREND, decision.sma_trend))
    print("Crossover: {}".format("YES" if decision.ma_crossover else "NO"))
    print("Bull:      {}".format("YES" if decision.bull_market else "NO"))
    print("\n>>> {} - {} <<<".format(decision.action, decision.reason))

    executed = None
    try:
        if decision.action == "BUY":
            executed = broker.buy(decision.price, decision.as_of, decision.reason)
            print("BOUGHT {:.6f} BTC at ${:,.2f} (fee ${:.2f})".format(
                executed["qty"], executed["fill"], executed["fee"]))
        elif decision.action == "SELL":
            executed = broker.sell(decision.price, decision.as_of, decision.reason)
            print("SOLD {:.6f} BTC at ${:,.2f} (fee ${:.2f}, P&L ${:,.2f})".format(
                executed["qty"], executed["fill"], executed["fee"], executed["pnl"]))
    except broker_module.BrokerError as exc:
        print("\nEXECUTION ERROR: {}".format(exc))
        send_notification(
            title="Trading bot: execution failed", message=str(exc)[:400], priority=1
        )
        return 4

    position = broker.position()
    equity = broker.equity(decision.price)
    return_pct = broker.total_return_pct(decision.price)

    print("\nPosition:  {}".format(
        "{:.6f} BTC @ ${:,.2f}".format(position.qty, position.entry_price)
        if position else "flat"))
    print("Equity:    ${:,.2f} ({:+.2f}%)".format(equity, return_pct))

    if decision.action in ("BUY", "SELL"):
        emoji, title = ("\U0001F7E2", "BOUGHT") if decision.action == "BUY" else ("\U0001F534", "SOLD")
    else:
        emoji, title = "⚪", "HOLD"

    sent = send_notification(
        title="{} {} BTC ({})".format(emoji, title, settings.MODE),
        message=_format_report(decision, position, equity, return_pct, executed),
    )

    print("\nDone.")
    # A run whose report nobody received is not a successful run.
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
