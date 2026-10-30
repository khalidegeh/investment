"""Tests for the fair value module. Pure math: no network, hand-checked values."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tradingsystem.valuation import combine, consensus_range, dcf_range, pe_history_range
from tradingsystem.valuation.methods import (
    DcfScenario,
    MethodRange,
    dcf_value_per_share,
    percentile,
)


class DcfTests(unittest.TestCase):
    def test_zero_growth_matches_perpetuity(self):
        # growth 0, terminal 0, r 10%, 1 year: PV = 100/1.1 + (100/0.1)/1.1 = 1000
        s = DcfScenario(growth=0.0, discount_rate=0.10, terminal_growth=0.0)
        self.assertAlmostEqual(dcf_value_per_share(100.0, s, 1, 0.0, 1.0), 1000.0)

    def test_net_debt_and_shares(self):
        s = DcfScenario(0.0, 0.10, 0.0)
        # (1000 - 200) / 4 = 200
        self.assertAlmostEqual(dcf_value_per_share(100.0, s, 1, 200.0, 4.0), 200.0)

    def test_two_year_growth_hand_check(self):
        s = DcfScenario(growth=0.10, discount_rate=0.10, terminal_growth=0.0)
        # cash: 110, 121. PV = 110/1.1 + 121/1.21 + (121/0.1)/1.21 = 100 + 100 + 1000
        self.assertAlmostEqual(dcf_value_per_share(100.0, s, 2, 0.0, 1.0), 1200.0)

    def test_rejects_bad_inputs(self):
        ok = DcfScenario(0.05, 0.09, 0.02)
        with self.assertRaises(ValueError):
            dcf_value_per_share(100.0, DcfScenario(0.05, 0.02, 0.02), 5, 0.0, 1.0)
        with self.assertRaises(ValueError):
            dcf_value_per_share(-1.0, ok, 5, 0.0, 1.0)
        with self.assertRaises(ValueError):
            dcf_value_per_share(100.0, ok, 0, 0.0, 1.0)
        with self.assertRaises(ValueError):
            dcf_value_per_share(100.0, ok, 5, 0.0, 0.0)

    def test_range_ordered_and_assumptions_echoed(self):
        bear = DcfScenario(0.03, 0.11, 0.02)
        base = DcfScenario(0.06, 0.09, 0.025)
        bull = DcfScenario(0.09, 0.08, 0.03)
        r = dcf_range(100.0, bear, base, bull, 5, 50.0, 10.0)
        self.assertLess(r.low, r.base)
        self.assertLess(r.base, r.high)
        self.assertEqual(r.assumptions["years"], 5)
        self.assertIn("discount=0.0900", str(r.assumptions["base"]))

    def test_misordered_scenarios_rejected(self):
        a = DcfScenario(0.03, 0.11, 0.02)
        b = DcfScenario(0.09, 0.08, 0.03)
        with self.assertRaises(ValueError):
            dcf_range(100.0, b, a, a, 5, 0.0, 1.0)

    def test_deterministic(self):
        s = DcfScenario(0.06, 0.09, 0.025)
        self.assertEqual(
            dcf_value_per_share(100.0, s, 5, 0.0, 1.0),
            dcf_value_per_share(100.0, s, 5, 0.0, 1.0),
        )


class PeHistoryTests(unittest.TestCase):
    PES = tuple(float(x) for x in range(10, 22))  # 10..21, 12 points

    def test_percentile_interpolation(self):
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 50), 2.5)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 0), 1.0)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 100), 4.0)

    def test_range(self):
        r = pe_history_range(2.0, list(self.PES))
        self.assertAlmostEqual(r.base, 2.0 * 15.5)
        self.assertLess(r.low, r.base)
        self.assertLess(r.base, r.high)
        self.assertEqual(r.assumptions["observations"], 12)

    def test_drops_nonpositive_and_enforces_minimum(self):
        with self.assertRaises(ValueError):
            pe_history_range(2.0, list(self.PES[:11]) + [-5.0, float("nan")])

    def test_requires_positive_eps(self):
        with self.assertRaises(ValueError):
            pe_history_range(0.0, list(self.PES))


class ConsensusTests(unittest.TestCase):
    def test_range(self):
        r = consensus_range(100.0, 120.0, 150.0, analyst_count=20)
        self.assertEqual((r.low, r.base, r.high), (100.0, 120.0, 150.0))

    def test_too_few_analysts(self):
        with self.assertRaises(ValueError):
            consensus_range(100.0, 120.0, 150.0, analyst_count=3)


class CombineTests(unittest.TestCase):
    def test_median_of_methods(self):
        ms = [
            MethodRange("a", 80.0, 100.0, 120.0),
            MethodRange("b", 90.0, 110.0, 130.0),
            MethodRange("c", 200.0, 300.0, 400.0),
        ]
        fv = combine("XYZ", 105.0, ms)
        self.assertEqual((fv.low, fv.base, fv.high), (90.0, 110.0, 130.0))
        self.assertEqual(fv.position, "within-range")
        self.assertAlmostEqual(fv.base_vs_price, 110.0 / 105.0 - 1.0)

    def test_position_flags(self):
        ms = [MethodRange("a", 80.0, 100.0, 120.0), MethodRange("b", 80.0, 100.0, 120.0)]
        self.assertEqual(combine("X", 70.0, ms).position, "below-range")
        self.assertEqual(combine("X", 130.0, ms).position, "above-range")

    def test_needs_two_methods_and_valid_price(self):
        one = [MethodRange("a", 1.0, 2.0, 3.0)]
        with self.assertRaises(ValueError):
            combine("X", 2.0, one)
        with self.assertRaises(ValueError):
            combine("X", 0.0, one * 2)

    def test_method_range_validates_order(self):
        with self.assertRaises(ValueError):
            MethodRange("a", 3.0, 2.0, 1.0)


if __name__ == "__main__":
    unittest.main()
