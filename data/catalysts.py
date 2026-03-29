"""Key catalysts and commodity price tracking for 603799.

Fetches LME/SHFE nickel spot price and maintains a calendar of
upcoming events that could materially impact the stock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import akshare as ak

logger = logging.getLogger(__name__)


@dataclass
class CatalystEvent:
    name: str
    expected_date: str  # "2026-04" or "2026-07" or "monthly"
    description: str
    impact: str  # "利好" / "利空" / "双向" / "待定"
    category: str  # "policy" / "earnings" / "commodity" / "industry"


@dataclass
class CatalystSnapshot:
    """All catalyst data for one analysis run."""
    lme_nickel_usd: float | None = None  # LME 3-month nickel, USD/ton
    lme_nickel_cny: float | None = None  # RMB-equivalent quote
    nickel_change_pct: float | None = None  # daily change %
    nickel_fetch_time: str = ""

    shfe_nickel: float | None = None  # SHFE沪镍主力, CNY/ton
    shfe_nickel_chg: float | None = None  # daily change CNY
    lithium_carbonate: float | None = None  # GFEX碳酸锂主力, CNY/ton
    lithium_carbonate_chg: float | None = None  # daily change CNY

    events: list[CatalystEvent] = field(default_factory=list)
    fetch_errors: list[str] = field(default_factory=list)


def _get_upcoming_events() -> list[CatalystEvent]:
    """Static calendar of key upcoming events. Update as events pass."""
    today = date.today()
    events = []

    if today < date(2026, 5, 1):
        events.append(CatalystEvent(
            name="华友钴业2025年报披露",
            expected_date="2026-04",
            description="关注全年营收/净利、镍钴锂各业务毛利率、印尼项目并表利润",
            impact="双向",
            category="earnings",
        ))

    if today < date(2026, 8, 1):
        events.append(CatalystEvent(
            name="印尼RKAB镍矿配额审批",
            expected_date="2026-07",
            description="印尼2026年镍矿RKAB配额已收紧至1.5亿吨(同比-17%)，7月可能补充配额或继续收紧；"
                        "若继续收紧→镍价上行+华友矿端优势扩大；若放松→镍价承压",
            impact="双向",
            category="policy",
        ))

    if today < date(2026, 9, 1):
        events.append(CatalystEvent(
            name="华友钴业2026年中报",
            expected_date="2026-08",
            description="上半年业绩是否兑现镍价回暖预期",
            impact="双向",
            category="earnings",
        ))

    events.append(CatalystEvent(
        name="LME镍价月度走势",
        expected_date="monthly",
        description="LME镍3个月合约，若站稳$16,000/吨以上对华友冶炼利润有利；"
                    "低于$14,000则利润承压",
        impact="双向",
        category="commodity",
    ))

    events.append(CatalystEvent(
        name="新能源车月度销量",
        expected_date="monthly",
        description="中国新能源车月度销量直接影响三元正极材料需求，间接影响镍钴锂价格",
        impact="双向",
        category="industry",
    ))

    return events


def _fetch_lme_nickel() -> tuple[float | None, float | None, float | None, str]:
    """Fetch LME Nickel 3-month real-time price.

    Returns (usd_price, cny_price, change_pct, fetch_time).
    """
    try:
        df = ak.futures_foreign_commodity_realtime(symbol="NID")
        if df.empty:
            return None, None, None, ""

        row = df.iloc[0]
        usd_price = float(row.get("最新价", 0) or 0)
        cny_price = float(row.get("人民币报价", 0) or 0) if "人民币报价" in row.index else None
        change_pct = float(row.get("涨跌幅", 0) or 0) if "涨跌幅" in row.index else None
        fetch_time = str(row.get("行情时间", "")) if "行情时间" in row.index else ""

        if usd_price <= 0:
            return None, None, None, ""

        return usd_price, cny_price, change_pct, fetch_time

    except Exception as e:
        logger.warning("Failed to fetch LME nickel: %s", e)
        return None, None, None, ""


def _fetch_domestic_futures(symbol: str, name: str) -> tuple[float | None, float | None]:
    """Fetch latest domestic futures close and daily change from Sina.

    Returns (close_price, change_from_prev_close).
    """
    try:
        df = ak.futures_main_sina(symbol=symbol)
        if df.empty or len(df) < 2:
            return None, None
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(latest["收盘价"])
        prev_close = float(prev["收盘价"])
        change = round(close - prev_close, 0)
        return close, change
    except Exception as e:
        logger.warning("Failed to fetch %s (%s): %s", name, symbol, e)
        return None, None


def fetch_catalysts() -> CatalystSnapshot:
    """Gather all catalyst data: commodity prices + upcoming events."""
    snap = CatalystSnapshot()

    logger.info("Fetching LME nickel price...")
    usd, cny, chg, ftime = _fetch_lme_nickel()
    snap.lme_nickel_usd = usd
    snap.lme_nickel_cny = cny
    snap.nickel_change_pct = chg
    snap.nickel_fetch_time = ftime
    if usd is None:
        snap.fetch_errors.append("LME镍价获取失败")

    logger.info("Fetching SHFE nickel (ni0)...")
    snap.shfe_nickel, snap.shfe_nickel_chg = _fetch_domestic_futures("ni0", "沪镍")
    if snap.shfe_nickel is None:
        snap.fetch_errors.append("沪镍主力获取失败")

    logger.info("Fetching lithium carbonate (lc0)...")
    snap.lithium_carbonate, snap.lithium_carbonate_chg = _fetch_domestic_futures("lc0", "碳酸锂")
    if snap.lithium_carbonate is None:
        snap.fetch_errors.append("碳酸锂主力获取失败")

    snap.events = _get_upcoming_events()

    return snap
