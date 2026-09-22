"""
Execution. One interface, two implementations.

    Broker
      +- PaperBroker   simulated fills against real prices, state in ledger.json
      +- KrakenBroker  real orders (deliberately not enabled)

The point of the split is that the strategy calls buy()/sell() and never knows
which is underneath. Flipping MODE in .env from paper to live changes the
broker and nothing else, so the code that runs with real money is the same
code that has been running on paper -- not a rewrite of it.

Paper fills are deliberately pessimistic: every trade pays the Kraken taker
fee and crosses the spread. A simulation with free, perfect fills flatters a
strategy that trades often, which is exactly the error a backtest is supposed
to protect you from.
"""

import json
import os
from datetime import datetime, timezone

import settings
from strategy import Position


class BrokerError(Exception):
    pass


def _buy_fill(price: float) -> float:
    """You pay slightly above the quote when crossing the spread."""
    return price * (1.0 + settings.SLIPPAGE_PCT)


def _sell_fill(price: float) -> float:
    return price * (1.0 - settings.SLIPPAGE_PCT)


class Broker(object):
    def position(self):
        raise NotImplementedError

    def equity(self, price):
        raise NotImplementedError

    def buy(self, price, as_of, reason):
        raise NotImplementedError

    def sell(self, price, as_of, reason):
        raise NotImplementedError


class PaperBroker(Broker):
    """Simulated account persisted to ledger.json.

    Position sizing is all-in / all-out, matching the backtest, which is
    either fully long or fully flat. Partial sizing would be a different
    strategy and would need its own validation.
    """

    def __init__(self, path=None):
        self.path = str(path or settings.LEDGER_PATH)
        self.state = self._load()

    # --- persistence ---------------------------------------------------
    def _load(self):
        if not os.path.exists(self.path):
            return {
                "cash": float(settings.PAPER_STARTING_CASH),
                "qty": 0.0,
                "entry_price": 0.0,
                "starting_cash": float(settings.PAPER_STARTING_CASH),
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "trades": [],
            }
        with open(self.path) as handle:
            return json.load(handle)

    def _save(self):
        # Write to a temp file and rename, so a crash mid-write cannot leave a
        # truncated ledger -- which would lose the record of an open position.
        tmp = self.path + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(self.state, handle, indent=2)
        os.replace(tmp, self.path)

    # --- account -------------------------------------------------------
    def position(self):
        if self.state["qty"] <= 0:
            return None
        return Position(
            qty=float(self.state["qty"]),
            entry_price=float(self.state["entry_price"]),
        )

    def equity(self, price):
        return float(self.state["cash"]) + float(self.state["qty"]) * price

    def total_return_pct(self, price):
        start = float(self.state.get("starting_cash") or settings.PAPER_STARTING_CASH)
        if start <= 0:
            return 0.0
        return (self.equity(price) / start - 1.0) * 100.0

    # --- execution -----------------------------------------------------
    def buy(self, price, as_of, reason):
        cash = float(self.state["cash"])
        if cash <= 0:
            raise BrokerError("buy requested with no cash (already in position?)")

        fill = _buy_fill(price)
        fee = cash * settings.TAKER_FEE_PCT
        qty = (cash - fee) / fill

        self.state["cash"] = 0.0
        self.state["qty"] = qty
        self.state["entry_price"] = fill
        self._record("BUY", as_of, price, fill, qty, fee, reason)
        self._save()
        return {"fill": fill, "qty": qty, "fee": fee}

    def sell(self, price, as_of, reason):
        qty = float(self.state["qty"])
        if qty <= 0:
            raise BrokerError("sell requested with no position")

        fill = _sell_fill(price)
        proceeds = qty * fill
        fee = proceeds * settings.TAKER_FEE_PCT
        entry = float(self.state["entry_price"])
        pnl = proceeds - fee - (qty * entry)

        self.state["cash"] = proceeds - fee
        self.state["qty"] = 0.0
        self.state["entry_price"] = 0.0
        self._record("SELL", as_of, price, fill, qty, fee, reason, pnl=pnl)
        self._save()
        return {"fill": fill, "qty": qty, "fee": fee, "pnl": pnl}

    def _record(self, action, as_of, price, fill, qty, fee, reason, pnl=None):
        entry = {
            "action": action,
            "as_of": str(as_of),
            "at": datetime.now(timezone.utc).isoformat(),
            "quote_price": round(price, 2),
            "fill_price": round(fill, 2),
            "qty": round(qty, 8),
            "fee": round(fee, 2),
            "reason": reason,
        }
        if pnl is not None:
            entry["realised_pnl"] = round(pnl, 2)
        self.state["trades"].append(entry)


class KrakenBroker(Broker):
    """Real orders on Kraken spot.

    Deliberately not implemented. Wiring this up requires a fresh Kraken API
    key with Query Funds and order permissions and NO withdrawal permission,
    and it should not happen until the paper ledger has a track record worth
    acting on. Constructing it raises rather than silently doing nothing,
    because a broker that accepts buy() and does nothing is the most dangerous
    object in this codebase.
    """

    def __init__(self):
        raise BrokerError(
            "Live trading is not implemented. MODE=live is refused until a "
            "paper track record exists and a scoped Kraken key is created."
        )


def get_broker():
    """Broker for the configured MODE."""
    if settings.MODE == "paper":
        return PaperBroker()
    if settings.MODE == "live":
        return KrakenBroker()
    raise BrokerError("unknown MODE: {}".format(settings.MODE))
