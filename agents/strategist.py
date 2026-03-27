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
from backtest.engine import BacktestResult

logger = logging.getLogger(__name__)


# ── Regime matching ──

def classify_regime(indicators: pd.DataFrame) -> dict:
    """Discretize latest indicator state into a 2-dimension regime vector.

    Dimensions:
        trend: up / down / sideways (based on MA20 vs MA60)
        rsi: oversold / neutral / overbought (RSI12 buckets)
    """
    if indicators.empty:
        return {"trend": "unknown", "rsi": "unknown"}

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

    return {"trend": trend, "rsi": rsi_bucket}


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
                       regime_match: dict | None = None) -> list[str]:
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

    for ar in agent_results:
        for v in ar.signals:
            for n in re.findall(r"[\d]+\.?\d*", v):
                try:
                    allowed_numbers.add(float(n))
                except ValueError:
                    pass

    date_stripped = re.sub(r"\d{4}-\d{2}-\d{2}", "", brief_text)
    found_numbers = re.findall(r"[\d]+\.?\d*", date_stripped)
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

def _decide_action(tech_score: float, regime: dict, regime_match: dict,
                   best_strategy: BacktestResult | None) -> tuple[str, str]:
    """Deterministic action and risk level from scores.

    Returns (action, risk_level).
    """
    has_backtest_edge = best_strategy is not None and best_strategy.passes_threshold
    regime_bearish = regime.get("trend") == "down"
    regime_oversold = regime.get("rsi") == "oversold"
    regime_overbought = regime.get("rsi") == "overbought"

    if tech_score >= 40 and not regime_overbought:
        action = "BUY (积极建仓)" if has_backtest_edge else "BUY (轻仓试探)"
        risk = "MEDIUM" if has_backtest_edge else "HIGH"
    elif tech_score >= 20:
        action = "BUY (轻仓)" if not regime_bearish else "HOLD (观望为主)"
        risk = "MEDIUM"
    elif tech_score <= -40 and not regime_oversold:
        action = "SELL (减仓)" if has_backtest_edge else "SELL (止损)"
        risk = "HIGH"
    elif tech_score <= -20:
        action = "SELL (轻仓减持)" if not regime_oversold else "HOLD (超卖反弹可能)"
        risk = "HIGH" if regime_bearish else "MEDIUM"
    else:
        action = "HOLD (震荡观望)"
        risk = "LOW"

    return action, risk


# ── Synthesis ──

def synthesize(
    agent_results: list[AgentResult],
    backtest_results: list[BacktestResult],
    regime_match: dict,
    current_regime: dict,
    latest_price: float,
    analysis_date: str | None = None,
) -> dict:
    """Produce the morning brief. Pure rule-based, no LLM needed."""
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

    action, risk_level = _decide_action(tech_score, current_regime, regime_match, best_strategy)

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

    brief_text = f"""{'═' * 56}
  华友钴业 (603799) — {today} Morning Brief
{'═' * 56}

  ACTION:     {action}
  CONFIDENCE: {confidence*100:.0f}%
  RISK LEVEL: {risk_level}
  PRICE:      {latest_price:.2f}  |  ATR(14): {atr}

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

    brief_text += f"""
  ── REGIME ({current_regime['trend']} / {current_regime['rsi']}) ──
{regime_line}

  ── BACKTEST STRATEGIES ──
{bt_lines}
  ── LEVELS ──
  Support: {support}  |  Resistance: {resistance}
{'═' * 56}
"""

    violations = validate_grounding(brief_text, agent_results, backtest_results, regime_match)
    if violations:
        logger.warning("Grounding violations found: %s", violations)

    return {
        "date": today,
        "action": action,
        "confidence": confidence,
        "risk_level": risk_level,
        "brief_text": brief_text,
        "key_signals": tech_result.signals if tech_result else [],
        "reasoning": f"Tech score {tech_score:+.0f}, regime {current_regime['trend']}/{current_regime['rsi']}",
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
