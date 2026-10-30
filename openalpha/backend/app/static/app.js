"use strict";
// OpenAlpha positions screen. Every string from the server is untrusted: it is only ever
// written with textContent, and links are only created for https URLs.

const money = new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
const priceFmt = new Intl.NumberFormat("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const sharesFmt = new Intl.NumberFormat("en-CA", { maximumFractionDigits: 4 });
const SIGNAL_TONE = { BUY: "good", HOLD: "", SELL: "bad", "N/A": "" };
const THESIS_TONE = { INTACT: "good", WATCH: "warn", BROKEN: "bad", UNKNOWN: "", "N/A": "" };
const NEEDED_SOON_MONTHS = 48;
const ROW_LABELS = ["Holding", "Shares", "Avg entry", "Price", "P/L", "Today", "Signal", "Thesis", "News"];

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}
function pct(x, digits = 1) {
  if (x === null || x === undefined) return "n/a";
  return (x >= 0 ? "+" : "") + (x * 100).toFixed(digits) + "%";
}
function signed(x) {
  if (x === null || x === undefined) return "n/a";
  return (x >= 0 ? "+" : "") + priceFmt.format(x);
}
function tone(x) { return x === null || x === undefined ? "" : x >= 0 ? "pos" : "neg"; }
function chip(text, toneClass) { return el("span", "chip " + (toneClass || ""), text); }
function reasonList(reasons) {
  const ul = el("ul");
  for (const r of reasons || []) ul.appendChild(el("li", "", r));
  return ul;
}
function details(summaryText, content) {
  const d = el("details");
  d.appendChild(el("summary", "", summaryText));
  d.appendChild(content);
  return d;
}

async function getJson(path, options) {
  const res = await fetch(path, options);
  let body = {};
  try { body = await res.json(); } catch (_) { /* not JSON */ }
  if (!res.ok) throw new Error(body.error || body.detail || res.statusText);
  return body;
}

function renderClock(c) {
  const box = document.getElementById("clock");
  if (c.error) { box.textContent = c.error; return; }
  const next = new Date(c.next_open).toLocaleString("en-CA", { weekday: "short", hour: "numeric", minute: "2-digit", month: "short", day: "numeric" });
  box.textContent = c.open ? "NYSE open" : "NYSE closed, next open " + next;
}

function renderAlerts(result) {
  const box = document.getElementById("alerts");
  box.replaceChildren();
  if (!result.fired.length) { box.hidden = true; return; }
  box.hidden = false;
  const b = el("div", "banner good");
  b.appendChild(el("strong", "", "Alerts"));
  const ul = el("ul");
  for (const f of result.fired) ul.appendChild(el("li", "", f.message));
  b.appendChild(ul);
  box.appendChild(b);
}

function renderTotals(snap) {
  const box = document.getElementById("totals");
  box.replaceChildren();
  const grid = el("div", "totals");
  const stat = (label, value, cls) => {
    const s = el("div", "stat");
    s.appendChild(el("div", "v " + (cls || ""), value));
    s.appendChild(el("div", "l", label));
    grid.appendChild(s);
  };
  stat("Market value (CAD)", money.format(snap.market_value_base));
  stat("Cost (CAD)", money.format(snap.cost_base));
  stat("Total P/L (CAD)", (snap.total_pl_base >= 0 ? "+" : "") + money.format(snap.total_pl_base), tone(snap.total_pl_base));
  stat("Today (CAD)", (snap.day_pl_base >= 0 ? "+" : "") + money.format(snap.day_pl_base), tone(snap.day_pl_base));
  stat("USD/CAD", snap.usdcad === null ? "n/a" : priceFmt.format(snap.usdcad));
  box.appendChild(grid);
}

function newsCell(line) {
  const items = line.news || [];
  const body = el("div");
  if (!items.length) body.appendChild(el("div", "muted", "Nothing in the last week"));
  const ul = el("ul");
  for (const n of items) {
    const li = el("li");
    const when = new Date(n.published).toLocaleDateString("en-CA", { month: "short", day: "numeric" });
    li.appendChild(el("span", "badge", when + (n.kind === "8-K" ? " 8-K" + (n.material ? " (material)" : "") : "") + " "));
    if (typeof n.url === "string" && n.url.startsWith("https://")) {
      const a = el("a", "", n.title);
      a.href = n.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      li.appendChild(a);
    } else {
      li.appendChild(el("span", "", n.title));
    }
    ul.appendChild(li);
  }
  body.appendChild(ul);
  for (const w of line.news_warnings || []) body.appendChild(el("div", "muted", w));
  const material = items.filter((n) => n.material).length;
  return details("News (" + items.length + (material ? ", " + material + " material" : "") + ")", body);
}

