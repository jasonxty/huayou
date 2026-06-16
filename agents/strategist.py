"""Chief Strategist — rule-based synthesis of agent results into a morning brief.

Grounding rule: the brief may ONLY cite numbers present in agent outputs
or backtest results. No LLM calls needed.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import numpy as np
import pandas as pd

import config
from agents.base import AgentResult
from agents.t0_advisor import T0Advice
from backtest.engine import BacktestResult
from data.catalysts import CatalystSnapshot
from data.news import NewsSentiment
from data.taoguba import ExpertSnapshot

logger = logging.getLogger(__name__)

_BUCKET_LABEL_RE = re.compile(r"(\d+)-(\d+)%")


def parse_calibration_bucket_label(label: str) -> tuple[float, float]:
    m = _BUCKET_LABEL_RE.search(label or "")
    if not m:
        return 0.50, 0.60
    return int(m.group(1)) / 100.0, int(m.group(2)) / 100.0


def effective_signal_score(tech_score: float, fund_score: float, weights: dict) -> float:
    """技术面与基本面加权合成，用于与阈值比较（Buffett 决策核）。"""
    ft = weights.get("fundamental_trust", 1.0)
    contrib = ft * fund_score * config.KOBE_FUND_BLEND_RATIO
    return float(np.clip(tech_score + contrib, -100.0, 100.0))


def apply_confidence_calibration(
    confidence: float,
    buckets: list[dict] | None,
) -> float:
    """用最近一次校准 bucket 的 actual/predicted 比值缩放置信度。"""
    if not buckets:
        return confidence
    lo_out, hi_out = 0.30, 0.90
    r_lo, r_hi = config.KOBE_CALIBRATION_RATIO_CLAMP

    for b in buckets:
        cnt = b.get("count") or 0
        if cnt < 5:
            continue
        blo, bhi = parse_calibration_bucket_label(b.get("bucket", ""))
        pred = b.get("predicted")
        act = b.get("actual")
        if pred is None or pred <= 0 or act is None:
            continue
        if not (blo <= confidence < bhi):
            continue
        ratio = act / pred
        ratio = max(r_lo, min(r_hi, ratio))
        cal = confidence * ratio
        return float(max(lo_out, min(hi_out, cal)))
    return confidence


# ── Regime matching ──

def classify_regime(indicators: pd.DataFrame, ohlcv: pd.DataFrame | None = None) -> dict:
    """Discretize latest indicator state into a multi-dimension regime vector.

    Dimensions:
        trend: up / down / sideways (based on MA20 vs MA60)
        rsi: oversold / neutral / overbought (RSI12 buckets)
        volatility: low / normal / high (ATR14 vs 20-day average)
        momentum: accelerating / decelerating / flat (MACD histogram slope)
        consecutive_down: number of consecutive down days (0+)
        recent_drop_pct: cumulative drop over the last 5 trading days
    """
    if indicators.empty:
        return {"trend": "unknown", "rsi": "unknown", "volatility": "normal",
                "momentum": "flat", "consecutive_down": 0, "recent_drop_pct": 0.0,
                "strategy_mode": "default"}

    latest = indicators.iloc[-1]

    ma20 = latest.get("ma20", np.nan)
    ma60 = latest.get("ma60", np.nan)
    if pd.isna(ma20) or pd.isna(ma60):
        trend = "unknown"
    elif ma20 > ma60 * 1.02:
        trend = "up"
    elif ma20 < ma60 * 0.98:
        trend = "down"
    else:
        trend = "sideways"

    rsi = latest.get("rsi12", np.nan)
    if pd.isna(rsi):
        rsi_bucket = "unknown"
    elif rsi < 30:
        rsi_bucket = "oversold"
    elif rsi > 70:
        rsi_bucket = "overbought"
    else:
        rsi_bucket = "neutral"

    atr = latest.get("atr14", np.nan)
    if pd.isna(atr) or len(indicators) < 20:
        vol = "normal"
    else:
        avg_atr = indicators["atr14"].iloc[-20:].mean()
        if atr > avg_atr * 1.3:
            vol = "high"
        elif atr < avg_atr * 0.7:
            vol = "low"
        else:
            vol = "normal"

    macd_hist = latest.get("macd_hist", np.nan)
    if pd.isna(macd_hist) or len(indicators) < 3:
        momentum = "flat"
    else:
        prev_hist = indicators["macd_hist"].iloc[-3]
        if pd.isna(prev_hist):
            momentum = "flat"
        elif macd_hist > prev_hist + 0.05:
            momentum = "accelerating"
        elif macd_hist < prev_hist - 0.05:
            momentum = "decelerating"
        else:
            momentum = "flat"

    consecutive_down = 0
    recent_drop_pct = 0.0
    if ohlcv is not None and len(ohlcv) >= 2:
        closes = ohlcv["close"].values
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < closes[i - 1]:
                consecutive_down += 1
            else:
                break
        if len(closes) >= 6:
            recent_drop_pct = (closes[-1] / closes[-6] - 1) * 100

    strategy_mode = _classify_strategy_mode(
        trend, rsi_bucket, vol, momentum, consecutive_down, recent_drop_pct
    )

    return {
        "trend": trend, "rsi": rsi_bucket,
        "volatility": vol, "momentum": momentum,
        "consecutive_down": consecutive_down,
        "recent_drop_pct": round(recent_drop_pct, 2),
        "strategy_mode": strategy_mode,
    }


# ── Per-regime strategy modes ──

REGIME_STRATEGIES = {
    "contrarian_bounce": {
        "label": "Contrarian Bounce",
        "desc": "Deep oversold — look for reversal entries",
        "buy_adjust": -8,
        "sell_adjust": -5,
        "position_pct": 30,
    },
    "trend_follow": {
        "label": "Trend Following",
        "desc": "Uptrend confirmed — ride momentum, trail stops",
        "buy_adjust": -5,
        "sell_adjust": 5,
        "position_pct": 70,
    },
    "swing_range": {
        "label": "Swing / Range-Bound",
        "desc": "Sideways market — buy support, sell resistance",
        "buy_adjust": 0,
        "sell_adjust": 0,
        "position_pct": 50,
    },
    "defensive_exit": {
        "label": "Defensive / Exit",
        "desc": "Strong downtrend — protect capital, tight stops",
        "buy_adjust": 10,
        "sell_adjust": -8,
        "position_pct": 20,
    },
    "capitulation_watch": {
        "label": "Capitulation Watch",
        "desc": "Panic selling — extreme drop, watch for snap-back",
        "buy_adjust": -12,
        "sell_adjust": -10,
        "position_pct": 25,
    },
    "default": {
        "label": "Standard",
        "desc": "Normal conditions — use base thresholds",
        "buy_adjust": 0,
        "sell_adjust": 0,
        "position_pct": 50,
    },
}


def _classify_strategy_mode(
    trend: str, rsi: str, vol: str, momentum: str,
    consecutive_down: int, recent_drop_pct: float,
) -> str:
    """Map regime dimensions to a strategy mode."""
    if consecutive_down >= 5 or recent_drop_pct <= -10:
        return "capitulation_watch"

    if trend == "down" and rsi == "oversold":
        return "contrarian_bounce"

    if trend == "down" and vol == "high" and momentum == "decelerating":
        return "defensive_exit"

    if trend == "down" and rsi == "neutral":
        return "defensive_exit"

    if trend == "up" and momentum == "accelerating":
        return "trend_follow"

    if trend == "up" and rsi == "neutral":
        return "trend_follow"

    if trend == "sideways":
        return "swing_range"

    return "default"


def match_historical_regime(
    indicators: pd.DataFrame,
    ohlcv: pd.DataFrame,
    current_regime: dict,
    forward_days: int = 5,
) -> dict:
    """Find historical occurrences of the same regime and compute forward returns."""
    if len(indicators) < forward_days + 1:
        return {"count": 0, "avg_return": 0, "win_rate": 0, "sufficient": False}

    matches = []
    for i in range(len(indicators) - forward_days):
        row = indicators.iloc[i]
        ma20 = row.get("ma20", np.nan)
        ma60 = row.get("ma60", np.nan)
        rsi = row.get("rsi12", np.nan)

        if pd.isna(ma20) or pd.isna(ma60) or pd.isna(rsi):
            continue

        if ma20 > ma60 * 1.02:
            t = "up"
        elif ma20 < ma60 * 0.98:
            t = "down"
        else:
            t = "sideways"

        if rsi < 30:
            r = "oversold"
        elif rsi > 70:
            r = "overbought"
        else:
            r = "neutral"

        if t == current_regime["trend"] and r == current_regime["rsi"]:
            entry_price = ohlcv.iloc[i]["close"]
            exit_price = ohlcv.iloc[i + forward_days]["close"]
            ret = (exit_price - entry_price) / entry_price
            matches.append(ret)

    count = len(matches)
    sufficient = count >= config.REGIME_MIN_SAMPLES

    return {
        "count": count,
        "avg_return": float(np.mean(matches)) if matches else 0,
        "win_rate": float(sum(1 for r in matches if r > 0) / count) if count > 0 else 0,
        "sufficient": sufficient,
    }


# ── Grounding validator ──

def validate_grounding(brief_text: str, agent_results: list[AgentResult],
                       backtest_results: list[BacktestResult],
                       regime_match: dict | None = None,
                       catalysts: CatalystSnapshot | None = None) -> list[str]:
    """Check that all numbers in the brief are traceable to inputs.

    Returns list of violation descriptions (empty = clean).
    """
    allowed_numbers = set()

    for ar in agent_results:
        allowed_numbers.add(round(ar.score, 2))
        allowed_numbers.add(round(ar.confidence * 100, 1))
        for v in ar.details.values():
            if isinstance(v, (int, float)):
                allowed_numbers.add(round(float(v), 2))

    for bt in backtest_results:
        allowed_numbers.add(round(bt.win_rate * 100, 1))
        allowed_numbers.add(round(bt.sharpe, 2))
        allowed_numbers.add(round(abs(bt.sharpe), 2))
        allowed_numbers.add(round(bt.max_drawdown * 100, 1))
        allowed_numbers.add(round(bt.profit_factor, 2))
        allowed_numbers.add(bt.total_trades)

    if regime_match:
        allowed_numbers.add(regime_match.get("count", 0))
        allowed_numbers.add(round(regime_match.get("avg_return", 0) * 100, 1))
        allowed_numbers.add(round(regime_match.get("win_rate", 0) * 100, 0))

    if catalysts:
        if catalysts.lme_nickel_usd:
            allowed_numbers.add(catalysts.lme_nickel_usd)
        if catalysts.lme_nickel_cny:
            allowed_numbers.add(catalysts.lme_nickel_cny)
        if catalysts.nickel_change_pct is not None:
            allowed_numbers.add(round(abs(catalysts.nickel_change_pct), 2))
            allowed_numbers.add(round(catalysts.nickel_change_pct, 2))

    for ar in agent_results:
        for v in ar.signals:
            for n in re.findall(r"[\d]+\.?\d*", v):
                try:
                    allowed_numbers.add(float(n))
                except ValueError:
                    pass

    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}", "", brief_text)
    cleaned = re.sub(r"── KEY CATALYSTS.*?── (NEWS|EXPERT|T\+0|REGIME)", r"── \1", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"── NEWS SENTIMENT.*?── (EXPERT|T\+0|REGIME)", r"── \1", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"── EXPERT OPINIONS.*?── (T\+0|REGIME)", r"── \1", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"── T\+0.*?── (REGIME|HISTORICAL)", r"── \1", cleaned, flags=re.DOTALL)
    cleaned = cleaned.replace(",", "")
    found_numbers = re.findall(r"[\d]+\.?\d*", cleaned)
    violations = []

    for num_str in found_numbers:
        try:
            num = float(num_str)
        except ValueError:
            continue
        if num in (0, 1, 2, 3, 5, 10, 14, 100):
            continue
        if num > 2020:
            continue
        if num <= 120 and num == int(num):
            continue
        if any(abs(num - allowed) < 0.1 for allowed in allowed_numbers):
            continue
        violations.append(f"Number {num_str} in brief not found in agent/backtest outputs")

    return violations


# ── Rule-based action decision ──

def compute_commodity_signal(catalysts: CatalystSnapshot | None) -> float:
    """Score based on commodity price momentum (-15 to +15).

    SHFE nickel day-change is the primary driver since it directly
    affects Huayou's smelting margins.
    """
    if not catalysts:
        return 0.0

    score = 0.0

    if catalysts.shfe_nickel_chg and catalysts.shfe_nickel:
        pct = catalysts.shfe_nickel_chg / catalysts.shfe_nickel * 100
        if pct > 2.0:
            score += 12
        elif pct > 1.0:
            score += 6
        elif pct > 0.3:
            score += 3
        elif pct < -2.0:
            score -= 12
        elif pct < -1.0:
            score -= 6
        elif pct < -0.3:
            score -= 3

    if catalysts.lithium_carbonate_chg and catalysts.lithium_carbonate:
        li_pct = catalysts.lithium_carbonate_chg / catalysts.lithium_carbonate * 100
        if li_pct > 2.0:
            score += 3
        elif li_pct < -2.0:
            score -= 3

    return float(np.clip(score, -15, 15))


def _decide_action(
    tech_score: float,
    fund_score: float,
    regime: dict,
    regime_match: dict,
    best_strategy: BacktestResult | None,
    weights: dict | None = None,
    commodity_signal: float = 0.0,
) -> tuple[str, str, str]:
    """Deterministic action, risk level, and position % from scores.

    Uses Buffett's learned weights + regime-aware dynamic strategy +
    基本面合成得分 + 商品期货领先信号。

    Returns (action, risk_level, position_advice).
    """
    w = weights or config.KOBE_DEFAULT_WEIGHTS
    signal = effective_signal_score(tech_score, fund_score, w)

    cw = w.get("commodity_weight", 0.5)
    signal += commodity_signal * cw

    strategy_mode = regime.get("strategy_mode", "default")
    strat = REGIME_STRATEGIES.get(strategy_mode, REGIME_STRATEGIES["default"])

    rk = f"{regime.get('trend', 'unknown')}|{regime.get('rsi', 'unknown')}"
    rb = (w.get("regime_buy_adjust") or {}).get(rk, 0)
    rs = (w.get("regime_sell_adjust") or {}).get(rk, 0)

    buy_adj = strat["buy_adjust"]
    sell_adj = strat["sell_adjust"]

    buy_thresh = w.get("tech_buy_threshold", 25) + rb + buy_adj
    mild_buy = w.get("tech_mild_buy_threshold", 12) + int(round((rb + buy_adj) * 0.55))
    sell_thresh = w.get("tech_sell_threshold", -25) - rs + sell_adj
    mild_sell = w.get("tech_mild_sell_threshold", -12) - int(round((rs - sell_adj) * 0.55))

    has_backtest_edge = best_strategy is not None and best_strategy.passes_threshold
    regime_oversold = regime.get("rsi") == "oversold"
    regime_overbought = regime.get("rsi") == "overbought"
    pos_pct = strat["position_pct"]

    if strategy_mode == "capitulation_watch":
        if signal >= mild_buy or regime_oversold:
            action = "BUY (capitulation bounce)"
            risk = "HIGH"
        else:
            action = "HOLD (panic — wait for volume exhaustion)"
            risk = "HIGH"
        return action, risk, f"{pos_pct}%"

    if strategy_mode == "contrarian_bounce":
        if signal >= mild_buy:
            action = "BUY (oversold bounce)"
            risk = "HIGH"
        elif signal <= sell_thresh:
            action = "HOLD (oversold — avoid selling into weakness)"
            risk = "HIGH"
        else:
            action = "HOLD (watch for reversal signal)"
            risk = "MEDIUM"
        return action, risk, f"{pos_pct}%"

    if strategy_mode == "trend_follow":
        if signal >= buy_thresh and not regime_overbought:
            action = "BUY (trend continuation)" if has_backtest_edge else "BUY (add to position)"
            risk = "LOW" if has_backtest_edge else "MEDIUM"
        elif signal >= mild_buy:
            action = "BUY (pullback entry)"
            risk = "MEDIUM"
        elif signal <= sell_thresh:
            action = "SELL (trend reversal warning)"
            risk = "HIGH"
        elif signal <= mild_sell:
            action = "SELL (trail stop hit)"
            risk = "MEDIUM"
        else:
            action = "HOLD (ride the trend)"
            risk = "LOW"
        return action, risk, f"{pos_pct}%"

    if strategy_mode == "swing_range":
        if signal >= buy_thresh:
            action = "BUY (near support)" if not regime_overbought else "HOLD (near resistance)"
            risk = "MEDIUM"
        elif signal >= mild_buy:
            action = "BUY (range bottom)"
            risk = "MEDIUM"
        elif signal <= sell_thresh:
            action = "SELL (near resistance)"
            risk = "MEDIUM"
        elif signal <= mild_sell:
            action = "SELL (range top)"
            risk = "MEDIUM"
        else:
            action = "HOLD (mid-range)"
            risk = "LOW"
        return action, risk, f"{pos_pct}%"

    if strategy_mode == "defensive_exit":
        if signal >= buy_thresh and not regime_overbought:
            action = "BUY (light probe)" if has_backtest_edge else "HOLD (too risky to add)"
            risk = "HIGH"
        elif signal <= mild_sell:
            action = "SELL (protect capital)"
            risk = "HIGH"
        elif signal <= sell_thresh:
            action = "SELL (stop loss — downtrend)"
            risk = "HIGH"
        else:
            action = "HOLD (wait for trend change)"
            risk = "MEDIUM"
        return action, risk, f"{pos_pct}%"

    if signal >= buy_thresh and not regime_overbought:
        action = "BUY (aggressive)" if has_backtest_edge else "BUY (light probe)"
        risk = "MEDIUM" if has_backtest_edge else "HIGH"
        if fund_score <= config.KOBE_FUND_BLOCK_AGGRESSIVE_BELOW:
            action = "BUY (light probe)" if has_backtest_edge else "BUY (light)"
            risk = "HIGH"
    elif signal >= mild_buy:
        action = "BUY (light)"
        risk = "MEDIUM"
    elif signal <= sell_thresh and not regime_oversold:
        action = "SELL (reduce)" if has_backtest_edge else "SELL (stop loss)"
        risk = "HIGH"
    elif signal <= mild_sell:
        action = "SELL (trim)" if not regime_oversold else "HOLD (oversold bounce possible)"
        risk = "HIGH"
    else:
        action = "HOLD (range-bound)"
        risk = "LOW"

    return action, risk, f"{pos_pct}%"


# ── Expert confidence adjustment ──

def _expert_confidence_adjustment(snapshot: ExpertSnapshot | None,
                                  tech_score: float) -> float:
    """Adjust confidence based on expert consensus.

    Rules:
    - Need at least 2 experts with recent posts to have an opinion
    - If 2/3+ experts align with technical direction: +0.08
    - If 2/3+ experts contradict technical direction: -0.08 (divergence warning)
    - Split opinions or insufficient data: no adjustment
    """
    if not snapshot or not snapshot.posts:
        return 0.0

    unique_experts = {p.expert_id for p in snapshot.posts}
    if len(unique_experts) < 2:
        return 0.0

    total = snapshot.bullish_count + snapshot.bearish_count + snapshot.neutral_count
    if total == 0:
        return 0.0

    tech_bullish = tech_score > 0
    expert_bullish_ratio = snapshot.bullish_count / total
    expert_bearish_ratio = snapshot.bearish_count / total

    if tech_bullish and expert_bullish_ratio >= 2 / 3:
        return 0.08
    if not tech_bullish and expert_bearish_ratio >= 2 / 3:
        return 0.08
    if tech_bullish and expert_bearish_ratio >= 2 / 3:
        return -0.08
    if not tech_bullish and expert_bullish_ratio >= 2 / 3:
        return -0.08

    return 0.0


# ── Yesterday retro + tomorrow forecast ──


def build_yesterday_retro(conn, today: str) -> str:
    """Compare yesterday's brief prediction with today's actual open/close.

    Returns a formatted text block, or empty string if no prior brief exists.
    """
    import json as _json

    row = conn.execute(
        """SELECT b.date, b.action, b.confidence, b.agent_summary_json,
                  o_today.open AS today_open, o_today.close AS today_close,
                  o_prev.close AS prev_close
           FROM briefs b
           LEFT JOIN ohlcv o_prev ON o_prev.date = b.date
           LEFT JOIN ohlcv o_today ON o_today.date = ?
           WHERE b.date < ?
           ORDER BY b.date DESC LIMIT 1""",
        (today, today),
    ).fetchone()

    if not row or row[0] is None:
        return ""

    prev_date, prev_action, prev_conf = row[0], row[1], row[2]
    summary_json = row[3]
    today_open, today_close, prev_close = row[4], row[5], row[6]

    if not prev_close or prev_close <= 0 or not today_close:
        return ""

    ret_pct = (today_close - prev_close) / prev_close * 100
    gap_pct = (today_open - prev_close) / prev_close * 100 if today_open else 0

    prev_prediction = ""
    if summary_json:
        try:
            blob = _json.loads(summary_json)
            prev_prediction = blob.get("tomorrow_prediction", "")
        except (ValueError, TypeError):
            pass

    action_upper = (prev_action or "").upper()
    if "BUY" in action_upper:
        correct = ret_pct > 0
        verdict = "Correct — price rose" if correct else "Wrong — price fell"
    elif "SELL" in action_upper:
        correct = ret_pct < 0
        verdict = "Correct — price dropped" if correct else "Wrong — price rose"
    else:
        correct = abs(ret_pct) < 1.5
        verdict = "Correct — market stayed flat" if correct else f"Missed — market moved {ret_pct:+.1f}%"

    icon = "✅" if correct else "❌"

    lines = f"\n  ── YESTERDAY'S RETRO ({prev_date}) ──\n"
    lines += f"  Signal was: {prev_action} (conf {prev_conf*100:.0f}%)\n"
    if prev_prediction:
        lines += f"  Prediction: {prev_prediction}\n"
    lines += f"  Actual: open {gap_pct:+.1f}% gap, close {ret_pct:+.1f}% vs prev close\n"
    lines += f"  {icon} Verdict: {verdict}\n"

    return lines


def build_tomorrow_forecast(
    tech_score: float,
    fund_score: float,
    current_regime: dict,
    regime_match: dict,
    action: str,
    confidence: float,
    support: float | str,
    resistance: float | str,
    latest_price: float,
) -> tuple[str, str]:
    """Generate a short next-day prediction and key levels to watch.

    Returns (brief_text_block, prediction_summary_for_storage).
    """
    trend = current_regime.get("trend", "unknown")
    rsi_state = current_regime.get("rsi", "unknown")

    if regime_match.get("sufficient"):
        hist_wr = regime_match["win_rate"]
        hist_avg = regime_match["avg_return"] * 100
    else:
        hist_wr = 0.5
        hist_avg = 0.0

    if "BUY" in action.upper():
        direction = "up"
        reason = "bullish technicals + buy signal"
    elif "SELL" in action.upper():
        direction = "down"
        reason = "bearish technicals + sell signal"
    else:
        if hist_avg > 0.3:
            direction = "slightly up"
            reason = f"historical pattern ({hist_wr*100:.0f}% win rate, avg {hist_avg:+.1f}%)"
        elif hist_avg < -0.3:
            direction = "slightly down"
            reason = f"historical pattern ({hist_wr*100:.0f}% win rate, avg {hist_avg:+.1f}%)"
        else:
            direction = "range-bound"
            reason = "no strong directional signal"

    try:
        sup_val = float(support)
        res_val = float(resistance)
    except (ValueError, TypeError):
        sup_val = latest_price * 0.98
        res_val = latest_price * 1.02

    prediction_text = (
        f"{direction} — {reason}"
    )

    lines = f"\n  ── TOMORROW'S OUTLOOK ──\n"
    lines += f"  Expected: {direction}\n"
    lines += f"  Reasoning: {reason}\n"
    lines += f"  Key levels: support ¥{sup_val:.2f} / resistance ¥{res_val:.2f}\n"

    if "BUY" in action.upper():
        lines += f"  Watch for: breakout above ¥{res_val:.2f} to confirm entry\n"
    elif "SELL" in action.upper():
        lines += f"  Watch for: breakdown below ¥{sup_val:.2f} to confirm exit\n"
    else:
        lines += f"  Watch for: stay between ¥{sup_val:.2f}–¥{res_val:.2f}, wait for breakout\n"

    if rsi_state == "overbought":
        lines += "  ⚠ RSI overbought — potential pullback risk\n"
    elif rsi_state == "oversold":
        lines += "  ⚠ RSI oversold — potential bounce opportunity\n"

    return lines, prediction_text


# ── Synthesis ──

def synthesize(
    agent_results: list[AgentResult],
    backtest_results: list[BacktestResult],
    regime_match: dict,
    current_regime: dict,
    latest_price: float,
    catalysts: CatalystSnapshot | None = None,
    t0_advice: T0Advice | None = None,
    news_sentiment: NewsSentiment | None = None,
    expert_snapshot: ExpertSnapshot | None = None,
    analysis_date: str | None = None,
    kobe_weights: dict | None = None,
    calibration_buckets: list[dict] | None = None,
    conn=None,
) -> dict:
    """Produce the morning brief. Pure rule-based, no LLM needed.

    Uses Buffett's learned weights when provided.
    """
    today = analysis_date or date.today().isoformat()

    best_strategy = None
    if backtest_results:
        passing = [b for b in backtest_results if b.passes_threshold]
        if passing:
            best_strategy = max(passing, key=lambda b: b.win_rate)

    if best_strategy and regime_match.get("sufficient"):
        raw_conf = regime_match["win_rate"]
    elif best_strategy:
        raw_conf = best_strategy.win_rate
    else:
        raw_conf = 0.5

    lo, hi = config.REGIME_CONFIDENCE_CLAMP
    confidence = max(lo, min(hi, raw_conf))

    tech_result = next((a for a in agent_results if a.agent_name == "technical"), None)
    fund_result = next((a for a in agent_results if a.agent_name == "fundamental"), None)
    tech_score = tech_result.score if tech_result else 0
    fund_score = fund_result.score if fund_result else 0
    support = tech_result.details.get("support", "N/A") if tech_result else "N/A"
    resistance = tech_result.details.get("resistance", "N/A") if tech_result else "N/A"
    atr = tech_result.details.get("atr14", "N/A") if tech_result else "N/A"

    w = kobe_weights or config.KOBE_DEFAULT_WEIGHTS
    expert_mag = w.get("expert_adjustment", 0.08)
    expert_adj = _expert_confidence_adjustment(expert_snapshot, tech_score)
    if expert_adj != 0:
        expert_adj = expert_mag if expert_adj > 0 else -expert_mag

    regime_trust = w.get("regime_trust", 1.0)
    confidence = max(0.30, min(0.90, confidence * regime_trust + expert_adj))
    confidence = apply_confidence_calibration(confidence, calibration_buckets)

    commodity_sig = compute_commodity_signal(catalysts)

    action, risk_level, position_advice = _decide_action(
        tech_score,
        fund_score,
        current_regime,
        regime_match,
        best_strategy,
        weights=w,
        commodity_signal=commodity_sig,
    )

    regime_line = (
        f"  Similar setup occurred {regime_match['count']} times in 603799's history.\n"
        f"  {5}-day forward return: {regime_match['avg_return']*100:+.1f}% avg "
        f"(win rate {regime_match['win_rate']*100:.0f}%)"
        if regime_match.get("sufficient")
        else f"  Insufficient historical data ({regime_match['count']} matches, "
             f"need {config.REGIME_MIN_SAMPLES})"
    )

    bt_lines = ""
    for bt in backtest_results:
        status = "✓" if bt.passes_threshold else "✗"
        bt_lines += (
            f"  {status} {bt.strategy:<18} win={bt.win_rate*100:5.1f}%  "
            f"sharpe={bt.sharpe:5.2f}  dd={bt.max_drawdown*100:5.1f}%\n"
        )

    retro_text = ""
    if conn is not None:
        try:
            retro_text = build_yesterday_retro(conn, today)
        except Exception as e:
            logger.warning("Failed to build yesterday retro: %s", e)

    brief_text = f"""{'═' * 56}
  {config.KOBE_AVATAR} {config.KOBE_NAME}'s Morning Brief — {today}
  {config.TICKER_NAME} ({config.TICKER})
{'═' * 56}
"""

    if retro_text:
        brief_text += retro_text

    strat_mode = current_regime.get("strategy_mode", "default")
    strat_info = REGIME_STRATEGIES.get(strat_mode, REGIME_STRATEGIES["default"])
    consec = current_regime.get("consecutive_down", 0)
    drop5 = current_regime.get("recent_drop_pct", 0)
    vol_label = current_regime.get("volatility", "normal")

    brief_text += f"""
  {config.KOBE_NAME} says: {action}
  CONFIDENCE: {confidence*100:.0f}%  |  RISK: {risk_level}  |  Position: {position_advice}
  PRICE:      {latest_price:.2f}  |  ATR(14): {atr}

  ── STRATEGY MODE: {strat_info['label']} ──
  {strat_info['desc']}
  Regime: {current_regime['trend']} / {current_regime['rsi']} / vol={vol_label} / momentum={current_regime.get('momentum', 'flat')}
  Streak: {consec} consecutive down days  |  5-day change: {drop5:+.1f}%

  ── TECHNICAL SIGNALS (score: {tech_score:+.0f}/100) ──
