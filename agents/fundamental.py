"""Rule-based Fundamental Analyst for 603799.

Scores range from -100 (deep value trap / deteriorating) to +100 (strong growth + cheap).
Assesses: growth trajectory, profitability, valuation, balance sheet health,
and business cycle position for 华友钴业 as a multi-metal new energy materials company.
"""

from __future__ import annotations

import logging

from agents.base import AgentResult
from data.fundamental import FundamentalSnapshot

logger = logging.getLogger(__name__)


def _score_growth(snap: FundamentalSnapshot) -> tuple[float, list[str]]:
    """Score revenue and profit growth. Max ±30 points."""
    signals = []
    score = 0.0

    rev_yoy = snap.revenue_yoy()
    profit_yoy = snap.profit_yoy()

    if profit_yoy > 50:
        score += 20
        signals.append(f"归母净利高速增长 ({profit_yoy:+.0f}% YoY)")
    elif profit_yoy > 20:
        score += 12
        signals.append(f"归母净利稳健增长 ({profit_yoy:+.0f}% YoY)")
    elif profit_yoy > 0:
        score += 5
        signals.append(f"归母净利小幅增长 ({profit_yoy:+.0f}% YoY)")
    elif profit_yoy > -20:
        score -= 10
        signals.append(f"归母净利小幅下滑 ({profit_yoy:+.0f}% YoY)")
    else:
        score -= 20
        signals.append(f"归母净利大幅下滑 ({profit_yoy:+.0f}% YoY)")

    if rev_yoy > 30:
        score += 10
        signals.append(f"营收快速增长 ({rev_yoy:+.0f}% YoY)")
    elif rev_yoy > 10:
        score += 5
        signals.append(f"营收稳定增长 ({rev_yoy:+.0f}% YoY)")
    elif rev_yoy < -10:
        score -= 10
        signals.append(f"营收收缩 ({rev_yoy:+.0f}% YoY)")

    return score, signals


def _score_profitability(snap: FundamentalSnapshot) -> tuple[float, list[str]]:
    """Score margins and ROE. Max ±25 points."""
    signals = []
    score = 0.0

    if snap.gross_margin > 25:
        score += 10
        signals.append(f"毛利率优秀 ({snap.gross_margin:.1f}%)")
    elif snap.gross_margin > 15:
        score += 5
        signals.append(f"毛利率尚可 ({snap.gross_margin:.1f}%)")
    elif snap.gross_margin < 10:
        score -= 10
        signals.append(f"毛利率偏低 ({snap.gross_margin:.1f}%)")

    if snap.roe > 20:
        score += 10
        signals.append(f"ROE优秀 ({snap.roe:.1f}%)")
    elif snap.roe > 10:
        score += 5
        signals.append(f"ROE良好 ({snap.roe:.1f}%)")
    elif snap.roe < 5:
        score -= 10
        signals.append(f"ROE偏低 ({snap.roe:.1f}%)")

    if snap.net_margin > 10:
        score += 5
        signals.append(f"净利率健康 ({snap.net_margin:.1f}%)")
    elif snap.net_margin < 3:
        score -= 5
        signals.append(f"净利率微薄 ({snap.net_margin:.1f}%)")

    return score, signals


def _score_valuation(snap: FundamentalSnapshot) -> tuple[float, list[str]]:
    """Score PE/PB relative to growth. Max ±25 points."""
    signals = []
    score = 0.0

    profit_yoy = snap.profit_yoy()
    peg = snap.pe_ttm / profit_yoy if profit_yoy > 0 else 99

    if snap.pe_ttm <= 0:
        score -= 15
        signals.append("PE为负（亏损状态）")
    elif snap.pe_ttm < 15:
        score += 15
        signals.append(f"PE偏低 ({snap.pe_ttm:.1f}x), 可能低估")
    elif snap.pe_ttm < 25:
        score += 5
        signals.append(f"PE合理 ({snap.pe_ttm:.1f}x)")
    elif snap.pe_ttm < 40:
        score -= 5
        signals.append(f"PE偏高 ({snap.pe_ttm:.1f}x)")
    else:
        score -= 15
        signals.append(f"PE过高 ({snap.pe_ttm:.1f}x)")

    if 0 < peg < 0.8:
        score += 10
        signals.append(f"PEG<0.8 ({peg:.1f}), 成长性价比高")
    elif 0 < peg < 1.2:
        score += 3
        signals.append(f"PEG合理 ({peg:.1f})")
    elif peg > 2:
        score -= 5
        signals.append(f"PEG偏高 ({peg:.1f}), 增长不匹配估值")

    if 0 < snap.pb < 2:
        score += 5
        signals.append(f"PB偏低 ({snap.pb:.1f}x)")
    elif snap.pb > 5:
        score -= 5
        signals.append(f"PB偏高 ({snap.pb:.1f}x)")

    return score, signals


