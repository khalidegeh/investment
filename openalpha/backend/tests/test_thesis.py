"""Thesis check: growth trend only, price ignored, thin history returns UNKNOWN."""
import unittest

import numpy as np
import pandas as pd

from app.portfolio import ThesisConfig, assess_thesis, fund_result
from app.portfolio.thesis import BROKEN, INTACT, NOT_APPLICABLE, UNKNOWN, WATCH


def income(revenues: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        pd.date_range("2018-12-31", periods=len(revenues), freq="YE"), name="period_end"
    )
    return pd.DataFrame({"revenue": revenues}, index=idx)


class ThesisTests(unittest.TestCase):
    def test_steady_growth_is_intact(self):
        r = assess_thesis(income([100.0, 120.0, 150.0, 190.0]))     # +20%, +25%, +26.7%
        self.assertEqual(r.status, INTACT)
        self.assertAlmostEqual(r.revenue_growth[-1], 190.0 / 150.0 - 1.0)
        self.assertEqual(r.as_of, "2021-12-31")

    def test_two_flat_or_down_periods_is_broken(self):
        r = assess_thesis(income([100.0, 120.0, 118.0, 110.0]))
        self.assertEqual(r.status, BROKEN)
        self.assertIn("2 straight periods", r.reasons[0])

    def test_single_decline_is_watch_not_broken(self):
        r = assess_thesis(income([100.0, 120.0, 150.0, 140.0]))
        self.assertEqual(r.status, WATCH)
        self.assertTrue(any("non-positive" in x for x in r.reasons))

    def test_two_slowdowns_in_a_row_is_watch(self):
        # growth 50%, 30%, 10%: still positive, but slowing twice
        r = assess_thesis(income([100.0, 150.0, 195.0, 214.5]))
        self.assertEqual(r.status, WATCH)
        self.assertTrue(any("slowed 2 periods" in x for x in r.reasons))

    def test_one_slowdown_alone_is_intact(self):
        r = assess_thesis(income([100.0, 150.0, 195.0]))             # +50%, +30%
        self.assertEqual(r.status, INTACT)

    def test_peer_gap_triggers_watch(self):
        base = income([100.0, 120.0, 150.0])                          # latest +25%
        self.assertEqual(assess_thesis(base, peer_growth=0.27).status, INTACT)   # within 5 points
        r = assess_thesis(base, peer_growth=0.40)
        self.assertEqual(r.status, WATCH)
        self.assertTrue(any("vs peers" in x for x in r.reasons))

    def test_short_or_missing_history_is_unknown(self):
        for df in (income([100.0, 120.0]), income([100.0]), pd.DataFrame({"revenue": []}),
                   pd.DataFrame({"other": [1.0]})):
            self.assertEqual(assess_thesis(df).status, UNKNOWN)

    def test_gaps_and_zero_base_do_not_invent_growth(self):
        # 0 -> 50 has a zero base (no reading); 50 -> NaN -> 80 spans a missing year
        # (no reading); only 80 -> 100 is a true consecutive-year reading.
        r = assess_thesis(income([0.0, 50.0, np.nan, 80.0, 100.0]))
        self.assertEqual(r.revenue_growth, (0.25,))
        self.assertEqual(r.status, UNKNOWN)             # one reading is below the minimum of two
        self.assertEqual(r.as_of, "2022-12-31")

    def test_config_is_respected(self):
        cfg = ThesisConfig(shrink_periods=1)
        self.assertEqual(assess_thesis(income([100.0, 120.0, 118.0]), cfg).status, BROKEN)

    def test_fund_is_not_applicable(self):
        r = fund_result()
        self.assertEqual(r.status, NOT_APPLICABLE)


if __name__ == "__main__":
    unittest.main()
