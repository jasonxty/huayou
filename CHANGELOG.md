# Changelog

## 2026-06-16 — Buffett Gets Smarter

### Regime-Aware Dynamic Strategy

Buffett no longer uses one set of rules for all market conditions. He now detects six
dimensions of market state — trend, RSI, volatility, momentum, consecutive down days,
and 5-day cumulative drop — and activates one of five strategy modes:

- **Trend Following**: In confirmed uptrends, Buffett rides momentum with easier buys
  and tighter trailing stops (70% position).
- **Swing / Range-Bound**: In sideways markets, buy support and sell resistance with
  standard thresholds (50% position).
- **Defensive Exit**: In downtrends, protect capital with harder buys and easier sells
  (20% position).
- **Contrarian Bounce**: When oversold in a downtrend, look for reversal entries and
  block panic selling (30% position).
- **Capitulation Watch**: After 5+ consecutive down days or a -10% drop, watch for
  snap-back opportunities with very easy buy triggers (25% position).

The active strategy mode is shown at the top of every morning brief and as a color-coded
badge in the strategic comparison table on the dashboard.

### Commodity-Driven Signals

SHFE nickel and lithium carbonate price momentum now directly influence Buffett's
buy/sell decisions. When upstream commodity prices rally, the signal score gets a boost
since Huayou's margins improve. A new **Commodity Signal Weight** is tunable in the
Control Panel.

### Lower Thresholds, More Actionable Signals

BUY/SELL thresholds dropped from ±40 to ±25 (light thresholds from ±20 to ±12). Buffett
was stuck saying HOLD 87% of the time — now he actually takes positions when the setup
is there.

### Fundamental Data Caching

When the AKShare API fails (common), Buffett now falls back to cached fundamental data
instead of dropping the score to zero. No more random signal flips because of network
hiccups. Cache is valid for 90 days (one quarterly report cycle).

### Tighter HOLD Accuracy Standard

The "correct HOLD" threshold was tightened from 1.8% to 1.0% — on a stock that moves
4%+ per day, the old standard was too easy. Accuracy stats are now meaningful.

### Control Panel Enhancements

The control panel now explains the regime strategy system, shows all five modes with
their effects, and includes the new Commodity Signal Weight slider.
