"""Generate a beautiful HTML morning brief for macOS popup display."""

from __future__ import annotations

import html
import os
from datetime import date
from pathlib import Path


def brief_to_html(brief: dict) -> str:
    """Convert a brief dict into a styled HTML page."""
    today = brief.get("date", date.today().isoformat())
    action = brief.get("action", "N/A")
    confidence = brief.get("confidence", 0)
    risk_level = brief.get("risk_level", "N/A")
    text = brief.get("brief_text", "")

    action_word = action.split("(")[0].strip() if "(" in action else action.split()[0]
    action_colors = {
        "BUY": ("#e8f5e9", "#2e7d32", "#43a047"),
        "SELL": ("#fbe9e7", "#c62828", "#e53935"),
        "HOLD": ("#fff8e1", "#f57f17", "#ffa000"),
    }
    bg, text_color, accent = action_colors.get(action_word, ("#f5f5f5", "#333", "#666"))

    risk_colors = {"LOW": "#43a047", "MEDIUM": "#ffa000", "HIGH": "#e53935"}
    risk_color = risk_colors.get(risk_level, "#666")

    sections = _parse_brief_sections(text)

    html_sections = ""
    for title, content in sections:
        icon = _section_icon(title)
        html_sections += f"""
        <div class="section">
            <div class="section-title">{icon} {html.escape(title)}</div>
            <div class="section-body">{_format_section_content(title, content)}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>华友钴业晨报 — {today}</title>
<style>
    :root {{
        --accent: {accent};
        --bg: {bg};
        --text: {text_color};
        --risk: {risk_color};
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Helvetica Neue", sans-serif;
        background: #f0f2f5;
        color: #1a1a2e;
        padding: 24px;
        max-width: 680px;
        margin: 0 auto;
        -webkit-font-smoothing: antialiased;
    }}
    .header {{
        background: linear-gradient(135deg, var(--accent), var(--text));
        border-radius: 16px;
        padding: 28px 32px;
        color: white;
        margin-bottom: 20px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    }}
    .header-top {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 16px;
    }}
    .stock-name {{
        font-size: 14px;
        opacity: 0.9;
        letter-spacing: 1px;
    }}
    .date {{
        font-size: 13px;
        opacity: 0.8;
    }}
    .action-row {{
        display: flex;
        align-items: baseline;
        gap: 16px;
        margin-bottom: 12px;
    }}
    .action {{
        font-size: 36px;
        font-weight: 700;
        letter-spacing: 2px;
    }}
    .action-cn {{
        font-size: 18px;
        opacity: 0.9;
    }}
    .metrics {{
        display: flex;
        gap: 24px;
        font-size: 14px;
        opacity: 0.9;
    }}
    .metric-label {{ opacity: 0.7; margin-right: 4px; }}
    .metric-value {{ font-weight: 600; }}
    .risk-badge {{
        display: inline-block;
        padding: 2px 10px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 600;
        background: rgba(255,255,255,0.2);
    }}
    .section {{
        background: white;
        border-radius: 12px;
        padding: 20px 24px;
        margin-bottom: 12px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }}
    .section-title {{
        font-size: 13px;
        font-weight: 600;
        color: #666;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 12px;
        padding-bottom: 8px;
        border-bottom: 1px solid #f0f0f0;
    }}
    .section-body {{
        font-size: 14px;
        line-height: 1.8;
        color: #333;
    }}
    .signal {{ margin: 4px 0; }}
    .signal-bull {{ color: #2e7d32; }}
    .signal-bear {{ color: #c62828; }}
    .signal-neutral {{ color: #666; }}
    .news-item {{
        display: flex;
        align-items: flex-start;
        gap: 8px;
        margin: 6px 0;
        font-size: 13px;
    }}
    .news-dot {{
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-top: 6px;
        flex-shrink: 0;
    }}
    .news-dot.bull {{ background: #43a047; }}
    .news-dot.bear {{ background: #e53935; }}
    .news-dot.neutral {{ background: #bbb; }}
    .news-source {{
        color: #999;
        font-size: 12px;
    }}
    .commodity-row {{
        display: flex;
        justify-content: space-between;
        padding: 6px 0;
        border-bottom: 1px solid #f8f8f8;
        font-size: 13px;
    }}
    .commodity-row:last-child {{ border-bottom: none; }}
    .commodity-change.up {{ color: #2e7d32; }}
    .commodity-change.down {{ color: #c62828; }}
    .t0-box {{
        background: #f8f9ff;
        border: 1px solid #e3e7f5;
        border-radius: 8px;
        padding: 16px;
        margin: 8px 0;
    }}
    .t0-strategy {{
        font-size: 16px;
        font-weight: 600;
        color: var(--accent);
        margin-bottom: 8px;
    }}
    .t0-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        font-size: 13px;
    }}
    .t0-label {{ color: #888; }}
    .t0-value {{ font-weight: 600; }}
    .event-item {{
        margin: 8px 0;
        padding-left: 12px;
        border-left: 3px solid var(--accent);
        font-size: 13px;
    }}
    .event-date {{ color: var(--accent); font-weight: 600; }}
    .event-desc {{ color: #666; margin-top: 2px; }}
    .pnl-positive {{ color: #2e7d32; font-weight: 600; }}
    .pnl-negative {{ color: #c62828; font-weight: 600; }}
    .footer {{
        text-align: center;
        color: #aaa;
        font-size: 11px;
        margin-top: 16px;
        padding-top: 12px;
    }}
    .escape-plan {{
        background: #fff3e0;
        border-radius: 6px;
        padding: 10px 14px;
        margin-top: 8px;
        font-size: 12px;
        line-height: 1.7;
        color: #e65100;
    }}
    .escape-plan-title {{
        font-weight: 600;
        margin-bottom: 4px;
    }}
</style>
</head>
<body>
    <div class="header">
        <div class="header-top">
            <div class="stock-name">华友钴业 603799</div>
            <div class="date">{today}</div>
        </div>
        <div class="action-row">
            <div class="action">{html.escape(action_word)}</div>
            <div class="action-cn">{html.escape(_extract_cn_action(action))}</div>
        </div>
        <div class="metrics">
            <div>
                <span class="metric-label">置信度</span>
                <span class="metric-value">{confidence*100:.0f}%</span>
            </div>
            <div>
                <span class="metric-label">风险</span>
                <span class="risk-badge">{html.escape(risk_level)}</span>
            </div>
        </div>
    </div>

    {html_sections}

    <div class="footer">
        华友钴业 AI Analyst — 规则引擎生成，仅供参考
    </div>
</body>
</html>"""


