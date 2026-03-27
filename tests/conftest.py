"""Shared fixtures for tests."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.store import get_connection, init_db


@pytest.fixture
def db():
    """In-memory SQLite database with schema."""
    conn = get_connection(db_path=":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """300 trading days of synthetic OHLCV data resembling 603799."""
    np.random.seed(42)
    n = 300
    dates = pd.bdate_range("2023-01-03", periods=n)
    base = 40.0
    returns = np.random.normal(0.001, 0.025, n)
    prices = base * np.cumprod(1 + returns)

    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": prices * (1 + np.random.uniform(-0.01, 0.01, n)),
        "high": prices * (1 + np.random.uniform(0, 0.03, n)),
        "low": prices * (1 - np.random.uniform(0, 0.03, n)),
        "close": prices,
        "volume": np.random.randint(50_000_000, 200_000_000, n),
        "turnover": np.random.uniform(1e9, 5e9, n),
        "turnover_rate": np.random.uniform(1.0, 8.0, n),
    })
    return df


@pytest.fixture
def sample_ohlcv_5yr() -> pd.DataFrame:
    """5+ years (1300 days) of synthetic data for walk-forward testing."""
    np.random.seed(123)
    n = 1300
    dates = pd.bdate_range("2019-01-02", periods=n)
    base = 30.0
    returns = np.random.normal(0.0005, 0.03, n)
    prices = base * np.cumprod(1 + returns)

    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": prices * (1 + np.random.uniform(-0.01, 0.01, n)),
        "high": prices * (1 + np.random.uniform(0, 0.03, n)),
        "low": prices * (1 - np.random.uniform(0, 0.03, n)),
        "close": prices,
        "volume": np.random.randint(50_000_000, 200_000_000, n),
        "turnover": np.random.uniform(1e9, 5e9, n),
        "turnover_rate": np.random.uniform(1.0, 8.0, n),
    })
    return df
