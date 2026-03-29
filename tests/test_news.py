"""Tests for data.news module — sentiment scoring and news fetching."""

import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from data.news import (
    _score_text, _label, fetch_news,
    NewsItem, NewsSentiment, BULLISH_KEYWORDS, BEARISH_KEYWORDS,
)


class TestScoreText:
    def test_pure_bullish(self):
        score = _score_text("利好消息 增持 买入 上调 突破")
        assert score > 0

    def test_pure_bearish(self):
        score = _score_text("利空 减持 卖出 下调 跌破")
        assert score < 0

    def test_neutral(self):
        score = _score_text("今天天气很好")
        assert score == 0.0

    def test_mixed(self):
        score = _score_text("利好消息 但也有利空风险")
        assert -1.0 <= score <= 1.0

    def test_empty(self):
        assert _score_text("") == 0.0


class TestLabel:
    def test_bullish_label(self):
        assert _label(0.5) == "利好"

    def test_bearish_label(self):
        assert _label(-0.5) == "利空"

    def test_neutral_label(self):
        assert _label(0.0) == "中性"
        assert _label(0.1) == "中性"
        assert _label(-0.1) == "中性"


class TestFetchNews:
    @patch("akshare.stock_news_em")
    def test_returns_items(self, mock_news):
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        mock_news.return_value = pd.DataFrame({
            "关键词": ["603799", "603799"],
            "新闻标题": ["华友钴业利好消息涨停突破", "市场震荡"],
            "新闻内容": ["增持买入推荐", "持有观望"],
            "发布时间": [yesterday, two_days_ago],
            "文章来源": ["东方财富", "证券时报"],
            "新闻链接": ["http://a.com", "http://b.com"],
        })
        result = fetch_news()
        assert isinstance(result, NewsSentiment)
        assert len(result.items) == 2
        assert result.bullish_count >= 1

    @patch("akshare.stock_news_em")
    def test_empty_dataframe(self, mock_news):
        mock_news.return_value = pd.DataFrame()
        result = fetch_news()
        assert result.items == []
        assert result.overall_score == 0.0

    @patch("akshare.stock_news_em")
    def test_api_failure(self, mock_news):
        mock_news.side_effect = Exception("network error")
        result = fetch_news()
        assert result.fetch_error == "network error"
        assert result.items == []

    @patch("akshare.stock_news_em")
    def test_sentiment_score_range(self, mock_news):
        mock_news.return_value = pd.DataFrame({
            "关键词": ["603799"],
            "新闻标题": ["利好增持突破"],
            "新闻内容": ["买入推荐"],
            "发布时间": ["2026-03-22 10:00:00"],
            "文章来源": ["东方财富"],
            "新闻链接": ["http://a.com"],
        })
        result = fetch_news()
        assert -1.0 <= result.overall_score <= 1.0
        for item in result.items:
            assert -1.0 <= item.sentiment_score <= 1.0


class TestKeywordCompleteness:
    def test_no_overlap(self):
        overlap = set(BULLISH_KEYWORDS) & set(BEARISH_KEYWORDS)
        assert overlap == set(), f"Keywords appear in both lists: {overlap}"
