"""Rule-based trend signals. Transparent arithmetic, no model in the loop.

These are technical rules with no proven edge. They exist to be backtested and
scored, not trusted. The Sharia gate is absolute: a BUY for any name that is
not explicitly 'compliant' is downgraded to HOLD.
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
    z_entry: float = 1.0           # BUY a pullback this many sigma below the 20-day mean
    z_stretched: float = 2.0       # flag price this many sigma above the 20-day mean
    stop_loss_pct: float = 0.15    # SELL when price is this far below your average entry
    trading_days: int = 252


@dataclass(frozen=True)
class Signal:
    action: str                    # BUY | HOLD | SELL | N/A
    reasons: tuple[str, ...]
    sma_fast: float | None = None
    sma_slow: float | None = None
    zscore: float | None = None
    vol_annual: float | None = None


def _finite(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def compute_signal(
    close: pd.Series,
    avg_entry: float | None,
    cfg: SignalConfig,
    *,
    sharia_ok: bool,
    instrument: str = "stock",
) -> Signal:
    """Signal for one holding from its daily closes (ascending)."""
    if instrument != "stock":
        return Signal("N/A", ("ETF: excluded from signals",))
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

    def sig(action: str, *reasons: str) -> Signal:
        return Signal(action, reasons, sma_fast, sma_slow, z, vol)

    uptrend = last > sma_slow and sma_fast > sma_slow
    downtrend = last < sma_slow and sma_fast < sma_slow

    if avg_entry is not None and last <= avg_entry * (1.0 - cfg.stop_loss_pct):
        return sig("SELL", f"stop loss: price is {cfg.stop_loss_pct:.0%} or more below your entry")
    if downtrend:
        return sig("SELL", f"confirmed downtrend: price and SMA{cfg.fast} are below SMA{cfg.slow}")
    if uptrend and z is not None and z <= -cfg.z_entry:
        if not sharia_ok:
            return sig("HOLD", "BUY blocked by the Sharia gate (status is not 'compliant')")
        return sig("BUY", f"pullback in an uptrend: z-score {z:.2f} vs the {cfg.z_window}-day mean")
    notes = ["uptrend" if uptrend else "mixed trend"]
    if z is not None and z >= cfg.z_stretched:
        notes.append(f"stretched: z-score {z:.2f}")
    return sig("HOLD", *notes)
