"""Tests for data.performance module — recommendation tracking."""

import sqlite3
import pytest
import pandas as pd
from data.performance import (
    compute_performance, format_performance, _is_correct,
    PerformanceSummary, BriefPerformance,
)
from data.store import init_db


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def _seed_ohlcv(conn, dates_prices: list[tuple[str, float]]):
    for d, p in dates_prices:
        conn.execute(
            "INSERT OR REPLACE INTO ohlcv (date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (d, p, p + 1, p - 1, p, 1000000),
        )
    conn.commit()


def _seed_brief(conn, date: str, action: str, confidence: int = 70):
    conn.execute(
        "INSERT INTO briefs (date, action, confidence, risk_level, brief_text, agent_summary_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (date, action, confidence, "MEDIUM", "test", "{}"),
    )
    conn.commit()


class TestIsCorrect:
    def test_buy_positive_return(self):
        assert _is_correct("BUY", 2.5) is True

    def test_buy_negative_return(self):
        assert _is_correct("BUY", -1.0) is False

    def test_sell_negative_return(self):
        assert _is_correct("SELL", -2.0) is True

    def test_sell_positive_return(self):
        assert _is_correct("SELL", 1.0) is False

    def test_hold_always_correct(self):
        assert _is_correct("HOLD", 5.0) is True
        assert _is_correct("HOLD", -5.0) is True
        assert _is_correct("HOLD (震荡观望)", 0.0) is True


class TestComputePerformance:
    def test_empty_db(self):
        conn = _make_db()
        summary = compute_performance(conn)
        assert summary.total_briefs == 0
        assert summary.details == []

    def test_single_brief_no_forward(self):
        conn = _make_db()
        _seed_ohlcv(conn, [("2026-03-20", 60.0)])
        _seed_brief(conn, "2026-03-20", "BUY")
        summary = compute_performance(conn)
        assert summary.total_briefs == 1
        assert summary.briefs_with_1d == 0

    def test_buy_correct_1d(self):
        conn = _make_db()
        _seed_ohlcv(conn, [
            ("2026-03-20", 60.0),
            ("2026-03-21", 62.0),
        ])
        _seed_brief(conn, "2026-03-20", "BUY")
        summary = compute_performance(conn)
        assert summary.briefs_with_1d == 1
        assert summary.hit_rate_1d == 100.0
        assert summary.avg_return_1d > 0

    def test_sell_correct_1d(self):
        conn = _make_db()
        _seed_ohlcv(conn, [
            ("2026-03-20", 60.0),
            ("2026-03-21", 58.0),
        ])
        _seed_brief(conn, "2026-03-20", "SELL")
        summary = compute_performance(conn)
        assert summary.hit_rate_1d == 100.0
        assert summary.avg_return_1d < 0

    def test_5d_return(self):
        conn = _make_db()
        prices = [(f"2026-03-{20+i:02d}", 60.0 + i) for i in range(7)]
        _seed_ohlcv(conn, prices)
        _seed_brief(conn, "2026-03-20", "BUY")
        summary = compute_performance(conn)
        assert summary.briefs_with_5d == 1
        assert summary.hit_rate_5d == 100.0
        assert summary.avg_return_5d > 0

    def test_multiple_briefs(self):
        conn = _make_db()
        prices = [(f"2026-03-{20+i:02d}", 60.0 + i) for i in range(10)]
        _seed_ohlcv(conn, prices)
        _seed_brief(conn, "2026-03-20", "BUY")
        _seed_brief(conn, "2026-03-22", "HOLD (震荡观望)")
        _seed_brief(conn, "2026-03-24", "BUY")
        summary = compute_performance(conn)
        assert summary.total_briefs == 3
        assert len(summary.details) == 3


class TestFormatPerformance:
    def test_empty_summary(self):
        s = PerformanceSummary()
        text = format_performance(s)
        assert "总晨报数: 0" in text
        assert "暂无足够数据" in text

    def test_with_data(self):
        s = PerformanceSummary(
            total_briefs=5,
            briefs_with_1d=4,
            hit_rate_1d=75.0,
            avg_return_1d=1.2,
            details=[
                BriefPerformance("2026-03-20", "BUY", 60.0, 2.0, None, None, True, None),
            ],
        )
        text = format_performance(s)
        assert "75.0%" in text
        assert "1.2" in text or "1.20" in text
