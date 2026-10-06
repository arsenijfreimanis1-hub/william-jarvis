"""Device persona: Steward on the Mac mini, Scout on the MacBook.

The persona text is the instructions body of `jarvis/agents/brain/<name>/README.md`, so the
brain is itself an agent under the same contract. `JARVIS_PERSONALITY_PROMPT` or
`JARVIS_PERSONA_FILE` override it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jarvis.config import settings
from jarvis.paths import ROOT

ROLE_TO_PERSONA = {"mini": "steward", "macbook": "scout"}


def persona_name(role: str | None = None) -> str:
    role = role or getattr(settings, "role", "mini")
    return ROLE_TO_PERSONA.get(role, "steward")


@lru_cache(maxsize=4)
def _load(path_str: str) -> str:
    path = Path(path_str)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    # Drop the H1 title line.
    lines = [ln for ln in text.strip().splitlines()]
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def prompt(role: str | None = None) -> str:
    """Persona block prepended to chat/reason prompts."""
    explicit = (settings.personality_prompt or "").strip()
    if explicit:
        return explicit
    override = (getattr(settings, "persona_file", "") or "").strip()
    if override:
        return _load(override)
    name = persona_name(role)
    return _load(str(ROOT / "jarvis" / "agents" / "brain" / name / "README.md"))


def describe() -> dict:
    name = persona_name()
    return {"role": getattr(settings, "role", "mini"), "persona": name,
            "source": (settings.personality_prompt and "env") or (getattr(settings, "persona_file", "") and "file") or "agent",
            "chars": len(prompt())}
