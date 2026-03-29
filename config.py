import os
from pathlib import Path

import yaml

TICKER = "603799"
TICKER_NAME = "华友钴业"
DB_PATH = Path(__file__).parent / "huayou.db"

FETCH_RETRY_ATTEMPTS = 3
FETCH_RETRY_DELAYS = [5, 15, 45]

BACKTEST_MIN_OOS_TRADES = 20
BACKTEST_MIN_WIN_RATE = 0.55
BACKTEST_MIN_SHARPE = 1.0
BACKTEST_MAX_DRAWDOWN = 0.20

REGIME_MIN_SAMPLES = 15
REGIME_CONFIDENCE_CLAMP = (0.50, 0.85)

# Monitor settings
MONITOR_INTERVAL = 30  # seconds between price checks
MONITOR_DAILY_PUSH_LIMIT = 5
MONITOR_COOLDOWN = 1800  # 30 min per-alert cooldown

# Server酱 SendKey — loaded from local config.yaml (gitignored)
_LOCAL_CONFIG = Path(__file__).parent / "config.yaml"


def get_serverchan_key() -> str:
    """Load Server酱 SendKey from local config.yaml or env var."""
    if _LOCAL_CONFIG.exists():
        with open(_LOCAL_CONFIG) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("notification", {}).get("serverchan_key", "")
    return os.environ.get("SERVERCHAN_KEY", "")
