from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from jarvis.config import settings
from jarvis.services import (
    activity_stream,
    agent_author,
    agent_registry,
    agent_runtime,
    approvals,
    cursor_trace,
    desktop,
    event_log,
    fleet_auth,
    fleet_power,
    fleet_registry,
    fleet_router,
    goal_runner,
    improve_run,
    learning,
    macos,
    memory,
    minis,
    minis_ops,
    netatmo,
    notion_sync,
    ollama,
    openclaw,
    people,
    planner,
    remote_control,
    scheduler,
    screen_observer,
    security,
    self_modify,
    sessions,
    skills,
    system_map,
    tasks,
    terminal,
    vigil_metrics,
    voice_state,
    worker,
)
from jarvis.services.fleet_types import (
    FleetClaimRequest,
    FleetEnqueueRequest,
    FleetHeartbeatRequest,
    FleetRegisterRequest,
    FleetResultRequest,
)

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    message: str
    source: str = "web"
    session_id: str | None = None
    speaker_verified: bool | None = None
    speaker_confidence: float | None = None


class ApprovalRequest(BaseModel):
    approved: bool


class NamePersonRequest(BaseModel):
    netatmo_id: str
    name: str
    face_url: str | None = None


class SelfProposeRequest(BaseModel):
    description: str


class MuteRequest(BaseModel):
    muted: bool


class FullAccessRequest(BaseModel):
    enabled: bool


class RemoteControlRequest(BaseModel):
    enabled: bool


class RemotePointRequest(BaseModel):
    x: float
    y: float
    button: str = "left"


class RemoteTypeRequest(BaseModel):
    text: str


class RemoteKeyRequest(BaseModel):
    key: str
    modifiers: list[str] = Field(default_factory=list)


class ScreenShareRequest(BaseModel):
    enabled: bool


class MinisInputRequest(BaseModel):
    type: str
    x: float
    y: float


class MinisRestartRequest(BaseModel):
    target: str = "both"


class TerminalRequest(BaseModel):
    command: str


class CreateGoalRequest(BaseModel):
    prompt: str = Field(min_length=3)
    source: str = "web"


class CreateBuildRequest(BaseModel):
    prompt: str = Field(min_length=3)
    source: str = "web"
    workspace_path: str | None = None
    session_id: str | None = None


class ApproveMicroPromptsRequest(BaseModel):
    edits: list[dict] | None = None


class RevisePrdRequest(BaseModel):
    feedback: str = Field(min_length=1)


class AgentRuntimeRequest(BaseModel):
    execution_engine: str = "cursor"
    autonomy_mode: str = "supervised"
    model: str | None = None
    workspace_dir: str | None = None
    allowed_tools: list[str] = Field(default_factory=lambda: ["cursor_agent.run"])


class CreateAgentRequest(BaseModel):
    name: str | None = None
    purpose: str | None = None
    instructions: str | None = None
    trigger_phrases: list[str] = Field(default_factory=list)
    status: str = "active"
    runtime: AgentRuntimeRequest = Field(default_factory=AgentRuntimeRequest)
    parent_agent_id: int | None = None
    learning_notes: str = ""
    authoring_prompt: str | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    purpose: str | None = None
    instructions: str | None = None
    trigger_phrases: list[str] | None = None
    status: str | None = None
    runtime: AgentRuntimeRequest | None = None
    parent_agent_id: int | None = None
    learning_notes: str | None = None


class InvokeAgentRequest(BaseModel):
    task: str = Field(min_length=1)
    session_id: str | None = None
    voice: bool = False


class SaveProviderKeyRequest(BaseModel):
    provider: str = Field(min_length=2)
    key: str = Field(min_length=8)
    env_name: str | None = None
    token: str | None = None


class GatewayChatRequest(BaseModel):
    message: str | None = None
    messages: list[dict] | None = None
    system: str | None = None
    capability: str = "chat"
    prefer: str | None = None
    temperature: float = 0.1
    max_tokens: int | None = None
    source: str = "device"
    token: str | None = None


class GatewayFimRequest(BaseModel):
    prefix: str
    suffix: str = ""
    max_tokens: int = 256
    token: str | None = None


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    token: str | None = None


class TtsRequest(BaseModel):
    text: str = Field(min_length=1)
    prefer_local: bool = False
    voice: str | None = None
    token: str | None = None


class CadRequest(BaseModel):
    prompt: str = Field(min_length=3)
    engine: str = "auto"
    image_path: str | None = None
    token: str | None = None


class IconRequest(BaseModel):
    name: str = Field(min_length=1)
    target: str | None = None
    kind: str = "auto"
    seed: str | None = None


@router.get("/dashboard")
async def dashboard():
    """Single fast poll for kiosk — health, voice, active work."""
    helper_status = await macos.health()
    ollama_status = await ollama.health()
    active = await tasks.list_tasks_by_status("running", limit=8)
    queued = await tasks.list_tasks_by_status("queued", limit=8)
    pending_approvals = await approvals.list_approvals(status="pending", limit=20)
    return {
        "agent": settings.agent_name,
        "voice_ui": voice_state.voice_ui_payload(helper_status),
        "willy": ollama_status,
        "ollama": ollama_status,
        "macos_helper": helper_status,
        "worker": worker.status(),
        "security": await security.status(),
        "remote_control": await remote_control.status(),
        "openclaw": await openclaw.health(),
        "tasks_active": active + queued,
        "approvals_pending": pending_approvals,
        "approval_count": len(pending_approvals),
    }


