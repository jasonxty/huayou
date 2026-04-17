"""SQLite storage for OHLCV data, indicators, agent runs, and briefs."""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

import config


class _NumpyEncoder(json.JSONEncoder):
    """Handle numpy types that stdlib json can't serialize."""
    def default(self, o):
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            date TEXT PRIMARY KEY,
            open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, turnover REAL, turnover_rate REAL
        );

        CREATE TABLE IF NOT EXISTS indicators (
            date TEXT PRIMARY KEY,
            ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL, ma120 REAL, ma250 REAL,
            macd REAL, macd_signal REAL, macd_hist REAL,
            rsi6 REAL, rsi12 REAL, rsi24 REAL,
            kdj_k REAL, kdj_d REAL, kdj_j REAL,
            boll_upper REAL, boll_mid REAL, boll_lower REAL,
            obv REAL, atr14 REAL
        );

        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT, agent TEXT, score REAL,
            output_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS briefs (
            date TEXT PRIMARY KEY,
            action TEXT, confidence REAL, risk_level TEXT,
            brief_text TEXT, agent_summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT, train_start TEXT, train_end TEXT,
            test_start TEXT, test_end TEXT,
            win_rate REAL, sharpe REAL, max_drawdown REAL,
            profit_factor REAL, total_trades INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_date TEXT, strategy TEXT, action TEXT,
            signal_price REAL, exec_price REAL,
            filled INTEGER DEFAULT 1,
            outcome REAL,
            closed_date TEXT
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, entry_price REAL, quantity INTEGER,
            entry_date TEXT, notes TEXT
        );

        CREATE TABLE IF NOT EXISTS t0_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            ticker TEXT,
            sell_price REAL,
            buy_price REAL,
            quantity INTEGER,
            profit REAL,
            cost_before REAL,
            cost_after REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS expert_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expert_id TEXT,
            expert_name TEXT,
            post_date TEXT,
            title TEXT,
            content TEXT,
            url TEXT,
            sentiment_score REAL,
            signals_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(expert_id, url)
        );

        CREATE TABLE IF NOT EXISTS trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            ticker TEXT,
            direction TEXT,
            price REAL,
            quantity INTEGER,
            amount REAL,
            fee REAL DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_date TEXT,
            alert_time TEXT,
            alert_type TEXT,
            price REAL,
            change_pct REAL,
            action TEXT,
            quantity INTEGER,
            target_price REAL,
            zone_low REAL,
            zone_high REAL,
            cost REAL,
            pnl_pct REAL,
            strategy TEXT,
            popup_sent INTEGER DEFAULT 0,
            wechat_sent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS decision_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_date TEXT,
            note_type TEXT,
            ref_id TEXT,
            note_text TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(note_date, note_type, ref_id)
        );
    """)
    conn.commit()


def load_position(conn: sqlite3.Connection, ticker: str = config.TICKER) -> dict | None:
    """Load the latest position for a ticker. Returns dict or None."""
    row = conn.execute(
        "SELECT ticker, entry_price, quantity, entry_date, notes "
        "FROM positions WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if row is None:
        return None
    return {
        "ticker": row[0], "cost": float(row[1]),
        "quantity": int(row[2]), "entry_date": row[3], "notes": row[4],
    }


def save_position(conn: sqlite3.Connection, ticker: str, cost: float,
                  quantity: int, entry_date: str = "", notes: str = "") -> None:
    """Upsert position for a ticker (keeps only the latest)."""
    conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
    conn.execute(
        "INSERT INTO positions (ticker, entry_price, quantity, entry_date, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticker, cost, quantity, entry_date, notes),
    )
    conn.commit()


def record_t0_trade(conn: sqlite3.Connection, sell_price: float, buy_price: float,
                    quantity: int, ticker: str = config.TICKER) -> dict:
    """Record a completed T+0 trade and auto-update position cost.

    A T+0 trade = sell `quantity` shares at `sell_price`, buy back at `buy_price`.
    Profit = (sell - buy) * quantity * (1 - fee).
    New cost = old_cost - profit / total_shares.

    Returns dict with trade details and updated position.
    """
    position = load_position(conn, ticker)
    if position is None:
        raise ValueError(f"No position found for {ticker}")

    gross = (sell_price - buy_price) * quantity
    sell_fee = config.calc_trade_fee(sell_price * quantity, "SELL")
    buy_fee = config.calc_trade_fee(buy_price * quantity, "BUY")
    fee = sell_fee + buy_fee
    profit = gross - fee
    cost_before = position["cost"]
    total_shares = position["quantity"]
    cost_after = round(cost_before - profit / total_shares, 4)

    conn.execute(
        "INSERT INTO t0_trades (trade_date, ticker, sell_price, buy_price, "
        "quantity, profit, cost_before, cost_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pd.Timestamp.now().strftime("%Y-%m-%d"), ticker,
         sell_price, buy_price, quantity, round(profit, 2),
         cost_before, cost_after),
    )
    save_position(conn, ticker, cost_after, total_shares)

    return {
        "sell_price": sell_price, "buy_price": buy_price,
        "quantity": quantity, "profit": round(profit, 2),
        "fee": round(fee, 2), "cost_before": cost_before,
        "cost_after": cost_after,
    }


def save_trade(conn: sqlite3.Connection, trade_date: str, direction: str,
               price: float, quantity: int, notes: str = "",
               ticker: str = config.TICKER) -> None:
    """Record a buy/sell trade in the trade log."""
    amount = price * quantity
    fee = config.calc_trade_fee(amount, direction)
    conn.execute(
        """INSERT INTO trade_log
           (trade_date, ticker, direction, price, quantity, amount, fee, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (trade_date, ticker, direction, price, quantity,
         round(amount, 2), fee, notes),
    )
    conn.commit()


