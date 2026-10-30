"""Portfolio analytics on top of ledger positions and price history.

Pure functions over plain lists and dicts: no network, no pickles. Callers
supply aligned, date-ordered series (ascending) and CAD-converted values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean, stdev

TRADING_DAYS = 252


def daily_returns(prices: list[float]) -> list[float]:
    """Simple daily returns from an ascending price series."""
    if len(prices) < 2:
        raise ValueError("need at least two prices")
    if any(p <= 0 or not math.isfinite(p) for p in prices):
        raise ValueError("prices must be positive and finite")
    return [b / a - 1.0 for a, b in pairwise(prices)]


def annualized_volatility(returns: list[float], periods: int = TRADING_DAYS) -> float:
    """Sample standard deviation of returns, scaled by sqrt(periods)."""
    if len(returns) < 2:
        raise ValueError("need at least two returns")
    return stdev(returns) * math.sqrt(periods)


def max_drawdown(prices: list[float]) -> float:
    """Worst peak-to-trough decline as a negative fraction (0.0 if none)."""
    if not prices:
        raise ValueError("no prices")
    peak = prices[0]
    worst = 0.0
    for p in prices:
        peak = max(peak, p)
        worst = min(worst, p / peak - 1.0)
    return worst


@dataclass(frozen=True)
class Concentration:
    weights: dict[str, float]
    top1: float
    top3: float
    hhi: float                 # sum of squared weights, in (0, 1]
    effective_names: float     # 1 / HHI: how many equal-sized names this equals


def concentration(values: dict[str, float]) -> Concentration:
    """Weights, top-N share and Herfindahl index from CAD position values."""
    if not values:
        raise ValueError("no positions")
    if any(v < 0 or not math.isfinite(v) for v in values.values()):
        raise ValueError("position values must be non-negative and finite")
    total = sum(values.values())
    if total <= 0:
        raise ValueError("portfolio value must be positive")
    weights = {k: v / total for k, v in values.items()}
    ranked = sorted(weights.values(), reverse=True)
    hhi = sum(w * w for w in ranked)
    return Concentration(
        weights=weights,
        top1=ranked[0],
        top3=sum(ranked[:3]),
        hhi=hhi,
        effective_names=1.0 / hhi,
    )


@dataclass(frozen=True)
class BenchmarkStats:
    beta: float
    correlation: float
    portfolio_return: float     # cumulative over the window
    benchmark_return: float
    excess_return: float        # portfolio minus benchmark, cumulative


def _cumulative(returns: list[float]) -> float:
    total = 1.0
    for r in returns:
        total *= 1.0 + r
    return total - 1.0


def benchmark_stats(
    portfolio: list[float], benchmark: list[float]
) -> BenchmarkStats:
    """Beta, correlation and cumulative excess return vs a benchmark.

    Both series are aligned daily returns of equal length.
    """
    if len(portfolio) != len(benchmark):
        raise ValueError("series must have equal length")
    if len(portfolio) < 2:
        raise ValueError("need at least two observations")
    mp, mb = fmean(portfolio), fmean(benchmark)
    cov = sum((p - mp) * (b - mb) for p, b in zip(portfolio, benchmark)) / (
        len(portfolio) - 1
    )
    var_b = stdev(benchmark) ** 2
    sd_p = stdev(portfolio)
    if var_b == 0 or sd_p == 0:
        raise ValueError("zero variance series: beta undefined")
    beta = cov / var_b
    corr = cov / (sd_p * math.sqrt(var_b))
    pr, br = _cumulative(portfolio), _cumulative(benchmark)
    return BenchmarkStats(beta, corr, pr, br, pr - br)
