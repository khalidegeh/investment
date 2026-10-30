"""Paper broker: fills at reference price +/- slippage, flat commission.

Idempotency: repeating a client_order_id returns the original fill —
replaying history can never double-fill.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Broker, Fill, Order


class PaperBroker(Broker):
    def __init__(self, slippage_bps: float = 5.0, commission_per_trade: float = 4.95):
        self.slippage_bps = slippage_bps
        self.commission_per_trade = commission_per_trade
        self._fills: dict[str, Fill] = {}

    def place_order(self, order: Order, reference_price: float) -> Fill:
        if order.client_order_id in self._fills:
            return self._fills[order.client_order_id]  # idempotent replay
        if order.quantity <= 0:
            raise ValueError("quantity must be positive")
        slip = reference_price * self.slippage_bps / 10_000
        price = reference_price + slip if order.side == "BUY" else reference_price - slip
        fill = Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=round(price, 4),
            commission=self.commission_per_trade,
            filled_at=datetime.now(timezone.utc),
        )
        self._fills[order.client_order_id] = fill
        return fill
