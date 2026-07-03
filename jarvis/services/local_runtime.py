"""Free-first execution policy — local terminal + Ollama before paid cloud tiers."""

from __future__ import annotations

from jarvis.config import settings
from jarvis.services import cursor_agent, terminal


def should_escalate_to_cursor(text: str, *, kind: str, messaging: bool = False) -> bool:
    """Use Cursor cloud only when configured and the task truly needs it."""
    if messaging or not settings.cursor_configured():
        return False
    if settings.free_first:
        return kind == "code" and cursor_agent.should_escalate(text)
    return kind == "code" or cursor_agent.should_escalate(text)


def should_use_cursor_reasoning(text: str, *, kind: str, messaging: bool = False) -> bool:
    """Hard reasoning via cloud — off in free-first mode (web + Ollama instead)."""
    if messaging or not settings.cursor_configured():
        return False
    if settings.free_first:
        return False
    return cursor_agent.should_reason(text) and kind in ("fact", "reason")


def is_terminal_task(text: str, *, kind: str | None = None) -> bool:
    if kind == "terminal":
        return True
    return bool(terminal.resolve_command(text))


def execution_profile() -> dict:
    from jarvis.services import compute_fleet

    return {
        "free_first": settings.free_first,
        "cursor_configured": settings.cursor_configured(),
        "worker_parallel": compute_fleet.worker_parallel(),
        "terminal_parallel": settings.terminal_parallel,
        "local_brain": settings.ollama_model,
    }
