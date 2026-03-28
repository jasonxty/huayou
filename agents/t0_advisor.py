"""T+0 Intraday Trading Advisor for A-share positions.

A-share T+0 rule: shares bought before today can be sold today,
but shares bought today cannot be sold until tomorrow (T+1).
So "做T" = sell high from existing position → buy back low same day.

All prices and zones are derived from technical data: ATR, support/resistance,
Bollinger bands, and the current market regime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class T0Advice:
    """Structured T+0 trading recommendation."""
    has_position: bool
    quantity: int = 0
    cost: float = 0.0
    current_price: float = 0.0
    pnl_pct: float = 0.0

    t0_enabled: bool = False
    t0_lot: int = 0
    sell_zone_low: float = 0.0
    sell_zone_high: float = 0.0
    buy_zone_low: float = 0.0
    buy_zone_high: float = 0.0
    stop_loss: float = 0.0

    # Split selling: sell lot1 at sell_zone_low, lot2 at sell_zone_high
    sell_lot1: int = 0
    sell_lot2: int = 0

    # Breakout escape: if price breaks above this, don't chase — wait for pullback
    breakout_price: float = 0.0
    rebuy_abort_price: float = 0.0  # if sold and price stays above this, skip rebuy today

    strategy: str = ""  # "先卖后买" or "先买后卖" or "不建议做T"
    risk_note: str = ""
    signals: list[str] = None
    escape_plan: list[str] = None  # what to do if price keeps rising after sell

    def __post_init__(self):
        if self.signals is None:
            self.signals = []
        if self.escape_plan is None:
            self.escape_plan = []


def _build_escape_plan(advice: T0Advice, bearish: bool, bullish: bool) -> None:
    """Populate escape_plan for the scenario where price keeps rising after sell."""
    if not advice.t0_enabled:
        return

    if advice.sell_lot2 > 0:
        advice.escape_plan.append(
            f"分批卖出: 先卖{advice.sell_lot1}股@¥{advice.sell_zone_low:.2f}，"
            f"再卖{advice.sell_lot2}股@¥{advice.sell_zone_high:.2f}"
        )
    advice.escape_plan.append(
        f"若卖出后价格突破¥{advice.rebuy_abort_price:.2f}且不回落 → 今日不接回，"
        f"明日观察（剩余{advice.quantity - advice.t0_lot}股继续享受上涨）"
    )
    advice.escape_plan.append(
        f"若放量突破¥{advice.breakout_price:.2f} → 趋势可能反转，"
        f"次日开盘补回仓位并上调做T区间"
    )

    if bearish:
        advice.escape_plan.append(
            "空头反弹卖飞概率低；即使卖飞，降低成本优先于追涨"
        )
    elif bullish:
        advice.escape_plan.append(
            "多头趋势中卖飞有成本，建议只卖计划仓位，不追空"
        )


def advise(
    position: dict | None,
    latest_price: float,
    atr: float,
    support: float,
    resistance: float,
    boll_upper: float,
    boll_mid: float,
    boll_lower: float,
    tech_score: float,
    regime: dict,
) -> T0Advice:
    """Generate T+0 trading advice based on position and technical data."""

    if position is None or position.get("quantity", 0) <= 0:
        return T0Advice(
            has_position=False,
            current_price=latest_price,
            strategy="无持仓",
            signals=["当前无持仓，无法做T"],
        )

    qty = position["quantity"]
    cost = position["cost"]
    pnl_pct = (latest_price - cost) / cost * 100

    advice = T0Advice(
        has_position=True,
        quantity=qty,
        cost=cost,
        current_price=latest_price,
        pnl_pct=round(pnl_pct, 1),
    )

    if qty < 200:
        advice.strategy = "不建议做T"
        advice.risk_note = "持仓不足200股（最小交易单位），做T空间有限"
        advice.signals.append(f"持仓{qty}股，低于做T最低门槛200股")
        return advice

    if atr <= 0:
        advice.strategy = "不建议做T"
        advice.risk_note = "ATR数据异常"
        return advice

    advice.t0_enabled = True

    regime_bearish = regime.get("trend") == "down"
    regime_bullish = regime.get("trend") == "up"
    regime_oversold = regime.get("rsi") == "oversold"

    if regime_bearish and not regime_oversold:
        lot_ratio = 0.2
    elif regime_bullish:
        lot_ratio = 0.3
    else:
        lot_ratio = 0.25

    if abs(pnl_pct) > 15:
        lot_ratio = min(lot_ratio, 0.2)

    raw_lot = int(qty * lot_ratio)
    advice.t0_lot = max(100, (raw_lot // 100) * 100)
    if advice.t0_lot > qty:
        advice.t0_lot = (qty // 100) * 100

    half_atr = atr * 0.5
    advice.sell_zone_low = round(max(latest_price + half_atr * 0.3, boll_mid), 2)
    advice.sell_zone_high = round(min(latest_price + atr, resistance), 2)
    if advice.sell_zone_low > advice.sell_zone_high:
        advice.sell_zone_low, advice.sell_zone_high = advice.sell_zone_high, advice.sell_zone_low

    advice.buy_zone_low = round(max(latest_price - atr, support, boll_lower), 2)
    advice.buy_zone_high = round(latest_price - half_atr * 0.3, 2)
    if advice.buy_zone_low > advice.buy_zone_high:
        advice.buy_zone_low, advice.buy_zone_high = advice.buy_zone_high, advice.buy_zone_low

    advice.stop_loss = round(support - atr * 0.5, 2)

    # Split selling: first batch at sell_zone_low, second batch at sell_zone_high.
    # If only 100 shares, no split — sell all at sell_zone_low.
    if advice.t0_lot >= 200:
        advice.sell_lot1 = (advice.t0_lot // 2 // 100) * 100
        advice.sell_lot2 = advice.t0_lot - advice.sell_lot1
    else:
        advice.sell_lot1 = advice.t0_lot
        advice.sell_lot2 = 0

    # Breakout escape: if price blows through resistance + 0.5*ATR,
    # the move is too strong — don't chase, wait for pullback to rebuy.
    advice.breakout_price = round(resistance + atr * 0.5, 2)
    advice.rebuy_abort_price = round(advice.sell_zone_high + atr * 0.3, 2)

    _build_escape_plan(advice, regime_bearish, regime_bullish)

    if regime_oversold:
        advice.strategy = "先买后卖"
        advice.signals.append("超卖区域 → 可先低吸，等反弹后卖出原有持仓")
    elif regime_bearish:
        advice.strategy = "先卖后买"
        advice.signals.append("空头趋势 → 优先高抛降低成本，待回落后接回")
    elif tech_score >= 20:
        advice.strategy = "先买后卖"
        advice.signals.append("技术面偏多 → 先低吸增仓，高位卖出原持仓摊薄成本")
    elif tech_score <= -20:
        advice.strategy = "先卖后买"
        advice.signals.append("技术面偏空 → 先逢高减仓，待回调再接回")
    else:
        advice.strategy = "先卖后买"
        advice.signals.append("震荡市 → 默认先卖后买，降低风险")

    spread = advice.sell_zone_low - advice.buy_zone_high
    if spread < atr * 0.15:
        advice.t0_enabled = False
        advice.strategy = "不建议做T"
        advice.risk_note = "买卖区间过窄，手续费可能吃掉利润"
        advice.signals.append(f"高抛低吸价差仅{spread:.2f}元，不足ATR的15%")
        return advice

    if pnl_pct < -20:
        advice.risk_note = "深度套牢，做T以降本为主，严格止损"
        advice.signals.append(f"浮亏{pnl_pct:.1f}%，建议小仓位谨慎做T")
    elif pnl_pct < -10:
        advice.risk_note = "中度套牢，做T摊薄成本"
        advice.signals.append(f"浮亏{pnl_pct:.1f}%，逢高减仓+低吸降本")
    elif pnl_pct < 0:
        advice.risk_note = "小幅浮亏，做T回本"
    elif pnl_pct > 10:
        advice.risk_note = "浮盈中，做T锁定部分利润"
    else:
        advice.risk_note = "小幅浮盈，做T增厚收益"

    return advice
