#!/bin/bash
# 华友钴业 T+0 自动监控启动脚本
# 由 launchd 每个交易日 9:20 自动调用

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$DIR/.venv/bin/python"
LOG="$DIR/logs/monitor-$(date +%Y%m%d).log"

mkdir -p "$DIR/logs"

echo "=== $(date) — 华友钴业 T+0 监控启动 ===" >> "$LOG"

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

# Step 3: 启动实时监控（收盘后自动退出）
echo "[$(date +%H:%M:%S)] Starting monitor..." >> "$LOG"
"$PYTHON" "$DIR/monitor.py" >> "$LOG" 2>&1

echo "=== $(date) — 监控结束 ===" >> "$LOG"
