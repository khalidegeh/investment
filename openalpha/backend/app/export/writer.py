"""Chunked exports with UTC timestamped file names.

Files are written to a temp name and renamed, so a reader never sees a partial
file. Text cells that a spreadsheet could execute as a formula are neutralised.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..symbols import normalize_symbol

EXPORT_KINDS: tuple[str, ...] = ("ohlcv", "income", "balance", "cashflow", "positions")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ExportResult:
    path: Path
    rows: int
    bytes: int


def sanitize_cell(value: object) -> object:
    """Prefix a quote to text that starts like a spreadsheet formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    out: pd.DataFrame = df.copy()
    for col in out.columns:
        if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(sanitize_cell)
    return out


def export_filename(symbol: str, kind: str, ext: str, now: datetime) -> str:
    if kind not in EXPORT_KINDS:
        raise ValueError(f"unknown export kind {kind!r}")
    if ext not in ("csv", "parquet"):
        raise ValueError(f"unknown extension {ext!r}")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    safe = re.sub(r"[^A-Z0-9.\-]", "_", normalize_symbol(symbol))
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe}_{kind}_{stamp}.{ext}"


def stream_csv(df: pd.DataFrame, chunk_rows: int = 5000) -> Iterator[bytes]:
    """Yield the header, then the body in ``chunk_rows`` slices."""
    if chunk_rows < 1:
        raise ValueError("chunk_rows must be >= 1")
    clean = _clean(df)
    yield clean.head(0).to_csv(index=True).encode()
    for start in range(0, len(clean), chunk_rows):
        yield clean.iloc[start : start + chunk_rows].to_csv(header=False, index=True).encode()


def _tmp(path: Path) -> Path:
    return path.with_name(path.name + ".tmp")


def write_csv_file(
    df: pd.DataFrame, directory: Path, symbol: str, kind: str, now: datetime,
    chunk_rows: int = 5000,
) -> ExportResult:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / export_filename(symbol, kind, "csv", now)
    tmp = _tmp(path)
    size = 0
    with open(tmp, "wb") as f:
        for chunk in stream_csv(df, chunk_rows):
            size += f.write(chunk)
    os.replace(tmp, path)
    return ExportResult(path, len(df), size)


def write_parquet_file(
    df: pd.DataFrame, directory: Path, symbol: str, kind: str, now: datetime,
    row_group_rows: int = 50_000,
) -> ExportResult:
    if row_group_rows < 1:
        raise ValueError("row_group_rows must be >= 1")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / export_filename(symbol, kind, "parquet", now)
    tmp = _tmp(path)
    flat = _clean(df).reset_index()
    schema = pa.Table.from_pandas(flat.head(0), preserve_index=False).schema
    with pq.ParquetWriter(str(tmp), schema) as writer:
        for start in range(0, len(flat), row_group_rows):
            part = flat.iloc[start : start + row_group_rows]
            writer.write_table(pa.Table.from_pandas(part, schema=schema, preserve_index=False))
    os.replace(tmp, path)
    return ExportResult(path, len(df), path.stat().st_size)
