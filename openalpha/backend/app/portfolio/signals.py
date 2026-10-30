"""Rule-based trend signals. Transparent arithmetic, no model in the loop.

The owner holds through drawdowns, so selling at a loss is never the default:
the stop-loss and downtrend exits are opt-in and off. When one of those
conditions is present but disabled, the signal stays HOLD and says so as a
WARNING, so the information is shown without telling anyone to sell.

These are technical rules with no proven edge. The Sharia gate is absolute:
a BUY for any name that is not explicitly 'compliant' becomes HOLD.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SignalConfig:
    """Defaults are placeholders for the owner to set, not recommendations."""

    fast: int = 50
    slow: int = 200
    z_window: int = 20
    vol_window: int = 20
    z_entry: float = 1.0                 # BUY a pullback this many sigma below the 20-day mean
    z_stretched: float = 2.0             # flag price this many sigma above the 20-day mean
    stop_loss_pct: float | None = None   # opt-in: SELL when this far below your average entry
    sell_on_downtrend: bool = False      # opt-in: SELL on a confirmed downtrend
    trading_days: int = 252


@dataclass(frozen=True)
class Signal:
    action: str                          # BUY | HOLD | SELL | N/A
    reasons: tuple[str, ...]
    sma_fast: float | None = None
    sma_slow: float | None = None
    zscore: float | None = None
    vol_annual: float | None = None
    vs_entry: float | None = None        # price / average entry - 1
    recovery_needed: float | None = None  # gain needed to get back to entry when underwater


def _finite(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def compute_signal(
    close: pd.Series,
    avg_entry: float | None,
    cfg: SignalConfig,
    *,
    sharia_ok: bool,
    take_profit_pct: float | None = None,
) -> Signal:
    """Signal for one holding (stock or ETF) from its daily closes (ascending).

    ``take_profit_pct`` is the owner's per-holding plan: SELL once the price is at
    least that far above the average entry (0.0 means "sell when profitable").
    It needs only the latest price, so it works before 200 bars of history exist.
    """
    if take_profit_pct is not None and avg_entry is not None and len(close) > 0:
        now_price = float(close.iloc[-1])
        if now_price >= avg_entry * (1.0 + take_profit_pct):
            gain = now_price / avg_entry - 1.0
            plan = (
                "your plan: sell when profitable"
                if take_profit_pct == 0
                else f"target {take_profit_pct:.1%}"
            )
            return Signal("SELL", (f"profit target met: {gain:.1%} above your entry ({plan})",),
                          vs_entry=gain)
    if len(close) < cfg.slow + 1:
        return Signal("N/A", (f"need {cfg.slow + 1} daily bars, have {len(close)}",))

    last = float(close.iloc[-1])
    sma_fast = float(close.tail(cfg.fast).mean())
    sma_slow = float(close.tail(cfg.slow).mean())
    window = close.tail(cfg.z_window)
    std = float(window.std())
    z = _finite((last - float(window.mean())) / std) if std > 0 else None
    rets = np.log(close.tail(cfg.vol_window + 1).to_numpy(dtype="float64"))
    vol = _finite(float(np.std(np.diff(rets), ddof=1) * math.sqrt(cfg.trading_days)))
    vs_entry = None if avg_entry is None else last / avg_entry - 1.0
    recovery = None if avg_entry is None or last >= avg_entry else avg_entry / last - 1.0

    def sig(action: str, *reasons: str) -> Signal:
        return Signal(action, reasons, sma_fast, sma_slow, z, vol, vs_entry, recovery)

    uptrend = last > sma_slow and sma_fast > sma_slow
    downtrend = last < sma_slow and sma_fast < sma_slow
    notes: list[str] = []
    if vs_entry is not None and recovery is not None:
        notes.append(f"below your entry by {-vs_entry:.1%}; needs +{recovery:.1%} to get back")
    trend_msg = f"confirmed downtrend: price and SMA{cfg.fast} are below SMA{cfg.slow}"

    if (
        cfg.stop_loss_pct is not None
        and avg_entry is not None
        and last <= avg_entry * (1.0 - cfg.stop_loss_pct)
    ):
        return sig("SELL", f"stop loss: {cfg.stop_loss_pct:.0%} or more below your entry", *notes)
    if downtrend and cfg.sell_on_downtrend:
        return sig("SELL", trend_msg, *notes)
    if downtrend:
        return sig("HOLD", f"WARNING: {trend_msg} (exits are off, holding)", *notes)
    if uptrend and z is not None and z <= -cfg.z_entry:
        if not sharia_ok:
            return sig("HOLD", "BUY blocked by the Sharia gate (status is not 'compliant')", *notes)
        return sig(
            "BUY", f"pullback in an uptrend: z-score {z:.2f} vs the {cfg.z_window}-day mean", *notes
        )
    notes.insert(0, "uptrend" if uptrend else "mixed trend")
    if z is not None and z >= cfg.z_stretched:
        notes.append(f"stretched: z-score {z:.2f}")
    return sig("HOLD", *notes)