def _gateway_health() -> dict:
    from jarvis.services.providers import gateway, keys as provider_keys

    return {
        "mode": gateway.mode(),
        "free_keys": [p for p in provider_keys.configured_providers() if p != "ollama"],
        "last_provider": gateway.last_decision.get("provider"),
    }


@router.get("/health")
async def health():
    ollama_status = await ollama.health()
    helper_status = await macos.health()
    key = settings.resolved_cursor_api_key()
    security_status = await security.status()
    openclaw_status = await openclaw.health()
    sandbox = await self_modify.status()
    return {
        "agent": settings.agent_name,
        "willy": ollama_status,
        "ollama": ollama_status,
        "macos_helper": helper_status,
        "voice_ui": voice_state.voice_ui_payload(helper_status),
        "cursor": {"configured": settings.cursor_configured()},
        "openclaw": openclaw_status,
        "self_modify": sandbox,
        "scheduler": scheduler.status(),
        "worker": worker.status(),
        "security": security_status,
        "remote_control": await remote_control.status(),
        "execution": __import__("jarvis.services.local_runtime", fromlist=["execution_profile"]).execution_profile(),
        "vigil": vigil_metrics.status(),
        "gateway": _gateway_health(),
        "skills": {
            "external_enabled": settings.external_skills_enabled,
            "installed": skills.list_installed_skills(),
        },
        "port": settings.port,
    }


@router.get("/skills")
async def list_skills():
    return {
        "external_enabled": settings.external_skills_enabled,
        "skills": skills.list_installed_skills(),
    }


@router.get("/activity/stream")
async def activity_sse():
    async def event_generator():
        # Hello so clients connect immediately
        yield activity_stream.event_to_sse({"kind": "system", "title": "Live feed connected", "status": "done"})
        async for event in activity_stream.subscribe():
            yield activity_stream.event_to_sse(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/activity/recent")
async def activity_recent(limit: int = 40):
    events = await event_log.list_events(limit=limit)
    return {"events": events}


@router.get("/activity/frame/{frame_id}")
async def activity_frame(frame_id: str):
    path = activity_stream.frame_path(frame_id)
    if not path:
        raise HTTPException(status_code=404, detail="frame not found")
    return FileResponse(path, media_type="image/png")


@router.get("/system/map")
async def get_system_map():
    """Live topology: services, flows, capabilities, and runtime state."""
    return await system_map.build_map()


@router.get("/vigil/status")
async def vigil_status():
    from jarvis.services import vigil_proxy

    return {"metrics": vigil_metrics.status(), "proxy": vigil_proxy.status()}


@router.post("/vigil/test-signal")
async def vigil_test_signal(message: str = "William Agent test ping"):
    if not vigil_metrics.configured():
        return {
            "ok": False,
            "error": "Vigil Cloud API key (vgl_...) required for signals — the api.vigil.wtf URL is an LLM proxy, not a signal endpoint.",
            **vigil_metrics.status(),
        }
    vigil_metrics.emit_signal(message, signal_type="observation", metadata={"source": "api_test"})
    return {"ok": True, "queued": True, **vigil_metrics.status()}


@router.post("/vigil/test-chat")
async def vigil_test_chat(message: str = "Hello from William Agent"):
    from jarvis.services import vigil_proxy

    st = vigil_proxy.status()
    if not vigil_proxy.configured():
        return {"ok": False, "error": "Vigil proxy not ready", "status": st}
    try:
        reply = await vigil_proxy.chat(prompt=message, system="Reply in one short sentence.")
        return {"ok": True, "reply": reply, "status": st}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "status": st}


@router.post("/chat")
async def chat(req: ChatRequest):
    return await planner.handle_message(
        req.message,
        source=req.source,
        session_id=req.session_id,
        speaker_verified=req.speaker_verified,
        speaker_confidence=req.speaker_confidence,
    )


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str, limit: int = 80):
    if not await sessions.get_conversation(session_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"messages": await sessions.get_history(session_id, limit=limit)}


@router.get("/sessions")
async def list_sessions(source: str = "web", limit: int = 50, offset: int = 0):
    items = await sessions.list_conversations(source=source, limit=limit, offset=offset)
    return {"sessions": items, "count": len(items)}


class CreateSessionRequest(BaseModel):
    source: str = "web"


@router.post("/sessions")
async def create_session(req: CreateSessionRequest):
    created = await sessions.create_new(source=req.source)
    return {"session": created}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    if not await sessions.delete_conversation(session_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True, "id": session_id}


@router.get("/tasks/batch/{batch_id}")
async def get_batch_tasks(batch_id: str):
    return await tasks.list_batch_tasks(batch_id)


