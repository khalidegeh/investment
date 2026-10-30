"""Fundamentals advisor: fetch -> score -> buy / hold / sell.

Halal exclusions from config are honored first: an excluded symbol is never
scored. This is a first-pass activity filter only; final halal compliance
belongs to a qualified scholar, not this module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import providers, scorecard


@dataclass
class Advice:
    symbol: str
    verdict: str  # buy | hold | sell | insufficient-data | excluded
    score: float | None  # 0-100, None when no verdict could be computed
    coverage: int  # metrics present out of the scorecard's total
    provider: str | None
    price: float | None
    currency: str | None
    fetched_at: str
    breakdown: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def build_providers(config) -> list[providers.BaseProvider]:
    """Instantiate the configured provider chain, in order.

    "finnhub" resolves to the vault-stored key via the custom.finnhub
    connector helper; an explicit FINNHUB_API_KEY (or the configured env var)
    overrides it for scripts and testing.
    """
    order = config.get("fundamentals", "providers", default=["finnhub", "yfinance", "csv"])
    out: list[providers.BaseProvider] = []
    for name in order or []:
        if name == "finnhub":
            env_var = config.get("fundamentals", "finnhub_api_key_env", default="FINNHUB_API_KEY")
            key = os.environ.get(env_var or "FINNHUB_API_KEY", "")
            if key:
                out.append(providers.FinnhubProvider(key))
            else:
                try:
                    out.append(providers.FinnhubConnectorProvider())
                except FileNotFoundError:
                    pass  # no vault key wired up; chain falls through
        elif name == "yfinance":
            out.append(providers.YFinanceProvider())
        elif name == "csv":
            out.append(
                providers.CsvProvider(config.get("fundamentals", "csv_dir", default="data/fundamentals"))
            )
    return out


def advise(symbol: str, config, provider_list: list | None = None) -> Advice:
    sym = symbol.upper().strip()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    exclusions = {s.upper() for s in (config.get("halal_exclusions", default=[]) or [])}
    if sym in exclusions:
        return Advice(
            symbol=sym,
            verdict=scorecard.EXCLUDED,
            score=None,
            coverage=0,
            provider=None,
            price=None,
            currency=None,
            fetched_at=now,
            warnings=["excluded by halal_exclusions in config (first-pass filter only)"],
        )

    if provider_list is None:
        provider_list = build_providers(config)
    min_coverage = int(
        config.get("fundamentals", "min_coverage", default=scorecard.DEFAULT_MIN_COVERAGE)
    )
    fund = providers.fetch_with_fallback(sym, provider_list, min_coverage)
    if fund is None:
        return Advice(
            symbol=sym,
            verdict=scorecard.INSUFFICIENT,
            score=None,
            coverage=0,
            provider=None,
            price=None,
            currency=None,
            fetched_at=now,
            warnings=["no provider could serve fundamentals for this symbol"],
        )

    specs = scorecard.DEFAULT_METRICS
    raw_specs = config.get("fundamentals", "scorecard")
    if raw_specs:
        specs = scorecard.specs_from_config(raw_specs)
    result = scorecard.score(
        fund.metrics,
        specs,
        buy_at=float(config.get("fundamentals", "buy_at", default=scorecard.DEFAULT_BUY_AT)),
        hold_at=float(config.get("fundamentals", "hold_at", default=scorecard.DEFAULT_HOLD_AT)),
        min_coverage=min_coverage,
    )
    warnings = list(fund.warnings)
    if result["coverage"] < len(specs):
        missing = [s.name for s in specs if s.name not in result["breakdown"]]
        warnings.append(f"metrics skipped (no data): {', '.join(missing)}")
    return Advice(
        symbol=sym,
        verdict=result["verdict"],
        score=result["score"],
        coverage=result["coverage"],
        provider=fund.provider,
        price=fund.price,
        currency=fund.currency,
        fetched_at=fund.fetched_at or now,
        breakdown=result["breakdown"],
        warnings=warnings,
    )
