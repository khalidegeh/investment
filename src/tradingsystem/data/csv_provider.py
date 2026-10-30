"""CSV data provider (v1). One file per symbol: data/<SYMBOL>.csv"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .base import DataProvider


class CsvProvider(DataProvider):
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def get_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        path = self.data_dir / f"{symbol}.csv"
        if not path.exists():
            raise FileNotFoundError(f"No data file: {path}")
        df = pd.read_csv(path)
        df = self.validate(df, symbol)
        mask = (df["timestamp"] >= pd.Timestamp(start, tz="UTC")) & (
            df["timestamp"] <= pd.Timestamp(end, tz="UTC")
        )
        return df.loc[mask].reset_index(drop=True)
