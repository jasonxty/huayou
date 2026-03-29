#!/bin/bash
# 华友钴业 T+0 自动监控启动脚本
# 由 launchd 每个交易日 9:20 自动调用

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$DIR/.venv/bin/python"
LOG="$DIR/logs/monitor-$(date +%Y%m%d).log"

mkdir -p "$DIR/logs"

echo "=== $(date) — 华友钴业 T+0 监控启动 ===" >> "$LOG"

# Step 1: 获取最新行情数据 + 计算指标
echo "[$(date +%H:%M:%S)] Fetching latest data..." >> "$LOG"
"$PYTHON" "$DIR/analyze.py" --fetch-only >> "$LOG" 2>&1

# Step 2: 启动实时监控（收盘后自动退出）
echo "[$(date +%H:%M:%S)] Starting monitor..." >> "$LOG"
"$PYTHON" "$DIR/monitor.py" >> "$LOG" 2>&1

echo "=== $(date) — 监控结束 ===" >> "$LOG"
