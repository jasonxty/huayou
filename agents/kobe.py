"""Buffett — learning engine, journal generation, and calibration.

Buffett is the system's AI trading persona. It starts with default rule-based
weights and gradually adjusts them based on historical brief accuracy.

No LLM calls — all learning is statistical parameter tuning.

标签定义：混合 1日/5日 收益，可选扣除双边手续费；与日记、校准、学习共用。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

import config
from data.store import (
    load_briefs_for_learning,
    load_kobe_journal,
    load_kobe_weights,
    save_kobe_calibration,
    save_kobe_journal,
    save_kobe_monthly,
    save_kobe_weights,
)

logger = logging.getLogger(__name__)


def get_active_weights(conn) -> dict:
    """Return Buffett's current strategy weights (learned or default)."""
    stored = load_kobe_weights(conn)
    merged = dict(config.KOBE_DEFAULT_WEIGHTS)
    if stored:
        merged.update(stored)
    for rk in ("regime_buy_adjust", "regime_sell_adjust"):
        if rk not in merged or merged[rk] is None:
            merged[rk] = {}
        elif not isinstance(merged[rk], dict):
            merged[rk] = {}
    return merged


def blended_return(ret_1d: float | None, ret_5d: float | None) -> float | None:
    """混合持有期收益；若无 5 日数据则退回 1 日。"""
    if ret_1d is None:
        return None
    if ret_5d is None:
        return ret_1d
    w1 = config.KOBE_LABEL_WEIGHT_1D
    w2 = config.KOBE_LABEL_WEIGHT_5D
    return w1 * ret_1d + w2 * ret_5d


def _friction_frac() -> float:
    if not config.KOBE_USE_FRICTION_IN_LABEL:
        return 0.0
    return config.estimate_round_trip_friction_fraction()


def _classify_brief_outcome(
    action: str,
    ret_1d: float | None,
    ret_5d: float | None | object = None,
    close_today: float | None = None,
) -> str:
    """根据混合收益（及手续费）判定 brief 是否正确。

    HOLD: 窄幅震荡为 correct；中等波动 neutral；大波动为 wrong。
    """
    blended = blended_return(ret_1d, ret_5d if isinstance(ret_5d, (int, float)) else None)
    if blended is None:
        return "pending"

    friction = _friction_frac()
    action_upper = action.upper() if action else ""

    if "BUY" in action_upper:
        net = blended - friction
        return "correct" if net > 0 else "wrong"

    if "SELL" in action_upper:
        net = blended + friction
        return "correct" if net < 0 else "wrong"

    ab = abs(blended)
    if ab < config.KOBE_LABEL_HOLD_FLAT:
        return "correct"
    if ab < config.KOBE_LABEL_HOLD_NEUTRAL_BELOW:
        return "neutral"
    return "wrong"


def _generate_reflection(
    action: str,
    confidence: float,
    ret_1d: float | None,
    ret_5d: float | None,
    outcome: str,
) -> str:
    """Generate Buffett's journal reflection for a trading day (rule-based)."""
    action_upper = (action or "").upper()
    ret_1d_pct = f"{ret_1d * 100:+.2f}%" if ret_1d is not None else "N/A"
    ret_5d_pct = f"{ret_5d * 100:+.2f}%" if ret_5d is not None else "pending"

    if outcome == "correct":
        if "BUY" in action_upper:
            if ret_1d and ret_1d > 0.03:
                return (
                    f"Called BUY at {confidence*100:.0f}% confidence. "
                    f"Price moved {ret_1d_pct} next day — strong hit. "
                    f"5d return: {ret_5d_pct}. The signals were clear."
                )
            return (
                f"BUY call was right — {ret_1d_pct} next day (blended label). "
                f"Confidence was {confidence*100:.0f}%, and the market agreed."
            )
        elif "SELL" in action_upper:
            return (
                f"SELL signal validated — price dropped {ret_1d_pct}. "
                f"Risk management paid off. 5d: {ret_5d_pct}."
            )
        else:
            return (
                f"HOLD was the right call — market moved only {ret_1d_pct}. "
                f"Patience over action."
            )
    elif outcome == "wrong":
        if "BUY" in action_upper:
            return (
                f"BUY call missed — price went {ret_1d_pct} instead. "
                f"Confidence was {confidence*100:.0f}%. "
                f"Need to weight the bearish signals more heavily next time."
            )
        elif "SELL" in action_upper:
            return (
                f"SELL signal was premature — price moved {ret_1d_pct} (up). "
                f"May have been too aggressive reading the technicals."
            )
        else:
            return (
                f"HOLD while the market moved {ret_1d_pct} — missed an opportunity. "
                f"Should have been more decisive."
            )
    else:
        return (
            f"Market moved {ret_1d_pct} on a HOLD day. "
            f"Inconclusive — staying the course."
        )


