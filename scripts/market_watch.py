#!/usr/bin/env python3
"""Ongoing market advisor watch — deterministic data pulls for the desk slots.

Slots:
  morning  Index futures, near-open snapshot vs prev close for holdings +
           watchlist, staged earnings catalyst calendar.
  check    Intraday alert check vs today's open. Prints exactly
           "SILENT — nothing to report." when there is nothing to flag
           (the cron worker then decides on news-based alerts).
  close    Closing tape: P/L totals + biggest movers.

Awareness only: this script never trades and never recommends buys.
Run with the trading-system venv python:
  /home/hatch/workspace/trading-system/.venv/bin/python scripts/market_watch.py --slot morning|check|close
"""
from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TS = Path("/home/hatch/workspace/trading-system")
sys.path.insert(0, str(TS / "src"))  # tradingsystem package (needed to unpickle ledger)
FINNHUB_HELPER = Path(
    "/home/hatch/workspace/custom-connectors/custom.finnhub/bin/finnhub_api.py"
)
LEDGER = TS / "ledger.pkl"
EARNINGS_DIR = TS / "data/earnings"
STATE_FILE = Path(
    "/home/hatch/workspace/goals/deterministic-trading-system-build"
    "/hidden_files/market-watch-state.json"
)
TZ = ZoneInfo("America/Toronto")

WATCHLIST = []  # retired 2026-10-21 at Khalid's direction: ONON/RELY/GIC out of his lane (tech-facing only)
QUOTE_ALIASES = {"XQQ": "XQQ.TO"}  # ledger keeps the short symbol


# ---------------------------------------------------------------- helpers
def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def is_usd(sym: str) -> bool:
    return not QUOTE_ALIASES.get(sym, sym).endswith(".TO")


