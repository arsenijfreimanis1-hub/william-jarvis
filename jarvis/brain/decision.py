"""Engine decision policy: which brain answers, given budget, urgency and resource state.

Order of preference is fixed by principle P1: rules → ollama (local) → free cloud → cursor.
This module only *recommends* (and explains); router.py and agent_runtime.py apply it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.config import settings


@dataclass
class Decision:
    engine: str  # rules | ollama | free_cloud | cursor
    reasons: list[str] = field(default_factory=list)
    capability: str = "chat"
    max_tokens: int | None = None

    def to_dict(self) -> dict:
        return {"engine": self.engine, "reasons": self.reasons, "capability": self.capability,
                "max_tokens": self.max_tokens}


_HEAVY_HINTS = ("refactor", "multi-file", "across the codebase", "architecture", "migrate", "rewrite")
_CODE_HINTS = ("code", "function", "bug", "test", "python", "swift", "typescript", "sql", "script")


def decide(text: str, *, kind: str | None = None, rules_available: bool = False, voice: bool = False,
           token_budget: int = 0) -> Decision:
    from jarvis.services import resource_governor

    lowered = text.lower()
    reasons: list[str] = []
    if rules_available:
        return Decision("rules", ["a rules agent matched: zero tokens"], capability="none", max_tokens=0)

    constrained = resource_governor._state.get("mode") == "constrained"
    mode = (settings.gateway_mode or "free_cloud_first").lower()
    heavy = any(h in lowered for h in _HEAVY_HINTS) or len(text) > 4000
    codey = kind == "code" or any(h in lowered for h in _CODE_HINTS)
    capability = "code" if codey else ("fast" if voice else ("reason" if kind in ("fact", "reason") else "chat"))

    if heavy and settings.cursor_configured() and not voice:
        reasons.append("heavy multi-file task → Cursor")
        return Decision("cursor", reasons, capability=capability, max_tokens=token_budget or None)

    if mode == "local_only":
        reasons.append("gateway_mode=local_only")
        return Decision("ollama", reasons, capability=capability, max_tokens=token_budget or None)

    if voice and not constrained and mode == "local_first":
        reasons.append("voice + local_first → Ollama for latency")
        return Decision("ollama", reasons, capability=capability, max_tokens=token_budget or 400)

    if constrained:
        reasons.append("governor constrained → prefer free cloud over local CPU")
    reasons.append(f"gateway_mode={mode}")
    return Decision("free_cloud", reasons, capability=capability, max_tokens=token_budget or None)
