#!/usr/bin/env python3
"""Idempotently seed every agent defined under jarvis/agents/ into the specialist registry.

Accepts folder agents (README.md + POSTTHOUGHT.md, see docs/AGENT_CONTRACT.md) and legacy
single-file briefs. Re-running bumps versions only for changed definitions.

Usage:
    .venv/bin/python scripts/seed-agents.py              # all groups
    .venv/bin/python scripts/seed-agents.py providers    # one group
    .venv/bin/python scripts/seed-agents.py --check      # parse only, no DB writes
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.services import agent_loader, agent_registry  # noqa: E402


async def main(argv: list[str]) -> int:
    check = "--check" in argv
    args = [a for a in argv[1:] if not a.startswith("--")]
    only = args[0] if args else None
    try:
        found = agent_loader.discover(only)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not found:
        print("no agent definitions found", file=sys.stderr)
        return 1
    for path, spec in found:
        rel = path.relative_to(ROOT)
        if check:
            print(f"ok   {spec.name:<22} engine={spec.runtime.engine:<7} requires={spec.runtime.requires} ← {rel}")
            continue
        existing = await agent_registry.get_agent(spec.name, include_inactive=True)
        if existing and _same(existing, spec):
            print(f"same {spec.name:<22} (v{existing.version}) ← {rel}")
            continue
        record = await agent_registry.register_agent(spec)
        print(f"seed {record.name:<22} (id={record.id} v{record.version}) ← {rel}")
    print(f"done: {len(found)} agents")
    return 0


def _same(existing, spec) -> bool:
    a = existing.model_dump(include={"name", "purpose", "instructions", "trigger_phrases", "status", "runtime"})
    b = spec.model_dump(include={"name", "purpose", "instructions", "trigger_phrases", "status", "runtime"})
    return a == b


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
