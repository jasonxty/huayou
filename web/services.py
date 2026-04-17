"""Business logic for the dashboard — extracted from alert_history.py."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import config
from data.store import (
    load_alerts, load_alerts_with_ids, load_position, load_trade_log,
    load_trade_log_with_ids, load_t0_trades, load_ohlcv,
    load_decision_notes,
)
from monitor import fetch_realtime_quote


ALERT_ICONS = {
    "sell_zone_1": ("\U0001f534", "Sell #1", "#e53935"),
    "sell_zone_2": ("\U0001f534", "Sell #2", "#c62828"),
    "buy_zone": ("\U0001f7e2", "Buy Dip", "#2e7d32"),
    "stop_loss": ("\u26d4", "Stop Loss", "#b71c1c"),
    "breakout": ("\u26a1", "Breakout", "#1565c0"),
}


def get_latest_price(conn: sqlite3.Connection) -> tuple[float, str]:
    """Return (price, date_label). Tries real-time first, falls back to DB."""
    quote = fetch_realtime_quote()
    if quote and quote.price > 0:
        return quote.price, datetime.now().strftime("%Y-%m-%d %H:%M")

    ohlcv = load_ohlcv(conn)
    if not ohlcv.empty:
        return float(ohlcv.iloc[-1]["close"]), str(ohlcv.iloc[-1]["date"].date())
    return 0.0, "N/A"


def get_portfolio_data(conn: sqlite3.Connection, latest_price: float) -> dict:
    """Compute portfolio overview numbers."""
    position = load_position(conn)
    trades = load_trade_log(conn)
    t0_trades = load_t0_trades(conn)

    qty = position["quantity"] if position else 0
    cost = position["cost"] if position else 0
    total_invested = sum(t["amount"] for t in trades if t["direction"] == "BUY")
    total_sold = sum(t["amount"] for t in trades if t["direction"] == "SELL")
    total_buy_fees = sum(t["fee"] for t in trades if t["direction"] == "BUY")
    total_sell_fees = sum(t["fee"] for t in trades if t["direction"] == "SELL")
    market_value = qty * latest_price
    est_sell_fee = config.calc_trade_fee(market_value, "SELL") if market_value > 0 else 0
    total_fees = total_buy_fees + total_sell_fees + est_sell_fee
    pnl = market_value - total_invested - total_fees + total_sold
    pnl_pct = pnl / total_invested * 100 if total_invested > 0 else 0
    t0_total_profit = sum(t["profit"] for t in t0_trades)

    return {
        "qty": qty,
        "cost": cost,
        "latest_price": latest_price,
        "market_value": market_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "total_invested": total_invested,
        "total_sold": total_sold,
        "total_buy_fees": total_buy_fees,
        "total_sell_fees": total_sell_fees,
        "est_sell_fee": est_sell_fee,
        "total_fees": total_fees,
        "t0_total_profit": t0_total_profit,
    }


def get_trades_with_ids(conn: sqlite3.Connection) -> list[dict]:
    return load_trade_log_with_ids(conn)


def get_t0_trades(conn: sqlite3.Connection) -> list[dict]:
    return load_t0_trades(conn)


def get_alert_stats(conn: sqlite3.Connection) -> dict:
    """Compute alert summary statistics."""
    alerts = load_alerts(conn)
    return {
        "alerts": alerts,
        "total": len(alerts),
        "sell_count": sum(1 for a in alerts if "sell" in (a["alert_type"] or "")),
        "buy_count": sum(1 for a in alerts if a["alert_type"] == "buy_zone"),
        "stop_count": sum(1 for a in alerts if a["alert_type"] == "stop_loss"),
        "breakout_count": sum(1 for a in alerts if a["alert_type"] == "breakout"),
        "unique_days": len({a["alert_date"] for a in alerts}),
    }


def get_brief_list(conn: sqlite3.Connection, limit: int = 60) -> list[dict]:
    """Load brief history for display."""
    rows = conn.execute(
        """SELECT date, action, confidence, risk_level, brief_text
           FROM briefs ORDER BY date DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    cols = ["date", "action", "confidence", "risk_level", "brief_text"]
    return [dict(zip(cols, r)) for r in rows]


