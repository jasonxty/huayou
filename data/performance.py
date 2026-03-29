"""Performance tracking — compare morning brief recommendations vs actual outcomes.

Tracks each brief's ACTION recommendation and computes forward returns
(1-day, 5-day, 20-day) once enough data accumulates.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

import config

logger = logging.getLogger(__name__)


@dataclass
class BriefPerformance:
    """Performance of a single brief recommendation."""
    brief_date: str
    action: str
    price_at_brief: float
    return_1d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    correct_1d: bool | None = None
    correct_5d: bool | None = None


@dataclass
class PerformanceSummary:
    """Aggregate performance across all tracked briefs."""
    total_briefs: int = 0
    briefs_with_1d: int = 0
    briefs_with_5d: int = 0
    hit_rate_1d: float = 0.0
    hit_rate_5d: float = 0.0
    avg_return_1d: float = 0.0
    avg_return_5d: float = 0.0
    details: list[BriefPerformance] = None

    def __post_init__(self):
        if self.details is None:
            self.details = []


def _is_correct(action: str, ret: float) -> bool:
    """Check if the brief's recommendation was directionally correct."""
    if "BUY" in action:
        return ret > 0
    elif "SELL" in action:
        return ret < 0
    return True  # HOLD is always "correct" (no directional bet)


def compute_performance(conn: sqlite3.Connection) -> PerformanceSummary:
    """Compute forward returns for all historical briefs."""
    briefs_df = pd.read_sql(
        "SELECT date, action, confidence FROM briefs ORDER BY date", conn
    )
    ohlcv_df = pd.read_sql("SELECT date, close FROM ohlcv ORDER BY date", conn)

    if briefs_df.empty or ohlcv_df.empty:
        return PerformanceSummary()

    ohlcv_df["date"] = pd.to_datetime(ohlcv_df["date"])
    prices = ohlcv_df.set_index("date")["close"]

    details = []
    correct_1d = []
    correct_5d = []
    returns_1d = []
    returns_5d = []

    for _, row in briefs_df.iterrows():
        brief_date = str(row["date"])
        action = str(row["action"])

        try:
            bd = pd.Timestamp(brief_date)
        except (ValueError, TypeError):
            continue

        if bd not in prices.index:
            idx = prices.index.searchsorted(bd)
            if idx >= len(prices.index):
                continue
            bd = prices.index[idx]

        price_at = float(prices.loc[bd])
        bp = BriefPerformance(
            brief_date=brief_date, action=action, price_at_brief=price_at,
        )

        bd_idx = prices.index.get_loc(bd)

        if bd_idx + 1 < len(prices):
            p1 = float(prices.iloc[bd_idx + 1])
            bp.return_1d = round((p1 - price_at) / price_at * 100, 2)
            bp.correct_1d = _is_correct(action, bp.return_1d)
            returns_1d.append(bp.return_1d)
            correct_1d.append(bp.correct_1d)

        if bd_idx + 5 < len(prices):
            p5 = float(prices.iloc[bd_idx + 5])
            bp.return_5d = round((p5 - price_at) / price_at * 100, 2)
            bp.correct_5d = _is_correct(action, bp.return_5d)
            returns_5d.append(bp.return_5d)
            correct_5d.append(bp.correct_5d)

        details.append(bp)

    total = len(details)
    n1d = len(correct_1d)
    n5d = len(correct_5d)

    return PerformanceSummary(
        total_briefs=total,
        briefs_with_1d=n1d,
        briefs_with_5d=n5d,
        hit_rate_1d=round(sum(correct_1d) / n1d * 100, 1) if n1d > 0 else 0.0,
        hit_rate_5d=round(sum(correct_5d) / n5d * 100, 1) if n5d > 0 else 0.0,
        avg_return_1d=round(sum(returns_1d) / n1d, 2) if n1d > 0 else 0.0,
        avg_return_5d=round(sum(returns_5d) / n5d, 2) if n5d > 0 else 0.0,
        details=details,
    )


def format_performance(summary: PerformanceSummary) -> str:
    """Format performance summary as a readable report."""
    lines = [
        f"{'═' * 56}",
        f"  华友钴业 (603799) — 晨报绩效追踪",
        f"{'═' * 56}",
        f"",
        f"  总晨报数: {summary.total_briefs}",
    ]

    if summary.briefs_with_1d > 0:
        lines.append(f"  1日命中率: {summary.hit_rate_1d:.1f}% ({summary.briefs_with_1d}条)")
        lines.append(f"  1日平均收益: {summary.avg_return_1d:+.2f}%")
    if summary.briefs_with_5d > 0:
        lines.append(f"  5日命中率: {summary.hit_rate_5d:.1f}% ({summary.briefs_with_5d}条)")
        lines.append(f"  5日平均收益: {summary.avg_return_5d:+.2f}%")

    if not summary.details:
        lines.append(f"\n  暂无足够数据（需要至少2天的晨报+行情数据）")
    else:
        lines.append(f"\n  ── 近期记录 ──")
        for bp in summary.details[-10:]:
            r1 = f"{bp.return_1d:+.2f}%" if bp.return_1d is not None else "  N/A"
            r5 = f"{bp.return_5d:+.2f}%" if bp.return_5d is not None else "  N/A"
            mark1 = "✓" if bp.correct_1d else ("✗" if bp.correct_1d is not None else " ")
            mark5 = "✓" if bp.correct_5d else ("✗" if bp.correct_5d is not None else " ")
            action_short = bp.action.split("(")[0].strip()[:4]
            lines.append(
                f"  {bp.brief_date}  {action_short:<5} ¥{bp.price_at_brief:>7.2f}"
                f"  1d:{mark1}{r1:>7}  5d:{mark5}{r5:>7}"
            )

    lines.append(f"{'═' * 56}")
    return "\n".join(lines)
