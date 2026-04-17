"""华友钴业 Dashboard — FastAPI + HTMX web app."""

from __future__ import annotations

import sys
import time
from datetime import datetime, date
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config
from data.store import (
    get_connection, init_db, save_trade, save_position,
    load_position, record_t0_trade, delete_trade,
)
from web.services import (
    ALERT_ICONS, get_latest_price, get_portfolio_data,
    get_trades_with_ids, get_t0_trades, get_alert_stats,
    get_brief_list, get_brief_detail, get_monitor_status,
    get_comparison_hero, get_strategic_comparison, get_tactical_comparison,
)
from data.store import save_decision_note

app = FastAPI(title="Huayou Cobalt Dashboard")

_WEB_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=_WEB_DIR / "static"), name="static")

_jinja_env = Environment(
    loader=FileSystemLoader(str(_WEB_DIR / "templates")),
    autoescape=True,
    auto_reload=True,
)
_jinja_env.globals["alert_icons"] = ALERT_ICONS

# In-memory quote cache (TTL=30s)
_quote_cache: dict = {"price": 0.0, "date": "N/A", "ts": 0.0}


def _get_cached_price() -> tuple[float, str]:
    now = time.time()
    if now - _quote_cache["ts"] < 30 and _quote_cache["price"] > 0:
        return _quote_cache["price"], _quote_cache["date"]

    conn = get_connection()
    init_db(conn)
    try:
        price, date_label = get_latest_price(conn)
    finally:
        conn.close()

    _quote_cache["price"] = price
    _quote_cache["date"] = date_label
    _quote_cache["ts"] = now
    return price, date_label


def _render(template_name: str, **ctx) -> HTMLResponse:
    tpl = _jinja_env.get_template(template_name)
    html = tpl.render(**ctx)
    return HTMLResponse(html)


def _base_ctx() -> dict:
    return {
        "ticker": config.TICKER,
        "ticker_name": config.TICKER_NAME,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": date.today().isoformat(),
        "monitor_status": get_monitor_status(),
        "flash_message": None,
        "flash_type": None,
    }


# ── Full page routes ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    ctx = _base_ctx()
    conn = get_connection()
    init_db(conn)
    try:
        price, price_date = _get_cached_price()
        ctx["p"] = get_portfolio_data(conn, price)
        ctx["price_date"] = price_date
        ctx["trades"] = get_trades_with_ids(conn)
        ctx["t0_trades"] = get_t0_trades(conn)
        ctx["alert_stats"] = get_alert_stats(conn)
        ctx["briefs"] = get_brief_list(conn)
        ctx["hero"] = get_comparison_hero(conn)
        ctx["strategic"] = get_strategic_comparison(conn)
        ctx["tactical"] = get_tactical_comparison(conn)
    finally:
        conn.close()
    return _render("dashboard.html", **ctx)


@app.get("/brief/{brief_date}", response_class=HTMLResponse)
async def brief_detail_page(brief_date: str):
    ctx = _base_ctx()
    conn = get_connection()
    init_db(conn)
    try:
        brief = get_brief_detail(conn, brief_date)
    finally:
        conn.close()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    action = brief.get("action") or "N/A"
    action_word = action.split("(")[0].strip() if "(" in action else action.split()[0]
    risk = brief.get("risk_level") or "N/A"

    ctx["brief"] = brief
    ctx["action_cls"] = {"BUY": "buy", "SELL": "sell", "HOLD": "hold"}.get(action_word, "")
    ctx["risk_cls"] = {"LOW": "low", "MEDIUM": "med", "HIGH": "high"}.get(risk, "")
    return _render("brief_detail.html", **ctx)


# ── HTMX partial / API routes ──────────────────────────────────────────

@app.get("/api/portfolio", response_class=HTMLResponse)
async def api_portfolio():
    ctx = _base_ctx()
    conn = get_connection()
    init_db(conn)
    try:
        price, price_date = _get_cached_price()
        ctx["p"] = get_portfolio_data(conn, price)
        ctx["price_date"] = price_date
    finally:
        conn.close()
    return _render("partials/portfolio.html", **ctx)


@app.get("/api/trades", response_class=HTMLResponse)
async def api_trades():
    conn = get_connection()
    init_db(conn)
    try:
        trades = get_trades_with_ids(conn)
    finally:
        conn.close()
    return _render("partials/trades.html", trades=trades)


@app.get("/api/t0-trades", response_class=HTMLResponse)
async def api_t0_trades():
    conn = get_connection()
    init_db(conn)
    try:
        t0_trades = get_t0_trades(conn)
    finally:
        conn.close()
    return _render("partials/t0_trades.html", t0_trades=t0_trades)


