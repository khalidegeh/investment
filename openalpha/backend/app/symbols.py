"""Ticker validation. Runs before a symbol touches a URL, file name or SQL."""
from __future__ import annotations

import re

from .errors import InvalidSymbol

# Letters, digits, and the punctuation real tickers use: SHOP.TO, BRK-B, ^GSPC, CAD=X.
_SYMBOL = re.compile(r"[A-Z0-9^][A-Z0-9.\-=^]{0,14}")


def normalize_symbol(raw: str) -> str:
    """Upper-case and validate. Raises InvalidSymbol on anything unexpected."""
    s = raw.strip().upper()
    if not _SYMBOL.fullmatch(s) or ".." in s:
        raise InvalidSymbol(f"invalid ticker: {raw!r}")
    return s
