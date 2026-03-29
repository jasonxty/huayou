"""Real-time T+0 price monitor with WeChat push via Server酱.

Fetches live quotes from Eastmoney every 30s during trading hours.
When price hits a T+0 threshold (sell zone, buy zone, stop loss, breakout),
sends a WeChat notification. Daily limit: 5 pushes (Server酱 free plan).

Usage:
    python monitor.py               # Start monitoring (runs during trading hours)
    python monitor.py --test-push   # Send a test notification
    python monitor.py --once        # Check once and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path

import requests

import config
from data.store import get_connection, init_db, load_ohlcv, load_indicators, load_position
from data.holidays import is_trading_day
from data.indicators import compute_all
from agents.technical import analyze as analyze_technical
from agents.t0_advisor import advise as advise_t0, T0Advice
from agents.strategist import classify_regime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("monitor")


# ── Eastmoney real-time quote (same API as stock_monitor) ───────────────

@dataclass
class Quote:
    price: float
    open_price: float
    high: float
    low: float
    change_pct: float
    volume: int
    timestamp: datetime


def fetch_realtime_quote(stock_code: str = config.TICKER) -> Quote | None:
    """Fetch real-time quote from Eastmoney push API."""
    market = "1" if stock_code.startswith("6") else "0"
    secid = f"{market}.{stock_code}"
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": secid,
        "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f169,f170",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "_": str(int(time.time() * 1000)),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rc") != 0 or "data" not in data:
            return None
        d = data["data"]

        def p(val):
            if val is None or val == "-":
                return 0.0
            return float(val) / 100

        return Quote(
            price=p(d.get("f43")),
            open_price=p(d.get("f46")),
            high=p(d.get("f44")),
            low=p(d.get("f45")),
            change_pct=p(d.get("f170")),
            volume=int(d.get("f47", 0) or 0),
            timestamp=datetime.now(),
        )
    except Exception as e:
        logger.error("Quote fetch failed: %s", e)
        return None


def is_trading_time() -> bool:
    """Check if current time is within A-share trading hours."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning = (t >= datetime.strptime("09:25", "%H:%M").time()
               and t <= datetime.strptime("11:30", "%H:%M").time())
    afternoon = (t >= datetime.strptime("13:00", "%H:%M").time()
                 and t <= datetime.strptime("15:00", "%H:%M").time())
    return morning or afternoon


# ── Server酱 push ──────────────────────────────────────────────────────

@dataclass
class PushResult:
    success: bool
    message: str


def send_wechat(title: str, content: str = "") -> PushResult:
    """Send WeChat notification via Server酱."""
    key = config.get_serverchan_key()
    if not key or key == "YOUR_SENDKEY_HERE":
        return PushResult(False, "Server酱 SendKey 未配置")

    if len(title) > 32:
        title = title[:29] + "..."

    try:
        resp = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": content},
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            return PushResult(True, "发送成功")
        return PushResult(False, result.get("message", "未知错误"))
    except Exception as e:
        return PushResult(False, str(e))


# ── Alert tracker (daily quota + per-alert dedup) ──────────────────────

ALERT_SELL1 = "sell_zone_1"
ALERT_SELL2 = "sell_zone_2"
ALERT_BUY = "buy_zone"
ALERT_STOP = "stop_loss"
ALERT_BREAKOUT = "breakout"


@dataclass
class AlertTracker:
    """Track which alerts have fired today. Enforces daily push limit."""
    today: date = field(default_factory=date.today)
    push_count: int = 0
    fired: set = field(default_factory=set)

    def can_push(self) -> bool:
        self._rotate()
        return self.push_count < config.MONITOR_DAILY_PUSH_LIMIT

    def has_fired(self, alert_id: str) -> bool:
        self._rotate()
        return alert_id in self.fired

    def record(self, alert_id: str) -> None:
        self._rotate()
        self.fired.add(alert_id)
        self.push_count += 1

    def remaining(self) -> int:
        self._rotate()
        return config.MONITOR_DAILY_PUSH_LIMIT - self.push_count

    def _rotate(self) -> None:
        if date.today() != self.today:
            self.today = date.today()
            self.push_count = 0
            self.fired.clear()


# ── Core monitoring logic ──────────────────────────────────────────────

