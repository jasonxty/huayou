# 华友钴业 (603799) AI Analyst

Rule-based single-stock trading intelligence for 603799 华友钴业. Fetches real market data, computes 20 technical indicators, runs 5 backtested strategies with walk-forward validation, and produces a daily morning brief with actionable signals — all locally, no API keys needed.

## Quick Start

```bash
# Python 3.12+ recommended
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run full pipeline (~2 seconds)
python analyze.py
```

## Sample Output

```
════════════════════════════════════════════════════════
  华友钴业 (603799) — 2026-03-27 Morning Brief
════════════════════════════════════════════════════════

  ACTION:     HOLD (震荡观望)
  CONFIDENCE: 50%
  RISK LEVEL: LOW
  PRICE:      59.90  |  ATR(14): 3.12

  ── TECHNICAL SIGNALS (score: -19/100) ──
    • 均线空头排列 (MA5<10<20<60)
    • MACD空头趋缓
    • KDJ金叉
    • 价格低于布林中轨

  ── REGIME (down / neutral) ──
  Similar setup occurred 580 times in 603799's history.
  5-day forward return: +0.4% avg (win rate 52%)

  ── BACKTEST STRATEGIES ──
  ✗ ma_crossover       win= 42.9%  sharpe= 1.74  dd= 20.7%
  ✗ macd_divergence    win= 48.3%  sharpe= 3.31  dd= 35.8%
  ✗ volume_breakout    win= 41.7%  sharpe= 3.24  dd= 21.8%
  ✗ rsi_oversold       win= 52.6%  sharpe= 0.58  dd= 14.8%
  ✗ mean_reversion     win= 35.3%  sharpe=-3.61  dd= 30.4%

  ── LEVELS ──
  Support: 59.63  |  Resistance: 60.73
════════════════════════════════════════════════════════
```

## Commands

```bash
python analyze.py              # Full pipeline: fetch → indicators → backtest → brief
python analyze.py --fetch-only # Update data only
python analyze.py --backtest   # Run backtests only
python analyze.py --no-fetch   # Skip fetch, use cached data
```

## Architecture

```
huayou-analyst/
├── analyze.py            # CLI entry point — orchestrates the full pipeline
├── config.py             # Ticker, thresholds, tuning parameters
├── data/
│   ├── fetcher.py        # AKShare data fetch with retry + incremental updates
│   ├── store.py          # SQLite schema + CRUD (OHLCV, indicators, briefs)
│   └── indicators.py     # 20 technical indicators via pandas-ta
├── agents/
│   ├── base.py           # AgentResult dataclass
│   ├── technical.py      # Rule-based scoring (MA, MACD, RSI, KDJ, Bollinger, volume)
│   └── strategist.py     # Regime matching, action decision, morning brief synthesis
├── backtest/
│   └── engine.py         # 5 strategies + walk-forward validation
└── tests/                # 22 tests — store, indicators, backtest, grounding, regime
```

## How It Works

### Technical Analyst (rule-based, -100 to +100)

Six sub-scorers, each contributing a weighted component:

| Signal | Max Score | What it checks |
|--------|-----------|----------------|
| MA Alignment | ±25 | 均线多头/空头排列 (MA5 vs MA10 vs MA20 vs MA60) |
| MACD | ±20 | 金叉/死叉, histogram momentum |
| RSI | ±20 | Overbought (>70) / oversold (<30) |
| KDJ | ±15 | K/D cross with J-value context |
| Bollinger | ±10 | Price position relative to bands |
| Volume | ±10 | 放量/缩量 patterns with price direction |

### Backtest Engine

- **5 strategies**: MA crossover, MACD divergence, volume breakout, RSI oversold, mean reversion
- **Walk-forward validation**: train on first 4 years, test out-of-sample on the remainder
- **T+1 compliance**: buys execute at next-day open; limit-up/down fills are skipped
- **Promotion thresholds**: win rate ≥ 55%, Sharpe ≥ 1.0, max drawdown ≤ 20%, ≥ 20 trades

### Regime Matching

Classifies the current market state into a 2D regime (trend × RSI bucket), finds all historical matches in 603799's data, and computes forward 5-day return statistics. Requires ≥ 15 samples to report confidence.

### Grounding Validator

Every number in the morning brief is cross-checked against agent outputs, backtest results, and regime data. Hallucinated statistics are flagged as violations.

## Data Sources

| Source | Data | Refresh |
|--------|------|---------|
| [AKShare](https://github.com/akfamily/akshare) | OHLCV (前复权/qfq) | Daily, incremental |
| SQLite (local) | Indicators, agent runs, briefs | Computed on each run |

## Tests

```bash
python -m pytest tests/ -v    # 22 tests, <3 seconds
```

## Disclaimer

This tool is for personal research and education only. It does not constitute financial advice. Past backtest performance does not guarantee future results. Always do your own due diligence before trading.
