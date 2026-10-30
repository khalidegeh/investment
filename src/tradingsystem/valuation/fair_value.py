"""Combine method ranges into one displayed fair value range."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .methods import MethodRange


@dataclass(frozen=True)
class FairValue:
    symbol: str
    price: float
    low: float
    base: float
    high: float
    methods: tuple[MethodRange, ...]

    @property
    def base_vs_price(self) -> float:
        """Base fair value relative to price: +0.20 means 20% above price."""
        return self.base / self.price - 1.0

    @property
    def position(self) -> str:
        if self.price < self.low:
            return "below-range"
        if self.price > self.high:
            return "above-range"
        return "within-range"


def combine(symbol: str, price: float, methods: list[MethodRange]) -> FairValue:
    """Median of the per-method lows, bases and highs.

    The median keeps one outlier method from setting the range. At least two
    methods are required so the output is never a single method relabelled.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    if len(methods) < 2:
        raise ValueError("need at least two methods to form a range")
    low = median(m.low for m in methods)
    base = median(m.base for m in methods)
    high = median(m.high for m in methods)
    return FairValue(symbol, price, low, base, high, tuple(methods))
