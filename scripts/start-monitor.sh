#!/bin/bash
# 华友钴业 T+0 自动监控启动脚本
# 由 launchd 每个交易日 9:20 自动调用

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$DIR/.venv/bin/python"
LOG="$DIR/logs/monitor-$(date +%Y%m%d).log"

mkdir -p "$DIR/logs"

echo "=== $(date) — 华友钴业 T+0 监控启动 ===" >> "$LOG"

# Step 0: 唤醒网络 + 等待连通（macOS睡眠唤醒后WiFi可能要几分钟）
caffeinate -i -t 10 &
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
        exit 1
    fi
    if [ $((WAITED % 30)) -eq 0 ]; then
        echo "[$(date +%H:%M:%S)] Waiting for network... (${WAITED}s)" >> "$LOG"
    fi
    sleep 5
done
echo "[$(date +%H:%M:%S)] Network OK (waited ${WAITED}s)." >> "$LOG"

# Step 1: 检查是否为交易日（跳过节假日）
if "$PYTHON" -c "from data.holidays import is_trading_day; import sys; sys.exit(0 if is_trading_day() else 1)" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)] Trading day confirmed." >> "$LOG"
else
    echo "[$(date +%H:%M:%S)] Non-trading day (holiday). Exiting." >> "$LOG"
    exit 0
fi

# Step 2: 获取最新数据 + 推送晨报到微信
echo "[$(date +%H:%M:%S)] Fetching data and pushing morning brief..." >> "$LOG"
"$PYTHON" "$DIR/analyze.py" --push-brief >> "$LOG" 2>&1

# Step 3: 启动实时监控 + macOS弹窗提醒（收盘后自动退出）
echo "[$(date +%H:%M:%S)] Starting monitor with popup alerts..." >> "$LOG"
NUMBA_CACHE_DIR=/tmp/numba_cache "$PYTHON" "$DIR/monitor.py" >> "$LOG" 2>&1

echo "=== $(date) — 监控结束 ===" >> "$LOG"