def _row_ret_1d(b: dict) -> float | None:
    if b["close_today"] is None or b["close_today"] <= 0 or b["close_1d"] is None:
        return None
    return (b["close_1d"] - b["close_today"]) / b["close_today"]


def _row_ret_5d(b: dict) -> float | None:
    if b["close_today"] is None or b["close_today"] <= 0 or b["close_5d"] is None:
        return None
    return (b["close_5d"] - b["close_today"]) / b["close_today"]


def run_journal_update(conn) -> int:
    """Generate journal entries for briefs that have forward returns."""
    briefs = load_briefs_for_learning(conn)
    existing = {j["entry_date"] for j in load_kobe_journal(conn, limit=9999)}
    count = 0

    for b in briefs:
        if b["date"] in existing:
            continue
        if b["close_today"] is None or b["close_today"] <= 0:
            continue

        ret_1d = _row_ret_1d(b)
        ret_5d = _row_ret_5d(b)

        outcome = _classify_brief_outcome(
            b["action"], ret_1d, ret_5d, float(b["close_today"]),
        )
        if outcome == "pending":
            continue

        reflection = _generate_reflection(
            b["action"], b["confidence"], ret_1d, ret_5d, outcome
        )
        save_kobe_journal(
            conn,
            b["date"],
            b["action"],
            ret_1d,
            ret_5d,
            outcome,
            reflection,
        )
        count += 1

    if count:
        logger.info("Buffett journal: wrote %d new entries", count)
    return count


def _learning_step(base: float) -> float:
    return base * config.KOBE_LEARNING_STEP_SCALE


def _clamp_regime_adj(d: dict[str, int]) -> dict[str, int]:
    cap = config.KOBE_REGIME_ADJUST_CAP
    out = {}
    for k, v in d.items():
        out[k] = int(max(-cap, min(cap, v)))
    return out


