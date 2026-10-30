"""FastAPI routes against mocked upstream transports and a fixed clock."""
import json
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd
from fastapi.testclient import TestClient
from helpers import route
from test_normalizers import yf_frames
from test_sources import chart_payload

from app.config import Settings
from app.engine import IngestionEngine
from app.main import create_app

NOW = datetime(2026, 10, 30, 14, 5, 9, tzinfo=timezone.utc)


def loader(symbol: str) -> dict[str, pd.DataFrame]:
    i, b, c = yf_frames()
    return {"income": i, "balance": b, "cashflow": c}


@contextmanager
def client(
    tmp: str, chart_status: int = 200, portfolio: dict[str, object] | None = None
) -> Iterator[TestClient]:
    root = Path(tmp)
    if portfolio is not None:
        (root / "pf.json").write_text(json.dumps(portfolio))
    settings = Settings(root / "c.sqlite", 900.0, None, False, root / "pf.json", root / "exports")

    def chart(_: httpx.Request) -> httpx.Response:
        if chart_status != 200:
            return httpx.Response(chart_status)
        return httpx.Response(200, json=chart_payload())

    transport = route({"/v8/finance/chart/": chart})
    app = create_app(
        settings,
        engine_factory=lambda: IngestionEngine(settings, transport=transport),
        frame_loader=loader,
        clock=lambda: NOW,
    )
    with TestClient(app) as c:
        yield c


class ApiTests(unittest.TestCase):
    def test_health_and_ohlcv(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp) as c:
            self.assertEqual(c.get("/health").json(), {"status": "ok"})
            rows = c.get("/api/ohlcv/aapl").json()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["date"][:10], "2024-01-02")
            self.assertEqual(rows[0]["close"], 12.0)

    def test_validation_errors(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp) as c:
            self.assertEqual(c.get("/api/ohlcv/BAD$SYM").status_code, 422)
            self.assertEqual(c.get("/api/ohlcv/AAPL?years=0").status_code, 422)
            self.assertEqual(c.get("/api/statements/AAPL/nonsense").status_code, 422)
            self.assertEqual(c.get("/api/export/nonsense/AAPL").status_code, 422)
            self.assertEqual(c.get("/api/export/ohlcv/AAPL?fmt=xlsx").status_code, 422)

    def test_upstream_failure_is_502(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, chart_status=404) as c:
            r = c.get("/api/ohlcv/AAPL")
            self.assertEqual(r.status_code, 502)
            self.assertIn("error", r.json())

    def test_statements_fall_back_to_yfinance(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp) as c:
            rows = c.get("/api/statements/SHOP.TO/income").json()
            self.assertEqual(rows[-1]["revenue"], 2000.0)
            rows = c.get("/api/statements/AAPL/cashflow").json()   # no SEC contact configured
            self.assertEqual(rows[-1]["free_cash_flow"], 380.0)

    def test_streaming_csv_download(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp) as c:
            r = c.get("/api/export/ohlcv/AAPL")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.headers["content-type"].startswith("text/csv"))
            self.assertIn('filename="AAPL_ohlcv_20261030T140509Z.csv"',
                          r.headers["content-disposition"])
            lines = r.text.strip().splitlines()
            self.assertEqual(lines[0], "date,open,high,low,close,adj_close,volume")
            self.assertEqual(len(lines), 3)

    def test_post_writes_physical_files(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp) as c:
            for fmt, ext in (("csv", ".csv"), ("parquet", ".parquet")):
                r = c.post(f"/api/export/income/SHOP.TO?fmt={fmt}")
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertTrue(Path(body["path"]).exists())
                self.assertTrue(body["path"].endswith("SHOP.TO_income_20261030T140509Z" + ext))
                self.assertEqual(body["rows"], 2)

    def test_parquet_download(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp) as c:
            r = c.get("/api/export/ohlcv/AAPL?fmt=parquet")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content[:4], b"PAR1")

    def test_cors_is_local_only(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp) as c:
            ok = c.options("/health", headers={"Origin": "http://localhost:5173",
                                               "Access-Control-Request-Method": "GET"})
            self.assertEqual(ok.headers.get("access-control-allow-origin"), "http://localhost:5173")
            evil = c.options("/health", headers={"Origin": "https://evil.example",
                                                 "Access-Control-Request-Method": "GET"})
            self.assertIsNone(evil.headers.get("access-control-allow-origin"))


PORTFOLIO = {"accounts": {
    "TFSA": [{"symbol": "NVDA", "shares": 2, "entry_price": 10.0, "entry_date": "2026-01-02",
              "currency": "USD", "sharia": "compliant"}],
    "FHSA": [{"symbol": "SHOP.TO", "shares": 1, "entry_price": 8.0, "entry_date": "2026-01-02",
              "currency": "CAD"}],
}}


class PositionsApiTests(unittest.TestCase):
    def test_positions_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, portfolio=PORTFOLIO) as c:
            r = c.get("/api/positions")
            self.assertEqual(r.status_code, 200)
            snap = r.json()
            self.assertEqual(snap["usdcad"], 11.0)               # latest mocked CAD=X close
            lines = {ln["symbol"]: ln for a in snap["accounts"] for ln in a["lines"]}
            self.assertEqual(lines["NVDA"]["avg_entry"], 10.0)
            self.assertEqual(lines["NVDA"]["price"], 11.0)
            self.assertEqual(lines["NVDA"]["total_pl"], 2.0)       # (11 - 10) x 2
            self.assertEqual(lines["NVDA"]["day_pl"], -2.0)        # (11 - 12) x 2
            self.assertEqual(lines["NVDA"]["signal"]["action"], "N/A")   # only 2 bars of history
            self.assertAlmostEqual(snap["total_pl_base"], 2.0 * 11.0 + 3.0)   # USD x 11 + CAD

    def test_positions_csv_export(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, portfolio=PORTFOLIO) as c:
            r = c.get("/api/export/positions")
            self.assertEqual(r.status_code, 200)
            self.assertIn("PORTFOLIO_positions_20261030T140509Z.csv",
                          r.headers["content-disposition"])
            self.assertTrue(r.text.startswith("account,symbol,currency,shares,avg_entry"))

    def test_missing_portfolio_is_409(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp) as c:
            r = c.get("/api/positions")
            self.assertEqual(r.status_code, 409)
            self.assertIn("not found", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
