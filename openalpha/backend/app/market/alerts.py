"""Price alerts. Information only: an alert never trades or recommends a trade.

Pure evaluation over supplied quotes and a small ``state`` mapping, so the caller
owns persistence. Each level alert rings once until reset; a percent-move alert
rings at most once per day.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

KINDS = ("above", "below", "pct_move")
_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,60}")


@dataclass(frozen=True)
class Quote:
    price: float
    prev_close: float | None = None


@dataclass(frozen=True)
class AlertRule:
    id: str
    symbol: str
    kind: str                      # above | below | pct_move
    level: float                   # price for above/below, percent for pct_move
    note: str = ""

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.id):
            raise ValueError(f"bad alert id {self.id!r}")
        if self.kind not in KINDS:
            raise ValueError(f"unknown alert kind {self.kind!r}")
        if self.level <= 0:
            raise ValueError("level must be positive")


@dataclass(frozen=True)
class Fired:
    rule: AlertRule
    price: float
    message: str


def evaluate(
    rules: list[AlertRule],
    quotes: Mapping[str, Quote],
    state: Mapping[str, str],
    today: date,
) -> tuple[list[Fired], dict[str, str]]:
    """Which rules ring now, and the updated state (rule id -> ISO date it last rang)."""
    new_state = dict(state)
    fired: list[Fired] = []
    for r in rules:
        q = quotes.get(r.symbol)
        if q is None or q.price <= 0:
            continue
        last = state.get(r.id)
        if r.kind == "pct_move":
            if last == today.isoformat() or q.prev_close is None or q.prev_close <= 0:
                continue
            move = (q.price / q.prev_close - 1.0) * 100.0
            if abs(move) < r.level:
                continue
            msg = f"{r.symbol} moved {move:+.1f}% from the previous close"
        else:
            if last is not None:
                continue
            hit = q.price >= r.level if r.kind == "above" else q.price <= r.level
            if not hit:
                continue
            msg = f"{r.symbol} is {q.price:.2f}, {r.kind} your level {r.level:.2f}"
        new_state[r.id] = today.isoformat()
        fired.append(Fired(r, q.price, msg + (f" ({r.note})" if r.note else "")))
    return fired, new_state
