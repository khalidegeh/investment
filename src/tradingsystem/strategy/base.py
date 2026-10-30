"""Strategy contract. Strategies are pure functions of (history, params)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import pandas as pd

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: Side
    # Exactly one of quantity / target_weight should be set.
    quantity: int | None = None
    target_weight: float | None = None  # fraction of equity, e.g. 0.10
    reason: str = ""
    strategy_name: str = ""
    strategy_version: str = ""


class Strategy(ABC):
    name: str = "base"
    version: str = "0.0"

    @abstractmethod
    def on_bar(self, symbol: str, history: pd.DataFrame, position: int = 0) -> list[Signal]:
        """Called with all bars up to and including the current bar.
        Must never use rows beyond the current bar (no lookahead).
        `position` is shares currently held (0 if flat)."""
        ...
