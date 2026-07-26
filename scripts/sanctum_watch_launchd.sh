#!/usr/bin/env bash
# Launchd wrapper for SOL-007 Sanctum/JitoSOL/INF health checks.
# Keeps the 30-minute watch out of the LLM-agent cron path.

set -uo pipefail

DASH="/Users/clayton/sol007-dashboard"
WATCH_SCRIPT="$DASH/scripts/sanctum_watch.sh"
LOG_DIR="$DASH/logs"
LOG_FILE="$LOG_DIR/sanctum-watch.log"
INCIDENT_LOG="/Users/clayton/.openclaw/workspace/memory/solana-trading/pilot-incidents.log"
OPENCLAW="/opt/homebrew/bin/openclaw"
ALERT_TARGET="1477258172"

mkdir -p "$LOG_DIR" "$(dirname "$INCIDENT_LOG")"

ts() {
  /bin/date -u +"%Y-%m-%dT%H:%M:%SZ"
}

run_ts="$(ts)"
output="$("$WATCH_SCRIPT" 2>&1)"
status=$?

printf '%s status=%s %s\n' "$run_ts" "$status" "$output" >> "$LOG_FILE"

case "$output" in
  STATUS=BREACH*)
    printf '%s %s\n' "$run_ts" "$output" >> "$INCIDENT_LOG"
    "$OPENCLAW" message send \
      --channel telegram \
      --target "$ALERT_TARGET" \
      --message "SOL-007 BREACH: $output" >> "$LOG_FILE" 2>&1 || true
    ;;
esac

exit "$status"
