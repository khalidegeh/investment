"""Fractional shares, per-account ACB pools, opening balances."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingsystem.ledger.trade_ledger import TradeLedger


def _now():
    return datetime(2026, 10, 8, tzinfo=timezone.utc)


class TestAccountsAndFractional(unittest.TestCase):
    def test_fractional_acb(self):
        L = TradeLedger()
        L.record_opening_balance("SHOP.TO", "TFSA", 2.6551, 513.91, _now())
        self.assertAlmostEqual(L.get_position("SHOP.TO", "TFSA"), 2.6551, places=4)
        self.assertAlmostEqual(L.get_acb_per_share("SHOP.TO", "TFSA"),
                               513.91 / 2.6551, places=2)

    def test_pools_are_per_account(self):
        L = TradeLedger()
        L.record_opening_balance("NVDA", "TFSA", 1.0, 222.51, _now())
        L.record_opening_balance("NVDA", "FHSA", 3.0, 720.80, _now())
        self.assertAlmostEqual(L.get_acb_per_share("NVDA", "TFSA"), 222.51, places=2)
        self.assertAlmostEqual(L.get_acb_per_share("NVDA", "FHSA"), 240.27, places=2)
        # selling in one account doesn't touch the other
        from tradingsystem.execution.base import Fill
        fill = Fill(client_order_id="s1", symbol="NVDA", side="SELL",
                    quantity=1.0, price=230.97, commission=0.0,
                    filled_at=_now())
        L.record_fill(fill, account="TFSA")
        self.assertAlmostEqual(L.get_position("NVDA", "TFSA"), 0.0)
        self.assertAlmostEqual(L.get_position("NVDA", "FHSA"), 3.0)
        d = L.dispositions()[0]
        self.assertEqual(d.account, "TFSA")
        self.assertAlmostEqual(d.gain_loss, 230.97 - 222.51, places=2)

    def test_opening_balance_marks_source(self):
        L = TradeLedger()
        t = L.record_opening_balance("XQQ", "FHSA", 26.3435, 2000.00, _now())
        self.assertIn("OPENING BALANCE", t.notes)
        self.assertEqual(t.account, "FHSA")

    def test_rejects_zero_shares(self):
        L = TradeLedger()
        with self.assertRaises(ValueError):
            L.record_opening_balance("AAA", "TFSA", 0.0, 100.0, _now())


if __name__ == "__main__":
    unittest.main()
