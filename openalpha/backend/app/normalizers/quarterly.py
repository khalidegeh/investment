"""Quarterly income figures from SEC XBRL company facts.

Quarters come from 10-Qs (three-month periods only; year-to-date values are
ignored). The fourth quarter is never filed on its own, so it is derived as the
10-K year minus the three quarters inside it and flagged ``derived``. Derived EPS
is approximate because share counts differ across periods. The approach follows
trader-skill by Jake Schincariol (MIT), re-implemented here; see NOTICE.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import pairwise

import pandas as pd

from ..errors import DataError
from .edgar import ANNUAL_FORMS, MAPPING

QUARTER_FIELDS: tuple[str, ...] = (
    "revenue",
    "cost_of_goods_sold",
    "operating_income",
    "net_income",
    "diluted_eps",
)
QUARTER_FORMS = ("10-Q", "10-Q/A")
_Key = tuple[date, date]


@dataclass(frozen=True)
class _Fact:
    val: float
    filed: str


def _facts(
    node: object, unit: str, forms: tuple[str, ...], lo: int, hi: int
) -> dict[_Key, _Fact]:
    """Facts with a duration of lo..hi days, keyed by (start, end); latest filing wins."""
    units = node.get("units") if isinstance(node, dict) else None
    entries = units.get(unit) if isinstance(units, dict) else None
    out: dict[_Key, _Fact] = {}
    if not isinstance(entries, list):
        return out
    for e in entries:
        if not isinstance(e, dict) or e.get("form") not in forms:
            continue
        val, start, end = e.get("val"), e.get("start"), e.get("end")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        if not isinstance(start, str) or not isinstance(end, str):
            continue
        try:
            s, f = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            continue
        if not lo <= (f - s).days <= hi:
            continue
        filed = str(e.get("filed", ""))
        if (s, f) not in out or filed >= out[(s, f)].filed:
            out[(s, f)] = _Fact(float(val), filed)
    return out


def _by_period(
    gaap: dict[str, object], field: str, forms: tuple[str, ...], lo: int, hi: int
) -> dict[_Key, float]:
    _, _, unit, tags = MAPPING[field]
    merged: dict[_Key, _Fact] = {}
    for tag in tags:
        for key, fact in _facts(gaap.get(tag), unit, forms, lo, hi).items():
            merged.setdefault(key, fact)
    return {k: v.val for k, v in merged.items()}


def normalize_quarterly(gaap: dict[str, object]) -> pd.DataFrame:
    """Quarterly frame indexed by fiscal quarter end, plus a boolean ``derived`` column."""
    rows: dict[date, dict[str, float]] = {}
    derived_ends: set[date] = set()
    for field in QUARTER_FIELDS:
        quarters = _by_period(gaap, field, QUARTER_FORMS, 75, 105)
        years = _by_period(gaap, field, ANNUAL_FORMS, 340, 380)
        for (_, end), val in quarters.items():
            rows.setdefault(end, {})[field] = val
        for (y_start, y_end), y_val in years.items():
            inside = sorted(k for k in quarters if k[0] >= y_start and k[1] < y_end)
            contiguous = len(inside) == 3 and all(
                0 <= (b[0] - a[1]).days <= 3 for a, b in pairwise(inside)
            )
            if not contiguous or (y_end in rows and field in rows[y_end]):
                continue
            q4 = y_val - sum(quarters[k] for k in inside)
            rows.setdefault(y_end, {})[field] = q4
            derived_ends.add(y_end)
    if not rows:
        raise DataError("edgar: no quarterly data")
    df = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=list(QUARTER_FIELDS))
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index), name="period_end")
    out: pd.DataFrame = df.sort_index().astype("float64")
    out["derived"] = [d.date() in derived_ends for d in pd.DatetimeIndex(out.index)]
    return out


__all__ = ["QUARTER_FIELDS", "normalize_quarterly"]
