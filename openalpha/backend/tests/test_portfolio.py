"""Portfolio config validation, P/L maths and the signal rules."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from app.errors import PortfolioError
from app.portfolio import (
    PortfolioConfig,
    SignalConfig,
    build_snapshot,
    compute_signal,
    load_portfolio,
    rolling_pl,
)

CFG = SignalConfig()


def lot(
    symbol: str, shares: float, price: float, cur: str = "USD", **kw: object
) -> dict[str, object]:
    return {"symbol": symbol, "shares": shares, "entry_price": price, "entry_date": "2026-01-02",
            "currency": cur, **kw}


def pf(accounts: dict[str, list[dict[str, object]]]) -> PortfolioConfig:
    return PortfolioConfig.model_validate({"accounts": accounts})


def uptrend_with_dip() -> pd.Series:
    base = np.linspace(100.0, 160.0, 300)
    base[-1] *= 0.95
    return pd.Series(base)


def downtrend() -> pd.Series:
    return pd.Series(np.linspace(160.0, 100.0, 300))


class ConfigTests(unittest.TestCase):
    def test_valid_and_normalised(self):
        p = pf({"TFSA": [lot("nvda", 2, 100.0)]})
        self.assertEqual(p.symbols(), ["NVDA"])

    def test_rejects_bad_input(self):
        bad = [
            {"TFSA": [lot("../x", 1, 1.0)]},
            {"TFSA": [lot("NVDA", 0, 1.0)]},
            {"TFSA": [lot("NVDA", 1, -1.0)]},
            {"TFSA": [lot("NVDA", 1, 1.0, cur="EUR")]},
            {"tfsa; drop": [lot("NVDA", 1, 1.0)]},
            {},
        ]
        for accounts in bad:
            with self.assertRaises(Exception):  # noqa: B017  (pydantic ValidationError)
                pf(accounts)

    def test_load_errors_are_portfolio_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PortfolioError):
                load_portfolio(Path(tmp) / "missing.json")
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json")
            with self.assertRaises(PortfolioError):
                load_portfolio(bad)
            ok = Path(tmp) / "ok.json"
            ok.write_text(json.dumps({"accounts": {"TFSA": [lot("NVDA", 1, 10.0)]}}))
            self.assertEqual(load_portfolio(ok).symbols(), ["NVDA"])


class SnapshotTests(unittest.TestCase):
    def test_pl_math_multi_lot_and_fx(self):
        closes = {"NVDA": pd.Series([100.0, 110.0, 120.0]), "SHOP.TO": pd.Series([50.0, 55.0])}
        p = pf({
            "TFSA": [lot("NVDA", 2, 100.0), lot("NVDA", 2, 140.0)],       # avg entry 120
            "FHSA": [lot("SHOP.TO", 10, 40.0, cur="CAD")],
        })
        snap = build_snapshot(p, closes, usdcad=1.5)
        nv = next(ln for ln in snap.lines() if ln.symbol == "NVDA")
        self.assertAlmostEqual(nv.avg_entry, 120.0)
        self.assertAlmostEqual(nv.cost, 480.0)
        self.assertAlmostEqual(nv.total_pl or 0, 0.0)                     # 4 x 120 - 480
        self.assertAlmostEqual(nv.day_pl or 0, 40.0)                      # 4 x (120 - 110)
        sh = next(ln for ln in snap.lines() if ln.symbol == "SHOP.TO")
        self.assertAlmostEqual(sh.total_pl or 0, 150.0)
        self.assertAlmostEqual(sh.total_pl_pct or 0, 150.0 / 400.0)
        tfsa = next(a for a in snap.accounts if a.account == "TFSA")
        self.assertAlmostEqual(tfsa.market_value_base, 480.0 * 1.5)       # USD converted
        self.assertAlmostEqual(snap.total_pl_base, 0.0 * 1.5 + 150.0)
        self.assertAlmostEqual(snap.day_pl_base, 40.0 * 1.5 + 50.0)       # 10 x (55 - 50)

    def test_missing_fx_and_price_are_excluded_not_assumed(self):
        closes = {"NVDA": pd.Series([100.0, 110.0])}
        p = pf({"TFSA": [lot("NVDA", 1, 100.0), lot("ON", 1, 50.0)]})
        snap = build_snapshot(p, closes, usdcad=None)
        a = snap.accounts[0]
        self.assertEqual(set(a.excluded), {"NVDA", "ON"})
        self.assertEqual(a.market_value_base, 0.0)
        flags = {ln.symbol: ln.flags for ln in snap.lines()}
        self.assertTrue(any("USDCAD" in f for f in flags["NVDA"]))
        self.assertIn("no price data", flags["ON"])

    def test_mixed_currency_lots_rejected(self):
        p = pf({"TFSA": [lot("NVDA", 1, 1.0), lot("NVDA", 1, 1.0, cur="CAD")]})
        with self.assertRaises(PortfolioError):
            build_snapshot(p, {"NVDA": pd.Series([1.0, 2.0])}, 1.4)

    def test_anything_invested_is_included_etf_gets_totals_and_signal(self):
        closes = {"XQQ": pd.Series(np.linspace(100.0, 160.0, 300))}
        p = pf({"TFSA": [lot("XQQ", 1, 100.0, cur="CAD", instrument="etf")]})
        snap = build_snapshot(p, closes, 1.4)
        line = snap.lines()[0]
        self.assertNotEqual(line.signal.action, "N/A")
        self.assertEqual(line.flags, ())
        self.assertAlmostEqual(snap.market_value_base, 160.0)
        self.assertAlmostEqual(snap.total_pl_base, 60.0)

    def test_rolling_pl(self):
        s = rolling_pl(pd.Series([10.0, 12.0, 9.0]), shares=2, entry_price=10.0)
        self.assertEqual(list(s), [0.0, 4.0, -2.0])
        with self.assertRaises(ValueError):
            rolling_pl(pd.Series([1.0]), 0, 1.0)


class SignalTests(unittest.TestCase):
    def test_buy_on_pullback_in_uptrend(self):
        sig = compute_signal(uptrend_with_dip(), 100.0, CFG, sharia_ok=True)
        self.assertEqual(sig.action, "BUY", sig.reasons)
        self.assertIsNotNone(sig.vol_annual)

    def test_sharia_gate_blocks_buy(self):
        sig = compute_signal(uptrend_with_dip(), 100.0, CFG, sharia_ok=False)
        self.assertEqual(sig.action, "HOLD")
        self.assertIn("Sharia", sig.reasons[0])

    def test_downtrend_warns_but_does_not_sell_by_default(self):
        sig = compute_signal(downtrend(), None, CFG, sharia_ok=True)
        self.assertEqual(sig.action, "HOLD")
        self.assertTrue(sig.reasons[0].startswith("WARNING"))

    def test_downtrend_sells_only_when_opted_in(self):
        cfg = SignalConfig(sell_on_downtrend=True)
        self.assertEqual(compute_signal(downtrend(), None, cfg, sharia_ok=True).action, "SELL")

    def test_stop_loss_is_off_by_default_and_opt_in(self):
        s = uptrend_with_dip()
        entry = float(s.iloc[-1]) / 0.8                       # price is 20% under entry
        held = compute_signal(s, entry, CFG, sharia_ok=True)
        self.assertNotEqual(held.action, "SELL")
        self.assertAlmostEqual(held.vs_entry or 0.0, -0.2)
        self.assertAlmostEqual(held.recovery_needed or 0.0, 0.25)   # 1 / 0.8 - 1
        self.assertTrue(any("needs +25.0%" in r for r in held.reasons))
        armed = SignalConfig(stop_loss_pct=0.15)
        sig = compute_signal(s, entry, armed, sharia_ok=True)
        self.assertEqual(sig.action, "SELL")
        self.assertIn("stop loss", sig.reasons[0])

    def test_no_recovery_figure_when_above_entry(self):
        sig = compute_signal(uptrend_with_dip(), 50.0, CFG, sharia_ok=True)
        self.assertIsNone(sig.recovery_needed)
        self.assertGreater(sig.vs_entry or 0.0, 0.0)

    def test_hold_in_calm_uptrend_and_stretch_note(self):
        calm = pd.Series(np.linspace(100.0, 160.0, 300))
        self.assertEqual(compute_signal(calm, 100.0, CFG, sharia_ok=True).action, "HOLD")
        spike = calm.copy()
        spike.iloc[-1] *= 1.06
        sig = compute_signal(spike, 100.0, CFG, sharia_ok=True)
        self.assertEqual(sig.action, "HOLD")
        self.assertTrue(any("stretched" in r for r in sig.reasons))

    def test_short_history_is_not_guessed(self):
        sig = compute_signal(pd.Series(np.linspace(1.0, 2.0, 50)), 1.0, CFG, sharia_ok=True)
        self.assertEqual(sig.action, "N/A")

    def test_flat_series_has_no_zscore(self):
        sig = compute_signal(pd.Series([10.0] * 300), 10.0, CFG, sharia_ok=True)
        self.assertEqual(sig.action, "HOLD")
        self.assertIsNone(sig.zscore)


if __name__ == "__main__":
    unittest.main()
