# PRD — Stock Agent ("Personal Yahoo Gold")

**Repo:** trading-system (`~/workspace/trading-system/`)
**Author:** Khalid Egeh
**Status:** Draft — one open decision (see §9)
**Last updated:** 2026-10-29

---

## 1. Problem

Khalid holds a concentrated tech portfolio (NVDA, AAPL, ON Semiconductor, SHOP.TO, XQQ across TFSA/FHSA) and buys on conviction, but:

- He doesn't always have time to read *what* is moving his stocks on a given day — he just sees the portfolio down $200+ and doesn't know why.
- His entry points suffer: he buys, then news breaks that he hadn't seen.
- He has no hedge against his AI-position drawdowns.
- He wants a monthly, genuinely-researched shortlist of stocks to consider — not a brainstormed ticker list.

He already runs a deterministic research/paper-trading system in this repo (fundamentals scorer, P/L tracker, Wednesday allocator, weekday market watch). The stock agent is the intelligence layer on top of it — and, over time, a personal equivalent of Yahoo Finance Gold's analysis tooling, built from free data.

## 2. Goals

1. **Explain daily moves:** every trading day, tell him in plain English what moved each holding and why (news driver, sector move, or no-news noise).
2. **Protect entry points:** surface material news/events *before* they become portfolio damage — earnings dates, product events, analyst actions, macro prints relevant to his names.
3. **Monthly candidates:** one genuinely-researched shortlist per month of stocks to consider — every pick with a plain-English case (what it does, why it should keep growing, what could go wrong), good financials (P/E, forward earnings), and Sharia-compliant, always.
4. **Hedge sleeve:** identify hedge positions that offset AI-drawdown days (scope decision open — §9).
5. **Paper-trade in sync:** the agent paper-trades alongside him so its track record stays comparable to his real decisions. It never places real orders; he executes himself.

## 3. Non-goals

- No live trading, no order placement, no broker integration (Wealthsimple has no public API — this is permanent).
- No buy/sell directives. The agent presents researched candidates with cases and paper-trades its own book; the human decides and executes.
- No licensed research replication (Morningstar / Motley Fool reports). The substitute is the systematic plain-English research format.
- No new-stock universe re-screening on a loop. The monthly shortlist is deliberate, not a scan of 5,000 tickers.
- No sentiment-model (FinBERT) leg. Dropped by owner decision.

## 4. Build phase vs. operate phase

This PRD covers **two phases with different budgets**:

- **Build phase (one-time):** implement the modules in §6. Heavier lift; use the frontier model (Opus). Cap subagent fan-out during the build (known Opus issue with runaway parallel subagents draining quota). Ends with all tests passing (repo culture: full suite green, currently 51/51). Adversarial review of the build by a different vendor/model before merge.
- **Operate phase (ongoing):** the daily agent. **Sonnet or cheaper only — never above Sonnet.** No dev work in this phase: it runs the built tools, reads public information, and produces insight. Scope of each run must be narrow and explicit (Claude drifts on vague instructions).

## 5. Operating constraints (hard)

1. **Model cap:** Sonnet or below in operate phase. No exceptions.
2. **Cheap to run:** each run is scoped to the day's holdings + one monthly deep job. Sized to a small share of weekly usage; per-run turn/budget caps; global stop at a set share of weekly allowance (owner to set the number).
3. **Sharia gate is absolute:** no stock is surfaced, scored, or paper-traded unless it passes the business-activity screen and is checked against Zoya (AAOIFI). "Questionable" = fail. Software screening is preliminary; final compliance belongs to a qualified scholar — the agent must say this, not decide it.
4. **Financial quality bar:** every candidate must show good financials — P/E and forward earnings explicitly checked and quoted. No story stocks without numbers.
5. **Autonomy:** runs without the human. Touchpoints are the existing alert slots only — morning brief, midday, and close (current watch: 9:30 AM brief, 11 AM / 1 PM / 3 PM alert-only checks, 4:30 PM tape). Agent output feeds these slots; it does not invent new ones.
6. **Deterministic core:** valuation math, scoring, and P/L are code with tests, not model prose. The model writes insight; the code computes numbers.
7. **Kill switch + audit:** a kill file the loop checks before each run; every run logs inputs, outputs, and cost; paper trades are append-only with rationale (same ledger discipline as the real book).

## 6. Functional requirements

