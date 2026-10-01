"""SQLite registry for LAN fleet nodes and redistributable jobs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from jarvis.database import DB_PATH
from jarvis.services.fleet_types import (
    FleetHeartbeatRequest,
    FleetJobRecord,
    FleetJobTag,
    FleetNodeRecord,
    FleetNodeSpec,
    FleetRegisterRequest,
    node_name_key,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fleet_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'general',
    os TEXT NOT NULL DEFAULT 'unknown',
    capabilities TEXT NOT NULL DEFAULT '[]',
    lan_host TEXT,
    mac_address TEXT,
    status TEXT NOT NULL DEFAULT 'offline',
    last_heartbeat_at TEXT,
    last_wol_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fleet_nodes_status
    ON fleet_nodes(status, last_heartbeat_at);

CREATE TABLE IF NOT EXISTS fleet_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT 'general',
    body TEXT NOT NULL DEFAULT '',
    command TEXT,
    preferred_role TEXT,
    required_capabilities TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    assigned_node_id INTEGER,
    lease_expires_at TEXT,
    result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fleet_jobs_status_tag
    ON fleet_jobs(status, tag, created_at);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


async def ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


def _node_row(row: aiosqlite.Row) -> FleetNodeRecord:
    return FleetNodeRecord.model_validate(
        {
            "id": row["id"],
            "name": row["name"],
            "name_key": row["name_key"],
            "role": row["role"],
            "os": row["os"],
            "capabilities": json.loads(row["capabilities"] or "[]"),
            "lan_host": row["lan_host"],
            "mac_address": row["mac_address"],
            "status": row["status"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "last_wol_at": row["last_wol_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _job_row(row: aiosqlite.Row, node_name: str | None = None) -> FleetJobRecord:
    return FleetJobRecord.model_validate(
        {
            "id": row["id"],
            "title": row["title"],
            "tag": row["tag"],
            "body": row["body"] or "",
            "command": row["command"],
            "preferred_role": row["preferred_role"],
            "required_capabilities": json.loads(row["required_capabilities"] or "[]"),
            "status": row["status"],
            "assigned_node_id": row["assigned_node_id"],
            "assigned_node_name": node_name,
            "lease_expires_at": row["lease_expires_at"],
            "result": json.loads(row["result"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


async def upsert_configured_node(spec: FleetNodeSpec, *, status: str = "configured") -> FleetNodeRecord:
    await ensure_tables()
    name_key = node_name_key(spec.name)
    caps = json.dumps(spec.capabilities)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        existing = await (
            await db.execute("SELECT id, status FROM fleet_nodes WHERE name_key = ?", (name_key,))
        ).fetchone()
        if existing:
            # Preserve live status if the node is already online/sleeping.
            keep_status = existing["status"] if existing["status"] in ("online", "sleeping") else status
            await db.execute(
                """
                UPDATE fleet_nodes
                SET name = ?, role = ?, os = ?, capabilities = ?,
                    lan_host = COALESCE(?, lan_host),
                    mac_address = COALESCE(?, mac_address),
                    status = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    spec.name,
                    spec.role,
                    spec.os,
                    caps,
                    spec.lan_host,
                    spec.mac_address,
                    keep_status,
                    existing["id"],
                ),
            )
            node_id = int(existing["id"])
        else:
            cur = await db.execute(
                """
                INSERT INTO fleet_nodes
                    (name, name_key, role, os, capabilities, lan_host, mac_address, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.name,
                    name_key,
                    spec.role,
                    spec.os,
                    caps,
                    spec.lan_host,
                    spec.mac_address,
                    status,
                ),
            )
            node_id = int(cur.lastrowid)
        await db.commit()
        row = await (
            await db.execute("SELECT * FROM fleet_nodes WHERE id = ?", (node_id,))
        ).fetchone()
    return _node_row(row)


async def register_node(req: FleetRegisterRequest) -> FleetNodeRecord:
    await ensure_tables()
    name_key = node_name_key(req.name)
    caps = json.dumps(req.capabilities)
    now = _fmt(_utc_now())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        existing = await (
            await db.execute("SELECT id FROM fleet_nodes WHERE name_key = ?", (name_key,))
        ).fetchone()
        if existing:
            await db.execute(
                """
                UPDATE fleet_nodes
                SET name = ?, role = ?, os = ?, capabilities = ?,
                    lan_host = ?, mac_address = COALESCE(?, mac_address),
                    status = 'online', last_heartbeat_at = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    req.name,
                    req.role,
                    req.os,
                    caps,
                    req.lan_host,
                    req.mac_address,
                    now,
                    existing["id"],
                ),
            )
            node_id = int(existing["id"])
        else:
            cur = await db.execute(
                """
                INSERT INTO fleet_nodes
                    (name, name_key, role, os, capabilities, lan_host, mac_address,
                     status, last_heartbeat_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'online', ?)
                """,
                (req.name, name_key, req.role, req.os, caps, req.lan_host, req.mac_address, now),
            )
            node_id = int(cur.lastrowid)
        await db.commit()
        row = await (
            await db.execute("SELECT * FROM fleet_nodes WHERE id = ?", (node_id,))
        ).fetchone()
    return _node_row(row)


