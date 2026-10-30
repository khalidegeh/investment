"""Thesis check: is the business still growing? Price is deliberately ignored.

The owner holds through price drops and only reconsiders when a company stops
trending toward growth in its space. This turns that into plain arithmetic on
annual revenue: growth turning flat or negative, growth slowing several
periods running, or growing well below peers. History is only as deep as the
statement source provides, so short histories return UNKNOWN, never a guess.
"""
from __future__ import annotations

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
    shrink_periods: int = 2        # consecutive non-positive readings that mean BROKEN
    decel_periods: int = 2         # consecutive slowdowns that mean WATCH
    peer_gap: float = 0.05         # WATCH when this far below peer growth (0.05 = 5 points)


@dataclass(frozen=True)
class ThesisResult:
    status: str
    reasons: tuple[str, ...]
    revenue_growth: tuple[float, ...] = ()   # YoY readings, oldest to newest
    as_of: str | None = None                 # latest fiscal period used


def fund_result() -> ThesisResult:
    return ThesisResult(NOT_APPLICABLE, ("fund: no single-company thesis to check",))


def assess_thesis(
    income: pd.DataFrame,
    cfg: ThesisConfig | None = None,
    peer_growth: float | None = None,
) -> ThesisResult:
    """Status from a normalised income statement (ascending period index)."""
    cfg = cfg or ThesisConfig()
    if "revenue" not in income.columns or income["revenue"].dropna().empty:
        return ThesisResult(UNKNOWN, ("no revenue history available",))
    # Keep missing years as NaN so a gap breaks the chain: a reading compares two
    # consecutive fiscal years, never a year against two years earlier.
    revenue = income[["revenue"]]
    as_of = str(pd.Timestamp(revenue["revenue"].dropna().index[-1]).date())
    readings = [float(x) for x in yoy_growth(revenue)["revenue"].dropna()]
    if len(readings) < cfg.min_yoy_points:
        return ThesisResult(
            UNKNOWN,
            (f"need {cfg.min_yoy_points} growth readings, have {len(readings)} (short history)",),
            tuple(readings),
            as_of,
        )

    def res(status: str, *reasons: str) -> ThesisResult:
        return ThesisResult(status, reasons, tuple(readings), as_of)

    latest = readings[-1]
    tail = readings[-cfg.shrink_periods:]
    if len(tail) == cfg.shrink_periods and all(g <= 0 for g in tail):
        return res(BROKEN, f"revenue has not grown for {cfg.shrink_periods} straight periods")

    watch: list[str] = []
    if latest <= 0:
        watch.append("revenue growth turned non-positive in the latest period")
    k = cfg.decel_periods
    if len(readings) >= k + 1:
        run = readings[-(k + 1):]
        if all(b < a for a, b in pairwise(run)):
            watch.append(f"revenue growth has slowed {k} periods in a row")
    if peer_growth is not None and latest < peer_growth - cfg.peer_gap:
        watch.append(
            f"growing {latest:.1%} vs peers at {peer_growth:.1%} "
            f"(gap over {cfg.peer_gap:.1%})"
        )
    if watch:
        return res(WATCH, *watch)
    return res(INTACT, f"revenue growing {latest:.1%} year over year")
