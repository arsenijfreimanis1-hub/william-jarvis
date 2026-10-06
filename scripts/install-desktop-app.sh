#!/usr/bin/env bash
# Build William Agent desktop app (native window → local chat UI). No Cursor IDE required.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/jarvis-paths.sh
source "$ROOT/scripts/jarvis-paths.sh"
HELPER_DIR="$ROOT/macos-helper"
APP_DIR="$HELPER_DIR/WilliamDesktop.app"
DESKTOP_APP="$HOME/Desktop/William Agent.app"
BINARY="$HELPER_DIR/.build/release/WilliamDesktop"
LOGS="$JARVIS_LOGS_DIR"

cd "$HELPER_DIR"
echo "Building WilliamDesktop…"
swift build -c release --product WilliamDesktop

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BINARY" "$APP_DIR/Contents/MacOS/WilliamDesktop"
chmod +x "$APP_DIR/Contents/MacOS/WilliamDesktop"

cat > "$APP_DIR/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>WilliamDesktop</string>
  <key>CFBundleIdentifier</key>
  <string>com.willy.william-desktop</string>
  <key>CFBundleName</key>
  <string>William Agent</string>
  <key>CFBundleDisplayName</key>
  <string>William Agent</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

# App icon (SVG → PNG → icns, sets CFBundleIconFile)
python3 "$ROOT/jarvis/services/app_icons.py" --target "$APP_DIR" --name "William Agent" >/dev/null \
  || echo "warning: app icon generation skipped"

codesign --force --sign - --identifier com.willy.william-desktop --deep "$APP_DIR" 2>/dev/null || true

rm -rf "$DESKTOP_APP"
ditto "$APP_DIR" "$DESKTOP_APP"

mkdir -p "$LOGS"
PLIST_DST="$HOME/Library/LaunchAgents/com.willy.william-desktop.plist"
cat > "$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.willy.william-desktop</string>
    <key>Comment</key>
    <string>William Agent desktop chat window</string>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <false/>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>ProgramArguments</key>
    <array>
      <string>$APP_DIR/Contents/MacOS/WilliamDesktop</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$HELPER_DIR</string>
    <key>StandardOutPath</key>
    <string>$LOGS/william-desktop.log</string>
    <key>StandardErrorPath</key>
    <string>$LOGS/william-desktop.err.log</string>
  </dict>
</plist>
EOF

open "$DESKTOP_APP" 2>/dev/null || true

echo ""
echo "William Agent installed on Desktop."
echo "  App:     $DESKTOP_APP"
echo "  Bundle:  $APP_DIR"
echo "  Backend: http://127.0.0.1:8787 (JarvisCore must be running)"
echo ""
echo "Change William's code via chat — no Cursor IDE needed:"
echo '  "improve yourself: add a status widget to the chat UI"'
