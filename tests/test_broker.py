"""Paper broker: idempotency and fill math."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingsystem.execution.base import Order
from tradingsystem.execution.paper_broker import PaperBroker


def _order(cid, side="BUY", qty=10):
    return Order(client_order_id=cid, symbol="AAA", side=side, quantity=qty,
                 created_at=datetime.now(timezone.utc))


class TestPaperBroker(unittest.TestCase):
    def test_idempotent_replay(self):
        b = PaperBroker()
        f1 = b.place_order(_order("id-1"), reference_price=100.0)
        f2 = b.place_order(_order("id-1"), reference_price=999.0)  # diff price
        self.assertEqual(f1, f2)
        self.assertEqual(f1.price, f2.price)  # original fill kept

    def test_slippage_direction(self):
        b = PaperBroker(slippage_bps=100)  # 1%
        buy = b.place_order(_order("b1", "BUY"), reference_price=100.0)
        sell = b.place_order(_order("s1", "SELL"), reference_price=100.0)
        self.assertAlmostEqual(buy.price, 101.0)
        self.assertAlmostEqual(sell.price, 99.0)

    def test_commission_applied(self):
        b = PaperBroker(commission_per_trade=4.95)
        f = b.place_order(_order("c1"), reference_price=100.0)
        self.assertAlmostEqual(f.commission, 4.95)

    def test_zero_quantity_rejected(self):
        b = PaperBroker()
        with self.assertRaises(ValueError):
            b.place_order(_order("z1", qty=0), reference_price=100.0)


if __name__ == "__main__":
    unittest.main()
