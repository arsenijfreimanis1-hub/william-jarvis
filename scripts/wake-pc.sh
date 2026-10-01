#!/usr/bin/env bash
# Wake the Windows PC via Wake-on-LAN (same LAN as Mac Mini).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/jarvis-paths.sh" 2>/dev/null || true
cd "$ROOT"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
python3 - <<'PY'
import asyncio
from jarvis.services import fleet_power

async def main():
    result = await fleet_power.wake_pc()
    print(result)
    raise SystemExit(0 if result.get("ok") else 1)

asyncio.run(main())
PY
