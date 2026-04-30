"""Buffett 历史回测模块."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.indicators import compute_all
from data.store import get_connection, init_db, save_indicators, save_ohlcv
from backtest.buffett_backtest import format_buffett_backtest, run_buffett_backtest


def test_run_buffett_backtest_smoke(sample_ohlcv_5yr):
    conn = get_connection(":memory:")
    init_db(conn)
    save_ohlcv(conn, sample_ohlcv_5yr)
    ind = compute_all(sample_ohlcv_5yr)
    save_indicators(conn, ind)

    r = run_buffett_backtest(conn, trading_days=80, initial_capital=100_000.0)
    assert r.trading_days > 40
    assert r.label_count > 30
    assert r.records[0].date < r.records[-1].date
    txt = format_buffett_backtest(r)
    assert "Buffett" in txt or "回测" in txt
    conn.close()
