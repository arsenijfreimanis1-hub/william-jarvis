from jarvis.services import (
    capabilities,
    cursor_agent,
    executor,
    intent,
    learning,
    local_runtime,
    ollama,
    security,
    system_control,
    tasks,
    terminal,
    web,
    worker,
)


async def queue_action(text: str, *, task_id: int, voice: bool = False) -> dict:
    await tasks.update_task_status(task_id, "queued")
    worker.notify()
    ack = "On it, boss." if voice else f"Queued task #{task_id}."
    return {
        "reply": ack,
        "engine": "orchestrator",
        "deferred": True,
        "task_id": task_id,
    }


async def _answer_from_facts(text: str, *, action_system: str) -> tuple[str, str]:
    facts = await web.research(text)
    if facts.get("confidence") in ("low", "none") and cursor_agent.should_reason(text):
        cloud = await cursor_agent.run_reasoning(text, context=facts.get("text", ""))
        if cloud.get("ok"):
            return cloud["result"], "cursor"

    if not facts.get("text"):
        return "I could not verify that, boss.", "local"

    prompt = (
        f"{text}\n\nFacts:\n{facts['text']}\n\n"
        "One or two complete spoken sentences. Plain English."
    )
    reply = await ollama.chat(prompt, system=action_system)
    return reply, "willy"


async def execute_task(task: dict) -> dict:
    from jarvis.services import vigil_metrics

    text = task.get("body") or task.get("title") or ""
    task_id = task["id"]
    async with vigil_metrics.track_tool("orchestrator.execute_task", task_id=task_id):
        return await _execute_task_inner(task)


async def _execute_task_inner(task: dict) -> dict:
    text = task.get("body") or task.get("title") or ""
    task_id = task["id"]
    await tasks.update_task_status(task_id, "running")

    try:
        local = await executor.try_local_execute(text, voice=task.get("source") == "voice")
        if local:
            ok = local.get("executed", False)
            await tasks.update_task_status(task_id, "done" if ok else "failed")
            return {
                "ok": ok,
                "reply": local.get("reply", ""),
                "engine": local.get("engine", "local"),
                "task_id": task_id,
            }

        kind = intent.classify(text)

        if kind == "system":
            result = await system_control.execute(text)
            if result.get("ok"):
                reply = capabilities.trim_reply(result["reply"], voice=True)
                await tasks.update_task_status(task_id, "done")
                return {"ok": True, "reply": reply, "engine": "system", "task_id": task_id}

        if terminal.resolve_command(text):
            full_access = await security.is_full_access()
            result = await terminal.execute(text, full_access=full_access, voice=True)
            if result.get("ok") or result.get("error") != "not a terminal command":
                await tasks.update_task_status(task_id, "done" if result.get("ok") else "failed")
                return {
                    "ok": result.get("ok", False),
                    "reply": result.get("reply", result.get("error", "failed")),
                    "engine": "terminal",
                    "task_id": task_id,
                    "command": result.get("command"),
                }

        if kind == "code" or cursor_agent.should_escalate(text):
            if local_runtime.should_escalate_to_cursor(text, kind=kind):
                lessons = await learning.get_lessons_block()
                system = capabilities.full_system(voice=True, lessons=lessons)
                result = await cursor_agent.run(f"{system}\n\nTask (be brief):\n{text}")
                if result.get("ok"):
                    reply = capabilities.trim_reply(result["result"], voice=True)
                    await tasks.update_task_status(task_id, "done")
                    return {"ok": True, "reply": reply, "engine": "cursor", "task_id": task_id}

        lessons = await learning.get_lessons_block()
        action_system = capabilities.full_system(voice=True, lessons=lessons)
        reply, engine = await _answer_from_facts(text, action_system=action_system)
        reply = capabilities.trim_reply(reply, voice=True)

        await tasks.update_task_status(task_id, "done")
        return {"ok": True, "reply": reply, "engine": engine, "task_id": task_id}
    except Exception as exc:
        await tasks.update_task_status(task_id, "failed")
        return {"ok": False, "error": str(exc), "task_id": task_id}