def get_brief_detail(conn: sqlite3.Connection, brief_date: str) -> dict | None:
    """Load a single brief by date."""
    row = conn.execute(
        """SELECT date, action, confidence, risk_level, brief_text, agent_summary_json
           FROM briefs WHERE date = ?""",
        (brief_date,),
    ).fetchone()
    if not row:
        return None
    cols = ["date", "action", "confidence", "risk_level", "brief_text", "agent_summary_json"]
    return dict(zip(cols, row))


def get_monitor_status() -> dict:
    """Check if monitor process is likely running (simple heuristic)."""
    import subprocess
    try:
        result = subprocess.run(
            ["pgrep", "-f", "monitor.py"],
            capture_output=True, timeout=3, check=False,
        )
        running = result.returncode == 0
    except Exception:
        running = False
    return {"running": running}


# ── Comparison logic ─────────────────────────────────────────────────


def _parse_brief_action(action: str | None) -> str:
    """Normalize brief action to BUY/SELL/HOLD."""
    if not action:
        return "HOLD"
    token = action.split("(")[0].strip().split()[0].upper()
    if token in ("BUY", "SELL", "HOLD"):
        return token
    return "HOLD"


def get_strategic_comparison(conn: sqlite3.Connection) -> dict:
    """Compare morning brief recommendations vs. user's actual trades.

    For each brief date, simulate what would have happened if the user
    followed the system (buy/sell at that day's open price using same
    capital as the user's actual total_invested).

    Returns dict with rows, stats, and dual-track PnL.
    """
    briefs = conn.execute(
        "SELECT date, action, confidence FROM briefs ORDER BY date"
    ).fetchall()
    if not briefs:
        return {"rows": [], "stats": {}, "system_pnl": 0, "user_pnl": 0,
                "brief_count": 0}

    trades = load_trade_log(conn)
    trades_by_date: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        trades_by_date[t["trade_date"]].append(t)

    ohlcv_df = load_ohlcv(conn)
    ohlcv_map: dict[str, dict] = {}
    if not ohlcv_df.empty:
        for _, row in ohlcv_df.iterrows():
            d = str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"])[:10]
            ohlcv_map[d] = {"open": float(row["open"]), "close": float(row["close"])}

    notes = load_decision_notes(conn)

    first_brief_date = briefs[0][0]

    pre_trades = [t for t in trades if t["trade_date"] <= first_brief_date]
    sys_shares = 0
    sys_cost = 0.0
    sys_cash = 0.0
    for t in sorted(pre_trades, key=lambda x: x["trade_date"]):
        if t["direction"] == "BUY":
            new_qty = sys_shares + t["quantity"]
            if new_qty > 0:
                sys_cost = (sys_cost * sys_shares + t["price"] * t["quantity"]) / new_qty
            sys_shares = new_qty
            sys_cash -= (t["amount"] + t["fee"])
        else:
            sys_shares -= t["quantity"]
            sys_cash += (t["amount"] - t["fee"])

    total_invested = sum(t["amount"] for t in trades if t["direction"] == "BUY")
    initial_cost = sys_cost * sys_shares if sys_shares > 0 else (total_invested if total_invested > 0 else 100000)

    user_total_buy = sum(t["amount"] + t["fee"] for t in trades if t["direction"] == "BUY")
    user_total_sell = sum(t["amount"] - t["fee"] for t in trades if t["direction"] == "SELL")

    rows = []
    match_count = 0
    diverge_count = 0
    sys_win = 0
    user_win = 0

    for brief_date, brief_action_raw, confidence in briefs:
        sys_action = _parse_brief_action(brief_action_raw)
        day_ohlcv = ohlcv_map.get(brief_date)
        open_price = day_ohlcv["open"] if day_ohlcv else 0
        close_price = day_ohlcv["close"] if day_ohlcv else 0

        sys_day_pnl = 0.0
        if open_price > 0:
            if sys_action == "BUY" and sys_cash > 0:
                can_buy = int(sys_cash / (open_price * 1.0003)) // 100 * 100
                if can_buy > 0:
                    amt = can_buy * open_price
                    fee = config.calc_trade_fee(amt, "BUY")
                    sys_cash -= (amt + fee)
                    if sys_shares > 0:
                        sys_cost = (sys_cost * sys_shares + open_price * can_buy) / (sys_shares + can_buy)
                    else:
                        sys_cost = open_price
                    sys_shares += can_buy
            elif sys_action == "SELL" and sys_shares > 0:
                sell_qty = sys_shares // 2 if sys_shares >= 200 else sys_shares
                revenue = sell_qty * open_price
                fee = config.calc_trade_fee(revenue, "SELL")
                sys_day_pnl = (open_price - sys_cost) * sell_qty - fee
                sys_cash += (revenue - fee)
                sys_shares -= sell_qty
                if sys_shares == 0:
                    sys_cost = 0.0

        day_trades = trades_by_date.get(brief_date, [])
        user_action = "No Action"
        user_day_detail = ""
        if day_trades:
            dirs = [t["direction"] for t in day_trades]
            if "BUY" in dirs and "SELL" in dirs:
                user_action = "BUY+SELL"
            elif "BUY" in dirs:
                user_action = "BUY"
            else:
                user_action = "SELL"
            prices = [f"¥{t['price']:.2f}" for t in day_trades]
            user_day_detail = ", ".join(prices)

        matched = (
            (sys_action == "BUY" and "BUY" in (user_action,))
            or (sys_action == "SELL" and "SELL" in (user_action,))
            or (sys_action == "HOLD" and user_action == "No Action")
        )
        if user_action != "No Action" and sys_action != "HOLD":
            if matched:
                match_count += 1
            else:
                diverge_count += 1
        elif sys_action != "HOLD" and user_action == "No Action":
            diverge_count += 1

        day_user_pnl = 0.0
        for t in day_trades:
            if t["direction"] == "SELL":
                day_user_pnl += t["amount"] - t["fee"]
            else:
                day_user_pnl -= t["amount"] + t["fee"]

        if sys_day_pnl > 0:
            sys_win += 1
        if day_user_pnl > 0:
            user_win += 1

        note_key = f"{brief_date}|strategic|brief"
        note_text = notes.get(note_key, "")

        rows.append({
            "date": brief_date,
            "sys_action": sys_action,
            "confidence": confidence or 0,
            "user_action": user_action,
            "user_detail": user_day_detail,
            "sys_day_pnl": round(sys_day_pnl, 1),
            "user_day_pnl": round(day_user_pnl, 1),
            "delta": round(sys_day_pnl - day_user_pnl, 1),
            "matched": matched,
            "note": note_text,
        })

    latest_price, _ = get_latest_price(conn)
    if latest_price <= 0 and ohlcv_map:
        last_date = max(ohlcv_map.keys())
        latest_price = ohlcv_map[last_date]["close"]

    sys_market_value = sys_shares * latest_price
    sys_total_value = sys_cash + sys_market_value
    if sys_shares > 0 and latest_price > 0:
        est_sell_fee = config.calc_trade_fee(sys_market_value, "SELL")
        sys_total_value -= est_sell_fee
    sys_pnl = sys_total_value - initial_cost + sum(r["sys_day_pnl"] for r in rows if r["sys_day_pnl"] != 0 and sys_shares == 0)

    sys_unrealized = (latest_price - sys_cost) * sys_shares if sys_shares > 0 and latest_price > 0 else 0
    sys_realized = sum(r["sys_day_pnl"] for r in rows)
    sys_pnl = round(sys_realized + sys_unrealized, 1)

    pos = load_position(conn)
    user_qty = pos["quantity"] if pos else 0
    user_market = user_qty * latest_price if latest_price > 0 else 0
    user_est_fee = config.calc_trade_fee(user_market, "SELL") if user_market > 0 else 0
    user_pnl = user_market + user_total_sell - user_total_buy - user_est_fee

    total_signals = sum(1 for r in rows if r["sys_action"] != "HOLD")
    adopt_rate = match_count / total_signals * 100 if total_signals > 0 else 0

    rows.reverse()

    return {
        "rows": rows,
        "stats": {
            "total_signals": total_signals,
            "match_count": match_count,
            "diverge_count": diverge_count,
            "adopt_rate": round(adopt_rate, 0),
            "sys_win": sys_win,
            "user_win": user_win,
        },
        "system_pnl": round(sys_pnl, 1),
        "system_pnl_pct": round(sys_pnl / initial_cost * 100, 2) if initial_cost > 0 else 0,
        "user_pnl": round(user_pnl, 1),
        "user_pnl_pct": round(user_pnl / total_invested * 100, 2) if total_invested > 0 else 0,
        "initial_cost": initial_cost,
        "brief_count": len(briefs),
    }