@router.get("/sessions/active")
async def active_session(source: str | None = None):
    session = await sessions.get_active(source)
    if not session:
        return {"active": False}
    history = await sessions.get_history(session["id"], limit=6)
    return {"active": True, "session": session, "recent": history}


@router.get("/tasks")
async def get_tasks():
    return await tasks.list_tasks()


@router.get("/approvals")
async def get_approvals(status: str | None = None):
    return await approvals.list_approvals(status=status)


@router.post("/approvals/{approval_id}")
async def resolve_approval(approval_id: int, req: ApprovalRequest):
    result = await approvals.resolve_approval(approval_id, req.approved)
    if not result:
        return {"error": "not found"}
    return result


@router.post("/netatmo/webhook")
async def netatmo_webhook(request: Request):
    payload = await request.json()
    return await netatmo.handle_webhook(payload)


@router.get("/people")
async def get_people():
    return await people.list_people()


@router.post("/people/name")
async def name_person(req: NamePersonRequest):
    return await netatmo.name_person(req.netatmo_id, req.name, req.face_url)


@router.delete("/people/{person_id}")
async def forget_person(person_id: int):
    ok = await people.forget_person(person_id)
    return {"ok": ok}


@router.post("/macos/screenshot")
async def take_screenshot():
    """Legacy alias — returns Accessibility desktop context (no Screen Recording)."""
    return await macos.desktop_context()


@router.get("/macos/desktop/context")
async def desktop_context():
    return await macos.desktop_context()


@router.post("/macos/notify")
async def send_notify(title: str, message: str, speak: bool = False):
    return await macos.notify(title, message, speak=speak)


@router.post("/macos/mute")
async def set_voice_mute(req: MuteRequest):
    return await macos.set_muted(req.muted)


@router.post("/voice/clear-transcript")
async def voice_clear_transcript():
    return await macos.clear_transcript()


@router.post("/voice/listen")
async def voice_listen():
    return await macos.listen_voice()


@router.post("/voice/ensure-awake")
async def ensure_voice_awake():
    return await macos.ensure_voice_awake()


@router.post("/voice/sleep")
async def voice_sleep():
    return await macos.sleep_voice()


@router.post("/voice/enroll/start")
async def voice_enroll_start():
    return await macos.start_guided_voice_enrollment()


@router.get("/voice/enroll/status")
async def voice_enroll_status():
    return await macos.voice_enrollment_status()


@router.post("/voice/enroll/cancel")
async def voice_enroll_cancel():
    return await macos.cancel_voice_enrollment()


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: int):
    from jarvis.services import goals

    detail = await goals.get_goal_detail(goal_id)
    if not detail:
        raise HTTPException(status_code=404, detail="goal not found")
    return detail


@router.post("/goals")
async def create_goal(req: CreateGoalRequest):
    from jarvis.services import goals

    created = await goals.create_goal_from_prompt(req.prompt, source=req.source)
    return created


@router.get("/agents")
async def list_specialist_agents(status: str | None = None):
    return {"agents": await agent_registry.list_agents(status=status)}


@router.get("/agents/{agent_id}")
async def get_specialist_agent(agent_id: int):
    agent = await agent_registry.get_agent_by_id(agent_id, include_inactive=True)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@router.post("/agents")
async def create_specialist_agent(req: CreateAgentRequest):
    try:
        if req.authoring_prompt:
            created = await agent_author.create_from_prompt(
                req.authoring_prompt,
                workspace_dir=req.runtime.workspace_dir,
                model=req.runtime.model,
            )
        else:
            from jarvis.services.agent_types import AgentSpec

            created = await agent_author.create_from_spec(
                AgentSpec(
                    name=req.name or "",
                    purpose=req.purpose or "",
                    instructions=req.instructions or "",
                    trigger_phrases=req.trigger_phrases,
                    status=req.status,
                    runtime=req.runtime.model_dump(),
                    parent_agent_id=req.parent_agent_id,
                    learning_notes=req.learning_notes,
                )
            )
        return created
    except ValueError as exc:
        detail = str(exc)
        if "already exists" in detail:
            raise HTTPException(status_code=409, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc


@router.patch("/agents/{agent_id}")
async def update_specialist_agent(agent_id: int, req: UpdateAgentRequest):
    try:
        updated = await agent_registry.update_agent(
            agent_id,
            req.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        detail = str(exc)
        if "already exists" in detail:
            raise HTTPException(status_code=409, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="agent not found")
    return updated


@router.post("/agents/{agent_id}/archive")
async def archive_specialist_agent(agent_id: int):
    archived = await agent_registry.archive_agent(agent_id)
    if not archived:
        raise HTTPException(status_code=404, detail="agent not found")
    return archived


@router.post("/agents/{agent_id}/invoke")
async def invoke_specialist_agent(agent_id: int, req: InvokeAgentRequest):
    result = await agent_runtime.invoke_agent(
        agent_id,
        req.task,
        voice=req.voice,
        conversation_id=req.session_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "agent invocation failed"))
    return result


@router.post("/goals/{goal_id}/approve")
async def approve_goal(goal_id: int):
    result = await goal_runner.approve_goal(goal_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "approve failed"))
    return result


