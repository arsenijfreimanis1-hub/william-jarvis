#!/usr/bin/env bash
# Sync agent skills into .agents/skills (SKILL.md folders) from curated sources + the uni coach zip.
#
#   ./scripts/sync-skills.sh            # install/update everything
#   ./scripts/sync-skills.sh --list     # show what is installed
#
# Uses the `skills` CLI via npx (writes skills-lock.json, same format already in this repo).
# Falls back to a plain git clone + copy when npx is unavailable. All sources are free/public.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=".agents/skills"
mkdir -p "$DEST"

if [[ "${1:-}" == "--list" ]]; then
  for d in "$DEST"/*/; do
    [[ -f "$d/SKILL.md" ]] && printf '%-32s %s\n' "$(basename "$d")" "$(grep -m1 -E '^description:' "$d/SKILL.md" | cut -c1-90)"
  done
  exit 0
fi

# owner/repo [skill filter...]  — one line per source. Keep this list curated and small.
SOURCES=(
  "anthropics/skills"                 # reference skills: docx/pdf/xlsx/pptx, frontend-design, mcp-builder, skill-creator
  "earthtojake/text-to-cad"           # cad, gcode, dxf, bambu-labs (already present)
  "vercel-labs/agent-skills"          # vercel/nextjs/react patterns
  "obra/superpowers"                  # TDD, debugging, brainstorming, planning workflows
)

install_with_cli() {
  local src="$1"
  if command -v npx >/dev/null 2>&1; then
    npx -y skills add "$src" --all -y >/dev/null 2>&1 && echo "ok   $src (skills cli)" && return 0
  fi
  return 1
}

install_with_git() {
  local src="$1" tmp
  tmp="$(mktemp -d)"
  if git clone -q --depth 1 "https://github.com/$src.git" "$tmp/repo" 2>/dev/null; then
    local n=0
    while IFS= read -r -d '' skill; do
      local dir name
      dir="$(dirname "$skill")"; name="$(basename "$dir")"
      [[ "$name" == "." || "$name" == "repo" ]] && continue
      rm -rf "$DEST/$name"; cp -R "$dir" "$DEST/$name"; n=$((n+1))
    done < <(find "$tmp/repo" -name SKILL.md -not -path '*/node_modules/*' -print0)
    echo "ok   $src (git, $n skills)"
  else
    echo "skip $src (unreachable)"
  fi
  rm -rf "$tmp"
}

for src in "${SOURCES[@]}"; do
  install_with_cli "$src" || install_with_git "$src"
done

# University coach (UNKNOWN Business Coach) — used sparingly by the Mentor agent.
COACH_ZIP="${COACH_ZIP:-$HOME/Downloads/unknown-business-coach.zip}"
if [[ -f "$COACH_ZIP" ]]; then
  tmp="$(mktemp -d)"
  unzip -q -o "$COACH_ZIP" -d "$tmp"
  for d in "$tmp"/unknown-business-coach/skills/*/; do
    name="$(basename "$d")"
    rm -rf "$DEST/$name"; cp -R "$d" "$DEST/$name"
  done
  echo "ok   unknown-business-coach ($(ls "$tmp"/unknown-business-coach/skills | wc -l | tr -d ' ') coach skills)"
  rm -rf "$tmp"
fi

echo "installed: $(find "$DEST" -name SKILL.md | wc -l | tr -d ' ') skills in $DEST"
