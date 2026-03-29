"""News fetching and keyword-based sentiment scoring for 603799."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)

BULLISH_KEYWORDS = [
    "利好", "增持", "买入", "上调", "突破", "新高", "放量上涨", "强势",
    "超预期", "盈利", "扭亏", "订单", "扩产", "签约", "中标", "涨停",
    "需求旺盛", "景气", "回暖", "反弹", "龙头", "推荐", "增长",
    "产能释放", "新能源", "电池", "出海",
]

BEARISH_KEYWORDS = [
    "利空", "减持", "卖出", "下调", "跌破", "新低", "缩量下跌", "弱势",
    "不及预期", "亏损", "下滑", "减产", "取消", "处罚", "跌停",
    "需求萎缩", "过剩", "产能过剩", "暴跌", "风险", "下降",
    "价格战", "回落", "衰退",
]

NEUTRAL_KEYWORDS = [
    "维持", "持有", "观望", "震荡", "整理", "分化", "稳定",
]


@dataclass
class NewsItem:
    title: str
    content: str
    publish_time: str
    source: str
    url: str
    sentiment_score: float = 0.0  # -1.0 to +1.0
    sentiment_label: str = ""  # 利好 / 利空 / 中性


@dataclass
class NewsSentiment:
    items: list[NewsItem] = field(default_factory=list)
    overall_score: float = 0.0  # weighted average
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    fetch_error: str = ""


def _score_text(text: str) -> float:
    """Score a text string based on keyword matching. Returns -1.0 to +1.0."""
    text_lower = text.lower()
    bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 2)


def _label(score: float) -> str:
    if score > 0.15:
        return "利好"
    elif score < -0.15:
        return "利空"
    return "中性"


def fetch_news(ticker: str = config.TICKER, max_items: int = 15) -> NewsSentiment:
    """Fetch recent news for the ticker and compute sentiment."""
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=ticker)
    except Exception as e:
        logger.warning("News fetch failed: %s", e)
        return NewsSentiment(fetch_error=str(e))

    if df.empty:
        return NewsSentiment()

    cutoff = datetime.now() - timedelta(days=7)
    items = []

    for _, row in df.head(max_items).iterrows():
        title = str(row.get("新闻标题", ""))
        content = str(row.get("新闻内容", ""))
        pub_time = str(row.get("发布时间", ""))

        try:
            pub_dt = datetime.strptime(pub_time[:19], "%Y-%m-%d %H:%M:%S")
            if pub_dt < cutoff:
                continue
        except (ValueError, TypeError):
            pass

        combined = title + " " + content
        score = _score_text(combined)
        items.append(NewsItem(
            title=title,
            content=content[:200],
            publish_time=pub_time,
            source=str(row.get("文章来源", "")),
            url=str(row.get("新闻链接", "")),
            sentiment_score=score,
            sentiment_label=_label(score),
        ))

    if not items:
        return NewsSentiment()

    bullish = sum(1 for it in items if it.sentiment_label == "利好")
    bearish = sum(1 for it in items if it.sentiment_label == "利空")
    neutral = len(items) - bullish - bearish

    weights = []
    for i, it in enumerate(items):
        recency_weight = 1.0 / (i + 1)
        weights.append(it.sentiment_score * recency_weight)

    overall = round(sum(weights) / sum(1.0 / (i + 1) for i in range(len(items))), 2) if items else 0.0

    return NewsSentiment(
        items=items,
        overall_score=overall,
        bullish_count=bullish,
        bearish_count=bearish,
        neutral_count=neutral,
    )
