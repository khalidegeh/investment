# Earnings date sources (staged 2026-10-08 by research)

Each CSV holds upcoming announcement dates only. Confidence per symbol:

## AAPL — 2026-11-02 — CONFIRMED
Apple announced Q4 FY2026 results for Monday, November 2, 2026 (1:30pm PT
release, 2:00pm PT call) on its Investor Relations page, reported by
MacRumors on 2026-10-06:
https://www.macrumors.com/2026/10/06/apple-q4-2026-earnings-nov-2/
Note: aggregators (TipRanks, Zacks) still show Oct 28/29 — stale pattern
estimates. Nov 2 is the company-confirmed date. It is a Monday, which
exposes the exit-logic weekend edge case documented in the strategy notes.

## NVDA — 2026-11-18 — ESTIMATED (conflicting sources)
- Trefis 2026-09-27 states Nov 17; Trefis 2026-10-06 says "on or around
  November 18".
- MarketBeat estimates Nov 18 (after close).
- TipRanks labels Nov 25 "confirmed" but its own text says the date is
  pattern-estimated.
NVIDIA's last five reports all landed on Wednesdays; Nov 18, 2026 is a
Wednesday. Used 2026-11-18 as the best estimate. Replace when NVIDIA
announces (usually ~3 weeks prior via press release).

## AVGO — 2026-12-10 — ESTIMATED (announced per Zacks, unconfirmed elsewhere)
- Zacks: "announced... December 10, 2026" (page updated ~69 days ago).
- Trefis 2026-10-06: "expected on or around December 9, 2026".
- MarketBeat: Dec 9 estimated.
Used 2026-12-10; treat Dec 9 as the alternative until Broadcom confirms.

## What is still missing
Historical announcement dates (needed for backtests over 2024–2026) are
not yet sourced — that remains the Phase-1 data task (manual export or
OpenBB ODP). These CSVs only cover the next upcoming report per symbol,
which is what the forward/paper-trading signal needs.

## Batch 2 — 2026-10-11 (research-staged, review before use)

Ten large-cap growth names to widen the universe beyond AAPL/NVDA/AVGO.
Halal status below is a first-pass *activity* screen only (conventional
finance / alcohol / gambling / weapons as primary business) — financial
ratio screens (debt, interest income) are NOT done, and final compliance
is for a qualified scholar. Financial ratios quoted are from the same
sources as the dates, for reference only.

### MSFT — 2026-10-28 — CONFIRMED (announced)
Zacks: "announced they will report... October 28, 2026" after close;
MarketBeat conference call page agrees.
http://www.zacks.com/stock/research/MSFT/earnings-calendar
Halal activity: passes (software/cloud). No exclusions triggered.

### GOOGL — 2026-10-28 — CONFIRMED
stockanalysis.com lists "next confirmed earnings date... Wednesday,
October 28, 2026, after market close"; MarketBeat estimates the same day.
https://stockanalysis.com/stocks/googl/statistics/
Halal activity: passes (ads/tech). Debt/equity 0.19 per stockanalysis —
passes the usual ratio screens, but do the ratios properly with
financials before relying on it.

### META — 2026-10-28 — CONFIRMED
TipRanks: "Report Date, Oct 28, 2026, After Close (Confirmed)".
https://www.tipranks.com/stocks/meta/earnings
Halal activity: passes (social/ads). No exclusions triggered.

### NOW — 2026-10-28 — CONFIRMED (per Zacks), BMO pre-market per MarketBeat
Zacks: "This is a confirmed date of earnings release... AMC";
MarketBeat estimates Oct 28 before market open. Time-of-day unconfirmed.
http://www.zacks.com/stock/research/NOW/earnings-calendar
Halal activity: passes (enterprise software). No exclusions triggered.

### AMZN — 2026-10-28/29 — ESTIMATED (conflicting)
AltIndex: "scheduled to report... Wednesday, October 28, 2026";
nextearningsdate: 10/29/2026 projected (after close).
https://altindex.com/earnings-calendar/amzn
https://www.nextearningsdate.com/amzn.html
Used 2026-10-28; treat Oct 29 as the alternative until Amazon confirms.
Halal activity: passes (retail/AWS primary business). Marketplace sells
some non-compliant goods — scholar call on whether that matters.

