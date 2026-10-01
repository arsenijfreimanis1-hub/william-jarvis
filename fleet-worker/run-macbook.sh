#!/usr/bin/env bash
# MacBook peer worker — loads secrets from fleet-worker/.env (not committed)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy .env.peer.example to .env and fill JARVIS_FLEET_TOKEN"
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
export JARVIS_CORE_URL="${JARVIS_CORE_URL:-http://192.168.178.159:8787}"
export JARVIS_FLEET_NODE_NAME="${JARVIS_FLEET_NODE_NAME:-MacBook}"
export JARVIS_FLEET_NODE_ROLE="${JARVIS_FLEET_NODE_ROLE:-planner}"
export JARVIS_FLEET_CAPABILITIES="${JARVIS_FLEET_CAPABILITIES:-planner,shell,ollama,cursor}"
if [[ -z "${JARVIS_FLEET_TOKEN:-}" ]]; then
  echo "JARVIS_FLEET_TOKEN is empty in .env"
  exit 1
fi
exec python3 "$DIR/worker.py" "$@"
