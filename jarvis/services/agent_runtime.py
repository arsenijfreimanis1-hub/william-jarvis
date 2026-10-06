"""Runtime execution for persisted William specialist agents."""

from __future__ import annotations

import re

from jarvis.config import settings
from jarvis.services import agent_learning, agent_registry, capabilities, cursor_agent, event_log, learning, memory, sessions
from jarvis.services.agent_types import AgentRecord

_USE_AGENT_RE = re.compile(
    r"^\s*(?:use|run|ask)\s+agent\s+(?P<name>[\w .-]+?)\s*(?:[:,]|to)\s*(?P<task>.+)\s*$",
    re.I | re.S,
)
_AGENT_PREFIX_RE = re.compile(
    r"^\s*agent\s*:\s*(?P<name>[\w .-]+?)\s*[:|-]\s*(?P<task>.+)\s*$",
    re.I | re.S,
)


def parse_agent_invocation(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    for pattern in (_USE_AGENT_RE, _AGENT_PREFIX_RE):
        match = pattern.match(stripped)
        if match:
            return match.group("name").strip(), match.group("task").strip()
    return None


async def resolve_invocation(text: str) -> tuple[AgentRecord, str] | None:
    parsed = parse_agent_invocation(text)
    if parsed:
        name, task = parsed
        agent = await agent_registry.get_agent(name)
        if agent and task:
            return agent, task
        return None

    triggered = await agent_registry.find_triggered_agent(text)
    if triggered:
        return triggered, text.strip()
    return None


async def _conversation_block(conversation_id: str | None, *, voice: bool) -> str:
    if not conversation_id:
        return ""
    limit = sessions.VOICE_HISTORY_LIMIT if voice else sessions.HISTORY_LIMIT
    history = await sessions.get_history(conversation_id, limit=limit)
    return sessions.format_context(history, limit=10 if voice else 8)


def _agent_overlay(agent: AgentRecord, *, specialist_lessons: str = "") -> str:
    tools = ", ".join(agent.runtime.allowed_tools) or "none"
    trigger_line = ", ".join(agent.trigger_phrases) or "none"
    notes = agent.learning_notes.strip() or "none"
    extra_lessons = f"\n\n{specialist_lessons}" if specialist_lessons else ""
    return (
        "SPECIALIST AGENT OVERLAY:\n"
        f"- Name: {agent.name}\n"
        f"- Purpose: {agent.purpose}\n"
        f"- Version: {agent.version}\n"
        f"- Triggers: {trigger_line}\n"
        f"- Allowed tools: {tools}\n"
        f"- Autonomy mode: {agent.runtime.autonomy_mode}\n"
        f"- Learning notes: {notes}\n"
        "Follow the specialist instructions below without replacing William's base safety rules.\n"
        "If a needed action is outside the allowlist, explain the constraint instead of inventing it.\n\n"
        f"{agent.instructions}{extra_lessons}"
    )


_PROVIDER_TOOLS = frozenset(
    {"providers.scan_keys", "providers.probe", "providers.usage", "providers.save_key", "gateway.chat", "gateway.fim"}
)


async def _live_context(agent: AgentRecord) -> str:
    """Ground specialist agents in live facts so they never guess about keys, quotas or tools."""
    tools = set(agent.runtime.allowed_tools)
    blocks: list[str] = []
    try:
        if tools & _PROVIDER_TOOLS:
            from jarvis.services.providers import scout, usage

            blocks.append("PROVIDER KEYS + USAGE (live):\n" + await scout.status_reply(voice=False))
            summary = await usage.summary(days=1)
            lines = []
            for pid, data in summary["providers"].items():
                today = data.get("today") or {}
                state = summary["state"].get(pid, {})
                lines.append(
                    f"- {pid}: {today.get('calls_today', 0)} calls today, "
                    f"{today.get('daily_percent') if today.get('daily_percent') is not None else '?'}% of free daily, "
                    f"{'cooling' if state.get('cooling') else 'ready'}"
                )
            blocks.append("\n".join(lines))
        if "speech.transcribe" in tools or "speech.synthesize" in tools:
            from jarvis.services.providers import speech

            blocks.append(f"SPEECH BACKENDS (live): {speech.status()}")
        if "cad.generate" in tools:
            from jarvis.services.providers import cad

            blocks.append(f"CAD TOOLS (live): {cad.status()}")
        if "vectors.search" in tools or "vectors.embed" in tools:
            from jarvis.services.providers import vectors

            blocks.append(f"VECTOR MEMORY (live): {await vectors.stats()}")
    except Exception:
        pass
    return ("\n\n".join(blocks) + "\n\n") if blocks else ""


def _gateway_first(agent: AgentRecord) -> bool:
    """Agents whose model is `gateway` or a free provider id skip Cursor entirely."""
    from jarvis.services.providers import catalog

    model = (agent.runtime.model or "").strip().lower()
    return model == "gateway" or model in catalog.PROVIDERS


async def _run_gateway(agent: AgentRecord, prompt: str, *, task: str) -> dict:
    from jarvis.services.providers import catalog, gateway

    model = (agent.runtime.model or "").strip().lower()
    answered = await gateway.chat_detailed(
        prompt=prompt,
        capability=gateway.infer_capability(task, kind="code" if "code" in agent.purpose.lower() else None),
        source=f"agent.{agent.name_key}",
        prefer=model if model in catalog.PROVIDERS else None,
    )
    return {"ok": True, "result": answered["reply"], "run_id": None, "engine": answered["provider"],
            "model": answered.get("model")}


async def _run_agent_prompt(agent: AgentRecord, prompt: str, *, task: str) -> dict:
    """Cursor for Cursor-model agents (as before) with the free gateway as fallback; gateway-first otherwise."""
    if _gateway_first(agent):
        try:
            return await _run_gateway(agent, prompt, task=task)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}

    result = await cursor_agent.run(
        prompt,
        cwd=agent.runtime.workspace_dir or str(settings.workspace_dir),
        model=agent.runtime.model,
    )
    if result.get("ok"):
        return result
    try:
        fallback = await _run_gateway(agent, prompt, task=task)
        fallback["cursor_error"] = result.get("error")
        return fallback
    except Exception as exc:
        return {"ok": False, "error": f"{result.get('error', 'cursor failed')}; gateway: {str(exc)[:200]}"}