@app.get("/api/alerts", response_class=HTMLResponse)
async def api_alerts():
    conn = get_connection()
    init_db(conn)
    try:
        alert_stats = get_alert_stats(conn)
    finally:
        conn.close()
    return _render("partials/alerts.html", alert_stats=alert_stats)


@app.get("/api/briefs", response_class=HTMLResponse)
async def api_briefs():
    conn = get_connection()
    init_db(conn)
    try:
        briefs = get_brief_list(conn)
    finally:
        conn.close()
    return _render("partials/briefs.html", briefs=briefs)


@app.get("/api/comparison-hero", response_class=HTMLResponse)
async def api_comparison_hero():
    conn = get_connection()
    init_db(conn)
    try:
        hero = get_comparison_hero(conn)
    finally:
        conn.close()
    return _render("partials/comparison_hero.html", hero=hero)


@app.get("/api/strategic-comparison", response_class=HTMLResponse)
async def api_strategic_comparison():
    conn = get_connection()
    init_db(conn)
    try:
        strategic = get_strategic_comparison(conn)
    finally:
        conn.close()
    return _render("partials/strategic_comparison.html", strategic=strategic)


@app.get("/api/tactical-comparison", response_class=HTMLResponse)
async def api_tactical_comparison():
    conn = get_connection()
    init_db(conn)
    try:
        tactical = get_tactical_comparison(conn)
    finally:
        conn.close()
    return _render("partials/tactical_comparison.html", tactical=tactical)


@app.get("/api/monitor-status")
async def api_monitor_status():
    return get_monitor_status()


# ── Form actions ────────────────────────────────────────────────────────

@app.post("/trades", response_class=HTMLResponse)
async def create_trade(
    trade_date: str = Form(...),
    direction: str = Form(...),
    price: float = Form(...),
    quantity: int = Form(...),
    notes: str = Form(""),
):
    if direction not in ("BUY", "SELL"):
        raise HTTPException(status_code=422, detail="direction must be BUY or SELL")
    if price <= 0 or quantity <= 0:
        raise HTTPException(status_code=422, detail="price and quantity must be positive")

    conn = get_connection()
    init_db(conn)
    try:
        save_trade(conn, trade_date, direction, price, quantity, notes)
        _recalc_position(conn)
        trades = get_trades_with_ids(conn)
    finally:
        conn.close()
    return _render("partials/trades.html", trades=trades)


@app.post("/t0-trades", response_class=HTMLResponse)
async def create_t0_trade(
    sell_price: float = Form(...),
    buy_price: float = Form(...),
    quantity: int = Form(...),
):
    if sell_price <= 0 or buy_price <= 0 or quantity <= 0:
        raise HTTPException(status_code=422, detail="All values must be positive")

    conn = get_connection()
    init_db(conn)
    try:
        pos = load_position(conn)
        if not pos:
            raise HTTPException(status_code=400, detail="No position found — cannot record T+0 trade")
        record_t0_trade(conn, sell_price, buy_price, quantity)
        t0_trades = get_t0_trades(conn)
    finally:
        conn.close()
    return _render("partials/t0_trades.html", t0_trades=t0_trades)


@app.post("/notes")
async def create_note(
    note_date: str = Form(...),
    note_type: str = Form(...),
    ref_id: str = Form(...),
    note_text: str = Form(""),
):
    if note_type not in ("strategic", "tactical"):
        raise HTTPException(status_code=422, detail="note_type must be strategic or tactical")
    conn = get_connection()
    init_db(conn)
    try:
        save_decision_note(conn, note_date, note_type, ref_id, note_text)
    finally:
        conn.close()
    return HTMLResponse("ok")


@app.delete("/trades/{trade_id}", response_class=HTMLResponse)
async def remove_trade(trade_id: int):
    conn = get_connection()
    init_db(conn)
    try:
        deleted = delete_trade(conn, trade_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Trade not found")
        _recalc_position(conn)
        trades = get_trades_with_ids(conn)
    finally:
        conn.close()
    return _render("partials/trades.html", trades=trades)


def _recalc_position(conn) -> None:
    """Recompute position (qty, avg cost) from trade_log."""
    from data.store import load_trade_log
    trades = load_trade_log(conn)
    net_qty = 0
    net_cost = 0.0
    for t in reversed(trades):
        if t["direction"] == "BUY":
            new_qty = net_qty + t["quantity"]
            net_cost = (net_cost * net_qty + t["price"] * t["quantity"]) / new_qty if new_qty > 0 else t["price"]
            net_qty = new_qty
        else:
            net_qty -= t["quantity"]
            if net_qty < 0:
                net_qty = 0

    if net_qty > 0:
        save_position(conn, config.TICKER, round(net_cost, 4), net_qty)
    else:
        save_position(conn, config.TICKER, 0, 0)
