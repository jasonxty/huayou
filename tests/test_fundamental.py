"""Tests for fundamental analyst — scoring logic with synthetic data."""

from agents.base import AgentResult
from agents.fundamental import (
    analyze,
    _score_growth,
    _score_profitability,
    _score_valuation,
    _score_balance_sheet,
)
from data.fundamental import FundamentalSnapshot


def _make_snap(**overrides) -> FundamentalSnapshot:
    defaults = dict(
        ticker="603799", name="华友钴业", industry="能源金属",
        total_shares=1.9e9, market_cap=1136, price=59.9,
        revenue_latest=589.4, revenue_prev_year=454.9,
        net_profit_latest=42.2, net_profit_prev_year=30.2,
        report_period="2025三季报",
        gross_margin=17.2, net_margin=8.5, roe=11.7,
        debt_ratio=64.4, current_ratio=1.1,
        pe_ttm=21.2, pb=2.4,
        annual_data=[
            {"year": "2024", "gross_margin": "17.23%", "revenue": "609亿", "net_profit": "41.55亿", "net_margin": "8.46%", "roe": "11.69%", "debt_ratio": "64.38%"},
            {"year": "2023", "gross_margin": "14.11%", "revenue": "663亿", "net_profit": "33.51亿", "net_margin": "6.79%", "roe": "11.14%", "debt_ratio": "64.36%"},
            {"year": "2022", "gross_margin": "18.59%", "revenue": "630亿", "net_profit": "39.08亿", "net_margin": "9.05%", "roe": "17.15%", "debt_ratio": "70.45%"},
            {"year": "2021", "gross_margin": "20.35%", "revenue": "353亿", "net_profit": "38.98亿", "net_margin": "11.39%", "roe": "23.49%", "debt_ratio": "58.78%"},
            {"year": "2020", "gross_margin": "15.66%", "revenue": "211亿", "net_profit": "11.65亿", "net_margin": "5.31%", "roe": "12.73%", "debt_ratio": "53.79%"},
        ],
    )
    defaults.update(overrides)
    return FundamentalSnapshot(**defaults)


def test_analyze_returns_agent_result():
    snap = _make_snap()
    result = analyze(snap)
    assert isinstance(result, AgentResult)
    assert result.agent_name == "fundamental"
    assert -100 <= result.score <= 100
    assert len(result.signals) > 0


def test_high_growth_scores_positive():
    snap = _make_snap(net_profit_latest=60.0, net_profit_prev_year=30.0)
    score, signals = _score_growth(snap)
    assert score > 0
    assert any("高速增长" in s for s in signals)


def test_declining_profit_scores_negative():
    snap = _make_snap(net_profit_latest=20.0, net_profit_prev_year=40.0)
    score, signals = _score_growth(snap)
    assert score < 0
    assert any("下滑" in s for s in signals)


def test_high_roe_scores_well():
    snap = _make_snap(roe=25.0, gross_margin=22.0, net_margin=12.0)
    score, _ = _score_profitability(snap)
    assert score > 0


def test_low_pe_scores_well():
    snap = _make_snap(pe_ttm=12.0, pb=1.5)
    score, signals = _score_valuation(snap)
    assert score > 0
    assert any("低估" in s or "偏低" in s for s in signals)


def test_high_debt_penalized():
    snap = _make_snap(debt_ratio=75.0, current_ratio=0.8)
    score, signals = _score_balance_sheet(snap)
    assert score < 0
    assert any("偏高" in s or "不足" in s for s in signals)


def test_commodities_identified():
    snap = _make_snap()
    result = analyze(snap)
    commodities = result.details.get("commodities_to_track", [])
    assert len(commodities) >= 4
    assert any("镍" in c for c in commodities)
    assert any("钴" in c for c in commodities)
    assert any("锂" in c for c in commodities)
    assert any("铜" in c for c in commodities)
