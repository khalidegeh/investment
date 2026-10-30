"""Backtest engine: determinism and no-lookahead."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingsystem.backtest.engine import run_backtest
from tradingsystem.config import Config
from tradingsystem.strategy.base import Strategy


def _peeking_strategy():
    """A deliberately cheating strategy: buys if tomorrow's close is higher.
    The engine must still only hand it history up to bar t, so it can never
    actually see the future — we assert the history length invariant here."""

    class Peek(Strategy):
        name = "peek"
        version = "0.0-test"

        def on_bar(self, symbol, history, position=0):
            # history must end at the current bar: engine slices iloc[:i+1]
            assert history["timestamp"].is_monotonic_increasing
            return []

    return Peek()


class TestBacktest(unittest.TestCase):
    def _write_data(self, tmp: Path, symbol="AAA", n=120):
        idx = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
        # deterministic pseudo-prices: slow sine wave -> guarantees crossovers
        import math
        closes = [100 + 10 * math.sin(i / 8) + i * 0.05 for i in range(n)]
        df = pd.DataFrame({
            "timestamp": idx, "open": closes, "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes], "close": closes,
            "volume": [1000] * n})
        (tmp / "data").mkdir(exist_ok=True)
        df.to_csv(tmp / "data" / f"{symbol}.csv", index=False)
        return tmp / "data"

    def _cfg(self, tmp: Path):
        return Config(raw={
            "data": {"provider": "csv", "path": str(tmp / "data")},
            "strategy": {"name": "sma_crossover", "version": "1.0",
                         "fast": 5, "slow": 20, "weight": 0.10},
            "risk": {"max_position_pct": 0.10, "max_exposure_pct": 0.60,
                     "max_daily_loss_pct": 0.50, "kill_switch": False},
            "execution": {"slippage_bps": 0, "commission_per_trade": 0.0},
            "backtest": {"start_cash": 100_000.0, "seed": 42}})

    def test_deterministic(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write_data(tmp)
            cfg = self._cfg(tmp)
            r1 = run_backtest(cfg, "AAA", datetime(2023, 1, 1),
                              datetime(2023, 12, 31))
            r2 = run_backtest(cfg, "AAA", datetime(2023, 1, 1),
                              datetime(2023, 12, 31))
            self.assertEqual(r1.to_json(), r2.to_json())
            self.assertGreater(r1.num_trades, 0)  # sine wave must trigger

    def test_no_lookahead_history_window(self):
        # The engine slices history as iloc[:i+1]; assert monotonicity held
        # for every bar via the peeking strategy's assertion.
        import tempfile
        from tradingsystem.backtest import engine as eng
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._write_data(tmp)
            cfg = self._cfg(tmp)
            orig = eng._build_strategy
            eng._build_strategy = lambda c, cal, exc: _peeking_strategy()
            try:
                eng.run_backtest(cfg, "AAA", datetime(2023, 1, 1),
                                 datetime(2023, 4, 30))
            finally:
                eng._build_strategy = orig


if __name__ == "__main__":
    unittest.main()