def run_learning(conn) -> dict | None:
    """Analyze historical accuracy and adjust Buffett's weights."""
    briefs = load_briefs_for_learning(conn)
    evaluated = []
    for b in briefs:
        if b["close_1d"] is None or not b["close_today"]:
            continue
        evaluated.append(b)

    if len(evaluated) < config.KOBE_MIN_SAMPLES_FOR_LEARNING:
        logger.info(
            "Buffett learning: only %d samples, need %d",
            len(evaluated),
            config.KOBE_MIN_SAMPLES_FOR_LEARNING,
        )
        return None

    current = get_active_weights(conn)
    bounds = config.KOBE_WEIGHT_BOUNDS
    adj_buy = dict(current.get("regime_buy_adjust") or {})
    adj_sell = dict(current.get("regime_sell_adjust") or {})

    buy_calls = [b for b in evaluated if "BUY" in (b["action"] or "").upper()]
    sell_calls = [b for b in evaluated if "SELL" in (b["action"] or "").upper()]

    adjustments = {}
    reasons = []

    def outcome_row(b: dict) -> str:
        r1 = _row_ret_1d(b)
        r5 = _row_ret_5d(b)
        return _classify_brief_outcome(
            b["action"], r1, r5,
            float(b["close_today"]) if b["close_today"] else None,
        )

    if len(buy_calls) >= 3:
        buy_wins = sum(1 for b in buy_calls if outcome_row(b) == "correct")
        buy_wr = buy_wins / len(buy_calls)
        step = max(1, int(round(_learning_step(3))))
        if buy_wr < 0.4:
            new_val = min(
                current["tech_buy_threshold"] + step,
                bounds["tech_buy_threshold"][1],
            )
            adjustments["tech_buy_threshold"] = new_val
            reasons.append(f"BUY win rate {buy_wr:.0%} low, raising threshold (+{step})")
        elif buy_wr > 0.7:
            step_dn = max(1, int(round(_learning_step(2))))
            new_val = max(
                current["tech_buy_threshold"] - step_dn,
                bounds["tech_buy_threshold"][0],
            )
            adjustments["tech_buy_threshold"] = new_val
            reasons.append(f"BUY win rate {buy_wr:.0%} strong, lowering threshold (-{step_dn})")

    if len(sell_calls) >= 3:
        sell_wins = sum(1 for b in sell_calls if outcome_row(b) == "correct")
        sell_wr = sell_wins / len(sell_calls)
        step = max(1, int(round(_learning_step(3))))
        if sell_wr < 0.4:
            new_val = max(
                current["tech_sell_threshold"] - step,
                bounds["tech_sell_threshold"][0],
            )
            adjustments["tech_sell_threshold"] = new_val
            reasons.append(f"SELL win rate {sell_wr:.0%} low, easing sell threshold")
        elif sell_wr > 0.7:
            step_up = max(1, int(round(_learning_step(2))))
            new_val = min(
                current["tech_sell_threshold"] + step_up,
                bounds["tech_sell_threshold"][1],
            )
            adjustments["tech_sell_threshold"] = new_val
            reasons.append(f"SELL win rate {sell_wr:.0%} strong, tightening sell threshold")

    overall_wins = sum(1 for b in evaluated if outcome_row(b) == "correct")
    overall_wr = overall_wins / len(evaluated)

    rt_step = max(0.03, round(_learning_step(0.1), 2))
    if overall_wr < 0.45:
        new_rt = max(current["regime_trust"] - rt_step, bounds["regime_trust"][0])
        adjustments["regime_trust"] = round(new_rt, 2)
        reasons.append(f"Overall win rate {overall_wr:.0%}, reducing regime trust")
    elif overall_wr > 0.65:
        new_rt = min(current["regime_trust"] + rt_step * 0.5, bounds["regime_trust"][1])
        adjustments["regime_trust"] = round(new_rt, 2)
        reasons.append(f"Overall win rate {overall_wr:.0%}, increasing regime trust")

    ea_step = round(_learning_step(0.015), 4)
    if overall_wr < 0.43:
        new_ea = max(
            current["expert_adjustment"] - ea_step,
            bounds["expert_adjustment"][0],
        )
        adjustments["expert_adjustment"] = round(new_ea, 4)
        reasons.append("Overall weak: taper expert bump magnitude")
    elif overall_wr > 0.62:
        new_ea = min(
            current["expert_adjustment"] + ea_step * 0.6,
            bounds["expert_adjustment"][1],
        )
        adjustments["expert_adjustment"] = round(new_ea, 4)
        reasons.append("Overall solid: slightly stronger expert alignment weight")

    r_step = max(1, int(round(_learning_step(config.KOBE_REGIME_ADJUST_STEP))))
    rg_min = config.KOBE_REGIME_BUCKET_MIN_ACTIONS
    rg_need = config.KOBE_REGIME_MIN_SAMPLES

    buy_by_reg: dict[str, list] = defaultdict(list)
    for b in buy_calls:
        buy_by_reg[b.get("regime_key") or "unknown|unknown"].append(b)

    for rk, lst in buy_by_reg.items():
        if len(lst) < max(rg_min, 3):
            continue
        wins = sum(1 for b in lst if outcome_row(b) == "correct")
        wr = wins / len(lst)
        if len(lst) >= rg_need and wr < 0.4:
            adj_buy[rk] = adj_buy.get(rk, 0) + r_step
            reasons.append(f"[{rk}] BUY WR {wr:.0%} → stricter buy +{r_step}")
        elif len(lst) >= rg_need and wr > 0.72:
            adj_buy[rk] = adj_buy.get(rk, 0) - r_step
            reasons.append(f"[{rk}] BUY WR {wr:.0%} → relax buy -{r_step}")

    sell_by_reg: dict[str, list] = defaultdict(list)
    for b in sell_calls:
        sell_by_reg[b.get("regime_key") or "unknown|unknown"].append(b)

    for rk, lst in sell_by_reg.items():
        if len(lst) < max(rg_min, 3):
            continue
        wins = sum(1 for b in lst if outcome_row(b) == "correct")
        wr = wins / len(lst)
        if len(lst) >= rg_need and wr < 0.4:
            adj_sell[rk] = adj_sell.get(rk, 0) + r_step
            reasons.append(f"[{rk}] SELL WR {wr:.0%} → easier sell +{r_step}")
        elif len(lst) >= rg_need and wr > 0.72:
            adj_sell[rk] = adj_sell.get(rk, 0) - r_step
            reasons.append(f"[{rk}] SELL WR {wr:.0%} → tighter sell -{r_step}")

    adj_buy_f = _clamp_regime_adj(adj_buy)
    adj_sell_f = _clamp_regime_adj(adj_sell)

    new_weights = dict(current)
    new_weights.update(adjustments)
    new_weights["regime_buy_adjust"] = adj_buy_f
    new_weights["regime_sell_adjust"] = adj_sell_f

    def _dict_same(a: dict, b: dict) -> bool:
        return dict(a or {}) == dict(b or {})

    changed = False
    for key in config.KOBE_DEFAULT_WEIGHTS:
        if key in ("regime_buy_adjust", "regime_sell_adjust"):
            continue
        if new_weights.get(key) != current.get(key):
            changed = True
            break
    if not changed:
        if not _dict_same(adj_buy_f, dict(current.get("regime_buy_adjust") or {})):
            changed = True
        elif not _dict_same(adj_sell_f, dict(current.get("regime_sell_adjust") or {})):
            changed = True

    if not changed:
        logger.info(
            "Buffett learning: no adjustments needed (overall WR %.0f%%)",
            overall_wr * 100,
        )
        return None

    reason_str = "; ".join(reasons) if reasons else "threshold/regime calibration"
    save_kobe_weights(conn, new_weights, reason_str, len(evaluated))
    logger.info("Buffett learning: updated weights — %s", reason_str)
    return new_weights


