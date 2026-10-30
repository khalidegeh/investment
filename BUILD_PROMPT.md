# Build Prompt — Reliable Algorithmic Trading System (Canada / CRA-aware)

> Use this document as the master specification when building, extending, or
> reviewing this system. Every future change should be checked against the
> Reliability and CRA sections before merging.

## 1. Objective

Build a **boring, reliable** algorithmic trading research and paper-trading
system for Canadian equities/ETFs. The system prioritizes determinism,
reproducibility, risk control, and tax-record integrity over sophistication.

Inspiration: the "3-layer" idea (research desk → data layer → execution
layer). This spec reinterprets it reliability-first:

| Layer | In this system | Role |
|---|---|---|
| Data | `data/` providers (CSV now, OpenBB ODP later) | Clean, point-in-time market data |
| Research | `strategy/` + `backtest/` | Deterministic signals, reproducible backtests |
| Execution | `execution/` paper broker (live broker later) | Idempotent orders, fills, reconciliation |
| Cross-cutting | `risk/` manager | Position sizing, limits, kill switch |
| Cross-cutting | `ledger/` trade ledger | Immutable log, ACB tracking, CRA tax export |

The LLM (if used at all) lives **outside** the decision path: it may summarize
research or draft reports, but it never generates orders.

## 2. Non-goals (explicit)

- No live trading in v1. Paper trading only until the acceptance criteria pass.
  (Operator context: Wealthsimple has no public trading API, so there is no
  live-broker phase for this setup — the system is a research and
  paper-trading tool by design, not by deferral.)
- No LLM-generated orders. Ever, without a separate human-approved design review.
- No "AI alpha." Signals are explicit, named, versioned rules.
- Not tax advice. The ledger produces *records*; classification
  (capital vs. business income) is the user's CPA's job.
- Not a halal certification. `halal_exclusions` is a first-pass activity
  filter; final compliance is for a qualified scholar.

## 3. Architecture principles

1. **Determinism.** Same inputs + same seed ⇒ byte-identical outputs.
   No wall-clock reads, no unordered dict/set iteration, no unseeded randomness
   in the critical path.
2. **Event-driven backtest.** The backtester replays bars in chronological
   order through the *same* strategy/risk/execution code as paper trading.
   No lookahead: a signal generated on bar `t` may only fill at bar `t` or later.
3. **Idempotent execution.** Every order carries a `client_order_id`. Replaying
   the same order twice must not double-fill.
4. **Immutable ledger.** Fills are appended, never edited or deleted.
   Corrections are new offsetting entries with a reason field.
5. **Fail-closed risk.** Any risk breach halts new orders (kill switch).
   The system must be safe when confused.

## 4. Module specifications

### 4.1 `data/` — market data providers
- `DataProvider` ABC: `get_bars(symbol, start, end) -> DataFrame`
  with columns `[timestamp, open, high, low, close, volume]`, UTC timestamps,
  sorted ascending, no duplicates.
- `CsvProvider` (v1): reads `data/<SYMBOL>.csv`. Validates schema on load;
  raises on gaps/duplicates rather than silently continuing.
- Future: `OdpProvider` wrapping OpenBB ODP (REST/MCP). Must record
  `data_asof` and provider version with every dataset for reproducibility.

### 4.2 `strategy/` — signal generation
- `Strategy` ABC: `on_bar(symbol, history, position=0) -> list[Signal]`.
- `Signal`: `{symbol, side: BUY|SELL, quantity | target_weight, reason: str,
  strategy_name, strategy_version}`.
- Strategies are pure functions of (history, parameters, position). Parameters
  live in config, are logged with every run, and are versioned
  (`strategy_version`).
- v1 ships `EarningsApproachSignal`: enter when
  `exit_days_before < days_to_earnings <= entry_days_before` and close is
  above `SMA(trend_window)` while flat; flatten when
  `days_to_earnings <= exit_days_before` while holding. Earnings dates come
  from `data/earnings/<SYMBOL>.csv`. Symbols in `halal_exclusions` never
  signal. `SmaCrossover` remains as a secondary example only.

### 4.3 `risk/` — pre-trade risk
`RiskManager` enforces, in order:
1. Kill switch state (manual or auto-tripped) ⇒ reject everything.
2. Max per-symbol position: `position_value <= max_position_pct * equity`.
3. Max total exposure: `gross_exposure <= max_exposure_pct * equity`.
4. Max daily loss: if `day_pnl <= -max_daily_loss_pct * start_of_day_equity`
   ⇒ trip kill switch, reject everything, require manual reset.
5. Position sizing: convert `target_weight` signals to share quantities using
   the latest close; round down; minimum 1 share or reject with reason.

Every decision returns `(approved: bool, quantity: int, reason: str)` and is
logged. Rejections must explain themselves.

### 4.4 `execution/` — order execution
- `Broker` ABC: `place_order(order) -> Fill`.
- `Order`: `{client_order_id, symbol, side, quantity, order_type: MARKET,
  created_at}`. `client_order_id` must be unique per intent
  (e.g. `f"{run_id}:{symbol}:{bar_ts}:{seq}"`).
- `Fill`: `{client_order_id, symbol, side, quantity, price, commission,
  filled_at}`.
- `PaperBroker` (v1): fills market orders at the bar close ± configurable
  slippage (bps), applies per-trade commission from config. Keeps an
  idempotency registry: a repeated `client_order_id` returns the original
  fill without executing again.
- Every fill is immediately appended to the ledger (no fill ⇒ no ledger entry).

