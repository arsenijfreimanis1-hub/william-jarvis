# William / Jarvis Fleet

Private home for the always-on Mac Mini agent and LAN peers (MacBook planner, Windows PC tester).

## Layout

| Path | Purpose |
|------|---------|
| [`jarvis/`](jarvis/) | FastAPI control plane (William) |
| [`macos-helper/`](macos-helper/) | Swift helper / kiosk (Mini) |
| [`fleet-worker/`](fleet-worker/) | Thin peer worker (MacBook + Windows) |
| [`devices/`](devices/) | Per-machine Cursor onboarding |
| [`docs/FLEET.md`](docs/FLEET.md) | LAN fleet how-to |
| [`docs/SYSTEMS_MAP.md`](docs/SYSTEMS_MAP.md) | Full architecture |
| [`scripts/`](scripts/) | Install, wake-PC, seed agents |
| [`openclaw-bridge/`](openclaw-bridge/) | WhatsApp bridge |

## Quick start (Mini — control plane)

```bash
cd jarvis-core   # or clone root of this repo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # fill CURSOR / fleet keys
./scripts/setup-all.sh
./scripts/seed-fleet-agents.py
```

API: `http://127.0.0.1:8787` · Fleet: `GET /api/fleet/status`

## Peers (same Wi‑Fi)

1. Clone this **private** repo on MacBook / Windows.
2. Copy [`fleet-worker/.env.peer.example`](fleet-worker/.env.peer.example) → `fleet-worker/.env` and set `JARVIS_FLEET_TOKEN` (from Mini `.env`).
3. MacBook: `./fleet-worker/run-macbook.sh`
4. Windows: `powershell -ExecutionPolicy Bypass -File .\fleet-worker\run-windows.ps1`

Do **not** commit `.env` files. Access: same GitHub account on all three devices, or invite collaborators to this private repo.
