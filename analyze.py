#!/usr/bin/env python3
"""华友钴业 (603799) AI Analyst — Daily Morning Brief

Usage:
    python analyze.py                # Full pipeline: fetch + analyze + backtest + brief
    python analyze.py --fetch-only   # Just update data, no analysis
    python analyze.py --backtest     # Run backtests only, show results
    python analyze.py --monitor      # Start real-time T+0 price monitor + WeChat push
    python analyze.py --test-push    # Test WeChat push notification
    python analyze.py --performance  # Show recommendation performance tracker
"""

import argparse
import logging

import config
from data.store import (
    get_connection, init_db, save_ohlcv, load_ohlcv,
    save_indicators, load_indicators, save_agent_run, save_brief,
    load_position, save_position, record_t0_trade,
)
from data.fetcher import fetch_incremental
from data.indicators import compute_all
from agents.technical import analyze as analyze_technical
from agents.fundamental import analyze as analyze_fundamental
from agents.strategist import synthesize, classify_regime, match_historical_regime
from agents.t0_advisor import advise as advise_t0
from data.fundamental import fetch_fundamentals
from data.catalysts import fetch_catalysts
from data.news import fetch_news
from data.taoguba import fetch_expert_posts
from brief_html import save_html_brief
from backtest.engine import run_all_strategies
from backtest.t0_backtest import run_t0_backtest, format_t0_backtest

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


def _run_analysis(conn, backtest_results: list) -> dict | None:
    """Run all agents and synthesize brief. Returns brief dict or None."""
    ohlcv = load_ohlcv(conn)
    indicators = load_indicators(conn)

    if ohlcv.empty or indicators.empty:
        logger.error("No data available for analysis.")
        return None

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

    logger.info("Fetching catalysts & commodity prices...")
    try:
        catalysts = fetch_catalysts()
        if catalysts.lme_nickel_usd:
            logger.info("LME Nickel: $%.0f/ton", catalysts.lme_nickel_usd)
        for err in catalysts.fetch_errors:
            logger.warning("Catalyst fetch issue: %s", err)
    except Exception as e:
        logger.warning("Catalyst fetch failed (non-fatal): %s", e)
        catalysts = None

    logger.info("Fetching news sentiment...")
    try:
        news_sentiment = fetch_news()
        if news_sentiment.items:
            logger.info("News: %d items, sentiment %.2f (%d bull / %d bear)",
                        len(news_sentiment.items), news_sentiment.overall_score,
                        news_sentiment.bullish_count, news_sentiment.bearish_count)
        if news_sentiment.fetch_error:
            logger.warning("News fetch issue: %s", news_sentiment.fetch_error)
    except Exception as e:
        logger.warning("News fetch failed (non-fatal): %s", e)
        news_sentiment = None

    logger.info("Fetching expert opinions (淘股吧)...")
    expert_snapshot = None
    try:
        tgb_cfg = config.get_taoguba_config()
        if tgb_cfg["enabled"] and tgb_cfg["experts"]:
            expert_snapshot = fetch_expert_posts(
                experts=tgb_cfg["experts"],
                max_age_days=tgb_cfg["max_post_age_days"],
                request_delay=tgb_cfg["request_delay_seconds"],
                conn=conn,
            )
            if expert_snapshot.posts:
                logger.info("Experts: %d posts, consensus %.2f (%d bull / %d bear)",
                            len(expert_snapshot.posts), expert_snapshot.consensus_score,
                            expert_snapshot.bullish_count, expert_snapshot.bearish_count)
            for err in expert_snapshot.fetch_errors:
                logger.warning("Expert fetch issue: %s", err)
        else:
            logger.info("TaoGuBa tracking disabled or no experts configured")
    except Exception as e:
        logger.warning("Expert fetch failed (non-fatal): %s", e)

    logger.info("Generating T+0 advice...")
    position = load_position(conn)
    t0_advice = None
    if position:
        latest_ind = indicators.iloc[-1]
        t0_advice = advise_t0(
            position=position,
            latest_price=latest_price,
            atr=float(latest_ind.get("atr14", 0)),
            support=tech_result.details.get("support", 0),
            resistance=tech_result.details.get("resistance", 0),
            boll_upper=float(latest_ind.get("boll_upper", 0)),
            boll_mid=float(latest_ind.get("boll_mid", 0)),
            boll_lower=float(latest_ind.get("boll_lower", 0)),
            tech_score=tech_result.score,
            regime=regime,
        )
        logger.info("T+0: %s, lot=%d", t0_advice.strategy, t0_advice.t0_lot)
    else:
        logger.info("No position found, skipping T+0 advice")

    logger.info("Synthesizing morning brief...")
    brief = synthesize(
        agent_results=agent_results,
        backtest_results=backtest_results,
        regime_match=regime_match,
        current_regime=regime,
        latest_price=latest_price,
        catalysts=catalysts,
        t0_advice=t0_advice,
        news_sentiment=news_sentiment,
        expert_snapshot=expert_snapshot,
    )

    save_brief(
        conn, brief["date"], brief["action"],
        brief["confidence"], brief["risk_level"],
        brief["brief_text"], brief,
    )
    return brief


