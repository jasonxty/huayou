"""Tests for backtest/engine.py — strategies and walk-forward validation."""

import pandas as pd
from backtest.engine import (
    strategy_ma_crossover,
    strategy_macd_divergence,
    walk_forward_eval,
    run_all_strategies,
    _compute_metrics,
)


def test_ma_crossover_produces_signals(sample_ohlcv):
    signals = strategy_ma_crossover(sample_ohlcv)
    assert signals.dtype == bool
    assert signals.any()


def test_macd_produces_signals(sample_ohlcv):
    signals = strategy_macd_divergence(sample_ohlcv)
    assert signals.dtype == bool


def test_walk_forward_no_lookahead(sample_ohlcv_5yr):
    """Verify train period strictly before test period."""
    result = walk_forward_eval(
        sample_ohlcv_5yr, strategy_ma_crossover, "ma_crossover", train_years=4
    )
    assert result is not None
    assert result.train_end < result.test_start, \
        f"Train end {result.train_end} must be before test start {result.test_start}"


def test_walk_forward_min_data():
    """Insufficient data returns None."""
    short_df = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
        "open": [10] * 100, "high": [11] * 100,
        "low": [9] * 100, "close": [10] * 100,
        "volume": [1000000] * 100,
    })
    result = walk_forward_eval(short_df, strategy_ma_crossover, "test", train_years=4)
    assert result is None


def test_compute_metrics_empty():
    metrics = _compute_metrics([])
    assert metrics["total_trades"] == 0
    assert metrics["win_rate"] == 0


def test_compute_metrics_known():
    trades = [
        {"return_pct": 0.05},
        {"return_pct": -0.02},
        {"return_pct": 0.03},
        {"return_pct": 0.01},
        {"return_pct": -0.04},
    ]
    metrics = _compute_metrics(trades)
    assert metrics["total_trades"] == 5
    assert metrics["win_rate"] == 0.6


def test_run_all_strategies(sample_ohlcv_5yr):
    results = run_all_strategies(sample_ohlcv_5yr)
    assert len(results) > 0
    for r in results:
        assert 0 <= r.win_rate <= 1
        assert r.total_trades >= 0
