"""Tests for T+0 strategy backtester."""

import numpy as np
import pandas as pd

from backtest.t0_backtest import (
    run_t0_backtest,
    format_t0_backtest,
    _intraday_sequence_favorable,
    T0BacktestResult,
)


def _make_ohlcv(n: int = 300, base_price: float = 60.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data with realistic noise."""
    np.random.seed(42)
    dates = pd.bdate_range("2024-01-02", periods=n)
    close = [base_price]
    for _ in range(n - 1):
        close.append(close[-1] * (1 + np.random.normal(0, 0.025)))

    close = np.array(close)
    high = close * (1 + np.abs(np.random.normal(0, 0.015, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.015, n)))
    open_ = close * (1 + np.random.normal(0, 0.005, n))
    volume = np.random.randint(500000, 2000000, n)

    return pd.DataFrame({
        "date": dates,
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "turnover": volume * close,
        "turnover_rate": np.random.uniform(0.5, 3.0, n),
    })


def test_backtest_runs():
    ohlcv = _make_ohlcv(300)
    result = run_t0_backtest(ohlcv, position_qty=1000, position_cost=60.0,
                             lookback_years=1)
    assert isinstance(result, T0BacktestResult)
    assert result.total_days > 0
    assert result.completed_trades + result.sell_only_trades + result.skipped_days == result.total_days


def test_backtest_trades_have_correct_structure():
    ohlcv = _make_ohlcv(300)
    result = run_t0_backtest(ohlcv, position_qty=1000, position_cost=60.0)
    for t in result.trades:
        if t.triggered:
            assert t.sell_price > 0
            assert t.buy_price > 0
            assert t.lot > 0
            assert t.sell_price > t.buy_price


def test_win_rate_bounded():
    ohlcv = _make_ohlcv(300)
    result = run_t0_backtest(ohlcv, position_qty=1000, position_cost=60.0)
    assert 0 <= result.win_rate <= 1


def test_format_output():
    ohlcv = _make_ohlcv(300)
    result = run_t0_backtest(ohlcv, position_qty=1000, position_cost=60.0)
    output = format_t0_backtest(result)
    assert "T+0 策略回测" in output
    assert "胜率" in output
    assert "累计收益" in output


def test_intraday_sequence_sell_first():
    assert _intraday_sequence_favorable(62, 63, 59, "先卖后买") is True
    assert _intraday_sequence_favorable(60, 63, 59, "先卖后买") is False


def test_intraday_sequence_buy_first():
    assert _intraday_sequence_favorable(60, 63, 59, "先买后卖") is True
    assert _intraday_sequence_favorable(62, 63, 59, "先买后卖") is False


def test_monthly_breakdown_present():
    ohlcv = _make_ohlcv(300)
    result = run_t0_backtest(ohlcv, position_qty=1000, position_cost=60.0)
    if result.completed_trades > 0:
        assert len(result.monthly_breakdown) > 0
        for m in result.monthly_breakdown:
            assert "month" in m
            assert "trades" in m
            assert "profit" in m
            assert "win_rate" in m
