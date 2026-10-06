"""SQLite ledger of provider calls, cooldowns and free-tier headroom (Quota Keeper's hands)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from jarvis.database import DB_PATH
from jarvis.services.providers import catalog

log = logging.getLogger("jarvis.providers.usage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT,
    capability TEXT NOT NULL DEFAULT 'chat',
    ok INTEGER NOT NULL DEFAULT 1,
    status_code INTEGER,
    latency_ms INTEGER,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_provider_usage_provider_time
    ON provider_usage(provider, created_at);

CREATE TABLE IF NOT EXISTS provider_state (
    provider TEXT PRIMARY KEY,
    cooldown_until TEXT,
    consecutive_errors INTEGER NOT NULL DEFAULT 0,
    last_ok_at TEXT,
    last_error TEXT,
    last_error_at TEXT,
    key_present INTEGER NOT NULL DEFAULT 0,
    key_source TEXT,
    last_probe_ok INTEGER,
    last_probe_at TEXT,
    last_probe_detail TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_daily (
    day TEXT NOT NULL,
    provider TEXT NOT NULL,
    capability TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, provider, capability)
);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        for col, typedef in (("agent", "TEXT"), ("device", "TEXT"), ("trace_id", "TEXT"), ("task_id", "INTEGER")):
            try:
                await db.execute(f"ALTER TABLE provider_usage ADD COLUMN {col} {typedef}")
            except Exception:
                pass
        await db.commit()


async def by_agent_device(*, days: int = 7) -> dict[str, Any]:
    """Token ledger grouped by agent, device and provider (the token economy view)."""
    await ensure_tables()
    since = _fmt(_utc_now() - timedelta(days=days))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        agents = await (await db.execute(
            """SELECT COALESCE(agent, '(router)') AS agent, COUNT(*) AS calls,
                      SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct
               FROM provider_usage WHERE created_at >= ? AND ok = 1 GROUP BY agent ORDER BY pt + ct DESC""",
            (since,))).fetchall()
        devices = await (await db.execute(
            """SELECT COALESCE(device, 'mini') AS device, COUNT(*) AS calls,
                      SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct
               FROM provider_usage WHERE created_at >= ? AND ok = 1 GROUP BY device""", (since,))).fetchall()
        providers = await (await db.execute(
            """SELECT provider, model, COUNT(*) AS calls, SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct
               FROM provider_usage WHERE created_at >= ? AND ok = 1 GROUP BY provider, model ORDER BY pt + ct DESC""",
            (since,))).fetchall()
        hourly = await (await db.execute(
            """SELECT substr(created_at, 1, 13) AS hour, SUM(prompt_tokens + completion_tokens) AS tokens, COUNT(*) AS calls
               FROM provider_usage WHERE created_at >= ? AND ok = 1 GROUP BY hour ORDER BY hour""",
            (_fmt(_utc_now() - timedelta(hours=48)),))).fetchall()

    def rows(rs):
        return [{**dict(r), "tokens": int((r["pt"] or 0) + (r["ct"] or 0))} for r in rs]

    return {"days": days, "by_agent": rows(agents), "by_device": rows(devices), "by_provider": rows(providers),
            "hourly": [dict(r) for r in hourly]}


async def record(
    provider: str,
    *,
    capability: str,
    ok: bool,
    model: str | None = None,
    status_code: int | None = None,
    latency_ms: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    error: str | None = None,
    source: str | None = None,
) -> None:
    """Append one call to the ledger. Never raises — bookkeeping must not break a model call."""
    try:
        if ok:
            from jarvis.services import spans

            spans.add_tokens(prompt_tokens, completion_tokens, provider=provider, model=model)
    except Exception:
        pass
    try:
        await _record(
            provider, capability=capability, ok=ok, model=model, status_code=status_code, latency_ms=latency_ms,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, error=error, source=source,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("provider usage record failed for %s: %s", provider, exc)


async def _record(
    provider: str,
    *,
    capability: str,
    ok: bool,
    model: str | None,
    status_code: int | None,
    latency_ms: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    error: str | None,
    source: str | None,
) -> None:
    await ensure_tables()
    now = _fmt(_utc_now())
    day = now[:10]
    agent = device = trace_id = None
    task_id = None
    try:
        from jarvis.services import spans

        sp = spans.current()
        device = spans.device_name()
        if sp:
            agent, trace_id, task_id = sp.agent, sp.trace_id, sp.task_id
    except Exception:
        pass
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO provider_usage
                (provider, model, capability, ok, status_code, latency_ms,
                 prompt_tokens, completion_tokens, error, source, created_at,
                 agent, device, trace_id, task_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                model,
                capability,
                1 if ok else 0,
                status_code,
                latency_ms,
                int(prompt_tokens or 0),
                int(completion_tokens or 0),
                (error or "")[:400] or None,
                source,
                now,
                agent,
                device,
                trace_id,
                task_id,
            ),
        )
        await db.execute(
            """
            INSERT INTO provider_daily (day, provider, capability, calls, errors, prompt_tokens, completion_tokens)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(day, provider, capability) DO UPDATE SET
                calls = calls + 1,
                errors = errors + excluded.errors,
                prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                completion_tokens = completion_tokens + excluded.completion_tokens
            """,
            (day, provider, capability, 0 if ok else 1, int(prompt_tokens or 0), int(completion_tokens or 0)),
        )
        if ok:
            await db.execute(
                """
                INSERT INTO provider_state (provider, consecutive_errors, last_ok_at, updated_at)
                VALUES (?, 0, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    consecutive_errors = 0, last_ok_at = excluded.last_ok_at,
                    cooldown_until = NULL, updated_at = excluded.updated_at
                """,
                (provider, now, now),
            )
        else:
            await db.execute(
                """
                INSERT INTO provider_state (provider, consecutive_errors, last_error, last_error_at, updated_at)
                VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    consecutive_errors = consecutive_errors + 1,
                    last_error = excluded.last_error,
                    last_error_at = excluded.last_error_at,
                    updated_at = excluded.updated_at
                """,
                (provider, (error or "")[:400], now, now),
            )
        await db.commit()


async def set_cooldown(provider: str, *, seconds: int, reason: str = "") -> None:
    await ensure_tables()
    until = _fmt(_utc_now() + timedelta(seconds=max(5, seconds)))
    now = _fmt(_utc_now())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO provider_state (provider, cooldown_until, last_error, last_error_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                last_error = COALESCE(excluded.last_error, last_error),
                last_error_at = excluded.last_error_at,
                updated_at = excluded.updated_at
            """,
            (provider, until, reason[:400] or None, now, now),
        )
        await db.commit()


