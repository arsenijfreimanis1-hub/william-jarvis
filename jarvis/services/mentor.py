"""Mentor trigger policy: bring in the UNKNOWN Business Coach only at crucial moments.

Milestones (any one is enough, at most once per Mentor cooldown):
  * docs/PRD.md changed in the last 24 h (direction changed)
  * the same agent failed >= 3 times in 24 h (stuck)
  * a journal decision mentions pricing / pivot / launch / investor
Everything else is ignored, so the coach stays rare and valuable.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta, timezone

from jarvis.paths import ROOT
from jarvis.services import agent_registry, agent_runtime, journal

_MILESTONE_WORDS = re.compile(r"\b(pricing|price point|pivot|launch(?:ing)?|investor|fundrais|go[- ]to[- ]market|"
                              r"first customer|revenue model)\b", re.I)


def _prd_changed_recently(hours: int = 24) -> str | None:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        out = subprocess.run(["git", "log", f"--since={since}", "--format=%h %s", "--", "docs/PRD.md"],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return None
    return out.splitlines()[0] if out else None


async def detect_milestones() -> list[dict]:
    found: list[dict] = []
    commit = _prd_changed_recently()
    if commit:
        found.append({"kind": "prd_changed", "detail": commit})
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    problems = await journal.list_entries(kind="problem", since=since, limit=200)
    per_agent: dict[str, int] = {}
    for p in problems:
        if p.get("agent"):
            per_agent[p["agent"]] = per_agent.get(p["agent"], 0) + 1
    for agent, n in per_agent.items():
        if n >= 3:
            found.append({"kind": "stuck", "detail": f"{agent} failed {n}x in 24h"})
    decisions = await journal.list_entries(kind="decision", since=since, limit=200)
    for d in decisions:
        if _MILESTONE_WORDS.search(d["summary"] + " " + (d.get("detail") or "")):
            found.append({"kind": "business_decision", "detail": d["summary"][:120]})
            break
    return found


async def maybe_coach(*, force: bool = False) -> dict:
    """Run the Mentor if a milestone is present and the cooldown allows. Returns what happened."""
    milestones = [] if force else await detect_milestones()
    if not milestones and not force:
        return {"ran": False, "reason": "no milestone"}
    mentor = await agent_registry.get_agent("Mentor")
    if not mentor:
        return {"ran": False, "reason": "Mentor agent not seeded"}
    brief = "Crucial moment check-in. Milestones: " + ("; ".join(f"{m['kind']}: {m['detail']}" for m in milestones)
                                                        or "manual request") + \
            ". Ask the single most useful guiding question for the founder right now, in the right coach voice."
    result = await agent_runtime.execute_agent(mentor, brief)
    if result.get("cooldown"):
        return {"ran": False, "reason": "cooldown", "milestones": milestones}
    if result.get("ok"):
        try:
            from jarvis.services import macos

            await macos.notify("Mentor", str(result.get("reply", ""))[:140])
        except Exception:
            pass
    return {"ran": bool(result.get("ok")), "milestones": milestones, "reply": result.get("reply"),
            "error": result.get("error")}
