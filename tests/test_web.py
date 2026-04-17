"""Tests for the FastAPI + HTMX dashboard web app."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config
from data.store import (
    init_db, save_trade, save_position, save_brief, save_alert,
    save_decision_note, load_decision_notes,
)

_TEST_DB: Path = Path("/tmp/test_web.db")


def _make_conn(db_path=None):
    conn = sqlite3.connect(str(_TEST_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


_FAKE_QUOTE = MagicMock(
    price=55.0, open_price=54.0, high=56.0, low=53.5,
    change_pct=1.23, volume=100000, timestamp=None,
)


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    global _TEST_DB
    _TEST_DB = tmp_path / "test_web.db"
    conn = _make_conn()
    init_db(conn)
    save_position(conn, config.TICKER, 65.30, 1000)
    save_trade(conn, "2025-03-04", "BUY", 70.65, 400)
    save_trade(conn, "2025-03-09", "BUY", 67.00, 300)
    save_trade(conn, "2025-03-23", "BUY", 56.50, 300)
    save_brief(conn, "2025-03-22", "HOLD (range-bound)", 0.62, "MEDIUM",
               "TEST BRIEF TEXT", {"test": True})
    save_brief(conn, "2025-03-23", "BUY (accumulate)", 0.70, "LOW",
               "BUY BRIEF TEXT", {"test": True})
    conn.execute(
        """INSERT OR REPLACE INTO ohlcv (date, open, high, low, close, volume)
           VALUES ('2025-03-22', 57.0, 58.0, 56.0, 57.5, 80000),
                  ('2025-03-23', 56.5, 57.5, 55.8, 56.8, 90000)""")
    save_alert(conn, "sell_zone_1", 58.0, 1.5, "Sell 200 shares", 200,
               58.2, 57.5, 59.0, 55.0, 5.8, "t0_grid")
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def client(setup_db):
    """Starlette TestClient with DB and network calls stubbed."""
    from starlette.testclient import TestClient

    with patch("data.store.get_connection", side_effect=_make_conn), \
         patch("monitor.fetch_realtime_quote", return_value=_FAKE_QUOTE), \
         patch("web.services.get_monitor_status", return_value={"running": False}):

        from importlib import reload
        import web.services
        reload(web.services)
        import web.app
        reload(web.app)

        with TestClient(web.app.app) as c:
            yield c


class TestDashboardPage:
    def test_get_dashboard(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text

    def test_dashboard_has_portfolio(self, client):
        resp = client.get("/")
        assert "Shares Held" in resp.text
        assert "Avg Cost" in resp.text

    def test_dashboard_has_tabs(self, client):
        resp = client.get("/")
        assert "Trades" in resp.text
        assert "Alerts" in resp.text
        assert "Briefs" in resp.text

    def test_dashboard_shows_seeded_trades(self, client):
        resp = client.get("/")
        assert "70.65" in resp.text
        assert "67.00" in resp.text


class TestPartialAPIs:
    def test_api_portfolio(self, client):
        resp = client.get("/api/portfolio")
        assert resp.status_code == 200
        assert "Shares Held" in resp.text

    def test_api_trades(self, client):
        resp = client.get("/api/trades")
        assert resp.status_code == 200
        assert "Trade Log" in resp.text

    def test_api_t0_trades(self, client):
        resp = client.get("/api/t0-trades")
        assert resp.status_code == 200
        assert "T+0 Trade Log" in resp.text

    def test_api_alerts(self, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        assert "Total Alerts" in resp.text

    def test_api_briefs(self, client):
        resp = client.get("/api/briefs")
        assert resp.status_code == 200
        assert "Brief History" in resp.text

    def test_api_monitor_status(self, client):
        resp = client.get("/api/monitor-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert data["running"] is False


class TestHTMXPartials:
    def test_htmx_request_returns_fragment(self, client):
        resp = client.get("/api/portfolio", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "Shares Held" in resp.text
        assert "<!DOCTYPE" not in resp.text


class TestTradeForm:
    def test_create_buy_trade(self, client):
        resp = client.post("/trades", data={
            "trade_date": "2025-04-01",
            "direction": "BUY",
            "price": "55.00",
            "quantity": "200",
            "notes": "test buy",
        })
        assert resp.status_code == 200
        assert "55.00" in resp.text
        assert "test buy" in resp.text

    def test_create_sell_trade(self, client):
        resp = client.post("/trades", data={
            "trade_date": "2025-04-01",
            "direction": "SELL",
            "price": "60.00",
            "quantity": "100",
            "notes": "",
        })
        assert resp.status_code == 200
        assert "60.00" in resp.text

    def test_invalid_direction_rejected(self, client):
        resp = client.post("/trades", data={
            "trade_date": "2025-04-01",
            "direction": "INVALID",
            "price": "55.00",
            "quantity": "200",
            "notes": "",
        })
        assert resp.status_code == 422

    def test_negative_price_rejected(self, client):
        resp = client.post("/trades", data={
            "trade_date": "2025-04-01",
            "direction": "BUY",
            "price": "-1",
            "quantity": "200",
            "notes": "",
        })
        assert resp.status_code == 422

    def test_zero_quantity_rejected(self, client):
        resp = client.post("/trades", data={
            "trade_date": "2025-04-01",
            "direction": "BUY",
            "price": "55.00",
            "quantity": "0",
            "notes": "",
        })
        assert resp.status_code == 422


class TestDeleteTrade:
    def test_delete_existing_trade(self, client):
        from data.store import load_trade_log_with_ids
        conn = _make_conn()
        init_db(conn)
        trades = load_trade_log_with_ids(conn)
        conn.close()
        assert len(trades) > 0
        trade_id = trades[0]["id"]

        resp = client.delete(f"/trades/{trade_id}")
        assert resp.status_code == 200

    def test_delete_nonexistent_trade(self, client):
        resp = client.delete("/trades/99999")
        assert resp.status_code == 404


class TestT0TradeForm:
    def test_create_t0_trade(self, client):
        resp = client.post("/t0-trades", data={
            "sell_price": "62.50",
            "buy_price": "60.00",
            "quantity": "200",
        })
        assert resp.status_code == 200
        assert "T+0 Trade Log" in resp.text

    def test_t0_without_position_rejected(self, client):
        conn = _make_conn()
        init_db(conn)
        conn.execute("DELETE FROM positions")
        conn.commit()
        conn.close()

        resp = client.post("/t0-trades", data={
            "sell_price": "62.50",
            "buy_price": "60.00",
            "quantity": "200",
        })
        assert resp.status_code == 400


class TestBriefDetail:
    def test_view_existing_brief(self, client):
        resp = client.get("/brief/2025-03-22")
        assert resp.status_code == 200
        assert "TEST BRIEF TEXT" in resp.text
        assert "HOLD" in resp.text

    def test_nonexistent_brief_returns_404(self, client):
        resp = client.get("/brief/1999-01-01")
        assert resp.status_code == 404


class TestComparisonHero:
    def test_dashboard_has_comparison_hero(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "System P&amp;L" in resp.text
        assert "My Actual P&amp;L" in resp.text
        assert "Delta" in resp.text

    def test_api_comparison_hero(self, client):
        resp = client.get("/api/comparison-hero")
        assert resp.status_code == 200
        assert "System P&amp;L" in resp.text


class TestStrategicComparison:
    def test_dashboard_has_strategic_tab(self, client):
        resp = client.get("/")
        assert "Strategic" in resp.text

    def test_api_strategic_comparison(self, client):
        resp = client.get("/api/strategic-comparison")
        assert resp.status_code == 200
        assert "Strategic Comparison" in resp.text

    def test_strategic_shows_brief_dates(self, client):
        resp = client.get("/api/strategic-comparison")
        assert "2025-03-22" in resp.text
        assert "2025-03-23" in resp.text


class TestTacticalComparison:
    def test_dashboard_has_tactical_tab(self, client):
        resp = client.get("/")
        assert "Tactical" in resp.text

    def test_api_tactical_comparison(self, client):
        resp = client.get("/api/tactical-comparison")
        assert resp.status_code == 200
        assert "Tactical Comparison" in resp.text


class TestDecisionNotes:
    def test_save_note(self, client):
        resp = client.post("/notes", data={
            "note_date": "2025-03-22",
            "note_type": "strategic",
            "ref_id": "brief",
            "note_text": "Thought it would keep going up",
        })
        assert resp.status_code == 200

        conn = _make_conn()
        init_db(conn)
        notes = load_decision_notes(conn)
        conn.close()
        assert "2025-03-22|strategic|brief" in notes
        assert notes["2025-03-22|strategic|brief"] == "Thought it would keep going up"

    def test_save_note_invalid_type(self, client):
        resp = client.post("/notes", data={
            "note_date": "2025-03-22",
            "note_type": "invalid",
            "ref_id": "brief",
            "note_text": "test",
        })
        assert resp.status_code == 422

    def test_upsert_note(self, client):
        client.post("/notes", data={
            "note_date": "2025-03-22",
            "note_type": "strategic",
            "ref_id": "brief",
            "note_text": "first thought",
        })
        client.post("/notes", data={
            "note_date": "2025-03-22",
            "note_type": "strategic",
            "ref_id": "brief",
            "note_text": "updated thought",
        })
        conn = _make_conn()
        init_db(conn)
        notes = load_decision_notes(conn)
        conn.close()
        assert notes["2025-03-22|strategic|brief"] == "updated thought"


class TestFeeCalculation:
    def test_buy_fee_no_stamp_tax(self):
        fee = config.calc_trade_fee(10000, "BUY")
        fc = config.get_fee_config()
        commission = max(10000 * fc["commission_rate"], fc["commission_min"])
        transfer = 10000 * fc["transfer_fee_rate"]
        expected = round(commission + transfer, 2)
        assert fee == expected

    def test_sell_fee_higher_than_buy(self):
        assert config.calc_trade_fee(10000, "SELL") > config.calc_trade_fee(10000, "BUY")

    def test_minimum_commission_floor(self):
        fee = config.calc_trade_fee(100, "BUY")
        fc = config.get_fee_config()
        assert fee >= fc["commission_min"]