@router.get("/builds")
async def list_builds(limit: int = 20):
    from jarvis.services import build_pipeline

    return {"builds": await build_pipeline.list_builds(limit=limit)}


@router.post("/builds")
async def create_build(req: CreateBuildRequest):
    from jarvis.services import build_pipeline

    created = await build_pipeline.start(
        req.prompt,
        source=req.source,
        workspace_path=req.workspace_path,
        session_id=req.session_id,
    )
    if not created.get("ok"):
        raise HTTPException(status_code=400, detail=created.get("error", "build failed"))
    return created


@router.get("/builds/{build_id}")
async def get_build(build_id: int):
    from jarvis.services import build_pipeline

    detail = await build_pipeline.get_build_detail(build_id)
    if not detail:
        raise HTTPException(status_code=404, detail="build not found")
    return detail


@router.post("/builds/{build_id}/approve-micro-prompts")
async def approve_build_micro_prompts(build_id: int, req: ApproveMicroPromptsRequest | None = None):
    from jarvis.services import build_pipeline

    result = await build_pipeline.approve_micro_prompts(
        build_id,
        edits=req.edits if req else None,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "approve failed"))
    return result


@router.post("/builds/{build_id}/approve-prd")
async def approve_build_prd(build_id: int):
    from jarvis.services import build_pipeline

    result = await build_pipeline.approve_prd(build_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "approve failed"))
    return result


@router.post("/builds/{build_id}/revise-prd")
async def revise_build_prd(build_id: int, req: RevisePrdRequest):
    from jarvis.services import build_pipeline

    result = await build_pipeline.revise_prd(build_id, req.feedback)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "revise failed"))
    return result


@router.post("/builds/{build_id}/stop")
async def stop_build(build_id: int):
    from jarvis.services import build_pipeline

    return await build_pipeline.stop(build_id)


@router.get("/github/status")
async def github_status():
    from jarvis.services import compute_fleet, github_sync

    return {
        "configured": github_sync.configured(),
        "hub_repo": github_sync.hub_repo_url() if github_sync.configured() else None,
        "owner": github_sync.resolved_owner() if github_sync.configured() else None,
        "fleet": await compute_fleet.fleet_status(),
    }


@router.post("/github/sync")
async def github_sync_now():
    from jarvis.services import github_sync, state_export

    if not github_sync.configured():
        raise HTTPException(status_code=400, detail="GitHub not configured")
    result = await state_export.export_all()
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "sync failed"))
    return result


@router.get("/security")
async def get_security():
    return await security.status()


@router.post("/security/full-access")
async def set_full_access(req: FullAccessRequest):
    return await security.set_full_access(req.enabled)


@router.post("/permissions/bootstrap")
async def permissions_bootstrap():
    from jarvis.services import permissions

    return await permissions.bootstrap()


@router.post("/permissions/prompt")
async def permissions_prompt():
    """User-initiated — triggers macOS TCC permission dialogs."""
    return await macos.prompt_permissions()


@router.post("/terminal/run")
async def run_terminal(req: TerminalRequest):
    full_access = await security.is_full_access()
    result = await terminal.run_command(req.command, full_access=full_access)
    await event_log.log_integration(
        "terminal",
        source="api",
        detail=req.command[:200],
        metadata={"ok": result.get("ok"), "stdout_len": len(result.get("stdout") or "")},
    )
    return result


class StartImproveRunRequest(BaseModel):
    duration_minutes: int = Field(ge=5, le=180, default=30)


@router.post("/self/improve-run/start")
async def start_improve_run(req: StartImproveRunRequest):
    return await improve_run.start(duration_minutes=req.duration_minutes)


@router.post("/self/improve-run/stop")
async def stop_improve_run():
    return await improve_run.stop()


@router.get("/self/improve-run/status")
async def improve_run_status():
    return await improve_run.get_status()


@router.post("/openclaw/reconnect")
async def openclaw_reconnect():
    return await openclaw.ensure_whatsapp()


@router.get("/self/status")
async def self_status():
    return await self_modify.status()


@router.post("/self/propose")
async def self_propose(req: SelfProposeRequest):
    return await self_modify.propose(req.description)


@router.post("/self/test")
async def self_test():
    return await self_modify.run_tests()


@router.post("/desktop/analyze")
async def desktop_analyze():
    return await desktop.analyze_screen()


@router.post("/scheduler/briefing")
async def trigger_briefing():
    await scheduler.morning_briefing()
    return {"ok": True}


@router.get("/learning/report")
async def get_learning_report():
    return await learning.get_report()


@router.post("/learning/refresh")
async def refresh_learning_report():
    return await learning.update_report(force=True)


@router.get("/memory")
async def get_memory(limit: int = 20):
    return {"entries": await memory.list_recent(limit=limit), "stats": await memory.stats()}


@router.post("/memory/compress")
async def compress_memory():
    return await memory.compress_stale_sessions()