def _score_balance_sheet(snap: FundamentalSnapshot) -> tuple[float, list[str]]:
    """Score financial health. Max ±20 points."""
    signals = []
    score = 0.0

    if snap.debt_ratio > 70:
        score -= 15
        signals.append(f"资产负债率偏高 ({snap.debt_ratio:.1f}%), 财务压力大")
    elif snap.debt_ratio > 60:
        score -= 5
        signals.append(f"资产负债率中等 ({snap.debt_ratio:.1f}%)")
    elif snap.debt_ratio < 40:
        score += 10
        signals.append(f"资产负债率低 ({snap.debt_ratio:.1f}%), 财务稳健")
    else:
        score += 5
        signals.append(f"资产负债率适中 ({snap.debt_ratio:.1f}%)")

    if snap.current_ratio > 2:
        score += 5
        signals.append(f"流动比率充裕 ({snap.current_ratio:.2f})")
    elif snap.current_ratio < 1:
        score -= 5
        signals.append(f"流动比率不足 ({snap.current_ratio:.2f}), 短期偿债风险")

    return score, signals


def _analyze_cycle(snap: FundamentalSnapshot) -> tuple[float, list[str]]:
    """Analyze business cycle position from historical margins."""
    signals = []

    if len(snap.annual_data) < 3:
        return 0, ["历史数据不足，无法判断周期位置"]

    def parse_pct(val) -> float:
        s = str(val).replace("%", "").strip()
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    margins = [parse_pct(d["gross_margin"]) for d in snap.annual_data if parse_pct(d["gross_margin"]) > 0]

    if len(margins) < 3:
        return 0, ["毛利率数据不足"]

    current_margin = margins[0]
    avg_margin = sum(margins) / len(margins)
    max_margin = max(margins)
    min_margin = min(margins)

    margin_range = max_margin - min_margin if max_margin > min_margin else 1
    position = (current_margin - min_margin) / margin_range

    score = 0.0
    if position > 0.7:
        score = -5
        signals.append(f"毛利率处于历史高位 ({current_margin:.1f}% vs 均值{avg_margin:.1f}%), 周期见顶风险")
    elif position < 0.3:
        score = 10
        signals.append(f"毛利率处于历史低位 ({current_margin:.1f}% vs 均值{avg_margin:.1f}%), 可能处于周期底部")
    else:
        score = 3
        signals.append(f"毛利率处于历史中位 ({current_margin:.1f}% vs 均值{avg_margin:.1f}%)")

    recent_trend = margins[0] - margins[1] if len(margins) > 1 else 0
    if recent_trend > 2:
        score += 5
        signals.append("毛利率回升趋势 (同比改善)")
    elif recent_trend < -2:
        score -= 5
        signals.append("毛利率下行趋势 (同比恶化)")

    return score, signals


def _identify_commodities(snap: FundamentalSnapshot) -> list[str]:
    """Identify which commodities to track based on business profile."""
    commodities = []

    commodities.append("镍 (Ni) — 第一大业务，印尼MHP/高冰镍项目")
    commodities.append("钴 (Co) — 传统主业，刚果(金)钴矿 → 四氧化三钴/硫酸钴")
    commodities.append("锂 (Li) — 碳酸锂/氢氧化锂，正在扩产")
    commodities.append("铜 (Cu) — 刚果(金)副产品，贡献现金流")

    commodities.append("下游需求: 三元正极材料 → 动力电池 → 新能源车销量")

    return commodities


def analyze(snap: FundamentalSnapshot) -> AgentResult:
    """Rule-based fundamental analysis. Returns AgentResult."""

    total_score = 0.0
    all_signals = []

    for scorer in [
        lambda: _score_growth(snap),
        lambda: _score_profitability(snap),
        lambda: _score_valuation(snap),
        lambda: _score_balance_sheet(snap),
        lambda: _analyze_cycle(snap),
    ]:
        s, sigs = scorer()
        total_score += s
        all_signals.extend(sigs)

    total_score = max(-100, min(100, total_score))

    commodities = _identify_commodities(snap)

    return AgentResult(
        agent_name="fundamental",
        score=total_score,
        confidence=0.0,
        signals=all_signals,
        details={
            "report_period": snap.report_period,
            "market_cap": snap.market_cap,
            "pe_ttm": snap.pe_ttm,
            "pb": snap.pb,
            "roe": snap.roe,
            "gross_margin": snap.gross_margin,
            "net_margin": snap.net_margin,
            "debt_ratio": snap.debt_ratio,
            "revenue_yoy": round(snap.revenue_yoy(), 1),
            "profit_yoy": round(snap.profit_yoy(), 1),
            "commodities_to_track": commodities,
        },
    )
