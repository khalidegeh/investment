"""Earnings-approach signal: enter/exit windows, trend filter, halal exclusion."""
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingsystem.data.earnings import CsvEarningsCalendar
from tradingsystem.strategy.earnings_approach import EarningsApproachSignal


class FixedCalendar(CsvEarningsCalendar):
    def __init__(self, dates):
        self._dates = dates

    def announcement_dates(self, symbol):
        return self._dates


def _history(n=60, end="2024-01-31", trend="up"):
    idx = pd.date_range(end=end, periods=n, freq="D", tz="UTC")
    if trend == "up":
        closes = [100 + i * 0.5 for i in range(n)]  # above its SMA20
    else:
        closes = [100 - i * 0.5 for i in range(n)]  # below its SMA20
    return pd.DataFrame({"timestamp": idx, "open": closes,
                         "high": closes, "low": closes, "close": closes,
                         "volume": [1000] * n})


class TestEarningsApproach(unittest.TestCase):
    def _strat(self, **kw):
        cal = FixedCalendar([date(2024, 2, 20)])  # earnings 20 days after 2024-01-31
        kw.setdefault("calendar", cal)
        return EarningsApproachSignal(**kw)

    def test_enters_in_window_above_trend(self):
        s = self._strat()
        sigs = s.on_bar("AAA", _history(), position=0)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].side, "BUY")
        self.assertAlmostEqual(sigs[0].target_weight, 0.10)

    def test_no_enter_when_flat_and_far_out(self):
        s = self._strat(entry_days_before=10)  # 20d out > 10d window
        self.assertEqual(s.on_bar("AAA", _history(), position=0), [])

    def test_no_enter_below_trend(self):
        s = self._strat()
        self.assertEqual(s.on_bar("AAA", _history(trend="down"), position=0), [])

    def test_no_double_enter_when_holding(self):
        s = self._strat()
        self.assertEqual(s.on_bar("AAA", _history(), position=100), [])

    def test_exits_before_report(self):
        # 1 day to earnings, holding -> flatten
        cal = FixedCalendar([date(2024, 2, 1)])
        s = EarningsApproachSignal(calendar=cal, exit_days_before=1)
        sigs = s.on_bar("AAA", _history(), position=100)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].side, "SELL")
        self.assertAlmostEqual(sigs[0].target_weight, 0.0)

    def test_exits_friday_when_earnings_monday(self):
        # Monday-earnings hole: with a pure calendar-day rule, Friday's bar
        # (dte=3) never fired the exit and the position flattened on the
        # report day itself. Fixed: flatten on the last trading day before.
        cal = FixedCalendar([date(2024, 2, 5)])  # Monday
        s = EarningsApproachSignal(calendar=cal, exit_days_before=1)
        friday = _history(end="2024-02-02")  # Friday, 3 calendar days out
        sigs = s.on_bar("AAA", friday, position=100)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].side, "SELL")
        self.assertAlmostEqual(sigs[0].target_weight, 0.0)

    def test_no_signal_without_earnings_date(self):
        s = EarningsApproachSignal(calendar=FixedCalendar([]))
        self.assertEqual(s.on_bar("AAA", _history(), position=0), [])

    def test_halal_exclusion_blocks_everything(self):
        s = self._strat(halal_exclusions={"AAA"})
        self.assertEqual(s.on_bar("AAA", _history(), position=0), [])
        self.assertEqual(s.on_bar("AAA", _history(), position=100), [])

    def test_csv_calendar_parses(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "AAA.csv").write_text("date\n2024-02-20\n2024-05-21\n")
            cal = CsvEarningsCalendar(d)
            self.assertEqual(cal.days_to_earnings("AAA", date(2024, 1, 31)), 20)
            self.assertEqual(cal.days_to_earnings("BBB", date(2024, 1, 31)), None)


if __name__ == "__main__":
    unittest.main()
