#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER_DIR="$ROOT/macos-helper"
APP_DIR="$HELPER_DIR/JarvisHelper.app"
BINARY="$HELPER_DIR/.build/release/JarvisHelper"

read_env_value() {
  local key="$1"
  python3 - <<PY
from pathlib import Path
env_path = Path("$ROOT/.env")
key = "$key"
value = ""
if env_path.exists():
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            value = v.strip().strip('"').strip("'")
            break
print(value)
PY
}

VOICE_BACKEND="${WILLIAM_VOICE_BACKEND:-$(read_env_value WILLIAM_VOICE_BACKEND)}"
VOICE_BACKEND="${VOICE_BACKEND:-apple_legacy}"
OPENWAKEWORD_COMMAND="${WILLIAM_OPENWAKEWORD_COMMAND:-$(read_env_value WILLIAM_OPENWAKEWORD_COMMAND)}"
OPENWAKEWORD_MODEL="${WILLIAM_OPENWAKEWORD_MODEL:-$(read_env_value WILLIAM_OPENWAKEWORD_MODEL)}"
OPENWAKEWORD_THRESHOLD="${WILLIAM_OPENWAKEWORD_THRESHOLD:-$(read_env_value WILLIAM_OPENWAKEWORD_THRESHOLD)}"
WHISPER_COMMAND="${WILLIAM_WHISPER_COMMAND:-$(read_env_value WILLIAM_WHISPER_COMMAND)}"
WHISPER_MODEL="${WILLIAM_WHISPER_MODEL:-$(read_env_value WILLIAM_WHISPER_MODEL)}"
if [[ "$VOICE_BACKEND" == "local_openwakeword_whisper" ]]; then
  [[ -n "$OPENWAKEWORD_COMMAND" ]] || OPENWAKEWORD_COMMAND="$ROOT/scripts/local-wakeword.sh"
  [[ -n "$OPENWAKEWORD_MODEL" ]] || OPENWAKEWORD_MODEL="$ROOT/.voice-venv/lib/python3.12/site-packages/openwakeword/resources/models/hey_jarvis_v0.1.onnx"
  [[ -n "$OPENWAKEWORD_THRESHOLD" ]] || OPENWAKEWORD_THRESHOLD="0.62"
  [[ -n "$WHISPER_COMMAND" ]] || WHISPER_COMMAND="$ROOT/scripts/local-whisper-transcribe.sh"
  [[ -n "$WHISPER_MODEL" ]] || WHISPER_MODEL="$ROOT/models/whisper/ggml-base.en.bin"
fi

chmod +x "$ROOT/scripts/local-wakeword.sh" "$ROOT/scripts/local-whisper-transcribe.sh" 2>/dev/null || true

cd "$HELPER_DIR"
swift build -c release

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources/Sounds"
cp "$BINARY" "$APP_DIR/Contents/MacOS/JarvisHelper"
chmod +x "$APP_DIR/Contents/MacOS/JarvisHelper"
if [ -d "$HELPER_DIR/Resources/Sounds" ]; then
  cp "$HELPER_DIR/Resources/Sounds/"*.caf "$APP_DIR/Contents/Resources/Sounds/" 2>/dev/null || true
fi
codesign --force --sign - --identifier com.willy.jarvis-helper --deep "$APP_DIR"

cat > "$APP_DIR/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>JarvisHelper</string>
  <key>CFBundleIdentifier</key>
  <string>com.willy.jarvis-helper</string>
  <key>CFBundleName</key>
  <string>JarvisHelper</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>William Agent listens for "Hey Willy" to assist you.</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>William Agent uses speech recognition to hear wake words and commands.</string>
  <key>NSScreenCaptureUsageDescription</key>
  <string>William Agent observes your screen to provide context and handle permission dialogs.</string>
</dict>
</plist>
EOF

# App icon (SVG → PNG → icns, sets CFBundleIconFile) — re-sign so the bundle stays valid
python3 "$ROOT/jarvis/services/app_icons.py" --target "$APP_DIR" --name "Jarvis Helper" >/dev/null \
  || echo "warning: app icon generation skipped"
codesign --force --sign - --identifier com.willy.jarvis-helper --deep "$APP_DIR"

PLIST_DST="$HOME/Library/LaunchAgents/com.willy.jarvis-helper.plist"
cat > "$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.willy.jarvis-helper</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>ProgramArguments</key>
    <array>
      <string>$APP_DIR/Contents/MacOS/JarvisHelper</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
      <key>WILLIAM_VOICE_BACKEND</key>
      <string>$VOICE_BACKEND</string>
      <key>WILLIAM_OPENWAKEWORD_COMMAND</key>
      <string>$OPENWAKEWORD_COMMAND</string>
      <key>WILLIAM_OPENWAKEWORD_MODEL</key>
      <string>$OPENWAKEWORD_MODEL</string>
      <key>WILLIAM_OPENWAKEWORD_THRESHOLD</key>
      <string>$OPENWAKEWORD_THRESHOLD</string>
      <key>WILLIAM_WHISPER_COMMAND</key>
      <string>$WHISPER_COMMAND</string>
      <key>WILLIAM_WHISPER_MODEL</key>
      <string>$WHISPER_MODEL</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>$HELPER_DIR</string>
    <key>StandardOutPath</key>
    <string>$ROOT/logs/helper.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/helper.err.log</string>
  </dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/com.willy.jarvis-helper" 2>/dev/null || true
pkill -f "JarvisHelper.app/Contents/MacOS/JarvisHelper" 2>/dev/null || true
pkill -f "local_voice_openwakeword.py" 2>/dev/null || true
sleep 1
if ! launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null; then
  launchctl bootout "gui/$(id -u)/com.willy.jarvis-helper" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
fi
launchctl enable "gui/$(id -u)/com.willy.jarvis-helper" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/com.willy.jarvis-helper" 2>/dev/null || \
  launchctl kickstart "gui/$(id -u)/com.willy.jarvis-helper"

echo "macOS helper running on http://127.0.0.1:8788"
echo "Grant mic + speech once: ./scripts/grant-voice-permissions.sh"
echo "Voice backend: $VOICE_BACKEND"
if [[ "$VOICE_BACKEND" == "local_openwakeword_whisper" ]]; then
  echo "Local wake command: ${OPENWAKEWORD_COMMAND:-<unset>}"
  echo "Local wake model: ${OPENWAKEWORD_MODEL:-<unset>}"
  echo "Local wake threshold: ${OPENWAKEWORD_THRESHOLD:-<unset>}"
  echo "Local whisper command: ${WHISPER_COMMAND:-<unset>}"
fi
