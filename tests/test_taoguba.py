"""Tests for TaoGuBa expert integration — keywords, signals, scraper, config."""

import json
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from data.taoguba_keywords import (
    score_text,
    label_sentiment,
    mentions_stock,
    extract_signals,
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
    ExtractedSignals,
)
from data.taoguba import (
    ExpertPost,
    ExpertSnapshot,
    _parse_blog_page,
    _parse_time,
    _load_cached_posts,
    _cache_posts,
    fetch_expert_posts,
)
from agents.strategist import _expert_confidence_adjustment


# ── Keyword scoring ──

class TestScoreText:
    def test_bullish(self):
        score = score_text("看多 建仓 加仓 突破 龙头")
        assert score > 0

    def test_bearish(self):
        score = score_text("看空 清仓 跌停 破位 暴跌")
        assert score < 0

    def test_neutral(self):
        assert score_text("今天天气不错") == 0.0

    def test_mixed(self):
        score = score_text("看多 但有看空风险")
        assert -1.0 <= score <= 1.0

    def test_empty(self):
        assert score_text("") == 0.0

    def test_no_overlap(self):
        overlap = set(BULLISH_KEYWORDS) & set(BEARISH_KEYWORDS)
        assert overlap == set(), f"Keywords in both lists: {overlap}"


class TestLabelSentiment:
    def test_bullish(self):
        assert label_sentiment(0.5) == "看多"

    def test_bearish(self):
        assert label_sentiment(-0.5) == "看空"

    def test_neutral(self):
        assert label_sentiment(0.0) == "中性"
        assert label_sentiment(0.1) == "中性"
        assert label_sentiment(-0.1) == "中性"


class TestMentionsStock:
    def test_ticker(self):
        assert mentions_stock("603799 走势分析")

    def test_name(self):
        assert mentions_stock("华友钴业今日涨停")

    def test_alias(self):
        assert mentions_stock("华友的走势不错")

    def test_no_match(self):
        assert not mentions_stock("宁德时代走势分析")


# ── Signal extraction ──

class TestExtractSignals:
    def test_action_extraction(self):
        text = "华友钴业建仓，低吸为主，做T赚差价"
        signals = extract_signals(text)
        assert "建仓" in signals.actions
        assert "低吸" in signals.actions
        assert "做T" in signals.actions

    def test_price_target(self):
        text = "目标价65元，支撑58，压力70"
        signals = extract_signals(text)
        assert signals.price_targets.get("目标") == 65.0
        assert signals.price_targets.get("支撑") == 58.0
        assert signals.price_targets.get("压力") == 70.0

    def test_stop_loss(self):
        text = "止损位55元"
        signals = extract_signals(text)
        assert signals.price_targets.get("止损") == 55.0

    def test_sentiment_label(self):
        bullish_text = "看多 建仓 加仓 龙头"
        signals = extract_signals(bullish_text)
        assert signals.sentiment_label == "看多"

        bearish_text = "看空 清仓 暴跌 破位"
        signals = extract_signals(bearish_text)
        assert signals.sentiment_label == "看空"

    def test_confidence_boost(self):
        text = "确定性高 坚定看好 华友必涨"
        signals = extract_signals(text)
        assert signals.confidence_level == "高"

    def test_confidence_discount(self):
        text = "仅供参考 风险自担 赌一把"
        signals = extract_signals(text)
        assert signals.confidence_level == "低"

    def test_no_signals(self):
        signals = extract_signals("今天天气不错")
        assert signals.actions == []
        assert signals.price_targets == {}

    def test_ignores_unrealistic_prices(self):
        text = "目标价5元"  # too low
        signals = extract_signals(text)
        assert "目标" not in signals.price_targets


# ── Time parsing ──

