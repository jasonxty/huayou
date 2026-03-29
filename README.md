# 华友钴业 (603799) AI Analyst

Rule-based single-stock trading intelligence for 603799 华友钴业. Fetches real market data, computes 20 technical indicators, runs fundamental analysis, tracks commodity catalysts, generates T+0 intraday trading advice, and produces a daily morning brief — all locally, no API keys needed.

Real-time price monitoring with automatic WeChat push notifications when T+0 thresholds are hit.

## Quick Start

```bash
# Python 3.12+ recommended
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run full pipeline (~5 seconds)
python analyze.py

# Record your position
python analyze.py --set-position 1000 65.3

# Start real-time monitoring with WeChat alerts
python analyze.py --monitor
```

## Commands

```bash
# Analysis
python analyze.py                  # Full pipeline: fetch → analyze → backtest → brief
python analyze.py --fetch-only     # Update data only
python analyze.py --backtest       # Run backtests only
python analyze.py --no-fetch       # Skip fetch, use cached data
python analyze.py --push-brief     # Run pipeline + push brief to WeChat

# Position management
python analyze.py --set-position 1000 65.3    # Record holding
python analyze.py --t0-done 62.5 60.0 200     # Record T+0 trade, auto-update cost

# Monitoring
python analyze.py --monitor        # Start real-time T+0 monitor
python analyze.py --test-push      # Test WeChat push
python monitor.py --once           # Check once and exit

# Backtesting
python analyze.py --backtest       # Run 5 strategies with walk-forward
python analyze.py --backtest-t0    # T+0 strategy historical validation
```

## Architecture

```
huayou-analyst/
├── analyze.py              # CLI entry point — orchestrates the full pipeline
├── monitor.py              # Real-time T+0 price monitor + WeChat push
├── config.py               # Ticker, thresholds, Server酱 config
├── data/
│   ├── fetcher.py          # AKShare data fetch with retry + incremental updates
│   ├── store.py            # SQLite schema + CRUD (OHLCV, positions, T+0 trades)
│   ├── indicators.py       # 20 technical indicators via pandas-ta
│   ├── fundamental.py      # Fundamental data (financials, valuation, margins)
│   ├── catalysts.py        # LME nickel price + catalyst event calendar
│   └── holidays.py         # A-share holiday calendar (2026)
├── agents/
│   ├── base.py             # AgentResult dataclass
│   ├── technical.py        # Technical scoring (MA, MACD, RSI, KDJ, Bollinger, volume)
│   ├── fundamental.py      # Fundamental scoring (growth, margins, valuation, cycle)
│   ├── t0_advisor.py       # T+0 intraday advisor (zones, split-sell, escape plan)
│   └── strategist.py       # Regime matching, brief synthesis, grounding validator
├── backtest/
│   ├── engine.py           # 5 strategies + walk-forward validation
│   └── t0_backtest.py      # T+0 strategy backtester
├── scripts/
│   └── start-monitor.sh    # Auto-start script for launchd
├── com.huayou.monitor.plist # macOS launchd config (auto-run Mon-Fri 9:20)
└── tests/                  # 75 tests, <2s
```

## What's in the Morning Brief

| Section | Content |
|---------|---------|
| ACTION | BUY / HOLD / SELL with confidence % and risk level |
| TECHNICAL | 6 sub-scorers: MA alignment, MACD, RSI, KDJ, Bollinger, volume |
| FUNDAMENTAL | PE/PB, margins, ROE, revenue growth, cycle analysis |
| KEY CATALYSTS | LME nickel price, upcoming policy/earnings events |
| T+0 ADVICE | Split-batch sell/buy zones, stop-loss, escape plan |
| REGIME | Historical pattern matching with forward return stats |
| BACKTEST | 5 strategies: MA crossover, MACD, volume breakout, RSI, mean reversion |

## Real-time Monitoring

The monitor checks real-time price every 30 seconds during trading hours and pushes WeChat alerts when price hits T+0 thresholds:

| Alert | Trigger |
|-------|---------|
| 🔴 高抛第1批 | Price reaches sell zone low |
| 🔴 高抛第2批 | Price reaches sell zone high |
| 🟢 低吸触发 | Price drops into buy zone |
| ⛔ 止损触发 | Price breaks below stop-loss |
| ⚡ 突破确认 | Price breaks above resistance |

Daily limit: 5 pushes (Server酱 free plan). Each alert type fires once per day.

### Auto-start (launchd)

The monitor auto-starts Mon-Fri at 9:20 and exits after market close:

```bash
# Already installed — manage with:
launchctl list | grep huayou              # Check status
launchctl unload ~/Library/LaunchAgents/com.huayou.monitor.plist  # Disable
launchctl load ~/Library/LaunchAgents/com.huayou.monitor.plist    # Re-enable
```

Logs: `logs/monitor-YYYYMMDD.log`

## Position Tracking

```bash
# Record position
python analyze.py --set-position 1000 65.3

# After a successful T+0 trade (sell 200 @ 62.5, buy back @ 60.0)
python analyze.py --t0-done 62.5 60.0 200
# Output: cost 65.3000 → 64.8010 (auto-adjusted)
```

## Data Sources

| Source | Data | Refresh |
|--------|------|---------|
| [AKShare](https://github.com/akfamily/akshare) | OHLCV, fundamentals, LME nickel | Daily |
| [Eastmoney](https://push2.eastmoney.com) | Real-time quotes (monitor) | 30s |
| [Server酱](https://sct.ftqq.com) | WeChat push notifications | On trigger |
| SQLite (local) | Indicators, briefs, positions, trades | Computed |

## Tests

```bash
python -m pytest tests/ -v    # 75 tests, <2s
```

## Setup: WeChat Push

1. Visit [sct.ftqq.com](https://sct.ftqq.com), scan with WeChat to get a SendKey
2. Create `config.yaml` in project root:
   ```yaml
   notification:
     serverchan_key: "YOUR_SENDKEY_HERE"
   ```
3. Test: `python analyze.py --test-push`

## Disclaimer

This tool is for personal research and education only. It does not constitute financial advice. Past backtest performance does not guarantee future results. Always do your own due diligence before trading.
