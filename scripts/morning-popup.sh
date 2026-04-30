#!/bin/bash
# Morning brief popup — triggered daily at 9:15 by launchd
# 1. Wake network  2. Run analysis  3. macOS notification  4. Open browser

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export TZ="Asia/Shanghai"

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$DIR/.venv/bin/python"
LOG="$DIR/logs/popup-$(date +%Y%m%d).log"
TODAY=$(date +%Y-%m-%d)

mkdir -p "$DIR/logs"

echo "=== $(date) — Morning brief starting ===" >> "$LOG"

# Step 0: Wake network — keep system awake for 10 minutes while we work
caffeinate -i -t 600 &
CAFFEINATE_PID=$!

# Try connecting without resetting WiFi first (reset can make things worse)
if /sbin/ping -c1 -W5 223.5.5.5 >/dev/null 2>&1 || \
   /sbin/ping -c1 -W5 114.114.114.114 >/dev/null 2>&1; then
    echo "[$(date +%H:%M:%S)] Network already up." >> "$LOG"
else
    # Only reset WiFi if ping fails
    echo "[$(date +%H:%M:%S)] No network, resetting WiFi..." >> "$LOG"
    networksetup -setairportpower en0 off 2>/dev/null; sleep 2
    networksetup -setairportpower en0 on 2>/dev/null

    MAX_WAIT=600
    WAITED=0
    while ! /sbin/ping -c1 -W5 223.5.5.5 >/dev/null 2>&1 && \
          ! /sbin/ping -c1 -W5 114.114.114.114 >/dev/null 2>&1; do
        WAITED=$((WAITED + 10))
        if [ $WAITED -ge $MAX_WAIT ]; then
            echo "[$(date +%H:%M:%S)] No network after ${MAX_WAIT}s. Aborting." >> "$LOG"
            osascript -e 'display notification "No network after 10min — brief skipped" with title "Huayou Analyst" sound name "Basso"'
            kill $CAFFEINATE_PID 2>/dev/null
            exit 1
        fi
        if [ $((WAITED % 60)) -eq 0 ]; then
            echo "[$(date +%H:%M:%S)] Waiting for network... (${WAITED}s)" >> "$LOG"
        fi
        sleep 10
    done
    echo "[$(date +%H:%M:%S)] Network OK (waited ${WAITED}s)." >> "$LOG"
fi

# Step 1: Check trading day
if "$PYTHON" -c "from data.holidays import is_trading_day; import sys; sys.exit(0 if is_trading_day() else 1)" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)] Trading day confirmed." >> "$LOG"
else
    echo "[$(date +%H:%M:%S)] Non-trading day. Skipping." >> "$LOG"
    kill $CAFFEINATE_PID 2>/dev/null
    exit 0
fi

# Step 2: Generate brief (5-minute timeout to prevent hangs)
echo "[$(date +%H:%M:%S)] Generating morning brief..." >> "$LOG"
BRIEF_OUTPUT=$(timeout 300 env NUMBA_CACHE_DIR=/tmp/numba_cache "$PYTHON" "$DIR/analyze.py" --push-brief 2>> "$LOG")
EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
    echo "[$(date +%H:%M:%S)] Brief generation timed out after 300s." >> "$LOG"
    osascript -e 'display notification "Brief timed out (5min)" with title "Huayou Analyst" sound name "Basso"'
    kill $CAFFEINATE_PID 2>/dev/null
    exit 1
fi

if [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date +%H:%M:%S)] Brief generation failed (exit $EXIT_CODE)." >> "$LOG"
    osascript -e 'display notification "Brief generation failed — check logs" with title "Huayou Analyst" sound name "Basso"'
    kill $CAFFEINATE_PID 2>/dev/null
    exit 1
fi

# Step 3: Extract key info for notification
ACTION=$(echo "$BRIEF_OUTPUT" | grep "ACTION:" | head -1 | sed 's/.*ACTION: *//')
PRICE=$(echo "$BRIEF_OUTPUT" | grep "PRICE:" | head -1 | sed 's/.*PRICE: *//' | cut -d'|' -f1 | xargs)
RISK=$(echo "$BRIEF_OUTPUT" | grep "RISK LEVEL:" | head -1 | sed 's/.*RISK LEVEL: *//')

NOTIFY_BODY="¥${PRICE} | ${ACTION} | Risk:${RISK}"

echo "[$(date +%H:%M:%S)] Brief generated: $NOTIFY_BODY" >> "$LOG"

# Step 4: macOS notification
osascript -e "display notification \"$NOTIFY_BODY\" with title \"Huayou Analyst\" subtitle \"$TODAY\" sound name \"Glass\""

# Step 5: Open dashboard
open "http://127.0.0.1:8600/brief/$TODAY"
echo "[$(date +%H:%M:%S)] Opened dashboard in browser." >> "$LOG"

kill $CAFFEINATE_PID 2>/dev/null
echo "=== $(date) — Morning brief done ===" >> "$LOG"
