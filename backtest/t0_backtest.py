"""T+0 intraday strategy backtester.

Uses daily OHLCV to simulate the T+0 advisor's recommendations historically.

Key assumption: we only have daily bars, not tick-level data.
For "先卖后买" (sell first, buy back later):
  - Sell triggered if day's high >= sell_zone
  - Buy-back triggered if day's low <= buy_zone
  - Intraday sequence heuristic: if open > midpoint(high,low) → likely
    peaked first then dipped (favorable); otherwise unfavorable.
  - Conservative: only count as successful if both zones were hit AND
    the sequence heuristic agrees.

Costs: 0.06% round-trip (commission + stamp duty on sell-side only for A-shares).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from agents.t0_advisor import advise, T0Advice
from agents.technical import (
    _score_ma_alignment,
    _score_macd,
    _score_rsi,
    _score_kdj,
    _score_bollinger,
    _score_volume,
    _find_support_resistance,
)
from data.indicators import compute_all

logger = logging.getLogger(__name__)

ROUND_TRIP_COST = 0.0006  # 0.06% (stamp duty 0.05% sell-side + 0.01% commission)


@dataclass
class T0Trade:
    date: str
    strategy: str  # "先卖后买" / "先买后卖"
    sell_price: float
    buy_price: float
    lot: int
    profit: float  # absolute RMB profit
    profit_pct: float  # % return on trade capital
    triggered: bool  # both legs executed
    sell_only: bool  # sold but didn't buy back (卖飞)
    skip_reason: str = ""  # why T+0 was skipped


@dataclass
class T0BacktestResult:
    test_start: str
    test_end: str
    total_days: int
    trading_days: int  # days where T+0 was attempted
    completed_trades: int  # both legs executed
    sell_only_trades: int  # sold but didn't buy back
    skipped_days: int  # T+0 disabled

    total_profit: float  # cumulative RMB (per 1000-share base position)
    avg_profit_per_trade: float
    win_rate: float  # % of completed trades that were profitable
    max_single_loss: float
    max_single_win: float
    cost_basis_reduction: float  # total cost reduction from T+0 per share

    monthly_breakdown: list[dict] = field(default_factory=list)
    trades: list[T0Trade] = field(default_factory=list)


def _compute_tech_score(indicators_row: pd.Series, prev_row: pd.Series | None,
                        price: float, ohlcv_slice: pd.DataFrame) -> float:
    """Lightweight tech score computation for backtesting."""
    total = 0.0
    for scorer in [
        lambda: _score_ma_alignment(indicators_row),
        lambda: _score_macd(indicators_row, prev_row),
        lambda: _score_rsi(indicators_row),
        lambda: _score_kdj(indicators_row),
        lambda: _score_bollinger(indicators_row, price),
        lambda: _score_volume(ohlcv_slice),
    ]:
        s, _ = scorer()
        total += s
    return max(-100, min(100, total))


def _classify_regime_simple(row: pd.Series) -> dict:
    ma20 = row.get("ma20", np.nan)
    ma60 = row.get("ma60", np.nan)
    rsi = row.get("rsi12", np.nan)

    if pd.isna(ma20) or pd.isna(ma60):
        trend = "unknown"
    elif ma20 > ma60 * 1.02:
        trend = "up"
    elif ma20 < ma60 * 0.98:
        trend = "down"
    else:
        trend = "sideways"

    if pd.isna(rsi):
        rsi_bucket = "unknown"
    elif rsi < 30:
        rsi_bucket = "oversold"
    elif rsi > 70:
        rsi_bucket = "overbought"
    else:
        rsi_bucket = "neutral"

    return {"trend": trend, "rsi": rsi_bucket}


def _intraday_sequence_favorable(open_p: float, high: float, low: float,
                                  strategy: str) -> bool:
    """Heuristic: did the intraday price action favor the T+0 strategy?

    If open is above the day's midpoint → likely peaked early then dipped
    → favorable for "先卖后买" (sell first).
    If open is below midpoint → likely dipped early then rallied
    → favorable for "先买后卖" (buy first).
    """
    mid = (high + low) / 2
    if strategy == "先卖后买":
        return open_p >= mid
    else:
        return open_p <= mid


def run_t0_backtest(
    ohlcv: pd.DataFrame,
    position_qty: int = 1000,
    position_cost: float = 65.3,
    lookback_years: int = 2,
) -> T0BacktestResult:
    """Run T+0 strategy backtest on historical data.

    For each historical day:
    1. Use data up to yesterday to compute indicators and generate T+0 advice
    2. Check today's OHLCV to see if the trade would have triggered
    3. Calculate P&L including transaction costs

    Args:
        ohlcv: Full OHLCV dataframe
        position_qty: Number of shares held
        position_cost: Average cost per share
        lookback_years: How many recent years to test
    """
    indicators = compute_all(ohlcv)

    dates = pd.to_datetime(ohlcv["date"])
    cutoff = dates.max() - pd.DateOffset(years=lookback_years)
    start_idx = max(120, int((dates >= cutoff).idxmax()))

    trades = []
    position = {"ticker": "603799", "cost": position_cost,
                "quantity": position_qty, "entry_date": "", "notes": ""}

    for i in range(start_idx, len(ohlcv)):
        today = ohlcv.iloc[i]
        today_date = str(today["date"])[:10]
        yesterday_close = float(ohlcv.iloc[i - 1]["close"])

        ind_row = indicators.iloc[i - 1] if i - 1 < len(indicators) else None
        prev_ind = indicators.iloc[i - 2] if i - 2 < len(indicators) and i >= 2 else None

        if ind_row is None or pd.isna(ind_row.get("atr14", np.nan)):
            trades.append(T0Trade(
                date=today_date, strategy="", sell_price=0, buy_price=0,
                lot=0, profit=0, profit_pct=0, triggered=False,
                sell_only=False, skip_reason="indicators_unavailable",
            ))
            continue

        ohlcv_slice = ohlcv.iloc[max(0, i - 21):i]
        tech_score = _compute_tech_score(ind_row, prev_ind, yesterday_close, ohlcv_slice)
        regime = _classify_regime_simple(ind_row)
        support, resistance = _find_support_resistance(ohlcv.iloc[:i])

        advice = advise(
            position=position,
            latest_price=yesterday_close,
            atr=float(ind_row.get("atr14", 0)),
            support=support,
            resistance=resistance,
            boll_upper=float(ind_row.get("boll_upper", 0)),
            boll_mid=float(ind_row.get("boll_mid", 0)),
            boll_lower=float(ind_row.get("boll_lower", 0)),
            tech_score=tech_score,
            regime=regime,
        )

        if not advice.t0_enabled:
            trades.append(T0Trade(
                date=today_date, strategy=advice.strategy, sell_price=0,
                buy_price=0, lot=0, profit=0, profit_pct=0,
                triggered=False, sell_only=False, skip_reason="t0_disabled",
            ))
            continue

        day_high = float(today["high"])
        day_low = float(today["low"])
        day_open = float(today["open"])
        lot = advice.t0_lot

        if advice.strategy == "先卖后买":
            sell_hit = day_high >= advice.sell_zone_low
            buy_hit = day_low <= advice.buy_zone_high
            favorable = _intraday_sequence_favorable(
                day_open, day_high, day_low, "先卖后买")

            if sell_hit and buy_hit and favorable:
                sell_p = advice.sell_zone_low
                buy_p = advice.buy_zone_high
                gross = (sell_p - buy_p) * lot
                cost = (sell_p + buy_p) * lot * ROUND_TRIP_COST
                net = gross - cost
                trades.append(T0Trade(
                    date=today_date, strategy="先卖后买",
                    sell_price=sell_p, buy_price=buy_p, lot=lot,
                    profit=round(net, 2),
                    profit_pct=round(net / (sell_p * lot) * 100, 3),
                    triggered=True, sell_only=False,
                ))
            elif sell_hit and not buy_hit:
                trades.append(T0Trade(
                    date=today_date, strategy="先卖后买",
                    sell_price=advice.sell_zone_low, buy_price=0, lot=lot,
                    profit=0, profit_pct=0,
                    triggered=False, sell_only=True,
                    skip_reason="sold_but_no_buyback",
                ))
            else:
                trades.append(T0Trade(
                    date=today_date, strategy="先卖后买",
                    sell_price=0, buy_price=0, lot=lot,
                    profit=0, profit_pct=0,
                    triggered=False, sell_only=False,
                    skip_reason="zones_not_reached",
                ))

        else:  # 先买后卖
            buy_hit = day_low <= advice.buy_zone_high
            sell_hit = day_high >= advice.sell_zone_low
            favorable = _intraday_sequence_favorable(
                day_open, day_high, day_low, "先买后卖")

            if buy_hit and sell_hit and favorable:
                buy_p = advice.buy_zone_high
                sell_p = advice.sell_zone_low
                gross = (sell_p - buy_p) * lot
                cost = (sell_p + buy_p) * lot * ROUND_TRIP_COST
                net = gross - cost
                trades.append(T0Trade(
                    date=today_date, strategy="先买后卖",
                    sell_price=sell_p, buy_price=buy_p, lot=lot,
                    profit=round(net, 2),
                    profit_pct=round(net / (buy_p * lot) * 100, 3),
                    triggered=True, sell_only=False,
                ))
            elif buy_hit and not sell_hit:
                trades.append(T0Trade(
                    date=today_date, strategy="先买后卖",
                    sell_price=0, buy_price=advice.buy_zone_high, lot=lot,
                    profit=0, profit_pct=0,
                    triggered=False, sell_only=False,
                    skip_reason="bought_but_no_sell",
                ))
            else:
                trades.append(T0Trade(
                    date=today_date, strategy="先买后卖",
                    sell_price=0, buy_price=0, lot=lot,
                    profit=0, profit_pct=0,
                    triggered=False, sell_only=False,
                    skip_reason="zones_not_reached",
                ))

    completed = [t for t in trades if t.triggered]
    sell_only = [t for t in trades if t.sell_only]
    skipped = [t for t in trades if not t.triggered and not t.sell_only]

    profits = [t.profit for t in completed]
    wins = [p for p in profits if p > 0]

    total_profit = sum(profits)
    avg_profit = float(np.mean(profits)) if profits else 0
    win_rate = len(wins) / len(profits) if profits else 0
    max_loss = min(profits) if profits else 0
    max_win = max(profits) if profits else 0
    cost_reduction = total_profit / position_qty if position_qty > 0 else 0

    monthly = _monthly_breakdown(completed)

    total_days = len(ohlcv) - start_idx

    return T0BacktestResult(
        test_start=str(ohlcv.iloc[start_idx]["date"])[:10],
        test_end=str(ohlcv.iloc[-1]["date"])[:10],
        total_days=total_days,
        trading_days=len(completed) + len(sell_only),
        completed_trades=len(completed),
        sell_only_trades=len(sell_only),
        skipped_days=len(skipped),
        total_profit=round(total_profit, 2),
        avg_profit_per_trade=round(avg_profit, 2),
        win_rate=round(win_rate, 4),
        max_single_loss=round(max_loss, 2),
        max_single_win=round(max_win, 2),
        cost_basis_reduction=round(cost_reduction, 2),
        monthly_breakdown=monthly,
        trades=trades,
    )


def _monthly_breakdown(completed_trades: list[T0Trade]) -> list[dict]:
    """Group completed trades by month and compute stats."""
    if not completed_trades:
        return []

    months: dict[str, list[float]] = {}
    for t in completed_trades:
        month_key = t.date[:7]  # "2024-03"
        months.setdefault(month_key, []).append(t.profit)

    result = []
    for month, profits in sorted(months.items()):
        result.append({
            "month": month,
            "trades": len(profits),
            "profit": round(sum(profits), 2),
            "win_rate": round(sum(1 for p in profits if p > 0) / len(profits), 2),
        })
    return result


def format_t0_backtest(result: T0BacktestResult) -> str:
    """Format T+0 backtest result as a printable report."""
    lines = [
        f"\n{'─' * 60}",
        f"  T+0 策略回测 — {result.test_start} ~ {result.test_end}",
        f"{'─' * 60}",
        f"  总交易日: {result.total_days}",
        f"  完成做T: {result.completed_trades}次  |  卖飞: {result.sell_only_trades}次  |  未触发: {result.skipped_days}次",
        f"  胜率: {result.win_rate * 100:.1f}%",
        f"  累计收益: ¥{result.total_profit:,.2f}  (每股降本: ¥{result.cost_basis_reduction:.2f})",
        f"  单笔均盈: ¥{result.avg_profit_per_trade:.2f}",
        f"  单笔最大盈利: ¥{result.max_single_win:.2f}  |  最大亏损: ¥{result.max_single_loss:.2f}",
    ]

    if result.total_days > 0:
        trigger_rate = (result.completed_trades + result.sell_only_trades) / result.total_days * 100
        lines.append(f"  触发率: {trigger_rate:.0f}%  (有做T机会的天数占比)")

    if result.monthly_breakdown:
        lines.append(f"\n  {'月份':<10} {'交易次数':>8} {'盈亏':>10} {'胜率':>8}")
        lines.append(f"  {'─' * 40}")
        for m in result.monthly_breakdown[-6:]:
            lines.append(
                f"  {m['month']:<10} {m['trades']:>8}  "
                f"¥{m['profit']:>8,.2f}  {m['win_rate'] * 100:>6.0f}%"
            )

    verdict = "✓ 策略有效" if result.win_rate >= 0.55 and result.total_profit > 0 else "✗ 策略需优化"
    lines.append(f"\n  结论: {verdict}")

    if result.win_rate < 0.5:
        lines.append("  ⚠ 胜率低于50%，做T可能得不偿失")
    elif result.total_profit < 0:
        lines.append("  ⚠ 累计亏损，手续费吃掉了利润")
    elif result.win_rate >= 0.6 and result.total_profit > 0:
        lines.append("  ✓ 胜率>60%且累计盈利，策略表现良好")

    lines.append(f"{'─' * 60}")
    return "\n".join(lines)