def fh_quote(qsym: str) -> dict | None:
    """Finnhub /quote -> price, prev_close, open (US symbols)."""
    try:
        out = subprocess.run(
            [sys.executable, str(FINNHUB_HELPER), "/quote", f"symbol={qsym}"],
            capture_output=True, text=True, timeout=40,
        )
    except Exception as e:  # noqa: BLE001 - quote failure is non-fatal
        print(f"  [warn] finnhub {qsym}: {e}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"  [warn] finnhub {qsym}: {out.stderr.strip()[:120]}", file=sys.stderr)
        return None
    try:
        q = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    if not q.get("c"):
        return None

    def f(k):
        v = q.get(k)
        return float(v) if v else None

    return {"price": f("c"), "prev_close": f("pc"), "open": f("o")}


def yf_fast(qsym: str) -> dict | None:
    """yfinance fast info -> price, prev_close (TSX, futures, FX)."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [warn] yfinance not installed", file=sys.stderr)
        return None
    try:
        fi = yf.Ticker(qsym).fast_info
        price = float(fi.last_price)
        prev = float(fi.previous_close)
    except Exception as e:  # noqa: BLE001 - quote failure is non-fatal
        print(f"  [warn] yfinance {qsym}: {e}", file=sys.stderr)
        return None
    if not price:
        return None
    return {"price": price, "prev_close": prev, "open": None}


def yf_open(qsym: str) -> float | None:
    """Today's open from the daily bar; None if unavailable."""
    try:
        import pandas as pd
        import yfinance as yf

        df = yf.download(qsym, period="2d", interval="1d", progress=False)
    except Exception as e:  # noqa: BLE001 - quote failure is non-fatal
        print(f"  [warn] yf open {qsym}: {e}", file=sys.stderr)
        return None
    if df.empty or "Open" not in df.columns:
        return None
    col = df["Open"]
    if isinstance(col, pd.DataFrame):
        col = col.iloc[:, 0]
    try:
        o = float(col.iloc[-1])
    except (ValueError, TypeError):
        return None
    return o if o else None


def get_quote(sym: str) -> dict | None:
    qsym = QUOTE_ALIASES.get(sym, sym)
    if qsym.endswith(".TO"):
        q = yf_fast(qsym)
        if q:
            q["open"] = yf_open(qsym)
        return q
    q = fh_quote(qsym)
    time.sleep(1.2)  # Finnhub free-tier throttle
    return q


def load_ledger():
    with open(LEDGER, "rb") as f:
        return pickle.load(f)


def holding_symbols() -> list[str]:
    ledger = load_ledger()
    return sorted({sym for (_, sym) in ledger.positions()})


def all_names() -> list[str]:
    syms = holding_symbols()
    return syms + [w for w in WATCHLIST if w not in syms]


def pct(a: float | None, b: float | None) -> float | None:
    if not a or not b:
        return None
    return (a - b) / b * 100.0


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def market_open_today() -> bool:
    """False on weekends/holidays (no daily bar for today)."""
    try:
        import yfinance as yf

        df = yf.download("SPY", period="3d", interval="1d", progress=False)
        if df.empty:
            return True  # can't tell; proceed
        last = df.index[-1].date().isoformat()
        return last == today_str()
    except Exception:  # noqa: BLE001 - be permissive on infra failure
        return True


# ---------------------------------------------------------------- slots
def slot_morning() -> None:
    t = today_str()
    print(f"=== MARKET WATCH — MORNING BRIEF DATA ({t}, America/Toronto) ===")
    for fsym, name in (("ES=F", "S&P 500"), ("NQ=F", "Nasdaq-100")):
        q = yf_fast(fsym)
        p = pct(q["price"], q["prev_close"]) if q else None
        if p is None:
            print(f"Futures {name} ({fsym}): n/a")
        else:
            print(f"Futures {name} ({fsym}): {q['price']:.1f}  {p:+.2f}% vs prev close")

    print("--- Holdings + watchlist (vs prev close, near open) ---")
    for sym in all_names():
        q = get_quote(sym)
        if not q:
            print(f"  {sym:<8} n/a")
            continue
        p = pct(q["price"], q["prev_close"])
        ps = f"{p:+.2f}%" if p is not None else "n/a"
        tag = " [watchlist]" if sym in WATCHLIST else ""
        print(f"  {sym:<8} {q['price']:>9.2f}  {ps:>8}{tag}")

    print("--- Catalyst calendar (staged earnings dates) ---")
    td = date.fromisoformat(t)
    upcoming: list[tuple[int, str, str]] = []
    for sym in all_names():
        p = EARNINGS_DIR / f"{sym}.csv"
        if not p.exists():
            continue
        for line in p.read_text().splitlines()[1:]:
            d = line.strip()
            if not d:
                continue
            days = (date.fromisoformat(d) - td).days
            if days >= 0:
                upcoming.append((days, sym, d))
    upcoming.sort()
    near = [u for u in upcoming if u[0] <= 7]
    if near:
        for days, sym, d in near:
            print(f"  EARNINGS <=7d: {sym} reports {d} ({days}d out)")
    else:
        print("  No staged earnings within 7 days.")
    for days, sym, d in upcoming[:6]:
        print(f"  Next: {sym} {d} ({days}d)")
    print("--- MACRO: worker, do a quick web search for today's macro events "
          "(CPI/PPI/jobs claims/Fed speakers/BoC) and fold into the brief. ---")


def slot_check() -> None:
    t = today_str()
    if not market_open_today():
        print("SILENT — market closed.")
        return
    syms = holding_symbols()
    quotes = {s: get_quote(s) for s in syms}

    state = load_state()
    if state.get("date") != t:
        opens = {}
        for s in syms:
            q = quotes.get(s)
            o = (q or {}).get("open") or (q or {}).get("prev_close")
            if o:
                opens[s] = o
        save_state({"date": t, "opens": opens})
    else:
        opens = state.get("opens", {})

    alerts = []
    for s in syms:
        q = quotes.get(s)
        o = opens.get(s)
        if not q or not o:
            continue
        p = pct(q["price"], o)
        if p is not None and abs(p) >= 3.0:
            alerts.append((s, q["price"], p))
    if alerts:
        print("ALERT — intraday move >=3% vs today's open:")
        for s, price, p in alerts:
            print(f"  {s}: ${price:.2f} ({p:+.1f}% intraday)")
    else:
        print("SILENT — nothing to report.")


def slot_close() -> None:
    t = today_str()
    ledger = load_ledger()
    fxq = yf_fast("CAD=X")
    usdcad = fxq["price"] if fxq else None
    if usdcad is None:
        print("[warn] no USDCAD rate; USD lots converted at 1.0", file=sys.stderr)

    rows = []
    tot_u = tot_d = 0.0
    for (acct, sym), sh in sorted(ledger.positions().items()):
        entry = ledger.get_acb_per_share(sym, acct)
        q = get_quote(sym)
        if not q:
            rows.append((acct, sym, sh, None, None, None, None))
            continue
        unreal = (q["price"] - entry) * sh
        day = (q["price"] - q["prev_close"]) * sh if q["prev_close"] else 0.0
        day_pct = pct(q["price"], q["prev_close"]) or 0.0
        rate = 1.0 if not is_usd(sym) else (usdcad or 1.0)
        u_cad, d_cad = unreal * rate, day * rate
        tot_u += u_cad
        tot_d += d_cad
        rows.append((acct, sym, sh, q["price"], u_cad, d_cad, day_pct))

    print(f"=== MARKET WATCH — CLOSING TAPE ({t}, America/Toronto) ===")
    for acct, sym, sh, price, u_cad, d_cad, day_pct in rows:
        if price is None:
            print(f"  {acct:<6} {sym:<8} quote n/a")
            continue
        print(f"  {acct:<6} {sym:<8} {sh:>8.4f}sh @ {price:>8.2f}  "
              f"day {day_pct:+.1f}%  P/L {d_cad:+.2f} C$  unreal {u_cad:+.2f} C$")
    fx_s = f"{usdcad:.4f}" if usdcad else "n/a"
    print(f"TOTAL unrealized: C${tot_u:+.2f}  |  Day P/L: C${tot_d:+.2f}  (USDCAD {fx_s})")
    print("--- Biggest movers (by |day P/L|) ---")
    movers = sorted([r for r in rows if r[4] is not None],
                    key=lambda r: abs(r[5]), reverse=True)[:3]
    for acct, sym, sh, price, u_cad, d_cad, day_pct in movers:
        print(f"  {sym} ({acct}): {d_cad:+.2f} C$ on the day ({day_pct:+.1f}%)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Market watch data pulls")
    ap.add_argument("--slot", required=True, choices=["morning", "check", "close"])
    args = ap.parse_args()
    {"morning": slot_morning, "check": slot_check, "close": slot_close}[args.slot]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
