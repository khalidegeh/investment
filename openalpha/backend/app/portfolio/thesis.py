"""Thesis check: is the business still growing? Price is deliberately ignored.

The owner holds through price drops and only reconsiders when a company stops
trending toward growth. Two annual series are judged separately, revenue and
diluted EPS, because either one alone misleads (Apple's revenue fell in fiscal
2023 while earnings held and the stock later re-rated). BROKEN needs both to
fail; one failing is WATCH. A missing fiscal year breaks the chain, and short
or stale history is UNKNOWN, never a guess.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

import pandas as pd

from ..normalizers.base import yoy_growth

INTACT = "INTACT"
WATCH = "WATCH"
BROKEN = "BROKEN"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "N/A"


@dataclass(frozen=True)
class ThesisConfig:
    """Defaults are placeholders for the owner to set."""

    min_yoy_points: int = 2        # growth readings needed to say anything
    shrink_periods: int = 2        # consecutive non-positive readings that mean a metric is BROKEN
    decel_periods: int = 2         # consecutive slowdowns that mean a metric is WATCH
    peer_gap: float = 0.05         # revenue WATCH when this far below peer growth


@dataclass(frozen=True)
class ThesisResult:
    status: str
    reasons: tuple[str, ...]
    revenue_growth: tuple[float, ...] = ()   # YoY readings, oldest to newest
    eps_growth: tuple[float, ...] = ()
    as_of: str | None = None                 # latest fiscal period used


@dataclass(frozen=True)
class _Metric:
    status: str
    reasons: tuple[str, ...]
    readings: tuple[float, ...] = ()


def fund_result() -> ThesisResult:
    return ThesisResult(NOT_APPLICABLE, ("fund: no single-company thesis to check",))


def _assess_metric(
    df: pd.DataFrame, column: str, label: str, cfg: ThesisConfig, peer_growth: float | None
) -> _Metric:
    if column not in df.columns or df[column].dropna().empty:
        return _Metric(UNKNOWN, (f"{label}: no history available",))
    growth = yoy_growth(df[[column]])[column]
    readings = [float(x) for x in growth.dropna()]
    if len(readings) < cfg.min_yoy_points:
        return _Metric(
            UNKNOWN,
            (f"{label}: need {cfg.min_yoy_points} growth readings, have {len(readings)}",),
            tuple(readings),
        )
    if math.isnan(float(growth.iloc[-1])):
        return _Metric(
            UNKNOWN,
            (f"{label}: latest fiscal period has no comparable prior year",),
            tuple(readings),
        )

    def res(status: str, *reasons: str) -> _Metric:
        return _Metric(status, reasons, tuple(readings))

    latest = readings[-1]
    tail = readings[-cfg.shrink_periods:]
    if len(tail) == cfg.shrink_periods and all(g <= 0 for g in tail):
        return res(BROKEN, f"{label} has not grown for {cfg.shrink_periods} straight periods")
    watch: list[str] = []
    if latest <= 0:
        watch.append(f"{label} growth turned non-positive in the latest period")
    k = cfg.decel_periods
    if len(readings) >= k + 1 and all(b < a for a, b in pairwise(readings[-(k + 1):])):
        watch.append(f"{label} growth has slowed {k} periods in a row")
    if peer_growth is not None and latest < peer_growth - cfg.peer_gap:
        watch.append(
            f"{label} growing {latest:.1%} vs peers at {peer_growth:.1%} "
            f"(gap over {cfg.peer_gap:.1%})"
        )
    if watch:
        return res(WATCH, *watch)
    return res(INTACT, f"{label} growing {latest:.1%} year over year")


def assess_thesis(
    income: pd.DataFrame,
    cfg: ThesisConfig | None = None,
    peer_growth: float | None = None,
) -> ThesisResult:
    """Status from a normalised income statement (ascending period index)."""
    cfg = cfg or ThesisConfig()
    rev = _assess_metric(income, "revenue", "revenue", cfg, peer_growth)
    eps = _assess_metric(income, "diluted_eps", "EPS", cfg, None)
    used = [c for c in ("revenue", "diluted_eps") if c in income.columns]
    seen = income[used].dropna(how="all") if used else income.iloc[0:0]
    as_of = None if seen.empty else str(pd.Timestamp(seen.index[-1]).date())
    reasons = (*rev.reasons, *eps.reasons)

    def res(status: str, *extra: str) -> ThesisResult:
        return ThesisResult(status, (*reasons, *extra), rev.readings, eps.readings, as_of)

    measured = [m for m in (rev, eps) if m.status != UNKNOWN]
    if not measured:
        return res(UNKNOWN)
    if len(measured) == 2 and all(m.status == BROKEN for m in measured):
        return res(BROKEN)
    if any(m.status in (WATCH, BROKEN) for m in measured):
        if len(measured) == 1:
            return res(WATCH, "only one of revenue and EPS could be measured; BROKEN needs both")
        if any(m.status == BROKEN for m in measured):
            return res(WATCH, "only one of revenue and EPS has failed; BROKEN needs both")
        return res(WATCH)
    if len(measured) == 1:
        return res(INTACT, "based on one of revenue and EPS only; the other is unavailable")
    return res(INTACT)
