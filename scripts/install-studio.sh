#!/usr/bin/env bash
# Build and install William Studio (native SwiftUI: intake, call graph, system, journal, IDE, settings).
# Usage: scripts/install-studio.sh [--no-launch]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER_DIR="$ROOT/macos-helper"
APP_DIR="$HELPER_DIR/WilliamStudio.app"
DESKTOP_APP="$HOME/Desktop/William Studio.app"
APPS_APP="$HOME/Applications/William Studio.app"
BINARY="$HELPER_DIR/.build/release/WilliamStudio"
LAUNCH=1
[[ "${1:-}" == "--no-launch" ]] && LAUNCH=0

cd "$HELPER_DIR"
echo "Building William Studio (release)…"
swift build -c release --product WilliamStudio 2>&1 | grep -E "error|Build of product|Compiling WilliamStudio" || true
[[ -x "$BINARY" ]] || { echo "build failed: $BINARY missing" >&2; exit 1; }

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BINARY" "$APP_DIR/Contents/MacOS/WilliamStudio"
chmod +x "$APP_DIR/Contents/MacOS/WilliamStudio"
# SwiftTerm resource bundle (if SwiftPM emitted one)
for b in "$HELPER_DIR"/.build/release/*.bundle; do
  [[ -d "$b" ]] && cp -R "$b" "$APP_DIR/Contents/Resources/" || true
done

cat > "$APP_DIR/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>WilliamStudio</string>
  <key>CFBundleIdentifier</key>
  <string>com.willy.william-studio</string>
  <key>CFBundleName</key>
  <string>William Studio</string>
  <key>CFBundleDisplayName</key>
  <string>William Studio</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>LSUIElement</key>
  <false/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>William Studio transcribes your voice on-device so prompts can be spoken instead of typed.</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>William Studio listens while you hold push-to-talk.</string>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
    <key>NSAllowsArbitraryLoads</key>
    <true/>
  </dict>
</dict>
</plist>
EOF

python3 "$ROOT/jarvis/services/app_icons.py" --target "$APP_DIR" --name "William Studio" >/dev/null 2>&1 \
  || echo "warning: app icon generation skipped"

codesign --force --sign - --identifier com.willy.william-studio --deep "$APP_DIR"

rm -rf "$DESKTOP_APP" "$APPS_APP"
mkdir -p "$HOME/Applications"
ditto "$APP_DIR" "$APPS_APP"
ln -sfn "$APPS_APP" "$DESKTOP_APP" 2>/dev/null || ditto "$APP_DIR" "$DESKTOP_APP"

echo "William Studio installed."
echo "  App:     $APPS_APP"
echo "  Desktop: $DESKTOP_APP"
echo "First launch: grant Speech Recognition + Microphone when asked (push-to-talk is on-device)."

if [[ $LAUNCH -eq 1 ]]; then
  pkill -f "William Studio.app/Contents/MacOS/WilliamStudio" 2>/dev/null || true
  pkill -f "WilliamStudio.app/Contents/MacOS/WilliamStudio" 2>/dev/null || true
  sleep 0.5
  open "$APPS_APP"
fi
