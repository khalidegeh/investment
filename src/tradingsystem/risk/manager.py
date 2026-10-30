"""Pre-trade risk manager. Fail-closed: any breach blocks new orders.

Checks (in order):
 1. Kill switch (manual or auto-tripped) -> reject everything.
 2. Per-symbol position limit.
 3. Total gross exposure limit.
 4. Daily loss limit -> trips kill switch, requires manual reset.
 5. Position sizing: target_weight -> share quantity (rounded down).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PortfolioState:
    equity: float
    positions: dict[str, float]       # symbol -> shares held (fractional ok)
    prices: dict[str, float]           # symbol -> latest price
    day_pnl: float = 0.0
    start_of_day_equity: float = 0.0


@dataclass
class RiskCheck:
    approved: bool
    quantity: int = 0                 # signed share quantity (negative = sell)
    reason: str = ""


@dataclass
class RiskManager:
    max_position_pct: float = 0.10
    max_exposure_pct: float = 0.60
    max_daily_loss_pct: float = 0.03
    kill_switch: bool = False
    kill_reason: str = ""
    _day_pnl: float = field(default=0.0, repr=False)
    _sod_equity: float = field(default=0.0, repr=False)

    # -- daily accounting -------------------------------------------------
    def start_day(self, equity: float) -> None:
        self._sod_equity = equity
        self._day_pnl = 0.0

    def record_day_pnl(self, pnl: float) -> None:
        self._day_pnl += pnl
        if self._sod_equity > 0 and self._day_pnl <= -self.max_daily_loss_pct * self._sod_equity:
            self.trip(f"daily loss limit breached: {self._day_pnl:.2f}")

    # -- kill switch ------------------------------------------------------
    def trip(self, reason: str) -> None:
        self.kill_switch = True
        self.kill_reason = reason

    def reset(self) -> None:
        """Manual reset only — a human must acknowledge the breach."""
        self.kill_switch = False
        self.kill_reason = ""

    # -- order check ------------------------------------------------------
    def check(self, symbol: str, side: str, target_weight: float | None,
              quantity: int | None, state: PortfolioState) -> RiskCheck:
        if self.kill_switch:
            return RiskCheck(False, 0, f"kill switch active: {self.kill_reason}")

        price = state.prices.get(symbol)
        if price is None or price <= 0:
            return RiskCheck(False, 0, f"no valid price for {symbol}")

        current_shares = state.positions.get(symbol, 0)

        # Resolve desired quantity.
        if target_weight is not None:
            if side == "BUY":
                desired_value = target_weight * state.equity
                qty = int(desired_value // price)
                # Don't rebuy what we already hold beyond target.
                qty = max(0, qty - current_shares)
            else:  # SELL / flatten
                qty = -current_shares if target_weight == 0.0 else 0
        elif quantity is not None:
            qty = quantity if side == "BUY" else -abs(quantity)
        else:
            return RiskCheck(False, 0, "signal has neither quantity nor target_weight")

        if qty == 0:
            return RiskCheck(False, 0, "quantity resolves to zero; nothing to do")

        # Per-symbol position limit (applies to buys).
        if qty > 0:
            new_value = (current_shares + qty) * price
            if new_value > self.max_position_pct * state.equity:
                return RiskCheck(
                    False, 0,
                    f"per-symbol limit: {(current_shares + qty) * price:.2f} > "
                    f"{self.max_position_pct:.0%} of equity")

        # Total exposure limit.
        gross = sum(abs(sh) * state.prices.get(s, 0.0)
                    for s, sh in state.positions.items())
        gross_after = gross + (abs(qty) * price if qty > 0 else 0)
        if gross_after > self.max_exposure_pct * state.equity:
            return RiskCheck(
                False, 0,
                f"exposure limit: {gross_after:.2f} > {self.max_exposure_pct:.0%} of equity")

        # Can't sell what we don't hold (long-only v1).
        if qty < 0 and abs(qty) > current_shares:
            return RiskCheck(False, 0,
                             f"sell qty {abs(qty)} exceeds held {current_shares}")

        return RiskCheck(True, qty, "approved")
