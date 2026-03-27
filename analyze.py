#!/usr/bin/env python3
"""华友钴业 (603799) AI Analyst — Daily Morning Brief

Usage:
    python analyze.py              # Full pipeline: fetch + analyze + backtest + brief
    python analyze.py --fetch-only # Just update data, no analysis
    python analyze.py --backtest   # Run backtests only, show results
"""

import argparse
import logging

import config
from data.store import (
    get_connection, init_db, save_ohlcv, load_ohlcv,
    save_indicators, load_indicators, save_agent_run, save_brief,
)
from data.fetcher import fetch_incremental
from data.indicators import compute_all
from agents.technical import analyze as analyze_technical
from agents.fundamental import analyze as analyze_fundamental
from agents.strategist import synthesize, classify_regime, match_historical_regime
from data.fundamental import fetch_fundamentals
from backtest.engine import run_all_strategies

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("huayou")


def step_fetch(conn) -> bool:
    """Fetch latest data for 603799. Returns True if new data was added."""
    logger.info("Fetching data for %s %s...", config.TICKER, config.TICKER_NAME)
    try:
        df = fetch_incremental(conn)
        if df.empty:
            logger.info("No new data to fetch.")
            return False
        new_rows = save_ohlcv(conn, df)
        logger.info("Added %d new rows.", new_rows)
        return True
    except Exception as e:
        logger.error("Fetch failed: %s", e)
        return False


def step_indicators(conn) -> None:
    """Recompute all indicators from stored OHLCV."""
    logger.info("Computing indicators...")
    ohlcv = load_ohlcv(conn)
    if ohlcv.empty:
        logger.error("No OHLCV data. Run fetch first.")
        return
    ind = compute_all(ohlcv)
    save_indicators(conn, ind)
    logger.info("Indicators computed for %d rows.", len(ind))


def step_backtest(conn) -> list:
    """Run all backtesting strategies."""
    logger.info("Running backtests...")
    ohlcv = load_ohlcv(conn)
    if ohlcv.empty:
        logger.error("No data for backtesting.")
        return []
    results = run_all_strategies(ohlcv)

    print(f"\n{'─' * 60}")
    print(f"  BACKTEST RESULTS — {config.TICKER} {config.TICKER_NAME}")
    print(f"{'─' * 60}")
    for r in results:
        status = "✓ PASS" if r.passes_threshold else "✗ FAIL"
        print(f"  {status}  {r.strategy:<20} win={r.win_rate*100:5.1f}%  "
              f"sharpe={r.sharpe:5.2f}  dd={r.max_drawdown*100:5.1f}%  "
              f"trades={r.total_trades}")
    print(f"{'─' * 60}\n")

    return results


def step_analyze(conn, backtest_results: list) -> None:
    """Run Technical Analyst + Chief Strategist and print morning brief."""
    ohlcv = load_ohlcv(conn)
    indicators = load_indicators(conn)

    if ohlcv.empty or indicators.empty:
        logger.error("No data available for analysis.")
        return

    latest_price = float(ohlcv.iloc[-1]["close"])
    agent_results = []

    logger.info("Running Technical Analyst...")
    tech_result = analyze_technical(ohlcv, indicators)
    save_agent_run(
        conn, str(ohlcv.iloc[-1]["date"]),
        tech_result.agent_name, tech_result.score, tech_result.to_dict(),
    )
    agent_results.append(tech_result)
    logger.info("Technical score: %+.0f", tech_result.score)

    logger.info("Running Fundamental Analyst...")
    try:
        snap = fetch_fundamentals(current_price=latest_price)
        fund_result = analyze_fundamental(snap)
        save_agent_run(
            conn, str(ohlcv.iloc[-1]["date"]),
            fund_result.agent_name, fund_result.score, fund_result.to_dict(),
        )
        agent_results.append(fund_result)
        logger.info("Fundamental score: %+.0f", fund_result.score)
    except Exception as e:
        logger.warning("Fundamental analysis failed (non-fatal): %s", e)

    logger.info("Classifying market regime...")
    regime = classify_regime(indicators)
    regime_match = match_historical_regime(indicators, ohlcv, regime)
    logger.info("Regime: %s, matches: %d", regime, regime_match["count"])

    logger.info("Synthesizing morning brief...")
    brief = synthesize(
        agent_results=agent_results,
        backtest_results=backtest_results,
        regime_match=regime_match,
        current_regime=regime,
        latest_price=latest_price,
    )

    save_brief(
        conn, brief["date"], brief["action"],
        brief["confidence"], brief["risk_level"],
        brief["brief_text"], brief,
    )

    print(brief["brief_text"])

    if brief["grounding_violations"]:
        print(f"\n⚠ Grounding violations: {len(brief['grounding_violations'])}")
        for v in brief["grounding_violations"]:
            print(f"  - {v}")


def main():
    parser = argparse.ArgumentParser(description="华友钴业 AI Analyst")
    parser.add_argument("--fetch-only", action="store_true", help="Only fetch data")
    parser.add_argument("--backtest", action="store_true", help="Run backtests only")
    parser.add_argument("--no-fetch", action="store_true", help="Skip data fetch")
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)

    if args.fetch_only:
        step_fetch(conn)
        step_indicators(conn)
        conn.close()
        return

    if args.backtest:
        if not args.no_fetch:
            step_fetch(conn)
            step_indicators(conn)
        step_backtest(conn)
        conn.close()
        return

    if not args.no_fetch:
        step_fetch(conn)
        step_indicators(conn)

    bt_results = step_backtest(conn)
    step_analyze(conn, bt_results)

    conn.close()


if __name__ == "__main__":
    main()
