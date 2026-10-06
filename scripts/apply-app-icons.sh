#!/usr/bin/env bash
# Give every William app an icon: repo .app bundles, Desktop copies, and ~/Projects/willy-build-*.
# Re-signs the repo bundles so the icon is covered by the ad-hoc signature.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

python3 "$ROOT/jarvis/services/app_icons.py" --all

for app in "$ROOT"/macos-helper/*.app; do
  [ -d "$app" ] || continue
  ident="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Contents/Info.plist" 2>/dev/null || echo "com.willy.$(basename "$app" .app | tr '[:upper:]' '[:lower:]')")"
  codesign --force --sign - --identifier "$ident" --deep "$app" 2>/dev/null || true
done

# Refresh Finder / Dock icon caches for the Desktop copies.
for app in "$HOME/Desktop/William Agent.app" "$HOME/Desktop/William System Map.app"; do
  [ -d "$app" ] && touch "$app"
done
killall Finder 2>/dev/null || true
echo "Icons applied. Dock may need a restart to refresh: killall Dock"
