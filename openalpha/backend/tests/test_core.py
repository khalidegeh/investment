"""Retry, cache, symbol validation, user agents and the fetcher."""
import random
import unittest

import httpx
from helpers import FakeTime, Script

from app.cache import SqliteCache, make_key
from app.errors import FetchError, InvalidSymbol, RetriesExhausted
from app.fetcher import AsyncFetcher, HostLimit
from app.retry import RetryPolicy, parse_retry_after
from app.symbols import normalize_symbol
from app.useragents import BROWSER_AGENTS, UserAgentPool, identifying_agent

URL = "https://example.test/data"


_OPEN: list[SqliteCache] = []


def _cache(ft: FakeTime, ttl: float = 900.0) -> SqliteCache:
    c = SqliteCache(":memory:", ttl, ft.time)
    _OPEN.append(c)
    return c


def make_fetcher(script: Script, ft: FakeTime, ttl: float = 900.0, **kw: object) -> AsyncFetcher:
    cache = _cache(ft, ttl)
    return AsyncFetcher(
        cache,
        UserAgentPool(["test-agent"]),
        transport=script.transport(),
        sleep=ft.sleep,
        monotonic=ft.time,
        rng=random.Random(1),
        host_limits={"example.test": HostLimit(0.0, 4)},
        **kw,  # type: ignore[arg-type]
    )


class RetryPolicyTests(unittest.TestCase):
    def test_delay_bounds_and_cap(self):
        p = RetryPolicy()
        rng = random.Random(0)
        for attempt in range(8):
            ceiling = min(30.0, 1.0 * 2.0**attempt)
            for _ in range(50):
                d = p.delay(attempt, rng)
                self.assertGreaterEqual(d, ceiling / 2)
                self.assertLessEqual(d, ceiling)
                self.assertLessEqual(d, 30.0)

    def test_retry_after_raises_floor_but_is_capped(self):
        p = RetryPolicy()
        self.assertGreaterEqual(p.delay(0, random.Random(0), retry_after=10.0), 10.0)
        self.assertEqual(p.delay(0, random.Random(0), retry_after=999.0), 30.0)

    def test_parse_retry_after(self):
        self.assertEqual(parse_retry_after("7"), 7.0)
        self.assertIsNone(parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT"))
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after("-3"))


class SymbolTests(unittest.TestCase):
    def test_accepts_real_tickers(self):
        for raw, want in [("aapl", "AAPL"), ("SHOP.TO", "SHOP.TO"), ("brk-b", "BRK-B"),
                          ("^GSPC", "^GSPC"), ("CAD=X", "CAD=X"), (" nvda ", "NVDA")]:
            self.assertEqual(normalize_symbol(raw), want)

    def test_rejects_unsafe(self):
        for bad in ["", "../etc", "A B", "A/B", "A;DROP", "x" * 20, "A..B", "'--", "A%2F"]:
            with self.assertRaises(InvalidSymbol):
                normalize_symbol(bad)


class UserAgentTests(unittest.TestCase):
    def test_default_is_constant(self):
        pool = UserAgentPool(BROWSER_AGENTS)
        self.assertEqual({pool.next() for _ in range(20)}, {BROWSER_AGENTS[0]})

    def test_rotation_uses_pool(self):
        pool = UserAgentPool(BROWSER_AGENTS, rotate=True, rng=random.Random(3))
        seen = {pool.next() for _ in range(60)}
        self.assertGreater(len(seen), 1)
        self.assertTrue(seen <= set(BROWSER_AGENTS))

    def test_identifying_agent_needs_contact(self):
        self.assertIn("me@example.com", identifying_agent("me@example.com"))
        for bad in (None, "", "no-at-sign"):
            with self.assertRaises(ValueError):
                identifying_agent(bad)


class CacheTests(unittest.TestCase):
    def test_ttl_expiry_and_override(self):
        ft = FakeTime()
        c = _cache(ft)
        c.put("k", 200, b"x")
        ft.now += 899
        self.assertIsNotNone(c.get("k"))
        ft.now += 2
        self.assertIsNone(c.get("k"))
        self.assertIsNotNone(c.get("k", ttl=10_000.0))

    def test_key_depends_on_params_not_order(self):
        a = make_key("GET", URL, {"a": "1", "b": "2"})
        self.assertEqual(a, make_key("GET", URL, {"b": "2", "a": "1"}))
        self.assertNotEqual(a, make_key("GET", URL, {"a": "1", "b": "3"}))

    def test_key_is_inert_against_sql_text(self):
        c = _cache(FakeTime())
        c.put("x'; DROP TABLE http_cache;--", 200, b"ok")
        self.assertEqual(c.get("x'; DROP TABLE http_cache;--").body, b"ok")  # type: ignore[union-attr]


class FetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_503_then_succeeds(self):
        ft = FakeTime()
        s = Script([httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"ok": 1})])
        f = make_fetcher(s, ft)
        r = await f.get(URL)
        self.assertEqual((r.status, r.attempts, len(s.calls)), (200, 3, 3))
        self.assertEqual(len(ft.sleeps), 2)
        self.assertGreaterEqual(ft.sleeps[0], 0.5)
        self.assertGreaterEqual(ft.sleeps[1], 1.0)
        await f.aclose()

    async def test_gives_up_after_five_retries(self):
        ft = FakeTime()
        s = Script([httpx.Response(500)])
        f = make_fetcher(s, ft)
        with self.assertRaises(RetriesExhausted):
            await f.get(URL)
        self.assertEqual(len(s.calls), 6)       # 1 try + 5 retries
        self.assertEqual(len(ft.sleeps), 5)
        self.assertTrue(all(d <= 30.0 for d in ft.sleeps))
        await f.aclose()

    async def test_404_is_not_retried(self):
        ft = FakeTime()
        s = Script([httpx.Response(404)])
        f = make_fetcher(s, ft)
        with self.assertRaises(FetchError) as ctx:
            await f.get(URL)
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(s.calls), 1)
        await f.aclose()

    async def test_429_honors_retry_after(self):
        ft = FakeTime()
        s = Script([
            httpx.Response(429, headers={"Retry-After": "12"}),
            httpx.Response(200, json={}),
        ])
        f = make_fetcher(s, ft)
        await f.get(URL)
        self.assertGreaterEqual(ft.sleeps[0], 12.0)
        await f.aclose()

    async def test_transport_errors_are_retried(self):
        ft = FakeTime()
        s = Script([httpx.ConnectError("boom"), httpx.Response(200, json={})])
        f = make_fetcher(s, ft)
        r = await f.get(URL)
        self.assertEqual(r.attempts, 2)
        await f.aclose()

    async def test_cache_hit_makes_no_network_call(self):
        ft = FakeTime()
        s = Script([httpx.Response(200, json={"v": 1})])
        f = make_fetcher(s, ft)
        first = await f.get(URL, {"s": "AAPL"})
        second = await f.get(URL, {"s": "AAPL"})
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(len(s.calls), 1)
        await f.get(URL, {"s": "NVDA"})             # different params: new request
        self.assertEqual(len(s.calls), 2)
        ft.now += 901                               # past the 15 minute window
        again = await f.get(URL, {"s": "AAPL"})
        self.assertFalse(again.from_cache)
        self.assertEqual(len(s.calls), 3)
        await f.aclose()

    async def test_errors_are_never_cached(self):
        ft = FakeTime()
        s = Script([httpx.Response(404), httpx.Response(200, json={"v": 1})])
        f = make_fetcher(s, ft)
        with self.assertRaises(FetchError):
            await f.get(URL)
        r = await f.get(URL)
        self.assertEqual(r.status, 200)
        await f.aclose()

    async def test_user_agent_header_sent(self):
        ft = FakeTime()
        s = Script([httpx.Response(200, json={})])
        f = make_fetcher(s, ft)
        await f.get(URL)
        self.assertEqual(s.calls[0].headers["User-Agent"], "test-agent")
        await f.get(URL, {"x": "1"}, user_agent="override")
        self.assertEqual(s.calls[1].headers["User-Agent"], "override")
        await f.aclose()

    async def test_host_pacing_spaces_requests(self):
        ft = FakeTime()
        s = Script([httpx.Response(200, json={})])
        cache = _cache(ft)
        f = AsyncFetcher(
            cache, UserAgentPool(["a"]), transport=s.transport(), sleep=ft.sleep,
            monotonic=ft.time, host_limits={"example.test": HostLimit(0.5, 1)},
        )
        for i in range(3):
            await f.get(URL, {"i": str(i)})
        self.assertEqual(len(ft.sleeps), 2)
        self.assertTrue(all(abs(d - 0.5) < 1e-9 for d in ft.sleeps))
        await f.aclose()


def tearDownModule() -> None:
    for c in _OPEN:
        c.close()


if __name__ == "__main__":
    unittest.main()