async def heartbeat(req: FleetHeartbeatRequest) -> FleetNodeRecord | None:
    await ensure_tables()
    name_key = node_name_key(req.name)
    now = _fmt(_utc_now())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM fleet_nodes WHERE name_key = ?", (name_key,))
        ).fetchone()
        if not row:
            return None
        caps = row["capabilities"]
        if req.capabilities is not None:
            caps = json.dumps(req.capabilities)
        await db.execute(
            """
            UPDATE fleet_nodes
            SET status = ?, last_heartbeat_at = ?,
                lan_host = COALESCE(?, lan_host),
                capabilities = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (req.status, now, req.lan_host, caps, row["id"]),
        )
        await db.commit()
        updated = await (
            await db.execute("SELECT * FROM fleet_nodes WHERE id = ?", (row["id"],))
        ).fetchone()
    return _node_row(updated)


async def mark_stale_offline(*, timeout_seconds: int) -> int:
    """Mark online nodes without a fresh heartbeat as offline. Returns count updated.

    Nodes in `sleeping` stay sleeping (WoL pending / cooling) until a longer silence
    or an explicit status change — they are not cleared by the normal heartbeat window.
    """
    await ensure_tables()
    cutoff = _fmt(_utc_now() - timedelta(seconds=max(15, timeout_seconds)))
    sleep_cutoff = _fmt(_utc_now() - timedelta(seconds=max(300, timeout_seconds * 10)))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE fleet_nodes
            SET status = 'offline', updated_at = datetime('now')
            WHERE status = 'online'
              AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)
            """,
            (cutoff,),
        )
        marked = int(cur.rowcount or 0)
        cur2 = await db.execute(
            """
            UPDATE fleet_nodes
            SET status = 'offline', updated_at = datetime('now')
            WHERE status = 'sleeping'
              AND (
                (last_wol_at IS NOT NULL AND last_wol_at < ?)
                OR (
                  last_wol_at IS NULL
                  AND last_heartbeat_at IS NOT NULL
                  AND last_heartbeat_at < ?
                )
              )
            """,
            (sleep_cutoff, sleep_cutoff),
        )
        marked += int(cur2.rowcount or 0)
        await db.commit()
        return marked


async def list_nodes() -> list[FleetNodeRecord]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM fleet_nodes ORDER BY role, name")
        ).fetchall()
    return [_node_row(r) for r in rows]


async def get_node_by_name(name: str) -> FleetNodeRecord | None:
    await ensure_tables()
    name_key = node_name_key(name)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM fleet_nodes WHERE name_key = ?", (name_key,))
        ).fetchone()
    return _node_row(row) if row else None


async def get_node_by_id(node_id: int) -> FleetNodeRecord | None:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM fleet_nodes WHERE id = ?", (node_id,))
        ).fetchone()
    return _node_row(row) if row else None


async def set_wol_sent(node_id: int) -> FleetNodeRecord | None:
    await ensure_tables()
    now = _fmt(_utc_now())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            UPDATE fleet_nodes
            SET last_wol_at = ?, status = CASE
                WHEN status = 'online' THEN status
                ELSE 'sleeping'
            END,
            updated_at = datetime('now')
            WHERE id = ?
            """,
            (now, node_id),
        )
        await db.commit()
        row = await (
            await db.execute("SELECT * FROM fleet_nodes WHERE id = ?", (node_id,))
        ).fetchone()
    return _node_row(row) if row else None


async def enqueue_job(
    *,
    title: str,
    tag: FleetJobTag = "general",
    body: str = "",
    command: str | None = None,
    preferred_role: str | None = None,
    required_capabilities: list[str] | None = None,
) -> FleetJobRecord:
    await ensure_tables()
    caps = json.dumps(required_capabilities or [])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            INSERT INTO fleet_jobs
                (title, tag, body, command, preferred_role, required_capabilities, status)
            VALUES (?, ?, ?, ?, ?, ?, 'queued')
            """,
            (title, tag, body, command, preferred_role, caps),
        )
        job_id = int(cur.lastrowid)
        await db.commit()
        row = await (
            await db.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,))
        ).fetchone()
    return _job_row(row)


