"""Tests for Buffett learning engine, journal, and calibration."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from data.store import (
    get_connection, init_db, save_brief, save_ohlcv,
    load_kobe_weights, load_kobe_journal, load_kobe_calibration,
    load_kobe_stats, save_kobe_weights, save_kobe_journal,
    load_kobe_monthly,
)
from agents.kobe import (
    get_active_weights, _classify_brief_outcome, _generate_reflection,
    run_journal_update, run_learning, run_calibration, run_monthly_report,
)


@pytest.fixture
def db():
    conn = get_connection(db_path=":memory:")
    init_db(conn)
    yield conn
    conn.close()


def _seed_briefs_and_ohlcv(conn, count=15):
    """Insert briefs and OHLCV with forward-looking data."""
    import pandas as pd
    import numpy as np
    np.random.seed(42)

    dates = pd.bdate_range("2026-01-05", periods=count + 10)

    prices = 55.0 + np.cumsum(np.random.normal(0, 0.5, count + 10))
    records = []
    for i, d in enumerate(dates):
        records.append((
            str(d.date()), float(prices[i]), float(prices[i] + 0.5),
            float(prices[i] - 0.5), float(prices[i]),
            100000, 1e9, 3.0,
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO ohlcv
           (date, open, high, low, close, volume, turnover, turnover_rate)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        records,
    )
    conn.commit()

    actions = ["BUY (light)", "HOLD (range-bound)", "SELL (reduce)",
               "BUY (aggressive)", "HOLD (wait and see)"]
    for i in range(count):
        d = str(dates[i].date())
        action = actions[i % len(actions)]
        conf = 0.55 + (i % 4) * 0.1
        save_brief(conn, d, action, conf, "MEDIUM",
                   f"Test brief for {d}", {"test": True})


class TestActiveWeights:
    def test_default_weights_when_empty(self, db):
        weights = get_active_weights(db)
        assert weights == config.KOBE_DEFAULT_WEIGHTS

    def test_stored_weights_override(self, db):
        custom = {"tech_buy_threshold": 35, "regime_trust": 0.8}
        save_kobe_weights(db, custom, "test", 10)
        weights = get_active_weights(db)
        assert weights["tech_buy_threshold"] == 35
        assert weights["regime_trust"] == 0.8
        assert weights["tech_sell_threshold"] == config.KOBE_DEFAULT_WEIGHTS["tech_sell_threshold"]


class TestClassifyOutcome:
    def test_buy_correct(self):
        assert _classify_brief_outcome("BUY (light)", 0.02) == "correct"

    def test_buy_wrong(self):
        assert _classify_brief_outcome("BUY (light)", -0.02) == "wrong"

    def test_sell_correct(self):
        assert _classify_brief_outcome("SELL (reduce)", -0.03) == "correct"

    def test_sell_wrong(self):
        assert _classify_brief_outcome("SELL (reduce)", 0.03) == "wrong"

    def test_hold_correct(self):
        assert _classify_brief_outcome("HOLD (range-bound)", 0.005) == "correct"

    def test_hold_neutral(self):
        assert _classify_brief_outcome("HOLD (range-bound)", 0.05) == "neutral"

    def test_pending(self):
        assert _classify_brief_outcome("BUY", None) == "pending"


class TestReflection:
    def test_correct_buy(self):
        text = _generate_reflection("BUY (light)", 0.65, 0.02, 0.05, "correct")
        assert "BUY" in text
        assert "right" in text.lower() or "agreed" in text.lower()

    def test_wrong_sell(self):
        text = _generate_reflection("SELL (reduce)", 0.70, 0.03, 0.01, "wrong")
        assert "premature" in text.lower() or "aggressive" in text.lower()

    def test_correct_hold(self):
        text = _generate_reflection("HOLD", 0.60, 0.005, None, "correct")
        assert "patience" in text.lower() or "right call" in text.lower()


class TestJournalUpdate:
    def test_creates_entries(self, db):
        _seed_briefs_and_ohlcv(db, count=12)
        count = run_journal_update(db)
        assert count > 0
        journal = load_kobe_journal(db)
        assert len(journal) > 0
        assert journal[0]["reflection"]

    def test_idempotent(self, db):
        _seed_briefs_and_ohlcv(db, count=12)
        run_journal_update(db)
        count2 = run_journal_update(db)
        assert count2 == 0


class TestLearning:
    def test_not_enough_samples(self, db):
        _seed_briefs_and_ohlcv(db, count=3)
        result = run_learning(db)
        assert result is None

    def test_learning_creates_weights(self, db):
        _seed_briefs_and_ohlcv(db, count=20)
        result = run_learning(db)
        if result is not None:
            stored = load_kobe_weights(db)
            assert stored is not None
            for key in config.KOBE_DEFAULT_WEIGHTS:
                if key not in config.KOBE_WEIGHT_BOUNDS:
                    continue
                lo, hi = config.KOBE_WEIGHT_BOUNDS[key]
                assert lo <= stored.get(key, config.KOBE_DEFAULT_WEIGHTS[key]) <= hi


class TestCalibration:
    def test_not_enough_data(self, db):
        result = run_calibration(db)
        assert result == []

    def test_computes_buckets(self, db):
        _seed_briefs_and_ohlcv(db, count=20)
        result = run_calibration(db)
        assert len(result) == 4
        for b in result:
            assert "bucket" in b
            assert "predicted" in b
            assert "actual" in b
            assert "count" in b

        stored = load_kobe_calibration(db)
        assert len(stored) == 4


class TestMonthlyReport:
    def test_no_data(self, db):
        result = run_monthly_report(db, month="2025-12")
        assert result is None

    def test_generates_report(self, db):
        _seed_briefs_and_ohlcv(db, count=20)
        run_journal_update(db)
        journal = load_kobe_journal(db)
        if journal:
            month = journal[0]["entry_date"][:7]
            result = run_monthly_report(db, month=month)
            if result is not None:
                assert "kobe_wins" in result
                assert "kobe_losses" in result
                assert "kobe_win_rate" in result

                stored = load_kobe_monthly(db)
                assert len(stored) >= 1


class TestBuffettStats:
    def test_empty_stats(self, db):
        stats = load_kobe_stats(db)
        assert stats["total_briefs"] == 0
        assert stats["wins"] == 0
        assert stats["win_rate"] == 0.0

    def test_with_data(self, db):
        _seed_briefs_and_ohlcv(db, count=12)
        run_journal_update(db)
        stats = load_kobe_stats(db)
        assert stats["total_briefs"] == 12
        assert stats["journal_entries"] > 0


class TestWebRoutes:
    """Test that Buffett-related dashboard routes return 200."""

    @pytest.fixture
    def client(self, db):
        import config as _cfg
        original_path = _cfg.DB_PATH

        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        _cfg.DB_PATH = Path(tmp.name)

        conn = get_connection(_cfg.DB_PATH)
        init_db(conn)
        _seed_briefs_and_ohlcv(conn, count=12)
        run_journal_update(conn)
        conn.close()

        from web.app import app
        from starlette.testclient import TestClient
        client = TestClient(app)
        yield client

        _cfg.DB_PATH = original_path
        os.unlink(tmp.name)

    def test_dashboard_has_kobe_tab(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Buffett" in resp.text

    def test_kobe_profile_partial(self, client):
        resp = client.get("/api/kobe-profile")
        assert resp.status_code == 200
        assert "Buffett" in resp.text

    def test_kobe_journal_partial(self, client):
        resp = client.get("/api/kobe-journal")
        assert resp.status_code == 200

    def test_kobe_calibration_partial(self, client):
        resp = client.get("/api/kobe-calibration")
        assert resp.status_code == 200
