"""Immutable trade ledger with CRA-style Adjusted Cost Base (ACB) accounting.

Rules implemented (per CRA treatment of identical properties):
- BUY:  new_acb_per_share = (old_acb_total + cost + commission) / new_shares
         Commissions are capitalized into ACB.
- SELL:  gain = proceeds - (qty * acb_per_share) - sell_commission - sell_fees
         ACB per share is UNCHANGED by a (partial) sale.
- Full liquidation resets the ACB pool to zero.
- ACB pools are per (account, symbol): TFSA and FHSA positions never mix.

What this module does NOT do:
- It does not classify income (capital vs. business) — your CPA does that.
- It does not auto-apply the superficial loss rule — it FLAGS repurchases
  within 30 days of a loss sale in the export notes for CPA review.

The log is append-only: there is no update/delete API. Corrections are new
offsetting trades with a notes field. Fractional shares are supported.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..execution.base import Fill


@dataclass(frozen=True)
class Trade:
    timestamp: datetime
    symbol: str
    side: str            # "BUY" | "SELL"
    quantity: float      # fractional shares supported
    price: float
    commission: float = 0.0
    fees: float = 0.0
    client_order_id: str = ""
    account: str = ""    # e.g. "TFSA", "FHSA" — ACB pools are per (account, symbol)
    notes: str = ""


@dataclass
class Disposition:
    date: date
    symbol: str
    quantity: float
    proceeds: float
    acb: float           # cost attributed to the shares sold
    expenses: float      # commissions + fees on the sale
    gain_loss: float
    account: str = ""
    notes: str = ""


@dataclass
class _AcbPool:
    shares: float = 0.0
    total_cost: float = 0.0  # includes capitalized commissions

    @property
    def per_share(self) -> float:
        return self.total_cost / self.shares if self.shares else 0.0


class TradeLedger:
    def __init__(self) -> None:
        self._trades: list[Trade] = []
        self._pools: dict[tuple[str, str], _AcbPool] = {}  # (account, symbol)
        self._dispositions: list[Disposition] = []

    # -- immutable log ----------------------------------------------------
    def record_fill(self, fill: Fill, account: str = "", fees: float = 0.0,
                    notes: str = "") -> Trade:
        trade = Trade(
            timestamp=fill.filled_at,
            symbol=fill.symbol,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            commission=fill.commission,
            fees=fees,
            client_order_id=fill.client_order_id,
            account=account,
            notes=notes,
        )
        self._apply(trade)
        self._trades.append(trade)
        return trade

    def record_opening_balance(self, symbol: str, account: str, shares: float,
                               total_cost: float, asof: datetime,
                               notes: str = "") -> Trade:
        """Seed an ACB pool from a known book cost (e.g. derived from a
        broker screenshot as value - unrealized P/L). Recorded as an explicit
        opening-balance BUY so the audit trail stays honest about its source."""
        if shares <= 0:
            raise ValueError("shares must be positive")
        trade = Trade(
            timestamp=asof, symbol=symbol, side="BUY", quantity=shares,
            price=round(total_cost / shares, 4), commission=0.0, fees=0.0,
            client_order_id=f"opening:{account}:{symbol}:{asof.date().isoformat()}",
            account=account,
            notes=((notes + "; ") if notes else "") +
                  "OPENING BALANCE — book cost derived, verify against statements",
        )
        self._apply(trade)
        self._trades.append(trade)
        return trade

    @property
    def trades(self) -> tuple[Trade, ...]:
        return tuple(self._trades)

    # -- ACB accounting ---------------------------------------------------
    def _pool(self, account: str, symbol: str) -> _AcbPool:
        return self._pools.setdefault((account, symbol), _AcbPool())

    def _apply(self, t: Trade) -> None:
        pool = self._pool(t.account, t.symbol)
        if t.side == "BUY":
            pool.total_cost += t.quantity * t.price + t.commission + t.fees
            pool.shares += t.quantity
        elif t.side == "SELL":
            if t.quantity > pool.shares + 1e-9:
                raise ValueError(
                    f"cannot sell {t.quantity} {t.symbol}: only {pool.shares} held")
            acb = t.quantity * pool.per_share
            proceeds = t.quantity * t.price
            expenses = t.commission + t.fees
            gain = proceeds - acb - expenses
            pool.total_cost -= acb
            pool.shares -= t.quantity
            if pool.shares <= 1e-9:
                pool.shares, pool.total_cost = 0.0, 0.0  # full liquidation resets
            self._dispositions.append(Disposition(
                date=t.timestamp.date(), symbol=t.symbol, quantity=t.quantity,
                proceeds=round(proceeds, 2), acb=round(acb, 2),
                expenses=round(expenses, 2), gain_loss=round(gain, 2),
                account=t.account))
        else:
            raise ValueError(f"unknown side {t.side}")

    def get_acb_per_share(self, symbol: str, account: str = "") -> float:
        return self._pool(account, symbol).per_share

    def get_position(self, symbol: str, account: str = "") -> float:
        return self._pool(account, symbol).shares

    def positions(self, account: str | None = None) -> dict[tuple[str, str], float]:
        return {(a, s): p.shares for (a, s), p in self._pools.items()
                if p.shares and (account is None or a == account)}

    def dispositions(self, start: date | None = None,
                     end: date | None = None) -> list[Disposition]:
        out = self._dispositions
        if start:
            out = [d for d in out if d.date >= start]
        if end:
            out = [d for d in out if d.date <= end]
        return list(out)

    # -- superficial-loss flagging ----------------------------------------
    def _flag_superficial_losses(self, disps: list[Disposition]) -> None:
        """Flag loss dispositions where the same symbol was repurchased
        within 30 calendar days after the sale (CPA review, not advice)."""
        buys: dict[tuple[str, str], list[date]] = {}
        for t in self._trades:
            if t.side == "BUY":
                buys.setdefault((t.account, t.symbol), []).append(t.timestamp.date())
        for d in disps:
            if d.gain_loss >= 0:
                continue
            window_end = d.date + timedelta(days=30)
            repurchased = any(d.date < b <= window_end
                              for b in buys.get((d.account, d.symbol), []))
            if repurchased:
                d.notes = (d.notes + "; " if d.notes else "") + \
                    "FLAG: repurchased within 30 days of loss sale — " \
                    "superficial loss rule may apply; CPA review required"

    # -- tax export ---------------------------------------------------------
    def export_tax_csv(self, path: str | Path, year: int) -> Path:
        """Deterministic per-tax-year CSV for Schedule 3 preparation."""
        path = Path(path)
        disps = self.dispositions(date(year, 1, 1), date(year, 12, 31))
        self._flag_superficial_losses(disps)
        disps.sort(key=lambda d: (d.date, d.account, d.symbol))
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "account", "symbol", "quantity", "proceeds",
                        "acb", "expenses", "gain_loss", "notes"])
            for d in disps:
                w.writerow([d.date.isoformat(), d.account, d.symbol, d.quantity,
                            f"{d.proceeds:.2f}", f"{d.acb:.2f}",
                            f"{d.expenses:.2f}", f"{d.gain_loss:.2f}", d.notes])
        return path

    # -- reconciliation ------------------------------------------------------
    def reconcile(self, broker_trades: list[dict]) -> list[str]:
        """Compare ledger against broker statement rows.

        Each row: {"client_order_id": str, "quantity": float, "price": float}.
        Returns human-readable mismatch descriptions (empty = clean)."""
        by_id = {t.client_order_id: t for t in self._trades}
        problems = []
        for row in broker_trades:
            cid = row.get("client_order_id", "")
            t = by_id.get(cid)
            if t is None:
                problems.append(f"broker fill {cid} missing from ledger")
            elif abs(t.quantity - row.get("quantity", 0)) > 1e-9 or \
                    abs(t.price - row.get("price", 0)) > 0.01:
                problems.append(f"mismatch on {cid}: ledger "
                                f"{t.quantity}@{t.price} vs broker "
                                f"{row.get('quantity')}@{row.get('price')}")
        return problems
