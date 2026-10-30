"""Per-method fair value ranges.

Pure functions: numbers in, ``MethodRange`` out. Every assumption is an
explicit argument and is echoed back in ``MethodRange.assumptions`` so the
report can show it. A method that cannot be computed raises ``ValueError``
rather than guessing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median


@dataclass(frozen=True)
class MethodRange:
    method: str
    low: float
    base: float
    high: float
    assumptions: dict[str, float | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.low, self.base, self.high)):
            raise ValueError(f"{self.method}: non-finite value")
        if not self.low <= self.base <= self.high:
            raise ValueError(f"{self.method}: need low <= base <= high")


@dataclass(frozen=True)
class DcfScenario:
    """One DCF scenario. Rates are decimals (0.08 = 8%)."""

    growth: float            # annual FCF growth during the explicit period
    discount_rate: float
    terminal_growth: float


def dcf_value_per_share(
    fcf: float,
    scenario: DcfScenario,
    years: int,
    net_debt: float,
    shares: float,
) -> float:
    """Two-stage DCF: ``years`` of growth, then a Gordon terminal value."""
    if shares <= 0:
        raise ValueError("shares must be positive")
    if years < 1:
        raise ValueError("years must be >= 1")
    if fcf <= 0:
        raise ValueError("DCF needs positive free cash flow")
    if scenario.discount_rate <= scenario.terminal_growth:
        raise ValueError("discount_rate must exceed terminal_growth")
    if scenario.discount_rate <= 0:
        raise ValueError("discount_rate must be positive")

    pv = 0.0
    cash = fcf
    for t in range(1, years + 1):
        cash *= 1.0 + scenario.growth
        pv += cash / (1.0 + scenario.discount_rate) ** t
    terminal = cash * (1.0 + scenario.terminal_growth) / (
        scenario.discount_rate - scenario.terminal_growth
    )
    pv += terminal / (1.0 + scenario.discount_rate) ** years
    return (pv - net_debt) / shares


def dcf_range(
    fcf: float,
    bear: DcfScenario,
    base: DcfScenario,
    bull: DcfScenario,
    years: int,
    net_debt: float,
    shares: float,
) -> MethodRange:
    """DCF range from three explicit scenarios (bear / base / bull)."""
    low, mid, high = (
        dcf_value_per_share(fcf, s, years, net_debt, shares) for s in (bear, base, bull)
    )
    if not low <= mid <= high:
        raise ValueError("scenarios must be ordered bear <= base <= bull in value")
    return MethodRange(
        "dcf",
        low,
        mid,
        high,
        {
            "fcf": fcf,
            "years": years,
            "net_debt": net_debt,
            "shares": shares,
            "bear": _fmt(bear),
            "base": _fmt(base),
            "bull": _fmt(bull),
        },
    )


def _fmt(s: DcfScenario) -> str:
    return f"growth={s.growth:.4f} discount={s.discount_rate:.4f} terminal={s.terminal_growth:.4f}"


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile, pct in [0, 100]."""
    if not values:
        raise ValueError("no values")
    if not 0.0 <= pct <= 100.0:
        raise ValueError("pct must be within [0, 100]")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = pct / 100.0 * (len(xs) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def pe_history_range(
    eps: float,
    historical_pes: list[float],
    low_pct: float = 25.0,
    high_pct: float = 75.0,
    min_observations: int = 12,
) -> MethodRange:
    """Fair price = EPS x the company's own historical P/E distribution.

    Non-positive P/Es (loss-making periods) are dropped. Fewer than
    ``min_observations`` usable points raises: no thin-sample ranges.
    """
    if eps <= 0:
        raise ValueError("P/E method needs positive EPS")
    pes = [p for p in historical_pes if math.isfinite(p) and p > 0]
    if len(pes) < min_observations:
        raise ValueError(
            f"need >= {min_observations} positive historical P/E points, got {len(pes)}"
        )
    return MethodRange(
        "pe_history",
        eps * percentile(pes, low_pct),
        eps * median(pes),
        eps * percentile(pes, high_pct),
        {
            "eps": eps,
            "observations": len(pes),
            "low_percentile": low_pct,
            "high_percentile": high_pct,
        },
    )


def consensus_range(
    target_low: float,
    target_mean: float,
    target_high: float,
    analyst_count: int,
    min_analysts: int = 5,
) -> MethodRange:
    """Analyst consensus price target range. Needs ``min_analysts`` opinions."""
    if analyst_count < min_analysts:
        raise ValueError(f"need >= {min_analysts} analysts, got {analyst_count}")
    if target_low <= 0:
        raise ValueError("targets must be positive")
    return MethodRange(
        "consensus",
        target_low,
        target_mean,
        target_high,
        {"analyst_count": analyst_count},
    )
