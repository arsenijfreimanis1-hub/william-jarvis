#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER_DIR="$ROOT/macos-helper"
APP_DIR="$HELPER_DIR/WilliamKiosk.app"
DESKTOP_APP="$HOME/Desktop/William Agent.app"
BINARY="$HELPER_DIR/.build/release/WilliamKiosk"

cd "$HELPER_DIR"
swift build -c release --product WilliamKiosk

mkdir -p "$APP_DIR/Contents/MacOS"
cp "$BINARY" "$APP_DIR/Contents/MacOS/WilliamKiosk"
chmod +x "$APP_DIR/Contents/MacOS/WilliamKiosk"

cat > "$APP_DIR/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>WilliamKiosk</string>
  <key>CFBundleIdentifier</key>
  <string>com.willy.william-kiosk</string>
  <key>CFBundleName</key>
  <string>William Agent</string>
  <key>CFBundleDisplayName</key>
  <string>William Agent</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>LSUIElement</key>
  <false/>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

# App icon (SVG → PNG → icns, sets CFBundleIconFile)
python3 "$ROOT/jarvis/services/app_icons.py" --target "$APP_DIR" --name "William Agent" >/dev/null \
  || echo "warning: app icon generation skipped"

codesign --force --sign - --identifier com.willy.william-kiosk --deep "$APP_DIR"

rm -rf "$DESKTOP_APP"
ditto "$APP_DIR" "$DESKTOP_APP"

mkdir -p "$ROOT/logs"
PLIST_DST="$HOME/Library/LaunchAgents/com.willy.william-kiosk.plist"
cat > "$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.willy.william-kiosk</string>
    <key>Comment</key>
    <string>William Agent fullscreen kiosk home screen</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>ProgramArguments</key>
    <array>
      <string>$APP_DIR/Contents/MacOS/WilliamKiosk</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
      <key>AppleInterfaceStyle</key>
      <string>Dark</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>$HELPER_DIR</string>
    <key>StandardOutPath</key>
    <string>$ROOT/logs/kiosk.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/kiosk.err.log</string>
  </dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/com.willy.william-kiosk" 2>/dev/null || true
pkill -f "WilliamKiosk.app/Contents/MacOS/WilliamKiosk" 2>/dev/null || true
pkill -f ".build/release/WilliamKiosk" 2>/dev/null || true
sleep 1
if ! launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null; then
  launchctl bootout "gui/$(id -u)/com.willy.william-kiosk" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
fi
launchctl enable "gui/$(id -u)/com.willy.william-kiosk" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/com.willy.william-kiosk" 2>/dev/null || \
  launchctl kickstart "gui/$(id -u)/com.willy.william-kiosk"

echo "William Agent kiosk installed."
echo "  Desktop: $DESKTOP_APP"
echo "  Bundle:  $APP_DIR"
echo "  Logs:    $ROOT/logs/kiosk.log"

echo "Restarting JarvisCore + helper (loads latest API + voice)…"
launchctl kickstart -k "gui/$(id -u)/com.willy.jarvis-core" 2>/dev/null || "$ROOT/scripts/install.sh"
sleep 2
"$ROOT/scripts/install-helper.sh" 2>/dev/null || launchctl kickstart -k "gui/$(id -u)/com.willy.jarvis-helper" 2>/dev/null || true
sleep 1
launchctl kickstart -k "gui/$(id -u)/com.willy.william-kiosk" 2>/dev/null || true
