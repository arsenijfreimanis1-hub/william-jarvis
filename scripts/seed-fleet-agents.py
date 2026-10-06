#!/usr/bin/env python3
"""Idempotently seed specialist agents from jarvis/agents/*/*.md (fleet + free-provider rosters).

Usage:
    .venv/bin/python scripts/seed-fleet-agents.py            # all rosters
    .venv/bin/python scripts/seed-fleet-agents.py providers  # one roster directory
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.services import agent_registry
from jarvis.services.agent_types import KNOWN_AGENT_TOOLS, AgentRuntimeConfig, AgentSpec

AGENTS_DIR = ROOT / "jarvis" / "agents"
DEFAULT_TOOLS = ["cursor_agent.run", "terminal.execute", "memory.retrieve", "memory.store"]


def _list_field(text: str, label: str) -> list[str]:
    m = re.search(rf"\*\*{label}:\*\*\s*(.+)$", text, re.M | re.I)
    if not m:
        return []
    return [part.strip() for part in m.group(1).split(",") if part.strip()]


def _parse_md(path: Path) -> AgentSpec:
    text = path.read_text(encoding="utf-8")
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    purpose_m = re.search(r"\*\*purpose:\*\*\s*(.+)$", text, re.M | re.I)
    role_m = re.search(r"\*\*preferred_role:\*\*\s*(\w+)", text, re.M | re.I)
    instructions_m = re.search(r"\*\*instructions:\*\*\s*\n(.*)$", text, re.S | re.I)
    triggers_block = re.search(r"\*\*triggers:\*\*\s*\n((?:- .+\n?)+)", text, re.I)
    triggers: list[str] = []
    if triggers_block:
        triggers = [
            re.sub(r"^-+\s*", "", line).strip()
            for line in triggers_block.group(1).splitlines()
            if line.strip().startswith("-")
        ]
    name = (title_m.group(1) if title_m else path.stem).strip()
    purpose = (purpose_m.group(1) if purpose_m else name).strip()
    instructions = (instructions_m.group(1) if instructions_m else text).strip()
    role = (role_m.group(1).lower() if role_m else None)
    if role not in ("control", "planner", "tester", "general"):
        role = None
    tools = [t for t in _list_field(text, "tools") if t in KNOWN_AGENT_TOOLS] or DEFAULT_TOOLS
    caps = _list_field(text, "preferred_capabilities")
    model_m = re.search(r"\*\*model:\*\*\s*([\w.:/-]+)", text, re.M | re.I)
    return AgentSpec(
        name=name,
        purpose=purpose,
        instructions=instructions,
        trigger_phrases=triggers,
        runtime=AgentRuntimeConfig(
            preferred_role=role,
            preferred_capabilities=caps,
            allowed_tools=tools,
            model=model_m.group(1).strip() if model_m else None,
        ),
    )


def roster_dirs(only: str | None) -> list[Path]:
    if only:
        return [AGENTS_DIR / only]
    return sorted(p for p in AGENTS_DIR.iterdir() if p.is_dir() and not p.name.startswith("__"))


async def main(argv: list[str]) -> int:
    only = argv[1] if len(argv) > 1 else None
    paths: list[Path] = []
    for d in roster_dirs(only):
        if not d.is_dir():
            print(f"missing {d}", file=sys.stderr)
            return 1
        paths.extend(sorted(d.glob("*.md")))
    if not paths:
        print("no agent markdown files", file=sys.stderr)
        return 1
    seeded = []
    for path in paths:
        spec = _parse_md(path)
        record = await agent_registry.register_agent(spec)
        seeded.append(record.name)
        print(f"seeded {record.name} (id={record.id} v{record.version}) ← {path.parent.name}/{path.name}")
    print(f"done: {len(seeded)} agents")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
