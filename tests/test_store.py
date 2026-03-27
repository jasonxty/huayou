"""Tests for data/store.py — SQLite operations."""

import pandas as pd
from data.store import save_ohlcv, get_latest_date, load_ohlcv


def test_save_and_load(db, sample_ohlcv):
    new_rows = save_ohlcv(db, sample_ohlcv)
    assert new_rows > 0

    loaded = load_ohlcv(db)
    assert len(loaded) == len(sample_ohlcv)
    assert loaded.iloc[0]["close"] > 0


def test_upsert_idempotent(db, sample_ohlcv):
    save_ohlcv(db, sample_ohlcv)
    count_1 = db.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]

    save_ohlcv(db, sample_ohlcv)
    count_2 = db.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
    assert count_1 == count_2


def test_skip_zero_volume(db):
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02"],
        "open": [10.0, 10.0], "high": [11.0, 11.0],
        "low": [9.0, 9.0], "close": [10.5, 10.5],
        "volume": [0, 100000],
        "turnover": [0, 1e8],
        "turnover_rate": [0, 3.0],
    })
    save_ohlcv(db, df)
    count = db.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
    assert count == 1


def test_latest_date(db, sample_ohlcv):
    assert get_latest_date(db) is None
    save_ohlcv(db, sample_ohlcv)
    latest = get_latest_date(db)
    assert latest == sample_ohlcv.iloc[-1]["date"]
