"""Tests for portfolio analytics. Hand-checked values, no network."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tradingsystem.analytics import (
    annualized_volatility,
    benchmark_stats,
    concentration,
    daily_returns,
    max_drawdown,
)


class ReturnTests(unittest.TestCase):
    def test_daily_returns(self):
        r = daily_returns([100.0, 110.0, 99.0])
        self.assertAlmostEqual(r[0], 0.10)
        self.assertAlmostEqual(r[1], -0.10)

    def test_bad_prices(self):
        with self.assertRaises(ValueError):
            daily_returns([100.0])
        with self.assertRaises(ValueError):
            daily_returns([100.0, 0.0])
        with self.assertRaises(ValueError):
            daily_returns([100.0, float("nan")])

    def test_volatility(self):
        # sample stdev of [0.01, -0.01] = 0.0141421...
        v = annualized_volatility([0.01, -0.01])
        self.assertAlmostEqual(v, math.sqrt(2) * 0.01 * math.sqrt(252))
        self.assertEqual(annualized_volatility([0.01, 0.01]), 0.0)
        with self.assertRaises(ValueError):
            annualized_volatility([0.01])

    def test_max_drawdown(self):
        self.assertAlmostEqual(max_drawdown([100.0, 120.0, 90.0, 130.0]), 90.0 / 120.0 - 1.0)
        self.assertEqual(max_drawdown([1.0, 2.0, 3.0]), 0.0)
        with self.assertRaises(ValueError):
            max_drawdown([])


class ConcentrationTests(unittest.TestCase):
    def test_equal_weights(self):
        c = concentration({"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0})
        self.assertAlmostEqual(c.hhi, 0.25)
        self.assertAlmostEqual(c.effective_names, 4.0)
        self.assertAlmostEqual(c.top1, 0.25)
        self.assertAlmostEqual(c.top3, 0.75)

    def test_concentrated(self):
        c = concentration({"A": 900.0, "B": 100.0})
        self.assertAlmostEqual(c.top1, 0.9)
        self.assertAlmostEqual(c.hhi, 0.82)
        self.assertAlmostEqual(sum(c.weights.values()), 1.0)

    def test_rejects_bad_input(self):
        for bad in ({}, {"A": 0.0}, {"A": -1.0, "B": 2.0}, {"A": float("inf")}):
            with self.assertRaises(ValueError):
                concentration(bad)


class BenchmarkTests(unittest.TestCase):
    def test_identical_series(self):
        r = [0.01, -0.02, 0.015, 0.0, 0.005]
        s = benchmark_stats(r, r)
        self.assertAlmostEqual(s.beta, 1.0)
        self.assertAlmostEqual(s.correlation, 1.0)
        self.assertAlmostEqual(s.excess_return, 0.0)

    def test_double_leverage_beta(self):
        b = [0.01, -0.02, 0.015, 0.0, 0.005]
        p = [2 * x for x in b]
        s = benchmark_stats(p, b)
        self.assertAlmostEqual(s.beta, 2.0)
        self.assertAlmostEqual(s.correlation, 1.0)

    def test_negative_correlation_hedge(self):
        b = [0.01, -0.02, 0.015, 0.0, 0.005]
        s = benchmark_stats([-x for x in b], b)
        self.assertAlmostEqual(s.beta, -1.0)
        self.assertAlmostEqual(s.correlation, -1.0)

    def test_cumulative_excess(self):
        s = benchmark_stats([0.10, 0.20], [0.0, 0.10])
        self.assertAlmostEqual(s.portfolio_return, 0.32)
        self.assertAlmostEqual(s.benchmark_return, 0.10)
        self.assertAlmostEqual(s.excess_return, 0.22)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            benchmark_stats([0.01, 0.02], [0.01])
        with self.assertRaises(ValueError):
            benchmark_stats([0.01], [0.01])
        with self.assertRaises(ValueError):
            benchmark_stats([0.01, 0.02], [0.01, 0.01])


if __name__ == "__main__":
    unittest.main()
