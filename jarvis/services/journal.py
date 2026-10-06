"""The journal: a constant log of inputs, decisions, problems, weaknesses, strengths and fixes.

Every subsystem writes here with one call. Post-thought reflections land here. Self-heal
reads the open `problem` entries. Entries are streamed live to Studio.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal

import aiosqlite

from jarvis.database import DB_PATH
from jarvis.services import activity_stream, spans

log = logging.getLogger("jarvis.journal")

Kind = Literal["input", "decision", "problem", "weakness", "strength", "fix", "note"]
KINDS: tuple[str, ...] = ("input", "decision", "problem", "weakness", "strength", "fix", "note")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    device TEXT NOT NULL,
    agent TEXT,
    source TEXT,
    summary TEXT NOT NULL,
    detail TEXT,
    trace_id TEXT,
    span_id TEXT,
    task_id INTEGER,
    severity INTEGER NOT NULL DEFAULT 1,
    resolved INTEGER NOT NULL DEFAULT 0,
    resolved_by TEXT,
    resolved_at TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_kind_time ON journal(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_agent_time ON journal(agent, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_open_problems ON journal(kind, resolved, created_at DESC);
"""

_sinks: list = []


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def register_sink(sink) -> None:
    if sink not in _sinks:
        _sinks.append(sink)


def unregister_sink(sink) -> None:
    try:
        _sinks.remove(sink)
    except ValueError:
        pass


async def ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def write(
    kind: str,
    summary: str,
    *,
    detail: str | None = None,
    agent: str | None = None,
    source: str | None = None,
    severity: int = 1,
    task_id: int | None = None,
    trace_id: str | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """Append one entry. Never raises — journaling must not break work."""
    if kind not in KINDS:
        kind = "note"
    sp = spans.current()
    row = {
        "kind": kind,
        "device": spans.device_name(),
        "agent": agent or (sp.agent if sp else None),
        "source": source,
        "summary": (summary or "").strip()[:500],
        "detail": (detail or "").strip()[:4000] or None,
        "trace_id": trace_id or (sp.trace_id if sp else None),
        "span_id": sp.span_id if sp else None,
        "task_id": task_id if task_id is not None else (sp.task_id if sp else None),
        "severity": max(1, min(int(severity or 1), 5)),
        "metadata": metadata or {},
        "created_at": _now(),
    }
    if not row["summary"]:
        return row
    try:
        await ensure_tables()
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                """INSERT INTO journal (kind, device, agent, source, summary, detail, trace_id, span_id,
                       task_id, severity, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["kind"], row["device"], row["agent"], row["source"], row["summary"], row["detail"],
                    row["trace_id"], row["span_id"], row["task_id"], row["severity"],
                    json.dumps(row["metadata"], default=str), row["created_at"],
                ),
            )
            row["id"] = cur.lastrowid
            await db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("journal write failed: %s", exc)
        return row
    try:
        await activity_stream.broadcast(
            {"kind": "journal", "title": f"{kind}: {row['summary'][:80]}", "detail": row["detail"] or "",
             "status": "done", "engine": row["agent"], "metadata": {"journal": row}}
        )
    except Exception:
        pass
    for sink in list(_sinks):
        try:
            await sink(row)
        except Exception:
            pass
    return row


# Convenience wrappers so call sites read well.
async def input(summary: str, **kw) -> dict:  # noqa: A001 - intentional name
    return await write("input", summary, **kw)


async def decision(summary: str, **kw) -> dict:
    return await write("decision", summary, **kw)


async def problem(summary: str, **kw) -> dict:
    kw.setdefault("severity", 3)
    return await write("problem", summary, **kw)


async def weakness(summary: str, **kw) -> dict:
    return await write("weakness", summary, **kw)


async def strength(summary: str, **kw) -> dict:
    return await write("strength", summary, **kw)


async def fix(summary: str, **kw) -> dict:
    return await write("fix", summary, **kw)


async def resolve(entry_id: int, *, by: str = "self-heal") -> bool:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE journal SET resolved = 1, resolved_by = ?, resolved_at = ? WHERE id = ? AND resolved = 0",
            (by, _now(), entry_id),
        )
        await db.commit()
        return bool(cur.rowcount)


async def list_entries(
    *,
    kind: str | None = None,
    agent: str | None = None,
    device: str | None = None,
    unresolved_only: bool = False,
    since: str | None = None,
    limit: int = 100,
) -> list[dict]:
    await ensure_tables()
    where, params = [], []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if agent:
        where.append("agent = ?")
        params.append(agent)
    if device:
        where.append("device = ?")
        params.append(device)
    if unresolved_only:
        where.append("resolved = 0")
    if since:
        where.append("created_at >= ?")
        params.append(since)
    sql = "SELECT * FROM journal"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(min(int(limit), 1000))
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
        out.append(d)
    return out


async def open_problems(*, limit: int = 50) -> list[dict]:
    return await list_entries(kind="problem", unresolved_only=True, limit=limit)


async def stats(*, days: int = 7) -> dict:
    await ensure_tables()
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT kind, COUNT(*) AS c FROM journal WHERE created_at >= ? GROUP BY kind", (since,)
        )).fetchall()
        open_p = await (await db.execute(
            "SELECT COUNT(*) AS c FROM journal WHERE kind = 'problem' AND resolved = 0"
        )).fetchone()
        agents = await (await db.execute(
            """SELECT agent, SUM(kind = 'strength') AS strengths, SUM(kind = 'weakness') AS weaknesses,
                      SUM(kind = 'problem') AS problems
               FROM journal WHERE created_at >= ? AND agent IS NOT NULL GROUP BY agent""", (since,)
        )).fetchall()
    return {
        "days": days,
        "by_kind": {r["kind"]: int(r["c"]) for r in rows},
        "open_problems": int(open_p["c"]) if open_p else 0,
        "by_agent": [dict(r) for r in agents],
    }


async def block(*, limit: int = 6) -> str:
    """Compact text block of recent weaknesses/problems for prompt injection."""
    rows = await list_entries(limit=limit * 3)
    picked = [r for r in rows if r["kind"] in ("problem", "weakness", "fix")][:limit]
    if not picked:
        return ""
    lines = [f"- [{r['kind']}] {r['summary']}" + (f" ({r['agent']})" if r.get("agent") else "") for r in picked]
    return "JOURNAL (recent):\n" + "\n".join(lines)