async def reclaim_expired_leases() -> int:
    await ensure_tables()
    now = _fmt(_utc_now())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE fleet_jobs
            SET status = 'queued', assigned_node_id = NULL, lease_expires_at = NULL,
                updated_at = datetime('now')
            WHERE status = 'claimed'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ?
            """,
            (now,),
        )
        await db.commit()
        return int(cur.rowcount or 0)


async def claim_job(
    *,
    node: FleetNodeRecord,
    tags: list[FleetJobTag],
    lease_seconds: int = 120,
) -> FleetJobRecord | None:
    await ensure_tables()
    await reclaim_expired_leases()
    if node.status != "online":
        return None
    tag_set = {t for t in tags} or {"general", "shell"}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT * FROM fleet_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                """
            )
        ).fetchall()
        chosen = None
        for row in rows:
            if row["tag"] not in tag_set:
                continue
            required = json.loads(row["required_capabilities"] or "[]")
            if required and not set(required).issubset(set(node.capabilities)):
                continue
            preferred = row["preferred_role"]
            if preferred and preferred != node.role and preferred != "general":
                # Soft preference: skip if another online node with that role exists.
                peer = await (
                    await db.execute(
                        """
                        SELECT id FROM fleet_nodes
                        WHERE role = ? AND status = 'online' AND id != ?
                        LIMIT 1
                        """,
                        (preferred, node.id),
                    )
                ).fetchone()
                if peer:
                    continue
            chosen = row
            break
        if not chosen:
            return None
        lease_until = _fmt(_utc_now() + timedelta(seconds=lease_seconds))
        await db.execute(
            """
            UPDATE fleet_jobs
            SET status = 'claimed', assigned_node_id = ?, lease_expires_at = ?,
                updated_at = datetime('now')
            WHERE id = ? AND status = 'queued'
            """,
            (node.id, lease_until, chosen["id"]),
        )
        await db.commit()
        updated = await (
            await db.execute("SELECT * FROM fleet_jobs WHERE id = ?", (chosen["id"],))
        ).fetchone()
        if not updated or updated["status"] != "claimed":
            return None
    return _job_row(updated, node_name=node.name)


async def complete_job(
    *,
    job_id: int,
    node: FleetNodeRecord,
    ok: bool,
    output: str = "",
    error: str = "",
) -> FleetJobRecord | None:
    await ensure_tables()
    result = {"ok": ok, "output": output, "error": error, "node": node.name}
    status = "done" if ok else "failed"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,))
        ).fetchone()
        if not row:
            return None
        if row["assigned_node_id"] not in (None, node.id):
            return None
        await db.execute(
            """
            UPDATE fleet_jobs
            SET status = ?, result = ?, lease_expires_at = NULL, updated_at = datetime('now')
            WHERE id = ?
            """,
            (status, json.dumps(result), job_id),
        )
        await db.commit()
        updated = await (
            await db.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,))
        ).fetchone()
    return _job_row(updated, node_name=node.name)


async def list_jobs(*, status: str | None = None, limit: int = 50) -> list[FleetJobRecord]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status:
            rows = await (
                await db.execute(
                    """
                    SELECT j.*, n.name AS node_name
                    FROM fleet_jobs j
                    LEFT JOIN fleet_nodes n ON n.id = j.assigned_node_id
                    WHERE j.status = ?
                    ORDER BY j.created_at DESC
                    LIMIT ?
                    """,
                    (status, limit),
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    """
                    SELECT j.*, n.name AS node_name
                    FROM fleet_jobs j
                    LEFT JOIN fleet_nodes n ON n.id = j.assigned_node_id
                    ORDER BY j.created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ).fetchall()
    return [_job_row(r, node_name=r["node_name"]) for r in rows]


async def job_counts() -> dict[str, int]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT status, COUNT(*) AS c FROM fleet_jobs GROUP BY status"
            )
        ).fetchall()
    return {str(r["status"]): int(r["c"]) for r in rows}


async def fleet_snapshot(*, heartbeat_timeout_seconds: int) -> dict[str, Any]:
    stale = await mark_stale_offline(timeout_seconds=heartbeat_timeout_seconds)
    await reclaim_expired_leases()
    # Control plane is this process — keep Mini online even without a peer worker.
    from jarvis.config import settings
    from jarvis.services.fleet_types import FleetRegisterRequest

    await register_node(
        FleetRegisterRequest(
            name=settings.fleet_mini_name or "Mac Mini",
            role="control",
            os="macos",
            capabilities=["control", "shell", "ollama", "cursor", "planner"],
            lan_host=settings.fleet_mini_lan_host or "127.0.0.1",
        )
    )
    nodes = await list_nodes()
    return {
        "stale_marked_offline": stale,
        "nodes": [n.model_dump() for n in nodes],
        "jobs": await job_counts(),
    }
