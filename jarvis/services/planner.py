import re
import uuid

from jarvis.services import (
    approvals,
    build_pipeline,
    event_log,
    goal_runner,
    improve_run,
    intent,
    learning,
    local_runtime,
    memory,
    notion_sync,
    router,
    screen_hooks,
    security,
    self_modify,
    sessions,
    task_priority,
    task_splitter,
    tasks,
    terminal,
    worker,
)

SENSITIVE_KEYWORDS = ("delete", "deploy", "purchase", "api key", "credential")
GOAL_PREFIX = re.compile(r"^(?:goal|autonomous goal):\s*", re.I)
MESSAGING_SOURCES = ("whatsapp:", "telegram:", "signal:", "sms:", "web:", "openclaw:")


def _is_messaging_source(source: str) -> bool:
    lowered = source.lower()
    return lowered.startswith(MESSAGING_SOURCES) or lowered == "openclaw"
VOICE_COMMAND_HINTS = (
    "play", "open", "weather", "spotify", "remember", "what", "who", "how",
    "time", "pause", "stop", "skip", "close", "search", "tell", "check",
    "music", "volume", "youtube", "launch", "focus", "goodbye", "thanks",
)


async def _sync_session_to_notion(conversation_id: str, *, source: str) -> None:
    if not notion_sync.configured():
        return
    history = await sessions.get_history(conversation_id, limit=24)
    if len(history) < 2:
        return
    await screen_hooks.on_session_end(
        conversation_id=conversation_id,
        messages=[{**m, "source": source} for m in history],
    )


def _looks_like_voice_noise(text: str) -> bool:
    words = text.split()
    if len(words) <= 18:
        return False
    lowered = text.lower()
    return not any(hint in lowered for hint in VOICE_COMMAND_HINTS)


async def _queue_batch(
    text: str,
    subtasks: list[str],
    *,
    source: str,
    voice: bool,
    conversation_id: str,
    speaker_verified: bool = True,
) -> dict:
    if not speaker_verified:
        reply = "I don't recognize your voice, boss. Say enroll my voice to set that up."
        await sessions.add_message(conversation_id, "assistant", reply, engine="blocked")
        return {
            "reply": reply,
            "engine": "blocked",
            "session_id": conversation_id,
            "intent": "blocked",
        }

    ordered = task_priority.sort_by_speed(subtasks)
    batch_id = uuid.uuid4().hex[:10]
    parent = await tasks.create_task(
        title=f"Plan: {text[:90]}",
        body=text,
        source=source,
        status="running",
        batch_id=batch_id,
        priority=0,
    )
    child_ids: list[int] = []
    fast_notes: list[str] = []
    queued_count = 0

    children: list[tuple[str, dict]] = []
    for part in ordered:
        prio, _ = task_priority.estimate_priority(part)
        child = await tasks.create_task(
            title=part[:120],
            body=part,
            source=source,
            status="running",
            parent_id=parent["id"],
            batch_id=batch_id,
            priority=prio,
        )
        child_ids.append(child["id"])
        children.append((part, child))

    full_access = await security.is_full_access()
    terminal_parts: list[tuple[str, dict]] = []
    other_batch: list[tuple[str, dict]] = []
    for part, child in children:
        kind = intent.classify(part)
        if local_runtime.is_terminal_task(part, kind=kind):
            terminal_parts.append((part, child))
        else:
            other_batch.append((part, child))

    if len(terminal_parts) > 1:
        results = await terminal.execute_parallel(
            [p for p, _ in terminal_parts],
            full_access=full_access,
            voice=voice,
        )
        for (_, child), result in zip(terminal_parts, results):
            status = "done" if result.get("ok") else "failed"
            await tasks.update_task_status(child["id"], status)
            fast_notes.append(result.get("reply", "Done."))
    elif len(terminal_parts) == 1:
        other_batch.insert(0, terminal_parts[0])

    for part, child in other_batch:
        routed = await router.route(
            part,
            voice=voice,
            conversation_id=conversation_id,
            task_id=child["id"],
        )
        if routed.get("deferred"):
            await tasks.update_task_status(child["id"], "queued")
            queued_count += 1
        else:
            await tasks.update_task_status(child["id"], "done")
            fast_notes.append(routed.get("reply", "Done."))

    if queued_count:
        worker.notify()

    deferred = queued_count > 0
    plan_line = task_priority.plan_summary(ordered, voice=voice)
    if voice:
        if fast_notes and deferred:
            reply = f"{plan_line} On it for the rest, boss."
        elif fast_notes:
            reply = plan_line or fast_notes[0]
        elif deferred:
            reply = f"{plan_line} Queued {len(deferred)} background tasks, boss."
        else:
            reply = f"Split into {len(ordered)} tasks, boss."
    else:
        lines = "\n".join(f"{i + 1}. {part}" for i, part in enumerate(ordered))
        reply = f"Plan ({len(ordered)} tasks, fastest first):\n{lines}"
        if fast_notes:
            reply += f"\n\nDone immediately:\n" + "\n".join(f"- {n}" for n in fast_notes)
        if deferred:
            reply += f"\n\nQueued in background: {len(deferred)} task(s)"

    await sessions.add_message(conversation_id, "assistant", reply, engine="batch")
    return {
        "reply": reply,
        "task_id": parent["id"],
        "task_ids": child_ids,
        "subtasks": ordered,
        "batch_id": batch_id,
        "engine": "batch",
        "deferred": bool(deferred),
        "session_id": conversation_id,
        "intent": "action",
    }


