"""TaoGuBa (淘股吧) expert blog scraper + sentiment scorer for 603799."""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from data.taoguba_keywords import (
    extract_signals,
    label_sentiment,
    mentions_stock,
    score_text,
    ExtractedSignals,
)

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


@dataclass
class ExpertPost:
    expert_id: str
    expert_name: str
    title: str
    content: str
    publish_time: str
    url: str
    sentiment_score: float = 0.0
    sentiment_label: str = ""
    signals: ExtractedSignals = field(default_factory=ExtractedSignals)


@dataclass
class ExpertSnapshot:
    posts: list[ExpertPost] = field(default_factory=list)
    consensus_score: float = 0.0  # -1.0 to +1.0
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    total_experts_checked: int = 0
    fetch_errors: list[str] = field(default_factory=list)


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.tgb.cn/",
    }


def _parse_blog_page(html: str, expert_id: str, expert_name: str,
                     max_age_days: int = 3) -> list[ExpertPost]:
    """Parse expert blog page HTML, extract posts mentioning 603799."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("beautifulsoup4 not installed, cannot parse TaoGuBa pages")
        return []

    soup = BeautifulSoup(html, "html.parser")
    cutoff = datetime.now() - timedelta(days=max_age_days)
    posts: list[ExpertPost] = []

    for article in soup.select("div.blog-item, div.article-item, div.post-item, li.blog_list"):
        title_el = article.select_one("a.title, h3 a, .blog_title a, a[href*='/blog/']")
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        if href and not href.startswith("http"):
            href = f"https://www.tgb.cn{href}"

        content_el = article.select_one(".content, .summary, .blog_content, p")
        content = content_el.get_text(strip=True) if content_el else ""

        time_el = article.select_one(".time, .date, .pub_time, time, span.time")
        pub_time_str = time_el.get_text(strip=True) if time_el else ""

        pub_dt = _parse_time(pub_time_str)
        if pub_dt and pub_dt < cutoff:
            continue

        combined_text = title + " " + content
        if not mentions_stock(combined_text):
            continue

        score = score_text(combined_text)
        signals = extract_signals(combined_text)

        posts.append(ExpertPost(
            expert_id=expert_id,
            expert_name=expert_name,
            title=title,
            content=content[:500],
            publish_time=pub_time_str,
            url=href,
            sentiment_score=score,
            sentiment_label=label_sentiment(score),
            signals=signals,
        ))

    return posts


def _parse_time(time_str: str) -> datetime | None:
    """Best-effort parse of Chinese date/time strings."""
    if not time_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%m-%d %H:%M", "%m月%d日 %H:%M"):
        try:
            dt = datetime.strptime(time_str.strip(), fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return dt
        except ValueError:
            continue
    if "今天" in time_str or "刚刚" in time_str or "分钟前" in time_str or "小时前" in time_str:
        return datetime.now()
    if "昨天" in time_str:
        return datetime.now() - timedelta(days=1)
    return None


def _load_cached_posts(conn: sqlite3.Connection, expert_id: str,
                       max_age_days: int = 3) -> list[ExpertPost]:
    """Load cached posts from DB to avoid re-fetching."""
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT expert_id, expert_name, title, content, post_date, url, "
        "sentiment_score, signals_json FROM expert_posts "
        "WHERE expert_id = ? AND post_date >= ? AND fetched_at >= date('now')",
        (expert_id, cutoff),
    ).fetchall()

    posts = []
    for row in rows:
        signals_data = json.loads(row[7]) if row[7] else {}
        signals = ExtractedSignals(
            actions=signals_data.get("actions", []),
            price_targets=signals_data.get("price_targets", {}),
            sentiment_label=signals_data.get("sentiment_label", ""),
            confidence_level=signals_data.get("confidence_level", ""),
        )
        posts.append(ExpertPost(
            expert_id=row[0], expert_name=row[1],
            title=row[2], content=row[3],
            publish_time=row[4], url=row[5],
            sentiment_score=float(row[6]),
            sentiment_label=label_sentiment(float(row[6])),
            signals=signals,
        ))
    return posts


def _cache_posts(conn: sqlite3.Connection, posts: list[ExpertPost]) -> None:
    """Cache scraped posts to SQLite."""
    for p in posts:
        signals_json = json.dumps({
            "actions": p.signals.actions,
            "price_targets": p.signals.price_targets,
            "sentiment_label": p.signals.sentiment_label,
            "confidence_level": p.signals.confidence_level,
        }, ensure_ascii=False)
        conn.execute(
            "INSERT OR REPLACE INTO expert_posts "
            "(expert_id, expert_name, post_date, title, content, url, "
            "sentiment_score, signals_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (p.expert_id, p.expert_name, p.publish_time, p.title,
             p.content, p.url, p.sentiment_score, signals_json),
        )
    conn.commit()


def _fetch_expert_page(expert_id: str, expert_name: str,
                       max_age_days: int = 3,
                       conn: sqlite3.Connection | None = None) -> list[ExpertPost]:
    """Fetch a single expert's blog page and parse posts."""
    if conn:
        cached = _load_cached_posts(conn, expert_id, max_age_days)
        if cached:
            logger.info("Using %d cached posts for expert %s", len(cached), expert_name)
            return cached

    try:
        import requests
    except ImportError:
        logger.warning("requests not installed, cannot fetch TaoGuBa pages")
        return []

    url = f"https://www.tgb.cn/blog/{expert_id}"
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to fetch expert %s (%s): %s", expert_name, expert_id, e)
        return []

    posts = _parse_blog_page(resp.text, expert_id, expert_name, max_age_days)

    if conn and posts:
        _cache_posts(conn, posts)

    return posts


def fetch_expert_posts(
    experts: list[dict],
    max_age_days: int = 3,
    request_delay: float = 3.0,
    conn: sqlite3.Connection | None = None,
) -> ExpertSnapshot:
    """Fetch recent posts from all configured experts.

    Args:
        experts: list of {"id": "12345", "name": "某大神"}
        max_age_days: only keep posts within this window
        request_delay: seconds to wait between HTTP requests
        conn: optional SQLite connection for caching
    """
    if not experts:
        return ExpertSnapshot()

    all_posts: list[ExpertPost] = []
    errors: list[str] = []

    for i, expert in enumerate(experts):
        eid = expert.get("id", "")
        ename = expert.get("name", eid)
        if not eid:
            continue

        try:
            posts = _fetch_expert_page(eid, ename, max_age_days, conn)
            all_posts.extend(posts)
        except Exception as e:
            msg = f"Expert {ename} ({eid}): {e}"
            logger.warning(msg)
            errors.append(msg)

        if i < len(experts) - 1:
            delay = request_delay + random.uniform(0, 2)
            time.sleep(delay)

    if not all_posts:
        return ExpertSnapshot(
            total_experts_checked=len(experts),
            fetch_errors=errors,
        )

    bullish = sum(1 for p in all_posts if p.sentiment_label == "看多")
    bearish = sum(1 for p in all_posts if p.sentiment_label == "看空")
    neutral = len(all_posts) - bullish - bearish

    if all_posts:
        scores = [p.sentiment_score for p in all_posts]
        consensus = round(sum(scores) / len(scores), 2)
    else:
        consensus = 0.0

    all_posts.sort(key=lambda p: p.publish_time, reverse=True)

    return ExpertSnapshot(
        posts=all_posts,
        consensus_score=consensus,
        bullish_count=bullish,
        bearish_count=bearish,
        neutral_count=neutral,
        total_experts_checked=len(experts),
        fetch_errors=errors,
    )
