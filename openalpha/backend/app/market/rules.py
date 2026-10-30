"""Alert rules built from the owner's own plan, plus a small state file.

For every holding: a move of N percent from the previous close. For a lot with
``never_sell_below_entry`` (break-even is the floor) an alert when price gets back
to the average entry. For a lot with ``take_profit_pct`` an alert at that target.
Alerts inform; they never trade.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from ..errors import DataError
from ..portfolio.positions import Lot, PortfolioConfig
from .alerts import AlertRule

_SAFE = re.compile(r"[^a-z0-9.-]")


def _slug(text: str) -> str:
    return _SAFE.sub("-", text.lower())


def rules_from_portfolio(
    pf: PortfolioConfig,
    move_pct: float = 5.0,
    prices: Mapping[str, float] | None = None,
) -> list[AlertRule]:
    """Rules for the plan. A break-even alert is only built while the latest price (when
    supplied) is still under the average entry: for a holding that never fell below its
    entry there is nothing to get back to."""
    rules: list[AlertRule] = []
    for symbol in pf.symbols():
        rules.append(
            AlertRule(f"{_slug(symbol)}-move", symbol, "pct_move", move_pct,
                      f"moved {move_pct:g}% or more in a day")
        )
    for account, lots in sorted(pf.accounts.items()):
        by_symbol: dict[str, list[Lot]] = {}
        for lot in lots:
            by_symbol.setdefault(lot.symbol, []).append(lot)
        for symbol, group in sorted(by_symbol.items()):
            shares = sum(g.shares for g in group)
            avg = sum(g.shares * g.entry_price for g in group) / shares
            tag = f"{_slug(account)}-{_slug(symbol)}"
            under = prices is None or symbol not in prices or prices[symbol] < avg
            if any(g.never_sell_below_entry for g in group) and under:
                rules.append(
                    AlertRule(f"{tag}-breakeven", symbol, "above", avg,
                              f"{account}: back to your break-even")
                )
            targets = {g.take_profit_pct for g in group if g.take_profit_pct is not None}
            if len(targets) == 1:
                target = targets.pop()
                rules.append(
                    AlertRule(f"{tag}-target", symbol, "above", avg * (1.0 + target),
                              f"{account}: your take-profit level")
                )
    return rules


def load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DataError(f"alert state unreadable: {path}") from exc
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
    ):
        raise DataError(f"alert state malformed: {path}")
    return dict(raw)


def save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
