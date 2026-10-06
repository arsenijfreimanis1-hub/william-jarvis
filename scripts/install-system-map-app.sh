#!/usr/bin/env bash
# Build William System Map desktop app (live topology view). No Cursor IDE required.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/jarvis-paths.sh
source "$ROOT/scripts/jarvis-paths.sh"
HELPER_DIR="$ROOT/macos-helper"
APP_DIR="$HELPER_DIR/WilliamSystemMap.app"
DESKTOP_APP="$HOME/Desktop/William System Map.app"
BINARY="$HELPER_DIR/.build/release/WilliamSystemMap"
LOGS="$JARVIS_LOGS_DIR"

cd "$HELPER_DIR"
echo "Building WilliamSystemMap…"
swift build -c release --product WilliamSystemMap

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
cp "$BINARY" "$APP_DIR/Contents/MacOS/WilliamSystemMap"
chmod +x "$APP_DIR/Contents/MacOS/WilliamSystemMap"

cat > "$APP_DIR/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>WilliamSystemMap</string>
  <key>CFBundleIdentifier</key>
  <string>com.willy.william-system-map</string>
  <key>CFBundleName</key>
  <string>William System Map</string>
  <key>CFBundleDisplayName</key>
  <string>William System Map</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

# App icon (SVG → PNG → icns, sets CFBundleIconFile)
python3 "$ROOT/jarvis/services/app_icons.py" --target "$APP_DIR" --name "William System Map" >/dev/null \
  || echo "warning: app icon generation skipped"

codesign --force --sign - --identifier com.willy.william-system-map --deep "$APP_DIR" 2>/dev/null || true

rm -rf "$DESKTOP_APP"
ditto "$APP_DIR" "$DESKTOP_APP"

mkdir -p "$LOGS"
PLIST_DST="$HOME/Library/LaunchAgents/com.willy.william-system-map.plist"
cat > "$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.willy.william-system-map</string>
    <key>Comment</key>
    <string>William Agent live system topology map</string>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <false/>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>ProgramArguments</key>
    <array>
      <string>$APP_DIR/Contents/MacOS/WilliamSystemMap</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$HELPER_DIR</string>
    <key>StandardOutPath</key>
    <string>$LOGS/william-system-map.log</string>
    <key>StandardErrorPath</key>
    <string>$LOGS/william-system-map.err.log</string>
  </dict>
</plist>
EOF

open "$DESKTOP_APP" 2>/dev/null || true

echo ""
echo "William System Map installed on Desktop."
echo "  App:     $DESKTOP_APP"
echo "  Bundle:  $APP_DIR"
echo "  URL:     http://127.0.0.1:8787/map (JarvisCore must be running)"
echo ""
