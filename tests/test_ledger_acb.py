"""ACB tests with hand-computed values (CRA treatment).

Commissions are capitalized into ACB on buys and expensed on sells.
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingsystem.execution.base import Fill
from tradingsystem.ledger.trade_ledger import TradeLedger


def _fill(symbol, side, qty, price, commission=0.0, ts="2024-01-02T10:00:00"):
    return Fill(client_order_id=f"{symbol}-{side}-{qty}-{price}-{ts}",
                symbol=symbol, side=side, quantity=qty, price=price,
                commission=commission,
                filled_at=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc))


class TestAcb(unittest.TestCase):
    def test_buy_capitalizes_commission(self):
        L = TradeLedger()
        L.record_fill(_fill("AAA", "BUY", 100, 10.00, commission=10.00))
        # (100*10 + 10) / 100 = 10.10
        self.assertAlmostEqual(L.get_acb_per_share("AAA"), 10.10, places=2)
        self.assertEqual(L.get_position("AAA"), 100)

    def test_second_buy_averages(self):
        L = TradeLedger()
        L.record_fill(_fill("AAA", "BUY", 100, 10.00, commission=10.00))
        L.record_fill(_fill("AAA", "BUY", 100, 20.00, commission=10.00,
                            ts="2024-02-01T10:00:00"))
        # (1010 + 2010) / 200 = 15.10
        self.assertAlmostEqual(L.get_acb_per_share("AAA"), 15.10, places=2)

    def test_partial_sale_keeps_acb_and_computes_gain(self):
        L = TradeLedger()
        L.record_fill(_fill("AAA", "BUY", 100, 10.00, commission=10.00))
        L.record_fill(_fill("AAA", "BUY", 100, 20.00, commission=10.00,
                            ts="2024-02-01T10:00:00"))
        L.record_fill(_fill("AAA", "SELL", 50, 25.00, commission=10.00,
                            ts="2024-03-01T10:00:00"))
        # proceeds 1250 - acb 50*15.10=755 - expenses 10 = 485
        d = L.dispositions()[0]
        self.assertAlmostEqual(d.gain_loss, 485.00, places=2)
        self.assertAlmostEqual(d.acb, 755.00, places=2)
        # ACB per share unchanged after partial sale
        self.assertAlmostEqual(L.get_acb_per_share("AAA"), 15.10, places=2)
        self.assertEqual(L.get_position("AAA"), 150)

    def test_full_liquidation_resets_pool(self):
        L = TradeLedger()
        L.record_fill(_fill("AAA", "BUY", 100, 10.00, commission=10.00))
        L.record_fill(_fill("AAA", "SELL", 100, 12.00, commission=10.00,
                            ts="2024-03-01T10:00:00"))
        self.assertEqual(L.get_position("AAA"), 0)
        self.assertAlmostEqual(L.get_acb_per_share("AAA"), 0.0)
        # New buy starts a fresh pool
        L.record_fill(_fill("AAA", "BUY", 50, 30.00, commission=5.00,
                            ts="2024-04-01T10:00:00"))
        self.assertAlmostEqual(L.get_acb_per_share("AAA"), 30.10, places=2)

    def test_sell_more_than_held_raises(self):
        L = TradeLedger()
        L.record_fill(_fill("AAA", "BUY", 10, 10.00))
        with self.assertRaises(ValueError):
            L.record_fill(_fill("AAA", "SELL", 11, 10.00,
                                ts="2024-03-01T10:00:00"))

    def test_loss_and_superficial_flag(self):
        L = TradeLedger()
        L.record_fill(_fill("AAA", "BUY", 100, 20.00, ts="2024-01-02T10:00:00"))
        L.record_fill(_fill("AAA", "SELL", 100, 10.00, ts="2024-02-01T10:00:00"))
        L.record_fill(_fill("AAA", "BUY", 100, 11.00, ts="2024-02-10T10:00:00"))
        out = Path("/tmp") / "test_tax.csv"
        L.export_tax_csv(out, 2024)
        text = out.read_text()
        self.assertIn("FLAG", text)
        self.assertIn("superficial", text)

    def test_tax_csv_columns_and_sort(self):
        L = TradeLedger()
        L.record_fill(_fill("BBB", "BUY", 10, 100.00, ts="2024-01-02T10:00:00"))
        L.record_fill(_fill("AAA", "BUY", 10, 50.00, ts="2024-01-03T10:00:00"))
        L.record_fill(_fill("BBB", "SELL", 10, 110.00, ts="2024-06-01T10:00:00"))
        L.record_fill(_fill("AAA", "SELL", 10, 60.00, ts="2024-05-01T10:00:00"))
        out = Path("/tmp") / "test_tax2.csv"
        L.export_tax_csv(out, 2024)
        lines = out.read_text().strip().split("\n")
        self.assertEqual(lines[0],
                         "date,account,symbol,quantity,proceeds,acb,expenses,gain_loss,notes")
        self.assertEqual(len(lines), 3)  # header + 2 dispositions
        self.assertTrue(lines[1].startswith("2024-05-01,,"))  # sorted by date

    def test_ledger_is_immutable_view(self):
        L = TradeLedger()
        L.record_fill(_fill("AAA", "BUY", 10, 10.00))
        with self.assertRaises(AttributeError):
            L.trades.append("tamper")  # tuple has no append


if __name__ == "__main__":
    unittest.main()