async def execute_agent(
    agent: AgentRecord,
    task: str,
    *,
    voice: bool = False,
    conversation_id: str | None = None,
) -> dict:
    timeline = await event_log.get_timeline_block(limit=5)
    memory_block = await memory.get_block(task)
    lessons = await learning.get_lessons_block()
    specialist_lessons = await agent_learning.get_agent_lessons_block(agent.name)
    conversation = await _conversation_block(conversation_id, voice=voice)
    system = capabilities.full_system(
        voice=voice,
        lessons=lessons,
        memory="\n\n".join(part for part in (memory_block, timeline) if part),
        conversation=conversation,
    )
    live = await _live_context(agent)
    prompt = (
        f"{system}\n\n"
        f"{_agent_overlay(agent, specialist_lessons=specialist_lessons)}\n\n"
        f"{live}"
        f"SPECIALIST TASK:\n{task}\n\n"
        "Reply as William fulfilling the specialist role. "
        "Be concrete, stay within the allowlist, and mention constraints when blocked."
    )
    result = await _run_agent_prompt(agent, prompt, task=task)
    learning_state = await agent_learning.record_agent_execution(
        agent,
        task,
        result,
        voice=voice,
        conversation_id=conversation_id,
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "agent_name": agent.name,
            "error": result.get("error", "agent execution failed"),
            "agent_score": learning_state.get("score"),
            "agent_outcome": learning_state.get("outcome"),
        }
    return {
        "ok": True,
        "reply": capabilities.trim_reply(result.get("result", ""), voice=voice),
        "engine": "agent",
        "intent": "agent",
        "run_id": result.get("run_id"),
        "provider": result.get("engine", "cursor"),
        "agent_name": agent.name,
        "agent_version": agent.version,
        "allowed_tools": agent.runtime.allowed_tools,
        "agent_score": learning_state.get("score"),
        "agent_outcome": learning_state.get("outcome"),
    }


async def invoke_agent(
    agent_id: int,
    task: str,
    *,
    voice: bool = False,
    conversation_id: str | None = None,
) -> dict:
    agent = await agent_registry.get_agent_by_id(agent_id)
    if not agent:
        return {"ok": False, "error": "agent not found", "agent_id": agent_id}
    return await execute_agent(
        agent,
        task,
        voice=voice,
        conversation_id=conversation_id,
    )