def compute_t0_advice() -> T0Advice | None:
    """Run the analysis pipeline and return T+0 advice."""
    conn = get_connection()
    init_db(conn)
    try:
        ohlcv = load_ohlcv(conn)
        indicators = load_indicators(conn)
        position = load_position(conn)

        if ohlcv.empty or indicators.empty:
            logger.error("No data. Run `python analyze.py` first.")
            return None
        if not position:
            logger.error("No position. Run `python analyze.py --set-position QTY COST`.")
            return None

        latest_price = float(ohlcv.iloc[-1]["close"])
        tech = analyze_technical(ohlcv, indicators)
        regime = classify_regime(indicators)
        latest_ind = indicators.iloc[-1]

        return advise_t0(
            position=position,
            latest_price=latest_price,
            atr=float(latest_ind.get("atr14", 0)),
            support=tech.details.get("support", 0),
            resistance=tech.details.get("resistance", 0),
            boll_upper=float(latest_ind.get("boll_upper", 0)),
            boll_mid=float(latest_ind.get("boll_mid", 0)),
            boll_lower=float(latest_ind.get("boll_lower", 0)),
            tech_score=tech.score,
            regime=regime,
        )
    finally:
        conn.close()


def check_alerts(quote: Quote, advice: T0Advice, tracker: AlertTracker) -> None:
    """Check if current price triggers any T+0 alerts and push."""
    if not advice.t0_enabled:
        return

    price = quote.price
    alerts: list[tuple[str, str, str]] = []

    if price >= advice.sell_zone_high and not tracker.has_fired(ALERT_SELL2):
        alerts.append((
            ALERT_SELL2,
            f"🔴 {config.TICKER_NAME}高抛第2批",
            (f"**{config.TICKER_NAME} ¥{price:.2f}** 触及高抛第2批区间\n\n"
             f"- 建议卖出: **{advice.sell_lot2}股** @ ¥{advice.sell_zone_high:.2f}\n"
             f"- 成本: ¥{advice.cost:.2f} | 当前浮盈: {advice.pnl_pct:.1f}%\n"
             f"- 剩余推送额度: {tracker.remaining() - 1}")
        ))
    elif price >= advice.sell_zone_low and not tracker.has_fired(ALERT_SELL1):
        alerts.append((
            ALERT_SELL1,
            f"🔴 {config.TICKER_NAME}高抛第1批",
            (f"**{config.TICKER_NAME} ¥{price:.2f}** 触及高抛第1批区间\n\n"
             f"- 建议卖出: **{advice.sell_lot1}股** @ ¥{advice.sell_zone_low:.2f}\n"
             f"- 高抛第2批目标: ¥{advice.sell_zone_high:.2f}\n"
             f"- 成本: ¥{advice.cost:.2f}")
        ))

    if price <= advice.stop_loss and not tracker.has_fired(ALERT_STOP):
        alerts.append((
            ALERT_STOP,
            f"⛔ {config.TICKER_NAME}触及止损",
            (f"**{config.TICKER_NAME} ¥{price:.2f}** 跌破止损位 ¥{advice.stop_loss:.2f}\n\n"
             f"- 建议: 立即减仓或清仓\n"
             f"- 持仓: {advice.quantity}股 @ ¥{advice.cost:.2f}")
        ))
    elif price <= advice.buy_zone_high and not tracker.has_fired(ALERT_BUY):
        alerts.append((
            ALERT_BUY,
            f"🟢 {config.TICKER_NAME}低吸触发",
            (f"**{config.TICKER_NAME} ¥{price:.2f}** 进入低吸区间\n\n"
             f"- 建议买入: **{advice.t0_lot}股** @ ¥{advice.buy_zone_high:.2f}~¥{advice.buy_zone_low:.2f}\n"
             f"- 止损位: ¥{advice.stop_loss:.2f}\n"
             f"- 成本: ¥{advice.cost:.2f}")
        ))

    if price >= advice.breakout_price and not tracker.has_fired(ALERT_BREAKOUT):
        alerts.append((
            ALERT_BREAKOUT,
            f"⚡ {config.TICKER_NAME}放量突破",
            (f"**{config.TICKER_NAME} ¥{price:.2f}** 突破关键阻力 ¥{advice.breakout_price:.2f}\n\n"
             f"- 若已卖出做T仓位: **今日不追回**，明日观察\n"
             f"- 若持仓未动: 继续持有，享受上涨\n"
             f"- 剩余底仓: {advice.quantity - advice.t0_lot}股")
        ))

    for alert_id, title, body in alerts:
        if not tracker.can_push():
            logger.warning("Daily push limit (%d) reached, skipping: %s",
                           config.MONITOR_DAILY_PUSH_LIMIT, title)
            break
        result = send_wechat(title, body)
        if result.success:
            tracker.record(alert_id)
            logger.info("✓ Pushed: %s (remaining: %d)", title, tracker.remaining())
        else:
            logger.error("✗ Push failed: %s — %s", title, result.message)