def _extract_cn_action(action: str) -> str:
    """Extract Chinese description from action like 'HOLD (震荡观望)'."""
    if "(" in action:
        return action.split("(")[1].rstrip(")")
    return ""


def _parse_brief_sections(text: str) -> list[tuple[str, str]]:
    """Parse the brief text into (title, content) sections."""
    sections = []
    lines = text.strip().split("\n")
    current_title = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("══") or not stripped:
            continue
        if stripped.startswith("ACTION:"):
            continue
        if stripped.startswith("CONFIDENCE:") or stripped.startswith("RISK LEVEL:"):
            continue
        if stripped.startswith("PRICE:"):
            sections.append(("PRICE", stripped))
            continue
        if stripped.startswith("── ") and stripped.endswith(" ──"):
            if current_title and current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = stripped.strip("── ").strip()
            current_lines = []
            continue
        current_lines.append(line)

    if current_title and current_lines:
        sections.append((current_title, "\n".join(current_lines)))

    return sections


def _section_icon(title: str) -> str:
    icons = {
        "PRICE": "💰",
        "TECHNICAL": "📊",
        "FUNDAMENTAL": "📈",
        "KEY CATALYSTS": "🔑",
        "NEWS SENTIMENT": "📰",
        "EXPERT OPINIONS": "🧠",
        "T+0": "⚡",
        "REGIME": "🔄",
        "BACKTEST": "🧪",
        "LEVELS": "📐",
        "卖飞应对": "🛡️",
    }
    for key, icon in icons.items():
        if key in title:
            return icon
    return "📋"


