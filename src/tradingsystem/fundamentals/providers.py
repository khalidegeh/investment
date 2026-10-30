"""Fundamentals data providers.

One interface, three rungs: Finnhub (keyed, primary), yfinance (unofficial
fallback), CSV (manual backstop). A provider returns None when it cannot
serve a symbol; it never invents numbers. Transport failures degrade to
"provider unavailable" so the chain can fall through -- they never produce
a verdict on their own.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

STANDARD_METRICS = (
    "pe_ttm",
    "peg_ttm",
    "roe_ttm",
    "fcf_margin_ttm",
    "revenue_growth_yoy",
    "eps_growth_yoy",
    "debt_to_equity",
    "current_ratio",
)


@dataclass
class Fundamentals:
    symbol: str
    provider: str
    metrics: dict[str, float | None]
    price: float | None = None
    currency: str | None = None
    fetched_at: str = ""
    warnings: list[str] = field(default_factory=list)

    def coverage(self) -> int:
        return sum(1 for m in STANDARD_METRICS if self.metrics.get(m) is not None)


class BaseProvider:
    name = "base"

    def fetch(self, symbol: str) -> Fundamentals | None:
        raise NotImplementedError


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _first_present(d: dict, keys: list[str]) -> Any:
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# standard metric -> candidate keys in Finnhub /stock/metric `metric` dict.
# Verified against a live response 2026-10-15. Note the casing: TTMYoy, not TTMYoY.
# Finnhub free does not provide an FCF margin key; it stays missing here and
# the provider chain (yfinance/CSV) can supply it.
FINNHUB_KEYS: dict[str, list[str]] = {
    "pe_ttm": ["peTTM", "peNormalizedAnnual"],
    "peg_ttm": ["pegTTM"],
    "roe_ttm": ["roeTTM", "roeRfy"],
    "fcf_margin_ttm": ["fcfMarginTTM"],
    "revenue_growth_yoy": ["revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy"],
    "eps_growth_yoy": ["epsGrowthTTMYoy", "epsGrowthQuarterlyYoy"],
    "debt_to_equity": [
        "totalDebtToEquityAnnual",
        "debtToEquityAnnual",
        "totalDebt/totalEquityAnnual",
    ],
    "current_ratio": ["currentRatioAnnual", "currentRatioTTM", "currentRatioQuarterly"],
}
# Finnhub expresses these as percentages (e.g. roeTTM=137.18 means 137.18%).
# Verified live 2026-10-15 against yfinance decimals for the same symbol.
PCT_METRICS = {"roe_ttm", "fcf_margin_ttm", "revenue_growth_yoy", "eps_growth_yoy"}


def finnhub_metrics_from_payload(metric: dict) -> dict[str, float | None]:
    """Map a Finnhub /stock/metric `metric` dict onto standard metrics."""
    metrics: dict[str, float | None] = {}
    for std, candidates in FINNHUB_KEYS.items():
        v = _to_float(_first_present(metric, candidates))
        if v is not None and std in PCT_METRICS:
            v = v / 100.0
        metrics[std] = v
    return metrics


class FinnhubProvider(BaseProvider):
    """Keyed primary. /stock/metric?metric=all returns the whole scorecard
    input in one call; /quote adds the current price."""

    name = "finnhub"

    def __init__(self, api_key: str, timeout: int = 15, throttle_s: float = 1.1):
        if not api_key:
            raise ValueError("Finnhub API key is required")
        self._key = api_key
        self._timeout = timeout
        self._throttle_s = throttle_s
        self._last_call = 0.0

    def _get(self, path: str, params: dict) -> Any:
        wait = self._throttle_s - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        resp = requests.get(
            f"https://finnhub.io/api/v1{path}",
            params={**params, "token": self._key},
            timeout=self._timeout,
        )
        self._last_call = time.monotonic()
        if resp.status_code == 401:
            raise PermissionError("Finnhub rejected the API key (401)")
        if resp.status_code == 429:
            raise RuntimeError("Finnhub rate limit hit (429)")
        resp.raise_for_status()
        return resp.json()

    def fetch(self, symbol: str) -> Fundamentals | None:
        try:
            metric_payload = self._get("/stock/metric", {"symbol": symbol, "metric": "all"})
            quote = self._get("/quote", {"symbol": symbol})
        except (requests.RequestException, PermissionError, RuntimeError):
            return None
        m = metric_payload.get("metric") if isinstance(metric_payload, dict) else None
        if not m:
            return None
        price = _to_float(quote.get("c")) if isinstance(quote, dict) else None
        return Fundamentals(
            symbol=symbol,
            provider=self.name,
            metrics=finnhub_metrics_from_payload(m),
            price=price,
            currency="USD",
            fetched_at=_now(),
        )


def _default_connector_helper() -> Path:
    return Path.home() / "workspace" / "custom-connectors" / "custom.finnhub" / "bin" / "finnhub_api.py"


class FinnhubConnectorProvider(BaseProvider):
    """Finnhub via the vault-stored key (custom.finnhub connector).

    Shells out to bin/finnhub_api.py so the API key never enters this
    process, its arguments, or its logs. Same mapping and semantics as
    FinnhubProvider.
    """

    name = "finnhub"

    def __init__(
        self,
        helper: str | Path | None = None,
        timeout: int = 60,
        throttle_s: float = 1.1,
    ):
        self._helper = str(helper or _default_connector_helper())
        if not Path(self._helper).exists():
            raise FileNotFoundError(f"Finnhub connector helper not found: {self._helper}")
        self._timeout = timeout
        self._throttle_s = throttle_s
        self._last_call = 0.0

    def _call(self, path: str, params: dict[str, str]) -> Any:
        import subprocess

        wait = self._throttle_s - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        proc = subprocess.run(
            [sys.executable, self._helper, path]
            + [f"{k}={v}" for k, v in params.items()],
            capture_output=True,
            text=True,
            timeout=self._timeout,
        )
        self._last_call = time.monotonic()
        if proc.returncode != 0:
            raise RuntimeError(f"finnhub helper failed: {proc.stderr.strip()[:200]}")
        return json.loads(proc.stdout)

    def fetch(self, symbol: str) -> Fundamentals | None:
        try:
            metric_payload = self._call("/stock/metric", {"symbol": symbol, "metric": "all"})
            quote = self._call("/quote", {"symbol": symbol})
        except Exception:
            return None
        m = metric_payload.get("metric") if isinstance(metric_payload, dict) else None
        if not m:
            return None
        price = _to_float(quote.get("c")) if isinstance(quote, dict) else None
        return Fundamentals(
            symbol=symbol,
            provider=self.name,
            metrics=finnhub_metrics_from_payload(m),
            price=price,
            currency="USD",
            fetched_at=_now(),
        )


class YFinanceProvider(BaseProvider):
    """Unofficial fallback. Works without a key but Yahoo throttles and
    breaks it periodically -- never the primary."""

    name = "yfinance"

    def fetch(self, symbol: str) -> Fundamentals | None:
        try:
            import yfinance as yf
        except ImportError:
            return None
        try:
            info = yf.Ticker(symbol).info
        except Exception:
            return None
        if not info:
            return None
        dte = _to_float(info.get("debtToEquity"))
        metrics = {
            "pe_ttm": _to_float(info.get("trailingPE")),
            "peg_ttm": _to_float(info.get("pegRatio")),
            "roe_ttm": _to_float(info.get("returnOnEquity")),
            "fcf_margin_ttm": self._ratio(info.get("freeCashflow"), info.get("totalRevenue")),
            "revenue_growth_yoy": _to_float(info.get("revenueGrowth")),
            "eps_growth_yoy": _to_float(info.get("earningsGrowth")),
            # yfinance debtToEquity is a percent (e.g. 154.5 = 154.5%)
            "debt_to_equity": (dte / 100.0) if dte is not None else None,
            "current_ratio": _to_float(info.get("currentRatio")),
        }
        if all(v is None for v in metrics.values()):
            return None
        return Fundamentals(
            symbol=symbol,
            provider=self.name,
            metrics=metrics,
            price=_to_float(info.get("regularMarketPrice")),
            currency=info.get("currency"),
            fetched_at=_now(),
            warnings=["yfinance is unofficial and may be throttled; treat as fallback data"],
        )

    @staticmethod
    def _ratio(num: Any, den: Any) -> float | None:
        n, d = _to_float(num), _to_float(den)
        if n is None or not d:
            return None
        return n / d


class CsvProvider(BaseProvider):
    """Manual backstop: data/fundamentals/<SYMBOL>.csv with metric,value rows."""

    name = "csv"

    def __init__(self, directory: str | Path = "data/fundamentals"):
        self._dir = Path(directory)

    def fetch(self, symbol: str) -> Fundamentals | None:
        path = self._dir / f"{symbol.upper()}.csv"
        if not path.exists():
            return None
        metrics: dict[str, float | None] = {m: None for m in STANDARD_METRICS}
        price = None
        try:
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    key = (row.get("metric") or "").strip()
                    val = _to_float((row.get("value") or "").strip())
                    if key == "price":
                        price = val
                    elif key in metrics:
                        metrics[key] = val
        except (OSError, csv.Error):
            return None
        return Fundamentals(
            symbol=symbol,
            provider=self.name,
            metrics=metrics,
            price=price,
            fetched_at=_now(),
            warnings=[f"manual CSV data from {path}"],
        )


def fetch_with_fallback(
    symbol: str, provider_list: list[BaseProvider], min_coverage: int
) -> Fundamentals | None:
    """First provider meeting min_coverage wins; otherwise the best-effort
    result is returned with a warning, or None when nobody can serve."""
    best: Fundamentals | None = None
    for provider in provider_list:
        try:
            fund = provider.fetch(symbol)
        except Exception:
            continue  # a provider must never break the chain
        if fund is None:
            continue
        if fund.coverage() >= min_coverage:
            return fund
        if best is None or fund.coverage() > best.coverage():
            best = fund
    if best is not None:
        best.warnings.append(
            f"below minimum coverage ({best.coverage()} < {min_coverage}); "
            "no provider had full data"
        )
    return best
