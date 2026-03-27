"""Backtest engine with walk-forward validation for 603799 strategies."""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    strategy: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    win_rate: float
    sharpe: float
    max_drawdown: float
    profit_factor: float
    total_trades: int
    trades: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def passes_threshold(self) -> bool:
        return (
            self.total_trades >= config.BACKTEST_MIN_OOS_TRADES
            and self.win_rate >= config.BACKTEST_MIN_WIN_RATE
            and self.sharpe >= config.BACKTEST_MIN_SHARPE
            and self.max_drawdown <= config.BACKTEST_MAX_DRAWDOWN
        )


def _compute_metrics(trades: list[dict]) -> dict:
    """Compute win rate, sharpe, drawdown, profit factor from trade list."""
    if not trades:
        return {"win_rate": 0, "sharpe": 0, "max_drawdown": 0,
                "profit_factor": 0, "total_trades": 0}

    returns = [t["return_pct"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    win_rate = len(wins) / len(returns) if returns else 0

    mean_ret = np.mean(returns) if returns else 0
    std_ret = np.std(returns) if len(returns) > 1 else 1
    sharpe = (mean_ret / std_ret) * np.sqrt(252) if std_ret > 0 else 0

    cumulative = np.cumprod(1 + np.array(returns))
    peak = np.maximum.accumulate(cumulative)
    drawdown = (peak - cumulative) / peak
    max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 1e-9
    profit_factor = gross_profit / gross_loss

    return {
        "win_rate": win_rate,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "profit_factor": profit_factor,
        "total_trades": len(trades),
    }


def _simulate_trades(
    df: pd.DataFrame, signals: pd.Series, hold_days: int = 5
) -> list[dict]:
    """Simulate trades from buy signals. Respects T+1: buy at next open, sell at
    open after hold_days. Skips fills on ±10% limit days."""
    trades = []
    i = 0
    while i < len(df) - hold_days - 1:
        if not signals.iloc[i]:
            i += 1
            continue

        entry_idx = i + 1
        exit_idx = min(entry_idx + hold_days, len(df) - 1)

        entry_row = df.iloc[entry_idx]
        exit_row = df.iloc[exit_idx]

        entry_price = float(entry_row["open"])
        exit_price = float(exit_row["open"])

        prev_close = float(df.iloc[i]["close"])
        if abs(entry_price - prev_close) / prev_close >= 0.099:
            i = entry_idx + 1
            continue

        ret = (exit_price - entry_price) / entry_price
        trades.append({
            "entry_date": str(entry_row["date"]),
            "exit_date": str(exit_row["date"]),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "return_pct": ret,
        })

        i = exit_idx + 1

    return trades


# ── Strategy implementations ──

def strategy_ma_crossover(df: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.Series:
    """Buy when fast MA crosses above slow MA."""
    ma_fast = df["close"].rolling(fast).mean()
    ma_slow = df["close"].rolling(slow).mean()
    crossed = (ma_fast > ma_slow) & (ma_fast.shift(1) <= ma_slow.shift(1))
    return crossed.fillna(False)


def strategy_macd_divergence(df: pd.DataFrame) -> pd.Series:
    """Buy on MACD histogram crossing above zero (golden cross)."""
    ema12 = df["close"].ewm(span=12).mean()
    ema26 = df["close"].ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9).mean()
    hist = macd_line - signal_line
    crossed = (hist > 0) & (hist.shift(1) <= 0)
    return crossed.fillna(False)


def strategy_volume_breakout(df: pd.DataFrame, vol_mult: float = 2.0,
                              price_pct: float = 0.02) -> pd.Series:
    """Buy when volume > N× average AND price up > X%."""
    vol_avg = df["volume"].rolling(20).mean()
    high_vol = df["volume"] > vol_mult * vol_avg
    price_up = df["close"].pct_change() > price_pct
    return (high_vol & price_up).fillna(False)


def strategy_rsi_oversold(df: pd.DataFrame, threshold: int = 30,
                           period: int = 12) -> pd.Series:
    """Buy when RSI crosses above oversold threshold."""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))
    crossed = (rsi > threshold) & (rsi.shift(1) <= threshold)
    return crossed.fillna(False)


def strategy_mean_reversion(df: pd.DataFrame, window: int = 20,
                             std_mult: float = 2.0) -> pd.Series:
    """Buy when price touches lower Bollinger Band."""
    mid = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    lower = mid - std_mult * std
    return (df["close"] <= lower).fillna(False)


ALL_STRATEGIES = {
    "ma_crossover": strategy_ma_crossover,
    "macd_divergence": strategy_macd_divergence,
    "volume_breakout": strategy_volume_breakout,
    "rsi_oversold": strategy_rsi_oversold,
    "mean_reversion": strategy_mean_reversion,
}


def walk_forward_eval(
    df: pd.DataFrame,
    strategy_fn,
    strategy_name: str,
    train_years: int = 4,
) -> BacktestResult | None:
    """Walk-forward: train on first N years, test on remainder.

    Returns None if insufficient data for the split.
    """
    if df.empty or len(df) < 252:
        logger.warning("Insufficient data for walk-forward (%d rows)", len(df))
        return None

    dates = pd.to_datetime(df["date"])
    first_date = dates.min()
    split_date = first_date + pd.DateOffset(years=train_years)
    last_date = dates.max()

    if split_date >= last_date:
        logger.warning("Split date %s >= last date %s", split_date, last_date)
        return None

    train_mask = dates < split_date
    test_mask = dates >= split_date

    train_df = df[train_mask].reset_index(drop=True)
    test_df = df[test_mask].reset_index(drop=True)

    if len(test_df) < 60:
        logger.warning("Test period too short: %d rows", len(test_df))
        return None

    # Generate signals on test data only (no lookahead)
    test_signals = strategy_fn(test_df)
    trades = _simulate_trades(test_df, test_signals)
    metrics = _compute_metrics(trades)

    return BacktestResult(
        strategy=strategy_name,
        train_start=str(train_df.iloc[0]["date"]),
        train_end=str(train_df.iloc[-1]["date"]),
        test_start=str(test_df.iloc[0]["date"]),
        test_end=str(test_df.iloc[-1]["date"]),
        trades=trades,
        **metrics,
    )


def run_all_strategies(df: pd.DataFrame) -> list[BacktestResult]:
    """Run walk-forward eval on all strategies. Returns list of results."""
    results = []
    for name, fn in ALL_STRATEGIES.items():
        logger.info("Running strategy: %s", name)
        result = walk_forward_eval(df, fn, name)
        if result:
            results.append(result)
            status = "PASS" if result.passes_threshold else "FAIL"
            logger.info(
                "  %s: %s — win=%.1f%% sharpe=%.2f dd=%.1f%% trades=%d",
                name, status,
                result.win_rate * 100, result.sharpe,
                result.max_drawdown * 100, result.total_trades,
            )
    return results
