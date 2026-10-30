# OpenAlpha

Self-hosted research workspace. Free data only, no API keys required to start.

## Status

| Module | Path | State |
|---|---|---|
| Async ingestion, retry, cache, rate gates | `src/openalpha/{fetcher,retry,cache,engine}.py` | built, tested offline |
| Yahoo chart source (30y OHLCV) | `src/openalpha/sources/yahoo.py` | built, parser tested offline, live UNVERIFIED |
| SEC EDGAR source (company facts) | `src/openalpha/sources/edgar.py` | built, tested offline, live UNVERIFIED |
| Statement normalizer (income, balance, cash flow) | `src/openalpha/statements/` | planned |
| Streaming CSV / parquet export | `src/openalpha/export/` | planned |
| Quant library (CAPM, Sharpe, Sortino, Treynor, vol, z-bands) | `src/openalpha/quant/` | planned |
| Backtester | `src/openalpha/backtest/` | planned |
| Options flow scanner | `src/openalpha/options/` | planned |
| News + FRED macro | `src/openalpha/macro/` | planned |
| Layout state (`openalpha_layout.json`) | `src/openalpha/layout/` | planned |
| AI Scout router | `src/openalpha/scout/` | planned |
| FastAPI app | `src/openalpha/api/` | planned |
| Frontend | `frontend/` | planned |

## Rules the engine follows

- **Cache first.** Identical requests inside the TTL (default 15 minutes) read SQLite, not the network. Errors are never cached.
- **Backoff.** Exponential, factor 2, capped at 30 s, up to 5 retries, with jitter. `Retry-After` is honored (capped at 30 s). Only 408, 425, 429 and 5xx are retried; a 404 fails at once.
- **Per-host pacing.** A minimum interval and a concurrency cap per host, so a bulk run cannot hammer one site.
- **Identifying user agent by default.** Browser-style rotation is opt-in (`OPENALPHA_ROTATE_UA=1`) and never used for SEC EDGAR, which requires an identifying agent with a contact address (`OPENALPHA_CONTACT`).
- **Symbols are validated** before they reach a URL, a file name or the database.

## Run the tests

```bash
cd openalpha
pip install -e . pandas-stubs
PYTHONPATH=src python -m unittest discover -s tests
mypy
```

## Known limits

- Yahoo's chart endpoint is unofficial. It can change or block without notice, and using it may breach Yahoo's terms. Treat it as best effort for personal research.
- Live behavior against Yahoo and SEC has not been exercised from the build environment (network to those hosts is blocked there). Tests use mocked transports.
