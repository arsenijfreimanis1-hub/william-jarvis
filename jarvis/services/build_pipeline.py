"""Multi-agent build pipeline — voice prompt to PRD to parallel Cursor execution."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from jarvis.config import settings
from jarvis.database import DB_PATH
from jarvis.services import (
    build_coordinator,
    build_decomposer,
    build_intake,
    build_reconciler,
    build_research,
    build_validator,
    compute_fleet,
    github_sync,
    macos,
    prd_store,
    sessions,
    state_export,
    worktree_manager,
)

log = logging.getLogger("jarvis.build_pipeline")

# Phases
PHASE_INTAKE = "intake"
PHASE_COMPREHENDING = "comprehending"
PHASE_DECOMPOSING = "decomposing"
PHASE_RESEARCHING = "researching"
PHASE_RECONCILING = "reconciling"
PHASE_AWAITING_MICRO = "awaiting_micro_approval"
PHASE_DRAFTING_PRD = "drafting_prd"
PHASE_AWAITING_PRD = "awaiting_prd_approval"
PHASE_EXECUTING = "executing"
PHASE_VALIDATING = "validating"
PHASE_INTEGRATING = "integrating"
PHASE_COMPLETE = "complete"
PHASE_FAILED = "failed"
PHASE_STOPPED = "stopped"

_active_tasks: dict[int, asyncio.Task] = {}
_stop_requested: set[int] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    phase TEXT NOT NULL DEFAULT 'intake',
    prd_version INTEGER NOT NULL DEFAULT 1,
    workspace_path TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'web',
    session_id TEXT,
    error TEXT,
    log TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS build_slices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_id INTEGER NOT NULL,
    slice_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    deps TEXT NOT NULL DEFAULT '[]',
    research_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    worktree_path TEXT,
    cursor_trace_id INTEGER,
    error TEXT,
    FOREIGN KEY (build_id) REFERENCES builds(id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        for col, typedef in (
            ("github_repo_url", "TEXT"),
            ("github_clone_url", "TEXT"),
        ):
            try:
                await db.execute(f"ALTER TABLE builds ADD COLUMN {col} {typedef}")
            except Exception:
                pass
        await db.commit()


async def _append_log(build_id: int, message: str, *, level: str = "info") -> None:
    entry = {"ts": _now(), "level": level, "message": message}
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT log FROM builds WHERE id = ?", (build_id,))).fetchone()
        logs = json.loads(row[0] if row else "[]")
        logs.append(entry)
        if len(logs) > 200:
            logs = logs[-200:]
        await db.execute(
            "UPDATE builds SET log = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(logs), build_id),
        )
        await db.commit()
    log.info("build[%s]: %s", build_id, message)


async def _set_phase(build_id: int, phase: str, *, status: str = "running", error: str | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if error is not None:
            await db.execute(
                "UPDATE builds SET phase = ?, status = ?, error = ?, updated_at = datetime('now') WHERE id = ?",
                (phase, status, error, build_id),
            )
        else:
            await db.execute(
                "UPDATE builds SET phase = ?, status = ?, updated_at = datetime('now') WHERE id = ?",
                (phase, status, build_id),
            )
        await db.commit()


async def _get_build(build_id: int) -> dict | None:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM builds WHERE id = ?", (build_id,))).fetchone()
        return dict(row) if row else None


def _default_workspace(prompt: str) -> Path:
    base = getattr(settings, "build_projects_dir", Path.home() / "Projects")
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()[:40]).strip("-") or "project"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return base / f"willy-build-{slug}-{ts}"


async def get_build_detail(build_id: int) -> dict | None:
    build = await _get_build(build_id)
    if not build:
        return None
    build["log"] = json.loads(build.get("log") or "[]")
    build["slices"] = prd_store.load_slices(build_id)
    build["comprehension"] = prd_store.load_comprehension(build_id)
    build["prd_preview"] = prd_store.load_prd(build_id)[:4000]
    build["registry"] = prd_store.load_registry(build_id)
    build["fleet"] = await compute_fleet.fleet_status()
    build["running"] = build_id in _active_tasks and not _active_tasks[build_id].done()
    return build


async def list_builds(limit: int = 20) -> list[dict]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, prompt, status, phase, source, created_at, updated_at FROM builds ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def start(
    prompt: str,
    *,
    source: str = "web",
    workspace_path: str | Path | None = None,
    session_id: str | None = None,
    conversation_history: str = "",
) -> dict:
    """Create a build and kick off the planning pipeline."""
    prompt = prompt.strip()
    if len(prompt) < 3:
        return {"ok": False, "error": "prompt too short"}

    await ensure_tables()
    ws = Path(workspace_path) if workspace_path else _default_workspace(prompt)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO builds (prompt, status, phase, workspace_path, source, session_id)
            VALUES (?, 'running', ?, ?, ?, ?)
            """,
            (prompt, PHASE_COMPREHENDING, str(ws), source, session_id),
        )
        await db.commit()
        build_id = cursor.lastrowid

    prd_store.build_artifact_dir(build_id)
    prd_store.save_transcript(build_id, prompt, history=conversation_history)
    prd_store.save_registry(build_id, {"version": 1, "entries": {}})
    await _append_log(build_id, f"Build started — workspace: {ws}")

    if github_sync.configured():
        hub = await github_sync.ensure_hub_repo()
        if hub.get("ok"):
            await _append_log(build_id, f"William hub: {hub.get('html_url', github_sync.hub_repo_url())}")
        fleet = await compute_fleet.fleet_status()
        await _append_log(
            build_id,
            f"Compute fleet: {fleet.get('local_workers')} local workers, runtime={fleet.get('runtime')}",
        )

    _active_tasks[build_id] = asyncio.create_task(_run_planning_pipeline(build_id, conversation_history))
    detail = await get_build_detail(build_id)
    return {"ok": True, "build_id": build_id, **(detail or {})}


