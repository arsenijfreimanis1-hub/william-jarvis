import asyncio
import logging

from jarvis.services import goal_runner, learning, macos, notion_sync, orchestrator, tasks
from jarvis.services import compute_fleet, event_log

log = logging.getLogger("jarvis.worker")
_worker_task: asyncio.Task | None = None
_running = False
_active_jobs = 0
_POLL_IDLE_SEC = 0.25
_wake = asyncio.Event()


def _parallel_limit() -> int:
    limit = compute_fleet.worker_parallel()
    try:
        from jarvis.services import resource_governor

        cap = resource_governor.worker_parallel_cap()
        if cap is not None:
            return max(1, min(limit, int(cap)))
    except Exception:
        pass
    return limit


def notify() -> None:
    """Wake the worker immediately when a task is queued."""
    _wake.set()


async def _log_outcome(task: dict, result: dict) -> None:
    status = "done" if result.get("ok") else "failed"
    reply = result.get("reply", "Done.") if result.get("ok") else result.get("error", "failed")
    logged = await event_log.log_task_outcome(
        source=task.get("source", "worker"),
        user_message=task.get("body") or task.get("title") or "",
        reply=reply,
        task_id=task["id"],
        task_status=status,
        engine=result.get("engine"),
    )
    await notion_sync.capture_significant_task(
        task_id=task["id"],
        title=task.get("title", ""),
        body=task.get("body") or "",
        status=status,
        alignment_score=logged.get("alignment_score"),
        alignment_notes=logged.get("alignment_notes"),
    )
    if not result.get("ok"):
        await learning.observe_task_failure(
            user_message=task.get("body") or task.get("title") or "",
            error=result.get("error", "failed"),
            engine=result.get("engine"),
        )


async def _speak_result(task: dict, result: dict) -> None:
    from jarvis.services import capabilities

    if task.get("source") != "voice":
        return

    if result.get("ok"):
        summary = capabilities.trim_reply(
            result.get("reply", "Done."),
            voice=True,
            max_words=14,
        )
        spoken = summary if summary.lower().startswith("done") else f"Done, boss. {summary}"
        speak = await macos.speak_when_clear(spoken, max_wait=4.0)
        if not speak.get("ok"):
            log.debug("speak deferred: %s", speak.get("error"))
    else:
        err = result.get("error", "failed")
        await macos.speak_when_clear(f"Task failed, boss. {err[:60]}", max_wait=3.0)


async def _run_task(task: dict) -> None:
    from jarvis.services import vigil_metrics

    async with vigil_metrics.track_tool("worker.run_task", task_id=task.get("id"), title=task.get("title", "")[:80]):
        await _run_task_inner(task)


async def _run_task_inner(task: dict) -> None:
    global _active_jobs
    _active_jobs += 1
    try:
        result = await orchestrator.execute_task(task)
        await _log_outcome(task, result)
        asyncio.create_task(_speak_result(task, result))
        try:
            await goal_runner.on_task_done(task)
        except Exception as exc:
            log.exception("goal_runner hook failed: %s", exc)

        batch_id = task.get("batch_id")
        if not batch_id:
            return
        batch = await tasks.list_batch_tasks(batch_id)
        children = [t for t in batch if t.get("parent_id")]
        if not children:
            return
        if all(t["status"] in ("done", "failed") for t in children):
            parent = next((t for t in batch if not t.get("parent_id")), None)
            if parent and parent.get("status") == "running":
                failed = sum(1 for t in children if t["status"] == "failed")
                status = "failed" if failed == len(children) else "done"
                await tasks.update_task_status(parent["id"], status)
                if task.get("source") == "voice":
                    if failed:
                        asyncio.create_task(
                            macos.speak_when_clear(f"Batch finished, boss. {failed} failed.", max_wait=3.0)
                        )
                    else:
                        asyncio.create_task(
                            macos.speak_when_clear(f"All {len(children)} tasks complete, boss.", max_wait=3.0)
                        )
    finally:
        _active_jobs = max(0, _active_jobs - 1)


async def _process_loop() -> None:
    global _running
    _running = True
    while _running:
        try:
            queued = await tasks.list_tasks_by_status("queued", limit=_parallel_limit())
            if not queued:
                try:
                    await asyncio.wait_for(_wake.wait(), timeout=_POLL_IDLE_SEC)
                except TimeoutError:
                    pass
                _wake.clear()
                continue

            _wake.clear()
            await asyncio.gather(*[_run_task(task) for task in queued])
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.exception("worker error: %s", exc)
            await asyncio.sleep(1)


def start() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_process_loop())


def stop() -> None:
    global _running, _worker_task
    _running = False
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None


def status() -> dict:
    parallel = _parallel_limit()
    return {
        "running": _running,
        "task_active": _active_jobs > 0,
        "active_jobs": _active_jobs,
        "parallel": parallel,
    }
