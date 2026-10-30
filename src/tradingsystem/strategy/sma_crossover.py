"""SMA crossover example strategy. Deliberately simple and deterministic.

Long-only: BUY target_weight when fast SMA crosses above slow SMA,
SELL (flatten) when fast crosses below slow. One position per symbol.
"""
from __future__ import annotations

import pandas as pd

from .base import Signal, Strategy


class SmaCrossover(Strategy):
    name = "sma_crossover"
    version = "1.0"

    def __init__(self, fast: int = 20, slow: int = 50, weight: float = 0.10):
        if fast >= slow:
            raise ValueError("fast must be < slow")
        self.fast = fast
        self.slow = slow
        self.weight = weight

    def on_bar(self, symbol: str, history: pd.DataFrame,
               position: int = 0) -> list[Signal]:
        if len(history) < self.slow + 1:
            return []
        close = history["close"]
        fast_now = close.rolling(self.fast).mean().iloc[-1]
        slow_now = close.rolling(self.slow).mean().iloc[-1]
        fast_prev = close.rolling(self.fast).mean().iloc[-2]
        slow_prev = close.rolling(self.slow).mean().iloc[-2]

        if fast_prev <= slow_prev and fast_now > slow_now:
            return [Signal(symbol=symbol, side="BUY", target_weight=self.weight,
                           reason=f"SMA{self.fast} crossed above SMA{self.slow}",
                           strategy_name=self.name, strategy_version=self.version)]
        if fast_prev >= slow_prev and fast_now < slow_now:
            return [Signal(symbol=symbol, side="SELL", target_weight=0.0,
                           reason=f"SMA{self.fast} crossed below SMA{self.slow}",
                           strategy_name=self.name, strategy_version=self.version)]
        return []
