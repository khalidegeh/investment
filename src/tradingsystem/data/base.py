"""Market-data provider contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class DataProvider(ABC):
    """Returns clean, point-in-time bars. Implementations must guarantee:
    UTC timestamps, ascending sort, no duplicates, no lookahead."""

    @abstractmethod
    def get_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        ...

    @staticmethod
    def validate(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{symbol}: missing columns {missing}")
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        if df["timestamp"].duplicated().any():
            raise ValueError(f"{symbol}: duplicate timestamps in data")
        if df[["open", "high", "low", "close"]].isna().any().any():
            raise ValueError(f"{symbol}: NaN in OHLC data")
        return df
