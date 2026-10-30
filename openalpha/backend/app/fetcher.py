"""Async HTTP fetcher: cache first, per-host pacing, retry with backoff."""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .cache import SqliteCache, make_key
from .errors import FetchError, RetriesExhausted
from .retry import RetryPolicy, parse_retry_after
from .useragents import UserAgentPool

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class HostLimit:
    min_interval: float = 0.0     # seconds between request starts to this host
    max_concurrency: int = 4


# Conservative defaults. SEC allows 10 requests per second; stay well under.
DEFAULT_HOST_LIMITS: dict[str, HostLimit] = {
    "query1.finance.yahoo.com": HostLimit(0.25, 4),
    "query2.finance.yahoo.com": HostLimit(0.25, 4),
    "www.sec.gov": HostLimit(0.15, 4),
    "data.sec.gov": HostLimit(0.15, 4),
    "feeds.finance.yahoo.com": HostLimit(0.5, 2),
}


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    body: bytes
    from_cache: bool
    attempts: int

    def json(self) -> object:
        return json.loads(self.body)


class _HostGate:
    """Spaces request starts and caps concurrency for one host."""

    def __init__(self, limit: HostLimit, monotonic: Callable[[], float], sleep: Sleep):
        self.sem = asyncio.Semaphore(limit.max_concurrency)
        self._interval = limit.min_interval
        self._lock = asyncio.Lock()
        self._next = 0.0
        self._mono = monotonic
        self._sleep = sleep

    async def wait_turn(self) -> None:
        async with self._lock:
            now = self._mono()
            slot = max(now, self._next)
            self._next = slot + self._interval
        if slot > now:
            await self._sleep(slot - now)


class AsyncFetcher:
    def __init__(
        self,
        cache: SqliteCache,
        ua_pool: UserAgentPool,
        retry: RetryPolicy | None = None,
        host_limits: Mapping[str, HostLimit] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        timeout: float = 15.0,
    ):
        self._cache = cache
        self._ua = ua_pool
        self._retry = retry or RetryPolicy()
        self._limits = dict(DEFAULT_HOST_LIMITS if host_limits is None else host_limits)
        self._gates: dict[str, _HostGate] = {}
        self._sleep = sleep
        self._mono = monotonic
        self._rng = rng or random.Random()
        self._client = httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(timeout), follow_redirects=True
        )

    def _gate(self, host: str) -> _HostGate:
        if host not in self._gates:
            self._gates[host] = _HostGate(
                self._limits.get(host, HostLimit()), self._mono, self._sleep
            )
        return self._gates[host]

    async def get(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        user_agent: str | None = None,
        ttl: float | None = None,
        use_cache: bool = True,
    ) -> FetchResult:
        """GET with cache, pacing and retry. ``ttl`` overrides the cache default."""
        query = dict(params or {})
        key = make_key("GET", url, query)
        if use_cache:
            hit = await asyncio.to_thread(self._cache.get, key, ttl)
            if hit is not None:
                return FetchResult(url, hit.status, hit.body, True, 0)

        gate = self._gate(urlsplit(url).hostname or "")
        headers = {"User-Agent": user_agent or self._ua.next(), "Accept": "application/json"}
        last_error = "no attempt made"
        attempts = 0
        for attempt in range(self._retry.max_retries + 1):
            attempts += 1
            retry_after: float | None = None
            await gate.wait_turn()
            try:
                async with gate.sem:
                    resp = await self._client.get(url, params=query, headers=headers)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                code = resp.status_code
                if code < 400:
                    body = resp.content
                    if use_cache and 200 <= code < 300:
                        await asyncio.to_thread(self._cache.put, key, code, body)
                    return FetchResult(url, code, body, False, attempts)
                if code not in self._retry.retry_statuses:
                    raise FetchError(f"HTTP {code} from {url}", code, url)
                last_error = f"HTTP {code}"
                retry_after = parse_retry_after(resp.headers.get("Retry-After"))
            if attempt == self._retry.max_retries:
                break
            await self._sleep(self._retry.delay(attempt, self._rng, retry_after))
        raise RetriesExhausted(
            f"gave up on {url} after {attempts} attempts: {last_error}", None, url
        )

    async def aclose(self) -> None:
        await self._client.aclose()
