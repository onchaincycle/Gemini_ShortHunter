#!/usr/bin/env bash
# Script update berkala HTML Dashboard ShortHunter & Push ke GitHub
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO_DIR="/Users/imansulaiman/Documents/Gemini_ShortHunter"
cd "$REPO_DIR" || exit 1

mkdir -p "$REPO_DIR/logs"

echo "=== Cron Sync Started: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$REPO_DIR/logs/github_sync_cron.log"
"$REPO_DIR/.venv/bin/python" "$REPO_DIR/src/github_sync.py" >> "$REPO_DIR/logs/github_sync_cron.log" 2>&1
echo "=== Cron Sync Finished: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$REPO_DIR/logs/github_sync_cron.log"
