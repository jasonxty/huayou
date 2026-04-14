"""Trading-specific keyword dictionaries and signal extraction for TaoGuBa posts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


BULLISH_KEYWORDS = [
    "看多", "看涨", "做多", "建仓", "加仓", "满仓", "重仓", "抄底", "低吸",
    "反弹", "突破", "拉升", "涨停", "主升浪", "起飞", "龙头", "强势",
    "利好", "超预期", "景气", "回暖", "放量", "金叉", "多头",
    "买入", "介入", "上车", "启动", "打板", "追涨", "核心票",
    "目标价", "翻倍", "牛股", "底部", "支撑有效", "企稳",
]

BEARISH_KEYWORDS = [
    "看空", "看跌", "做空", "减仓", "清仓", "空仓", "跑路", "出局",
    "下跌", "跌停", "破位", "暴跌", "崩盘", "割肉", "止损",
    "利空", "不及预期", "高位", "见顶", "头部", "套牢", "被套",
    "死叉", "空头", "缩量", "弱势", "阴跌", "杀跌",
    "卖出", "离场", "回避", "风险", "压力位", "破位下行",
]

NEUTRAL_KEYWORDS = [
    "观望", "等待", "震荡", "横盘", "整理", "分化", "中性",
    "持有", "不动", "仅供参考", "谨慎", "犹豫",
]

CONFIDENCE_BOOST = [
    "确定性高", "大概率", "必涨", "铁底", "坚定看好", "重仓干",
    "满仓梭哈", "这票稳了", "信心十足",
]

CONFIDENCE_DISCOUNT = [
    "仅供参考", "不构成建议", "赌一把", "小仓位试试", "风险自担",
    "说不准", "不确定", "观察观察",
]

STOCK_ALIASES = ["603799", "华友钴业", "华友", "钴业"]

_PRICE_PATTERNS = [
    re.compile(r"目标[价位]?\s*[:：]?\s*(\d+\.?\d*)\s*[元块]?"),
    re.compile(r"看到\s*(\d+\.?\d*)"),
    re.compile(r"支撑[位]?\s*[:：]?\s*(\d+\.?\d*)"),
    re.compile(r"压力[位]?\s*[:：]?\s*(\d+\.?\d*)"),
    re.compile(r"止损[位价]?\s*[:：]?\s*(\d+\.?\d*)"),
    re.compile(r"止盈[位价]?\s*[:：]?\s*(\d+\.?\d*)"),
    re.compile(r"(\d+\.?\d*)\s*[元块]\s*(?:以上|以下|附近|左右)"),
]

ACTION_PATTERNS = {
    "建仓": re.compile(r"建仓|新开仓"),
    "加仓": re.compile(r"加仓|补仓|加码"),
    "减仓": re.compile(r"减仓|减持|卖一[些半部]"),
    "清仓": re.compile(r"清仓|全部卖出|全卖了|跑路"),
    "低吸": re.compile(r"低吸|逢低买|回调买|接回"),
    "做T": re.compile(r"做[tT]|T\+0|高抛低吸|日内"),
    "打板": re.compile(r"打板|排板|封板"),
    "追涨": re.compile(r"追涨|追入|追高"),
    "持有": re.compile(r"继续持有|拿住|不动|持股待涨"),
    "观望": re.compile(r"观望|等待|不参与|场外看"),
    "止损": re.compile(r"止损|割肉|认赔"),
    "止盈": re.compile(r"止盈|落袋|锁定利润"),
}


@dataclass
class ExtractedSignals:
    """Structured signals extracted from expert post text."""
    actions: list[str] = field(default_factory=list)
    price_targets: dict[str, float] = field(default_factory=dict)
    sentiment_label: str = ""  # 看多 / 看空 / 中性
    confidence_level: str = ""  # 高 / 中 / 低


def score_text(text: str) -> float:
    """Score expert post text. Returns -1.0 to +1.0."""
    bull = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
    bear = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 2)


def label_sentiment(score: float) -> str:
    if score > 0.15:
        return "看多"
    elif score < -0.15:
        return "看空"
    return "中性"


def mentions_stock(text: str) -> bool:
    """Check if text mentions 603799 / 华友钴业."""
    return any(alias in text for alias in STOCK_ALIASES)


def extract_signals(text: str) -> ExtractedSignals:
    """Extract structured trading signals from post text."""
    signals = ExtractedSignals()

    for action_name, pattern in ACTION_PATTERNS.items():
        if pattern.search(text):
            signals.actions.append(action_name)

    for pat in _PRICE_PATTERNS:
        for match in pat.finditer(text):
            price = float(match.group(1))
            if 10 < price < 500:
                label_text = match.group(0)
                if "目标" in label_text or "看到" in label_text or "止盈" in label_text:
                    signals.price_targets["目标"] = price
                elif "支撑" in label_text:
                    signals.price_targets["支撑"] = price
                elif "压力" in label_text:
                    signals.price_targets["压力"] = price
                elif "止损" in label_text:
                    signals.price_targets["止损"] = price
                elif "以上" in label_text or "以下" in label_text:
                    signals.price_targets["关注"] = price

    score = score_text(text)
    signals.sentiment_label = label_sentiment(score)

    has_boost = any(kw in text for kw in CONFIDENCE_BOOST)
    has_discount = any(kw in text for kw in CONFIDENCE_DISCOUNT)
    if has_boost and not has_discount:
        signals.confidence_level = "高"
    elif has_discount and not has_boost:
        signals.confidence_level = "低"
    else:
        signals.confidence_level = "中"

    return signals