@router.get("/events")
async def get_events(limit: int = 50, source: str | None = None, event_type: str | None = None):
    return {
        "events": await event_log.list_events(limit=limit, source=source, event_type=event_type),
        "stats": await event_log.stats(),
    }


@router.post("/events/export-notion")
async def export_events_notion(limit: int = 40):
    events = await event_log.list_events(limit=limit)
    return await notion_sync.export_recent_events(events)


@router.get("/notion/status")
async def notion_status():
    return await notion_sync.diagnostics()


class ScreenEventsRequest(BaseModel):
    events: list[dict] = Field(default_factory=list)


class ScreenContextRequest(BaseModel):
    minutes: int = Field(ge=5, le=180, default=30)
    query: str | None = None
    detail_ids: list[int] = Field(default_factory=list)


@router.post("/screen/events")
async def screen_ingest_events(req: ScreenEventsRequest):
    return await screen_observer.ingest_events(req.events)


@router.get("/screen/status")
async def screen_status():
    helper = await macos.screen_watcher_status()
    observer = await screen_observer.status()
    return {"helper": helper, "observer": observer}


@router.get("/screen/context")
async def screen_context(minutes: int = 30, query: str | None = None, detail_ids: str | None = None):
    ids: list[int] = []
    if detail_ids:
        ids = [int(x) for x in detail_ids.split(",") if x.strip().isdigit()]
    block = await screen_observer.get_recent_context(minutes=minutes, query=query, detail_ids=ids or None)
    return {"context": block}


@router.post("/screen/summarize-now")
async def screen_summarize_now():
    return await screen_observer.observer_tick()


@router.post("/screen/pause")
async def screen_pause():
    return await macos.screen_watcher_pause()


@router.post("/screen/resume")
async def screen_resume():
    return await macos.screen_watcher_resume()


@router.get("/cursor/runs")
async def list_cursor_runs(limit: int = 20, source: str | None = None):
    return {"runs": await cursor_trace.list_runs(limit=limit, source=source)}


@router.get("/cursor/runs/{run_db_id}")
async def get_cursor_run(run_db_id: int):
    run = await cursor_trace.get_run(run_db_id)
    if not run:
        raise HTTPException(status_code=404, detail="cursor run not found")
    return run


@router.get("/cursor/runs/{run_db_id}/transcript")
async def get_cursor_transcript(run_db_id: int):
    text = await cursor_trace.format_transcript(run_db_id)
    if not text:
        raise HTTPException(status_code=404, detail="cursor run not found")
    return {"transcript": text}


@router.post("/desktop/handle-popups")
async def desktop_handle_popups():
    from jarvis.services import popup_handler

    return await popup_handler.handle_popups(full_control=await security.is_full_access())


async def _ensure_remote_control() -> None:
    if not await remote_control.is_enabled():
        raise HTTPException(status_code=403, detail="remote control disabled")


@router.get("/remote/control")
async def get_remote_control():
    return await remote_control.status()


@router.post("/remote/control")
async def set_remote_control(req: RemoteControlRequest):
    return await remote_control.set_enabled(req.enabled)


@router.post("/remote/mousemove")
async def remote_mousemove(req: RemotePointRequest):
    await _ensure_remote_control()
    return await macos.mouse_move(req.x, req.y)


@router.post("/remote/mousedown")
async def remote_mousedown(req: RemotePointRequest):
    await _ensure_remote_control()
    return await macos.mouse_down(req.x, req.y, button=req.button)


@router.post("/remote/mouseup")
async def remote_mouseup(req: RemotePointRequest):
    await _ensure_remote_control()
    return await macos.mouse_up(req.x, req.y, button=req.button)


@router.post("/remote/click")
async def remote_click(req: RemotePointRequest):
    await _ensure_remote_control()
    return await macos.click(req.x, req.y, button=req.button)


@router.post("/remote/type")
async def remote_type(req: RemoteTypeRequest):
    await _ensure_remote_control()
    return await macos.type_text(req.text)


@router.post("/remote/key")
async def remote_key(req: RemoteKeyRequest):
    await _ensure_remote_control()
    return await macos.press_key(req.key, modifiers=req.modifiers)


@router.get("/minis/status")
async def minis_status():
    return await minis.status()


@router.post("/minis/screen-share")
async def minis_screen_share(req: ScreenShareRequest):
    return await minis.set_screen_share(req.enabled)


@router.get("/minis/screen/frame")
async def minis_screen_frame():
    frame = await minis.get_screen_frame()
    if not frame:
        raise HTTPException(status_code=404, detail="no frame available")
    return Response(content=frame, media_type=minis.frame_content_type(frame))


@router.post("/minis/input")
async def minis_input(req: MinisInputRequest):
    if req.type not in ("tap", "move", "down", "up"):
        raise HTTPException(status_code=400, detail="invalid input type")
    result = await minis.handle_input(req.type, req.x, req.y)
    if not result.get("ok"):
        detail = result.get("error", "failed")
        code = 403 if "disabled" in str(detail).lower() else 400
        raise HTTPException(status_code=code, detail=detail)
    return result


@router.get("/minis/info")
async def minis_info():
    return await minis_ops.service_info()


