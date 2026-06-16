"""Buffett strategy backtest over N trading days (technical + regime matching + Buffett weights).

Notes:
- Each period uses only OHLCV/indicator slices up to the current day to avoid look-ahead.
- Walk-forward sub-strategy pool is disabled by default; pass include_walk_forward=True to enable (slow).
- Excludes real-time fundamentals/sentiment/commodity data to avoid using current filings for historical dates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
from agents.kobe import _classify_brief_outcome, get_active_weights
from agents.strategist import classify_regime, match_historical_regime, synthesize
from agents.technical import analyze as analyze_technical
from backtest.engine import run_all_strategies
from data.store import get_connection, init_db, load_indicators, load_ohlcv

logger = logging.getLogger(__name__)

SLIPPAGE = 0.002


@dataclass
class DailyRecord:
    date: str
    action: str
    confidence: float
    risk_level: str
    tech_score: float
    outcome_label: str | None  # None when insufficient forward data
    ret_1d: float | None
    ret_5d: float | None


@dataclass
class BuffettBacktestResult:
    start_date: str
    end_date: str
    trading_days: int
    records: list[DailyRecord]
    label_win_rate: float | None
    label_count: int
    strategy_return_pct: float
    buy_hold_return_pct: float
    max_drawdown_pct: float
    sharpe_approx: float
    final_equity: float
    notes: list[str] = field(default_factory=list)


def _forward_returns(
    merged: pd.DataFrame,
    i: int,
) -> tuple[float | None, float | None]:
    """Forward returns: next-day and 5th-day returns relative to day i's close."""
    if i >= len(merged) - 1:
        return None, None
    close_today = float(merged.iloc[i]["close"])
    close_1 = float(merged.iloc[i + 1]["close"])
    ret_1d = (close_1 - close_today) / close_today
    j5 = min(i + 5, len(merged) - 1)
    if j5 <= i:
        return ret_1d, None
    close_5 = float(merged.iloc[j5]["close"])
    ret_5d = (close_5 - close_today) / close_today
    return ret_1d, ret_5d


def run_buffett_backtest(
    conn,
    trading_days: int = 252,
    initial_capital: float = 100_000.0,
    warmup_bars: int = 120,
    include_walk_forward: bool = False,
) -> BuffettBacktestResult:
    """Generate daily Buffett signals for the last `trading_days` and compute label stats + equity curve."""
    ohlcv = load_ohlcv(conn)
    indicators = load_indicators(conn)
    notes: list[str] = [
        "Fundamentals/TaoGuBa/news/commodity disabled (no look-ahead).",
        "Walk-forward sub-strategy disabled by default; tech + regime weights match current Buffett weights.",
    ]

    # Suppress verbose grounding logs during batch backtest
    _log_strategist = logging.getLogger("agents.strategist")
    _prev_log = _log_strategist.level
    _log_strategist.setLevel(logging.ERROR)
    try:
        if ohlcv.empty or indicators.empty:
            raise ValueError("OHLCV or indicators empty — run python analyze.py first to fetch data and compute indicators.")

        merged = ohlcv.merge(indicators, on="date", how="inner").sort_values("date").reset_index(drop=True)
        n = len(merged)
        if n < warmup_bars + 5:
            raise ValueError(f"Insufficient data: need at least {warmup_bars + 5} bars, got {n}.")

        eval_start = max(warmup_bars, n - trading_days)
        weights = get_active_weights(conn)

        records: list[DailyRecord] = []
        equity_curve: list[float] = []

        ocols = {"open", "high", "low", "close", "volume", "turnover", "turnover_rate"}

        for i in range(eval_start, n):
            row = merged.iloc[i]
            date_str = str(row["date"])[:10]
            sub = merged.iloc[: i + 1]
            slice_ohlcv = sub[
                ["date", "open", "high", "low", "close", "volume", "turnover", "turnover_rate"]
            ].copy()
            ind_cols = [c for c in sub.columns if c not in ocols]
            slice_ind = sub[ind_cols].copy()

            tech_result = analyze_technical(slice_ohlcv, slice_ind)
            regime = classify_regime(slice_ind, slice_ohlcv)
            regime_match = match_historical_regime(slice_ind, slice_ohlcv, regime)
            latest_price = float(slice_ohlcv.iloc[-1]["close"])

            bt_results = []
            if include_walk_forward:
                bt_results = run_all_strategies(slice_ohlcv)

            out = synthesize(
                agent_results=[tech_result],
                backtest_results=bt_results,
                regime_match=regime_match,
                current_regime=regime,
                latest_price=latest_price,
                catalysts=None,
                t0_advice=None,
                news_sentiment=None,
                expert_snapshot=None,
                analysis_date=date_str,
                kobe_weights=weights,
                calibration_buckets=None,
            )

            ret_1d, ret_5d = _forward_returns(merged, i)
            close_today = float(row["close"])

            if ret_1d is None:
                outcome = None
            else:
                outcome = _classify_brief_outcome(
                    out["action"],
                    ret_1d,
                    ret_5d,
                    close_today,
                )

            records.append(
                DailyRecord(
                    date=date_str,
                    action=out["action"],
                    confidence=out["confidence"],
                    risk_level=out["risk_level"],
                    tech_score=tech_result.score,
                    outcome_label=outcome,
                    ret_1d=ret_1d,
                    ret_5d=ret_5d,
                )
            )

        # --- Pass 2: T+1 execution (signal day D → fill at D+1 open) ---
        cash = initial_capital
        shares = 0
        for k, rec in enumerate(records):
            day_idx = eval_start + k
            if day_idx >= n - 1:
                break
            next_open = float(merged.iloc[day_idx + 1]["open"])
            act = rec.action.upper()
            if shares == 0 and "BUY" in act:
                lot = 100
                px = next_open * (1 + SLIPPAGE)
                max_sh = int((cash * 0.85) / px / lot) * lot
                notional = max_sh * px
                fee_buy = config.calc_trade_fee(notional, "BUY")
                cost = notional + fee_buy
                if max_sh >= lot and cost <= cash:
                    cash -= cost
                    shares = max_sh
            elif shares > 0 and "SELL" in act:
                px = next_open * (1 - SLIPPAGE)
                gross = shares * px
                fee_sell = config.calc_trade_fee(gross, "SELL")
                cash += gross - fee_sell
                shares = 0

            close_now = float(merged.iloc[day_idx + 1]["close"])
            eq = cash + shares * close_now
            equity_curve.append(eq)

        last_close = float(merged.iloc[-1]["close"])
        final_equity = cash + shares * last_close

        evaluated = [r for r in records if r.outcome_label is not None and r.outcome_label != "pending"]
        wins = sum(1 for r in evaluated if r.outcome_label == "correct")
        label_wr = wins / len(evaluated) if evaluated else None

        start_close = float(merged.iloc[eval_start]["close"])
        bh_ret = (last_close - start_close) / start_close * 100
        strat_ret = (final_equity - initial_capital) / initial_capital * 100

        max_dd = 0.0
        if equity_curve:
            peak_e = initial_capital
            for e in equity_curve:
                peak_e = max(peak_e, e)
                max_dd = max(max_dd, (peak_e - e) / peak_e if peak_e else 0)

        rets = np.diff(equity_curve) / np.array(equity_curve[:-1]) if len(equity_curve) > 1 else []
        sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(252)) if len(rets) > 1 and np.std(rets) > 0 else 0.0

        return BuffettBacktestResult(
            start_date=records[0].date if records else "",
            end_date=records[-1].date if records else "",
            trading_days=len(records),
            records=records,
            label_win_rate=round(label_wr * 100, 2) if label_wr is not None else None,
            label_count=len(evaluated),
            strategy_return_pct=round(strat_ret, 2),
            buy_hold_return_pct=round(bh_ret, 2),
            max_drawdown_pct=round(max_dd * 100, 2),
            sharpe_approx=round(sharpe, 2),
            final_equity=round(final_equity, 2),
            notes=notes,
        )
    finally:
        _log_strategist.setLevel(_prev_log)


