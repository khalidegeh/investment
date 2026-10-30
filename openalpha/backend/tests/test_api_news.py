"""News, market clock and quarterly thesis through the API, with SEC and Yahoo mocked."""
import json
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from helpers import route
from test_sources import chart_payload

from app.config import Settings
from app.engine import IngestionEngine
from app.main import create_app

FIX = Path(__file__).parent / "fixtures" / "trader_skill"
TICKERS = (FIX / "company_tickers.json").read_text()
FACTS = (FIX / "companyfacts-CIK0000320193.json").read_text()
SUBMISSIONS = (FIX / "submissions-CIK0000320193.json").read_text()
RSS = (FIX / "yahoo-AAPL.xml").read_bytes()
FRIDAY_MORNING = datetime(2026, 10, 30, 14, 5, 9, tzinfo=timezone.utc)      # 10:05 in New York
MID_SEPTEMBER = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


@contextmanager
def client(
    tmp: str, now: datetime, contact: str | None = "me@example.com",
    rss_status: int = 200, portfolio: dict[str, object] | None = None,
) -> Iterator[TestClient]:
    root = Path(tmp)
    if portfolio is not None:
        (root / "pf.json").write_text(json.dumps(portfolio))
    settings = Settings(
        root / "c.sqlite", 900.0, contact, False, root / "pf.json", root / "x",
        alerts_state_path=root / "alerts.json",
    )
    transport = route({
        "/v8/finance/chart/": lambda _: httpx.Response(200, json=chart_payload()),
        "headline": lambda _: httpx.Response(rss_status, content=RSS),
        "company_tickers": lambda _: httpx.Response(200, content=TICKERS),
        "companyfacts": lambda _: httpx.Response(200, content=FACTS),
        "submissions/CIK": lambda _: httpx.Response(200, content=SUBMISSIONS),
    })
    app = create_app(
        settings, engine_factory=lambda: IngestionEngine(settings, transport=transport),
        clock=lambda: now,
    )
    with TestClient(app) as c:
        yield c


class NewsApiTests(unittest.TestCase):
    def test_headlines_and_8k_merge_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, MID_SEPTEMBER) as c:
            body = c.get("/api/news/aapl?days=30").json()
            kinds = {i["kind"] for i in body["items"]}
            self.assertEqual(kinds, {"headline", "8-K"})
            stamps = [i["published"] for i in body["items"]]
            self.assertEqual(stamps, sorted(stamps, reverse=True))
            amended = next(i for i in body["items"] if i["kind"] == "8-K")
            self.assertEqual((amended["items"], amended["material"]), (["5.02"], True))
            self.assertTrue(amended["url"].startswith("https://www.sec.gov/Archives/"))
            self.assertEqual(body["warnings"], [])

    def test_non_us_listing_gets_headlines_only(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, MID_SEPTEMBER) as c:
            body = c.get("/api/news/SHOP.TO").json()
            self.assertTrue(all(i["kind"] == "headline" for i in body["items"]))
            self.assertTrue(any("non-US" in w for w in body["warnings"]))

    def test_no_contact_email_skips_sec_but_keeps_headlines(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, MID_SEPTEMBER, contact=None) as c:
            body = c.get("/api/news/AAPL?days=30").json()
            self.assertTrue(any(i["kind"] == "headline" for i in body["items"]))
            self.assertTrue(any("contact email" in w for w in body["warnings"]))

    def test_one_failing_source_does_not_sink_the_other(self):
        with tempfile.TemporaryDirectory() as tmp, \
                client(tmp, MID_SEPTEMBER, rss_status=404) as c:
            body = c.get("/api/news/AAPL?days=30").json()
            self.assertTrue(any(i["kind"] == "8-K" for i in body["items"]))
            self.assertTrue(any("headlines unavailable" in w for w in body["warnings"]))

    def test_validation(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, MID_SEPTEMBER) as c:
            self.assertEqual(c.get("/api/news/BAD$").status_code, 422)
            self.assertEqual(c.get("/api/news/AAPL?days=0").status_code, 422)
            self.assertEqual(c.get("/api/news/AAPL?days=31").status_code, 422)


class ClockApiTests(unittest.TestCase):
    def test_open_on_a_friday_morning(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING) as c:
            body = c.get("/api/market/clock").json()
            self.assertTrue(body["open"])
            self.assertEqual(body["next_open"], "2026-11-02T09:30:00-05:00")   # EST after Nov 1

    def test_unknown_year_is_404(self):
        with tempfile.TemporaryDirectory() as tmp, \
                client(tmp, datetime(2031, 1, 2, 15, 0, tzinfo=timezone.utc)) as c:
            self.assertEqual(c.get("/api/market/clock").status_code, 404)


PORTFOLIO = {"accounts": {"TFSA": [{
    "symbol": "AAPL", "shares": 1, "entry_price": 170.0, "entry_date": "2024-04-01",
    "currency": "USD", "never_sell_below_entry": True}]}}


class QuarterlyThesisApiTests(unittest.TestCase):
    def test_aapl_thesis_uses_real_quarters(self):
        with tempfile.TemporaryDirectory() as tmp, \
                client(tmp, FRIDAY_MORNING, portfolio=PORTFOLIO) as c:
            line = c.get("/api/positions").json()["accounts"][0]["lines"][0]
            thesis = line["thesis"]
            self.assertEqual((thesis["status"], thesis["basis"]), ("INTACT", "quarterly"))
            self.assertEqual(thesis["as_of"], "2026-06-27")
            self.assertAlmostEqual(thesis["revenue_growth"][-1], 0.1637, places=3)


if __name__ == "__main__":
    unittest.main()
