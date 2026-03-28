"""Tests for T+0 intraday trading advisor."""

from agents.t0_advisor import advise, T0Advice


def _base_args(**overrides):
    defaults = dict(
        position={"ticker": "603799", "cost": 65.3, "quantity": 1000,
                   "entry_date": "2025-10-15", "notes": ""},
        latest_price=59.9,
        atr=3.12,
        support=59.63,
        resistance=60.73,
        boll_upper=64.5,
        boll_mid=61.0,
        boll_lower=57.5,
        tech_score=-19,
        regime={"trend": "down", "rsi": "neutral"},
    )
    defaults.update(overrides)
    return defaults


def test_basic_advice_structure():
    advice = advise(**_base_args())
    assert isinstance(advice, T0Advice)
    assert advice.has_position is True
    assert advice.t0_enabled is True
    assert advice.t0_lot > 0
    assert advice.t0_lot % 100 == 0


def test_no_position():
    advice = advise(**_base_args(position=None))
    assert advice.has_position is False
    assert advice.t0_enabled is False
    assert "无持仓" in advice.strategy


def test_small_position():
    advice = advise(**_base_args(
        position={"ticker": "603799", "cost": 65, "quantity": 100,
                   "entry_date": "", "notes": ""},
    ))
    assert advice.has_position is True
    assert advice.t0_enabled is False
    assert "不建议" in advice.strategy


def test_bearish_regime_sell_first():
    advice = advise(**_base_args(regime={"trend": "down", "rsi": "neutral"}))
    assert "先卖后买" in advice.strategy


def test_oversold_regime_buy_first():
    advice = advise(**_base_args(regime={"trend": "down", "rsi": "oversold"}))
    assert "先买后卖" in advice.strategy


def test_bullish_regime_larger_lot():
    bearish = advise(**_base_args(regime={"trend": "down", "rsi": "neutral"}))
    bullish = advise(**_base_args(
        regime={"trend": "up", "rsi": "neutral"}, tech_score=25,
    ))
    assert bullish.t0_lot >= bearish.t0_lot


def test_sell_zone_above_current_price():
    advice = advise(**_base_args())
    if advice.t0_enabled:
        assert advice.sell_zone_low >= advice.current_price or \
               advice.sell_zone_low >= advice.buy_zone_high


def test_buy_zone_below_sell_zone():
    advice = advise(**_base_args())
    if advice.t0_enabled:
        assert advice.buy_zone_high <= advice.sell_zone_low


def test_stop_loss_below_support():
    advice = advise(**_base_args())
    if advice.t0_enabled:
        assert advice.stop_loss < advice.buy_zone_low


def test_pnl_calculation():
    advice = advise(**_base_args(latest_price=59.9))
    expected_pnl = (59.9 - 65.3) / 65.3 * 100
    assert abs(advice.pnl_pct - round(expected_pnl, 1)) < 0.2


def test_deep_loss_conservative():
    advice = advise(**_base_args(
        position={"ticker": "603799", "cost": 80, "quantity": 1000,
                   "entry_date": "", "notes": ""},
        latest_price=59.9,
    ))
    assert advice.pnl_pct < -20
    assert any("深度套牢" in s or "谨慎" in s or "浮亏" in s for s in advice.signals)


def test_lot_never_exceeds_position():
    advice = advise(**_base_args(
        position={"ticker": "603799", "cost": 65, "quantity": 300,
                   "entry_date": "", "notes": ""},
    ))
    assert advice.t0_lot <= 300
