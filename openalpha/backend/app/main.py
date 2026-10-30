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
from typing import Annotated, Literal, cast

import pandas as pd
from fastapi import FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .config import Settings
from .engine import IngestionEngine
from .errors import DataError, FetchError, InvalidSymbol, OpenAlphaError, PortfolioError
from .export import export_filename, stream_csv, write_csv_file, write_parquet_file
from .normalizers import EdgarNormalizer, StatementService, YFinanceNormalizer
from .normalizers.yfinance import FrameLoader, load_with_yfinance
from .portfolio import (
    PortfolioConfig,
    PortfolioSnapshot,
    SignalConfig,
    ThesisResult,
    assess_thesis,
    build_snapshot,
    fund_result,
    load_portfolio,
)
from .portfolio.thesis import UNKNOWN

Dataset = Literal["ohlcv", "income", "balance", "cashflow"]
Fmt = Literal["csv", "parquet"]
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]   # the Vite dev server


@dataclass
class Services:
    settings: Settings
    engine: IngestionEngine
    statements: StatementService
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
            out[sym] = assess_thesis(res.income)
    return out


async def _snapshot(svc: Services) -> PortfolioSnapshot:
    pf = load_portfolio(svc.settings.portfolio_path)
    frames = await svc.engine.ohlcv_many(pf.symbols(), years=3)
    closes = {s: f["close"] for s, f in frames.items() if isinstance(f, pd.DataFrame)}
    notes = tuple(f"{s}: {e}" for s, e in frames.items() if isinstance(e, OpenAlphaError))
    usdcad: float | None = None
    if any(lot.currency == "USD" for lots in pf.accounts.values() for lot in lots):
        try:
            usdcad = float((await svc.engine.ohlcv("CAD=X", years=1))["close"].iloc[-1])
        except OpenAlphaError as exc:
            notes += (f"USDCAD: {exc}",)
    theses = await _theses(svc, pf)
    return replace(build_snapshot(pf, closes, usdcad, svc.signal_cfg, theses), notes=notes)


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
        app.state.svc = Services(cfg, engine, statements, signal_cfg or SignalConfig(), clock)
        try:
            yield
        finally:
            await engine.aclose()

    app = FastAPI(title="OpenAlpha", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

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

    @app.get("/api/positions")
    async def positions(request: Request) -> JSONResponse:
        return JSONResponse(asdict(await _snapshot(_svc(request))))

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

    @app.post("/api/export/{dataset}/{symbol}")
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
