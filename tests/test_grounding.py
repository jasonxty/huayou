"""Tests for the grounding validator — ensuring brief cites only real data."""

from agents.base import AgentResult
from agents.strategist import validate_grounding
from backtest.engine import BacktestResult


def _make_agent_result(score: float = 42, support: float = 38.50,
                       resistance: float = 44.80) -> AgentResult:
    return AgentResult(
        agent_name="technical",
        score=score,
        confidence=0.68,
        signals=["MACD golden cross", "above MA20"],
        details={"support": support, "resistance": resistance,
                 "trend": "bullish"},
    )


def _make_backtest_result(win_rate: float = 0.612,
                          sharpe: float = 1.35) -> BacktestResult:
    return BacktestResult(
        strategy="ma_crossover",
        train_start="2019-01-02", train_end="2022-12-30",
        test_start="2023-01-03", test_end="2024-12-31",
        win_rate=win_rate, sharpe=sharpe,
        max_drawdown=0.156, profit_factor=1.82,
        total_trades=47, trades=[],
    )


def test_clean_brief_passes():
    ar = _make_agent_result()
    bt = _make_backtest_result()
    brief = "Score: 42/100. Support at 38.50, resistance at 44.80. Win rate 61.2%."
    violations = validate_grounding(brief, [ar], [bt])
    assert violations == []


def test_hallucinated_number_caught():
    ar = _make_agent_result()
    bt = _make_backtest_result()
    brief = "Score: 42/100. Target price 52.30 by next week."
    violations = validate_grounding(brief, [ar], [bt])
    assert len(violations) > 0
    assert any("52.3" in v for v in violations)


def test_dates_ignored():
    ar = _make_agent_result()
    bt = _make_backtest_result()
    brief = "Analysis for 2026-03-25. Score: 42."
    violations = validate_grounding(brief, [ar], [bt])
    assert violations == []


def test_small_numbers_ignored():
    ar = _make_agent_result()
    bt = _make_backtest_result()
    brief = "Score: 42. Top 3 signals. 5-day return."
    violations = validate_grounding(brief, [ar], [bt])
    assert violations == []
