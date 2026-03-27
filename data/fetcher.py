"""Fetch 603799 OHLCV data from AKShare with retry and incremental updates."""

import time
import logging

import akshare as ak
import pandas as pd

import config

logger = logging.getLogger(__name__)


def fetch_ohlcv(
    ticker: str = config.TICKER,
    start_date: str = "20190101",
    end_date: str | None = None,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """Fetch daily OHLCV with retry + exponential backoff.

    Returns a DataFrame with columns:
      date, open, high, low, close, volume, turnover, turnover_rate
    """
    for attempt in range(config.FETCH_RETRY_ATTEMPTS):
        try:
            df = ak.stock_zh_a_hist(
                symbol=ticker,
                period="daily",
                start_date=start_date,
                end_date=end_date or pd.Timestamp.now().strftime("%Y%m%d"),
                adjust=adjust,
            )
            break
        except Exception as e:
            delay = config.FETCH_RETRY_DELAYS[attempt] if attempt < len(config.FETCH_RETRY_DELAYS) else 60
            logger.warning("Fetch attempt %d failed: %s. Retrying in %ds...", attempt + 1, e, delay)
            if attempt == config.FETCH_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(delay)

    rename_map = {
        "日期": "date", "开盘": "open", "最高": "high",
        "最低": "low", "收盘": "close", "成交量": "volume",
        "成交额": "turnover", "换手率": "turnover_rate",
    }
    df = df.rename(columns=rename_map)

    keep_cols = [c for c in ["date", "open", "high", "low", "close", "volume", "turnover", "turnover_rate"] if c in df.columns]
    df = df[keep_cols]

    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def fetch_incremental(conn, ticker: str = config.TICKER) -> pd.DataFrame:
    """Fetch only new data since the last stored date."""
    from data.store import get_latest_date

    latest = get_latest_date(conn)
    if latest:
        start = (pd.Timestamp(latest) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    else:
        start = "20190101"

    today = pd.Timestamp.now().strftime("%Y%m%d")
    if start > today:
        logger.info("Data is up to date (latest: %s)", latest)
        return pd.DataFrame()

    logger.info("Fetching data from %s to %s", start, today)
    return fetch_ohlcv(ticker=ticker, start_date=start, end_date=today)