async def _run_planning_pipeline(build_id: int, history: str) -> None:
    build = await _get_build(build_id)
    if not build:
        return
    prompt = build["prompt"]

    try:
        if build_id in _stop_requested:
            return

        if build_id in _stop_requested:
            return

        await _set_phase(build_id, PHASE_COMPREHENDING)
        await _append_log(build_id, "Full read-through — comprehending master prompt")
        intake = await build_intake.comprehend(prompt, build_id=build_id, history=history)
        part_count = len(intake.get("identified_parts") or [])
        await _append_log(
            build_id,
            f"Comprehension complete — class={intake.get('prompt_class')}, parts={part_count}",
        )

        if build_id in _stop_requested:
            return

        await _set_phase(build_id, PHASE_DECOMPOSING)
        await _append_log(build_id, "Decomposing into workstream slices (based on comprehension)")
        slices = await build_decomposer.decompose(
            prompt, history=history, intake=intake, build_id=build_id,
        )
        prd_store.save_slices(build_id, slices)
        await _append_log(build_id, f"Decomposed into {len(slices)} slices")

        if build_id in _stop_requested:
            return

        await _set_phase(build_id, PHASE_RESEARCHING)
        await _append_log(build_id, "Deep dive per slice")
        stack_hint = " ".join(slices[0].get("registry_hints", [])[:3]) if slices else ""
        slices = await build_research.research_slices(
            slices,
            stack_hint=stack_hint,
            intake=intake,
            master_prompt=prompt,
            build_id=build_id,
        )
        prd_store.save_slices(build_id, slices)

        if build_id in _stop_requested:
            return

        await _set_phase(build_id, PHASE_RECONCILING)
        await _append_log(build_id, "Reconciling slices for consistency")
        slices = await build_reconciler.reconcile(prompt, slices, intake=intake, build_id=build_id)
        prd_store.save_slices(build_id, slices)
        await _sync_slices_db(build_id, slices)

        await _set_phase(build_id, PHASE_AWAITING_MICRO, status="awaiting_approval")
        await _append_log(build_id, f"Awaiting micro-prompt approval ({len(slices)} slices)")
        await _notify_approval(build, gate="micro", slices=slices)

    except Exception as exc:
        log.exception("planning pipeline failed for build %s", build_id)
        await _set_phase(build_id, PHASE_FAILED, status="failed", error=str(exc))
        await _append_log(build_id, f"Planning failed: {exc}", level="error")
    finally:
        _active_tasks.pop(build_id, None)


