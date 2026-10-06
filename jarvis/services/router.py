import re
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.config import settings
from jarvis.services import (
    agent_runtime,
    capabilities,
    cursor_agent,
    event_log,
    executor,
    grounding,
    intent,
    learning,
    local_runtime,
    memory,
    ollama,
    orchestrator,
    screen_hooks,
    security,
    self_modify,
    sessions,
    system_control,
    terminal,
    web,
)

TIME_QUERY = re.compile(r"\b(what time|time is it|current time|what's the time)\b", re.I)
DATE_QUERY = re.compile(r"\b(what day|what date|what's the date|today's date)\b", re.I)
FLEET_STATUS_HINTS = re.compile(
    r"\b(fleet status|are (?:the )?(?:machines|nodes|peers) online|"
    r"is (?:the )?(?:pc|macbook|mac mini) online|wake(?: the)? pc)\b",
    re.I,
)
PROVIDER_STATUS_HINTS = re.compile(
    r"\b(which (?:ai |api )?keys|(?:api|provider|model) (?:usage|quota|status)|"
    r"scan (?:for )?(?:api )?keys|free (?:api|ai) keys|what (?:models|providers) (?:do we have|are configured)|"
    r"key scout|quota keeper)\b",
    re.I,
)
CAD_HINTS = re.compile(
    r"\b(openscad|blender script|3d (?:model|print|part|cad)|\.stl\b|\.step\b|cad (?:model|file)|"
    r"design (?:a|an|the) (?:bracket|enclosure|case|mount|gear|holder|box))\b",
    re.I,
)
WAKE_ONLY = re.compile(
    r"^(hey\s+)?(willy|willie|william|will|woody|wil)\s*[?.!]*$",
    re.I,
)


def _local_time_reply(text: str, *, voice: bool) -> str | None:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    if TIME_QUERY.search(text):
        spoken = now.strftime("%I:%M %p").lstrip("0")
        return f"{spoken}, boss." if voice else f"{now.strftime('%H:%M')} ({settings.timezone})"
    if DATE_QUERY.search(text):
        spoken = now.strftime("%A, %d %B")
        return f"{spoken}, boss." if voice else spoken
    return None


def _history_messages(history: list[dict]) -> list[dict]:
    return [{"role": h["role"], "content": h["content"]} for h in history if h["role"] in ("user", "assistant")]


def _finish(reply: str, *, voice: bool) -> str:
    return capabilities.trim_reply(reply, voice=voice)


async def _build_system(
    *,
    voice: bool,
    text: str,
    lessons: str,
    conversation_id: str | None = None,
    messaging: bool = False,
    use_screen_context: bool = True,
) -> str:
    mem = await memory.get_block(text)
    timeline = await event_log.get_timeline_block(limit=5)
    screen_block = ""
    if use_screen_context and settings.screen_watch_enabled and not messaging:
        screen_block = await screen_hooks.on_user_prompt(text=text, conversation_id=conversation_id)
    memory_block = "\n\n".join(p for p in (mem, timeline, screen_block) if p)
    conversation = ""
    if conversation_id:
        limit = sessions.VOICE_HISTORY_LIMIT if voice else sessions.HISTORY_LIMIT
        hist = await sessions.get_history(conversation_id, limit=limit)
        conversation = sessions.format_context(hist, limit=10 if voice else 8)
    return capabilities.full_system(
        voice=voice, lessons=lessons, memory=memory_block, conversation=conversation
    )


async def _answer_facts(
    text: str,
    *,
    voice: bool,
    system: str,
    history: list[dict],
    kind: str,
) -> dict:
    facts = await web.research(text)
    confidence = facts.get("confidence", "none")
    fact_text = facts.get("text", "")

    if not fact_text or confidence in ("low", "none"):
        if voice:
            reply = grounding.UNVERIFIED_VOICE
        else:
            reply = grounding.UNVERIFIED_WEB
        return {"reply": reply, "engine": "local", "intent": kind, "web": confidence or "no_results"}

    if confidence == "low" and voice:
        reply = grounding.UNVERIFIED_VOICE
        return {"reply": reply, "engine": "local", "intent": kind, "web": confidence}

    if confidence in ("low", "none") and cursor_agent.should_reason(text) and not voice:
        cloud = await cursor_agent.run_reasoning(text, context=fact_text)
        if cloud.get("ok"):
            reply = grounding.enforce_grounded_reply(
                cloud["result"], voice=voice, had_facts=True, confidence=confidence
            )
            return {
                "reply": _finish(reply, voice=voice),
                "engine": "cursor",
                "intent": kind,
                "web": confidence,
            }

    prompt = grounding.fact_answer_prompt(text, fact_text, voice=voice)
    msgs = _history_messages(history)
    msgs.append({"role": "user", "content": prompt})
    reply = await ollama.chat(system=system, messages=msgs, capability="reason", source="router.facts")
    reply = grounding.enforce_grounded_reply(
        reply, voice=voice, had_facts=True, confidence=confidence
    )
    return {
        "reply": _finish(reply, voice=voice),
        "engine": "willy",
        "intent": kind,
        "web": confidence,
    }