async def clear_cooldown(provider: str) -> None:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE provider_state SET cooldown_until = NULL, updated_at = datetime('now') WHERE provider = ?",
            (provider,),
        )
        await db.commit()


async def cooling_until(provider: str) -> datetime | None:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT cooldown_until FROM provider_state WHERE provider = ?", (provider,))
        ).fetchone()
    until = _parse(row["cooldown_until"]) if row else None
    if until and until > _utc_now():
        return until
    return None


async def is_cooling(provider: str) -> bool:
    return (await cooling_until(provider)) is not None


async def record_key_state(provider: str, *, present: bool, source: str | None) -> None:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO provider_state (provider, key_present, key_source, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(provider) DO UPDATE SET
                key_present = excluded.key_present, key_source = excluded.key_source,
                updated_at = excluded.updated_at
            """,
            (provider, 1 if present else 0, source),
        )
        await db.commit()


async def record_probe(provider: str, *, ok: bool, detail: str = "") -> None:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO provider_state (provider, last_probe_ok, last_probe_at, last_probe_detail, updated_at)
            VALUES (?, ?, datetime('now'), ?, datetime('now'))
            ON CONFLICT(provider) DO UPDATE SET
                last_probe_ok = excluded.last_probe_ok, last_probe_at = excluded.last_probe_at,
                last_probe_detail = excluded.last_probe_detail, updated_at = excluded.updated_at
            """,
            (provider, 1 if ok else 0, detail[:400] or None),
        )
        await db.commit()