def load_trade_log(conn: sqlite3.Connection,
                   ticker: str = config.TICKER) -> list[dict]:
    """Load all trades for a ticker, newest first."""
    rows = conn.execute(
        """SELECT trade_date, direction, price, quantity, amount, fee, notes
           FROM trade_log WHERE ticker = ? ORDER BY trade_date DESC, id DESC""",
        (ticker,),
    ).fetchall()
    cols = ["trade_date", "direction", "price", "quantity", "amount", "fee", "notes"]
    return [dict(zip(cols, r)) for r in rows]


def delete_trade(conn: sqlite3.Connection, trade_id: int) -> bool:
    """Delete a trade by ID. Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM trade_log WHERE id = ?", (trade_id,))
    conn.commit()
    return cur.rowcount > 0


def load_trade_log_with_ids(conn: sqlite3.Connection,
                            ticker: str = config.TICKER) -> list[dict]:
    """Load all trades with row IDs (for delete support)."""
    rows = conn.execute(
        """SELECT id, trade_date, direction, price, quantity, amount, fee, notes
           FROM trade_log WHERE ticker = ? ORDER BY trade_date DESC, id DESC""",
        (ticker,),
    ).fetchall()
    cols = ["id", "trade_date", "direction", "price", "quantity", "amount", "fee", "notes"]
    return [dict(zip(cols, r)) for r in rows]


def load_t0_trades(conn: sqlite3.Connection,
                   ticker: str = config.TICKER) -> list[dict]:
    """Load T+0 trade history, newest first."""
    rows = conn.execute(
        """SELECT trade_date, sell_price, buy_price, quantity,
                  profit, cost_before, cost_after
           FROM t0_trades WHERE ticker = ? ORDER BY trade_date DESC, id DESC""",
        (ticker,),
    ).fetchall()
    cols = ["trade_date", "sell_price", "buy_price", "quantity",
            "profit", "cost_before", "cost_after"]
    return [dict(zip(cols, r)) for r in rows]


def save_alert(conn: sqlite3.Connection, alert_type: str, price: float,
               change_pct: float, action: str, quantity: int,
               target_price: float, zone_low: float, zone_high: float,
               cost: float, pnl_pct: float, strategy: str,
               popup_sent: bool = False, wechat_sent: bool = False) -> None:
    """Persist a fired trading alert for history review."""
    from datetime import datetime
    now = datetime.now()
    conn.execute(
        """INSERT INTO alerts
           (alert_date, alert_time, alert_type, price, change_pct,
            action, quantity, target_price, zone_low, zone_high,
            cost, pnl_pct, strategy, popup_sent, wechat_sent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
         alert_type, price, change_pct,
         action, quantity, target_price, zone_low, zone_high,
         cost, pnl_pct, strategy,
         1 if popup_sent else 0, 1 if wechat_sent else 0),
    )
    conn.commit()


