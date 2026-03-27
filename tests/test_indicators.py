"""Tests for data/indicators.py — technical indicator computation."""

import numpy as np
from data.indicators import compute_all


def test_compute_all_shape(sample_ohlcv):
    result = compute_all(sample_ohlcv)
    assert len(result) == len(sample_ohlcv)
    assert "ma5" in result.columns
    assert "macd" in result.columns
    assert "rsi12" in result.columns
    assert "atr14" in result.columns


def test_ma5_correctness(sample_ohlcv):
    result = compute_all(sample_ohlcv)
    expected_ma5 = sample_ohlcv["close"].rolling(5).mean().iloc[10]
    actual_ma5 = result["ma5"].iloc[10]
    assert abs(expected_ma5 - actual_ma5) < 0.01


def test_early_dates_have_nan(sample_ohlcv):
    result = compute_all(sample_ohlcv)
    assert np.isnan(result["ma250"].iloc[0])
    assert not np.isnan(result["ma5"].iloc[10])


def test_rsi_bounds(sample_ohlcv):
    result = compute_all(sample_ohlcv)
    rsi_vals = result["rsi12"].dropna()
    assert (rsi_vals >= 0).all()
    assert (rsi_vals <= 100).all()