### TSLA — 2026-10-21 — CONFIRMED
TipRanks: "Report Date, Oct 21, 2026, After Close (Confirmed)"; company
Q&A webcast scheduled 5:30pm ET.
https://www.tipranks.com/stocks/tsla/earnings
Halal activity: passes (EV/autos/energy). No exclusions triggered.

### NFLX — 2026-10-20 — CONFIRMED
TipRanks: "Report Date, Oct 20, 2026, After Close (Confirmed)".
https://www.tipranks.com/stocks/nflx/earnings
Halal activity: borderline — streaming content includes material some
scholars exclude. Screeners generally pass it; this is a judgment call
for a qualified scholar, not this file.

### AMD — 2026-11-03 — CONFIRMED (company press release)
AMD announced Oct 6, 2026: fiscal Q3 2026 results "on Tuesday, Nov. 3,
2026, after the market close."
https://www.globenewswire.com/news-release/2026/10/06/3376019/0/en/amd-to-report-fiscal-third-quarter-2026-financial-results.html
Halal activity: passes (semiconductors). No exclusions triggered.

### PLTR — 2026-11-02 — ESTIMATED
FXEmpire: "next earning report is scheduled for Nov 02, 2026";
Zacks: estimated 11/02/2026; MarketBeat: estimated Nov 2.
https://www.fxempire.com/stocks/pltr/earnings
Halal activity: FLAG — defense/government AI contracts (Maven Smart
System is a formal DoD program of record). Weapons-adjacent revenue is
a real screening question; get a scholar's ruling before including.

### QCOM — 2026-11-04 — CONFIRMED
stockanalysis.com: "next confirmed earnings date is Wednesday, November
4, 2026, after market close"; Zacks estimates the same day.
https://stockanalysis.com/stocks/qcom/statistics/
Halal activity: passes (semiconductors). No exclusions triggered.

## Batch-2 window map (as of 2026-10-11, entry rule 1 < DTE <= 30)
In window NOW: NFLX (9), TSLA (10), MSFT/GOOGL/META/NOW (17),
AMZN (17–18), AAPL/PLTR (22), AMD (23), QCOM (24).
Upcoming: NVDA (38, enters ~Oct 19), AVGO (60, enters ~Nov 10).
Note the Oct-28 cluster: MSFT, GOOGL, META, NOW (+AMZN ~28/29) all
report the same session — the strategy flattens everything the day
before (Oct 27), so this is a concentrated exit day, not diversified
signal. With 10+ names in-window at once, max_exposure_pct 0.60 and
weight 0.10 cap real concurrent positions at 6 — a backtest over this
crowded window is a good stress test of the risk limits.

---

## Batch 3 — 2026-10-14 (research-staged, review before use)

Khalid opened a new ON (onsemi) position in the TFSA on 2026-10-14
(ledger-logged). One next-report date staged below; config untouched —
ON is not in the config universe list yet.

### ON — 2026-11-02 — ESTIMATED (pattern-based, not company-confirmed)
- MarketBeat: "Q3 2026 earnings is estimated for Monday, November 2,
  2026, based on past reporting schedules," conference call at
  9:00 AM ET. Page last updated ~72 days ago; company has not confirmed.
  https://www.marketbeat.com/stocks/NASDAQ/ON/earnings/
- Zacks earnings calendar: estimated 11/02/2026 ("neither Zacks... can
  guarantee the accuracy... of any of the Information").
  http://www.zacks.com/stock/research/ON/earnings-calendar?tab=dividends
- Most recent report: Q2 2026 on Monday, August 3, 2026 — $0.74 EPS vs
  $0.72 consensus; Q3 guidance 0.810–0.930 EPS on $1.7B–$1.8B revenue.
Halal activity: passes first-pass (power/analog semiconductors —
automotive and industrial end-markets, no alcohol/gambling/conventional
finance as primary business; like AMD/QCOM). Financial ratio screens
NOT done; final compliance is for a qualified scholar.

## Batch-3 window math (as of 2026-10-14, entry rule 1 < DTE <= 30)
ON: DTE 19 — inside the entry window on the day he bought it.
Nov 2 is a Monday, so v1.1 exits on Friday Oct 30 (last weekday before).
Note: AAPL (CONFIRMED) and PLTR (est.) also report Nov 2 — Oct 30 is a
concentrated multi-name exit day across the FHSA and TFSA books.
Replace the estimate when onsemi announces (watch for a press release;
recent quarters were confirmed only a few weeks ahead).
