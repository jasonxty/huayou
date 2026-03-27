"""SQLite storage for OHLCV data, indicators, agent runs, and briefs."""

import json
import sqlite3
from pathlib import Path

import pandas as pd

import config


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
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
    """)
    conn.commit()


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
        (run_date, agent, score, json.dumps(output, ensure_ascii=False)),
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
         json.dumps(agent_summary, ensure_ascii=False)),
    )
    conn.commit()
