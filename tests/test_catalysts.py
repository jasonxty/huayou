"""Tests for catalysts module — event calendar and nickel price display."""

from data.catalysts import (
    CatalystEvent,
    CatalystSnapshot,
    _get_upcoming_events,
)
from agents.base import AgentResult
from agents.strategist import validate_grounding, synthesize
from backtest.engine import BacktestResult


def _make_catalyst_snap(usd: float = 15500, cny: float = 112000,
                        change_pct: float = 0.35) -> CatalystSnapshot:
    return CatalystSnapshot(
        lme_nickel_usd=usd,
        lme_nickel_cny=cny,
        nickel_change_pct=change_pct,
        nickel_fetch_time="2026-03-22 10:30",
        events=_get_upcoming_events(),
    )


def _make_tech_result() -> AgentResult:
    return AgentResult(
        agent_name="technical", score=-10, confidence=0.5,
        signals=["均线空头排列 (MA5<10<20<60)", "KDJ金叉"],
        details={"support": 58.5, "resistance": 62.3, "atr14": 3.1},
    )


def _make_bt() -> BacktestResult:
    return BacktestResult(
        strategy="ma_crossover",
        train_start="2019-01-02", train_end="2022-12-30",
        test_start="2023-01-03", test_end="2024-12-31",
        win_rate=0.45, sharpe=1.2, max_drawdown=0.18,
        profit_factor=1.1, total_trades=30, trades=[],
    )


def test_upcoming_events_nonempty():
    events = _get_upcoming_events()
    assert len(events) >= 2
    categories = {e.category for e in events}
    assert "policy" in categories or "earnings" in categories


def test_catalyst_snapshot_fields():
    snap = _make_catalyst_snap()
    assert snap.lme_nickel_usd == 15500
    assert snap.lme_nickel_cny == 112000
    assert len(snap.events) >= 2


def test_nickel_price_in_brief():
    cat = _make_catalyst_snap()
    brief = synthesize(
        agent_results=[_make_tech_result()],
        backtest_results=[_make_bt()],
        regime_match={"count": 100, "avg_return": 0.003, "win_rate": 0.52, "sufficient": True},
        current_regime={"trend": "down", "rsi": "neutral"},
        latest_price=60.0,
        catalysts=cat,
        analysis_date="2026-03-22",
    )
    text = brief["brief_text"]
    assert "LME" in text
    assert "15,500" in text or "15500" in text
    assert "RKAB" in text or "印尼" in text


def test_catalyst_grounding_clean():
    """Catalyst numbers (LME price, change%) should not trigger grounding violations."""
    cat = _make_catalyst_snap(usd=15823, cny=114500, change_pct=-0.72)
    ar = _make_tech_result()
    bt = _make_bt()
    regime = {"count": 100, "avg_return": 0.003, "win_rate": 0.52, "sufficient": True}

    brief = synthesize(
        agent_results=[ar], backtest_results=[bt],
        regime_match=regime, current_regime={"trend": "down", "rsi": "neutral"},
        latest_price=60.0, catalysts=cat, analysis_date="2026-03-22",
    )
    assert len(brief["grounding_violations"]) == 0


def test_brief_without_catalysts():
    """Brief should still render fine when catalysts=None."""
    brief = synthesize(
        agent_results=[_make_tech_result()],
        backtest_results=[_make_bt()],
        regime_match={"count": 100, "avg_return": 0.003, "win_rate": 0.52, "sufficient": True},
        current_regime={"trend": "down", "rsi": "neutral"},
        latest_price=60.0,
        catalysts=None,
        analysis_date="2026-03-22",
    )
    assert "REGIME" in brief["brief_text"]
    assert "LME" not in brief["brief_text"]


def test_high_nickel_price_shows_positive():
    cat = _make_catalyst_snap(usd=17200)
    brief = synthesize(
        agent_results=[_make_tech_result()],
        backtest_results=[_make_bt()],
        regime_match={"count": 100, "avg_return": 0.003, "win_rate": 0.52, "sufficient": True},
        current_regime={"trend": "down", "rsi": "neutral"},
        latest_price=60.0, catalysts=cat, analysis_date="2026-03-22",
    )
    assert "丰厚" in brief["brief_text"] or "$16K" in brief["brief_text"]


def test_low_nickel_price_shows_pressure():
    cat = _make_catalyst_snap(usd=13200)
    brief = synthesize(
        agent_results=[_make_tech_result()],
        backtest_results=[_make_bt()],
        regime_match={"count": 100, "avg_return": 0.003, "win_rate": 0.52, "sufficient": True},
        current_regime={"trend": "down", "rsi": "neutral"},
        latest_price=60.0, catalysts=cat, analysis_date="2026-03-22",
    )
    assert "承压" in brief["brief_text"] or "$14K" in brief["brief_text"]
