"""EDGAR-first statement lookup with yfinance failover."""
from __future__ import annotations

from dataclasses import replace

from ..errors import DataError, FetchError
from ..models import StatementSet
from ..symbols import normalize_symbol
from .edgar import EdgarNormalizer
from .yfinance import YFinanceNormalizer


class StatementService:
    def __init__(self, edgar: EdgarNormalizer | None, fallback: YFinanceNormalizer):
        self._edgar = edgar
        self._fallback = fallback

    async def get(self, symbol: str) -> StatementSet:
        sym = normalize_symbol(symbol)
        reason = "no SEC contact email configured (OPENALPHA_CONTACT)"
        # A dot in the ticker means a non-US listing (SHOP.TO): EDGAR cannot serve it.
        if "." in sym:
            reason = f"{sym} is a non-US listing"
        elif self._edgar is not None:
            try:
                return await self._edgar.fetch(sym)
            except (DataError, FetchError) as exc:
                reason = f"edgar unavailable: {exc}"
        result = await self._fallback.fetch(sym)
        return replace(result, notes=(*result.notes, f"fell back from edgar: {reason}"))
