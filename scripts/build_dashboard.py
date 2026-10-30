#!/usr/bin/env python3
"""Build a static fundamentals dashboard (deterministic, no external deps).

Reads open positions from ledger.pkl, scores each symbol with the
fundamentals advisor, and writes a self-contained HTML page. Every number
carries its source and timestamp; missing data is shown as missing, never
invented.

Usage:
    .venv/bin/python scripts/build_dashboard.py [--out dashboard/index.html]
"""
from __future__ import annotations

import argparse
import html
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradingsystem.config import Config  # noqa: E402
from tradingsystem.fundamentals import advisor  # noqa: E402

ET = ZoneInfo("America/Toronto")

VERDICT_STYLE = {
    "buy": ("BUY", "#1a7f37", "#dcfce7"),
    "hold": ("HOLD", "#9a6700", "#fef3c7"),
    "sell": ("SELL", "#b42318", "#fee4e2"),
    "insufficient-data": ("NO DATA", "#475467", "#f2f4f7"),
    "excluded": ("EXCLUDED", "#6941c6", "#ebe9fe"),
}

CSS = """
body{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;margin:0;
color:#101828;background:#f9fafb}
.wrap{max-width:1080px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
.sub{color:#475467;font-size:13px;margin-bottom:16px}
.banner{background:#fffbeb;border:1px solid #fedf89;border-radius:8px;
padding:12px 16px;font-size:13px;margin:16px 0}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.card{background:#fff;border:1px solid #e4e7ec;border-radius:8px;
padding:12px 16px;min-width:120px}
.card .n{font-size:24px;font-weight:700}.card .l{font-size:12px;color:#475467}
table{width:100%;border-collapse:collapse;background:#fff;font-size:13px;
border:1px solid #e4e7ec;border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #e4e7ec}
th{background:#f9fafb;color:#475467;font-weight:600;font-size:12px}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;
font-size:11px;font-weight:700}
.warn{color:#9a6700;font-size:12px}
.detail{background:#fff;border:1px solid #e4e7ec;border-radius:8px;
padding:16px;margin:12px 0}
.detail h3{margin:0 0 8px;font-size:15px}
footer{margin-top:32px;font-size:12px;color:#475467}
code{background:#f2f4f7;padding:1px 5px;border-radius:4px;font-size:12px}
"""


def badge(verdict: str) -> str:
    label, fg, bg = VERDICT_STYLE.get(verdict, (verdict.upper(), "#333", "#eee"))
    return (
        f'<span class="badge" style="color:{fg};background:{bg}">'
        f"{html.escape(label)}</span>"
    )


def load_positions(ledger_path: Path):
    with open(ledger_path, "rb") as f:
        ledger = pickle.load(f)
    out: dict[str, dict] = {}
    for (account, symbol), shares in ledger.positions().items():  # pools are keyed (account, symbol)
        if shares <= 0:
            continue
        entry = out.setdefault(symbol, {"accounts": [], "total": 0.0})
        acb = ledger.get_acb_per_share(symbol, account)
        entry["accounts"].append({"account": account, "shares": shares, "acb": acb})
        entry["total"] += shares
    return out


def metric_rows(breakdown: dict) -> str:
    rows = []
    for name, b in breakdown.items():
        val = b["value"]
        val_s = f"{val:.4f}" if isinstance(val, float) else html.escape(str(val))
        rows.append(
            f"<tr><td><code>{html.escape(name)}</code></td>"
            f"<td>{val_s}</td><td>{b['points']:+d}</td>"
            f"<td>{b['weight']}</td></tr>"
        )
    return "\n".join(rows) if rows else '<tr><td colspan="4">no metrics</td></tr>'


def build(config: Config, ledger_path: Path) -> str:
    positions = load_positions(ledger_path)
    universe = list(config.get("universe", default=[]) or [])
    symbols = sorted(set(universe) | set(positions))
    providers = advisor.build_providers(config)
    chain = [p.name for p in providers]

    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)

    advices = [advisor.advise(s, config, providers) for s in symbols]

    counts: dict[str, int] = {}
    for a in advices:
        counts[a.verdict] = counts.get(a.verdict, 0) + 1

    cards = "".join(
        f'<div class="card"><div class="n">{counts.get(v, 0)}</div>'
        f'<div class="l">{label}</div></div>'
        for v, (label, _, _) in VERDICT_STYLE.items()
    )

    table_rows = []
    details = []
    for a in advices:
        pos = positions.get(a.symbol, {"accounts": [], "total": 0.0})
        acct_s = ", ".join(
            f"{x['account']} {x['shares']:.4f} sh"
            + (f" @ {x['acb']:.2f}" if x["acb"] else "")
            for x in pos["accounts"]
        ) or "-"
        score_s = f"{a.score:.1f}" if a.score is not None else "-"
        price_s = f"{a.price:.2f} {a.currency or ''}".strip() if a.price else "-"
        table_rows.append(
            f"<tr><td><b>{html.escape(a.symbol)}</b></td>"
            f"<td>{html.escape(acct_s)}</td>"
            f"<td>{badge(a.verdict)}</td><td>{score_s}</td>"
            f"<td>{price_s}</td>"
            f"<td>{html.escape(a.provider or '-')}</td></tr>"
        )
        warns = "".join(f'<div class="warn">! {html.escape(w)}</div>' for w in a.warnings)
        details.append(
            f'<div class="detail"><h3>{html.escape(a.symbol)} {badge(a.verdict)}'
            f" <span style='font-weight:400;font-size:13px'>score {score_s} "
            f"| coverage {a.coverage}/8 | {html.escape(a.provider or 'none')} | "
            f"{html.escape(a.fetched_at or '')}</span></h3>"
            f"{warns}"
            f'<table><tr><th>Metric</th><th>Value</th><th>Points</th><th>Weight</th></tr>'
            f"{metric_rows(a.breakdown)}</table></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fundamentals Dashboard</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Fundamentals Dashboard</h1>
<div class="sub">Generated {now_et:%Y-%m-%d %H:%M} ET ({now_utc:%H:%M} UTC)
&middot; provider chain: {html.escape(", ".join(chain) or "(none configured)")}</div>
<div class="banner"><b>How to read this:</b> every verdict is computed from the
metric table below it using fixed thresholds in
<code>src/tradingsystem/fundamentals/scorecard.py</code>. A missing metric is
skipped, never estimated. Fewer than 5 of 8 metrics means
<b>NO DATA</b>, not a guess. Halal screening here is a first-pass activity
filter only; final compliance belongs to a qualified scholar.</div>
<div class="cards">{cards}</div>
<h2>Positions and verdicts</h2>
<table><tr><th>Symbol</th><th>Ledger position</th><th>Verdict</th><th>Score</th>
<th>Price</th><th>Source</th></tr>
{"".join(table_rows) if table_rows else '<tr><td colspan="6">no symbols</td></tr>'}
</table>
<h2>Metric detail</h2>
{"".join(details)}
<footer>Deterministic build: same inputs always produce the same page.
Scores: buy &ge; 65, hold 40&ndash;65, sell &lt; 40. Prices are reference only,
not trade triggers. Paper-trading research tool; not financial advice.</footer>
</div></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--ledger", default=str(ROOT / "ledger.pkl"))
    ap.add_argument("--out", default=str(ROOT / "dashboard" / "index.html"))
    args = ap.parse_args()

    config = Config.load(args.config)
    page = build(config, Path(args.ledger))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"wrote {out} ({len(page) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
