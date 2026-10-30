"""SEC EDGAR free APIs: ticker to CIK map and XBRL company facts.

SEC requires an identifying User-Agent with a contact address and limits
clients to 10 requests per second. Both are enforced here.
"""
from __future__ import annotations

from ..errors import DataError
from ..fetcher import AsyncFetcher
from ..symbols import normalize_symbol
from ..useragents import identifying_agent

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
DAY = 86_400.0


def parse_ticker_map(payload: object) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise DataError("edgar: ticker map is not an object")
    out: dict[str, int] = {}
    for row in payload.values():
        if isinstance(row, dict) and isinstance(row.get("ticker"), str):
            cik = row.get("cik_str")
            if isinstance(cik, int):
                out[row["ticker"].upper()] = cik
    if not out:
        raise DataError("edgar: ticker map empty")
    return out


class EdgarClient:
    def __init__(self, fetcher: AsyncFetcher, contact: str | None):
        # Raises ValueError if the contact is missing: no anonymous SEC traffic.
        self._ua = identifying_agent(contact)
        self._fetcher = fetcher

    async def cik_for(self, symbol: str) -> int:
        sym = normalize_symbol(symbol)
        res = await self._fetcher.get(TICKERS_URL, user_agent=self._ua, ttl=DAY)
        try:
            mapping = parse_ticker_map(res.json())
        except ValueError as exc:
            raise DataError("edgar: ticker map was not JSON") from exc
        if sym not in mapping:
            raise DataError(f"edgar: {sym} is not an SEC-registered ticker")
        return mapping[sym]

    async def company_facts(self, symbol: str) -> dict[str, object]:
        """Raw XBRL company facts. Normalisation into statements is a later module."""
        cik = await self.cik_for(symbol)
        res = await self._fetcher.get(FACTS_URL.format(cik=cik), user_agent=self._ua, ttl=DAY)
        try:
            payload = res.json()
        except ValueError as exc:
            raise DataError("edgar: company facts were not JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
            raise DataError(f"edgar: no facts for CIK {cik}")
        return payload
