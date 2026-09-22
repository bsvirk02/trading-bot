# BTC Trend-Following Bot

A daily moving-average trend system for BTC, with a paper-trading broker,
three-layer data validation and walk-forward analysis.

**Status: paper trading. Not trading real money, and the validation says it
shouldn't be.** See [Findings](#findings).

---

## What it does

Every day the bot fetches BTC daily closes, decides whether to be long or
flat, executes against a simulated account, and reports by push notification.

**The rules.** Hold BTC when the 5-day MA is above the 150-day MA *and* price
is above the 200-day MA. Exit when either condition fails, or when price falls
15% below the entry price. Long or flat only: no shorting, no leverage.

```
launchd / GitHub Actions ──▶ run_daily.sh
                                  │
                idempotency guard ─┤  (already ran today? stop)
                                  ▼
                            live_bot.py
                                  │
      data.py  ◀─────────────────┤  prices   (Kraken → Coinbase)
   strategy.py  ◀─────────────────┤  decide   (BUY / SELL / HOLD)
     broker.py  ◀─────────────────┤  execute  (PaperBroker → ledger.json)
     notify.py  ◀─────────────────┤  report   (Pushover)
     health.py  ◀─────────────────┘  watchdog (healthchecks.io)
```

`backtest.py` and `walkforward.py` run the same `strategy.py` and `broker.py`
against history, so the backtest cannot describe a different system from the
one that trades.

## Findings

Walk-forward validation over seven out-of-sample windows, 2020–2026, fitting
parameters on three years and testing on the next:

| Compounded out-of-sample | Return |
|---|---|
| Buy & hold | **1,098.8%** |
| Parameters re-fitted annually | 1,071.4% |
| Fixed 5/150 | 916.9% |

**Buy and hold beat every variant on return.** The strategy's case rests
entirely on drawdown — worst single window −50.0% against −67.0%, and 2022
returned 0.0% while holding lost 65.2%.

A full-history parameter sweep appeared to show 5/100 was nearly twice as good
as 5/150. Walk-forward found three *different* winners across seven fit
windows, which means that surface is noise and the sweep result should not be
acted on. This is the main reason the parameters have been left alone.

The strategy is best understood as insurance: it sits out crashes and gives up
return in bull markets. Over this period the premium slightly exceeded the
payout.

Full write-ups in [`docs/`](docs/).

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # add your Pushover keys
```

```bash
.venv/bin/python3 live_bot.py                       # today's run
.venv/bin/python3 test_strategy.py                  # 22 tests, no network
.venv/bin/python3 backtest.py --periods             # by market regime
.venv/bin/python3 backtest.py --sweep --score calmar
.venv/bin/python3 walkforward.py                    # the honest test
.venv/bin/python3 history.py --refresh              # re-download prices
```

Re-running the same day: `rm -f logs/.last_success` first — the run is
idempotent by design.

## Design notes

**No credential with financial power exists in this project.** Kraken's and
Coinbase's price endpoints need no authentication, so the bot computes signals
without any key. The only secrets are a Pushover token and a healthcheck URL;
the worst a full leak allows is sending a push notification.

**Prices come from the venue that would execute the trade.** Kraken is
primary, Coinbase is the fallback. Both are real exchange APIs. yfinance was
removed after Yahoo changed its response format and every request failed — a
fallback that doesn't work is worse than none, because it hides the real
failure behind a second one.

**The daily run is idempotent.** A per-day marker means the job can fire from
boot, from a schedule, and from a backstop trigger, and still run at most
once. The marker is written only when the signal computed *and* the
notification was delivered, so a failed push is retried rather than recorded
as done.

**Missed runs are the real failure mode.** launchd discards a schedule missed
while the machine is off rather than deferring it, which silently dropped two
days of signals in September 2026. Three triggers plus a catch-up guard
mitigate it; running in CI removes the dependency on any one machine; and an
external watchdog detects the case where nothing ran at all, which no
on-machine alerting can.

**Paper fills are pessimistic on purpose.** Every trade pays a 0.26% taker fee
and crosses a 0.05% spread on both sides. A round trip at an unchanged price
loses money, which is asserted in the tests. A costless backtest flatters
anything that trades.

**Live trading is deliberately unimplemented.** `KrakenBroker` raises on
construction. A broker that accepts `buy()` and silently does nothing is the
most dangerous object in a trading codebase.

## Layout

| File | Responsibility |
|---|---|
| `settings.py` | Loads `.env`, validates on startup, owns every parameter |
| `data.py` | Live prices: two sources, retries, validation |
| `strategy.py` | The entry/exit rules. The only copy |
| `broker.py` | Execution: `PaperBroker`, and `KrakenBroker` which refuses |
| `live_bot.py` | The daily run. Orchestration only |
| `notify.py` | Pushover, with the response actually checked |
| `health.py` | External watchdog pings |
| `backtest.py` | Backtest, regime comparison, parameter sweep |
| `walkforward.py` | Rolling out-of-sample validation |
| `history.py` | Pages Coinbase back to 2015, caches to CSV |
| `test_strategy.py` | 22 tests over synthetic price paths |
| `run_daily.sh` | Scheduler wrapper: guard, logging, failure alerting |

## Licence

MIT