class TestParseTime:
    def test_full_datetime(self):
        dt = _parse_time("2026-03-20 14:30:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3

    def test_date_only(self):
        dt = _parse_time("2026-03-20")
        assert dt is not None

    def test_today(self):
        dt = _parse_time("今天 14:30")
        assert dt is not None

    def test_yesterday(self):
        dt = _parse_time("昨天 10:00")
        assert dt is not None

    def test_empty(self):
        assert _parse_time("") is None

    def test_garbage(self):
        assert _parse_time("not a date") is None


# ── HTML parsing ──

SAMPLE_HTML = """
<html><body>
<div class="blog-item">
    <a class="title" href="/blog/12345/post1">华友钴业低吸建仓，目标价65元</a>
    <p class="content">603799这个位置可以低吸，支撑58，压力65</p>
    <span class="time">2026-03-21 10:30</span>
</div>
<div class="blog-item">
    <a class="title" href="/blog/12345/post2">今天吃啥</a>
    <p class="content">去食堂吃饭</p>
    <span class="time">2026-03-21 11:00</span>
</div>
<div class="blog-item">
    <a class="title" href="/blog/12345/post3">华友看空清仓跑路</a>
    <p class="content">华友钴业破位了赶紧跑</p>
    <span class="time">2020-01-01 10:00</span>
</div>
</body></html>
"""


class TestParseBlogPage:
    def test_filters_stock_mentions(self):
        posts = _parse_blog_page(SAMPLE_HTML, "12345", "测试大神", max_age_days=3650)
        stock_titles = [p.title for p in posts]
        assert any("华友" in t for t in stock_titles)
        assert not any("吃啥" in t for t in stock_titles)

    def test_extracts_signals(self):
        posts = _parse_blog_page(SAMPLE_HTML, "12345", "测试大神", max_age_days=3650)
        if posts:
            first = posts[0]
            assert first.expert_id == "12345"
            assert first.expert_name == "测试大神"
            assert first.sentiment_score != 0

    def test_empty_html(self):
        posts = _parse_blog_page("<html></html>", "12345", "测试大神")
        assert posts == []


# ── DB caching ──

class TestCaching:
    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE expert_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expert_id TEXT, expert_name TEXT, post_date TEXT,
                title TEXT, content TEXT, url TEXT,
                sentiment_score REAL, signals_json TEXT,
                fetched_at TEXT DEFAULT (datetime('now')),
                UNIQUE(expert_id, url)
            )
        """)
        conn.commit()
        return conn

    def test_cache_and_load(self, db):
        posts = [ExpertPost(
            expert_id="123", expert_name="大神A",
            title="华友看多", content="看好华友钴业",
            publish_time="2026-03-21", url="https://tgb.cn/blog/123/p1",
            sentiment_score=0.5, sentiment_label="看多",
            signals=ExtractedSignals(actions=["建仓"], price_targets={"目标": 65.0},
                                     sentiment_label="看多", confidence_level="中"),
        )]
        _cache_posts(db, posts)

        cached = _load_cached_posts(db, "123", max_age_days=30)
        assert len(cached) == 1
        assert cached[0].title == "华友看多"
        assert cached[0].signals.actions == ["建仓"]
        assert cached[0].signals.price_targets == {"目标": 65.0}

    def test_load_empty(self, db):
        cached = _load_cached_posts(db, "999", max_age_days=3)
        assert cached == []


# ── ExpertSnapshot aggregation ──

class TestFetchExpertPosts:
    @patch("data.taoguba._fetch_expert_page")
    def test_aggregation(self, mock_fetch):
        mock_fetch.return_value = [
            ExpertPost("1", "A", "华友看多", "", "2026-03-21", "",
                       sentiment_score=0.5, sentiment_label="看多"),
            ExpertPost("2", "B", "华友看空", "", "2026-03-20", "",
                       sentiment_score=-0.5, sentiment_label="看空"),
        ]
        experts = [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]
        snap = fetch_expert_posts(experts, request_delay=0)

        assert snap.bullish_count >= 1
        assert snap.bearish_count >= 1
        assert snap.total_experts_checked == 2
        assert -1.0 <= snap.consensus_score <= 1.0

    @patch("data.taoguba._fetch_expert_page")
    def test_empty_results(self, mock_fetch):
        mock_fetch.return_value = []
        snap = fetch_expert_posts([{"id": "1", "name": "A"}], request_delay=0)
        assert snap.posts == []
        assert snap.total_experts_checked == 1

    def test_no_experts(self):
        snap = fetch_expert_posts([])
        assert snap.posts == []
        assert snap.total_experts_checked == 0

    @patch("data.taoguba._fetch_expert_page")
    def test_graceful_error(self, mock_fetch):
        mock_fetch.side_effect = Exception("network error")
        snap = fetch_expert_posts([{"id": "1", "name": "A"}], request_delay=0)
        assert snap.posts == []
        assert len(snap.fetch_errors) == 1
        assert "network error" in snap.fetch_errors[0]


# ── Confidence adjustment ──

class TestExpertConfidenceAdjustment:
    def _make_snapshot(self, bullish, bearish, neutral):
        posts = []
        for i in range(bullish):
            posts.append(ExpertPost(str(i), f"B{i}", "", "", "", "",
                                    sentiment_label="看多"))
        for i in range(bearish):
            posts.append(ExpertPost(str(100+i), f"R{i}", "", "", "", "",
                                    sentiment_label="看空"))
        for i in range(neutral):
            posts.append(ExpertPost(str(200+i), f"N{i}", "", "", "", "",
                                    sentiment_label="中性"))
        return ExpertSnapshot(
            posts=posts,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            total_experts_checked=bullish + bearish + neutral,
        )

    def test_aligned_bullish(self):
        snap = self._make_snapshot(3, 0, 0)
        adj = _expert_confidence_adjustment(snap, tech_score=20)
        assert adj == 0.08

    def test_divergent_bearish_vs_tech_bull(self):
        snap = self._make_snapshot(0, 3, 0)
        adj = _expert_confidence_adjustment(snap, tech_score=20)
        assert adj == -0.08

    def test_split_no_adjustment(self):
        snap = self._make_snapshot(1, 1, 1)
        adj = _expert_confidence_adjustment(snap, tech_score=20)
        assert adj == 0.0

    def test_none_snapshot(self):
        adj = _expert_confidence_adjustment(None, tech_score=20)
        assert adj == 0.0

    def test_single_expert_insufficient(self):
        snap = self._make_snapshot(1, 0, 0)
        adj = _expert_confidence_adjustment(snap, tech_score=20)
        assert adj == 0.0

    def test_empty_posts(self):
        snap = ExpertSnapshot()
        adj = _expert_confidence_adjustment(snap, tech_score=20)
        assert adj == 0.0


# ── Config ──

class TestTaoGuBaConfig:
    @patch("config._LOCAL_CONFIG")
    def test_disabled(self, mock_path):
        mock_path.exists.return_value = False
        from config import get_taoguba_config
        cfg = get_taoguba_config()
        assert cfg["enabled"] is False
        assert cfg["experts"] == []

    @patch("builtins.open")
    @patch("config._LOCAL_CONFIG")
    def test_enabled(self, mock_path, mock_open):
        mock_path.exists.return_value = True
        import yaml
        yaml_content = {
            "taoguba": {
                "enabled": True,
                "experts": [{"id": "123", "name": "大神"}],
                "max_post_age_days": 5,
                "request_delay_seconds": 2,
            }
        }
        mock_open.return_value.__enter__ = lambda s: MagicMock(
            read=lambda: yaml.dump(yaml_content))
        mock_open.return_value.__exit__ = MagicMock(return_value=False)

        with patch("yaml.safe_load", return_value=yaml_content):
            from config import get_taoguba_config
            cfg = get_taoguba_config()
            assert cfg["enabled"] is True
            assert len(cfg["experts"]) == 1
            assert cfg["max_post_age_days"] == 5