"""
    if tech_result:
        for sig in tech_result.signals:
            brief_text += f"    • {sig}\n"

    if fund_result:
        fd = fund_result.details
        brief_text += f"""
  ── FUNDAMENTAL (score: {fund_score:+.0f}/100) ──
  {fd.get('report_period', '')}  市值{fd.get('market_cap', 0):.0f}亿  PE(TTM){fd.get('pe_ttm', 0):.1f}x  PB{fd.get('pb', 0):.1f}x
  毛利率{fd.get('gross_margin', 0):.1f}%  净利率{fd.get('net_margin', 0):.1f}%  ROE{fd.get('roe', 0):.1f}%  负债率{fd.get('debt_ratio', 0):.1f}%
  营收YoY{fd.get('revenue_yoy', 0):+.0f}%  归母净利YoY{fd.get('profit_yoy', 0):+.0f}%
"""
        for sig in fund_result.signals:
            brief_text += f"    • {sig}\n"

        commodities = fd.get("commodities_to_track", [])
        if commodities:
            brief_text += "\n  应追踪商品:\n"
            for c in commodities:
                brief_text += f"    → {c}\n"

    if catalysts:
        brief_text += "\n  ── KEY CATALYSTS (关键催化剂) ──\n"
        if catalysts.lme_nickel_usd:
            ni_line = f"  LME镍3个月: ${catalysts.lme_nickel_usd:,.0f}/吨"
            if catalysts.lme_nickel_cny:
                ni_line += f" (≈¥{catalysts.lme_nickel_cny:,.0f})"
            if catalysts.nickel_change_pct is not None:
                ni_line += f"  {catalysts.nickel_change_pct:+.2f}%"
            brief_text += ni_line + "\n"
            if catalysts.lme_nickel_usd >= 16000:
                brief_text += "    ⬆ 镍价$16K+: 华友冶炼利润丰厚区间\n"
            elif catalysts.lme_nickel_usd <= 14000:
                brief_text += "    ⬇ 镍价$14K-: 华友利润承压区间\n"
            else:
                brief_text += "    ◆ 镍价$14K-16K: 中性区间\n"
        else:
            brief_text += "  LME镍价: 获取失败\n"

        if catalysts.shfe_nickel:
            chg = f"  {catalysts.shfe_nickel_chg:+.0f}" if catalysts.shfe_nickel_chg else ""
            brief_text += f"  沪镍主力: ¥{catalysts.shfe_nickel:,.0f}/吨{chg}\n"
        if catalysts.lithium_carbonate:
            chg = f"  {catalysts.lithium_carbonate_chg:+.0f}" if catalysts.lithium_carbonate_chg else ""
            brief_text += f"  碳酸锂主力: ¥{catalysts.lithium_carbonate:,.0f}/吨{chg}\n"

        for evt in catalysts.events:
            if evt.category == "commodity":
                continue
            brief_text += f"  📅 [{evt.expected_date}] {evt.name}\n"
            brief_text += f"     {evt.description[:80]}\n"

        if commodity_sig != 0:
            direction = "bullish" if commodity_sig > 0 else "bearish"
            brief_text += f"  → Commodity signal: {commodity_sig:+.0f} ({direction} for stock)\n"

    if news_sentiment and news_sentiment.items:
        ns = news_sentiment
        label_map = {True: "偏多", False: "偏空"}
        label = "偏多" if ns.overall_score > 0.1 else ("偏空" if ns.overall_score < -0.1 else "中性")
        brief_text += f"\n  ── NEWS SENTIMENT (舆情: {label}) ──\n"
        brief_text += f"  近7日新闻: {len(ns.items)}条  利好{ns.bullish_count} / 利空{ns.bearish_count} / 中性{ns.neutral_count}\n"
        for it in ns.items[:5]:
            icon = "🟢" if it.sentiment_label == "利好" else ("🔴" if it.sentiment_label == "利空" else "⚪")
            title_short = it.title[:35] + "..." if len(it.title) > 35 else it.title
            brief_text += f"  {icon} [{it.source}] {title_short}\n"

    if expert_snapshot and expert_snapshot.posts:
        es = expert_snapshot
        total_e = es.bullish_count + es.bearish_count + es.neutral_count
        if es.consensus_score > 0.1:
            consensus_label = "偏多"
        elif es.consensus_score < -0.1:
            consensus_label = "偏空"
        else:
            consensus_label = "中性"
        bull_bear_str = f"{es.bullish_count}/{total_e}"
        brief_text += f"\n  ── EXPERT OPINIONS (淘股吧大神: {consensus_label} {bull_bear_str}) ──\n"
        brief_text += (f"  近{3}日 {es.total_experts_checked}位大神发帖, "
                       f"看多{es.bullish_count} / 看空{es.bearish_count} / 中性{es.neutral_count}\n")

        seen_experts: set[str] = set()
        for p in es.posts[:5]:
            if p.expert_id in seen_experts:
                continue
            seen_experts.add(p.expert_id)
            title_short = p.title[:40] + "..." if len(p.title) > 40 else p.title
            brief_text += f'  [{p.expert_name}] {p.publish_time[:10]}: "{title_short}"\n'
            sig_parts = []
            for act in p.signals.actions[:3]:
                sig_parts.append(act)
            for label, price in list(p.signals.price_targets.items())[:3]:
                sig_parts.append(f"{label}¥{price:.0f}")
            sig_parts.append(p.sentiment_label)
            brief_text += f"     Signals: {' | '.join(sig_parts)}\n"

        if expert_adj != 0:
            direction = "aligned" if expert_adj > 0 else "divergent"
            brief_text += f"  → Expert views {direction} with technicals, confidence {expert_adj:+.0%}\n"

    if t0_advice and t0_advice.has_position:
        brief_text += f"\n  ── T+0 Advice (Position: {t0_advice.quantity} shares @ ¥{t0_advice.cost:.1f}) ──\n"
        brief_text += f"  Unrealized: {t0_advice.pnl_pct:+.1f}%  (cost {t0_advice.cost:.1f} → price {t0_advice.current_price:.2f})\n"
        if t0_advice.t0_enabled:
            brief_text += f"  Strategy: {t0_advice.strategy}\n"
            brief_text += f"  T+0 Lot: {t0_advice.t0_lot} shares ({t0_advice.t0_lot/t0_advice.quantity*100:.0f}% of position)\n"
            if t0_advice.sell_lot2 > 0:
                brief_text += f"  Split Sell: batch 1 {t0_advice.sell_lot1} @¥{t0_advice.sell_zone_low:.2f}  "
                brief_text += f"batch 2 {t0_advice.sell_lot2} @¥{t0_advice.sell_zone_high:.2f}\n"
            else:
                brief_text += f"  Sell Zone: ¥{t0_advice.sell_zone_low:.2f} - ¥{t0_advice.sell_zone_high:.2f}\n"
            brief_text += f"  Buy Zone:  ¥{t0_advice.buy_zone_low:.2f} - ¥{t0_advice.buy_zone_high:.2f}\n"
            brief_text += f"  Stop Loss: ¥{t0_advice.stop_loss:.2f}\n"
            if t0_advice.risk_note:
                brief_text += f"  ⚠ {t0_advice.risk_note}\n"
            for sig in t0_advice.signals:
                brief_text += f"    • {sig}\n"
            if t0_advice.escape_plan:
                brief_text += "  ── Escape Plan (if sold too early) ──\n"
                for plan in t0_advice.escape_plan:
                    brief_text += f"    → {plan}\n"
        else:
            brief_text += f"  ✗ {t0_advice.strategy}\n"
            if t0_advice.risk_note:
                brief_text += f"  ⚠ {t0_advice.risk_note}\n"
            for sig in t0_advice.signals:
                brief_text += f"    • {sig}\n"

    forecast_text, prediction_summary = build_tomorrow_forecast(
        tech_score, fund_score, current_regime, regime_match,
        action, confidence, support, resistance, latest_price,
    )

    brief_text += f"""
  ── HISTORICAL PATTERN ({current_regime['trend']} / {current_regime['rsi']}) ──
{regime_line}

  ── BACKTEST STRATEGIES ──
{bt_lines}
  ── LEVELS ──
  Support: {support}  |  Resistance: {resistance}
{forecast_text}
{'═' * 56}
"""

    violations = validate_grounding(brief_text, agent_results, backtest_results, regime_match, catalysts)
    if violations:
        logger.warning("Grounding violations found: %s", violations)

    return {
        "date": today,
        "action": action,
        "confidence": confidence,
        "risk_level": risk_level,
        "brief_text": brief_text,
        "key_signals": tech_result.signals if tech_result else [],
        "tomorrow_prediction": prediction_summary,
        "commodity_signal": commodity_sig,
        "position_advice": position_advice,
        "strategy_mode": current_regime.get("strategy_mode", "default"),
        "reasoning": (
            f"Signal {effective_signal_score(tech_score, fund_score, w) + commodity_sig * w.get('commodity_weight', 0.5):+.0f} "
            f"(tech {tech_score:+.0f}, fund {fund_score:+.0f}, commodity {commodity_sig:+.0f}), "
            f"regime {current_regime['trend']}/{current_regime['rsi']}, "
            f"strategy={current_regime.get('strategy_mode', 'default')}"
        ),
        "regime": current_regime,
        "regime_match": regime_match,
        "grounding_violations": violations,
        "agent_results": [ar.to_dict() for ar in agent_results],
        "backtest_summary": [
            {"strategy": bt.strategy, "win_rate": bt.win_rate,
             "sharpe": bt.sharpe, "passes": bt.passes_threshold}
            for bt in backtest_results
        ],
    }
