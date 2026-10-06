#!/usr/bin/env bash
# One-line MacBook bootstrap for the William two-device setup (role=macbook).
#
#   curl -fsSL http://<mini-host>:8787/api/bootstrap/macbook.sh | bash
#
# The Mini fills __MINI_HOST__ / __FLEET_TOKEN__ when serving this file. Idempotent: safe to re-run.
# Installs: Homebrew (if missing), python3.12, git, ollama (optional small model), clones the private
# repo, writes .env with role=macbook + link to the Mini, installs the launchd agent, and builds
# William Studio when Swift tools are present.
set -euo pipefail

MINI_HOST="${MINI_HOST:-__MINI_HOST__}"
FLEET_TOKEN="${JARVIS_FLEET_TOKEN:-__FLEET_TOKEN__}"
REPO_URL="${WILLIAM_REPO_URL:-git@github.com:arsenijfreimanis1-hub/william-jarvis.git}"
TARGET="${WILLIAM_HOME:-$HOME/jarvis-core}"
BRANCH="${WILLIAM_BRANCH:-fleet/lan-foundation}"
WITH_OLLAMA="${WITH_OLLAMA:-1}"

say() { printf '\033[1;36m[william]\033[0m %s\n' "$*"; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This bootstrap targets macOS."; exit 1
fi
if [[ "$MINI_HOST" == "__MINI_HOST__" || -z "$MINI_HOST" ]]; then
  echo "MINI_HOST is unknown. Run: MINI_HOST=<mini-tailscale-or-lan-host> bash $0"; exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  say "Installing Homebrew"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
say "Installing python3.12 + git"
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
brew list git >/dev/null 2>&1 || brew install git
if [[ "$WITH_OLLAMA" == "1" ]]; then
  brew list ollama >/dev/null 2>&1 || brew install ollama
  brew services start ollama >/dev/null 2>&1 || true
fi
if ! command -v tailscale >/dev/null 2>&1 && [[ ! -d /Applications/Tailscale.app ]]; then
  say "Installing Tailscale (log in once from the menu bar)"
  brew install --cask tailscale || true
fi

if [[ ! -d "$TARGET/.git" ]]; then
  say "Cloning $REPO_URL → $TARGET"
  git clone "$REPO_URL" "$TARGET"
fi
cd "$TARGET"
git fetch -q origin || true
git checkout -q "$BRANCH" 2>/dev/null || true
git pull -q --ff-only || true

say "Python environment"
PY="/opt/homebrew/bin/python3.12"; [[ -x "$PY" ]] || PY="python3"
[[ -d .venv ]] || "$PY" -m venv .venv
.venv/bin/pip install -q -r requirements.txt

say "Syncing skills"
./scripts/sync-skills.sh >/dev/null 2>&1 || say "skills sync skipped"

say "Writing .env (role=macbook)"
touch .env
_set() { # key value
  if grep -q "^$1=" .env; then sed -i '' "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
_set JARVIS_ROLE macbook
_set JARVIS_DEVICE_NAME "$(scutil --get ComputerName 2>/dev/null | tr ' ' '-' || hostname -s)"
_set JARVIS_HOST 127.0.0.1
_set JARVIS_PORT 8787
_set JARVIS_FLEET_TOKEN "$FLEET_TOKEN"
_set JARVIS_LINK_PEER_URL "ws://$MINI_HOST:8787/ws/link"
_set JARVIS_FLEET_MINI_LAN_HOST "$MINI_HOST"
_set JARVIS_GATEWAY_MODE local_first
_set JARVIS_OLLAMA_MODEL llama3.2:3b
_set JARVIS_SCREEN_WATCH_ENABLED false
chmod 600 .env

if [[ "$WITH_OLLAMA" == "1" ]] && command -v ollama >/dev/null 2>&1; then
  say "Pulling small local model (llama3.2:3b) in background"
  (ollama pull llama3.2:3b >/dev/null 2>&1 || true) &
fi

say "Installing launchd agent"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/Jarvis" logs data
PLIST="$HOME/Library/LaunchAgents/com.willy.jarvis-core.plist"
sed -e "s|/Users/willy/jarvis-core|$TARGET|g" -e "s|/Users/willy|$HOME|g" launchd/com.willy.jarvis-core.plist > "$PLIST"
launchctl bootout "gui/$(id -u)/com.willy.jarvis-core" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/com.willy.jarvis-core" || true

if command -v swift >/dev/null 2>&1 && [[ -f macos-helper/Package.swift ]] && [[ -x scripts/install-studio.sh ]]; then
  say "Building William Studio"
  ./scripts/install-studio.sh || say "Studio build skipped (see output above)"
fi

sleep 2
if curl -fsS http://127.0.0.1:8787/api/health >/dev/null 2>&1; then
  say "JarvisCore (role=macbook) is up on :8787 and dialing ws://$MINI_HOST:8787/ws/link"
  curl -fsS http://127.0.0.1:8787/api/link/status || true; echo
else
  say "Core not answering yet. Check ~/Library/Logs/Jarvis/jarvis.err.log"
fi
say "Done. Open Tailscale and sign in if you have not; the link reconnects automatically."
