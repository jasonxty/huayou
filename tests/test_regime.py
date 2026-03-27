"""Tests for regime classification and matching."""

import numpy as np
import pandas as pd
from agents.strategist import classify_regime, match_historical_regime
import config


def _make_indicators(n=100, ma20_above_ma60=True, rsi=50.0):
    """Create synthetic indicator DataFrame."""
    base_ma = 40.0
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d"),
        "ma20": [base_ma * (1.05 if ma20_above_ma60 else 0.95)] * n,
        "ma60": [base_ma] * n,
        "rsi12": [rsi] * n,
    })


def test_classify_bullish():
    ind = _make_indicators(ma20_above_ma60=True, rsi=50)
    regime = classify_regime(ind)
    assert regime["trend"] == "up"
    assert regime["rsi"] == "neutral"


def test_classify_oversold():
    ind = _make_indicators(ma20_above_ma60=False, rsi=25)
    regime = classify_regime(ind)
    assert regime["trend"] == "down"
    assert regime["rsi"] == "oversold"


def test_min_samples_enforced(sample_ohlcv):
    from data.indicators import compute_all
    indicators = compute_all(sample_ohlcv)

    regime = {"trend": "up", "rsi": "overbought"}
    result = match_historical_regime(indicators, sample_ohlcv, regime)

    if result["count"] < config.REGIME_MIN_SAMPLES:
        assert result["sufficient"] is False
    else:
        assert result["sufficient"] is True
