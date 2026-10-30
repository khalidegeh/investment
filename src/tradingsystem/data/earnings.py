"""Earnings calendar provider.

v1 reads per-symbol CSVs: data/earnings/<SYMBOL>.csv with a single `date`
column (earnings announcement dates, YYYY-MM-DD). One row per announcement.

Sourcing these is a Phase-1 data task (manual export or OpenBB ODP later).
The calendar is deterministic: given the same CSVs, every run agrees.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

import pandas as pd


class EarningsCalendar(ABC):
    @abstractmethod
    def announcement_dates(self, symbol: str) -> list[date]:
        """All known earnings announcement dates for symbol, ascending."""
        ...

    def next_earnings(self, symbol: str, asof: date) -> date | None:
        for d in self.announcement_dates(symbol):
            if d > asof:
                return d
        return None

    def days_to_earnings(self, symbol: str, asof: date) -> int | None:
        nxt = self.next_earnings(symbol, asof)
        return (nxt - asof).days if nxt else None


class CsvEarningsCalendar(EarningsCalendar):
    def __init__(self, earnings_dir: str | Path):
        self.earnings_dir = Path(earnings_dir)
        self._cache: dict[str, list[date]] = {}

    def announcement_dates(self, symbol: str) -> list[date]:
        if symbol not in self._cache:
            path = self.earnings_dir / f"{symbol}.csv"
            if not path.exists():
                self._cache[symbol] = []
            else:
                df = pd.read_csv(path)
                if "date" not in df.columns:
                    raise ValueError(f"{path}: missing 'date' column")
                dates = sorted(pd.to_datetime(df["date"]).dt.date.unique())
                self._cache[symbol] = list(dates)
        return self._cache[symbol]