async def _sync_slices_db(build_id: int, slices: list[dict]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM build_slices WHERE build_id = ?", (build_id,))
        for sl in slices:
            await db.execute(
                """
                INSERT INTO build_slices (build_id, slice_key, ordinal, title, prompt, deps, research_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    build_id,
                    sl.get("id", ""),
                    sl.get("ordinal", 0),
                    sl.get("title", ""),
                    sl.get("prompt", ""),
                    json.dumps(sl.get("deps") or []),
                    json.dumps(sl.get("research") or {}),
                    sl.get("status", "pending"),
                ),
            )
        await db.commit()


async def _notify_approval(build: dict, *, gate: str, slices: list[dict] | None = None) -> None:
    voice = build.get("source") == "voice"
    if gate == "micro":
        count = len(slices or [])
        titles = ", ".join(sl.get("title", "")[:30] for sl in (slices or [])[:3])
        msg = f"{count} slices ready for approval, boss. {titles}."
        if voice:
            await macos.speak_when_clear(msg)
        await macos.notify("Build — review slices", msg)
    elif gate == "prd":
        msg = "PRD drafted, boss. Review and approve to start coding."
        if voice:
            await macos.speak_when_clear(msg)
        await macos.notify("Build — review PRD", msg)


async def approve_micro_prompts(
    build_id: int,
    *,
    edits: list[dict] | None = None,
) -> dict:
    build = await _get_build(build_id)
    if not build:
        return {"ok": False, "error": "build not found"}
    if build["phase"] != PHASE_AWAITING_MICRO:
        return {"ok": False, "error": f"expected phase {PHASE_AWAITING_MICRO}, got {build['phase']}"}

    slices = edits if edits else prd_store.load_slices(build_id)
    if not slices:
        return {"ok": False, "error": "no slices to approve"}

    prd_store.save_slices(build_id, slices)
    await _sync_slices_db(build_id, slices)
    await _append_log(build_id, "Micro-prompts approved")

    await _set_phase(build_id, PHASE_DRAFTING_PRD)
    _active_tasks[build_id] = asyncio.create_task(_draft_prd_and_wait(build_id, slices))
    detail = await get_build_detail(build_id)
    return {"ok": True, **(detail or {})}


async def _draft_prd_and_wait(build_id: int, slices: list[dict]) -> None:
    build = await _get_build(build_id)
    if not build:
        return
    try:
        await _append_log(build_id, "Drafting PRD with variable registry")
        prd_store.generate_prd(build_id, prompt=build["prompt"], slices=slices)
        await _set_phase(build_id, PHASE_AWAITING_PRD, status="awaiting_approval")
        await _append_log(build_id, "PRD drafted — awaiting approval")
        await _notify_approval(build, gate="prd")
    except Exception as exc:
        log.exception("PRD draft failed for build %s", build_id)
        await _set_phase(build_id, PHASE_FAILED, status="failed", error=str(exc))
    finally:
        _active_tasks.pop(build_id, None)


async def approve_prd(build_id: int) -> dict:
    build = await _get_build(build_id)
    if not build:
        return {"ok": False, "error": "build not found"}
    if build["phase"] != PHASE_AWAITING_PRD:
        return {"ok": False, "error": f"expected phase {PHASE_AWAITING_PRD}, got {build['phase']}"}

    await _append_log(build_id, "PRD approved — starting execution")
    _active_tasks[build_id] = asyncio.create_task(_run_execution_pipeline(build_id))
    detail = await get_build_detail(build_id)
    return {"ok": True, **(detail or {})}


async def revise_prd(build_id: int, feedback: str) -> dict:
    build = await _get_build(build_id)
    if not build:
        return {"ok": False, "error": "build not found"}
    if build["phase"] != PHASE_AWAITING_PRD:
        return {"ok": False, "error": f"expected phase {PHASE_AWAITING_PRD}, got {build['phase']}"}

    slices = prd_store.load_slices(build_id)
    prd_store.generate_prd(
        build_id,
        prompt=build["prompt"],
        slices=slices,
        feedback=feedback,
    )
    await _append_log(build_id, f"PRD revised: {feedback[:120]}")
    detail = await get_build_detail(build_id)
    return {"ok": True, **(detail or {})}


async def _on_slice_done(build_id: int, slice_item: dict, outcome: dict) -> None:
    wt = Path(outcome.get("worktree_path", ""))
    registry = prd_store.load_registry(build_id)
    stack = prd_store._infer_stack(prd_store.load_slices(build_id), (await _get_build(build_id) or {}).get("prompt", ""))

    await _set_phase(build_id, PHASE_VALIDATING)
    validation = await build_validator.validate_slice(wt, stack=stack)
    if not validation.get("ok"):
        await _append_log(build_id, f"Validation failed for {slice_item.get('id')} — attempting fix", level="warn")
        fixed = await build_validator.attempt_fix(build_id, slice_item, wt, validation)
        if fixed:
            validation = await build_validator.validate_slice(wt, stack=stack)

    if validation.get("ok"):
        await build_validator.update_prd_after_slice(
            build_id,
            slice_item,
            agent_result=outcome.get("result", ""),
        )
        await _append_log(build_id, f"Slice {slice_item.get('id')} validated and PRD updated")
    else:
        slice_item["status"] = "failed"
        slice_item["error"] = "validation failed"
        prd_store.save_slices(build_id, prd_store.load_slices(build_id))
        await _append_log(build_id, f"Slice {slice_item.get('id')} validation failed", level="error")


async def _run_execution_pipeline(build_id: int) -> None:
    build = await _get_build(build_id)
    if not build:
        return

    project_path = Path(build["workspace_path"])
    slices = prd_store.load_slices(build_id)
    github_repo_url = build.get("github_repo_url") or ""
    github_clone_url = build.get("github_clone_url") or ""

    try:
        await _set_phase(build_id, PHASE_EXECUTING)
        worktree_manager.init_project_repo(project_path)

        github_repo_url = build.get("github_repo_url") or ""
        github_clone_url = build.get("github_clone_url") or ""
        if github_sync.configured() and not github_repo_url:
            gh = await github_sync.bootstrap_project_github(
                project_path,
                build_id=build_id,
                prompt=build["prompt"],
            )
            if gh.get("ok"):
                github_repo_url = gh.get("github_repo_url", "")
                github_clone_url = gh.get("clone_url", "")
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE builds SET github_repo_url = ?, github_clone_url = ? WHERE id = ?",
                        (github_repo_url, github_clone_url, build_id),
                    )
                    await db.commit()
                await _append_log(build_id, f"GitHub project repo: {github_repo_url}")
            elif not gh.get("skipped"):
                await _append_log(build_id, f"GitHub bootstrap warning: {gh.get('error', '')[:120]}", level="warn")

        fleet = await compute_fleet.fleet_status()
        await _append_log(
            build_id,
            f"Executing {len(slices)} slices — {fleet.get('local_workers')} local workers, parallel={fleet.get('parallel')}",
        )

        registry = prd_store.load_registry(build_id)
        result = await build_coordinator.execute_build_slices(
            build_id,
            project_path=project_path,
            slices=slices,
            prd_version=int(registry.get("version", 1)),
            on_slice_done=_on_slice_done,
            github_repo_url=github_repo_url,
            clone_url=github_clone_url,
        )

        await _set_phase(build_id, PHASE_INTEGRATING)
        await _append_log(build_id, "Running integration validation")
        stack = prd_store._infer_stack(slices, build["prompt"])
        integration_path = project_path
        checkout = worktree_manager._run_git("checkout", worktree_manager.INTEGRATION_BRANCH, cwd=project_path)
        if checkout.returncode != 0:
            integration_path = project_path

        if getattr(settings, "app_icons_enabled", True):
            try:
                from jarvis.services import app_icons

                icon = await asyncio.to_thread(app_icons.apply_to_project, integration_path)
                if icon.get("ok"):
                    await _append_log(
                        build_id,
                        f"App icon applied ({icon.get('kind')}, {len(icon.get('applied') or [])} files) — {icon.get('name')}",
                    )
                else:
                    await _append_log(build_id, f"App icon skipped: {icon.get('error', '')[:120]}", level="warn")
            except Exception as exc:  # icons must never fail a build
                await _append_log(build_id, f"App icon skipped: {str(exc)[:120]}", level="warn")

        final_val = await build_validator.validate_integration(integration_path, stack=stack)
        if not final_val.get("ok") and result.get("failed"):
            await _set_phase(build_id, PHASE_FAILED, status="failed", error="integration validation failed")
            await _append_log(build_id, "Integration validation failed", level="error")
            if build.get("source") == "voice":
                await macos.speak_when_clear("Build failed validation, boss.")
            return

        await _set_phase(build_id, PHASE_COMPLETE, status="complete")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE builds SET completed_at = datetime('now') WHERE id = ?",
                (build_id,),
            )
            await db.commit()
        await _append_log(build_id, "Build complete")
        if github_sync.configured():
            await github_sync.export_build_manifest(build_id, github_repo_url=github_repo_url)
            if github_clone_url:
                github_sync.push_branch(project_path, worktree_manager.INTEGRATION_BRANCH)
            await state_export.export_all()
        if build.get("source") == "voice":
            await macos.speak_when_clear(f"Build complete, boss. Project at {project_path.name}.")

    except Exception as exc:
        log.exception("execution pipeline failed for build %s", build_id)
        await _set_phase(build_id, PHASE_FAILED, status="failed", error=str(exc))
        await _append_log(build_id, f"Execution failed: {exc}", level="error")
    finally:
        _active_tasks.pop(build_id, None)


async def stop(build_id: int) -> dict:
    _stop_requested.add(build_id)
    task = _active_tasks.get(build_id)
    if task and not task.done():
        task.cancel()
    await _set_phase(build_id, PHASE_STOPPED, status="stopped")
    await _append_log(build_id, "Stop requested", level="warn")
    return {"ok": True, "stopping": True}


BUILD_PREFIX = re.compile(r"^(?:build(?:\s+project)?|scaffold(?:\s+project)?):\s*", re.I)
BUILD_HINTS = (
    "build app",
    "create app",
    "create website",
    "scaffold project",
    "build project",
    "full stack",
    "new project",
    "planetary gear",
    "cad model",
    "3d model",
    "scrape website",
    "video edit",
    "promo video",
    "browser automation",
)


def looks_like_build(text: str) -> bool:
    if BUILD_PREFIX.match(text.strip()):
        return True
    lowered = text.lower()
    if len(text) > 2000 and build_intake.classify_prompt(text) in ("blueprint", "hybrid"):
        return True
    return any(h in lowered for h in BUILD_HINTS) and len(text) > 30


async def handle_voice_command(text: str, *, session_id: str | None = None) -> dict | None:
    """Handle voice/chat approval commands for active builds."""
    lowered = text.strip().lower()
    awaiting = await list_builds(limit=5)
    active = [b for b in awaiting if b.get("phase") in (PHASE_AWAITING_MICRO, PHASE_AWAITING_PRD)]

    if re.search(r"\bapprove\b.*\b(micro|slices?|prompts?)\b", lowered) or lowered in (
        "approve slices",
        "approve micro prompts",
        "approve build slices",
    ):
        for b in active:
            if b.get("phase") == PHASE_AWAITING_MICRO:
                return await approve_micro_prompts(b["id"])
        return {"ok": False, "error": "no build awaiting micro-prompt approval"}

    if re.search(r"\bapprove\b.*\bprd\b", lowered) or lowered in ("approve prd", "approve build prd"):
        for b in active:
            if b.get("phase") == PHASE_AWAITING_PRD:
                return await approve_prd(b["id"])
        return {"ok": False, "error": "no build awaiting PRD approval"}

    return None


async def create_from_message(
    text: str,
    *,
    source: str = "web",
    session_id: str | None = None,
) -> dict:
    """Entry point from planner — strip prefix and start build."""
    prompt = BUILD_PREFIX.sub("", text.strip()).strip() or text.strip()
    history = ""
    if session_id:
        msgs = await sessions.get_history(session_id, limit=12)
        history = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')[:200]}" for m in msgs)
    return await start(prompt, source=source, session_id=session_id, conversation_history=history)
