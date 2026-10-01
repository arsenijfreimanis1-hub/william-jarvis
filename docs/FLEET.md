# LAN Fleet

Same-WiFi compute mesh: **Mac Mini** (always-on control plane), **MacBook** (preferred planner), **Windows PC** (preferred tester / GPU).

## Roles

| Node | Role | When offline |
|------|------|--------------|
| Mac Mini | `control` — queue, memory, integrate, fallback plan | Always on |
| MacBook | `planner` | Mini plans locally; MacBook jobs stay queued |
| Windows PC | `tester` (+ GPU) | Tests queue; Mini can WoL if asleep; if powered off for cooling, wait |

## Status API

```bash
curl -s http://127.0.0.1:8787/api/fleet/status | python3 -m json.tool
```

## Register / heartbeat (peers)

```bash
curl -s -X POST http://<mini-lan-ip>:8787/api/fleet/register \
  -H 'Content-Type: application/json' \
  -H "X-Jarvis-Fleet-Token: $JARVIS_FLEET_TOKEN" \
  -d '{"name":"MacBook","role":"planner","os":"macos","capabilities":["planner","shell"]}'
```

## Enqueue a job

```bash
# Shell smoke test (any online worker)
curl -s -X POST http://127.0.0.1:8787/api/fleet/enqueue \
  -H 'Content-Type: application/json' \
  -d '{"title":"echo hello","tag":"shell","command":"echo hello"}'

# Plan prefers MacBook; falls back to Mini
curl -s -X POST http://127.0.0.1:8787/api/fleet/enqueue \
  -H 'Content-Type: application/json' \
  -d '{"title":"draft plan","tag":"plan","body":"Outline the landing page"}'

# Test waits for Windows PC (queues if offline; WoL if sleeping)
curl -s -X POST http://127.0.0.1:8787/api/fleet/enqueue \
  -H 'Content-Type: application/json' \
  -d '{"title":"run pytest","tag":"test","command":"pytest -q"}'
```

## Peer worker

See [`fleet-worker/README.md`](../fleet-worker/README.md).

```bash
JARVIS_CORE_URL=http://<mini-lan-ip>:8787 \
JARVIS_FLEET_NODE_NAME=MacBook \
JARVIS_FLEET_NODE_ROLE=planner \
python3 fleet-worker/worker.py
```

## Wake Windows PC (from Mini)

1. Enable Wake-on-LAN in BIOS and the NIC (Windows Device Manager → NIC → Power Management).
2. Set `JARVIS_FLEET_PC_MAC=AA:BB:CC:DD:EE:FF` in `.env`.
3. Run:

```bash
./scripts/wake-pc.sh
# or
curl -s -X POST http://127.0.0.1:8787/api/fleet/wake-pc
```

Fully powered-off PCs (no NIC standby) cannot wake — work stays queued until you power on.

## LAN bind (only when peers need Mini)

Default bind is `127.0.0.1`. For same-WiFi peers:

```bash
JARVIS_HOST=0.0.0.0
JARVIS_FLEET_TOKEN=pick-a-shared-secret
```

Do not expose `:8787` to the public internet. Restart Jarvis after changing env (`./scripts/restart.sh` / launchd sync).

## Config keys

| Key | Purpose |
|-----|---------|
| `JARVIS_FLEET_TOKEN` | Shared secret for peer APIs |
| `JARVIS_FLEET_HEARTBEAT_TIMEOUT_SECONDS` | Stale → offline (default 90) |
| `JARVIS_FLEET_MINI_LAN_HOST` | Mini LAN IP for docs/status |
| `JARVIS_FLEET_MACBOOK_LAN_HOST` | Optional MacBook IP |
| `JARVIS_FLEET_PC_LAN_HOST` | Optional PC IP |
| `JARVIS_FLEET_PC_MAC` | PC MAC for Wake-on-LAN |
| `JARVIS_FLEET_WOL_BROADCAST` | Default `255.255.255.255` |

## Seed fleet specialist agents

```bash
python3 scripts/seed-fleet-agents.py
curl -s http://127.0.0.1:8787/api/agents | python3 -m json.tool
```
