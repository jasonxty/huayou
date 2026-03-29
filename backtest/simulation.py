"""Full portfolio simulation — simulate 6 months of trading with real signals.

Uses the same technical indicators and regime classification as the live system
to make daily BUY/SELL/HOLD/T+0 decisions with a fixed starting capital.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FEE_RATE = 0.0013  # ~0.13% round-trip (commission 0.025% x2 + stamp tax 0.05% sell-side)
SLIPPAGE = 0.002   # 0.2% slippage per trade


@dataclass
class Trade:
    date: str
    action: str       # BUY / SELL / T0_SELL / T0_BUY
    price: float
    shares: int
    cost: float       # total cost including fees
    reason: str


@dataclass
class DailySnapshot:
    date: str
    close: float
    cash: float
    shares: int
    equity: float     # cash + shares * close
    drawdown: float   # from peak equity
    position_pct: float


@dataclass
class SimulationResult:
    initial_capital: float
    final_equity: float
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    buy_hold_return_pct: float
    trades: list[Trade]
    daily: list[DailySnapshot]
    monthly_returns: dict[str, float]


def _compute_signals(row, prev_row) -> dict:
    """Extract trading signals from indicator row."""
    signals = {}

    ma5 = float(row.get("ma5", 0) or 0)
    ma10 = float(row.get("ma10", 0) or 0)
    ma20 = float(row.get("ma20", 0) or 0)
    ma60 = float(row.get("ma60", 0) or 0)
    close = float(row.get("close", 0) or 0)

    prev_ma5 = float(prev_row.get("ma5", 0) or 0)
    prev_ma10 = float(prev_row.get("ma10", 0) or 0)

    signals["ma_bull"] = ma5 > ma10 > ma20
    signals["ma_bear"] = ma5 < ma10 < ma20
    signals["ma5_cross_up"] = prev_ma5 <= prev_ma10 and ma5 > ma10
    signals["ma5_cross_down"] = prev_ma5 >= prev_ma10 and ma5 < ma10
    signals["above_ma60"] = close > ma60 if ma60 > 0 else False

    rsi6 = float(row.get("rsi6", 50) or 50)
    rsi12 = float(row.get("rsi12", 50) or 50)
    signals["rsi_oversold"] = rsi6 < 25
    signals["rsi_overbought"] = rsi6 > 75
    signals["rsi_neutral"] = 35 < rsi6 < 65

    macd = float(row.get("macd", 0) or 0)
    macd_signal = float(row.get("macd_signal", 0) or 0)
    prev_macd = float(prev_row.get("macd", 0) or 0)
    prev_signal = float(prev_row.get("macd_signal", 0) or 0)
    signals["macd_golden"] = prev_macd <= prev_signal and macd > macd_signal
    signals["macd_death"] = prev_macd >= prev_signal and macd < macd_signal
    signals["macd_positive"] = macd > 0

    k = float(row.get("kdj_k", 50) or 50)
    d = float(row.get("kdj_d", 50) or 50)
    signals["kdj_oversold"] = k < 20 and d < 20
    signals["kdj_overbought"] = k > 80 and d > 80

    boll_upper = float(row.get("boll_upper", 0) or 0)
    boll_lower = float(row.get("boll_lower", 0) or 0)
    boll_mid = float(row.get("boll_mid", 0) or 0)
    signals["below_boll_lower"] = close < boll_lower if boll_lower > 0 else False
    signals["above_boll_upper"] = close > boll_upper if boll_upper > 0 else False
    signals["above_boll_mid"] = close > boll_mid if boll_mid > 0 else False

    vol = float(row.get("volume", 0) or 0)
    vol_ma = float(row.get("vol_ma20", 1) or 1)
    signals["volume_surge"] = vol > vol_ma * 1.5 if vol_ma > 0 else False

    atr = float(row.get("atr14", 0) or 0)
    signals["atr"] = atr

    return signals


def run_simulation(
    ohlcv: pd.DataFrame,
    indicators: pd.DataFrame,
    capital: float = 100_000.0,
    lookback_days: int = 180,
) -> SimulationResult:
    """Run a full portfolio simulation over the last `lookback_days` calendar days."""

    merged = ohlcv.merge(indicators, on="date", how="inner", suffixes=("", "_ind"))
    merged["date"] = pd.to_datetime(merged["date"])
    merged = merged.sort_values("date").reset_index(drop=True)

    cutoff = merged["date"].max() - pd.Timedelta(days=lookback_days)
    warmup_start = cutoff - pd.Timedelta(days=5)
    sim_data = merged[merged["date"] >= warmup_start].reset_index(drop=True)

    sim_start_idx = sim_data[sim_data["date"] >= cutoff].index[0]

    cash = capital
    shares = 0
    avg_cost = 0.0
    trades: list[Trade] = []
    daily: list[DailySnapshot] = []
    peak_equity = capital
    t0_sold_today = 0

    first_close = float(sim_data.iloc[sim_start_idx]["close"])

    for i in range(sim_start_idx, len(sim_data)):
        row = sim_data.iloc[i]
        prev = sim_data.iloc[i - 1] if i > 0 else row
        date_str = row["date"].strftime("%Y-%m-%d")
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        opn = float(row["open"])

        signals = _compute_signals(row, prev)
        atr = signals["atr"]

        t0_sold_today = 0

        score = 0
        reasons = []

        if signals["ma5_cross_up"]:
            score += 25
            reasons.append("MA5上穿MA10")
        if signals["ma5_cross_down"]:
            score -= 25
            reasons.append("MA5下穿MA10")
        if signals["ma_bull"]:
            score += 15
            reasons.append("均线多头")
        if signals["ma_bear"]:
            score -= 15
            reasons.append("均线空头")

        if signals["macd_golden"]:
            score += 20
            reasons.append("MACD金叉")
        if signals["macd_death"]:
            score -= 20
            reasons.append("MACD死叉")

        if signals["rsi_oversold"]:
            score += 15
            reasons.append("RSI超卖")
        if signals["rsi_overbought"]:
            score -= 15
            reasons.append("RSI超买")

        if signals["kdj_oversold"]:
            score += 10
            reasons.append("KDJ超卖")
        if signals["kdj_overbought"]:
            score -= 10
            reasons.append("KDJ超买")

        if signals["below_boll_lower"]:
            score += 10
            reasons.append("跌破布林下轨")
        if signals["above_boll_upper"]:
            score -= 10
            reasons.append("突破布林上轨")

        if signals["volume_surge"] and signals["above_boll_mid"]:
            score += 10
            reasons.append("放量上穿布林中轨")

        # === POSITION SIZING ===
        max_position_value = capital * 0.8  # max 80% in stock
        lot = 100  # A-share min lot

        # === BUY LOGIC ===
        if score >= 30 and shares == 0:
            buy_price = close * (1 + SLIPPAGE)
            max_shares = int(max_position_value / buy_price / lot) * lot
            if max_shares >= lot and cash >= buy_price * lot:
                shares_to_buy = max_shares
                cost = shares_to_buy * buy_price * (1 + FEE_RATE)
                if cost <= cash:
                    cash -= cost
                    shares = shares_to_buy
                    avg_cost = buy_price
                    trades.append(Trade(date_str, "BUY", buy_price, shares_to_buy, cost,
                                        f"建仓(score={score}): {', '.join(reasons)}"))

        elif score >= 20 and shares > 0:
            can_buy = int((cash * 0.5) / (close * (1 + SLIPPAGE)) / lot) * lot
            if can_buy >= lot:
                buy_price = close * (1 + SLIPPAGE)
                cost = can_buy * buy_price * (1 + FEE_RATE)
                if cost <= cash:
                    old_total = avg_cost * shares
                    cash -= cost
                    shares += can_buy
                    avg_cost = (old_total + can_buy * buy_price) / shares
                    trades.append(Trade(date_str, "BUY", buy_price, can_buy, cost,
                                        f"加仓(score={score}): {', '.join(reasons)}"))

        # === SELL LOGIC ===
        elif score <= -30 and shares > 0:
            sell_price = close * (1 - SLIPPAGE)
            revenue = shares * sell_price * (1 - FEE_RATE)
            trades.append(Trade(date_str, "SELL", sell_price, shares, revenue,
                                f"清仓(score={score}): {', '.join(reasons)}"))
            cash += revenue
            shares = 0
            avg_cost = 0

        elif score <= -15 and shares > 0:
            sell_shares = max(int(shares * 0.5 / lot) * lot, lot)
            if sell_shares > shares:
                sell_shares = shares
            sell_price = close * (1 - SLIPPAGE)
            revenue = sell_shares * sell_price * (1 - FEE_RATE)
            trades.append(Trade(date_str, "SELL", sell_price, sell_shares, revenue,
                                f"减仓(score={score}): {', '.join(reasons)}"))
            cash += revenue
            shares -= sell_shares
            if shares == 0:
                avg_cost = 0

        # === STOP LOSS ===
        if shares > 0 and avg_cost > 0:
            loss_pct = (close - avg_cost) / avg_cost
            if loss_pct <= -0.08:
                sell_price = close * (1 - SLIPPAGE)
                revenue = shares * sell_price * (1 - FEE_RATE)
                trades.append(Trade(date_str, "SELL", sell_price, shares, revenue,
                                    f"止损(亏损{loss_pct*100:.1f}%)"))
                cash += revenue
                shares = 0
                avg_cost = 0

        # === T+0 INTRADAY (simulate with high/low) ===
        if shares >= lot * 2 and atr > 0:
            t0_lot = max(int(shares * 0.2 / lot) * lot, lot)
            spread = high - low

            if spread > atr * 0.6 and high > opn and low < opn:
                sell_at = opn + atr * 0.4
                buy_at = opn - atr * 0.3

                if high >= sell_at and low <= buy_at:
                    sell_p = min(sell_at, high) * (1 - SLIPPAGE)
                    buy_p = max(buy_at, low) * (1 + SLIPPAGE)
                    if sell_p > buy_p:
                        profit = (sell_p - buy_p) * t0_lot
                        fee = (sell_p + buy_p) * t0_lot * FEE_RATE
                        net = profit - fee
                        if net > 0:
                            cash += net
                            old_total_cost = avg_cost * shares
                            avg_cost = (old_total_cost - net) / shares
                            trades.append(Trade(date_str, "T+0", sell_p, t0_lot, net,
                                                f"做T: 卖{sell_p:.2f}买{buy_p:.2f}, 净利{net:.0f}"))

        # === DAILY SNAPSHOT ===
        equity = cash + shares * close
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        pos_pct = (shares * close / equity * 100) if equity > 0 else 0

        daily.append(DailySnapshot(
            date=date_str, close=close, cash=round(cash, 2),
            shares=shares, equity=round(equity, 2),
            drawdown=round(dd * 100, 2), position_pct=round(pos_pct, 1),
        ))

    last_close = float(sim_data.iloc[-1]["close"])
    final_equity = cash + shares * last_close
    total_return = (final_equity - capital) / capital * 100
    trading_days = len(daily)
    ann_return = total_return * (252 / trading_days) if trading_days > 0 else 0
    max_dd = max(d.drawdown for d in daily) if daily else 0

    returns = []
    for i in range(1, len(daily)):
        ret = (daily[i].equity - daily[i - 1].equity) / daily[i - 1].equity
        returns.append(ret)

    if returns:
        avg_ret = np.mean(returns)
        std_ret = np.std(returns)
        sharpe = (avg_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0
    else:
        sharpe = 0

    winning = sum(1 for t in trades if t.action in ("SELL", "T+0") and t.cost > 0
                  and ((t.action == "SELL" and t.price > avg_cost) or t.action == "T+0"))
    sell_trades = sum(1 for t in trades if t.action in ("SELL", "T+0"))
    win_rate = winning / sell_trades if sell_trades > 0 else 0

    bh_return = (last_close - first_close) / first_close * 100

    monthly = {}
    for snap in daily:
        month = snap.date[:7]
        if month not in monthly:
            monthly[month] = {"start": snap.equity, "end": snap.equity}
        monthly[month]["end"] = snap.equity
    monthly_returns = {}
    prev_end = capital
    for m in sorted(monthly.keys()):
        ret = (monthly[m]["end"] - prev_end) / prev_end * 100
        monthly_returns[m] = round(ret, 2)
        prev_end = monthly[m]["end"]

    return SimulationResult(
        initial_capital=capital,
        final_equity=round(final_equity, 2),
        total_return_pct=round(total_return, 2),
        annualized_return_pct=round(ann_return, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 2),
        win_rate=round(win_rate * 100, 1),
        total_trades=len(trades),
        buy_hold_return_pct=round(bh_return, 2),
        trades=trades,
        daily=daily,
        monthly_returns=monthly_returns,
    )


def format_simulation(result: SimulationResult) -> str:
    """Format simulation results as a readable report."""
    lines = [
        f"{'═' * 68}",
        f"  华友钴业 (603799) — 模拟投资报告",
        f"  初始资金: ¥{result.initial_capital:,.0f}  |  期间: {result.daily[0].date} ~ {result.daily[-1].date}",
        f"{'═' * 68}",
        f"",
        f"  ── 总体业绩 ──",
        f"  期末净值:     ¥{result.final_equity:>12,.2f}",
        f"  总收益率:     {result.total_return_pct:>+10.2f}%",
        f"  年化收益率:   {result.annualized_return_pct:>+10.2f}%",
        f"  最大回撤:     {result.max_drawdown_pct:>10.2f}%",
        f"  Sharpe比率:   {result.sharpe_ratio:>10.2f}",
        f"  总交易次数:   {result.total_trades:>10d}",
        f"  买入持有收益: {result.buy_hold_return_pct:>+10.2f}%  (对比基准)",
        f"  超额收益:     {result.total_return_pct - result.buy_hold_return_pct:>+10.2f}%",
        f"",
    ]

    if result.monthly_returns:
        lines.append(f"  ── 月度收益 ──")
        for m, ret in result.monthly_returns.items():
            bar_len = int(abs(ret) / 2)
            bar = ("▓" * bar_len if ret >= 0 else "░" * bar_len)
            sign = "+" if ret >= 0 else ""
            lines.append(f"  {m}  {sign}{ret:>6.2f}%  {'█' if ret >= 0 else '▒'}{bar}")
        lines.append("")

    lines.append(f"  ── 交易记录 ({len(result.trades)}笔) ──")
    for t in result.trades:
        icon = {"BUY": "🟢", "SELL": "🔴", "T+0": "🔄"}.get(t.action, "⚪")
        if t.action == "BUY":
            lines.append(f"  {t.date}  {icon} {t.action:<5} {t.shares:>5}股 @ ¥{t.price:.2f}  "
                          f"费用¥{t.cost:>10,.2f}  {t.reason}")
        elif t.action == "SELL":
            lines.append(f"  {t.date}  {icon} {t.action:<5} {t.shares:>5}股 @ ¥{t.price:.2f}  "
                          f"回收¥{t.cost:>10,.2f}  {t.reason}")
        elif t.action == "T+0":
            lines.append(f"  {t.date}  {icon} {t.action:<5} {t.shares:>5}股              "
                          f"净利¥{t.cost:>10,.2f}  {t.reason}")
    lines.append("")

    lines.append(f"  ── 收益曲线 (周级) ──")
    step = max(1, len(result.daily) // 25)
    for i in range(0, len(result.daily), step):
        d = result.daily[i]
        ret = (d.equity - result.initial_capital) / result.initial_capital * 100
        bar_len = int(abs(ret) / 1.5)
        if ret >= 0:
            bar = "█" * min(bar_len, 40)
            lines.append(f"  {d.date}  ¥{d.equity:>10,.0f}  {ret:>+6.1f}%  |{bar}")
        else:
            bar = "░" * min(bar_len, 40)
            lines.append(f"  {d.date}  ¥{d.equity:>10,.0f}  {ret:>+6.1f}%  {bar}|")

    last = result.daily[-1]
    ret = (last.equity - result.initial_capital) / result.initial_capital * 100
    bar_len = int(abs(ret) / 1.5)
    bar = ("█" * min(bar_len, 40) if ret >= 0 else "░" * min(bar_len, 40))
    sep = "|" if ret >= 0 else ""
    lines.append(f"  {last.date}  ¥{last.equity:>10,.0f}  {ret:>+6.1f}%  {sep}{bar}  ← 今日")

    lines.append(f"")
    lines.append(f"  ── 持仓变化 ──")
    key_dates = [0] + [i for i in range(1, len(result.daily))
                       if result.daily[i].shares != result.daily[i-1].shares] + [len(result.daily)-1]
    seen = set()
    for i in key_dates:
        d = result.daily[i]
        if d.date in seen:
            continue
        seen.add(d.date)
        lines.append(f"  {d.date}  持仓{d.shares:>5}股  仓位{d.position_pct:>5.1f}%  "
                      f"现金¥{d.cash:>10,.0f}  回撤{d.drawdown:.1f}%")

    lines.append(f"{'═' * 68}")
    return "\n".join(lines)
