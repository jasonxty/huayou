"""Compute technical indicators for 603799 using pandas-ta."""

import pandas as pd
import pandas_ta as ta


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators on OHLCV DataFrame.

    Input must have: date, open, high, low, close, volume.
    Returns a new DataFrame with date + all indicator columns.
    """
    out = pd.DataFrame()
    out["date"] = df["date"]

    out["ma5"] = ta.sma(df["close"], length=5)
    out["ma10"] = ta.sma(df["close"], length=10)
    out["ma20"] = ta.sma(df["close"], length=20)
    out["ma60"] = ta.sma(df["close"], length=60)
    out["ma120"] = ta.sma(df["close"], length=120)
    out["ma250"] = ta.sma(df["close"], length=250)

    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        out["macd"] = macd.iloc[:, 0]
        out["macd_signal"] = macd.iloc[:, 2]
        out["macd_hist"] = macd.iloc[:, 1]

    out["rsi6"] = ta.rsi(df["close"], length=6)
    out["rsi12"] = ta.rsi(df["close"], length=12)
    out["rsi24"] = ta.rsi(df["close"], length=24)

    stoch = ta.stoch(df["high"], df["low"], df["close"], k=9, d=3, smooth_k=3)
    if stoch is not None:
        out["kdj_k"] = stoch.iloc[:, 0]
        out["kdj_d"] = stoch.iloc[:, 1]
        out["kdj_j"] = 3 * stoch.iloc[:, 0] - 2 * stoch.iloc[:, 1]

    bbands = ta.bbands(df["close"], length=20, std=2)
    if bbands is not None:
        out["boll_lower"] = bbands.iloc[:, 0]
        out["boll_mid"] = bbands.iloc[:, 1]
        out["boll_upper"] = bbands.iloc[:, 2]

    out["obv"] = ta.obv(df["close"], df["volume"])
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)
    out["atr14"] = atr

    return out
