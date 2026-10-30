"""Alert rules from the plan, the origin guard, security headers and the page itself."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from test_api_news import FRIDAY_MORNING, client

from app.errors import DataError, PortfolioError
from app.market import load_state, rules_from_portfolio, save_state
from app.portfolio import PortfolioConfig, build_snapshot

LOCAL = {"X-OpenAlpha": "1"}
STATIC = Path(__file__).parent.parent / "app" / "static"


def lot(
    symbol: str, shares: float, price: float, cur: str = "USD", **kw: object
) -> dict[str, object]:
    return {"symbol": symbol, "shares": shares, "entry_price": price, "entry_date": "2026-01-02",
            "currency": cur, **kw}


PLAN: dict[str, object] = {
    "accounts": {
        "TFSA": [
            lot("XQQ", 2, 100.0, "CAD", instrument="etf", never_sell_below_entry=True),
            lot("XQQ", 2, 120.0, "CAD", instrument="etf", never_sell_below_entry=True),
            lot("ON", 3, 50.0, take_profit_pct=0.0),
        ],
        "FHSA": [lot("SHOP.TO", 1, 8.0, "CAD")],
    },
    "needed_by": {"FHSA": "2029-10-30"},
}


class RulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = {r.id: r for r in rules_from_portfolio(PortfolioConfig.model_validate(PLAN))}

    def test_every_holding_gets_a_move_alert(self):
        for sym in ("xqq", "on", "shop.to"):
            r = self.rules[f"{sym}-move"]
            self.assertEqual((r.kind, r.level), ("pct_move", 5.0))

    def test_break_even_uses_the_weighted_average_entry(self):
        r = self.rules["tfsa-xqq-breakeven"]
        self.assertEqual((r.kind, r.symbol), ("above", "XQQ"))
        self.assertAlmostEqual(r.level, (2 * 100.0 + 2 * 120.0) / 4)        # 110

    def test_break_even_alert_only_while_under_entry(self):
        pf = PortfolioConfig.model_validate(PLAN)
        under = {r.id for r in rules_from_portfolio(pf, prices={"XQQ": 100.0})}
        above = {r.id for r in rules_from_portfolio(pf, prices={"XQQ": 115.0})}
        self.assertIn("tfsa-xqq-breakeven", under)               # 100 < 110 average entry
        self.assertNotIn("tfsa-xqq-breakeven", above)            # already above: nothing to regain
        self.assertIn("tfsa-on-target", above)                   # take-profit rules always stay

    def test_take_profit_level_is_entry_times_one_plus_target(self):
        r = self.rules["tfsa-on-target"]
        self.assertAlmostEqual(r.level, 50.0)                                # target 0 = break-even
        pf = PortfolioConfig.model_validate(
            {"accounts": {"TFSA": [lot("ON", 1, 50.0, take_profit_pct=0.2)]}})
        (target,) = [x for x in rules_from_portfolio(pf) if x.id.endswith("-target")]
        self.assertAlmostEqual(target.level, 60.0)

    def test_no_rule_for_holdings_without_a_plan(self):
        self.assertNotIn("fhsa-shop.to-breakeven", self.rules)
        self.assertNotIn("fhsa-shop.to-target", self.rules)

    def test_state_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s" / "state.json"
            self.assertEqual(load_state(p), {})
            save_state(p, {"a": "2026-10-30"})
            self.assertEqual(load_state(p), {"a": "2026-10-30"})
            self.assertEqual(list(p.parent.glob("*.tmp")), [])
            p.write_text("{not json")
            with self.assertRaises(DataError):
                load_state(p)
            p.write_text(json.dumps({"a": 5}))
            with self.assertRaises(DataError):
                load_state(p)


class NeededByTests(unittest.TestCase):
    def test_months_until_money_is_needed(self):
        pf = PortfolioConfig.model_validate(PLAN)
        snap = build_snapshot(pf, {}, 1.4, today=date(2026, 10, 30))
        fhsa = next(a for a in snap.accounts if a.account == "FHSA")
        tfsa = next(a for a in snap.accounts if a.account == "TFSA")
        self.assertEqual((fhsa.needed_by, fhsa.months_to_need), (date(2029, 10, 30), 36))
        self.assertIsNone(tfsa.months_to_need)
        late = build_snapshot(pf, {}, 1.4, today=date(2030, 1, 15))
        late_fhsa = next(a for a in late.accounts if a.account == "FHSA")
        self.assertLess(late_fhsa.months_to_need or 0, 0)

    def test_unknown_account_is_rejected(self):
        with self.assertRaises(Exception):  # noqa: B017  (pydantic ValidationError)
            PortfolioConfig.model_validate({**PLAN, "needed_by": {"RRSP": "2030-01-01"}})
        self.assertTrue(PortfolioError)


class AlertApiTests(unittest.TestCase):
    def test_check_requires_the_local_header(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING, portfolio=PLAN) as c:
            self.assertEqual(c.post("/api/alerts/check").status_code, 403)
            wrong = c.post("/api/alerts/check", headers={"X-OpenAlpha": "0"})
            self.assertEqual(wrong.status_code, 403)

    def test_rings_once_and_remembers(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING, portfolio=PLAN) as c:
            first = c.post("/api/alerts/check", headers=LOCAL).json()
            ids = {f["id"] for f in first["fired"]}
            # the mocked series ends at 11; XQQ break-even is 110 and ON's target is 50, so
            # nothing level-based fires, and the 5% move alerts fire on 11 vs 12 (-8.3%)
            self.assertEqual(ids, {"xqq-move", "on-move", "shop.to-move"})
            self.assertTrue((Path(tmp) / "alerts.json").exists())
            second = c.post("/api/alerts/check", headers=LOCAL).json()
            self.assertEqual(second["fired"], [])
            rules = {r["id"]: r for r in c.get("/api/alerts").json()["rules"]}
            self.assertEqual(rules["xqq-move"]["last_rang"], "2026-10-30")
            self.assertIsNone(rules["tfsa-xqq-breakeven"]["last_rang"])

    def test_missing_portfolio_is_409(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING) as c:
            self.assertEqual(c.get("/api/alerts").status_code, 409)


class PositionsNewsTests(unittest.TestCase):
    def test_news_is_attached_to_each_line(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING, portfolio=PLAN) as c:
            snap = c.get("/api/positions?news=true&news_days=30&news_limit=2").json()
            lines = {ln["symbol"]: ln for a in snap["accounts"] for ln in a["lines"]}
            self.assertEqual(set(lines), {"XQQ", "ON", "SHOP.TO"})
            for ln in lines.values():
                self.assertIn("news", ln)
                self.assertLessEqual(len(ln["news"]), 2)
            self.assertTrue(lines["ON"]["news"])                       # headlines for any symbol
            plain = c.get("/api/positions").json()
            self.assertNotIn("news", plain["accounts"][0]["lines"][0])

    def test_needed_by_reaches_the_api(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING, portfolio=PLAN) as c:
            fhsa = next(a for a in c.get("/api/positions").json()["accounts"]
                        if a["account"] == "FHSA")
            self.assertEqual((fhsa["needed_by"], fhsa["months_to_need"]), ("2029-10-30", 36))


class SecurityAndPageTests(unittest.TestCase):
    def test_host_header_must_be_local(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING) as c:
            self.assertEqual(c.get("/health").status_code, 200)
            self.assertEqual(c.get("/health", headers={"Host": "evil.example"}).status_code, 400)

    def test_headers_on_page_and_api(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING) as c:
            page = c.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("default-src 'none'", page.headers["content-security-policy"])
            self.assertEqual(page.headers["x-content-type-options"], "nosniff")
            self.assertIn("OpenAlpha", page.text)
            api = c.get("/api/market/clock")
            self.assertEqual(api.headers["cache-control"], "no-store")
            self.assertNotIn("content-security-policy", api.headers)

    def test_static_assets_are_served(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING) as c:
            js = c.get("/static/app.js")
            self.assertEqual(js.status_code, 200)
            self.assertIn("javascript", js.headers["content-type"])
            self.assertEqual(c.get("/static/app.css").status_code, 200)
            self.assertEqual(c.get("/static/../main.py").status_code, 404)

    def test_script_never_writes_untrusted_html(self):
        source = (STATIC / "app.js").read_text()
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                       "new Function"):
            self.assertNotIn(banned, source, banned)
        html = (STATIC / "index.html").read_text()
        self.assertNotIn("onclick", html)                 # no inline handlers (CSP forbids them)
        self.assertNotRegex(html, r"<script>(?!\s*</script>)")   # no inline scripts

    def test_cors_still_refuses_other_origins(self):
        with tempfile.TemporaryDirectory() as tmp, client(tmp, FRIDAY_MORNING) as c:
            r = c.options("/api/alerts/check", headers={
                "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-openalpha"})
            self.assertIsNone(r.headers.get("access-control-allow-origin"))
        _ = TestClient


if __name__ == "__main__":
    unittest.main()