async def _answer_chat(
    text: str,
    *,
    voice: bool,
    system: str,
    history: list[dict],
) -> dict:
    if grounding.needs_grounding(text):
        return await _answer_facts(text, voice=voice, system=system, history=history, kind="fact")

    lowered = text.lower().strip()
    open_ended = (
        "what are we gonna do",
        "what should we do",
        "what do you think",
        "what now",
        "any ideas",
    )
    if any(phrase in lowered for phrase in open_ended):
        reply = "What would you like to tackle first, boss?" if voice else "What would you like to do?"
        return {"reply": reply, "engine": "local", "intent": "chat"}

    msgs = _history_messages(history)
    msgs.append({
        "role": "user",
        "content": (
            f"{text}\n\n"
            "Reply naturally using conversation context. "
            "Do not invent facts, names, or past events not in context."
        ),
    })
    from jarvis.services.providers import gateway

    capability = "fast" if voice else gateway.infer_capability(text, kind="chat")
    reply = await ollama.chat(system=system, messages=msgs, capability=capability, source="router.chat")
    reply = grounding.enforce_grounded_reply(reply, voice=voice, had_facts=False)
    engine = gateway.last_decision.get("provider") or "willy"
    return {"reply": _finish(reply, voice=voice), "engine": engine if engine != "ollama" else "willy",
            "intent": "chat"}


async def route(
    text: str,
    *,
    voice: bool = False,
    conversation_id: str | None = None,
    task_id: int | None = None,
    messaging: bool = False,
) -> dict:
    """Orchestrator entry: opens the root span, journals the input, then dispatches."""
    from jarvis.services import journal, spans

    source = "voice" if voice else ("messaging" if messaging else "web")
    async with spans.span("orchestrator", spans.device_name(), task_id=task_id, input_text=text,
                          metadata={"source": source, "conversation_id": conversation_id}) as sp:
        await journal.input(text[:160], source=source, task_id=task_id)
        result = await _route_inner(text, voice=voice, conversation_id=conversation_id, task_id=task_id,
                                    messaging=messaging)
        engine = result.get("engine")
        sp.provider = sp.provider or (engine if engine not in ("agent", "local") else sp.provider)
        spans.set_output(result.get("reply"))
        result.setdefault("trace_id", sp.trace_id)
        result["tokens"] = sp.prompt_tokens + sp.completion_tokens
        if engine and engine not in ("agent",):
            await journal.decision(f"routed via {engine} ({result.get('intent', '?')}), {result['tokens']} tokens",
                                   source="router", task_id=task_id,
                                   metadata={"engine": engine, "intent": result.get("intent")})
        return result


async def _forward_to_steward(text: str, agent_invocation, *, voice: bool, conversation_id: str | None) -> dict | None:
    """On a `macbook` role with a connected peer, forward non-rules prompts to the Mini."""
    from jarvis.config import settings
    from jarvis.services import link

    if getattr(settings, "role", "mini") != "macbook" or not link.status()["peers"]:
        return None
    if agent_invocation and getattr(agent_invocation[0].runtime, "engine", "cursor") == "rules":
        return None  # zero tokens, answer locally
    result = await link.send_prompt(text, session_id=conversation_id, voice=voice)
    if not result.get("ok"):
        return None  # fall through to local handling
    reply = result.get("reply") or ""
    return {**{k: v for k, v in result.items() if k not in ("ok", "in_reply_to")},
            "reply": reply, "engine": result.get("engine", "steward"), "forwarded_to": "mini",
            "intent": result.get("intent", "forwarded")}