### 4.5 `ledger/` — the CRA-friendly trade ledger ⭐
This is the module that makes tax season survivable.

- **Immutable append-only log.** `record_fill(fill)` appends a `Trade`
  `{timestamp, symbol, side, quantity, price, commission, fees,
  client_order_id, notes}`. No update/delete API exists.
- **Adjusted Cost Base (ACB) per CRA rules, per symbol:**
  - Buy: `new_acb_per_share = (old_acb_total + cost + commission) / new_shares`.
    Commissions are capitalized into ACB (CRA treatment).
  - Sell (including partial): `gain = proceeds − (qty × acb_per_share) − sell_commissions_fees`.
    ACB per share is unchanged by a sale.
  - Selling the entire position resets ACB to zero for the next purchase.
- **Realized gains report:** `realized_gains(start, end)` returns every
  disposition with `{date, symbol, quantity, proceeds, acb, expenses,
  gain_loss}` — the raw material for Schedule 3.
- **Tax export:** `export_tax_csv(path, year)` writes one CSV per tax year
  with columns `date,symbol,quantity,proceeds,acb,expenses,gain_loss`.
  Deterministic column order, UTF-8, ISO dates.
- **Superficial loss rule:** v1 does *not* auto-adjust for it. The export
  flags repurchases within 30 days of a loss sale in a `notes` column so the
  user's CPA can review. (Full superficial-loss accounting is a defined
  future work item, not a silent behavior.)
- **Reconciliation:** `reconcile(broker_statement_trades)` compares ledger
  vs. broker records and reports mismatches. Run monthly.

### 4.6 `backtest/` — event-driven backtest engine
- Replays bars chronologically, calling the real `Strategy`, `RiskManager`,
  and `PaperBroker`.
- Tracks equity curve, daily PnL, max drawdown, Sharpe (informational only).
- Output: `results.json` with `{config_hash, data_hash, strategy_version,
  seed, trades, equity_curve, metrics}`. `config_hash` + `data_hash` make
  any result reproducible or explain why it isn't.
- Must assert no-lookahead in tests (a strategy that peeks at tomorrow's
  close must fail loudly in a test harness).

## 5. Reliability requirements (acceptance)

- [ ] Backtest is deterministic: two runs, same seed ⇒ identical `results.json`.
- [ ] No-lookahead test passes.
- [ ] Paper broker idempotency test passes (duplicate `client_order_id` ⇒ one fill).
- [ ] Risk manager tests pass: kill switch trips on daily-loss breach and
      blocks all further orders until manual reset.
- [ ] Ledger ACB tests pass, including partial sales and full liquidation.
- [ ] A 2-year backtest on sample data completes without exceptions and
      produces a reconciliation-clean ledger.

## 6. CRA / tax requirements (acceptance)

- [ ] Every fill lands in the immutable ledger with commission captured.
- [ ] ACB math matches CRA treatment (commissions capitalized; partial-sale
      ACB per share unchanged). Verified by unit tests with hand-computed values.
- [ ] `export_tax_csv` produces a deterministic, CPA-readable file per tax year.
- [ ] Superficial-loss repurchases are flagged, not silently adjusted.
- [ ] Docs state clearly: the system keeps *records*; income classification
      (capital vs. business) is a CPA decision. Active/systematic bot trading
      will likely be viewed as business income (100% taxable) — plan for it.
- [ ] Operational guidance in README: this system is a research/paper-trading
      tool. The operator trades actively in a TFSA at Wealthsimple; systematic
      trading there carries CRA business-income scrutiny risk (retroactive).
      Paper trading creates no tax events. Any move of systematic trading to a
      non-registered account is a CPA-guided decision, not a code change.

## 7. Configuration

All behavior comes from `config.yaml` (example: `config.example.yaml`).
No magic constants in code. Config is hashed into backtest results.

```yaml
data:      { provider: csv, path: data/ }
strategy:  { name: sma_crossover, version: "1.0", fast: 20, slow: 50 }
risk:
  max_position_pct: 0.10
  max_exposure_pct: 0.60
  max_daily_loss_pct: 0.03
  kill_switch: false
execution: { slippage_bps: 5, commission_per_trade: 4.95 }
ledger:    { base_currency: CAD }
```

## 8. Build phases

- **Phase 0 (this base):** scaffold + ledger ACB + risk + paper broker +
  backtester + `EarningsApproachSignal` + halal exclusions + tests.
  No live anything.
- **Phase 1:** earnings-calendar sourcing (manual CSV → OpenBB ODP),
  walk-forward validation harness, T5008/broker-statement reconciliation
  tooling, broader growth universe with halal screening workflow.
- **Phase 2:** expanded tax export (superficial-loss worksheet), monthly ops
  checklist, contribution-aware position sizing for biweekly deposits.
- **Phase 3:** CLOSED for this operator setup — Wealthsimple has no public
  trading API, so no live-broker adapter is planned. If the broker ever
  changes, a live adapter requires a separate human-approval-gated design
  review, 3+ months of clean paper trading, and CPA sign-off on the ledger
  format. Not covered by this prompt.

## 9. Working agreements for future builds

- Every change cites the section of this prompt it satisfies.
- New strategies ship with: rationale, parameters, version, backtest report,
  and a written falsification condition ("we will stop using this if X").
- Never silently change ledger math. ACB/tax logic changes require a
  migration note and re-verification of all ledger tests.
- Educational tool, not financial or tax advice. Say so in the README.