@router.post("/minis/restart")
async def minis_restart(req: MinisRestartRequest):
    target = req.target.strip().lower()
    if target not in ("both", "helper", "core"):
        raise HTTPException(status_code=400, detail="target must be both, helper, or core")
    return await minis_ops.restart_services(target=target)


@router.post("/minis/hard-reset")
async def minis_hard_reset():
    return await minis_ops.hard_reset_services()


@router.post("/minis/update/helper")
async def minis_update_helper(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    result = await minis_ops.apply_helper_binary(data)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "update failed"))
    return result


@router.post("/minis/update/helper-binary")
async def minis_update_helper_binary(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    result = await minis_ops.apply_helper_binary(data)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "update failed"))
    return result


@router.post("/minis/update/ui")
async def minis_update_ui(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    return await minis_ops.apply_minis_ui(data)


@router.post("/minis/update/build")
async def minis_build_helper():
    return await minis_ops.build_helper_from_source()


@router.get("/fleet/status")
async def get_fleet_status():
    from jarvis.services import compute_fleet

    return await compute_fleet.fleet_status()


def _fleet_token(
    req_token: str | None = None,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
) -> None:
    fleet_auth.verify_fleet_token(
        fleet_auth.token_from_request(body_token=req_token, header_token=x_jarvis_fleet_token)
    )


@router.post("/fleet/register")
async def fleet_register(
    req: FleetRegisterRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    node = await fleet_registry.register_node(req)
    return {"ok": True, "node": node.model_dump()}


@router.post("/fleet/heartbeat")
async def fleet_heartbeat(
    req: FleetHeartbeatRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    node = await fleet_registry.heartbeat(req)
    if not node:
        raise HTTPException(status_code=404, detail="node not registered")
    return {"ok": True, "node": node.model_dump()}


@router.post("/fleet/enqueue")
async def fleet_enqueue(
    req: FleetEnqueueRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    return await fleet_router.enqueue_routed(
        title=req.title,
        tag=req.tag,
        body=req.body,
        command=req.command,
        preferred_role=req.preferred_role,
        required_capabilities=req.required_capabilities,
    )


@router.post("/fleet/claim")
async def fleet_claim(
    req: FleetClaimRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    node = await fleet_registry.get_node_by_name(req.name)
    if not node:
        raise HTTPException(status_code=404, detail="node not registered")
    job = await fleet_registry.claim_job(node=node, tags=req.tags, lease_seconds=req.lease_seconds)
    return {"ok": True, "job": job.model_dump() if job else None}


@router.post("/fleet/result")
async def fleet_result(
    req: FleetResultRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    node = await fleet_registry.get_node_by_name(req.name)
    if not node:
        raise HTTPException(status_code=404, detail="node not registered")
    job = await fleet_registry.complete_job(
        job_id=req.job_id,
        node=node,
        ok=req.ok,
        output=req.output,
        error=req.error,
    )
    if not job:
        raise HTTPException(status_code=404, detail="job not found or not owned by node")
    return {"ok": True, "job": job.model_dump()}


@router.get("/fleet/jobs")
async def fleet_jobs(status: str | None = None, limit: int = 50):
    jobs = await fleet_registry.list_jobs(status=status, limit=min(limit, 200))
    return {"jobs": [j.model_dump() for j in jobs]}


@router.post("/fleet/wake/{node_name}")
async def fleet_wake_node(
    node_name: str,
    token: str | None = None,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(token, x_jarvis_fleet_token)
    return await fleet_power.wake_node_by_name(node_name)


@router.post("/fleet/wake-pc")
async def fleet_wake_pc(
    token: str | None = None,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(token, x_jarvis_fleet_token)
    return await fleet_power.wake_pc()


# ─── Free AI provider gateway (one backend for every device) ────────────────


@router.get("/providers")
async def providers_overview():
    from jarvis.services.providers import scout

    return await scout.overview()


@router.get("/providers/keys")
async def providers_keys():
    from jarvis.services.providers import keys as provider_keys

    return provider_keys.report()


@router.post("/providers/scan")
async def providers_scan():
    from jarvis.services.providers import scout

    return await scout.scan_keys()


@router.post("/providers/probe")
async def providers_probe(provider: str | None = None):
    from jarvis.services.providers import scout

    if provider:
        return await scout.probe(provider)
    return await scout.probe_all()


@router.post("/providers/keys")
async def providers_save_key(
    req: SaveProviderKeyRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    from jarvis.services.providers import keys as provider_keys, scout

    result = provider_keys.save_key(req.provider, req.key, env_name=req.env_name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "key rejected"))
    result["probe"] = await scout.probe(req.provider)
    await scout.scan_keys(announce=False)
    return result


@router.delete("/providers/keys/{provider}")
async def providers_delete_key(
    provider: str,
    token: str | None = None,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(token, x_jarvis_fleet_token)
    from jarvis.services.providers import keys as provider_keys

    return provider_keys.remove_key(provider)


@router.get("/providers/usage")
async def providers_usage(days: int = 7, recent: int = 0):
    from jarvis.services.providers import usage as provider_usage

    data = await provider_usage.summary(days=max(1, min(days, 90)))
    if recent:
        data["recent"] = await provider_usage.recent(limit=recent)
    return data


@router.get("/providers/shopping-list")
async def providers_shopping_list():
    from jarvis.services.providers import scout

    return {"missing": await scout.shopping_list()}


@router.post("/providers/open-signup/{provider}")
async def providers_open_signup(provider: str):
    from jarvis.services.providers import scout

    return scout.open_signup(provider)


@router.get("/gateway/status")
async def gateway_status():
    from jarvis.services.providers import gateway

    return await gateway.status()


@router.post("/gateway/chat")
async def gateway_chat(
    req: GatewayChatRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    """Devices (MacBook / PC) call this single backend instead of holding their own keys."""
    _fleet_token(req.token, x_jarvis_fleet_token)
    from jarvis.services.providers import gateway

    try:
        return await gateway.chat_detailed(
            req.messages,
            prompt=req.message,
            system=req.system,
            capability=req.capability,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            source=req.source,
            prefer=req.prefer,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:400])


@router.post("/gateway/fim")
async def gateway_fim(
    req: GatewayFimRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    from jarvis.services.providers import gateway

    return await gateway.fim(req.prefix, req.suffix, max_tokens=req.max_tokens)


@router.post("/gateway/embed")
async def gateway_embed(
    req: EmbedRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    from jarvis.services.providers import vectors

    try:
        return await vectors.embed(req.texts)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)[:400])


@router.get("/memory/semantic")
async def memory_semantic(q: str, limit: int = 5):
    from jarvis.services.providers import vectors

    return {"hits": await vectors.semantic_search(q, limit=limit), "stats": await vectors.stats()}


@router.get("/speech/status")
async def speech_status():
    from jarvis.services.providers import speech

    return speech.status()


@router.post("/speech/transcribe")
async def speech_transcribe(
    file: UploadFile = File(...),
    language: str | None = None,
    token: str | None = None,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(token, x_jarvis_fleet_token)
    import tempfile
    from pathlib import Path as _Path

    from jarvis.services.providers import speech

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    suffix = _Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return await speech.transcribe(tmp_path, language=language)
    finally:
        try:
            _Path(tmp_path).unlink()
        except Exception:
            pass


@router.post("/speech/tts")
async def speech_tts(
    req: TtsRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    from jarvis.services.providers import speech

    result = await speech.synthesize(req.text, prefer_local=req.prefer_local, voice=req.voice)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error", "tts failed"))
    return FileResponse(result["path"], media_type=result.get("content_type", "audio/aiff"),
                        headers={"X-Provider": result.get("provider", "")})


@router.get("/cad/status")
async def cad_status():
    from jarvis.services.providers import cad

    return cad.status()


@router.post("/cad/generate")
async def cad_generate(
    req: CadRequest,
    x_jarvis_fleet_token: str | None = Header(default=None, alias="X-Jarvis-Fleet-Token"),
):
    _fleet_token(req.token, x_jarvis_fleet_token)
    from jarvis.services.providers import cad

    return await cad.generate(req.prompt, engine=req.engine, image_path=req.image_path)


@router.post("/icons/generate")
async def icons_generate(req: IconRequest):
    from jarvis.services import app_icons

    if req.target:
        return app_icons.apply(req.target, name=req.name, kind=req.kind, seed=req.seed)
    return app_icons.generate_icon_set(req.name, seed=req.seed)


@router.post("/icons/apply-all")
async def icons_apply_all():
    from jarvis.services import app_icons

    return app_icons.apply_everywhere()


# --------------------------------------------------------------------------- Orchestra: spans, journal, system, link


class JournalWrite(BaseModel):
    kind: str
    summary: str
    detail: str | None = None
    agent: str | None = None
    severity: int = 1


class GovernorAction(BaseModel):
    action: str
    target: int | None = None


class GovernorPolicy(BaseModel):
    updates: dict


class OfferStatus(BaseModel):
    status: str


class SelfHealRequest(BaseModel):
    apply: bool = True
    run_tests: bool = False


@router.get("/spans")
async def spans_list(trace_id: str | None = None, agent: str | None = None, since: str | None = None, limit: int = 200):
    from jarvis.services import spans

    return {"spans": await spans.list_spans(trace_id=trace_id, agent=agent, since=since, limit=limit),
            "active": await spans.active()}


@router.get("/spans/trace/{trace_id}")
async def spans_trace(trace_id: str):
    from jarvis.services import spans

    return await spans.trace_tree(trace_id)


@router.get("/tokens")
async def tokens_overview(days: int = 7):
    from jarvis.services import spans
    from jarvis.services.providers import usage

    return {"spans": await spans.token_rollup(days=days), "ledger": await usage.by_agent_device(days=days)}


@router.get("/journal")
async def journal_list(kind: str | None = None, agent: str | None = None, device: str | None = None,
                       unresolved: bool = False, since: str | None = None, limit: int = 100):
    from jarvis.services import journal

    return {"entries": await journal.list_entries(kind=kind, agent=agent, device=device, unresolved_only=unresolved,
                                                  since=since, limit=limit),
            "stats": await journal.stats(days=7)}


@router.post("/journal")
async def journal_write(req: JournalWrite):
    from jarvis.services import journal

    return await journal.write(req.kind, req.summary, detail=req.detail, agent=req.agent, severity=req.severity,
                               source="api")


@router.post("/journal/{entry_id}/resolve")
async def journal_resolve(entry_id: int):
    from jarvis.services import journal

    return {"ok": await journal.resolve(entry_id, by="user")}


@router.get("/system/live")
async def system_live():
    from jarvis.services import link, resource_governor

    data = resource_governor.live()
    data["peers"] = [{"role": p["role"], "hostname": p["hostname"], "system": p["system"],
                      "last_heartbeat": p["last_heartbeat"]} for p in link.status()["peers"]]
    return data


@router.post("/system/sample")
async def system_sample_now():
    from jarvis.services import resource_governor

    return await resource_governor.tick()


@router.post("/system/governor")
async def system_governor_action(req: GovernorAction):
    from jarvis.services import resource_governor

    return await resource_governor.override(req.action, target=req.target, by="studio")


@router.post("/system/governor/policy")
async def system_governor_policy(req: GovernorPolicy):
    from jarvis.services import resource_governor

    return {"policy": resource_governor.set_policy(req.updates)}


@router.get("/brain")
async def brain_info():
    from jarvis.brain import persona

    return persona.describe()


@router.get("/agent-graph")
async def agents_graph():
    return await agent_runtime.dependency_graph()


@router.get("/link/status")
async def link_status():
    from jarvis.services import link

    return link.status()


class LinkPrompt(BaseModel):
    text: str
    session_id: str | None = None
    voice: bool = False


@router.post("/link/prompt")
async def link_prompt(req: LinkPrompt):
    """Forward a prompt to the connected peer (MacBook → Mini)."""
    from jarvis.services import link

    return await link.send_prompt(req.text, session_id=req.session_id, voice=req.voice)


@router.websocket("/ws/link")
async def link_ws(websocket: WebSocket):
    from jarvis.services import link

    token = websocket.headers.get("x-jarvis-fleet-token") or websocket.query_params.get("token")
    try:
        fleet_auth.verify_fleet_token(token)
    except HTTPException:
        await websocket.close(code=1008, reason="invalid fleet token")
        return
    await websocket.accept()
    client = websocket.client
    await link.serve(websocket, remote=f"{client.host}:{client.port}" if client else "unknown")


@router.post("/selfheal")
async def selfheal_run(req: SelfHealRequest | None = None):
    from jarvis.services import selfheal

    req = req or SelfHealRequest()
    return await selfheal.run(apply=req.apply, run_tests=req.run_tests, by="api")


@router.get("/selfheal")
async def selfheal_status():
    from jarvis.services import selfheal

    return {"last": selfheal.last_report(), "diagnosis": await selfheal.diagnose()}


@router.get("/mentor/milestones")
async def mentor_milestones():
    from jarvis.services import mentor

    return {"milestones": await mentor.detect_milestones()}


@router.post("/mentor/coach")
async def mentor_coach(force: bool = False):
    from jarvis.services import mentor

    return await mentor.maybe_coach(force=force)


@router.get("/providers/offers")
async def provider_offers(status: str | None = None, limit: int = 100):
    from jarvis.services.providers import hunter

    return {"offers": await hunter.list_offers(status=status, limit=limit)}


@router.post("/providers/offers/hunt")
async def provider_offers_hunt():
    from jarvis.services.providers import hunter

    return await hunter.hunt(notify=True)


@router.post("/providers/offers/{offer_id}")
async def provider_offer_status(offer_id: int, req: OfferStatus):
    from jarvis.services.providers import hunter

    return {"ok": await hunter.set_offer_status(offer_id, req.status)}


@router.get("/bootstrap/macbook.sh")
async def bootstrap_macbook_script(request: Request):
    """One-line MacBook bootstrap: curl -fsSL http://<mini>:8787/api/bootstrap/macbook.sh | bash"""
    path = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap-macbook.sh"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="bootstrap script missing")
    text = path.read_text(encoding="utf-8")
    host = request.headers.get("host", f"127.0.0.1:{settings.port}")
    text = text.replace("__MINI_HOST__", host.split(":")[0]).replace("__FLEET_TOKEN__", settings.fleet_token or "")
    return Response(content=text, media_type="text/x-shellscript")


@router.websocket("/remote/ws")
async def remote_control_ws(websocket: WebSocket):
    await websocket.accept()
    if not await remote_control.is_enabled():
        await websocket.close(code=1008, reason="remote control disabled")
        return
    try:
        while True:
            payload = await websocket.receive_json()
            action = str(payload.get("action", "")).strip().lower()
            if not action:
                await websocket.send_json({"ok": False, "error": "missing action"})
                continue
            result = await macos.dispatch_remote_action(action, payload)
            await websocket.send_json({"action": action, **result})
    except WebSocketDisconnect:
        return
