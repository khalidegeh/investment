"""Yahoo parsing, EDGAR lookups and the engine facade, all against mocked transports."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from app import DataError, IngestionEngine, Settings
from app.errors import FetchError, InvalidSymbol
from app.sources.edgar import parse_ticker_map
from app.sources.yahoo import DAY, parse_chart
from helpers import FakeTime, route

# 2024-01-02 and 2024-01-03 at 14:30 UTC (09:30 New York, gmtoffset -18000), a null holiday row,
# and a duplicate-free, out-of-order input to prove sorting.
T0 = 1_704_205_800   # 2024-01-02 14:30:00 UTC
T1 = T0 + DAY
T2 = T1 + DAY


def chart_payload(adj: bool = True, error: object = None) -> dict[str, object]:
    ind: dict[str, object] = {
        "quote": [{
            "open": [10.0, None, 11.0],
            "high": [12.0, None, 13.0],
            "low": [9.0, None, 10.0],
            "close": [11.0, None, 12.0],
            "volume": [1000, None, 2000],
        }]
    }
    if adj:
        ind["adjclose"] = [{"adjclose": [10.5, None, 11.5]}]
    return {"chart": {"result": [{"meta": {"gmtoffset": -18000},
                                  "timestamp": [T2, T1, T0], "indicators": ind}],
                      "error": error}}


class YahooParseTests(unittest.TestCase):
    def test_parses_sorts_and_drops_null_rows(self):
        df = parse_chart(chart_payload())
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "adj_close", "volume"])
        self.assertEqual(len(df), 2)                       # null holiday row dropped
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertEqual(str(df.index[0].date()), "2024-01-02")
        self.assertEqual(df["adj_close"].iloc[0], 11.5)    # T0 is last in the input arrays
        self.assertEqual(df["close"].iloc[0], 12.0)

    def test_missing_adjclose_is_nan_not_guessed(self):
        df = parse_chart(chart_payload(adj=False))
        self.assertTrue(df["adj_close"].isna().all())

    def test_error_payload(self):
        with self.assertRaises(DataError):
            parse_chart({"chart": {"result": None, "error": {"code": "Not Found"}}})

    def test_malformed_payloads(self):
        for bad in (None, [], {}, {"chart": {"result": []}}, {"chart": {"result": [{}]}}):
            with self.assertRaises(DataError):
                parse_chart(bad)

    def test_length_mismatch(self):
        p = chart_payload()
        p["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [1.0]  # type: ignore[index]
        with self.assertRaises(DataError):
            parse_chart(p)


class EdgarParseTests(unittest.TestCase):
    def test_ticker_map(self):
        m = parse_ticker_map({"0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple"},
                              "1": {"cik_str": "bad", "ticker": "X"}})
        self.assertEqual(m, {"AAPL": 320193})
        with self.assertRaises(DataError):
            parse_ticker_map({})


def settings(tmp: str, contact: str | None = None, rotate: bool = False) -> Settings:
    return Settings(Path(tmp) / "c.sqlite", 900.0, contact, rotate)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_ohlcv_end_to_end_with_cache_and_stable_key(self):
        ft = FakeTime(1_704_300_000.0)
        seen: list[httpx.Request] = []

        def chart(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(200, json=chart_payload())

        with TemporaryDirectory() as tmp:
            eng = IngestionEngine(settings(tmp), transport=route({"/v8/finance/chart/": chart}),
                                  sleep=ft.sleep, clock=ft.time)
            a = await eng.ohlcv("aapl", years=30)
            ft.now += 600                                   # still inside 15 minutes
            b = await eng.ohlcv("AAPL", years=30)
            self.assertEqual(len(a), 2)
            self.assertTrue(a.equals(b))
            self.assertEqual(len(seen), 1)                  # second call served from SQLite
            q = seen[0].url.params
            span = (int(q["period2"]) - int(q["period1"])) / (365.25 * DAY)
            self.assertAlmostEqual(span, 30.0, places=2)
            self.assertEqual(int(q["period2"]) % DAY, 0)
            await eng.aclose()

    async def test_rejects_bad_symbol_before_network(self):
        with TemporaryDirectory() as tmp:
            eng = IngestionEngine(settings(tmp), transport=route({}))
            with self.assertRaises(InvalidSymbol):
                await eng.ohlcv("../../etc/passwd")
            with self.assertRaises(ValueError):
                await eng.ohlcv("AAPL", years=0)
            await eng.aclose()

    async def test_ohlcv_many_isolates_failures(self):
        def chart(req: httpx.Request) -> httpx.Response:
            if "BAD" in str(req.url):
                return httpx.Response(404)
            return httpx.Response(200, json=chart_payload())

        with TemporaryDirectory() as tmp:
            eng = IngestionEngine(settings(tmp), transport=route({"/v8/finance/chart/": chart}))
            out = await eng.ohlcv_many(["AAPL", "BAD", "../x"])
            self.assertEqual(len(out["AAPL"]), 2)           # type: ignore[arg-type]
            self.assertIsInstance(out["BAD"], FetchError)
            self.assertIsInstance(out["../x"], InvalidSymbol)
            await eng.aclose()

    async def test_edgar_requires_contact(self):
        with TemporaryDirectory() as tmp:
            eng = IngestionEngine(settings(tmp), transport=route({}))
            with self.assertRaises(DataError):
                await eng.company_facts("AAPL")
            await eng.aclose()

    async def test_edgar_facts_and_identifying_agent(self):
        agents: list[str] = []

        def tickers(req: httpx.Request) -> httpx.Response:
            agents.append(req.headers["User-Agent"])
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL",
                                                   "title": "Apple"}})

        def facts(req: httpx.Request) -> httpx.Response:
            agents.append(req.headers["User-Agent"])
            assert "CIK0000320193" in str(req.url)
            return httpx.Response(200, json={"cik": 320193, "facts": {"us-gaap": {}}})

        with TemporaryDirectory() as tmp:
            # rotation is on, yet EDGAR must still send the identifying agent.
            eng = IngestionEngine(settings(tmp, "me@example.com", rotate=True),
                                  transport=route({"company_tickers": tickers,
                                                   "companyfacts": facts}))
            data = await eng.company_facts("aapl")
            self.assertIn("us-gaap", data["facts"])          # type: ignore[operator]
            self.assertTrue(all("me@example.com" in a for a in agents))
            with self.assertRaises(DataError):
                await eng.company_facts("ZZZZ")              # not in the SEC map
            await eng.aclose()


if __name__ == "__main__":
    unittest.main()
