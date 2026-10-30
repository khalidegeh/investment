"""Risk manager: fail-closed behavior, kill switch, sizing."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingsystem.risk.manager import PortfolioState, RiskManager


def _state(equity=100_000.0, positions=None, prices=None):
    return PortfolioState(equity=equity,
                          positions=positions or {},
                          prices=prices or {"AAA": 100.0})


class TestRisk(unittest.TestCase):
    def test_kill_switch_blocks_everything(self):
        r = RiskManager()
        r.trip("manual")
        chk = r.check("AAA", "BUY", 0.05, None, _state())
        self.assertFalse(chk.approved)
        self.assertIn("kill switch", chk.reason)
        r.reset()
        chk = r.check("AAA", "BUY", 0.05, None, _state())
        self.assertTrue(chk.approved)

    def test_daily_loss_trips_kill_switch(self):
        r = RiskManager(max_daily_loss_pct=0.03)
        r.start_day(100_000.0)
        r.record_day_pnl(-3_001.0)
        self.assertTrue(r.kill_switch)
        chk = r.check("AAA", "BUY", 0.05, None, _state())
        self.assertFalse(chk.approved)

    def test_per_symbol_limit(self):
        r = RiskManager(max_position_pct=0.10)
        # 10% of 100k = 10k -> 200 shares @50 would be 10k; ask for more
        chk = r.check("AAA", "BUY", 0.50, None,
                      _state(prices={"AAA": 50.0}))
        self.assertFalse(chk.approved)
        self.assertIn("per-symbol", chk.reason)

    def test_position_sizing_rounds_down(self):
        r = RiskManager(max_position_pct=0.10, max_exposure_pct=1.0)
        chk = r.check("AAA", "BUY", 0.10, None, _state(prices={"AAA": 30.0}))
        self.assertTrue(chk.approved)
        # 10% of 100k = 10k / 30 = 333.33 -> 333
        self.assertEqual(chk.quantity, 333)

    def test_cannot_sell_what_you_dont_hold(self):
        r = RiskManager()
        chk = r.check("AAA", "SELL", 0.0, None, _state())
        self.assertFalse(chk.approved)

    def test_sell_flattens_position(self):
        r = RiskManager()
        s = _state(positions={"AAA": 100}, prices={"AAA": 50.0})
        chk = r.check("AAA", "SELL", 0.0, None, s)
        self.assertTrue(chk.approved)
        self.assertEqual(chk.quantity, -100)


if __name__ == "__main__":
    unittest.main()
