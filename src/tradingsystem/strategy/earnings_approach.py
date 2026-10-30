"""Pre-earnings approach signal — the user's actual thesis.

Rules (all parameters in config, versioned):
- ENTER (BUY to target_weight) when:
    exit_days_before < days_to_next_earnings <= entry_days_before
    AND close > SMA(trend_window)                      (uptrend filter)
    AND position == 0                                  (one position per symbol)
    AND symbol not in halal_exclusions
- EXIT (SELL flatten) when:
    days_to_next_earnings <= exit_days_before AND position > 0
    (out before the report, avoiding the binary event)
    OR asof is the last trading day (weekday) before the report.
    The weekday roll-back fixes the Monday-earnings hole: with a pure
    calendar-day rule, earnings landing on a Monday meant Friday's bar had
    dte=3 > exit_days_before and the exit only fired on the report day
    itself — never flattening *before* the report. Known limitation:
    exchange holidays need a real trading calendar (Phase-1 data task);
    weekends are handled, holidays fall back to the calendar-day rule.

No earnings date known -> no signal. Deterministic: pure function of
(history, calendar, params, position).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from ..data.earnings import EarningsCalendar
from .base import Signal, Strategy


def _last_trading_day_before(earnings_date: date) -> date:
    """Last weekday strictly before the earnings date.

    Weekend announcements roll back to Friday, so a Monday report flattens
    on Friday's bar. Deterministic; does not know exchange holidays.
    """
    d = earnings_date - timedelta(days=1)
    while d.weekday() >= 5:  # Saturday / Sunday
        d -= timedelta(days=1)
    return d


class EarningsApproachSignal(Strategy):
    name = "earnings_approach"
    version = "1.1"

    def __init__(self, calendar: EarningsCalendar,
                 entry_days_before: int = 30,
                 exit_days_before: int = 1,
                 trend_window: int = 20,
                 weight: float = 0.10,
                 halal_exclusions: set[str] | None = None):
        if not 0 <= exit_days_before < entry_days_before:
            raise ValueError("need 0 <= exit_days_before < entry_days_before")
        self.calendar = calendar
        self.entry_days_before = entry_days_before
        self.exit_days_before = exit_days_before
        self.trend_window = trend_window
        self.weight = weight
        self.halal_exclusions = halal_exclusions or set()

    def _sig(self, symbol: str, side: str, target_weight: float,
             reason: str) -> Signal:
        return Signal(symbol=symbol, side=side, target_weight=target_weight,
                      reason=reason, strategy_name=self.name,
                      strategy_version=self.version)

    def on_bar(self, symbol: str, history: pd.DataFrame,
               position: int = 0) -> list[Signal]:
        if symbol in self.halal_exclusions:
            return []  # excluded from the halal universe: never signal
        if len(history) < self.trend_window:
            return []

        asof = history["timestamp"].iloc[-1].date()
        nxt = self.calendar.next_earnings(symbol, asof)
        if nxt is None:
            return []  # no known upcoming earnings: stay out
        dte = (nxt - asof).days

        close = float(history["close"].iloc[-1])
        sma = float(history["close"].rolling(self.trend_window).mean().iloc[-1])

        if position == 0 and self.exit_days_before < dte <= self.entry_days_before \
                and close > sma:
            return [self._sig(symbol, "BUY", self.weight,
                              f"{dte}d to earnings, close {close:.2f} > "
                              f"SMA{self.trend_window} {sma:.2f}")]
        exit_day = _last_trading_day_before(nxt)
        if position > 0 and (dte <= self.exit_days_before or asof >= exit_day):
            return [self._sig(symbol, "SELL", 0.0,
                              f"flatten before report: dte={dte}d, "
                              f"exit_day={exit_day}")]
        return []
