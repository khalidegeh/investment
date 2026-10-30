"""Headlines and SEC 8-K filings for a holding.

Everything returned here is untrusted text from the internet. It is cleaned and
length-capped, links are limited to http(s), and consumers (the UI, any LLM)
must treat it as data to display, never as instructions. The 8-K approach
follows trader-skill by Jake Schincariol (MIT), re-implemented; see NOTICE.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree

from ..errors import DataError, FetchError
from ..fetcher import AsyncFetcher
from ..symbols import normalize_symbol
from .edgar import EdgarClient

RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

# Standard Form 8-K item numbers. `material` marks the ones that usually move a stock.
ITEM_LABELS: dict[str, tuple[str, bool]] = {
    "1.01": ("Entry into a material agreement", True),
    "1.02": ("Termination of a material agreement", True),
    "2.01": ("Completed acquisition or disposition", True),
    "2.02": ("Results of operations (earnings)", True),
    "2.03": ("New direct financial obligation", False),
    "2.05": ("Exit or disposal costs", True),
    "2.06": ("Material impairments", True),
    "3.01": ("Delisting or listing-standard notice", True),
    "4.01": ("Change of auditor", True),
    "4.02": ("Financial statements no longer reliable", True),
    "5.01": ("Change in control", True),
    "5.02": ("Director or officer departure or appointment", True),
    "5.03": ("Charter or bylaw amendment", False),
    "5.07": ("Shareholder vote results", False),
    "7.01": ("Regulation FD disclosure", False),
    "8.01": ("Other events", False),
    "9.01": ("Financial statements and exhibits", False),
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACE = re.compile(r"\s+")
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_DOC = re.compile(r"[A-Za-z0-9._-]{1,100}")


@dataclass(frozen=True)
class NewsItem:
    kind: str                      # "headline" | "8-K"
    title: str
    url: str
    published: datetime            # timezone-aware
    source: str
    items: tuple[str, ...] = ()    # 8-K item numbers
    material: bool = False


def clean(text: str, limit: int = 300) -> str:
    """Strip control characters, collapse whitespace and cap the length."""
    return _SPACE.sub(" ", _CONTROL.sub(" ", text)).strip()[:limit]


def safe_url(url: str) -> str:
    """Only plain http(s) links survive; anything else (javascript:, data:) becomes empty."""
    u = url.strip()
    return u if re.match(r"https?://[^\s]+$", u) and len(u) <= 500 else ""


def parse_rss(body: bytes) -> list[NewsItem]:
    """Yahoo Finance RSS headlines. XML is parsed with defusedxml (no entity tricks)."""
    try:
        root = ElementTree.fromstring(body)
    except (ParseError, ValueError) as exc:
        raise DataError("news: headline feed was not valid XML") from exc
    out: list[NewsItem] = []
    for item in root.iter("item"):
        title = clean(item.findtext("title") or "")
        stamp = item.findtext("pubDate")
        if not title or not stamp:
            continue
        try:
            when = parsedate_to_datetime(stamp.strip())
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        out.append(
            NewsItem("headline", title, safe_url(item.findtext("link") or ""), when,
                     "Yahoo Finance")
        )
    return sorted(out, key=lambda n: n.published, reverse=True)


def parse_8k(cik: int, payload: dict[str, object], since: date) -> list[NewsItem]:
    """8-K filings on or after ``since`` from an SEC submissions payload."""
    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        raise DataError("news: submissions had no recent filings")

    def col(name: str) -> list[object]:
        v = recent.get(name)
        return v if isinstance(v, list) else []

    forms, dates, items = col("form"), col("filingDate"), col("items")
    accs, docs = col("accessionNumber"), col("primaryDocument")
    out: list[NewsItem] = []
    for i, form in enumerate(forms):
        if form not in ("8-K", "8-K/A") or i >= len(dates):
            continue
        try:
            filed = date.fromisoformat(str(dates[i]))
        except ValueError:
            continue
        if filed < since:
            continue
        codes = tuple(
            c.strip() for c in str(items[i] if i < len(items) else "").split(",") if c.strip()
        )
        labels = [ITEM_LABELS.get(c, (f"Item {c}", False)) for c in codes]
        title = clean("8-K: " + "; ".join(label for label, _ in labels)) if labels else "8-K filing"
        acc = str(accs[i]) if i < len(accs) else ""
        doc = str(docs[i]) if i < len(docs) else ""
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"
            if _ACCESSION.fullmatch(acc) and _DOC.fullmatch(doc)
            else ""
        )
        out.append(
            NewsItem("8-K", title, url, datetime(filed.year, filed.month, filed.day,
                                                 tzinfo=timezone.utc),
                     "SEC EDGAR", codes, any(m for _, m in labels))
        )
    return sorted(out, key=lambda n: n.published, reverse=True)


class NewsClient:
    def __init__(self, fetcher: AsyncFetcher, edgar: EdgarClient | None):
        self._fetcher = fetcher
        self._edgar = edgar

    async def recent(
        self, symbol: str, days: int, now: datetime
    ) -> tuple[list[NewsItem], list[str]]:
        """Headlines plus (for SEC filers) 8-Ks from the last ``days`` days, newest first.

        A source that fails is reported in the warnings and never sinks the other.
        """
        sym = normalize_symbol(symbol)
        cutoff = now - timedelta(days=days)
        items: list[NewsItem] = []
        warnings: list[str] = []
        try:
            res = await self._fetcher.get(RSS_URL, {"s": sym, "region": "US", "lang": "en-US"})
            items += [n for n in parse_rss(res.body) if n.published >= cutoff]
        except (FetchError, DataError) as exc:
            warnings.append(f"headlines unavailable: {exc}")
        if "." in sym:
            warnings.append("SEC filings skipped: non-US listing")
        elif self._edgar is None:
            warnings.append("SEC filings skipped: no contact email configured")
        else:
            try:
                cik, payload = await self._edgar.submissions(sym)
                items += parse_8k(cik, payload, cutoff.date())
            except (FetchError, DataError) as exc:
                warnings.append(f"SEC filings unavailable: {exc}")
        return sorted(items, key=lambda n: n.published, reverse=True), warnings
