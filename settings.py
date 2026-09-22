"""
Central configuration for the trading bot.

Secrets come from .env and never from source. Strategy parameters live here
too, so the backtest and the live bot cannot drift apart the way bot.py and
live_bot.py did (the backtest had a 15% stop loss; the live bot did not).

No third-party dependency: .env parsing is ~10 lines, and keeping this
import-free means the bot still starts on a bare system Python.
"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"
LEDGER_PATH = PROJECT_DIR / "ledger.json"


def _load_env(path: Path = PROJECT_DIR / ".env") -> None:
    """Minimal .env reader. Real environment variables always win."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_env()


# --- Secrets -----------------------------------------------------------
PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN = os.environ.get("PUSHOVER_API_TOKEN", "")

# --- Mode --------------------------------------------------------------
# paper = simulated fills against real prices. live = real orders.
MODE = os.environ.get("MODE", "paper").strip().lower()

# --- Market ------------------------------------------------------------
KRAKEN_PAIR = "XXBTZUSD"        # Kraken's name for BTC/USD
COINBASE_PRODUCT = "BTC-USD"    # first fallback: another real exchange
YF_TICKER = "BTC-USD"           # last resort: a scraper, not an exchange API

# --- Strategy ----------------------------------------------------------
# Shared by backtest.py and live_bot.py. Single source of truth.
SMA_SHORT = 5
SMA_LONG = 150
SMA_TREND = 200
STOP_LOSS_PCT = 0.15          # exit if price falls 15% below entry

# --- Execution costs (used by the paper broker and the backtest) --------
TAKER_FEE_PCT = 0.0026        # Kraken taker fee, 0.26%
SLIPPAGE_PCT = 0.0005         # assumed adverse fill, 0.05%

# --- Candle semantics ---------------------------------------------------
# Kraken's (and yfinance's) most recent daily row is the CURRENT, still-forming
# day. The backtest runs on completed daily closes, so feeding it a partial
# candle makes the live signal flicker intraday against a strategy that was
# never tested that way. True = signal on completed candles only.
USE_COMPLETED_CANDLES_ONLY = True

# --- Data quality guards -----------------------------------------------
MIN_CANDLES = SMA_TREND + 20  # enough history to compute every MA
# A candle is timestamped by its OPEN. A daily candle that opened at 00:00
# yesterday CLOSED at 00:00 today, so freshness must be measured from
# open + duration, not from the open. Measuring from the open made every
# healthy fetch look a day and a half stale.
CANDLE_DURATION_HOURS = 24
MAX_CANDLE_AGE_HOURS = 36     # newest candle CLOSED longer ago than this = stale

# --- Paper account -----------------------------------------------------
PAPER_STARTING_CASH = 10_000.0

_PLACEHOLDER = "REPLACE_ME"
REQUIRED = ("PUSHOVER_USER_KEY", "PUSHOVER_API_TOKEN")


def validate() -> None:
    """Fail fast and legibly, rather than halfway through a run."""
    missing = [
        name for name in REQUIRED
        if not globals().get(name) or globals().get(name) == _PLACEHOLDER
    ]
    if missing:
        raise SystemExit(
            "Configuration error: {} not set.\n"
            "Edit {} and replace the placeholder values.\n"
            "Get them from https://pushover.net (user key on the home page, "
            "API token on your application's page).".format(
                ", ".join(missing), PROJECT_DIR / ".env"
            )
        )
    if MODE not in ("paper", "live"):
        raise SystemExit(
            "Configuration error: MODE must be 'paper' or 'live', got '{}'.".format(MODE)
        )


if __name__ == "__main__":
    print("Project dir: {}".format(PROJECT_DIR))
    print("Mode:        {}".format(MODE))
    print("User key:    {}".format("set" if PUSHOVER_USER_KEY not in ("", _PLACEHOLDER) else "NOT SET"))
    print("API token:   {}".format("set" if PUSHOVER_API_TOKEN not in ("", _PLACEHOLDER) else "NOT SET"))
    validate()
    print("Configuration OK.")
