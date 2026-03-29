"""Tests for monitor.py — quote parsing, alert logic, tracker, holidays."""

from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest

from monitor import (
    Quote, AlertTracker, check_alerts, print_status,
    send_wechat, PushResult,
    ALERT_SELL1, ALERT_SELL2, ALERT_BUY, ALERT_STOP, ALERT_BREAKOUT,
)
from agents.t0_advisor import T0Advice
from data.holidays import is_trading_day
import config


# ── Fixtures ──

def _make_advice(**overrides) -> T0Advice:
    defaults = dict(
        has_position=True, quantity=1000, cost=65.3,
        current_price=60.0, pnl_pct=-8.1,
        t0_enabled=True, t0_lot=200,
        sell_zone_low=61.0, sell_zone_high=63.0,
        buy_zone_low=57.0, buy_zone_high=58.5,
        stop_loss=56.0,
        sell_lot1=100, sell_lot2=100,
        breakout_price=65.0, rebuy_abort_price=64.0,
        strategy="先卖后买", risk_note="", signals=[], escape_plan=[],
    )
    defaults.update(overrides)
    return T0Advice(**defaults)


def _make_quote(price: float, **kw) -> Quote:
    defaults = dict(
        price=price, open_price=60.0, high=price + 1,
        low=price - 1, change_pct=1.5, volume=10000,
        timestamp=datetime.now(),
    )
    defaults.update(kw)
    return Quote(**defaults)


# ── AlertTracker tests ──

class TestAlertTracker:
    def test_initial_state(self):
        t = AlertTracker()
        assert t.can_push()
        assert t.remaining() == config.MONITOR_DAILY_PUSH_LIMIT
        assert not t.has_fired("sell_zone_1")

    def test_record_and_dedup(self):
        t = AlertTracker()
        t.record("sell_zone_1")
        assert t.has_fired("sell_zone_1")
        assert not t.has_fired("buy_zone")
        assert t.push_count == 1

    def test_daily_limit(self):
        t = AlertTracker()
        for i in range(config.MONITOR_DAILY_PUSH_LIMIT):
            t.record(f"alert_{i}")
        assert not t.can_push()
        assert t.remaining() == 0

    def test_daily_rotation(self):
        t = AlertTracker()
        t.record("sell_zone_1")
        t.today = date(2020, 1, 1)
        assert t.can_push()
        assert not t.has_fired("sell_zone_1")
        assert t.push_count == 0


# ── Alert logic tests ──

class TestCheckAlerts:
    @patch("monitor.send_wechat")
    def test_sell_zone_1_triggered(self, mock_push):
        mock_push.return_value = PushResult(True, "ok")
        advice = _make_advice()
        tracker = AlertTracker()
        quote = _make_quote(61.5)

        check_alerts(quote, advice, tracker)

        mock_push.assert_called_once()
        title = mock_push.call_args[0][0]
        assert "高抛第1批" in title
        assert tracker.has_fired(ALERT_SELL1)

    @patch("monitor.send_wechat")
    def test_sell_zone_2_triggered(self, mock_push):
        mock_push.return_value = PushResult(True, "ok")
        advice = _make_advice()
        tracker = AlertTracker()
        quote = _make_quote(63.5)

        check_alerts(quote, advice, tracker)

        title = mock_push.call_args[0][0]
        assert "高抛第2批" in title
        assert tracker.has_fired(ALERT_SELL2)

    @patch("monitor.send_wechat")
    def test_buy_zone_triggered(self, mock_push):
        mock_push.return_value = PushResult(True, "ok")
        advice = _make_advice()
        tracker = AlertTracker()
        quote = _make_quote(58.0)

        check_alerts(quote, advice, tracker)

        title = mock_push.call_args[0][0]
        assert "低吸" in title
        assert tracker.has_fired(ALERT_BUY)

    @patch("monitor.send_wechat")
    def test_stop_loss_triggered(self, mock_push):
        mock_push.return_value = PushResult(True, "ok")
        advice = _make_advice()
        tracker = AlertTracker()
        quote = _make_quote(55.5)

        check_alerts(quote, advice, tracker)

        title = mock_push.call_args[0][0]
        assert "止损" in title
        assert tracker.has_fired(ALERT_STOP)

    @patch("monitor.send_wechat")
    def test_breakout_triggered(self, mock_push):
        mock_push.return_value = PushResult(True, "ok")
        advice = _make_advice()
        tracker = AlertTracker()
        quote = _make_quote(66.0)

        check_alerts(quote, advice, tracker)

        calls = mock_push.call_args_list
        titles = [c[0][0] for c in calls]
        assert any("突破" in t for t in titles)

    @patch("monitor.send_wechat")
    def test_no_alert_in_neutral_zone(self, mock_push):
        advice = _make_advice()
        tracker = AlertTracker()
        quote = _make_quote(60.0)

        check_alerts(quote, advice, tracker)

        mock_push.assert_not_called()

    @patch("monitor.send_wechat")
    def test_alert_not_fired_twice(self, mock_push):
        mock_push.return_value = PushResult(True, "ok")
        advice = _make_advice()
        tracker = AlertTracker()

        check_alerts(_make_quote(61.5), advice, tracker)
        check_alerts(_make_quote(61.8), advice, tracker)

        assert mock_push.call_count == 1

    @patch("monitor.send_wechat")
    def test_daily_limit_stops_push(self, mock_push):
        mock_push.return_value = PushResult(True, "ok")
        advice = _make_advice()
        tracker = AlertTracker()
        for i in range(config.MONITOR_DAILY_PUSH_LIMIT):
            tracker.record(f"fake_{i}")

        check_alerts(_make_quote(61.5), advice, tracker)

        mock_push.assert_not_called()

    @patch("monitor.send_wechat")
    def test_disabled_t0_skips(self, mock_push):
        advice = _make_advice(t0_enabled=False)
        tracker = AlertTracker()
        check_alerts(_make_quote(61.5), advice, tracker)
        mock_push.assert_not_called()


# ── Holiday tests ──

class TestHolidays:
    def test_weekend_not_trading(self):
        assert not is_trading_day(date(2026, 3, 28))  # Saturday
        assert not is_trading_day(date(2026, 3, 29))  # Sunday

    def test_weekday_is_trading(self):
        assert is_trading_day(date(2026, 3, 23))  # Monday

    def test_spring_festival_not_trading(self):
        assert not is_trading_day(date(2026, 2, 17))

    def test_national_day_not_trading(self):
        assert not is_trading_day(date(2026, 10, 1))

    def test_normal_weekday_after_holiday(self):
        assert is_trading_day(date(2026, 2, 23))  # Monday after Spring Festival


# ── Push function tests ──

class TestSendWechat:
    @patch("monitor.config.get_serverchan_key", return_value="")
    def test_no_key_returns_failure(self, mock_key):
        result = send_wechat("test")
        assert not result.success
        assert "未配置" in result.message

    @patch("monitor.config.get_serverchan_key", return_value="YOUR_SENDKEY_HERE")
    def test_placeholder_key_returns_failure(self, mock_key):
        result = send_wechat("test")
        assert not result.success