def get_tactical_comparison(conn: sqlite3.Connection) -> dict:
    """Compare T+0 alerts vs. user's actual T+0 trades.

    Matches alerts to T+0 trades by date. For each alert, checks if
    the user executed a similar T+0 trade on the same day.
    """
    alerts = load_alerts_with_ids(conn)
    t0_trades = load_t0_trades(conn)
    notes = load_decision_notes(conn)

    t0_by_date: dict[str, list[dict]] = defaultdict(list)
    for t in t0_trades:
        t0_by_date[t["trade_date"]].append(t)

    rows = []
    follow_count = 0
    skip_count = 0
    alert_total_pnl = 0.0
    user_t0_total_pnl = 0.0

    for a in alerts:
        alert_date = a["alert_date"]
        alert_type = a["alert_type"] or ""
        is_sell = "sell" in alert_type
        is_buy = alert_type == "buy_zone"

        if not (is_sell or is_buy):
            continue

        alert_pnl = 0.0
        if is_sell and a["target_price"] and a["cost"]:
            alert_pnl = (a["target_price"] - a["cost"]) * (a["quantity"] or 0)
            sell_fee = config.calc_trade_fee(a["target_price"] * (a["quantity"] or 0), "SELL")
            alert_pnl -= sell_fee

        day_t0 = t0_by_date.get(alert_date, [])
        followed = len(day_t0) > 0
        user_day_pnl = sum(t["profit"] for t in day_t0) if followed else 0.0

        if followed:
            follow_count += 1
        else:
            skip_count += 1

        alert_total_pnl += alert_pnl
        user_t0_total_pnl += user_day_pnl

        note_key = f"{alert_date}|tactical|{a['id']}"
        note_text = notes.get(note_key, "")

        rows.append({
            "date": alert_date,
            "time": a["alert_time"],
            "alert_type": alert_type,
            "alert_id": a["id"],
            "target_price": a["target_price"] or 0,
            "quantity": a["quantity"] or 0,
            "zone": f"¥{a['zone_low']:.2f}~¥{a['zone_high']:.2f}" if a["zone_low"] else "",
            "followed": followed,
            "user_detail": f"{len(day_t0)} T+0 trade(s)" if followed else "Skipped",
            "alert_pnl": round(alert_pnl, 1),
            "user_pnl": round(user_day_pnl, 1),
            "delta": round(alert_pnl - user_day_pnl, 1),
            "note": note_text,
        })

    total_actionable = follow_count + skip_count
    follow_rate = follow_count / total_actionable * 100 if total_actionable > 0 else 0

    return {
        "rows": rows,
        "stats": {
            "total_alerts": total_actionable,
            "follow_count": follow_count,
            "skip_count": skip_count,
            "follow_rate": round(follow_rate, 0),
        },
        "alert_total_pnl": round(alert_total_pnl, 1),
        "user_t0_total_pnl": round(user_t0_total_pnl, 1),
    }


def get_comparison_hero(conn: sqlite3.Connection) -> dict:
    """Aggregate data for the dual-track hero comparison cards."""
    strat = get_strategic_comparison(conn)
    tact = get_tactical_comparison(conn)

    sys_total = strat["system_pnl"] + tact["alert_total_pnl"]
    user_total = strat["user_pnl"] + tact["user_t0_total_pnl"]
    delta = sys_total - user_total

    return {
        "system_pnl": round(sys_total, 1),
        "user_pnl": round(user_total, 1),
        "delta": round(delta, 1),
        "system_pnl_pct": strat["system_pnl_pct"],
        "user_pnl_pct": strat["user_pnl_pct"],
        "leader": "system" if delta > 0 else ("user" if delta < 0 else "tie"),
        "brief_count": strat["brief_count"],
        "alert_count": tact["stats"]["total_alerts"],
        "adopt_rate": strat["stats"]["adopt_rate"],
        "follow_rate": tact["stats"]["follow_rate"],
    }
