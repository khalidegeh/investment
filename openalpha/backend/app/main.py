"""OpenAlpha local API.

Run (loopback only, so nothing is reachable from the network):

    uvicorn --factory app.main:create_default --host 127.0.0.1 --port 8000

Portfolio entries are read from a local file and are never sent anywhere.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, cast

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .engine import IngestionEngine
from .errors import DataError, FetchError, InvalidSymbol, OpenAlphaError, PortfolioError
from .export import export_filename, stream_csv, write_csv_file, write_parquet_file
from .market import (
    AlertRule,
    CalendarUnknown,
    Quote,
    evaluate,
    is_open,
    load_state,
    next_open,
    rules_from_portfolio,
    save_state,
)
from .normalizers import EdgarNormalizer, StatementService, YFinanceNormalizer
from .normalizers.yfinance import FrameLoader, load_with_yfinance
from .portfolio import (
    PortfolioConfig,
    PortfolioSnapshot,
    SignalConfig,
    ThesisResult,
    assess_thesis,
    assess_thesis_quarterly,
    build_snapshot,
    fund_result,
    load_portfolio,
)
from .portfolio.thesis import UNKNOWN
from .sources.news import NewsClient, NewsItem

Dataset = Literal["ohlcv", "income", "balance", "cashflow"]
Fmt = Literal["csv", "parquet"]
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]   # the Vite dev server
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]   # blocks DNS-rebinding requests
STATIC_DIR = Path(__file__).parent / "static"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _require_local_header(request: Request) -> None:
    """State-changing routes need a custom header, which browsers will not send
    cross-site without a CORS preflight that this server refuses."""
    if request.headers.get("x-openalpha") != "1":
        raise HTTPException(status_code=403, detail="missing X-OpenAlpha header")


def _news_dict(n: NewsItem) -> dict[str, object]:
    return {"kind": n.kind, "title": n.title, "url": n.url,
            "published": n.published.isoformat(), "source": n.source,
            "items": list(n.items), "material": n.material}


@dataclass
class Services:
    settings: Settings
    engine: IngestionEngine
    statements: StatementService
    news: NewsClient
    signal_cfg: SignalConfig
    clock: Callable[[], datetime]


def _svc(request: Request) -> Services:
    return cast(Services, request.app.state.svc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _theses(svc: Services, pf: PortfolioConfig) -> dict[str, ThesisResult]:
    """Thesis status per symbol. Funds are not applicable; failures are UNKNOWN, never guessed."""
    funds = {
        lot.symbol for lots in pf.accounts.values() for lot in lots if lot.instrument == "etf"
    }
    out = {sym: fund_result() for sym in funds}
    names = [s for s in pf.symbols() if s not in funds]
    got = await asyncio.gather(*(svc.statements.get(s) for s in names), return_exceptions=True)
    for sym, res in zip(names, got, strict=True):
        if isinstance(res, OpenAlphaError):
            out[sym] = ThesisResult(UNKNOWN, (f"statements unavailable: {res}",))
        elif isinstance(res, BaseException):
            raise res
        else:
            quarterly = None if res.quarterly is None else assess_thesis_quarterly(res.quarterly)
            out[sym] = (
                quarterly
                if quarterly is not None and quarterly.status != UNKNOWN
                else assess_thesis(res.income)
            )
    return out


async def _closes(
    svc: Services, pf: PortfolioConfig
) -> tuple[dict[str, pd.Series], tuple[str, ...]]:
    frames = await svc.engine.ohlcv_many(pf.symbols(), years=3)
    closes = {s: f["close"] for s, f in frames.items() if isinstance(f, pd.DataFrame)}
    notes = tuple(f"{s}: {e}" for s, e in frames.items() if isinstance(e, OpenAlphaError))
    return closes, notes


async def _snapshot(svc: Services) -> PortfolioSnapshot:
    pf = load_portfolio(svc.settings.portfolio_path)
    closes, notes = await _closes(svc, pf)
    usdcad: float | None = None
    if any(lot.currency == "USD" for lots in pf.accounts.values() for lot in lots):
        try:
            usdcad = float((await svc.engine.ohlcv("CAD=X", years=1))["close"].iloc[-1])
        except OpenAlphaError as exc:
            notes += (f"USDCAD: {exc}",)
    theses = await _theses(svc, pf)
    snap = build_snapshot(pf, closes, usdcad, svc.signal_cfg, theses, svc.clock().date())
    return replace(snap, notes=notes)


async def _attach_news(
    svc: Services, data: dict[str, object], days: int, limit: int
) -> None:
    """Add recent headlines and 8-Ks to each position line in the serialised snapshot."""
    accounts = cast(list[dict[str, object]], data["accounts"])
    lines = [ln for a in accounts for ln in cast(list[dict[str, object]], a["lines"])]
    symbols = sorted({str(ln["symbol"]) for ln in lines})
    got = await asyncio.gather(
        *(svc.news.recent(s, days, svc.clock()) for s in symbols), return_exceptions=True
    )
    by_symbol: dict[str, tuple[list[NewsItem], list[str]]] = {}
    for sym, res in zip(symbols, got, strict=True):
        if isinstance(res, OpenAlphaError):
            by_symbol[sym] = ([], [f"news unavailable: {res}"])
        elif isinstance(res, BaseException):
            raise res
        else:
            by_symbol[sym] = (res[0][:limit], res[1])
    for ln in lines:
        items, warns = by_symbol[str(ln["symbol"])]
        ln["news"] = [_news_dict(n) for n in items]
        ln["news_warnings"] = warns


def _rule_dict(r: AlertRule, state: dict[str, str]) -> dict[str, object]:
    return {"id": r.id, "symbol": r.symbol, "kind": r.kind, "level": r.level,
            "note": r.note, "last_rang": state.get(r.id)}


def _positions_frame(snap: PortfolioSnapshot) -> pd.DataFrame:
    rows = [
        {
            "account": ln.account, "symbol": ln.symbol, "currency": ln.currency,
            "shares": ln.shares, "avg_entry": ln.avg_entry, "price": ln.price,
            "prev_close": ln.prev_close, "cost": ln.cost, "market_value": ln.market_value,
            "total_pl": ln.total_pl, "total_pl_pct": ln.total_pl_pct, "day_pl": ln.day_pl,
            "signal": ln.signal.action, "reasons": "; ".join(ln.signal.reasons),
            "flags": "; ".join(ln.flags),
            "thesis": "" if ln.thesis is None else ln.thesis.status,
            "thesis_reasons": "" if ln.thesis is None else "; ".join(ln.thesis.reasons),
        }
        for ln in snap.lines()
    ]
    out: pd.DataFrame = pd.DataFrame(rows).set_index(["account", "symbol"])
    return out


async def _dataset(svc: Services, dataset: Dataset, symbol: str, years: int) -> pd.DataFrame:
    if dataset == "ohlcv":
        return await svc.engine.ohlcv(symbol, years)
    return (await svc.statements.get(symbol)).frame(dataset)


def _json(df: pd.DataFrame) -> Response:
    return Response(df.reset_index().to_json(orient="records", date_format="iso"),
                    media_type="application/json")


def create_app(
    settings: Settings | None = None,
    *,
    engine_factory: Callable[[], IngestionEngine] | None = None,
    frame_loader: FrameLoader = load_with_yfinance,
    signal_cfg: SignalConfig | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> FastAPI:
    cfg = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = engine_factory() if engine_factory else IngestionEngine(cfg)
        edgar = EdgarNormalizer(engine.edgar) if engine.settings.contact_email else None
        statements = StatementService(edgar, YFinanceNormalizer(engine.cache, frame_loader))
        news = NewsClient(engine.fetcher, engine.edgar if edgar is not None else None)
        app.state.svc = Services(
            cfg, engine, statements, news, signal_cfg or SignalConfig(), clock
        )
        try:
            yield
        finally:
            await engine.aclose()

    app = FastAPI(title="OpenAlpha", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = CSP
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.exception_handler(InvalidSymbol)
    async def _bad_symbol(_: Request, exc: InvalidSymbol) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=422)

    @app.exception_handler(DataError)
    async def _no_data(_: Request, exc: DataError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(FetchError)
    async def _upstream(_: Request, exc: FetchError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.exception_handler(PortfolioError)
    async def _portfolio(_: Request, exc: PortfolioError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/ohlcv/{symbol}")
    async def ohlcv(
        request: Request, symbol: str, years: Annotated[int, Query(ge=1, le=60)] = 30
    ) -> Response:
        return _json(await _dataset(_svc(request), "ohlcv", symbol, years))

    @app.get("/api/statements/{symbol}/{kind}")
    async def statement(
        request: Request, symbol: str, kind: Literal["income", "balance", "cashflow"]
    ) -> Response:
        return _json(await _dataset(_svc(request), kind, symbol, 30))

    @app.get("/api/news/{symbol}")
    async def news(
        request: Request, symbol: str, days: Annotated[int, Query(ge=1, le=30)] = 7
    ) -> JSONResponse:
        """Headlines and SEC 8-Ks. Untrusted text: display it, never act on it."""
        svc = _svc(request)
        items, warnings = await svc.news.recent(symbol, days, svc.clock())
        return JSONResponse({
            "symbol": symbol.upper(),
            "warnings": warnings,
            "items": [_news_dict(n) for n in items],
        })

    @app.get("/api/market/clock")
    async def market_clock(request: Request) -> JSONResponse:
        now = _svc(request).clock()
        try:
            return JSONResponse({"now": now.isoformat(), "open": is_open(now),
                                 "next_open": next_open(now).isoformat()})
        except CalendarUnknown as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/positions")
    async def positions(
        request: Request,
        news: bool = False,
        news_days: Annotated[int, Query(ge=1, le=30)] = 7,
        news_limit: Annotated[int, Query(ge=1, le=10)] = 3,
    ) -> JSONResponse:
        svc = _svc(request)
        data = jsonable_encoder(asdict(await _snapshot(svc)))
        if news:
            await _attach_news(svc, data, news_days, news_limit)
        return JSONResponse(data)

    @app.get("/api/alerts")
    async def alert_rules(request: Request) -> JSONResponse:
        """The rules built from your plan and when each last rang. No side effects."""
        svc = _svc(request)
        pf = load_portfolio(svc.settings.portfolio_path)
        closes, _ = await _closes(svc, pf)
        prices = {s: float(c.iloc[-1]) for s, c in closes.items() if len(c) > 0}
        state = load_state(svc.settings.alerts_state_path)
        rules = rules_from_portfolio(pf, prices=prices)
        return JSONResponse({"rules": [_rule_dict(r, state) for r in rules]})

    @app.post("/api/alerts/check", dependencies=[Depends(_require_local_header)])
    async def alert_check(request: Request) -> JSONResponse:
        """Evaluate the rules against the latest closes and remember what rang."""
        svc = _svc(request)
        pf = load_portfolio(svc.settings.portfolio_path)
        closes, notes = await _closes(svc, pf)
        quotes = {
            s: Quote(float(c.iloc[-1]), float(c.iloc[-2]) if len(c) > 1 else None)
            for s, c in closes.items() if len(c) > 0
        }
        rules = rules_from_portfolio(pf, prices={s: q.price for s, q in quotes.items()})
        state = load_state(svc.settings.alerts_state_path)
        fired, new_state = evaluate(rules, quotes, state, svc.clock().date())
        save_state(svc.settings.alerts_state_path, new_state)
        return JSONResponse({
            "fired": [{"id": f.rule.id, "symbol": f.rule.symbol, "message": f.message,
                       "price": f.price} for f in fired],
            "rules": [_rule_dict(r, new_state) for r in rules],
            "notes": list(notes),
        })

    @app.get("/api/export/positions")
    async def export_positions(request: Request, fmt: Fmt = "csv") -> Response:
        svc = _svc(request)
        df = _positions_frame(await _snapshot(svc))
        return _deliver(svc, df, "PORTFOLIO", "positions", fmt)

    @app.get("/api/export/{dataset}/{symbol}")
    async def export_stream(
        request: Request, dataset: Dataset, symbol: str, fmt: Fmt = "csv",
        years: Annotated[int, Query(ge=1, le=60)] = 30,
    ) -> Response:
        svc = _svc(request)
        return _deliver(svc, await _dataset(svc, dataset, symbol, years), symbol, dataset, fmt)

    @app.post("/api/export/{dataset}/{symbol}", dependencies=[Depends(_require_local_header)])
    async def export_to_disk(
        request: Request, dataset: Dataset, symbol: str, fmt: Fmt = "csv",
        years: Annotated[int, Query(ge=1, le=60)] = 30,
    ) -> dict[str, object]:
        svc = _svc(request)
        df = await _dataset(svc, dataset, symbol, years)
        writer = write_csv_file if fmt == "csv" else write_parquet_file
        res = writer(df, svc.settings.export_dir, symbol, dataset, svc.clock())
        return {"path": str(res.path), "rows": res.rows, "bytes": res.bytes}

    def _deliver(svc: Services, df: pd.DataFrame, symbol: str, kind: str, fmt: Fmt) -> Response:
        now = svc.clock()
        name = export_filename(symbol, kind, fmt, now)
        if fmt == "csv":
            return StreamingResponse(
                stream_csv(df), media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        res = write_parquet_file(df, svc.settings.export_dir, symbol, kind, now)
        return FileResponse(res.path, media_type="application/octet-stream", filename=name)

    return app


def create_default() -> FastAPI:
    return create_app()
