"""Rule-based Technical Analyst for 603799.

Scores range from -100 (strong sell) to +100 (strong buy).
Each sub-signal contributes a weighted component to the total score.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from agents.base import AgentResult

logger = logging.getLogger(__name__)


def _score_ma_alignment(latest: pd.Series) -> tuple[float, list[str]]:
    """Score moving average alignment. Max ±25 points."""
    signals = []
    score = 0.0

    ma5 = latest.get("ma5", np.nan)
    ma10 = latest.get("ma10", np.nan)
    ma20 = latest.get("ma20", np.nan)
    ma60 = latest.get("ma60", np.nan)

    if any(pd.isna(v) for v in [ma5, ma10, ma20, ma60]):
        return 0, []

    if ma5 > ma10 > ma20 > ma60:
        score = 25
        signals.append("均线多头排列 (MA5>10>20>60)")
    elif ma5 < ma10 < ma20 < ma60:
        score = -25
        signals.append("均线空头排列 (MA5<10<20<60)")
    elif ma5 > ma20:
        score = 10
        signals.append("短期均线上穿中期 (MA5>MA20)")
    elif ma5 < ma20:
        score = -10
        signals.append("短期均线下穿中期 (MA5<MA20)")

    return score, signals


def _score_macd(latest: pd.Series, prev: pd.Series | None) -> tuple[float, list[str]]:
    """Score MACD. Max ±20 points."""
    signals = []
    score = 0.0

    hist = latest.get("macd_hist", np.nan)
    if pd.isna(hist):
        return 0, []

    prev_hist = prev.get("macd_hist", np.nan) if prev is not None else np.nan

    if not pd.isna(prev_hist):
        if hist > 0 and prev_hist <= 0:
            score = 20
            signals.append("MACD金叉 (histogram转正)")
        elif hist < 0 and prev_hist >= 0:
            score = -20
            signals.append("MACD死叉 (histogram转负)")
        elif hist > 0:
            score = 10 if hist > prev_hist else 5
            signals.append("MACD多头持续" if hist > prev_hist else "MACD多头趋弱")
        else:
            score = -10 if hist < prev_hist else -5
            signals.append("MACD空头加速" if hist < prev_hist else "MACD空头趋缓")

    return score, signals


def _score_rsi(latest: pd.Series) -> tuple[float, list[str]]:
    """Score RSI. Max ±20 points."""
    signals = []
    score = 0.0

    rsi12 = latest.get("rsi12", np.nan)
    if pd.isna(rsi12):
        return 0, []

    if rsi12 < 20:
        score = 20
        signals.append(f"RSI12极度超卖 ({rsi12:.0f})")
    elif rsi12 < 30:
        score = 15
        signals.append(f"RSI12超卖 ({rsi12:.0f})")
    elif rsi12 > 80:
        score = -20
        signals.append(f"RSI12极度超买 ({rsi12:.0f})")
    elif rsi12 > 70:
        score = -15
        signals.append(f"RSI12超买 ({rsi12:.0f})")
    elif 40 <= rsi12 <= 60:
        score = 0
        signals.append(f"RSI12中性 ({rsi12:.0f})")

    return score, signals


def _score_kdj(latest: pd.Series) -> tuple[float, list[str]]:
    """Score KDJ. Max ±15 points."""
    signals = []
    score = 0.0

    k = latest.get("kdj_k", np.nan)
    d = latest.get("kdj_d", np.nan)
    j = latest.get("kdj_j", np.nan)
    if pd.isna(k) or pd.isna(d):
        return 0, []

    if k > d and not pd.isna(j) and j > 80:
        score = -10
        signals.append(f"KDJ超买区金叉 (J={j:.0f}, 警惕回调)")
    elif k > d and not pd.isna(j) and j < 20:
        score = 15
        signals.append(f"KDJ超卖区金叉 (J={j:.0f}, 反弹信号)")
    elif k > d:
        score = 8
        signals.append("KDJ金叉")
    elif k < d and not pd.isna(j) and j > 80:
        score = -15
        signals.append(f"KDJ超买区死叉 (J={j:.0f})")
    elif k < d:
        score = -8
        signals.append("KDJ死叉")

    return score, signals


def _score_bollinger(latest: pd.Series, latest_price: float) -> tuple[float, list[str]]:
    """Score Bollinger Band position. Max ±10 points."""
    signals = []
    score = 0.0

    upper = latest.get("boll_upper", np.nan)
    lower = latest.get("boll_lower", np.nan)
    mid = latest.get("boll_mid", np.nan)
    if pd.isna(upper) or pd.isna(lower):
        return 0, []

    if latest_price <= lower:
        score = 10
        signals.append(f"触及布林下轨 ({lower:.2f})")
    elif latest_price >= upper:
        score = -10
        signals.append(f"触及布林上轨 ({upper:.2f})")
    elif latest_price < mid:
        score = 3
        signals.append("价格低于布林中轨")
    else:
        score = -3
        signals.append("价格高于布林中轨")

    return score, signals


def _score_volume(ohlcv: pd.DataFrame) -> tuple[float, list[str]]:
    """Score recent volume pattern. Max ±10 points."""
    signals = []
    score = 0.0

    if len(ohlcv) < 21:
        return 0, []

    recent_vol = ohlcv["volume"].iloc[-1]
    avg_vol = ohlcv["volume"].iloc[-21:-1].mean()
    price_chg = ohlcv["close"].pct_change().iloc[-1]

    ratio = recent_vol / avg_vol if avg_vol > 0 else 1

    if ratio > 2.0 and price_chg > 0.02:
        score = 10
        signals.append(f"放量上涨 (量比{ratio:.1f})")
    elif ratio > 2.0 and price_chg < -0.02:
        score = -10
        signals.append(f"放量下跌 (量比{ratio:.1f})")
    elif ratio < 0.5 and price_chg > 0:
        score = -3
        signals.append(f"缩量上涨 (量比{ratio:.1f}, 动能不足)")
    elif ratio < 0.5 and price_chg < 0:
        score = 5
        signals.append(f"缩量回调 (量比{ratio:.1f}, 抛压减轻)")

    return score, signals


def _find_support_resistance(ohlcv: pd.DataFrame) -> tuple[float, float]:
    """Find nearest support and resistance from recent 60-day highs/lows."""
    if len(ohlcv) < 60:
        return 0.0, 0.0

    recent = ohlcv.tail(60)
    current = float(ohlcv.iloc[-1]["close"])
    lows = recent["low"].values
    highs = recent["high"].values

    below = lows[lows < current]
    above = highs[highs > current]

    support = float(np.max(below)) if len(below) > 0 else float(recent["low"].min())
    resistance = float(np.min(above)) if len(above) > 0 else float(recent["high"].max())

    return round(support, 2), round(resistance, 2)


def analyze(ohlcv: pd.DataFrame, indicators: pd.DataFrame,
            **_kwargs) -> AgentResult:
    """Rule-based technical analysis on the latest data."""
    if ohlcv.empty or indicators.empty:
        return AgentResult(agent_name="technical", score=0, confidence=0,
                           signals=["数据不足"], details={})

    latest_ind = indicators.iloc[-1]
    prev_ind = indicators.iloc[-2] if len(indicators) > 1 else None
    latest_price = float(ohlcv.iloc[-1]["close"])

    total_score = 0.0
    all_signals = []

    for scorer in [
        lambda: _score_ma_alignment(latest_ind),
        lambda: _score_macd(latest_ind, prev_ind),
        lambda: _score_rsi(latest_ind),
        lambda: _score_kdj(latest_ind),
        lambda: _score_bollinger(latest_ind, latest_price),
        lambda: _score_volume(ohlcv),
    ]:
        s, sigs = scorer()
        total_score += s
        all_signals.extend(sigs)

    total_score = max(-100, min(100, total_score))

    if total_score > 30:
        trend = "bullish"
    elif total_score < -30:
        trend = "bearish"
    else:
        trend = "neutral"

    support, resistance = _find_support_resistance(ohlcv)

    return AgentResult(
        agent_name="technical",
        score=total_score,
        confidence=0.0,
        signals=all_signals,
        details={
            "trend": trend,
            "support": support,
            "resistance": resistance,
            "latest_price": latest_price,
            "atr14": round(float(latest_ind.get("atr14", 0)), 2),
        },
    )
