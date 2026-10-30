"""Execution contract. Orders are idempotent via client_order_id."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Order:
    client_order_id: str   # unique per intent, e.g. "run42:AAPL:2024-01-02:003"
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int          # always positive; side gives direction
    created_at: datetime


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float       # fractional shares supported (e.g. Wealthsimple)
    price: float
    commission: float
    filled_at: datetime


class Broker(ABC):
    @abstractmethod
    def place_order(self, order: Order, reference_price: float) -> Fill:
        """Execute a market order. Must be idempotent on client_order_id."""
        ...
