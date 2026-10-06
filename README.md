# William / Jarvis Fleet

Private home for the always-on Mac Mini agent and LAN peers (MacBook planner, Windows PC tester).

## Layout

| Path | Purpose |
|------|---------|
| [`jarvis/`](jarvis/) | FastAPI control plane (William) |
| [`macos-helper/`](macos-helper/) | Swift helper / kiosk (Mini) |
| [`fleet-worker/`](fleet-worker/) | Thin peer worker (MacBook + Windows) |
| [`devices/`](devices/) | Per-machine Cursor onboarding |
| [`jarvis/services/providers/`](jarvis/services/providers/) | Free AI provider gateway (Gemini, Codestral, Groq, OpenRouter, HF → Ollama) |
| [`docs/FLEET.md`](docs/FLEET.md) | LAN fleet how-to |
| [`docs/PROVIDERS.md`](docs/PROVIDERS.md) | Free keys, routing chains, usage ledger, speech, memory, CAD, app icons |
| [`docs/SYSTEMS_MAP.md`](docs/SYSTEMS_MAP.md) | Full architecture |
| [`scripts/`](scripts/) | Install, wake-PC, seed agents, apply app icons |
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

API: `http://127.0.0.1:8787` · Fleet: `GET /api/fleet/status` · Providers: `GET /api/providers`

## Free AI keys (one backend powers every device)

William routes each task to the best **free** model and falls back to Ollama. Add keys once on the Mini:

```bash
# Gemini 2.5 Flash (1M ctx), Codestral, Groq (+Whisper), Hugging Face, OpenRouter — see docs/PROVIDERS.md
curl -s -X POST :8787/api/providers/keys -H 'Content-Type: application/json' -d '{"provider":"groq","key":"gsk_..."}'
python3 fleet-worker/ask.py --status            # from any device: keys present / missing + usage
python3 fleet-worker/ask.py "build a React navbar"
```

Key Scout scans env / `~/.config/jarvis/keys.env` / Keychain every 30 min; Quota Keeper tracks usage against free limits.

## Peers (same Wi‑Fi)

1. Clone this **private** repo on MacBook / Windows.
2. Copy [`fleet-worker/.env.peer.example`](fleet-worker/.env.peer.example) → `fleet-worker/.env` and set `JARVIS_FLEET_TOKEN` (from Mini `.env`).
3. MacBook: `./fleet-worker/run-macbook.sh`
4. Windows: `powershell -ExecutionPolicy Bypass -File .\fleet-worker\run-windows.ps1`

Do **not** commit `.env` files. Access: same GitHub account on all three devices, or invite collaborators to this private repo.
