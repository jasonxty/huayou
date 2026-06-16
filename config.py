import os
from pathlib import Path

import yaml

TICKER = "603799"
TICKER_NAME = "Huayou Cobalt"

# Buffett — the system's AI trading persona
KOBE_NAME = "Buffett"
KOBE_AVATAR = "\U0001F4B0"  # money bag emoji

# Default strategy weights (Buffett learns to adjust these over time)
KOBE_DEFAULT_WEIGHTS = {
    "tech_buy_threshold": 25,
    "tech_mild_buy_threshold": 12,
    "tech_sell_threshold": -25,
    "tech_mild_sell_threshold": -12,
    "expert_adjustment": 0.08,
    "regime_trust": 1.0,        # multiplier on regime-based confidence
    "fundamental_trust": 1.0,   # multiplier on fundamental score influence
    "commodity_weight": 0.5,    # multiplier on commodity signal contribution
    "regime_buy_adjust": {},   # "trend|rsi" -> int, 加到买阈上（变难买）
    "regime_sell_adjust": {},  # "trend|rsi" -> int, 从卖阈减去（更易卖）
}
KOBE_WEIGHT_BOUNDS = {
    "tech_buy_threshold": (15, 40),
    "tech_mild_buy_threshold": (5, 25),
    "tech_sell_threshold": (-40, -15),
    "tech_mild_sell_threshold": (-25, -5),
    "expert_adjustment": (0.02, 0.15),
    "regime_trust": (0.5, 1.5),
    "fundamental_trust": (0.5, 1.5),
    "commodity_weight": (0.0, 1.5),
}
KOBE_MIN_SAMPLES_FOR_LEARNING = 10  # min briefs before Buffett starts adjusting

# Buffett 预测标签：混合收益 + 手续费（用于学习/日记/校准，一致定义）
KOBE_LABEL_WEIGHT_1D = 0.45
KOBE_LABEL_WEIGHT_5D = 0.55
KOBE_LABEL_HOLD_FLAT = 0.010  # |混合收益| 低于此视为 HOLD「踏对节奏」(tightened for volatile stock)
KOBE_LABEL_HOLD_NEUTRAL_BELOW = 0.035  # 介于 flat 与此之间为 neutral；更大为错判
KOBE_USE_FRICTION_IN_LABEL = True
# 用于估算双边手续费占价比（万元级名义本金）
KOBE_FRICTION_NOTIONAL_CNY = 100_000.0

# 技术面 + 基本面 合成得分（送入阈值比较）
KOBE_FUND_BLEND_RATIO = 0.35  # effective = tech + fundamental_trust * fund * ratio

# 基本面极差时禁止激进 BUY（仍可比阈值得出轻仓等）
KOBE_FUND_BLOCK_AGGRESSIVE_BELOW = -38.0

# 学习：缩小步长防过拟合；行情分桶样本门槛
KOBE_LEARNING_STEP_SCALE = 0.55
KOBE_REGIME_MIN_SAMPLES = 5
KOBE_REGIME_BUCKET_MIN_ACTIONS = 3
KOBE_REGIME_ADJUST_STEP = 2
KOBE_REGIME_ADJUST_CAP = 12

# 校准：置信度映射到昨日 bucket 的 actual/predicted，比例夹紧
KOBE_CALIBRATION_RATIO_CLAMP = (0.72, 1.28)

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


def estimate_round_trip_friction_fraction(notional_cny: float | None = None) -> float:
    """双边手续费约占名义本金的比例，用于标签里 BUY 是否跑赢成本。"""
    n = float(notional_cny or KOBE_FRICTION_NOTIONAL_CNY)
    if n <= 0:
        return 0.002
    buy_f = calc_trade_fee(n, "BUY")
    sell_f = calc_trade_fee(n, "SELL")
    return (buy_f + sell_f) / n


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