def run_calibration(conn) -> list[dict]:
    """Compute confidence calibration buckets and persist."""
    briefs = load_briefs_for_learning(conn)
    evaluated = [b for b in briefs if b["close_1d"] is not None and b["close_today"]]

    if len(evaluated) < 5:
        return []

    bucket_ranges = [
        ("50-60%", 0.50, 0.60),
        ("60-70%", 0.60, 0.70),
        ("70-80%", 0.70, 0.80),
        ("80-90%", 0.80, 0.90),
    ]

    results = []
    for label, lo, hi in bucket_ranges:
        in_bucket = [b for b in evaluated if lo <= b["confidence"] < hi]
        if not in_bucket:
            results.append(
                {"bucket": label, "predicted": (lo + hi) / 2, "actual": 0, "count": 0}
            )
            continue

        wins = sum(
            1
            for b in in_bucket
            if _classify_brief_outcome(
                b["action"],
                _row_ret_1d(b),
                _row_ret_5d(b),
                float(b["close_today"]) if b["close_today"] else None,
            )
            == "correct"
        )
        actual_wr = wins / len(in_bucket)
        results.append(
            {
                "bucket": label,
                "predicted": round((lo + hi) / 2, 2),
                "actual": round(actual_wr, 3),
                "count": len(in_bucket),
            }
        )

    today = date.today().isoformat()
    save_kobe_calibration(conn, today, results)
    return results


def run_monthly_report(conn, month: str | None = None) -> dict | None:
    """Generate a monthly match report for Buffett vs user."""
    if month is None:
        today = date.today()
        if today.month == 1:
            month = f"{today.year - 1}-12"
        else:
            month = f"{today.year}-{today.month - 1:02d}"

    journal = load_kobe_journal(conn, limit=9999)
    month_entries = [j for j in journal if j["entry_date"].startswith(month)]

    if not month_entries:
        return None

    kobe_wins = sum(1 for j in month_entries if j["outcome"] == "correct")
    kobe_losses = sum(1 for j in month_entries if j["outcome"] == "wrong")
    kobe_neutral = len(month_entries) - kobe_wins - kobe_losses

    best_day = max(
        month_entries,
        key=lambda j: j["actual_return_1d"] or 0,
        default=None,
    )
    worst_day = min(
        month_entries,
        key=lambda j: j["actual_return_1d"] or 0,
        default=None,
    )

    weights = get_active_weights(conn)

    report = {
        "month": month,
        "kobe_wins": kobe_wins,
        "kobe_losses": kobe_losses,
        "kobe_neutral": kobe_neutral,
        "kobe_win_rate": round(kobe_wins / max(kobe_wins + kobe_losses, 1) * 100, 1),
        "total_briefs": len(month_entries),
        "best_day": {
            "date": best_day["entry_date"] if best_day else "N/A",
            "return": round((best_day["actual_return_1d"] or 0) * 100, 2)
            if best_day
            else 0,
        },
        "worst_day": {
            "date": worst_day["entry_date"] if worst_day else "N/A",
            "return": round((worst_day["actual_return_1d"] or 0) * 100, 2)
            if worst_day
            else 0,
        },
        "active_weights": weights,
    }

    save_kobe_monthly(conn, month, report)
    logger.info(
        "Buffett monthly report for %s: W%d L%d (%.1f%%)",
        month,
        kobe_wins,
        kobe_losses,
        report["kobe_win_rate"],
    )
    return report
