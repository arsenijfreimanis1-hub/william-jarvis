#!/usr/bin/env bash
# Sync jarvis-core/.env values into the launchd plist (no secret printing).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/jarvis-paths.sh
source "$ROOT/scripts/jarvis-paths.sh"
PLIST_SRC="$ROOT/launchd/com.willy.jarvis-core.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.willy.jarvis-core.plist"
ENV_FILE="$ROOT/.env"

read_env() {
  local key="$1"
  local default="${2:-}"
  if [[ -f "$ENV_FILE" ]]; then
    local line val
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%%#*}"
      line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
      [[ -z "$line" ]] && continue
      if [[ "$line" == "$key="* ]]; then
        val="${line#*=}"
        val="${val%\"}"; val="${val#\"}"
        val="${val%\'}"; val="${val#\'}"
        echo "$val"
        return 0
      fi
    done < "$ENV_FILE"
  fi
  echo "$default"
}

JARVIS_HOST="$(read_env JARVIS_HOST "127.0.0.1")"
JARVIS_PORT="$(read_env JARVIS_PORT "8787")"
OLLAMA_MODEL="$(read_env JARVIS_OLLAMA_MODEL "$(read_env OLLAMA_MODEL llama3.1:8b)")"
CURSOR_KEY="$(read_env CURSOR_API_KEY "")"
JARVIS_CURSOR_KEY="$(read_env JARVIS_CURSOR_API_KEY "")"
KEY="${JARVIS_CURSOR_KEY:-$CURSOR_KEY}"
NOTION_KEY="$(read_env JARVIS_NOTION_API_KEY "$(read_env NOTION_API_KEY "")")"
NOTION_PAGE="$(read_env JARVIS_NOTION_PARENT_PAGE_ID "")"
NOTION_INTERVAL="$(read_env JARVIS_NOTION_EXPORT_INTERVAL_HOURS "12")"

cp "$PLIST_SRC" "$PLIST_DST"

/usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:JARVIS_OLLAMA_MODEL" "$PLIST_DST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_OLLAMA_MODEL string $OLLAMA_MODEL" "$PLIST_DST"

/usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:CURSOR_API_KEY" "$PLIST_DST" 2>/dev/null || true
if [[ -n "$KEY" && "$KEY" != "cursor_..." && "$KEY" != *"..." ]]; then
  /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:CURSOR_API_KEY string $KEY" "$PLIST_DST"
fi

for var in JARVIS_NOTION_API_KEY JARVIS_NOTION_PARENT_PAGE_ID JARVIS_NOTION_EXPORT_INTERVAL_HOURS; do
  /usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:$var" "$PLIST_DST" 2>/dev/null || true
done
if [[ -n "$NOTION_KEY" && "$NOTION_KEY" != secret_* ]]; then
  /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_NOTION_API_KEY string $NOTION_KEY" "$PLIST_DST"
fi
if [[ -n "$NOTION_PAGE" ]]; then
  /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_NOTION_PARENT_PAGE_ID string $NOTION_PAGE" "$PLIST_DST"
fi
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_NOTION_EXPORT_INTERVAL_HOURS string $NOTION_INTERVAL" "$PLIST_DST"

/usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:JARVIS_DATA_DIR" "$PLIST_DST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_DATA_DIR string $JARVIS_DATA_DIR" "$PLIST_DST"
/usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:JARVIS_LOGS_DIR" "$PLIST_DST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_LOGS_DIR string $JARVIS_LOGS_DIR" "$PLIST_DST"
/usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:JARVIS_WORKSPACE_DIR" "$PLIST_DST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_WORKSPACE_DIR string $JARVIS_ROOT" "$PLIST_DST"

# LAN fleet token + Mini host (peers authenticate with this)
FLEET_TOKEN="$(read_env JARVIS_FLEET_TOKEN "")"
FLEET_MINI_HOST="$(read_env JARVIS_FLEET_MINI_LAN_HOST "127.0.0.1")"
FLEET_PC_MAC="$(read_env JARVIS_FLEET_PC_MAC "")"
for var in JARVIS_FLEET_TOKEN JARVIS_FLEET_MINI_LAN_HOST JARVIS_FLEET_PC_MAC JARVIS_FLEET_HEARTBEAT_TIMEOUT_SECONDS; do
  /usr/libexec/PlistBuddy -c "Delete :EnvironmentVariables:$var" "$PLIST_DST" 2>/dev/null || true
done
if [[ -n "$FLEET_TOKEN" ]]; then
  /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_FLEET_TOKEN string $FLEET_TOKEN" "$PLIST_DST"
fi
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_FLEET_MINI_LAN_HOST string $FLEET_MINI_HOST" "$PLIST_DST"
if [[ -n "$FLEET_PC_MAC" ]]; then
  /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_FLEET_PC_MAC string $FLEET_PC_MAC" "$PLIST_DST"
fi
HB="$(read_env JARVIS_FLEET_HEARTBEAT_TIMEOUT_SECONDS "90")"
/usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:JARVIS_FLEET_HEARTBEAT_TIMEOUT_SECONDS string $HB" "$PLIST_DST"

# uvicorn --host / --port in ProgramArguments (indices 5 and 7)
/usr/libexec/PlistBuddy -c "Set :ProgramArguments:5 $JARVIS_HOST" "$PLIST_DST"
/usr/libexec/PlistBuddy -c "Set :ProgramArguments:7 $JARVIS_PORT" "$PLIST_DST"

/usr/libexec/PlistBuddy -c "Set :StandardOutPath $JARVIS_LOGS_DIR/jarvis.log" "$PLIST_DST"
/usr/libexec/PlistBuddy -c "Set :StandardErrorPath $JARVIS_LOGS_DIR/jarvis.err.log" "$PLIST_DST"

echo "Synced launchd env → $PLIST_DST"