### 6.1 Holdings explainer (daily)
For each holding (NVDA, AAPL, ON, SHOP.TO, XQQ): today's move %, and the driver in one or two plain-English sentences — specific news, sector-wide move, or "no clear driver; within normal volatility." Must distinguish a listing from a cause: no invented catalysts. Feeds the morning brief and alert slots.

### 6.2 Event radar (daily)
Watchlist of upcoming events per holding: earnings dates, product events, analyst days, relevant macro prints (CPI, BoC/Fed decisions). Flag anything within 7 days. (Known gap this fixes: v1 exit logic covers earnings but not product events — e.g., Apple launch events inside the hold window.)

### 6.3 Fair value module (build)
Per holding and per candidate: DCF from free cash flow, P/E vs. own 5-year history, analyst consensus price target. Displayed as a range with stated assumptions, never a single "true price." Inputs: Finnhub (already connected) + yfinance. Free tiers only — no new data spend.

### 6.4 Portfolio analytics (build)
On top of the existing `pnl_tracker.py`: realized/unrealized P/L (already there), plus volatility, diversification/concentration readout, and benchmark comparison. This is the "Gold portfolio analytics" equivalent.

### 6.5 Screener (build)
A UI over the existing fundamentals scorer: filter the scored universe by P/E, forward P/E, revenue growth, and Sharia status. Supports the monthly shortlist workflow.

### 6.6 Charting (build)
Price charts with technical overlays and coded pattern detection. Math, not model judgment.

### 6.7 Monthly shortlist (monthly job)
3–5 candidates max. Each carries: plain-English case (business, growth thesis, risks), P/E + forward earnings quoted, Sharia status with Zoya check, fair-value range, and what would change the thesis. Delivered as a document, not chat prose.

### 6.8 Hedge sleeve (monthly job, scope per §9)
Candidates selected for low/negative correlation to the AI core (NVDA/ON/AAPL), to offset drawdown days. Same research bar as §6.7.

### 6.9 Paper trading ledger (ongoing)
Append-only paper book seeded from his real positions. The agent logs paper entries/exits with rationale and timestamps. Weekly sync note compares paper vs. real — "in sync" means comparable, not identical.

## 7. Data sources

- Finnhub (connected; free tier: 60 calls/min) — quotes, fundamentals, analyst targets.
- yfinance — TSX quotes (SHOP.TO, XQQ), FX, long price history.
- Public web — news drivers for the holdings explainer (Reuters, company IR, established financial press). No scraping behind paywalls; no credential sharing.
- Zoya (public screener) — Sharia status first pass.

## 8. Rollout

- **Week 1 — build, interactive only:** fair value module + portfolio analytics, tests green, file artefacts per task.
- **Week 2 — build:** screener UI + charting; holdings explainer running manually.
- **Week 3 — operate pilot:** daily explainer + event radar on Sonnet, feeding existing alert slots; paper ledger seeded.
- **Week 4 — monthly loop:** first monthly shortlist + hedge sleeve candidates.
- Advance each phase only when the previous one's outputs are accurate and the review burden is sustainable.

## 9. Hedge universe scope — DECIDED (owner, 2026-10-29)

**Option B selected:** the hedge sleeve is exempt from the tech-only rule. Hedges may come from outside tech — energy named as the preferred direction. The core portfolio rule (tech-facing names only, diversification across tech) is unchanged; the exemption applies to the hedge sleeve alone. Standing constraints still apply to every hedge candidate: Sharia gate absolute, financials must be good (P/E + forward earnings quoted), plain-English case required.

## 10. Acceptance criteria

1. Holdings explainer correctly attributes ≥90% of ±2% daily moves over a 2-week pilot (spot-checked against primary sources).
2. Event radar flags every earnings/product event for holdings 7+ days ahead (tested against known 2026 dates: AAPL/ON Nov 2).
3. Fair value module reproduces its numbers from code + tests; assumptions displayed, no black-box figures.
4. Monthly shortlist: every pick has a plain-English case, quoted P/E + forward earnings, and a Zoya status — zero exceptions.
5. Operate-phase runs stay on Sonnet-or-below and within the usage cap for 2 consecutive weeks.
6. Paper ledger reconciles to the real ledger's positions at seed; all paper trades carry rationale and timestamps.
7. Kill file stops the loop; run logs record cost per run.
