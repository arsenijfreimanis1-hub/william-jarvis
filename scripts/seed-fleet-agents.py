#!/usr/bin/env python3
"""Idempotently seed LAN fleet specialist agents from jarvis/agents/fleet/*.md."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.services import agent_registry
from jarvis.services.agent_types import AgentRuntimeConfig, AgentSpec

FLEET_DIR = ROOT / "jarvis" / "agents" / "fleet"


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
    return AgentSpec(
        name=name,
        purpose=purpose,
        instructions=instructions,
        trigger_phrases=triggers,
        runtime=AgentRuntimeConfig(
            preferred_role=role,
            allowed_tools=["cursor_agent.run", "terminal.execute", "memory.retrieve", "memory.store"],
        ),
    )


async def main() -> int:
    if not FLEET_DIR.is_dir():
        print(f"missing {FLEET_DIR}", file=sys.stderr)
        return 1
    paths = sorted(FLEET_DIR.glob("*.md"))
    if not paths:
        print("no fleet agent markdown files", file=sys.stderr)
        return 1
    seeded = []
    for path in paths:
        spec = _parse_md(path)
        record = await agent_registry.register_agent(spec)
        seeded.append(record.name)
        print(f"seeded {record.name} (id={record.id} v{record.version})")
    print(f"done: {len(seeded)} agents")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
