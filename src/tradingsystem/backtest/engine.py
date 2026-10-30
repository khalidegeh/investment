"""Event-driven backtest engine.

Replays bars in chronological order through the REAL Strategy, RiskManager,
Broker and Ledger — the same code path paper trading will use. A signal on
bar t fills at bar t's close (no lookahead). Supports a multi-symbol
universe with halal exclusions applied before any signal can fire.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Config
from ..data.base import DataProvider
from ..data.csv_provider import CsvProvider
from ..data.earnings import CsvEarningsCalendar, EarningsCalendar
from ..execution.base import Order
from ..execution.paper_broker import PaperBroker
from ..ledger.trade_ledger import TradeLedger
from ..risk.manager import PortfolioState, RiskManager
from ..strategy.base import Strategy
from ..strategy.earnings_approach import EarningsApproachSignal
from ..strategy.sma_crossover import SmaCrossover


@dataclass
class BacktestResult:
    config_hash: str
    data_hash: str
    strategy_name: str
    strategy_version: str
    seed: int
    start_cash: float
    end_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    num_trades: int
    realized_pnl: float
    symbols: list[str] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    rejected_signals: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def _data_hash(frames: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for sym in sorted(frames):
        h.update(sym.encode())
        h.update(pd.util.hash_pandas_object(frames[sym], index=True).values.tobytes())
    return h.hexdigest()[:16]


def _build_strategy(cfg: Config, calendar: EarningsCalendar,
                    exclusions: set[str]) -> Strategy:
    name = cfg.get("strategy", "name")
    if name == "earnings_approach":
        return EarningsApproachSignal(
            calendar=calendar,
            entry_days_before=int(cfg.get("strategy", "entry_days_before", default=30)),
            exit_days_before=int(cfg.get("strategy", "exit_days_before", default=1)),
            trend_window=int(cfg.get("strategy", "trend_window", default=20)),
            weight=float(cfg.get("strategy", "weight", default=0.10)),
            halal_exclusions=exclusions,
        )
    if name == "sma_crossover":
        return SmaCrossover(
            fast=int(cfg.get("strategy", "fast", default=20)),
            slow=int(cfg.get("strategy", "slow", default=50)),
            weight=float(cfg.get("strategy", "weight", default=0.10)),
        )
    raise ValueError(f"unknown strategy {name}")


def _universe(cfg: Config, symbols: list[str] | str | None) -> list[str]:
    if symbols is not None:
        return [symbols] if isinstance(symbols, str) else list(symbols)
    return list(cfg.get("universe", default=["AAA"]))


def run_backtest(cfg: Config, symbols: list[str] | str | None,
                 start: datetime, end: datetime,
                 data_dir: str | Path | None = None) -> BacktestResult:
    seed = int(cfg.get("backtest", "seed", default=42))
    start_cash = float(cfg.get("backtest", "start_cash", default=100_000))
    universe = _universe(cfg, symbols)
    exclusions = set(cfg.get("halal_exclusions", default=[]))
    tradable = [s for s in universe if s not in exclusions]

    data_path = Path(data_dir or cfg.get("data", "path", default="data"))
    provider: DataProvider = CsvProvider(data_path)
    calendar = CsvEarningsCalendar(data_path / "earnings")

    bars: dict[str, pd.DataFrame] = {}
    for sym in tradable:
        df = provider.get_bars(sym, start, end)
        if df.empty:
            raise ValueError(f"no bars for {sym} in range")
        bars[sym] = df

    strategy = _build_strategy(cfg, calendar, exclusions)
    risk = RiskManager(
        max_position_pct=float(cfg.get("risk", "max_position_pct", default=0.10)),
        max_exposure_pct=float(cfg.get("risk", "max_exposure_pct", default=0.60)),
        max_daily_loss_pct=float(cfg.get("risk", "max_daily_loss_pct", default=0.03)),
        kill_switch=bool(cfg.get("risk", "kill_switch", default=False)),
    )
    broker = PaperBroker(
        slippage_bps=float(cfg.get("execution", "slippage_bps", default=5.0)),
        commission_per_trade=float(cfg.get("execution", "commission_per_trade", default=4.95)),
    )
    ledger = TradeLedger()

    # Unified timeline across the universe.
    all_ts = sorted({ts for df in bars.values() for ts in df["timestamp"]})
    cash = start_cash
    equity_curve: list[dict] = []
    rejected: list[dict] = []
    seq = 0
    prev_day = None
    last_close: dict[str, float] = {}

    for ts in all_ts:
        day = ts.date()
        prices = {s: float(bars[s].loc[bars[s]["timestamp"] == ts, "close"].iloc[0])
                  for s in tradable if ts in set(bars[s]["timestamp"])}
        last_close.update(prices)
        if day != prev_day:
            equity = cash + sum(ledger.get_position(s) * last_close.get(s, 0.0)
                                for s in tradable)
            risk.start_day(equity)
            prev_day = day

        for sym in tradable:
            df = bars[sym]
            hist = df[df["timestamp"] <= ts]  # bars up to and including t only
            if hist.empty:
                continue
            position = ledger.get_position(sym)
            for signal in strategy.on_bar(sym, hist.reset_index(drop=True),
                                          position=position):
                price = float(hist["close"].iloc[-1])
                state = PortfolioState(
                    equity=cash + sum(ledger.get_position(s) * last_close.get(s, 0.0)
                                      for s in tradable),
                    positions={s: ledger.get_position(s) for s in tradable},
                    prices=dict(last_close),
                )
                check = risk.check(sym, signal.side, signal.target_weight,
                                   signal.quantity, state)
                if not check.approved:
                    rejected.append({"timestamp": ts.isoformat(), "symbol": sym,
                                     "reason": check.reason,
                                     "signal_reason": signal.reason})
                    continue
                seq += 1
                order = Order(
                    client_order_id=f"bt{seed}:{sym}:{ts.strftime('%Y%m%d')}:{seq:03d}",
                    symbol=sym, side=signal.side,
                    quantity=abs(check.quantity), created_at=ts.to_pydatetime(),
                )
                fill = broker.place_order(order, reference_price=price)
                ledger.record_fill(fill)
                if fill.side == "BUY":
                    cash -= fill.quantity * fill.price + fill.commission
                else:
                    cash += fill.quantity * fill.price - fill.commission

        equity = cash + sum(ledger.get_position(s) * last_close.get(s, 0.0)
                            for s in tradable)
        equity_curve.append({"timestamp": ts.isoformat(),
                             "equity": round(equity, 2)})

    end_equity = equity_curve[-1]["equity"] if equity_curve else start_cash
    peak, max_dd = start_cash, 0.0
    for pt in equity_curve:
        peak = max(peak, pt["equity"])
        max_dd = max(max_dd, (peak - pt["equity"]) / peak if peak else 0.0)

    realized = sum(d.gain_loss for d in ledger.dispositions())
    return BacktestResult(
        config_hash=cfg.content_hash(),
        data_hash=_data_hash(bars),
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        seed=seed,
        start_cash=start_cash,
        end_equity=round(end_equity, 2),
        total_return_pct=round((end_equity - start_cash) / start_cash * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        num_trades=len(ledger.trades),
        realized_pnl=round(realized, 2),
        symbols=tradable,
        equity_curve=equity_curve,
        rejected_signals=rejected,
    )