def format_buffett_backtest(result: BuffettBacktestResult) -> str:
    lines = [
        "=" * 64,
        "  Buffett Strategy Backtest (Daily Signals)",
        f"  Period: {result.start_date} ~ {result.end_date}  |  Trading Days: {result.trading_days}",
        "=" * 64,
        "",
        "  -- Notes --",
    ]
    for n in result.notes:
        lines.append(f"    - {n}")
    lines.extend(
        [
            "",
            "  -- Label Quality (blended return + fees) --",
            f"  Evaluated Samples: {result.label_count} days",
            f"  Label Win Rate: {result.label_win_rate if result.label_win_rate is not None else 'N/A'}%",
            "",
            "  -- Equity Curve (T+1 open execution, 85% max position, round lots) --",
            f"  Final Equity: ¥{result.final_equity:,.2f}",
            f"  Strategy Return: {result.strategy_return_pct:+.2f}%",
            f"  Buy & Hold: {result.buy_hold_return_pct:+.2f}%",
            f"  Excess Return: {result.strategy_return_pct - result.buy_hold_return_pct:+.2f}%",
            f"  Max Drawdown: {result.max_drawdown_pct:.2f}%",
            f"  Sharpe (daily approx): {result.sharpe_approx:.2f}",
            "",
            "  -- Signal Distribution --",
        ]
    )
    from collections import Counter

    c = Counter(r.action for r in result.records)
    for k, v in c.most_common(12):
        lines.append(f"    {k[:44]:<44} {v:>5}")

    lines.extend(["", "  -- Last 10 Signals --"])
    for r in result.records[-10:]:
        oc = r.outcome_label or "—"
        lines.append(
            f"    {r.date}  {r.action[:28]:<28} conf={r.confidence*100:.0f}%  "
            f"tech={r.tech_score:+.0f}  label={oc}"
        )
    lines.append("═" * 64)
    return "\n".join(lines)


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="Buffett strategy backtest (~252 trading days)")
    p.add_argument("--days", type=int, default=252, help="Number of trading days (default ~1 year)")
    p.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    p.add_argument("--walk-forward", action="store_true", help="Run walk-forward sub-strategies per day (slow)")
    args = p.parse_args()

    conn = get_connection()
    init_db(conn)
    try:
        r = run_buffett_backtest(
            conn,
            trading_days=args.days,
            initial_capital=args.capital,
            include_walk_forward=args.walk_forward,
        )
        print(format_buffett_backtest(r))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