async def counts_today(provider: str) -> dict[str, int]:
    await ensure_tables()
    day = _fmt(_utc_now())[:10]
    minute_cutoff = _fmt(_utc_now() - timedelta(seconds=60))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT COALESCE(SUM(calls), 0) AS calls, COALESCE(SUM(errors), 0) AS errors,
                       COALESCE(SUM(prompt_tokens), 0) AS pt, COALESCE(SUM(completion_tokens), 0) AS ct
                FROM provider_daily WHERE day = ? AND provider = ?
                """,
                (day, provider),
            )
        ).fetchone()
        minute = await (
            await db.execute(
                """
                SELECT COUNT(*) AS c, COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS t
                FROM provider_usage WHERE provider = ? AND created_at >= ?
                """,
                (provider, minute_cutoff),
            )
        ).fetchone()
    return {
        "calls_today": int(row["calls"]) if row else 0,
        "errors_today": int(row["errors"]) if row else 0,
        "tokens_today": int((row["pt"] or 0) + (row["ct"] or 0)) if row else 0,
        "calls_last_minute": int(minute["c"]) if minute else 0,
        "tokens_last_minute": int(minute["t"]) if minute else 0,
    }


async def headroom(provider: str) -> dict[str, Any]:
    """Compare today's usage with the catalog's approximate free-tier ceilings."""
    spec = catalog.get(provider)
    counts = await counts_today(provider)
    tier = spec.free_tier if spec else catalog.FreeTier()
    limits = {
        "requests_per_day": tier.requests_per_day,
        "requests_per_minute": tier.requests_per_minute,
        "tokens_per_minute": tier.tokens_per_minute,
        "tokens_per_day": tier.tokens_per_day,
    }
    exhausted: list[str] = []
    if tier.requests_per_day and counts["calls_today"] >= tier.requests_per_day:
        exhausted.append("requests_per_day")
    if tier.requests_per_minute and counts["calls_last_minute"] >= tier.requests_per_minute:
        exhausted.append("requests_per_minute")
    if tier.tokens_per_minute and counts["tokens_last_minute"] >= tier.tokens_per_minute:
        exhausted.append("tokens_per_minute")
    if tier.tokens_per_day and counts["tokens_today"] >= tier.tokens_per_day:
        exhausted.append("tokens_per_day")
    pct = None
    if tier.requests_per_day:
        pct = round(100.0 * counts["calls_today"] / tier.requests_per_day, 1)
    return {**counts, "limits": limits, "exhausted": exhausted, "daily_percent": pct, "available": not exhausted}


async def has_headroom(provider: str) -> bool:
    return (await headroom(provider))["available"]


async def state(provider: str) -> dict[str, Any]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM provider_state WHERE provider = ?", (provider,))
        ).fetchone()
    data = dict(row) if row else {"provider": provider}
    until = _parse(data.get("cooldown_until"))
    data["cooling"] = bool(until and until > _utc_now())
    return data


async def summary(*, days: int = 7) -> dict[str, Any]:
    await ensure_tables()
    since = _fmt(_utc_now() - timedelta(days=days))[:10]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT provider, capability, SUM(calls) AS calls, SUM(errors) AS errors,
                       SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct
                FROM provider_daily WHERE day >= ?
                GROUP BY provider, capability ORDER BY provider, capability
                """,
                (since,),
            )
        ).fetchall()
        states = await (await db.execute("SELECT * FROM provider_state")).fetchall()
    per_provider: dict[str, Any] = {}
    for r in rows:
        p = per_provider.setdefault(r["provider"], {"calls": 0, "errors": 0, "tokens": 0, "by_capability": {}})
        p["calls"] += int(r["calls"] or 0)
        p["errors"] += int(r["errors"] or 0)
        p["tokens"] += int((r["pt"] or 0) + (r["ct"] or 0))
        p["by_capability"][r["capability"]] = {
            "calls": int(r["calls"] or 0),
            "errors": int(r["errors"] or 0),
            "tokens": int((r["pt"] or 0) + (r["ct"] or 0)),
        }
    for pid in catalog.PROVIDERS:
        per_provider.setdefault(pid, {"calls": 0, "errors": 0, "tokens": 0, "by_capability": {}})
        per_provider[pid]["today"] = await headroom(pid)
    state_map = {}
    for s in states:
        d = dict(s)
        until = _parse(d.get("cooldown_until"))
        d["cooling"] = bool(until and until > _utc_now())
        state_map[d["provider"]] = d
    return {"days": days, "providers": per_provider, "state": state_map}


async def recent(limit: int = 50) -> list[dict[str, Any]]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM provider_usage ORDER BY id DESC LIMIT ?", (min(limit, 500),))
        ).fetchall()
    return [dict(r) for r in rows]


async def prune(*, keep_days: int = 30) -> int:
    await ensure_tables()
    cutoff = _fmt(_utc_now() - timedelta(days=keep_days))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM provider_usage WHERE created_at < ?", (cutoff,))
        await db.commit()
        return int(cur.rowcount or 0)
