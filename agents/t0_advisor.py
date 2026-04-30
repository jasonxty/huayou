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

    strategy: str = ""  # "Sell First, Buy Back" / "Buy First, Sell Later" / "T+0 Not Advised"
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
            f"Split sell: {advice.sell_lot1} shares @¥{advice.sell_zone_low:.2f}, "
            f"then {advice.sell_lot2} shares @¥{advice.sell_zone_high:.2f}"
        )
    advice.escape_plan.append(
        f"If price breaks above ¥{advice.rebuy_abort_price:.2f} after selling → skip buyback today, "
        f"observe tomorrow ({advice.quantity - advice.t0_lot} remaining shares still benefit from upside)"
    )
    advice.escape_plan.append(
        f"If volume breakout above ¥{advice.breakout_price:.2f} → trend may reverse, "
        f"buy back at next open and raise T+0 zones"
    )

    if bearish:
        advice.escape_plan.append(
            "Bearish bounce — low risk of missing rally; cost reduction takes priority over chasing"
        )
    elif bullish:
        advice.escape_plan.append(
            "Bullish trend — selling has opportunity cost; only sell planned lot, don't chase shorts"
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
            strategy="No Position",
            signals=["No position held — cannot do T+0"],
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
        advice.strategy = "T+0 Not Advised"
        advice.risk_note = "Position below 200 shares (minimum lot), insufficient for T+0"
        advice.signals.append(f"Holding {qty} shares, below 200-share T+0 minimum")
        return advice

    if atr <= 0:
        advice.strategy = "T+0 Not Advised"
        advice.risk_note = "ATR data unavailable"
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
        advice.strategy = "Buy First, Sell Later"
        advice.signals.append("Oversold zone — buy the dip first, sell existing shares on bounce")
    elif regime_bearish:
        advice.strategy = "Sell First, Buy Back"
        advice.signals.append("Bearish trend — sell high to reduce cost, buy back on pullback")
    elif tech_score >= 20:
        advice.strategy = "Buy First, Sell Later"
        advice.signals.append("Bullish technicals — buy low first, sell existing shares at high to reduce avg cost")
    elif tech_score <= -20:
        advice.strategy = "Sell First, Buy Back"
        advice.signals.append("Bearish technicals — sell at high, buy back on pullback")
    else:
        advice.strategy = "Sell First, Buy Back"
        advice.signals.append("Range-bound market — default sell first, buy back to reduce risk")

    spread = advice.sell_zone_low - advice.buy_zone_high
    if spread < atr * 0.15:
        advice.t0_enabled = False
        advice.strategy = "T+0 Not Advised"
        advice.risk_note = "Spread too narrow — fees may exceed profit"
        advice.signals.append(f"Sell/buy spread only ¥{spread:.2f}, below 15% of ATR")
        return advice

    if pnl_pct < -20:
        advice.risk_note = "Deep loss — T+0 for cost reduction only, strict stop-loss"
        advice.signals.append(f"Unrealized loss {pnl_pct:.1f}%, use small lots cautiously")
    elif pnl_pct < -10:
        advice.risk_note = "Moderate loss — T+0 to reduce avg cost"
        advice.signals.append(f"Unrealized loss {pnl_pct:.1f}%, sell high + buy low to reduce cost")
    elif pnl_pct < 0:
        advice.risk_note = "Small loss — T+0 to break even"
    elif pnl_pct > 10:
        advice.risk_note = "In profit — T+0 to lock partial gains"
    else:
        advice.risk_note = "Small profit — T+0 to enhance returns"

    return advice