async def _route_inner(
    text: str,
    *,
    voice: bool = False,
    conversation_id: str | None = None,
    task_id: int | None = None,
    messaging: bool = False,
) -> dict:
    # 1. Execute local macOS / terminal actions first (never hallucinate "opening Spotify").
    local = await executor.try_local_execute(text, voice=voice)
    if local:
        return local

    if FLEET_STATUS_HINTS.search(text):
        from jarvis.services import fleet_power, fleet_router

        if re.search(r"\bwake(?: the)? pc\b", text, re.I):
            result = await fleet_power.wake_pc()
            if result.get("ok"):
                reply = "Sent Wake-on-LAN to the Windows PC. Waiting for heartbeat."
            else:
                reply = f"Could not wake the PC: {result.get('error', 'unknown error')}"
            return {"reply": _finish(reply, voice=voice), "engine": "fleet", "intent": "fleet"}
        summary = await fleet_router.status_reply()
        return {"reply": _finish(summary, voice=voice), "engine": "fleet", "intent": "fleet"}

    if PROVIDER_STATUS_HINTS.search(text):
        from jarvis.services.providers import scout

        if re.search(r"\bscan\b", text, re.I):
            await scout.scan_keys()
        summary = await scout.status_reply(voice=voice)
        return {"reply": _finish(summary, voice=voice) if voice else summary, "engine": "providers",
                "intent": "providers"}

    if CAD_HINTS.search(text) and not voice:
        from jarvis.services.providers import cad

        result = await cad.generate(text)
        if result.get("ok"):
            rendered = (result.get("render") or {})
            where = rendered.get("path") or result.get("scad_path") or result.get("script_path")
            note = "" if rendered.get("ok") else f" ({rendered.get('error', 'not rendered')})"
            reply = f"CAD source generated with {result.get('provider')} → {where}{note}"
        else:
            reply = f"CAD generation failed: {result.get('error', 'unknown error')}"
        return {"reply": reply, "engine": "cad", "intent": "cad", "cad": result}

    agent_invocation = await agent_runtime.resolve_invocation(text)

    # Scout → Steward: on the MacBook, only macOS actions and zero-token rules agents run locally;
    # anything that would spend tokens or needs the control plane is forwarded to the Mini.
    forwarded = await _forward_to_steward(text, agent_invocation, voice=voice, conversation_id=conversation_id)
    if forwarded is not None:
        return forwarded

    if agent_invocation:
        agent, task = agent_invocation
        executed = await agent_runtime.execute_agent(
            agent,
            task,
            voice=voice,
            conversation_id=conversation_id,
            task_id=task_id,
        )
        if executed.get("ok"):
            return executed
        return {
            "reply": _finish(
                executed.get("error", f"Agent {agent.name} is unavailable right now."),
                voice=voice,
            ),
            "engine": "agent",
            "intent": "agent",
            "agent_name": agent.name,
        }

    kind = intent.classify(text) if voice else await intent.classify_async(text)
    lessons = await learning.get_lessons_block()
    use_screen = kind != "screen" and not intent.SCREEN_HINTS.search(text)
    system = await _build_system(
        voice=voice,
        text=text,
        lessons=lessons,
        conversation_id=conversation_id,
        messaging=messaging,
        use_screen_context=use_screen,
    )

    if WAKE_ONLY.match(text.strip()):
        return {"reply": "Yes, boss?", "engine": "local", "intent": "chat"}

    if kind == "exit":
        if not intent.EXIT_PHRASES.search(text):
            kind = "chat"
        else:
            return {"reply": "Goodbye, boss.", "engine": "local", "end_session": True, "intent": kind}

    if kind == "cancel":
        return {"reply": "Cancelled, boss.", "engine": "local", "intent": kind}

    blocked = None if await security.is_full_access() else capabilities.cannot_do_reply(text, voice=voice)
    if blocked:
        return {"reply": blocked, "engine": "local", "intent": "blocked"}

    if kind == "remember":
        payload = memory.wants_remember(text)
        if payload:
            await memory.store(payload, kind="preference", importance=3, source=conversation_id)
            reply = "Got it, boss. I'll remember that." if voice else f"Remembered: {payload}"
        else:
            reply = "What should I remember, boss?" if voice else "Tell me what to remember."
        return {"reply": reply, "engine": "local", "intent": kind}

    if kind == "recall":
        hits = await memory.retrieve(text, limit=3)
        if hits:
            summary = hits[0]["content"]
            reply = _finish(summary, voice=voice)
        else:
            reply = "I don't have that in memory yet, boss." if voice else "No matching memory found."
        return {"reply": reply, "engine": "memory", "intent": kind}

    if kind == "terminal":
        full_access = await security.is_full_access()
        result = await terminal.execute(text, full_access=full_access, voice=voice)
        if result.get("ok"):
            await event_log.log_integration(
                "terminal",
                source="voice" if voice else "web",
                detail=result.get("command") or text[:200],
                metadata={"stdout_len": len(result.get("stdout") or "")},
            )
            return {
                "reply": _finish(result["reply"], voice=voice),
                "engine": "terminal",
                "intent": kind,
                "command": result.get("command"),
                "stdout": result.get("stdout"),
            }
        if result.get("error") != "not a terminal command":
            return {
                "reply": _finish(result.get("reply", result.get("error", "Cannot run that, boss.")), voice=voice),
                "engine": "terminal",
                "intent": kind,
            }

    if kind == "system":
        result = await system_control.execute(text)
        if result.get("ok"):
            return {
                "reply": _finish(result["reply"], voice=voice),
                "engine": "system",
                "intent": kind,
                "executed": True,
            }
        return {
            "reply": _finish(result.get("reply", "Cannot do that, boss."), voice=voice),
            "engine": "system",
            "intent": kind,
            "executed": False,
        }

    if kind == "screen":
        from jarvis.services import desktop

        result = await desktop.observe_and_act(text, voice=voice)
        if not result.get("ok"):
            err = result.get("error", "Could not see the screen.")
            return {"reply": _finish(err, voice=voice), "engine": "vision", "intent": kind}
        if result.get("approval_id"):
            reply = (
                "I see it, boss. Approve the action in the panel."
                if voice
                else f"{result.get('description', '')} — approval #{result['approval_id']} required."
            )
        else:
            reply = result.get("reply") or result.get("description", "Done, boss.")
        return {
            "reply": _finish(reply, voice=voice),
            "engine": "vision",
            "intent": kind,
            "executed": result.get("acted", False),
            "image_url": (result.get("steps") or [{}])[-1].get("image_url") if result.get("steps") else None,
        }

    local = _local_time_reply(text, voice=voice)
    if local:
        return {"reply": local, "engine": "local", "intent": "time"}

    history = []
    if conversation_id:
        limit = sessions.VOICE_HISTORY_LIMIT if voice else sessions.HISTORY_LIMIT
        history = await sessions.get_history(conversation_id, limit=limit)

    if kind == "action" and task_id:
        routed = await orchestrator.queue_action(text, task_id=task_id, voice=voice)
        routed["intent"] = kind
        return routed

    if self_modify.looks_like_self_modify(text):
        result = await self_modify.propose(self_modify.extract_description(text))
        if result.get("merged") or result.get("auto_executed"):
            reply = _finish("Updated my code and merged, boss.", voice=voice)
        elif result.get("approval_id"):
            reply = _finish("Sandbox ready — approve the merge in the panel, boss.", voice=voice)
        elif result.get("ok"):
            reply = _finish("Sandbox branch created, boss.", voice=voice)
        else:
            reply = _finish(result.get("error", "Self-modify failed."), voice=voice)
        return {
            "reply": reply,
            "engine": "self_modify",
            "intent": "self_modify",
            "branch": result.get("branch"),
            "approval_id": result.get("approval_id"),
        }

    if not messaging and local_runtime.should_escalate_to_cursor(text, kind=kind):
        escalated = await cursor_agent.run(f"{system}\n\nTask (be brief):\n{text}")
        if escalated.get("ok"):
            return {
                "reply": _finish(escalated["result"], voice=voice),
                "engine": "cursor",
                "run_id": escalated.get("run_id"),
                "intent": kind,
            }
        if "CURSOR_API_KEY" in escalated.get("error", ""):
            return await _answer_chat(text, voice=voice, system=system, history=history)

    if kind == "code" and not voice:
        # Free coding specialists (Codestral / Gemini / Groq) before anything paid.
        from jarvis.services.providers import gateway

        try:
            result = await gateway.chat_detailed(
                _history_messages(history) + [{"role": "user", "content": text}],
                system=system,
                capability=gateway.infer_capability(text, kind="code"),
                source="router.code",
            )
            return {
                "reply": result["reply"],
                "engine": result["provider"],
                "model": result.get("model"),
                "intent": kind,
            }
        except Exception:
            pass

    if kind in ("fact", "reason") or grounding.needs_grounding(text):
        if local_runtime.should_use_cursor_reasoning(text, kind=kind, messaging=messaging):
            cloud = await cursor_agent.run_reasoning(text)
            if cloud.get("ok"):
                return {
                    "reply": _finish(cloud["result"], voice=voice),
                    "engine": "cursor",
                    "intent": kind,
                    "executed": False,
                    "run_id": cloud.get("run_id"),
                }
        return await _answer_facts(text, voice=voice, system=system, history=history, kind=kind)

    chat_voice = voice or messaging
    return await _answer_chat(text, voice=chat_voice, system=system, history=history)
