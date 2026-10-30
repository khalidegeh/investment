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
`/api/positions`, `GET /api/export/{dataset}/{symbol}?fmt=csv|parquet` (streams a download),
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
- **Hold-through by default.** No SELL is produced unless you opt in (`stop_loss_pct`, `sell_on_downtrend`). A drawdown or downtrend shows as a WARNING on a HOLD, with how far below your entry you are and the gain needed to get back.

## Known limits

- EDGAR XBRL statement history starts around 2009 (vendor-reported, not confirmed against SEC documentation). yfinance typically returns about 4 fiscal years. 30 to 40 years of statements is not available from these free sources.
- Free cash flow is defined as operating cash flow minus capex. Debt fields use the aggregate tag only and never sum components, so they can be NaN.
- Yahoo's chart endpoint is unofficial and may breach Yahoo's terms. yfinance row labels vary by version.
- Live behavior against Yahoo, SEC and yfinance has not been exercised from the build environment.
- The signals are plain technical rules with no demonstrated edge. Backtest and score them before relying on them.
- Holding through a drawdown does not guarantee recovery. A single stock can fall and stay down, and a concentrated portfolio is exposed to the same shock across names. Use the entry and recovery figures to size risk, not to assume it away.
