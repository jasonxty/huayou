#!/bin/bash
# 华友钴业晨报弹窗 — 每日 9:15 由 launchd 触发
# 1. 等待网络  2. 运行分析  3. 生成HTML  4. macOS弹窗通知  5. 打开浏览器

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$DIR/.venv/bin/python"
LOG="$DIR/logs/popup-$(date +%Y%m%d).log"
BRIEFS_DIR="$DIR/briefs"
TODAY=$(date +%Y-%m-%d)
HTML_FILE="$BRIEFS_DIR/brief-$TODAY.html"

mkdir -p "$DIR/logs" "$BRIEFS_DIR"

echo "=== $(date) — 晨报弹窗启动 ===" >> "$LOG"

# Step 0: 唤醒网络 + 等待连通
# macOS从睡眠唤醒后WiFi可能需要较长时间恢复，等5分钟
caffeinate -i -t 10 &  # 阻止系统立即回到睡眠
networksetup -setairportpower en0 off 2>/dev/null; sleep 1
networksetup -setairportpower en0 on 2>/dev/null
echo "[$(date +%H:%M:%S)] WiFi reset triggered, waiting for connection..." >> "$LOG"

MAX_WAIT=300
WAITED=0
while ! /sbin/ping -c1 -W3 223.5.5.5 >/dev/null 2>&1 && \
      ! /sbin/ping -c1 -W3 114.114.114.114 >/dev/null 2>&1; do
    WAITED=$((WAITED + 5))
    if [ $WAITED -ge $MAX_WAIT ]; then
        echo "[$(date +%H:%M:%S)] No network after ${MAX_WAIT}s. Aborting." >> "$LOG"
        osascript -e 'display notification "网络不通(等待5分钟)，晨报生成失败" with title "华友钴业" sound name "Basso"'
        exit 1
    fi
    if [ $((WAITED % 30)) -eq 0 ]; then
        echo "[$(date +%H:%M:%S)] Waiting for network... (${WAITED}s)" >> "$LOG"
    fi
    sleep 5
done
echo "[$(date +%H:%M:%S)] Network OK (waited ${WAITED}s)." >> "$LOG"

# Step 1: 检查是否为交易日
if "$PYTHON" -c "from data.holidays import is_trading_day; import sys; sys.exit(0 if is_trading_day() else 1)" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)] Trading day confirmed." >> "$LOG"
else
    echo "[$(date +%H:%M:%S)] Non-trading day. Skipping." >> "$LOG"
    exit 0
fi

# Step 2: 生成晨报 + 推送微信
echo "[$(date +%H:%M:%S)] Generating morning brief..." >> "$LOG"
BRIEF_OUTPUT=$(NUMBA_CACHE_DIR=/tmp/numba_cache "$PYTHON" "$DIR/analyze.py" --push-brief 2>> "$LOG")
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date +%H:%M:%S)] Brief generation failed (exit $EXIT_CODE)." >> "$LOG"
    osascript -e 'display notification "晨报生成失败，请查看日志" with title "华友钴业" sound name "Basso"'
    exit 1
fi

# Step 3: 提取关键信息用于通知弹窗
ACTION=$(echo "$BRIEF_OUTPUT" | grep "ACTION:" | head -1 | sed 's/.*ACTION: *//')
PRICE=$(echo "$BRIEF_OUTPUT" | grep "PRICE:" | head -1 | sed 's/.*PRICE: *//' | cut -d'|' -f1 | xargs)
RISK=$(echo "$BRIEF_OUTPUT" | grep "RISK LEVEL:" | head -1 | sed 's/.*RISK LEVEL: *//')

NOTIFY_BODY="¥${PRICE} | ${ACTION} | 风险:${RISK}"

echo "[$(date +%H:%M:%S)] Brief generated: $NOTIFY_BODY" >> "$LOG"

# Step 4: macOS 通知弹窗
osascript -e "display notification \"$NOTIFY_BODY\" with title \"📈 华友钴业晨报\" subtitle \"$TODAY\" sound name \"Glass\""

# Step 5: 打开 Dashboard 网页
open "http://127.0.0.1:8600/brief/$TODAY"
echo "[$(date +%H:%M:%S)] Opened dashboard in browser." >> "$LOG"

echo "=== $(date) — 晨报弹窗完成 ===" >> "$LOG"
