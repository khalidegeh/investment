"""IngestionEngine: the single entry point the API and UI layers call.

Wires the cache, retry policy, pacing and sources together. Transport
details live in ``fetcher``; parsing lives in ``sources``.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from types import TracebackType

import httpx
import pandas as pd
from typing_extensions import Self

from .cache import SqliteCache
from .config import Settings
from .errors import DataError, OpenAlphaError
from .fetcher import AsyncFetcher, Sleep
from .retry import RetryPolicy
from .sources.edgar import EdgarClient
from .sources.yahoo import YahooChartClient
from .useragents import BROWSER_AGENTS, UserAgentPool, identifying_agent


class IngestionEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
        retry: RetryPolicy | None = None,
    ):
        self.settings = settings or Settings.from_env()
        s = self.settings
        self.cache = SqliteCache(s.cache_path, s.cache_ttl_seconds, clock)
        if s.rotate_user_agent:
            pool = UserAgentPool(BROWSER_AGENTS, rotate=True, rng=rng)
        else:
            agent = (
                identifying_agent(s.contact_email)
                if s.contact_email
                else "OpenAlpha/0.1 (personal research)"
            )
            pool = UserAgentPool([agent])
        self.fetcher = AsyncFetcher(
            self.cache, pool, retry=retry, transport=transport, sleep=sleep, rng=rng
        )
        self._yahoo = YahooChartClient(self.fetcher, clock)
        self._edgar: EdgarClient | None = None

    async def ohlcv(self, symbol: str, years: int = 30) -> pd.DataFrame:
        """Daily OHLCV and adjusted close, up to ``years`` back."""
        return await self._yahoo.daily_ohlcv(symbol, years)

    async def ohlcv_many(
        self, symbols: list[str], years: int = 30
    ) -> dict[str, pd.DataFrame | OpenAlphaError]:
        """Fetch several symbols; one failure never sinks the batch."""
        results = await asyncio.gather(
            *(self.ohlcv(s, years) for s in symbols), return_exceptions=True
        )
        out: dict[str, pd.DataFrame | OpenAlphaError] = {}
        for sym, res in zip(symbols, results):
            if isinstance(res, (pd.DataFrame, OpenAlphaError)):
                out[sym] = res
            elif isinstance(res, BaseException):
                raise res
        return out

    @property
    def edgar(self) -> EdgarClient:
        if self._edgar is None:
            if not self.settings.contact_email:
                raise DataError("SEC EDGAR needs OPENALPHA_CONTACT (a contact email)")
            self._edgar = EdgarClient(self.fetcher, self.settings.contact_email)
        return self._edgar

    async def company_facts(self, symbol: str) -> dict[str, object]:
        return await self.edgar.company_facts(symbol)

    async def aclose(self) -> None:
        await self.fetcher.aclose()
        self.cache.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