def _format_section_content(title: str, content: str) -> str:
    """Convert plain text section content into styled HTML."""
    lines = content.strip().split("\n")

    if "TECHNICAL" in title:
        return _format_signals(lines)
    if "FUNDAMENTAL" in title:
        return _format_fundamental(lines)
    if "NEWS SENTIMENT" in title:
        return _format_news(lines)
    if "CATALYSTS" in title:
        return _format_catalysts(lines)
    if "T+0" in title:
        return _format_t0(lines)
    if "REGIME" in title:
        return _format_regime(lines)
    if "BACKTEST" in title:
        return _format_backtest(lines)
    if "LEVELS" in title:
        return _format_levels(lines)
    if "PRICE" in title:
        return _format_price(content)
    if "EXPERT" in title:
        return _format_experts(lines)

    return "<br>".join(html.escape(l.strip()) for l in lines if l.strip())


def _format_price(content: str) -> str:
    parts = content.replace("PRICE:", "").strip().split("|")
    price = parts[0].strip()
    atr = parts[1].strip() if len(parts) > 1 else ""
    return (f'<div style="font-size:28px;font-weight:700;color:#1a1a2e;">¥{html.escape(price)}</div>'
            f'<div style="font-size:12px;color:#888;margin-top:4px;">{html.escape(atr)}</div>')


def _format_signals(lines: list[str]) -> str:
    out = ""
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("•"):
            s = s[1:].strip()
        css = "signal-bull" if any(k in s for k in ["金叉", "放量", "突破", "站上", "超卖"]) else \
              "signal-bear" if any(k in s for k in ["死叉", "空头", "下穿", "跌破", "超买"]) else \
              "signal-neutral"
        out += f'<div class="signal {css}">• {html.escape(s)}</div>'
    return out


def _format_fundamental(lines: list[str]) -> str:
    out = ""
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("•"):
            s = s[1:].strip()
            css = "signal-bull" if any(k in s for k in ["增长", "良好", "优秀", "健康", "回升", "性价比", "低估"]) else \
                  "signal-bear" if any(k in s for k in ["下滑", "偏高", "偏低", "不足", "恶化", "微薄", "过高"]) else \
                  "signal-neutral"
            out += f'<div class="signal {css}">• {html.escape(s)}</div>'
        elif s.startswith("→"):
            out += f'<div style="color:#666;font-size:12px;margin:2px 0 2px 8px;">{html.escape(s)}</div>'
        elif "应追踪商品" in s:
            out += f'<div style="font-weight:600;margin-top:10px;font-size:13px;">{html.escape(s)}</div>'
        else:
            out += f'<div style="font-size:13px;color:#444;margin:4px 0;">{html.escape(s)}</div>'
    return out


def _format_news(lines: list[str]) -> str:
    out = ""
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("近") and "新闻" in s:
            out += f'<div style="font-size:13px;color:#666;margin-bottom:8px;">{html.escape(s)}</div>'
        elif s.startswith("🟢") or s.startswith("🔴") or s.startswith("⚪"):
            emoji = s[0:1] if len(s) > 0 else ""
            dot_class = "bull" if "🟢" in s[:4] else ("bear" if "🔴" in s[:4] else "neutral")
            rest = s[2:].strip() if len(s) > 2 else s
            out += f'<div class="news-item"><div class="news-dot {dot_class}"></div><div>{html.escape(rest)}</div></div>'
        else:
            out += f'<div style="font-size:13px;">{html.escape(s)}</div>'
    return out


def _format_catalysts(lines: list[str]) -> str:
    out = ""
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("LME") or s.startswith("沪镍") or s.startswith("碳酸锂"):
            parts = s.split("  ")
            name_price = parts[0] if parts else s
            change = parts[-1] if len(parts) > 1 else ""
            chg_class = "up" if "+" in change else ("down" if "-" in change else "")
            out += f'<div class="commodity-row"><div>{html.escape(name_price)}</div>'
            if change and change != name_price:
                out += f'<div class="commodity-change {chg_class}">{html.escape(change)}</div>'
            out += '</div>'
        elif s.startswith("⬆") or s.startswith("⬇") or s.startswith("◆"):
            css = "signal-bull" if s.startswith("⬆") else ("signal-bear" if s.startswith("⬇") else "signal-neutral")
            out += f'<div class="signal {css}" style="font-size:12px;margin:2px 0;">{html.escape(s)}</div>'
        elif s.startswith("📅"):
            out += f'<div class="event-item"><div class="event-date">{html.escape(s[2:].strip())}</div></div>'
        elif s.startswith("关注") or s.startswith("印尼") or s.startswith("上半年") or s.startswith("中国"):
            out += f'<div class="event-item"><div class="event-desc">{html.escape(s)}</div></div>'
        else:
            out += f'<div style="font-size:13px;">{html.escape(s)}</div>'
    return out


