"""Runtime execution for persisted William specialist agents.

Flow per task (docs/AGENT_CONTRACT.md):
  span(agent) → cooldown check → resolve `requires` (child spans) → engine
  (rules | gateway | cursor→gateway) → post-thought → journal + learning → close span.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from jarvis.config import settings
from jarvis.services import (
    agent_learning,
    agent_registry,
    capabilities,
    cursor_agent,
    event_log,
    journal,
    learning,
    memory,
    sessions,
    spans,
)
from jarvis.services.agent_types import AgentRecord

log = logging.getLogger("jarvis.agent_runtime")

_USE_AGENT_RE = re.compile(
    r"^\s*(?:use|run|ask)\s+agent\s+(?P<name>[\w .-]+?)\s*(?:[:,]|to)\s*(?P<task>.+)\s*$",
    re.I | re.S,
)
_AGENT_PREFIX_RE = re.compile(
    r"^\s*agent\s*:\s*(?P<name>[\w .-]+?)\s*[:|-]\s*(?P<task>.+)\s*$",
    re.I | re.S,
)
MAX_REQUIRE_DEPTH = 3

DEFAULT_POST_THOUGHT = (
    "You just finished a task as {agent}.\nTask: {task}\nOutcome: {outcome}\nReply (truncated): {reply}\n"
    'Answer ONLY compact JSON: {{"strength": "one sentence", "weakness": "one sentence", '
    '"problem": "one sentence or null", "fix": "one concrete fix or null", "decision": "the key decision made"}}'
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


# --------------------------------------------------------------------------- prompt assembly

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
    requires = ", ".join(agent.runtime.requires) or "none"
    skills_line = ", ".join(agent.runtime.skills) or "none"
    extra_lessons = f"\n\n{specialist_lessons}" if specialist_lessons else ""
    return (
        "SPECIALIST AGENT OVERLAY:\n"
        f"- Name: {agent.name}\n"
        f"- Purpose: {agent.purpose}\n"
        f"- Version: {agent.version}\n"
        f"- Engine: {agent.runtime.engine}\n"
        f"- Device: {spans.device_name()} (affinity {agent.runtime.device_affinity})\n"
        f"- Triggers: {trigger_line}\n"
        f"- Allowed tools: {tools}\n"
        f"- Requires agents: {requires}\n"
        f"- Skills: {skills_line}\n"
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
        if "system.live" in tools:
            from jarvis.services import resource_governor

            blocks.append(f"SYSTEM (live): {json.dumps(resource_governor.snapshot_compact(), default=str)}")
        if "journal.read" in tools:
            jb = await journal.block(limit=6)
            if jb:
                blocks.append(jb)
    except Exception:
        pass
    return ("\n\n".join(blocks) + "\n\n") if blocks else ""


# --------------------------------------------------------------------------- engines

def _engine(agent: AgentRecord) -> str:
    engine = agent.runtime.engine
    if engine == "cursor":
        # Legacy rosters set model=gateway/provider without engine; honour that.
        from jarvis.services.providers import catalog

        model = (agent.runtime.model or "").strip().lower()
        if model == "gateway" or model in catalog.PROVIDERS:
            return "gateway"
    return engine


def _load_entrypoint(entrypoint: str):
    module_name, func_name = entrypoint.split(":", 1)
    module = importlib.import_module(module_name)
    func = getattr(module, func_name, None)
    if func is None:
        raise AttributeError(f"{entrypoint}: function not found")
    return func


async def _run_rules(agent: AgentRecord, task: str, *, ctx: dict) -> dict:
    if not agent.runtime.entrypoint:
        return {"ok": False, "error": f"{agent.name}: rules agent without entrypoint"}
    func = _load_entrypoint(agent.runtime.entrypoint)
    async with spans.span("tool", f"rules:{agent.runtime.entrypoint}", input_text=task):
        result = func(task, ctx)
        if asyncio.iscoroutine(result):
            result = await result
    if not isinstance(result, dict):
        result = {"ok": True, "reply": str(result)}
    result.setdefault("ok", True)
    result.setdefault("reply", "")
    return {"ok": bool(result["ok"]), "result": result.get("reply", ""), "error": result.get("error"),
            "engine": "rules", "model": None, "run_id": None, "data": result.get("data")}


async def _run_gateway(agent: AgentRecord, prompt: str, *, task: str) -> dict:
    from jarvis.services.providers import catalog, gateway

    model = (agent.runtime.model or "").strip().lower()
    async with spans.span("model", "gateway.chat", input_text=task) as sp:
        answered = await gateway.chat_detailed(
            prompt=prompt,
            capability=gateway.infer_capability(task, kind="code" if "code" in agent.purpose.lower() else None),
            source=f"agent.{agent.name_key}",
            prefer=model if model in catalog.PROVIDERS else None,
            max_tokens=agent.runtime.token_budget or None,
        )
        sp.provider = answered.get("provider")
        sp.model = answered.get("model")
        spans.set_output(answered.get("reply"))
    return {"ok": True, "result": answered["reply"], "run_id": None, "engine": answered["provider"],
            "model": answered.get("model")}


async def _run_cursor(agent: AgentRecord, prompt: str, *, task: str) -> dict:
    async with spans.span("model", "cursor.run", input_text=task) as sp:
        result = await cursor_agent.run(
            prompt,
            cwd=agent.runtime.workspace_dir or str(settings.workspace_dir),
            model=agent.runtime.model,
        )
        sp.provider = "cursor"
        sp.model = agent.runtime.model or settings.cursor_model
        if result.get("ok"):
            spans.set_output(result.get("result"))
        else:
            sp.status = "failed"
            sp.error = str(result.get("error", ""))[:500]
    if result.get("ok"):
        return result
    try:
        fallback = await _run_gateway(agent, prompt, task=task)
        fallback["cursor_error"] = result.get("error")
        return fallback
    except Exception as exc:
        return {"ok": False, "error": f"{result.get('error', 'cursor failed')}; gateway: {str(exc)[:200]}"}


# --------------------------------------------------------------------------- requires + cooldown

async def _resolve_requires(agent: AgentRecord, task: str, *, voice: bool, conversation_id: str | None,
                            depth: int) -> list[dict]:
    """Invoke every required agent as a child span; return their results for the prompt."""
    results: list[dict] = []
    if not agent.runtime.requires or depth >= MAX_REQUIRE_DEPTH:
        return results
    for key in agent.runtime.requires:
        if key == agent.name_key:
            continue
        child = await agent_registry.get_agent(key)
        if not child:
            await journal.problem(f"{agent.name} requires unknown agent '{key}'", agent=agent.name,
                                  source="agent_runtime")
            results.append({"agent": key, "ok": False, "error": "unknown agent"})
            continue
        outcome = await execute_agent(child, task, voice=voice, conversation_id=conversation_id, _depth=depth + 1)
        results.append({"agent": child.name, "ok": outcome.get("ok", False),
                        "reply": outcome.get("reply") or outcome.get("error", "")})
    return results


def _requires_block(results: list[dict]) -> str:
    if not results:
        return ""
    lines = [f"- {r['agent']}: {'OK' if r.get('ok') else 'FAILED'} — {str(r.get('reply') or r.get('error', ''))[:600]}"
             for r in results]
    return "RESULTS FROM REQUIRED AGENTS:\n" + "\n".join(lines) + "\n\n"


async def _cooldown_remaining(agent: AgentRecord) -> timedelta | None:
    hours = float(agent.runtime.cooldown_hours or 0)
    if hours <= 0 or not agent.last_used_at:
        return None
    try:
        last = datetime.strptime(agent.last_used_at[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    until = last + timedelta(hours=hours)
    now = datetime.now(timezone.utc)
    return (until - now) if until > now else None


# --------------------------------------------------------------------------- post-thought

async def _post_thought(agent: AgentRecord, task: str, result: dict, *, engine: str) -> dict:
    """Reflect on the run and journal strength/weakness/problem/fix/decision."""
    ok = bool(result.get("ok"))
    reply = str((result.get("result") if ok else result.get("error")) or "")
    outcome = "success" if ok else "failure"
    parsed: dict = {}
    if engine == "rules":
        parsed = {
            "strength": f"Deterministic run with zero tokens ({agent.runtime.entrypoint})." if ok else None,
            "weakness": None if ok else "Rules agent could not handle the input; a model fallback may be needed.",
            "problem": None if ok else reply[:300],
            "fix": None if ok else "Extend the rule set or add a trigger that routes to a model agent.",
            "decision": f"Handled '{task[:80]}' with rules engine.",
        }
    else:
        template = agent.runtime.post_thought.strip() or DEFAULT_POST_THOUGHT
        try:
            prompt = template.format(agent=agent.name, task=task[:600], outcome=outcome, reply=reply[:800])
        except (KeyError, IndexError):
            prompt = DEFAULT_POST_THOUGHT.format(agent=agent.name, task=task[:600], outcome=outcome, reply=reply[:800])
        try:
            from jarvis.services.providers import gateway

            async with spans.span("model", "post_thought", input_text=task[:200]) as sp:
                answered = await gateway.chat_detailed(prompt=prompt, capability="fast",
                                                       source=f"postthought.{agent.name_key}", max_tokens=300)
                sp.provider = answered.get("provider")
                sp.model = answered.get("model")
            parsed = _parse_json_object(answered.get("reply", "")) or {}
        except Exception as exc:
            log.debug("post-thought skipped for %s: %s", agent.name, exc)
            parsed = {"weakness": None if ok else "Model call failed.", "problem": None if ok else reply[:300],
                      "decision": f"Ran {agent.name} via {engine}."}
    for kind in ("strength", "weakness", "problem", "fix", "decision"):
        value = parsed.get(kind)
        if value and isinstance(value, str) and value.strip().lower() not in ("null", "none", "n/a"):
            await journal.write(kind, value.strip(), agent=agent.name, source="post_thought",
                                severity=3 if kind == "problem" else 1,
                                metadata={"engine": engine, "ok": ok})
    return parsed


def _parse_json_object(text: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- main entry

async def execute_agent(
    agent: AgentRecord,
    task: str,
    *,
    voice: bool = False,
    conversation_id: str | None = None,
    task_id: int | None = None,
    _depth: int = 0,
) -> dict:
    engine = _engine(agent)
    async with spans.span("agent", agent.name_key, agent=agent.name, task_id=task_id, input_text=task,
                          metadata={"engine": engine, "depth": _depth,
                                    "affinity": agent.runtime.device_affinity}) as sp:
        await journal.input(f"{agent.name} ← {task[:120]}", agent=agent.name,
                            source="voice" if voice else "web", metadata={"engine": engine})

        remaining = await _cooldown_remaining(agent)
        if remaining is not None:
            hours = round(remaining.total_seconds() / 3600, 1)
            sp.status = "skipped"
            await journal.decision(f"{agent.name} skipped: cooldown {hours}h remaining", agent=agent.name,
                                   source="agent_runtime")
            return {"ok": False, "agent_name": agent.name, "cooldown": True,
                    "error": f"{agent.name} is on cooldown for another {hours}h."}

        required = await _resolve_requires(agent, task, voice=voice, conversation_id=conversation_id, depth=_depth)
        ctx = {"voice": voice, "conversation_id": conversation_id, "task_id": task_id,
               "device": spans.device_name(), "required": required, "agent": agent.model_dump()}

        if engine == "rules":
            try:
                result = await _run_rules(agent, task, ctx=ctx)
            except Exception as exc:
                result = {"ok": False, "error": f"rules engine failed: {str(exc)[:300]}"}
        else:
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
                f"{_requires_block(required)}"
                f"SPECIALIST TASK:\n{task}\n\n"
                "Reply as William fulfilling the specialist role. "
                "Be concrete, stay within the allowlist, and mention constraints when blocked."
            )
            if engine == "gateway":
                try:
                    result = await _run_gateway(agent, prompt, task=task)
                except Exception as exc:
                    result = {"ok": False, "error": str(exc)[:300]}
            else:
                result = await _run_cursor(agent, prompt, task=task)

        if result.get("ok"):
            spans.set_output(result.get("result"), provider=result.get("engine"), model=result.get("model"))
        else:
            sp.status = "failed"
            sp.error = str(result.get("error", ""))[:500]
            await journal.problem(f"{agent.name} failed: {str(result.get('error', ''))[:160]}", agent=agent.name,
                                  source="agent_runtime")

        learning_state = await agent_learning.record_agent_execution(
            agent, task, result, voice=voice, conversation_id=conversation_id,
        )
        asyncio.create_task(_post_thought(agent, task, result, engine=engine))

        if not result.get("ok"):
            return {
                "ok": False,
                "agent_name": agent.name,
                "error": result.get("error", "agent execution failed"),
                "agent_score": learning_state.get("score"),
                "agent_outcome": learning_state.get("outcome"),
                "trace_id": sp.trace_id,
                "engine": engine,
            }
        return {
            "ok": True,
            "reply": capabilities.trim_reply(result.get("result", ""), voice=voice),
            "engine": "agent",
            "agent_engine": engine,
            "intent": "agent",
            "run_id": result.get("run_id"),
            "provider": result.get("engine", "cursor"),
            "model": result.get("model"),
            "agent_name": agent.name,
            "agent_version": agent.version,
            "allowed_tools": agent.runtime.allowed_tools,
            "agent_score": learning_state.get("score"),
            "agent_outcome": learning_state.get("outcome"),
            "required": [{"agent": r["agent"], "ok": r.get("ok")} for r in required],
            "trace_id": sp.trace_id,
            "tokens": sp.prompt_tokens + sp.completion_tokens,
            "data": result.get("data"),
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
    return await execute_agent(agent, task, voice=voice, conversation_id=conversation_id)


async def invoke_by_name(name: str, task: str, **kw) -> dict:
    agent = await agent_registry.get_agent(name)
    if not agent:
        return {"ok": False, "error": f"agent not found: {name}"}
    return await execute_agent(agent, task, **kw)


async def dependency_graph() -> dict:
    """Agents + requires edges for the Studio call-graph's static layer."""
    agents = await agent_registry.list_agents(status="active")
    nodes = [
        {"id": a.name_key, "name": a.name, "group": a.runtime.group, "engine": _engine(a),
         "device_affinity": a.runtime.device_affinity, "tools": a.runtime.allowed_tools,
         "skills": a.runtime.skills, "token_budget": a.runtime.token_budget, "score": a.performance_score}
        for a in agents
    ]
    edges = [{"from": a.name_key, "to": req, "kind": "requires"} for a in agents for req in a.runtime.requires]
    return {"nodes": nodes, "edges": edges}
