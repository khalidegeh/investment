# OpenAlpha

Self-hosted research workspace. Free data only, no API keys required to start.
Portfolio entries stay on disk and are never sent anywhere.

## Layout

```
openalpha/
  config/portfolio_config.example.json   copy to portfolio_config.json (git-ignored)
  backend/
    app/
      main.py            FastAPI app, JSON routes, streaming export routes
      engine.py          IngestionEngine: wires cache, retry, pacing and sources
      fetcher.py cache.py retry.py useragents.py symbols.py config.py errors.py
      models.py          unified statement schema
      sources/           yahoo.py (OHLCV), edgar.py (XBRL company facts)
      normalizers/       base.py, edgar.py, yfinance.py, service.py (EDGAR first)
      portfolio/         positions.py (entries, P/L), signals.py (trend rules)
      export/            writer.py (chunked CSV and Parquet, timestamped, atomic)
    tests/               mocked-transport suite, no network
  frontend/              planned (React, Vite, Tailwind, lightweight-charts)
```

## Status

| Module | State |
|---|---|
| Async ingestion, retry, cache, pacing | built, tested offline |
| Yahoo OHLCV, SEC EDGAR sources | built, tested offline, live UNVERIFIED |
| Statement normalizers (EDGAR, yfinance) and failover | built, tested offline, yfinance labels live UNVERIFIED |
| Positions: entry, P/L, signals | built, tested |
| Streaming CSV and Parquet export, API routes | built, tested |
| Quarterly figures (derived Q4), quarterly thesis check | built, tested on real Apple SEC data |
| News and SEC 8-K feed, NYSE clock, price alerts | built, tested offline (live UNVERIFIED) |
| Debt discriminator, DCF margin of safety | planned |
| Options flow, news and FRED, backtester, layout state, AI Scout | planned |
| Frontend | planned |

## Run

```bash
cd openalpha/backend
pip install -e ".[dev]"            # add ".[data]" for the yfinance fallback
cp ../config/portfolio_config.example.json ../config/portfolio_config.json   # then edit
export OPENALPHA_CONTACT=you@example.com      # required for SEC EDGAR
export OPENALPHA_PORTFOLIO=../config/portfolio_config.json
uvicorn --factory app.main:create_default --host 127.0.0.1 --port 8000
python -m pytest && ruff check . && mypy
```

Routes: `/health`, `/api/ohlcv/{symbol}`, `/api/statements/{symbol}/{income|balance|cashflow}`,
`/api/positions`, `/api/news/{symbol}?days=7` (headlines and SEC 8-Ks), `/api/market/clock`, `GET /api/export/{dataset}/{symbol}?fmt=csv|parquet` (streams a download),
`POST /api/export/{dataset}/{symbol}?fmt=...` (writes a timestamped file to the export directory),
`/api/export/positions`.

## Rules the code enforces

- **Cache first.** Identical requests inside 15 minutes read SQLite. Errors are never cached.
- **Backoff.** Exponential, factor 2, capped at 30 s, 5 retries, jitter, `Retry-After` honored. Only 408, 425, 429 and 5xx retry; a 404 fails at once.
- **Identifying user agent by default.** Browser rotation is opt-in and never used for SEC.
- **Symbols are validated** before reaching a URL, file name or SQL. Exports sanitise formula-like text cells.
- **Nothing is guessed.** Missing statement values are NaN; a missing price or FX rate excludes a line from totals and flags it.
- **Sharia gate.** A BUY signal needs `sharia: "compliant"` on the holding; anything else is downgraded to HOLD.
- **Anything you are invested in is included**, ETFs too: totals, P/L and signals. `instrument` is a label only.
- **Thesis check, not price.** Each stock gets INTACT, WATCH, BROKEN or UNKNOWN from annual revenue growth and diluted EPS growth, judged separately. A metric fails after 2 straight non-growth periods; BROKEN needs both to fail, and one failing is WATCH (a single decline, 2 slowdowns in a row, or revenue well below peers also give WATCH). A missing year breaks the chain; short history is UNKNOWN. Funds are N/A.
- **Per-holding plans.** `take_profit_pct` on a lot (0 = sell when profitable) produces a SELL once price reaches that level above your average entry. Enter `entry_price` as cost per share including commission so profit is real.
- **Quarterly first.** For SEC filers the thesis check compares each quarter with the same quarter a year earlier (matched by date, so 52 and 53 week years work) and needs 4 readings; otherwise it falls back to annual. The fourth quarter is derived (10-K year minus three quarters) and flagged. A one-time charge in a base quarter distorts the next year's growth figure.
- **News is untrusted text.** Headlines and filing titles are cleaned, length-capped, links limited to http(s), RSS parsed with defusedxml. Treat them as data to show, never as instructions.
- **Floor rule.** `never_sell_below_entry: true` on a lot (for example XQQ) turns any SELL that would fire under your average entry into a HOLD, with the reason shown as a WARNING and the gain needed to get back to entry.
- **Hold-through by default.** No SELL is produced unless you opt in (`stop_loss_pct`, `sell_on_downtrend`). A drawdown or downtrend shows as a WARNING on a HOLD, with how far below your entry you are and the gain needed to get back.

## Known limits

- The NYSE holiday list covers 2026 and 2027 only (from trader-skill, matches my recollection, nyse.com not opened). Other years raise instead of guessing.
- Alerts are pure functions with no scheduler yet; they inform, never trade.
- EDGAR XBRL statement history starts around 2009 (vendor-reported, not confirmed against SEC documentation). yfinance typically returns about 4 fiscal years. 30 to 40 years of statements is not available from these free sources.
- Free cash flow is defined as operating cash flow minus capex. Debt fields use the aggregate tag only and never sum components, so they can be NaN.
- Yahoo's chart endpoint is unofficial and may breach Yahoo's terms. yfinance row labels vary by version.
- Live behavior against Yahoo, SEC and yfinance has not been exercised from the build environment.
- The signals are plain technical rules with no demonstrated edge. Backtest and score them before relying on them.
- Holding through a drawdown does not guarantee recovery. A single stock can fall and stay down, and a concentrated portfolio is exposed to the same shock across names. Use the entry and recovery figures to size risk, not to assume it away.
