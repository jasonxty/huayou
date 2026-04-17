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
MONITOR_INTERVAL = 60  # seconds between price checks
MONITOR_DAILY_PUSH_LIMIT = 5
MONITOR_COOLDOWN = 1800  # 30 min per-alert cooldown
MONITOR_STATUS_INTERVAL = 900  # 15 min — periodic price status popup

# A股交易费率默认值
DEFAULT_COMMISSION_RATE = 0.00025   # 万2.5
DEFAULT_COMMISSION_MIN = 5.0        # 最低¥5
DEFAULT_STAMP_TAX_RATE = 0.0005     # 0.05% (仅卖出)
DEFAULT_TRANSFER_FEE_RATE = 0.00001 # 万0.1 (沪市)

# Server酱 SendKey — loaded from local config.yaml (gitignored)
_LOCAL_CONFIG = Path(__file__).parent / "config.yaml"


def get_serverchan_key() -> str:
    """Load Server酱 SendKey from local config.yaml or env var."""
    if _LOCAL_CONFIG.exists():
        with open(_LOCAL_CONFIG) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("notification", {}).get("serverchan_key", "")
    return os.environ.get("SERVERCHAN_KEY", "")


def get_fee_config() -> dict:
    """Load A-share fee structure from config.yaml."""
    default = {
        "commission_rate": DEFAULT_COMMISSION_RATE,
        "commission_min": DEFAULT_COMMISSION_MIN,
        "stamp_tax_rate": DEFAULT_STAMP_TAX_RATE,
        "transfer_fee_rate": DEFAULT_TRANSFER_FEE_RATE,
    }
    if not _LOCAL_CONFIG.exists():
        return default
    with open(_LOCAL_CONFIG) as f:
        cfg = yaml.safe_load(f) or {}
    fees = cfg.get("fees", {})
    return {
        "commission_rate": float(fees.get("commission_rate", default["commission_rate"])),
        "commission_min": float(fees.get("commission_min", default["commission_min"])),
        "stamp_tax_rate": float(fees.get("stamp_tax_rate", default["stamp_tax_rate"])),
        "transfer_fee_rate": float(fees.get("transfer_fee_rate", default["transfer_fee_rate"])),
    }


def calc_trade_fee(amount: float, direction: str = "BUY") -> float:
    """Calculate A-share trade fee for a given trade amount.

    Buy:  commission (min ¥5) + transfer fee
    Sell: commission (min ¥5) + stamp duty + transfer fee
    """
    fc = get_fee_config()
    commission = max(amount * fc["commission_rate"], fc["commission_min"])
    transfer = amount * fc["transfer_fee_rate"]
    stamp = amount * fc["stamp_tax_rate"] if direction == "SELL" else 0
    return round(commission + transfer + stamp, 2)


def get_taoguba_config() -> dict:
    """Load TaoGuBa expert tracking config from config.yaml.

    Returns dict with keys: enabled (bool), experts (list[dict]),
    max_post_age_days (int), request_delay_seconds (float).
    Returns disabled config if file missing or section absent.
    """
    default = {"enabled": False, "experts": [], "max_post_age_days": 3,
               "request_delay_seconds": 3.0}
    if not _LOCAL_CONFIG.exists():
        return default
    with open(_LOCAL_CONFIG) as f:
        cfg = yaml.safe_load(f) or {}
    tgb = cfg.get("taoguba", {})
    if not tgb or not tgb.get("enabled", False):
        return default
    return {
        "enabled": True,
        "experts": tgb.get("experts", []),
        "max_post_age_days": int(tgb.get("max_post_age_days", 3)),
        "request_delay_seconds": float(tgb.get("request_delay_seconds", 3.0)),
    }
