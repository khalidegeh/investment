# Trading System — Reliable Base (v0)

A boring-on-purpose algorithmic trading research and paper-trading system,
built for a Canadian resident trading a TFSA at Wealthsimple. Deterministic
core, hard risk limits, and a CRA-friendly trade ledger (ACB tracking +
tax-year export) from day one.

> **Not financial advice. Not tax advice.** Educational engineering scaffold.
> Talk to a licensed professional before trading real money or filing taxes.

## How this fits your setup

- **Research tool, not an execution system.** Wealthsimple has no public
  trading API, so there is no live-broker phase for this setup — and that's
  fine. This system exists to *test whether your pre-earnings thesis has
  positive expectancy* before you keep deploying real money into it.
  Paper trading creates no tax events.
- **TFSA + active trading = CRA attention.** If CRA ever views the activity
  as carrying on a business, the tax treatment changes retroactively. This
  system keeps immaculate records either way, and the backtester tells you
  whether the strategy earns its risk before you scale it. Your CPA decides
  classification, not this README.
- **Halal screen.** `halal_exclusions` in config keeps excluded symbols out
  of the universe entirely. It's a first-pass activity filter — final
  compliance is for a qualified scholar, not a YAML file.

## Quickstart

```bash
cd trading-system
cp config.example.yaml config.yaml
PYTHONPATH=src python3 -m unittest discover -s tests -v   # 20 tests
# data/AAA.csv is bundled SYNTHETIC demo data — replace with real CSVs:
# timestamp,open,high,low,close,volume (UTC, ascending)
# data/earnings/<SYMBOL>.csv is likewise synthetic (single `date` column);
# source real earnings dates manually or via OpenBB in Phase 1.
PYTHONPATH=src python3 -m tradingsystem.run_backtest --config config.yaml --symbol AAA
```

## Layout

```
src/tradingsystem/
  data/        market-data providers (CSV now, OpenBB ODP later) + earnings calendar
  strategy/    deterministic signals — v1 is the pre-earnings approach signal
  risk/        pre-trade risk: sizing, limits, kill switch
  execution/   paper broker (no live-broker phase: Wealthsimple has no public API)
  ledger/      immutable trade log, ACB math, CRA tax-year CSV export
  backtest/    event-driven backtest engine (same code path as paper trading)
tests/         ACB, risk, broker-idempotency, no-lookahead, earnings-signal tests
BUILD_PROMPT.md  master spec — read before changing anything
```

## The 30-second model

`backtest/engine.py` replays bars in order through the **same**
`Strategy → RiskManager → Broker → Ledger` chain that paper trading uses.
The v1 strategy buys quality large-caps ~30 days before earnings (above
their 20-day trend) and flattens the day before the report — your thesis,
coded and testable. Every fill is appended to the immutable ledger; the
ledger computes Adjusted Cost Base per CRA rules and can export a
per-tax-year CSV your CPA can actually use.

## CRA notes (Canada)

- Active/systematic trading is likely to be treated as **business income**
  (100% taxable), not capital gains. Plan for it; your CPA decides.
- Keep systematic trading in a **non-registered** account. Business-like
  activity inside a TFSA can be taxed retroactively.
- The ledger flags repurchases within 30 days of a loss sale for
  superficial-loss review — it does not silently adjust.
