# Jarvis LAN Fleet Worker

Thin peer process for MacBook / Windows PC. Talks to Mac Mini Jarvis Core over the same WiFi.

## Quick start (MacBook)

```bash
# On Mac Mini: note LAN IP, set token, optionally bind for LAN peers
# JARVIS_HOST=0.0.0.0
# JARVIS_FLEET_TOKEN=some-shared-secret

export JARVIS_CORE_URL=http://<mac-mini-lan-ip>:8787
export JARVIS_FLEET_TOKEN=some-shared-secret
export JARVIS_FLEET_NODE_NAME=MacBook
export JARVIS_FLEET_NODE_ROLE=planner
export JARVIS_FLEET_CAPABILITIES=planner,shell,ollama,cursor

python3 /Users/willy/jarvis-core/fleet-worker/worker.py
```

## Quick start (Windows PC)

```powershell
$env:JARVIS_CORE_URL="http://<mac-mini-lan-ip>:8787"
$env:JARVIS_FLEET_TOKEN="some-shared-secret"
$env:JARVIS_FLEET_NODE_NAME="Windows PC"
$env:JARVIS_FLEET_NODE_ROLE="tester"
$env:JARVIS_FLEET_CAPABILITIES="tester,shell,gpu,dual_monitor"
python fleet-worker\worker.py --tags test,gpu,shell,general
```

## Safety

Only allowlisted shell prefixes run (`echo`, `pwd`, `python`, `pytest`, …). Expand carefully.
