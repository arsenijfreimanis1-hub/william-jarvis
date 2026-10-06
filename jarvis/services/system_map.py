"""Live topology map of William Agent — nodes, flows, capabilities, and runtime state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jarvis.config import settings
from jarvis.services import (
    approvals,
    macos,
    ollama,
    openclaw,
    remote_control,
    scheduler,
    screen_observer,
    security,
    sessions,
    tasks,
    voice_state,
    worker,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(ok: bool | None, *, degraded: bool = False) -> str:
    if ok is True:
        return "degraded" if degraded else "online"
    if ok is False:
        return "offline"
    return "unknown"


def _node(
    node_id: str,
    label: str,
    *,
    category: str,
    status: str,
    detail: str = "",
    port: int | None = None,
    host: str = "127.0.0.1",
    capabilities: list[str] | None = None,
    metrics: dict | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "category": category,
        "status": status,
        "detail": detail,
        "host": host,
        "port": port,
        "capabilities": capabilities or [],
        "metrics": metrics or {},
    }


def _edge(source: str, target: str, label: str, *, active: bool = False, kind: str = "data") -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "label": label,
        "active": active,
        "kind": kind,
    }


def _capability_groups() -> list[dict[str, Any]]:
    return [
        {
            "id": "voice",
            "title": "Voice & conversation",
            "items": [
                "Wake word (Hey Willy) with 180s conversation mode",
                "STT/TTS via JarvisHelper",
                "Speaker verification gate",
                "Proactive speak on background task completion",
            ],
        },
        {
            "id": "action",
            "title": "Actions & automation",
            "items": [
                "Background task queue (6 parallel workers)",
                "Parallel terminal shells (up to 4 concurrent)",
                "Multi-step goals with one upfront approval",
                "macOS app control, Spotify, terminal (gated)",
                "Desktop vision + click/type (approval or full access)",
                "Self-modify sandbox with merge approval",
            ],
        },
        {
            "id": "brain",
            "title": "Reasoning engines",
            "items": [
                "Free gateway: Codestral / Gemini 2.5 Flash (1M ctx) / Groq / OpenRouter / HF by capability",
                f"Local chat ({settings.ollama_model}) — free, always the fallback",
                f"Vision ({settings.ollama_vision_model})",
                "Web research + calculator + weather",
                "Cursor cloud only for large builds (when configured)",
                "Speech: Groq Whisper / HF TTS; 3D: OpenSCAD + Blender scripts, HF Spaces meshes",
            ],
        },
        {
            "id": "memory",
            "title": "Memory & learning",
            "items": [
                "Conversation sessions + FTS memory",
                "Failure lessons injected into prompts",
                "Optional Notion event export",
                "Optional GitHub state sync",
            ],
        },
        {
            "id": "observe",
            "title": "Observation (opt-in)",
            "items": [
                "Screen watcher (off by default)",
                "Screen summaries every 60s",
                "Popup handler (full access only)",
            ],
        },
    ]


async def build_map(*, active_session: dict | None = None) -> dict[str, Any]:
    """Aggregate live system topology for the map UI."""
    helper = await macos.health()
    ollama_h = await ollama.health()
    openclaw_h = await openclaw.health()
    sec = await security.status()
    remote = await remote_control.status()
    worker_st = worker.status()
    sched = scheduler.status()
    voice = voice_state.voice_ui_payload(helper)

    running = await tasks.list_tasks_by_status("running", limit=12)
    queued = await tasks.list_tasks_by_status("queued", limit=12)
    pending = await approvals.list_approvals(status="pending", limit=20)

    if active_session is None:
        active_session = await sessions.get_active()

    voice_active = voice.get("state") in ("conversation", "awaiting", "busy", "speaking")
    worker_active = worker_st.get("active_jobs", 0) > 0 or bool(running or queued)
    screen_on = settings.screen_watch_enabled

    from jarvis.services.providers import gateway, keys as provider_keys

    free_keys = [p for p in provider_keys.configured_providers() if p != "ollama"]

    nodes: list[dict[str, Any]] = [
        _node(
            "user",
            "You",
            category="input",
            status="online",
            detail="Voice, chat, kiosk, WhatsApp",
            capabilities=["Speak", "Type in chat", "Approve goals"],
        ),
        _node(
            "helper",
            "JarvisHelper",
            category="service",
            status=_status(helper.get("ok"), degraded=not helper.get("healthy", True)),
            detail=voice.get("label", ""),
            port=8788,
            capabilities=["Wake word", "STT/TTS", "Screenshots", "Desktop input"],
            metrics={
                "voice_state": voice.get("state"),
                "sleeping": helper.get("sleeping"),
                "conversation_mode": helper.get("conversation_mode"),
            },
        ),
        _node(
            "core",
            "JarvisCore",
            category="service",
            status="online",
            detail=settings.agent_name,
            port=settings.port,
            capabilities=["Planner", "Router", "Approvals", "Scheduler", "Worker"],
            metrics={"full_access": sec.get("full_access"), "remote_control": remote.get("remote_control_enabled")},
        ),
        _node(
            "ollama",
            "Ollama",
            category="brain",
            status=_status(ollama_h.get("ok")),
            detail=settings.ollama_model,
            port=11434,
            capabilities=["Local chat", "Intent assist", "Vision"],
            metrics={"model": settings.ollama_model},
        ),
        _node(
            "gateway",
            "Free AI gateway",
            category="brain",
            status="online" if free_keys else "degraded",
            detail=(", ".join(free_keys) if free_keys else "no free keys yet — Ollama only"),
            capabilities=["Code (Codestral/Gemini)", "Fast (Groq)", "1M context (Gemini)", "Whisper STT",
                          "Embeddings", "CAD scripts"],
            metrics={"mode": gateway.mode(), "last_provider": gateway.last_decision.get("provider")},
        ),
        _node(
            "cursor",
            "Cursor Cloud",
            category="brain",
            status="online" if settings.cursor_configured() else "offline",
            detail="composer-2.5" if settings.cursor_configured() else "API key not set",
            capabilities=["Hard reasoning", "Codegen", "Multi-file edits"],
        ),
        _node(
            "web",
            "Web tools",
            category="brain",
            status="online",
            detail="DuckDuckGo + Open-Meteo + calculator",
            capabilities=["Search", "Weather", "Calculator"],
        ),
        _node(
            "worker",
            "Background worker",
            category="executor",
            status="online" if worker_st.get("running") else "offline",
            detail=f"{worker_st.get('active_jobs', 0)} active · parallel {worker_st.get('parallel', 0)}",
            capabilities=["Queue drain", "Proactive TTS", "Goal runner hook"],
            metrics=worker_st,
        ),
        _node(
            "scheduler",
            "Scheduler",
            category="executor",
            status="online" if sched.get("running") else "offline",
            detail=f"{len(sched.get('jobs', []))} cron jobs",
            metrics={"jobs": sched.get("jobs", [])},
        ),
        _node(
            "memory",
            "SQLite memory",
            category="store",
            status="online",
            detail=str(settings.data_dir / "jarvis.db"),
            capabilities=["Sessions", "Tasks", "Approvals", "FTS memory"],
        ),
        _node(
            "openclaw",
            "OpenClaw",
            category="input",
            status=_status(openclaw_h.get("ok")),
            detail="WhatsApp bridge",
            port=18789,
            capabilities=["WhatsApp → /api/chat"],
        ),
        _node(
            "kiosk",
            "William Kiosk",
            category="ui",
            status="unknown",
            detail="Fullscreen home UI",
            capabilities=["Voice hero", "Goal approval", "Task tree"],
        ),
        _node(
            "desktop",
            "William Desktop",
            category="ui",
            status="unknown",
            detail="Chat window",
            capabilities=["Web chat UI", "Live activity rail"],
        ),
        _node(
            "map",
            "System Map",
            category="ui",
            status="online",
            detail="This view",
            capabilities=["Live topology", "Activity overlay"],
        ),
    ]

    if screen_on:
        nodes.append(
            _node(
                "screen",
                "Screen observer",
                category="observe",
                status="online",
                detail="Capture + summarize",
                capabilities=["Screen capture", "OCR", "Privacy exclusions"],
            )
        )

    edges = [
        _edge("user", "helper", "voice", active=voice_active, kind="voice"),
        _edge("user", "core", "chat / goals", active=bool(active_session), kind="http"),
        _edge("user", "kiosk", "touch / voice", kind="http"),
        _edge("user", "desktop", "chat", kind="http"),
        _edge("openclaw", "core", "POST /api/chat", active=openclaw_h.get("ok"), kind="http"),
        _edge("helper", "core", "POST /api/chat", active=voice_active, kind="http"),
        _edge("core", "gateway", "route by capability", active=voice_active or worker_active, kind="inference"),
        _edge("gateway", "ollama", "fallback", active=not free_keys, kind="inference"),
        _edge("core", "ollama", "chat / intent", active=voice_active or worker_active, kind="inference"),
        _edge("core", "cursor", "reason / code", active=worker_active, kind="inference"),
        _edge("core", "web", "fact lookup", kind="http"),
        _edge("core", "worker", "queue tasks", active=worker_active, kind="queue"),
        _edge("worker", "helper", "TTS / notify", active=worker_active, kind="voice"),
        _edge("core", "memory", "read / write", active=True, kind="storage"),
        _edge("scheduler", "core", "cron jobs", active=sched.get("running"), kind="internal"),
        _edge("core", "helper", "speak / screenshot", active=voice_active, kind="http"),
    ]
    if screen_on:
        edges.append(_edge("helper", "core", "screen events", active=True, kind="observe"))
        edges.append(_edge("core", "screen", "summarize", active=True, kind="observe"))

    live = {
        "voice": voice,
        "session": active_session,
        "tasks_running": len(running),
        "tasks_queued": len(queued),
        "tasks": (running + queued)[:16],
        "approvals_pending": len(pending),
        "approvals": pending[:8],
        "worker": worker_st,
        "security": sec,
        "remote_control": remote,
    }

    return {
        "ts": _now(),
        "agent": settings.agent_name,
        "nodes": nodes,
        "edges": edges,
        "capabilities": _capability_groups(),
        "live": live,
        "ports": {
            "jarvis_core": settings.port,
            "jarvis_helper": 8788,
            "ollama": 11434,
            "openclaw": 18789,
        },
    }
