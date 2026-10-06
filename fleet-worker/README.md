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

## Ask the free-model gateway from any device

Both machines call the Mac Mini's single backend; API keys never leave the Mini.

```bash
export JARVIS_CORE_URL=http://<mac-mini-lan-ip>:8787
export JARVIS_FLEET_TOKEN=some-shared-secret

python3 fleet-worker/ask.py "write a React navbar"              # → Codestral / Gemini / Groq
python3 fleet-worker/ask.py --cap long_context -f "src/*.py" "find the bug"   # Gemini 1M context
python3 fleet-worker/ask.py --stt command.wav                   # Groq Whisper
python3 fleet-worker/ask.py --tts "Hello boss" -o hello.aiff    # HF TTS / macOS say
python3 fleet-worker/ask.py --cad "20mm cube with a 5mm hole"   # OpenSCAD source (+ .stl on the Mini)
python3 fleet-worker/ask.py --status                            # keys present / missing + usage
```

Endpoints: `POST /api/gateway/chat`, `/api/gateway/fim`, `/api/gateway/embed`, `/api/speech/transcribe`,
`/api/speech/tts`, `/api/cad/generate`. See `docs/PROVIDERS.md`.

## Safety

Only allowlisted shell prefixes run (`echo`, `pwd`, `python`, `pytest`, …). Expand carefully.