function row(line) {
  const tr = el("tr", "row");
  const symCell = el("td");
  symCell.appendChild(el("span", "sym", line.symbol));
  symCell.appendChild(el("span", "badge", " " + line.currency + (line.instrument === "etf" ? " ETF" : "")));
  for (const f of line.flags || []) symCell.appendChild(el("div", "badge", f));
  tr.appendChild(symCell);
  tr.appendChild(el("td", "", sharesFmt.format(line.shares)));
  tr.appendChild(el("td", "", priceFmt.format(line.avg_entry)));
  tr.appendChild(el("td", "", line.price === null ? "n/a" : priceFmt.format(line.price)));
  const pl = el("td", tone(line.total_pl));
  pl.appendChild(document.createTextNode(signed(line.total_pl)));
  pl.appendChild(el("div", "badge", pct(line.total_pl_pct)));
  tr.appendChild(pl);
  tr.appendChild(el("td", tone(line.day_pl), signed(line.day_pl)));

  const sig = line.signal;
  const sigCell = el("td", "left");
  sigCell.appendChild(chip(sig.action, SIGNAL_TONE[sig.action] || ""));
  if (sig.recovery_needed !== null && sig.recovery_needed !== undefined) {
    sigCell.appendChild(el("div", "badge", "needs +" + (sig.recovery_needed * 100).toFixed(1) + "% to reach entry"));
  }
  sigCell.appendChild(details("Why", reasonList(sig.reasons)));
  tr.appendChild(sigCell);

  const th = line.thesis;
  const thCell = el("td", "left");
  if (th) {
    thCell.appendChild(chip(th.status, THESIS_TONE[th.status] || ""));
    if (th.basis && th.status !== "N/A") thCell.appendChild(el("span", "badge", " " + th.basis));
    thCell.appendChild(details("Why", reasonList(th.reasons)));
  } else {
    thCell.appendChild(el("span", "muted", "n/a"));
  }
  tr.appendChild(thCell);

  const newsTd = el("td", "left");
  newsTd.appendChild(newsCell(line));
  tr.appendChild(newsTd);
  ROW_LABELS.forEach((label, i) => tr.children[i].setAttribute("data-label", label));
  return tr;
}

function accountCard(acct) {
  const card = el("div", "card");
  card.appendChild(el("h2", "", acct.account + "  " + money.format(acct.market_value_base) +
    "  (" + (acct.total_pl_base >= 0 ? "+" : "") + money.format(acct.total_pl_base) + ")"));
  if (acct.needed_by && acct.months_to_need !== null && acct.months_to_need <= NEEDED_SOON_MONTHS) {
    const msg = acct.months_to_need < 0
      ? acct.account + " money was expected to be needed by " + acct.needed_by + "."
      : acct.account + " money is expected to be needed in about " + acct.months_to_need + " months (" + acct.needed_by + ").";
    card.appendChild(el("div", "banner warn", msg + " Individual stocks can fall a long way before a fixed date, which could force a sale at a loss."));
  }
  const wrap = el("div", "table-wrap");
  const table = el("table");
  const head = el("tr", "head");
  [["Holding", ""], ["Shares", ""], ["Avg entry", ""], ["Price", ""], ["P/L", ""], ["Today", ""],
   ["Signal", "left"], ["Thesis", "left"], ["News", "left"]].forEach(([t, c]) => head.appendChild(el("th", c, t)));
  table.appendChild(head);
  for (const line of acct.lines) table.appendChild(row(line));
  wrap.appendChild(table);
  card.appendChild(wrap);
  return card;
}

function renderPositions(snap) {
  renderTotals(snap);
  const box = document.getElementById("accounts");
  box.replaceChildren(...snap.accounts.map(accountCard));
  const notes = document.getElementById("notes");
  notes.replaceChildren(...(snap.notes || []).map((n) => el("div", "", n)));
}

async function load() {
  const status = document.getElementById("status");
  const btn = document.getElementById("refresh");
  btn.disabled = true;
  status.textContent = "Loading";
  try {
    const [clock, snap] = await Promise.all([
      getJson("/api/market/clock"),
      getJson("/api/positions?news=true"),
    ]);
    renderClock(clock);
    renderPositions(snap);
    status.textContent = "Updated " + new Date().toLocaleTimeString("en-CA", { hour: "numeric", minute: "2-digit" });
    try {
      const alerts = await getJson("/api/alerts/check", { method: "POST", headers: { "X-OpenAlpha": "1" } });
      renderAlerts(alerts);
    } catch (e) {
      status.textContent += " (alerts unavailable: " + e.message + ")";
    }
  } catch (e) {
    status.textContent = "Could not load: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("refresh").addEventListener("click", load);
load();
