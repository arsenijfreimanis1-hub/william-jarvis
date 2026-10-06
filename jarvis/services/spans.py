"""Tracing spans: every orchestrator → agent → tool call is a span.

Spans are the backbone of the live call graph in William Studio and of per-agent /
per-device token attribution. A span carries `trace_id`, `span_id`, `parent_span_id`,
`device`, `agent`, `kind`, timings, tokens and status. The current span lives in a
contextvar so nested calls (requires-resolution, gateway calls) attach automatically.

Persistence is SQLite (`spans` table); live delivery is `activity_stream` (SSE) and the
device link. Never raises into callers — tracing must not break work.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import aiosqlite

from jarvis.config import settings
from jarvis.database import DB_PATH
from jarvis.services import activity_stream

log = logging.getLogger("jarvis.spans")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    device TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    agent TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms INTEGER,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    provider TEXT,
    model TEXT,
    task_id INTEGER,
    input TEXT,
    output TEXT,
    error TEXT,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, started_at);
CREATE INDEX IF NOT EXISTS idx_spans_started ON spans(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_spans_agent ON spans(agent, started_at DESC);
"""


class Span:
    __slots__ = (
        "span_id", "trace_id", "parent_span_id", "device", "kind", "name", "agent", "status",
        "started_at", "started_mono", "ended_at", "duration_ms", "prompt_tokens",
        "completion_tokens", "provider", "model", "task_id", "input", "output", "error", "metadata",
    )

    def __init__(
        self,
        *,
        kind: str,
        name: str,
        agent: str | None = None,
        parent: "Span | None" = None,
        trace_id: str | None = None,
        task_id: int | None = None,
        input_text: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.span_id = uuid.uuid4().hex
        self.trace_id = trace_id or (parent.trace_id if parent else uuid.uuid4().hex)
        self.parent_span_id = parent.span_id if parent else None
        self.device = device_name()
        self.kind = kind
        self.name = name
        self.agent = agent or (parent.agent if parent else None)
        self.status = "running"
        self.started_at = _now()
        self.started_mono = time.monotonic()
        self.ended_at: str | None = None
        self.duration_ms: int | None = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.provider: str | None = None
        self.model: str | None = None
        self.task_id = task_id if task_id is not None else (parent.task_id if parent else None)
        self.input = (input_text or "")[:2000] or None
        self.output: str | None = None
        self.error: str | None = None
        self.metadata: dict = dict(metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "device": self.device,
            "kind": self.kind,
            "name": self.name,
            "agent": self.agent,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.prompt_tokens + self.completion_tokens,
            "provider": self.provider,
            "model": self.model,
            "task_id": self.task_id,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }


_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar("jarvis_span", default=None)
_link_sinks: list = []  # callables(dict) -> awaitable, registered by the device link


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def device_name() -> str:
    return getattr(settings, "role", "mini") or "mini"


def current() -> Span | None:
    return _current.get()


def current_trace_id() -> str | None:
    span = _current.get()
    return span.trace_id if span else None


def root() -> Span | None:
    """The outermost running span of the current trace (the task boundary)."""
    span = _current.get()
    while span is not None and span.parent_span_id:
        parent = _parent_lookup.get(span.parent_span_id)
        if parent is None:
            break
        span = parent
    return span


def register_sink(sink) -> None:
    """Register an async callable receiving every span event (used by the device link)."""
    if sink not in _link_sinks:
        _link_sinks.append(sink)


def unregister_sink(sink) -> None:
    try:
        _link_sinks.remove(sink)
    except ValueError:
        pass


async def ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def _persist(span: Span) -> None:
    try:
        await ensure_tables()
        d = span.to_dict()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO spans (span_id, trace_id, parent_span_id, device, kind, name, agent, status,
                    started_at, ended_at, duration_ms, prompt_tokens, completion_tokens, provider, model,
                    task_id, input, output, error, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(span_id) DO UPDATE SET
                    status = excluded.status, ended_at = excluded.ended_at, duration_ms = excluded.duration_ms,
                    prompt_tokens = excluded.prompt_tokens, completion_tokens = excluded.completion_tokens,
                    provider = excluded.provider, model = excluded.model, output = excluded.output,
                    error = excluded.error, metadata = excluded.metadata
                """,
                (
                    d["span_id"], d["trace_id"], d["parent_span_id"], d["device"], d["kind"], d["name"],
                    d["agent"], d["status"], d["started_at"], d["ended_at"], d["duration_ms"],
                    d["prompt_tokens"], d["completion_tokens"], d["provider"], d["model"], d["task_id"],
                    d["input"], (d["output"] or "")[:4000] or None, (d["error"] or "")[:1000] or None,
                    json.dumps(d["metadata"], default=str),
                ),
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("span persist failed: %s", exc)


async def _emit(span: Span, event: str) -> None:
    payload = {"event": event, **span.to_dict()}
    try:
        await activity_stream.broadcast(
            {
                "kind": "span",
                "title": f"{span.kind}:{span.name}",
                "detail": span.agent or "",
                "status": span.status,
                "engine": span.provider,
                "metadata": payload,
            }
        )
    except Exception:
        pass
    for sink in list(_link_sinks):
        try:
            await sink(payload)
        except Exception:
            pass


def add_tokens(prompt_tokens: int = 0, completion_tokens: int = 0, *, provider: str | None = None,
               model: str | None = None) -> None:
    """Attribute tokens to the current span (and up the chain). Called by the provider gateway."""
    span = _current.get()
    while span is not None:
        span.prompt_tokens += int(prompt_tokens or 0)
        span.completion_tokens += int(completion_tokens or 0)
        if provider and not span.provider:
            span.provider = provider
        if model and not span.model:
            span.model = model
        span = _parent_lookup.get(span.parent_span_id) if span.parent_span_id else None


_parent_lookup: dict[str, Span] = {}


@asynccontextmanager
async def span(
    kind: str,
    name: str,
    *,
    agent: str | None = None,
    trace_id: str | None = None,
    task_id: int | None = None,
    input_text: str | None = None,
    metadata: dict | None = None,
) -> AsyncIterator[Span]:
    """Open a span as the current context. kind: orchestrator | agent | tool | model | link | governor."""
    parent = _current.get()
    sp = Span(kind=kind, name=name, agent=agent, parent=parent, trace_id=trace_id, task_id=task_id,
              input_text=input_text, metadata=metadata)
    _parent_lookup[sp.span_id] = sp
    token = _current.set(sp)
    await _emit(sp, "open")
    asyncio.create_task(_persist(sp))
    try:
        yield sp
        if sp.status == "running":
            sp.status = "done"
    except asyncio.CancelledError:
        sp.status = "cancelled"
        raise
    except Exception as exc:
        sp.status = "failed"
        sp.error = str(exc)[:1000]
        raise
    finally:
        sp.ended_at = _now()
        sp.duration_ms = int((time.monotonic() - sp.started_mono) * 1000)
        _current.reset(token)
        _parent_lookup.pop(sp.span_id, None)
        await _emit(sp, "close")
        await _persist(sp)


def set_output(text: str | None, *, provider: str | None = None, model: str | None = None) -> None:
    sp = _current.get()
    if sp is None:
        return
    sp.output = (text or "")[:4000] or None
    if provider:
        sp.provider = provider
    if model:
        sp.model = model


async def list_spans(*, trace_id: str | None = None, agent: str | None = None, limit: int = 200,
                     since: str | None = None) -> list[dict]:
    await ensure_tables()
    where, params = [], []
    if trace_id:
        where.append("trace_id = ?")
        params.append(trace_id)
    if agent:
        where.append("agent = ?")
        params.append(agent)
    if since:
        where.append("started_at >= ?")
        params.append(since)
    sql = "SELECT * FROM spans"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(min(int(limit), 2000))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(sql, params)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        d["tokens"] = int(d.get("prompt_tokens") or 0) + int(d.get("completion_tokens") or 0)
        out.append(d)
    return out


async def trace_tree(trace_id: str) -> dict:
    rows = await list_spans(trace_id=trace_id, limit=2000)
    rows.sort(key=lambda r: r["started_at"])
    by_id = {r["span_id"]: {**r, "children": []} for r in rows}
    roots = []
    for node in by_id.values():
        parent = by_id.get(node["parent_span_id"]) if node["parent_span_id"] else None
        (parent["children"] if parent else roots).append(node)
    return {"trace_id": trace_id, "roots": roots, "count": len(rows)}


async def active() -> list[dict]:
    """Spans still running in this process (for concurrent-call views)."""
    return [s.to_dict() for s in _parent_lookup.values()]


async def token_rollup(*, days: int = 7) -> dict:
    """Tokens by agent, device and provider for the token economy views."""
    await ensure_tables()
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        by_agent = await (await db.execute(
            """SELECT COALESCE(agent, '(none)') AS agent, COUNT(*) AS spans,
                      SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct, SUM(duration_ms) AS ms
               FROM spans WHERE started_at >= ? AND parent_span_id IS NULL
               GROUP BY agent ORDER BY pt + ct DESC""", (since,))).fetchall()
        by_device = await (await db.execute(
            """SELECT device, COUNT(*) AS spans, SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct
               FROM spans WHERE started_at >= ? AND parent_span_id IS NULL GROUP BY device""", (since,))).fetchall()
        by_provider = await (await db.execute(
            """SELECT COALESCE(provider, 'rules/none') AS provider, COUNT(*) AS spans,
                      SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct
               FROM spans WHERE started_at >= ? AND kind = 'model' GROUP BY provider ORDER BY pt + ct DESC""",
            (since,))).fetchall()
        zero = await (await db.execute(
            """SELECT COUNT(*) AS c FROM spans WHERE started_at >= ? AND parent_span_id IS NULL
               AND prompt_tokens + completion_tokens = 0 AND status = 'done'""", (since,))).fetchone()
        total = await (await db.execute(
            "SELECT COUNT(*) AS c FROM spans WHERE started_at >= ? AND parent_span_id IS NULL", (since,))).fetchone()

    def rows(rs):
        return [{**dict(r), "tokens": int((r["pt"] or 0) + (r["ct"] or 0))} for r in rs]

    total_c = int(total["c"]) if total else 0
    zero_c = int(zero["c"]) if zero else 0
    return {
        "days": days,
        "by_agent": rows(by_agent),
        "by_device": rows(by_device),
        "by_provider": rows(by_provider),
        "zero_token_tasks": zero_c,
        "total_tasks": total_c,
        "zero_token_ratio": round(zero_c / total_c, 3) if total_c else None,
    }
