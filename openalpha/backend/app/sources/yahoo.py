"""Yahoo chart endpoint (unofficial, free): daily OHLCV with adjusted close."""
from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import quote

import pandas as pd

from ..errors import DataError
from ..fetcher import AsyncFetcher
from ..symbols import normalize_symbol

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DAY = 86_400
COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]


def _obj(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DataError(f"yahoo: {what} missing or not an object")
    return value


def _arr(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise DataError(f"yahoo: {what} missing or not a list")
    return value


def parse_chart(payload: object) -> pd.DataFrame:
    """Turn a chart payload into a DataFrame indexed by exchange-local date.

    Rows with no close (holidays) are dropped. If the payload carries no
    adjusted close the column is left NaN rather than guessed.
    """
    chart = _obj(_obj(payload, "payload").get("chart"), "chart")
    if chart.get("error"):
        raise DataError(f"yahoo: {chart['error']}")
    results = _arr(chart.get("result"), "chart.result")
    if not results:
        raise DataError("yahoo: empty result")
    result = _obj(results[0], "result[0]")
    meta = _obj(result.get("meta", {}), "meta")
    offset = meta.get("gmtoffset", 0)
    if not isinstance(offset, int):
        raise DataError("yahoo: gmtoffset is not an integer")

    stamps = _arr(result.get("timestamp"), "timestamp")
    indicators = _obj(result.get("indicators"), "indicators")
    quotes = _arr(indicators.get("quote"), "indicators.quote")
    if not quotes:
        raise DataError("yahoo: no quote block")
    q = _obj(quotes[0], "quote[0]")

    data: dict[str, list[object]] = {}
    for name in ("open", "high", "low", "close", "volume"):
        series = _arr(q.get(name), name)
        if len(series) != len(stamps):
            raise DataError(f"yahoo: {name} length does not match timestamps")
        data[name] = series
    adj_blocks = indicators.get("adjclose")
    adj: list[object] = [None] * len(stamps)
    if isinstance(adj_blocks, list) and adj_blocks:
        candidate = _obj(adj_blocks[0], "adjclose[0]").get("adjclose")
        if isinstance(candidate, list):
            if len(candidate) != len(stamps):
                raise DataError("yahoo: adjclose length does not match timestamps")
            adj = candidate
    data["adj_close"] = adj

    index = pd.to_datetime(pd.Series(stamps, dtype="int64") + offset, unit="s").dt.normalize()
    df = pd.DataFrame(data, index=pd.DatetimeIndex(index, name="date"))
    numeric: pd.DataFrame = df[COLUMNS].apply(pd.to_numeric, errors="coerce")
    clean: pd.DataFrame = numeric.dropna(subset=["close"])
    clean = clean[~clean.index.duplicated(keep="last")].sort_index()
    if clean.empty:
        raise DataError("yahoo: no usable rows")
    return clean


class YahooChartClient:
    def __init__(self, fetcher: AsyncFetcher, clock: Callable[[], float] = time.time):
        self._fetcher = fetcher
        self._clock = clock

    async def daily_ohlcv(self, symbol: str, years: int = 30) -> pd.DataFrame:
        """Up to ``years`` of daily OHLCV plus adjusted close.

        ``period2`` is rounded to the next UTC midnight so the request key is
        stable all day and the 15 minute cache can actually hit.
        """
        if not 1 <= years <= 60:
            raise ValueError("years must be between 1 and 60")
        sym = normalize_symbol(symbol)
        now = int(self._clock())
        end = (now // DAY + 1) * DAY
        start = end - int(years * 365.25 * DAY)
        res = await self._fetcher.get(
            CHART_URL.format(symbol=quote(sym, safe="")),
            {
                "period1": str(start),
                "period2": str(end),
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            },
        )
        try:
            return parse_chart(res.json())
        except ValueError as exc:  # json.JSONDecodeError
            raise DataError(f"yahoo: response was not JSON for {sym}") from exc