def print_status(quote: Quote, advice: T0Advice, tracker: AlertTracker) -> None:
    """Print monitoring status to console."""
    now = datetime.now().strftime("%H:%M:%S")
    chg = f"+{quote.change_pct:.2f}" if quote.change_pct >= 0 else f"{quote.change_pct:.2f}"
    fired = ", ".join(tracker.fired) if tracker.fired else "无"
    print(
        f"[{now}] ¥{quote.price:.2f} ({chg}%) "
        f"| 高:{quote.high:.2f} 低:{quote.low:.2f} "
        f"| 卖区:¥{advice.sell_zone_low:.2f}~{advice.sell_zone_high:.2f} "
        f"| 买区:¥{advice.buy_zone_low:.2f}~{advice.buy_zone_high:.2f} "
        f"| 推送:{tracker.push_count}/{config.MONITOR_DAILY_PUSH_LIMIT} "
        f"| 已触发:{fired}",
        flush=True,
    )


# ── Main loop ──────────────────────────────────────────────────────────

def run_monitor(once: bool = False) -> None:
    """Main monitoring loop."""
    if not is_trading_day():
        logger.info("Non-trading day (holiday/weekend). Exiting.")
        return

    logger.info("Computing T+0 advice from latest data...")
    advice = compute_t0_advice()
    if advice is None:
        return
    if not advice.t0_enabled:
        logger.warning("T+0 not enabled today (strategy: %s). Monitoring skipped.", advice.strategy)
        return

    tracker = AlertTracker()

    print(f"\n{'═' * 60}")
    print(f"  {config.TICKER_NAME} ({config.TICKER}) T+0 实时监控")
    print(f"{'═' * 60}")
    print(f"  策略: {advice.strategy}")
    print(f"  做T仓位: {advice.t0_lot}股 (总持仓{advice.quantity}股)")
    print(f"  高抛区间: ¥{advice.sell_zone_low:.2f} ~ ¥{advice.sell_zone_high:.2f}")
    print(f"    第1批: {advice.sell_lot1}股 @ ¥{advice.sell_zone_low:.2f}")
    if advice.sell_lot2 > 0:
        print(f"    第2批: {advice.sell_lot2}股 @ ¥{advice.sell_zone_high:.2f}")
    print(f"  低吸区间: ¥{advice.buy_zone_low:.2f} ~ ¥{advice.buy_zone_high:.2f}")
    print(f"  止损价:   ¥{advice.stop_loss:.2f}")
    print(f"  突破价:   ¥{advice.breakout_price:.2f}")
    print(f"  每日推送额度: {config.MONITOR_DAILY_PUSH_LIMIT}次")
    print(f"{'═' * 60}\n")

    if once:
        quote = fetch_realtime_quote()
        if quote:
            check_alerts(quote, advice, tracker)
            print_status(quote, advice, tracker)
        else:
            logger.error("Failed to fetch quote.")
        return

    logger.info("Entering monitoring loop (interval: %ds)...", config.MONITOR_INTERVAL)
    while True:
        if not is_trading_time():
            now = datetime.now()
            if now.hour >= 15:
                logger.info("Market closed for today. Exiting.")
                break
            logger.info("Outside trading hours. Waiting...")
            time.sleep(60)
            continue

        quote = fetch_realtime_quote()
        if quote and quote.price > 0:
            check_alerts(quote, advice, tracker)
            print_status(quote, advice, tracker)
        else:
            logger.warning("Quote unavailable, retrying next cycle.")

        time.sleep(config.MONITOR_INTERVAL)


def test_push() -> None:
    """Send a test notification to verify Server酱 config."""
    key = config.get_serverchan_key()
    masked = key[:8] + "..." if len(key) > 8 else key
    logger.info("SendKey: %s", masked)

    result = send_wechat(
        f"📊 {config.TICKER_NAME}监控测试",
        (f"这是 **{config.TICKER_NAME}({config.TICKER})** T+0 监控的测试消息。\n\n"
         f"如果你收到了，说明配置正确！\n\n"
         f"---\n"
         f"- 监控间隔: {config.MONITOR_INTERVAL}s\n"
         f"- 每日推送上限: {config.MONITOR_DAILY_PUSH_LIMIT}次\n"
         f"- 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    )
    if result.success:
        print("✓ 测试消息发送成功！请检查微信。")
    else:
        print(f"✗ 发送失败: {result.message}")


def main():
    parser = argparse.ArgumentParser(
        description="华友钴业 T+0 实时监控 + 微信推送",
    )
    parser.add_argument("--test-push", action="store_true",
                        help="发送测试通知到微信")
    parser.add_argument("--once", action="store_true",
                        help="检查一次就退出（不循环）")
    args = parser.parse_args()

    if args.test_push:
        test_push()
        return

    run_monitor(once=args.once)


if __name__ == "__main__":
    main()
