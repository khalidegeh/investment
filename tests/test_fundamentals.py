"""Tests for the deterministic fundamentals advisor.

The scorecard is pure: fixed inputs must always produce the same verdict.
Providers are stubbed -- no network in tests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tradingsystem.fundamentals import advisor, providers, scorecard


class StubConfig:
    def __init__(self, raw):
        self._raw = raw

    def get(self, *keys, default=None):
        node = self._raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node


def base_config(**over):
    raw = {
        "halal_exclusions": [],
        "fundamentals": {"min_coverage": 5, "buy_at": 65.0, "hold_at": 40.0},
    }
    raw.update(over)
    return StubConfig(raw)


STRONG = {
    "pe_ttm": 18.0,
    "peg_ttm": 0.9,
    "roe_ttm": 0.30,
    "fcf_margin_ttm": 0.25,
    "revenue_growth_yoy": 0.20,
    "eps_growth_yoy": 0.22,
    "debt_to_equity": 0.3,
    "current_ratio": 2.0,
}

WEAK = {
    "pe_ttm": 55.0,
    "peg_ttm": 3.0,
    "roe_ttm": 0.02,
    "fcf_margin_ttm": -0.05,
    "revenue_growth_yoy": -0.03,
    "eps_growth_yoy": -0.10,
    "debt_to_equity": 3.0,
    "current_ratio": 0.5,
}


class StubProvider(providers.BaseProvider):
    def __init__(self, name, payload):
        self.name = name
        self._payload = payload

    def fetch(self, symbol):
        return self._payload


def _fund(symbol, metrics, provider="stub"):
    return providers.Fundamentals(
        symbol=symbol, provider=provider, metrics=metrics, price=100.0, currency="USD"
    )


# --- scorecard bands ---


def test_pe_bands():
    spec = next(s for s in scorecard.DEFAULT_METRICS if s.name == "pe_ttm")
    assert scorecard.score_metric(spec, 18.0) == 2
    assert scorecard.score_metric(spec, 20.0) == 2  # boundary inclusive
    assert scorecard.score_metric(spec, 25.0) == 1
    assert scorecard.score_metric(spec, 35.0) == 0
    assert scorecard.score_metric(spec, 45.0) == -1
    assert scorecard.score_metric(spec, -5.0) == -1  # negative earnings: worst band


def test_higher_is_better_bands():
    spec = next(s for s in scorecard.DEFAULT_METRICS if s.name == "roe_ttm")
    assert scorecard.score_metric(spec, 0.25) == 2
    assert scorecard.score_metric(spec, 0.20) == 2
    assert scorecard.score_metric(spec, 0.15) == 1
    assert scorecard.score_metric(spec, 0.07) == 0
    assert scorecard.score_metric(spec, 0.01) == -1


def test_missing_metric_returns_none():
    spec = next(s for s in scorecard.DEFAULT_METRICS if s.name == "pe_ttm")
    assert scorecard.score_metric(spec, None) is None


# --- verdicts ---


def test_strong_fundamentals_is_buy():
    r = scorecard.score(STRONG)
    assert r["verdict"] == "buy"
    assert r["score"] >= 65.0
    assert r["coverage"] == 8


def test_weak_fundamentals_is_sell():
    r = scorecard.score(WEAK)
    assert r["verdict"] == "sell"
    assert r["score"] < 40.0


def test_mixed_fundamentals_hold_and_renormalize():
    metrics = {
        "pe_ttm": 35.0,  # 0 pts
        "peg_ttm": 1.8,  # 0 pts
        "roe_ttm": 0.15,  # 1 pt
        "fcf_margin_ttm": 0.10,  # 1 pt
        "revenue_growth_yoy": 0.08,  # 1 pt
        "eps_growth_yoy": 0.06,  # 1 pt
        "debt_to_equity": 1.5,  # 0 pts
        "current_ratio": 1.2,  # 1 pt
    }
    r = scorecard.score(metrics)
    assert r["verdict"] == "hold"
    assert 40.0 <= r["score"] < 65.0
    # same verdict if two metrics are simply missing (renormalized weights)
    partial = {k: v for k, v in metrics.items() if k not in ("pe_ttm", "peg_ttm")}
    r2 = scorecard.score(partial)
    assert r2["coverage"] == 6
    assert r2["verdict"] == r["verdict"]


def test_below_min_coverage_never_guesses():
    metrics = {k: STRONG[k] for k in ("pe_ttm", "roe_ttm", "revenue_growth_yoy", "debt_to_equity")}
    r = scorecard.score(metrics, min_coverage=5)
    assert r["verdict"] == "insufficient-data"
    assert r["score"] is None
    assert r["coverage"] == 4


def test_score_is_deterministic():
    assert scorecard.score(STRONG) == scorecard.score(STRONG)


def test_specs_from_config_override():
    raw = [
        {
            "name": "pe_ttm",
            "weight": 100.0,
            "higher_is_better": False,
            "bands": [[10.0, 2], [20.0, 1]],
        }
    ]
    specs = scorecard.specs_from_config(raw)
    assert len(specs) == 1
    r = scorecard.score({"pe_ttm": 5.0}, specs=specs, min_coverage=1)
    assert r["verdict"] == "buy" and r["score"] == 100.0


# --- advisor behavior ---


def test_halal_exclusion_never_scores():
    cfg = base_config(halal_exclusions=["AAPL"])
    a = advisor.advise("aapl", cfg, provider_list=[])
    assert a.verdict == "excluded"
    assert a.score is None


def test_no_provider_means_insufficient_not_guess():
    cfg = base_config()
    a = advisor.advise("AAPL", cfg, provider_list=[])
    assert a.verdict == "insufficient-data"
    assert a.score is None


def test_fallback_chain_uses_first_with_coverage():
    cfg = base_config()
    dead = StubProvider("dead", None)
    thin = StubProvider("thin", _fund("AAPL", {"pe_ttm": 18.0}, provider="thin"))
    full = StubProvider("full", _fund("AAPL", STRONG, provider="full"))
    a = advisor.advise("AAPL", cfg, provider_list=[dead, thin, full])
    assert a.provider == "full"
    assert a.verdict == "buy"


def test_below_coverage_warns_but_still_scores_best_effort():
    cfg = base_config()
    thin = StubProvider("thin", _fund("AAPL", dict(list(STRONG.items())[:4]), provider="thin"))
    a = advisor.advise("AAPL", cfg, provider_list=[thin])
    assert a.verdict == "insufficient-data"
    assert any("below minimum coverage" in w for w in a.warnings)


def test_broken_provider_does_not_break_chain():
    class Boom(providers.BaseProvider):
        name = "boom"

        def fetch(self, symbol):
            raise RuntimeError("boom")

    cfg = base_config()
    good = StubProvider("good", _fund("AAPL", STRONG, provider="good"))
    a = advisor.advise("AAPL", cfg, provider_list=[Boom(), good])
    assert a.provider == "good" and a.verdict == "buy"


# --- Finnhub payload mapping ---


def test_finnhub_key_mapping_with_fixture():
    class FakeResp:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    fixture_metric = {
        # key casing verified against a live /stock/metric response 2026-10-15
        "peTTM": 28.5,
        "pegTTM": 1.2,
        "roeTTM": 38.0,  # percent units from Finnhub
        "revenueGrowthTTMYoy": 12.0,
        "epsGrowthTTMYoy": 15.0,
        "currentRatioAnnual": 1.1,
    }
    fixture_quote = {"c": 340.0}

    import tradingsystem.fundamentals.providers as pv

    calls = []

    def fake_get(path, params):
        calls.append(path)
        if path == "/stock/metric":
            return {"metric": fixture_metric}
        return dict(fixture_quote)

    p = pv.FinnhubProvider("dummy-key", throttle_s=0)
    p._get = fake_get
    fund = p.fetch("AAPL")
    assert fund is not None
    assert fund.metrics["pe_ttm"] == 28.5
    assert fund.metrics["peg_ttm"] == 1.2
    assert fund.metrics["roe_ttm"] == 0.38  # percent -> decimal
    assert fund.metrics["revenue_growth_yoy"] == 0.12
    assert fund.metrics["fcf_margin_ttm"] is None  # key absent: missing, not zero
    assert fund.price == 340.0
    assert fund.coverage() == 6


def test_finnhub_connector_provider_uses_shared_mapping(tmp_path):
    import tradingsystem.fundamentals.providers as pv

    helper = tmp_path / "finnhub_api.py"
    helper.write_text("# stub")
    p = pv.FinnhubConnectorProvider(helper=helper, throttle_s=0)
    p._call = lambda path, params: (
        {"metric": {"peTTM": 18.0, "roeTTM": 25.0}} if path == "/stock/metric"
        else {"c": 100.0}
    )
    fund = p.fetch("AAPL")
    assert fund is not None
    assert fund.provider == "finnhub"
    assert fund.metrics["pe_ttm"] == 18.0
    assert fund.metrics["roe_ttm"] == 0.25
    assert fund.price == 100.0


def test_finnhub_connector_provider_degrades_on_helper_failure(tmp_path):
    import tradingsystem.fundamentals.providers as pv

    helper = tmp_path / "finnhub_api.py"
    helper.write_text("# stub")
    p = pv.FinnhubConnectorProvider(helper=helper, throttle_s=0)

    def boom(path, params):
        raise RuntimeError("helper exploded")

    p._call = boom
    assert p.fetch("AAPL") is None


def test_finnhub_connector_provider_requires_helper():
    import tradingsystem.fundamentals.providers as pv

    try:
        pv.FinnhubConnectorProvider(helper="/nonexistent/finnhub_api.py")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")
