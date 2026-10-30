"""Deterministic fundamentals scorecard.

Pure functions: metric values in, score and verdict out. No network access,
no guessing. A missing metric is skipped and the remaining weights are
renormalized; if fewer than ``min_coverage`` metrics are present the verdict
is ``insufficient-data`` -- never a guess.

Thresholds below are starting points calibrated for large-cap growth names.
Override them from config.yaml under ``fundamentals.scorecard`` (see
config.example.yaml). The code defaults never change silently.
"""
from __future__ import annotations

from dataclasses import dataclass

BUY = "buy"
HOLD = "hold"
SELL = "sell"
INSUFFICIENT = "insufficient-data"
EXCLUDED = "excluded"


@dataclass(frozen=True)
class MetricSpec:
    name: str
    weight: float
    higher_is_better: bool
    # ((limit, points), ...) evaluated in order; first match wins.
    bands: tuple[tuple[float, int], ...]
    fallback_points: int = -1  # value outside every band (e.g. negative P/E)


DEFAULT_METRICS: tuple[MetricSpec, ...] = (
    # --- valuation (30) ---
    MetricSpec("pe_ttm", 12.0, False, ((20.0, 2), (30.0, 1), (40.0, 0))),
    MetricSpec("peg_ttm", 18.0, False, ((1.0, 2), (1.5, 1), (2.0, 0))),
    # --- quality (30) ---
    MetricSpec("roe_ttm", 15.0, True, ((0.20, 2), (0.12, 1), (0.05, 0))),
    MetricSpec("fcf_margin_ttm", 15.0, True, ((0.15, 2), (0.08, 1), (0.0, 0))),
    # --- growth (25) ---
    MetricSpec("revenue_growth_yoy", 12.5, True, ((0.15, 2), (0.05, 1), (0.0, 0))),
    MetricSpec("eps_growth_yoy", 12.5, True, ((0.15, 2), (0.05, 1), (0.0, 0))),
    # --- balance sheet (15) ---
    MetricSpec("debt_to_equity", 8.0, False, ((0.5, 2), (1.0, 1), (2.0, 0))),
    MetricSpec("current_ratio", 7.0, True, ((1.5, 2), (1.0, 1), (0.7, 0))),
)

DEFAULT_BUY_AT = 65.0
DEFAULT_HOLD_AT = 40.0
DEFAULT_MIN_COVERAGE = 5


def score_metric(spec: MetricSpec, value: float | None) -> int | None:
    """Points for one metric, or None when the value is missing."""
    if value is None:
        return None
    if not spec.higher_is_better and value < 0:
        # Negative P/E, PEG or D/E means negative earnings/growth/equity:
        # the worst band, never the best.
        return spec.fallback_points
    for limit, points in spec.bands:
        if spec.higher_is_better:
            if value >= limit:
                return points
        elif value <= limit:
            return points
    return spec.fallback_points


def score(
    metrics: dict[str, float | None],
    specs: tuple[MetricSpec, ...] = DEFAULT_METRICS,
    buy_at: float = DEFAULT_BUY_AT,
    hold_at: float = DEFAULT_HOLD_AT,
    min_coverage: int = DEFAULT_MIN_COVERAGE,
) -> dict:
    """Score a metric dict -> verdict, 0-100 score, per-metric breakdown."""
    breakdown: dict[str, dict] = {}
    weighted = 0.0
    total_weight = 0.0
    for spec in specs:
        points = score_metric(spec, metrics.get(spec.name))
        if points is None:
            continue
        breakdown[spec.name] = {
            "value": metrics[spec.name],
            "points": points,
            "weight": spec.weight,
        }
        weighted += spec.weight * points
        total_weight += spec.weight
    coverage = len(breakdown)
    if coverage < min_coverage or total_weight <= 0:
        return {
            "verdict": INSUFFICIENT,
            "score": None,
            "coverage": coverage,
            "breakdown": breakdown,
        }
    # Points range is [-1, 2]; normalize to 0..100 over the metrics present.
    normalized = (weighted + total_weight) / (3.0 * total_weight) * 100.0
    normalized = max(0.0, min(100.0, normalized))
    if normalized >= buy_at:
        verdict = BUY
    elif normalized >= hold_at:
        verdict = HOLD
    else:
        verdict = SELL
    return {
        "verdict": verdict,
        "score": round(normalized, 1),
        "coverage": coverage,
        "breakdown": breakdown,
    }


def specs_from_config(raw_list: list[dict]) -> tuple[MetricSpec, ...]:
    """Build MetricSpecs from a config.yaml ``fundamentals.scorecard`` list."""
    specs = []
    for item in raw_list:
        specs.append(
            MetricSpec(
                name=item["name"],
                weight=float(item["weight"]),
                higher_is_better=bool(item["higher_is_better"]),
                bands=tuple((float(lim), int(pts)) for lim, pts in item["bands"]),
                fallback_points=int(item.get("fallback_points", -1)),
            )
        )
    return tuple(specs)