def load_alerts(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Load recent alerts for display."""
    rows = conn.execute(
        """SELECT alert_date, alert_time, alert_type, price, change_pct,
                  action, quantity, target_price, zone_low, zone_high,
                  cost, pnl_pct, strategy, popup_sent, wechat_sent
           FROM alerts ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    cols = ["alert_date", "alert_time", "alert_type", "price", "change_pct",
            "action", "quantity", "target_price", "zone_low", "zone_high",
            "cost", "pnl_pct", "strategy", "popup_sent", "wechat_sent"]
    return [dict(zip(cols, r)) for r in rows]


def save_decision_note(conn: sqlite3.Connection, note_date: str,
                       note_type: str, ref_id: str, note_text: str) -> None:
    """Upsert a reflection note for a decision comparison row."""
    conn.execute(
        """INSERT INTO decision_notes (note_date, note_type, ref_id, note_text)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(note_date, note_type, ref_id)
           DO UPDATE SET note_text = excluded.note_text""",
        (note_date, note_type, ref_id, note_text),
    )
    conn.commit()


def load_decision_notes(conn: sqlite3.Connection) -> dict[str, str]:
    """Load all decision notes as {date|type|ref_id: note_text} lookup."""
    rows = conn.execute(
        "SELECT note_date, note_type, ref_id, note_text FROM decision_notes"
    ).fetchall()
    return {f"{r[0]}|{r[1]}|{r[2]}": r[3] for r in rows}


def load_alerts_with_ids(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Load recent alerts with row IDs for matching."""
    rows = conn.execute(
        """SELECT id, alert_date, alert_time, alert_type, price, change_pct,
                  action, quantity, target_price, zone_low, zone_high,
                  cost, pnl_pct, strategy, popup_sent, wechat_sent
           FROM alerts ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    cols = ["id", "alert_date", "alert_time", "alert_type", "price", "change_pct",
            "action", "quantity", "target_price", "zone_low", "zone_high",
            "cost", "pnl_pct", "strategy", "popup_sent", "wechat_sent"]
    return [dict(zip(cols, r)) for r in rows]


def save_ohlcv(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Upsert OHLCV rows. Returns number of new rows inserted."""
    if df.empty:
        return 0

    rows_before = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]

    records = []
    for _, row in df.iterrows():
        if pd.isna(row.get("volume")) or row.get("volume", 0) == 0:
            continue
        records.append((
            str(row["date"]),
            float(row["open"]), float(row["high"]),
            float(row["low"]), float(row["close"]),
            int(row["volume"]),
            float(row.get("turnover", 0)),
            float(row.get("turnover_rate", 0)),
        ))

    conn.executemany(
        """INSERT OR REPLACE INTO ohlcv
           (date, open, high, low, close, volume, turnover, turnover_rate)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        records,
    )
    conn.commit()

    rows_after = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
    return rows_after - rows_before


def get_latest_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM ohlcv").fetchone()
    return row[0] if row and row[0] else None


def load_ohlcv(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM ohlcv ORDER BY date", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def save_indicators(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    """Save computed indicators. Expects a DataFrame with date + indicator columns."""
    cols = [
        "date", "ma5", "ma10", "ma20", "ma60", "ma120", "ma250",
        "macd", "macd_signal", "macd_hist",
        "rsi6", "rsi12", "rsi24",
        "kdj_k", "kdj_d", "kdj_j",
        "boll_upper", "boll_mid", "boll_lower",
        "obv", "atr14",
    ]
    available = [c for c in cols if c in df.columns]
    sub = df[available].copy()
    sub["date"] = sub["date"].astype(str)

    placeholders = ", ".join(["?"] * len(available))
    col_names = ", ".join(available)
    conn.executemany(
        f"INSERT OR REPLACE INTO indicators ({col_names}) VALUES ({placeholders})",
        sub.values.tolist(),
    )
    conn.commit()


def load_indicators(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM indicators ORDER BY date", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def save_agent_run(conn: sqlite3.Connection, run_date: str, agent: str,
                   score: float, output: dict) -> None:
    conn.execute(
        "INSERT INTO agent_runs (run_date, agent, score, output_json) VALUES (?, ?, ?, ?)",
        (run_date, agent, score, json.dumps(output, ensure_ascii=False, cls=_NumpyEncoder)),
    )
    conn.commit()


def save_brief(conn: sqlite3.Connection, date: str, action: str,
               confidence: float, risk_level: str, brief_text: str,
               agent_summary: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO briefs
           (date, action, confidence, risk_level, brief_text, agent_summary_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (date, action, confidence, risk_level, brief_text,
         json.dumps(agent_summary, ensure_ascii=False, cls=_NumpyEncoder)),
    )
    conn.commit()