def _format_t0(lines: list[str]) -> str:
    out = ""
    in_escape = False
    escape_lines: list[str] = []

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if "卖飞应对" in s:
            in_escape = True
            continue
        if in_escape:
            if s.startswith("→"):
                escape_lines.append(s[1:].strip())
            continue

        if s.startswith("浮盈亏"):
            pnl_class = "pnl-positive" if "+" in s.split("%")[0] else "pnl-negative"
            out += f'<div class="{pnl_class}" style="font-size:16px;margin:4px 0;">{html.escape(s)}</div>'
        elif s.startswith("策略:"):
            strategy = s.replace("策略:", "").strip()
            out += f'<div class="t0-box"><div class="t0-strategy">{html.escape(strategy)}</div><div class="t0-grid">'
        elif s.startswith("做T仓位:") or s.startswith("分批高抛:") or s.startswith("高抛区间:") or \
             s.startswith("低吸区间:") or s.startswith("止损价:"):
            label, _, value = s.partition(":")
            out += f'<div><span class="t0-label">{html.escape(label)}</span></div><div class="t0-value">{html.escape(value.strip())}</div>'
        elif s.startswith("✗"):
            out += f'<div style="font-size:16px;font-weight:600;color:#999;">{html.escape(s)}</div>'
        elif s.startswith("⚠"):
            out += f'<div style="color:#e65100;font-size:13px;margin:6px 0;">{html.escape(s)}</div>'
        elif s.startswith("•"):
            out += f'<div class="signal" style="font-size:12px;">• {html.escape(s[1:].strip())}</div>'

    if "t0-grid" in out and "</div></div>" not in out.split("t0-grid")[-1]:
        out += "</div></div>"

    if escape_lines:
        out += '<div class="escape-plan"><div class="escape-plan-title">🛡️ 卖飞应对</div>'
        for el in escape_lines:
            out += f"<div>→ {html.escape(el)}</div>"
        out += "</div>"

    return out


def _format_regime(lines: list[str]) -> str:
    out = ""
    for line in lines:
        s = line.strip()
        if s:
            out += f'<div style="font-size:13px;color:#666;margin:2px 0;">{html.escape(s)}</div>'
    return out


def _format_backtest(lines: list[str]) -> str:
    out = '<div style="font-family:monospace;font-size:12px;line-height:1.8;">'
    for line in lines:
        s = line.strip()
        if not s:
            continue
        css = "color:#43a047" if s.startswith("✓") else "color:#999"
        out += f'<div style="{css}">{html.escape(s)}</div>'
    out += "</div>"
    return out


def _format_levels(lines: list[str]) -> str:
    out = ""
    for line in lines:
        s = line.strip()
        if s:
            out += f'<div style="font-size:15px;font-weight:600;letter-spacing:1px;">{html.escape(s)}</div>'
    return out


def _format_experts(lines: list[str]) -> str:
    out = ""
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("近") or s.startswith("→"):
            out += f'<div style="font-size:13px;color:#666;margin:2px 0;">{html.escape(s)}</div>'
        elif s.startswith("["):
            out += f'<div style="font-size:13px;margin:4px 0;">{html.escape(s)}</div>'
        elif s.startswith("信号:"):
            out += f'<div style="font-size:12px;color:#888;margin-left:12px;">{html.escape(s)}</div>'
        else:
            out += f'<div style="font-size:13px;">{html.escape(s)}</div>'
    return out


def save_html_brief(brief: dict, output_dir: str | None = None) -> Path:
    """Generate HTML brief and save to file. Returns the file path."""
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefs")
    os.makedirs(output_dir, exist_ok=True)

    today = brief.get("date", date.today().isoformat())
    filepath = Path(output_dir) / f"brief-{today}.html"
    filepath.write_text(brief_to_html(brief), encoding="utf-8")
    return filepath