async def handle_message(
    text: str,
    source: str = "web",
    *,
    session_id: str | None = None,
    speaker_verified: bool | None = None,
    speaker_confidence: float | None = None,
) -> dict:
    voice = source == "voice"
    messaging = _is_messaging_source(source)
    conversation_id, is_new_session = await sessions.get_or_create(session_id, source=source, message=text)
    await sessions.add_message(conversation_id, "user", text)

    verified = True if speaker_verified is None else speaker_verified
    if voice and speaker_verified is False:
        reply = "I don't recognize your voice, boss. Say enroll my voice to teach me."
        await sessions.add_message(conversation_id, "assistant", reply, engine="blocked")
        await event_log.log_interaction(
            source=source,
            user_message=text,
            assistant_reply=reply,
            intent="blocked",
            engine="voice_auth",
            conversation_id=conversation_id,
            metadata={"speaker_confidence": speaker_confidence},
        )
        return {
            "reply": reply,
            "engine": "blocked",
            "session_id": conversation_id,
            "intent": "blocked",
        }

    if voice and _looks_like_voice_noise(text):
        reply = "I didn't catch a command there, boss. Say hey Willie again?"
        await sessions.add_message(conversation_id, "assistant", reply, engine="ignored")
        await event_log.log_interaction(
            source=source,
            user_message=text,
            assistant_reply=reply,
            intent="ignored",
            engine="ignored",
            conversation_id=conversation_id,
        )
        return {
            "reply": reply,
            "engine": "ignored",
            "session_id": conversation_id,
            "intent": "ignored",
        }

    if self_modify.looks_like_improve_run(text):
        minutes = self_modify.improve_run_minutes(text)
        started = await improve_run.start(duration_minutes=minutes)
        if started.get("ok"):
            reply = (
                f"Improve loop running for {minutes} minutes, boss."
                if voice
                else f"Autonomous improve loop started ({minutes} min). Track at GET /api/self/improve-run/status"
            )
        else:
            reply = started.get("error", "Could not start improve loop.")
            if voice:
                reply = f"Couldn't start that, boss. {reply[:50]}"
        await sessions.add_message(conversation_id, "assistant", reply, engine="improve_run")
        return {
            "reply": reply,
            "engine": "improve_run",
            "session_id": conversation_id,
            "intent": "self_modify",
            "duration_minutes": minutes,
        }

    if self_modify.looks_like_self_modify(text):
        description = self_modify.extract_description(text)
        result = await self_modify.propose(description)
        if result.get("merged") or result.get("auto_executed"):
            reply = (
                "Done, boss. I updated my code and merged the changes."
                if voice
                else f"Self-modify merged on branch {result.get('branch', '')}. {result.get('diff', '')[:200]}"
            )
        elif result.get("approval_id"):
            reply = (
                "Changes are on a sandbox branch, boss. Approve the merge in the panel."
                if voice
                else (
                    f"Sandbox branch `{result.get('branch')}` ready. "
                    f"Approve merge #{result.get('approval_id')} in /admin or via API."
                )
            )
        elif result.get("ok"):
            reply = "Sandbox branch created, boss." if voice else f"Sandbox `{result.get('branch')}` — review diff in /admin."
        else:
            err = result.get("error", "Self-modify failed.")
            reply = f"Couldn't change my code, boss. {err[:60]}" if voice else err
        await sessions.add_message(conversation_id, "assistant", reply, engine="self_modify")
        await event_log.log_interaction(
            source=source,
            user_message=text,
            assistant_reply=reply,
            intent="self_modify",
            engine="self_modify",
            conversation_id=conversation_id,
            metadata={"branch": result.get("branch"), "approval_id": result.get("approval_id")},
        )
        return {
            "reply": reply,
            "engine": "self_modify",
            "session_id": conversation_id,
            "intent": "self_modify",
            "branch": result.get("branch"),
            "approval_id": result.get("approval_id"),
        }

    build_approval = await build_pipeline.handle_voice_command(text, session_id=conversation_id)
    if build_approval is not None:
        if build_approval.get("ok"):
            phase = build_approval.get("phase", "")
            if voice:
                reply = f"Approved, boss. Build is now {phase.replace('_', ' ')}."
            else:
                reply = f"Build #{build_approval.get('id', build_approval.get('build_id', ''))} approved — phase: {phase}"
        else:
            reply = build_approval.get("error", "Build approval failed.")
            if voice:
                reply = f"Couldn't approve, boss. {reply[:60]}"
        await sessions.add_message(conversation_id, "assistant", reply, engine="build")
        return {
            "reply": reply,
            "engine": "build",
            "session_id": conversation_id,
            "build_id": build_approval.get("id") or build_approval.get("build_id"),
            "intent": "build",
        }

    if build_pipeline.looks_like_build(text) or build_pipeline.BUILD_PREFIX.match(text.strip()):
        created = await build_pipeline.create_from_message(
            text,
            source=source,
            session_id=conversation_id,
        )
        if not created.get("ok"):
            reply = created.get("error", "Build failed to start.")
            if voice:
                reply = f"Couldn't start the build, boss. {reply[:50]}"
        elif voice:
            reply = "Planning your project, boss. I'll show slices for approval."
        else:
            reply = (
                f"Build #{created.get('build_id')} started — phase: {created.get('phase', 'planning')}.\n"
                f"Review slices at GET /api/builds/{created.get('build_id')}"
            )
        await sessions.add_message(conversation_id, "assistant", reply, engine="build")
        await event_log.log_interaction(
            source=source,
            user_message=text,
            assistant_reply=reply,
            intent="build",
            engine="build",
            conversation_id=conversation_id,
            metadata={"build_id": created.get("build_id")},
        )
        return {
            "reply": reply,
            "build_id": created.get("build_id"),
            "engine": "build",
            "session_id": conversation_id,
            "intent": "build",
        }

    goal_match = GOAL_PREFIX.match(text.strip())
    if goal_match:
        prompt = GOAL_PREFIX.sub("", text.strip()).strip()
        if prompt:
            created = await goal_runner.create_goal(prompt, source=source)
            subtasks = created.get("subtasks") or []
            if voice:
                reply = (
                    f"I've drafted a {len(subtasks)}-step plan for approval, boss. "
                    "Open the panel to review and approve it."
                )
            else:
                lines = "\n".join(f"{i + 1}. {part}" for i, part in enumerate(subtasks))
                reply = f"Goal #{created['id']} awaiting approval ({len(subtasks)} steps):\n{lines}"
            await sessions.add_message(conversation_id, "assistant", reply, engine="goal")
            await event_log.log_interaction(
                source=source,
                user_message=text,
                assistant_reply=reply,
                intent="goal",
                engine="goal",
                conversation_id=conversation_id,
                metadata={"goal_id": created.get("id"), "subtasks": len(subtasks)},
            )
            return {
                "reply": reply,
                "goal_id": created.get("id"),
                "subtasks": subtasks,
                "engine": "goal",
                "session_id": conversation_id,
                "intent": "goal",
            }

    lowered = text.lower()
    if not await security.is_full_access() and any(kw in lowered for kw in SENSITIVE_KEYWORDS):
        task = await tasks.create_task(title=text[:120], body=text, source=source)
        approval = await approvals.request_approval(
            action="sensitive_action",
            detail=text,
        )
        await tasks.update_task_status(task["id"], "awaiting_approval")
        reply = (
            "That looks sensitive, boss. I've queued it for your approval."
            if voice
            else "That looks sensitive — I've queued it for your approval in the inbox."
        )
        await sessions.add_message(conversation_id, "assistant", reply, engine="blocked")
        await event_log.log_interaction(
            source=source,
            user_message=text,
            assistant_reply=reply,
            intent="blocked",
            task_id=task["id"],
            task_status="awaiting_approval",
            engine="blocked",
            conversation_id=conversation_id,
        )
        return {
            "reply": reply,
            "task_id": task["id"],
            "approval_id": approval["id"],
            "engine": "blocked",
            "session_id": conversation_id,
        }

    try:
        subtasks = [text]
        if not messaging and (not voice or len(text) > 80 or task_splitter.looks_compound(text)):
            subtasks = await task_splitter.split_prompt(text)
        if len(subtasks) > 1:
            result = await _queue_batch(
                text,
                subtasks,
                source=source,
                voice=voice,
                conversation_id=conversation_id,
                speaker_verified=verified,
            )
            await learning.observe_turn(text, result)
            await event_log.log_interaction(
                source=source,
                user_message=text,
                assistant_reply=result["reply"],
                intent="action",
                task_id=result.get("task_id"),
                task_status="running",
                engine="batch",
                conversation_id=conversation_id,
                metadata={"batch_id": result.get("batch_id"), "subtasks": len(subtasks)},
            )
            return result

        task = await tasks.create_task(title=text[:120], body=text, source=source)
        routed = await router.route(
            text,
            voice=voice,
            conversation_id=conversation_id,
            task_id=task["id"],
            messaging=messaging,
        )

        if routed.get("deferred"):
            await tasks.update_task_status(task["id"], "queued")
            worker.notify()
        elif routed.get("end_session"):
            await tasks.update_task_status(task["id"], "done")
        else:
            await tasks.update_task_status(task["id"], "done")

        task_status = "queued" if routed.get("deferred") else "done"
        logged = await event_log.log_interaction(
            source=source,
            user_message=text,
            assistant_reply=routed["reply"],
            intent=routed.get("intent"),
            task_id=task["id"],
            task_status=task_status,
            engine=routed.get("engine"),
            conversation_id=conversation_id,
            metadata={
                "run_id": routed.get("run_id"),
                "command": routed.get("command"),
                "agent_name": routed.get("agent_name"),
                "agent_version": routed.get("agent_version"),
            },
        )

        if routed.get("engine") == "cursor":
            await event_log.log_integration(
                "cursor_escalation",
                source=source,
                detail=text[:200],
                task_id=task["id"],
                metadata={"run_id": routed.get("run_id")},
            )

        await sessions.add_message(
            conversation_id,
            "assistant",
            routed["reply"],
            engine=routed.get("engine"),
        )
        await learning.observe_turn(text, routed)

        if logged.get("alignment_score", 1) < 0.4:
            await learning.record_struggle(
                user_message=text,
                failure_kind="misaligned",
                intent=routed.get("intent"),
                engine=routed.get("engine"),
                detail=logged.get("alignment_notes"),
            )

        remember_payload = memory.wants_remember(text)
        if remember_payload and routed.get("intent") != "remember":
            await memory.store(remember_payload, kind="preference", importance=3, source=conversation_id)

        if routed.get("end_session"):
            await _sync_session_to_notion(conversation_id, source=source)

        return {
            "reply": routed["reply"],
            "task_id": task["id"],
            "engine": routed.get("engine", "willy"),
            "run_id": routed.get("run_id"),
            "escalation": routed.get("escalation"),
            "session_id": conversation_id,
            "is_new_session": is_new_session,
            "executed": routed.get("executed", False),
            "deferred": routed.get("deferred", False),
            "end_session": routed.get("end_session", False),
            "intent": routed.get("intent"),
            "command": routed.get("command"),
            "stdout": routed.get("stdout"),
            "agent_name": routed.get("agent_name"),
            "agent_version": routed.get("agent_version"),
            "agent_engine": routed.get("agent_engine"),
            "provider": routed.get("provider"),
            "model": routed.get("model"),
            "trace_id": routed.get("trace_id"),
            "tokens": routed.get("tokens"),
            "required": routed.get("required"),
        }
    except Exception as exc:
        if "task" in locals():
            await tasks.update_task_status(task["id"], "failed")
        reply = "Trouble right now, boss." if voice else f"Agent unavailable: {exc}"
        await sessions.add_message(conversation_id, "assistant", reply, engine="error")
        await event_log.log_interaction(
            source=source,
            user_message=text,
            assistant_reply=reply,
            intent="unknown",
            task_id=locals().get("task", {}).get("id") if "task" in locals() else None,
            task_status="failed",
            engine="error",
            conversation_id=conversation_id,
            metadata={"error": str(exc)},
        )
        await learning.observe_turn(
            text,
            {"reply": reply, "engine": "error", "intent": "unknown"},
            error=str(exc),
        )
        return {
            "reply": reply,
            "task_id": locals().get("task", {}).get("id") if "task" in locals() else None,
            "error": str(exc),
            "engine": "error",
            "session_id": conversation_id,
        }