def step_analyze(conn, backtest_results: list, *, open_html: bool = False) -> None:
    """Run analysis pipeline and print morning brief."""
    brief = _run_analysis(conn, backtest_results)
    if brief is None:
        return

    print(brief["brief_text"])

    if brief["grounding_violations"]:
        print(f"\n⚠ Grounding violations: {len(brief['grounding_violations'])}")
        for v in brief["grounding_violations"]:
            print(f"  - {v}")

    if open_html:
        import subprocess
        html_path = save_html_brief(brief)
        logger.info("HTML brief saved: %s", html_path)
        subprocess.run(["open", str(html_path)], check=False)


def main():
    parser = argparse.ArgumentParser(description="华友钴业 AI Analyst")
    parser.add_argument("--fetch-only", action="store_true", help="Only fetch data")
    parser.add_argument("--backtest", action="store_true", help="Run backtests only")
    parser.add_argument("--no-fetch", action="store_true", help="Skip data fetch")
    parser.add_argument("--set-position", nargs=2, metavar=("QUANTITY", "COST"),
                        help="Record position, e.g. --set-position 1000 65.3")
    parser.add_argument("--backtest-t0", action="store_true",
                        help="Run T+0 strategy backtest on historical data")
    parser.add_argument("--t0-done", nargs=3, metavar=("SELL_PRICE", "BUY_PRICE", "QTY"),
                        help="Record a completed T+0 trade, e.g. --t0-done 62.5 60.0 200")
    parser.add_argument("--monitor", action="store_true",
                        help="Start real-time T+0 price monitor with WeChat push")
    parser.add_argument("--test-push", action="store_true",
                        help="Send a test notification to WeChat via Server酱")
    parser.add_argument("--push-brief", action="store_true",
                        help="Run full pipeline then push morning brief to WeChat")
    parser.add_argument("--performance", action="store_true",
                        help="Show recommendation performance (brief vs actual returns)")
    parser.add_argument("--simulate", type=float, default=0, metavar="CAPITAL",
                        help="Run full portfolio simulation (e.g. --simulate 100000)")
    parser.add_argument("--experts", action="store_true",
                        help="Show TaoGuBa expert opinions standalone")
    parser.add_argument("--html", action="store_true",
                        help="Generate HTML brief and open in browser")
    args = parser.parse_args()

    if args.experts:
        tgb_cfg = config.get_taoguba_config()
        if not tgb_cfg["enabled"] or not tgb_cfg["experts"]:
            print("淘股吧追踪未启用或未配置专家。请编辑 config.yaml 中的 taoguba 部分。")
            return
        conn = get_connection()
        init_db(conn)
        snapshot = fetch_expert_posts(
            experts=tgb_cfg["experts"],
            max_age_days=tgb_cfg["max_post_age_days"],
            request_delay=tgb_cfg["request_delay_seconds"],
            conn=conn,
        )
        conn.close()
        if not snapshot.posts:
            print(f"近{tgb_cfg['max_post_age_days']}日无相关发帖")
            if snapshot.fetch_errors:
                for err in snapshot.fetch_errors:
                    print(f"  ⚠ {err}")
            return
        total = snapshot.bullish_count + snapshot.bearish_count + snapshot.neutral_count
        print(f"\n{'─' * 56}")
        print(f"  淘股吧大神观点 — 603799 华友钴业")
        print(f"{'─' * 56}")
        print(f"  共{len(snapshot.posts)}条帖子, 看多{snapshot.bullish_count} / "
              f"看空{snapshot.bearish_count} / 中性{snapshot.neutral_count}")
        print(f"  综合评分: {snapshot.consensus_score:+.2f}\n")
        for p in snapshot.posts:
            icon = "🟢" if p.sentiment_label == "看多" else ("🔴" if p.sentiment_label == "看空" else "⚪")
            print(f"  {icon} [{p.expert_name}] {p.publish_time}")
            print(f"     {p.title[:50]}")
            if p.signals.actions:
                print(f"     动作: {', '.join(p.signals.actions)}")
            if p.signals.price_targets:
                targets = [f"{k}¥{v:.0f}" for k, v in p.signals.price_targets.items()]
                print(f"     价位: {', '.join(targets)}")
            print()
        print(f"{'─' * 56}")
        return

    if args.simulate > 0:
        from backtest.simulation import run_simulation, format_simulation
        conn = get_connection()
        init_db(conn)
        if not args.no_fetch:
            step_fetch(conn)
            step_indicators(conn)
        ohlcv = load_ohlcv(conn)
        indicators = load_indicators(conn)
        conn.close()
        result = run_simulation(ohlcv, indicators, capital=args.simulate)
        print(format_simulation(result))
        return

    if args.performance:
        from data.performance import compute_performance, format_performance
        conn = get_connection()
        init_db(conn)
        if not args.no_fetch:
            step_fetch(conn)
            step_indicators(conn)
        summary = compute_performance(conn)
        print(format_performance(summary))
        conn.close()
        return

    if args.test_push:
        from monitor import test_push
        test_push()
        return

    if args.monitor:
        from monitor import run_monitor
        run_monitor()
        return

    if args.push_brief:
        conn = get_connection()
        init_db(conn)
        step_fetch(conn)
        step_indicators(conn)
        bt_results = step_backtest(conn)
        brief = _run_analysis(conn, bt_results)
        conn.close()
        if brief:
            from monitor import send_wechat
            title = f"📈 {config.TICKER_NAME}晨报"
            body = brief["brief_text"].replace("═", "-").replace("──", "--")
            result = send_wechat(title, body)
            if result.success:
                print("✓ 晨报已推送到微信")
            else:
                print(f"✗ 推送失败: {result.message}")
        return

    conn = get_connection()
    init_db(conn)

    if args.t0_done:
        sell_p, buy_p, qty = float(args.t0_done[0]), float(args.t0_done[1]), int(args.t0_done[2])
        result = record_t0_trade(conn, sell_p, buy_p, qty)
        print(f"✓ T+0交易已记录:")
        print(f"  卖出 {qty}股 @ ¥{sell_p:.2f} → 买回 @ ¥{buy_p:.2f}")
        print(f"  毛利: ¥{result['profit'] + result['fee']:.2f}  手续费: ¥{result['fee']:.2f}  净利: ¥{result['profit']:.2f}")
        print(f"  持仓成本: ¥{result['cost_before']:.4f} → ¥{result['cost_after']:.4f}")
        conn.close()
        return

    if args.set_position:
        qty, cost = int(args.set_position[0]), float(args.set_position[1])
        save_position(conn, config.TICKER, cost, qty)
        logger.info("Position saved: %d shares @ ¥%.2f", qty, cost)
        pos = load_position(conn)
        print(f"✓ 持仓已记录: {config.TICKER} {config.TICKER_NAME} — {pos['quantity']}股 @ ¥{pos['cost']:.2f}")
        conn.close()
        return

    if args.backtest_t0:
        if not args.no_fetch:
            step_fetch(conn)
            step_indicators(conn)
        ohlcv = load_ohlcv(conn)
        if ohlcv.empty:
            logger.error("No data for T+0 backtest.")
            conn.close()
            return
        pos = load_position(conn)
        qty = pos["quantity"] if pos else 1000
        cost = pos["cost"] if pos else 65.3
        logger.info("Running T+0 backtest (position: %d shares @ ¥%.2f)...", qty, cost)
        result = run_t0_backtest(ohlcv, position_qty=qty, position_cost=cost)
        print(format_t0_backtest(result))
        conn.close()
        return

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
    step_analyze(conn, bt_results, open_html=args.html)

    conn.close()


if __name__ == "__main__":
    main()
