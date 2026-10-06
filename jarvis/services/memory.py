"""Long-term memory: compress sessions, retrieve relevant facts, inject into prompts."""

from __future__ import annotations

import re
import uuid

import aiosqlite

from jarvis.config import settings
from jarvis.database import DB_PATH
from jarvis.services import ollama

REMEMBER_RE = re.compile(
    r"\b(remember|don't forget|do not forget|keep in mind|note that)\b(.+)",
    re.I | re.S,
)
RECALL_RE = re.compile(
    r"\b(what did (?:i|we) (?:say|talk|discuss)|do you remember|recall|"
    r"what was (?:that|it) about|earlier you said)\b",
    re.I,
)
_STOP = frozenset(
    "what when where that this with have will boss about your mine just like tell "
    "remember forget note keep mind does know said talk discuss earlier".split()
)


async def ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'fact',
                importance INTEGER NOT NULL DEFAULT 1,
                source TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                topic, content, content='memory_entries', content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS memory_entries_ai AFTER INSERT ON memory_entries BEGIN
                INSERT INTO memory_fts(rowid, topic, content)
                VALUES (new.rowid, new.topic, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_entries_ad AFTER DELETE ON memory_entries BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, topic, content)
                VALUES ('delete', old.rowid, old.topic, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_entries_au AFTER UPDATE ON memory_entries BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, topic, content)
                VALUES ('delete', old.rowid, old.topic, old.content);
                INSERT INTO memory_fts(rowid, topic, content)
                VALUES (new.rowid, new.topic, new.content);
            END;
            """
        )
        await db.commit()


def _topic_key(text: str) -> str:
    words = re.findall(r"[a-z]{3,}", text.lower())
    sig = [w for w in words if w not in _STOP][:8]
    return " ".join(sorted(set(sig))) or "general"


def _fts_query(text: str) -> str:
    tokens = re.findall(r"[a-z0-9]{3,}", text.lower())
    tokens = [t for t in tokens if t not in _STOP][:6]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


async def store(
    content: str,
    *,
    kind: str = "fact",
    topic: str | None = None,
    importance: int = 1,
    source: str | None = None,
) -> dict:
    await ensure_tables()
    entry_id = str(uuid.uuid4())
    topic = topic or _topic_key(content)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO memory_entries (id, topic, content, kind, importance, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entry_id, topic, content.strip()[:2000], kind, importance, source),
        )
        await db.commit()
    # Semantic index (free embeddings → local vectors + Pinecone/Supabase when configured).
    if settings.semantic_memory_enabled:
        try:
            from jarvis.services.providers import vectors

            await vectors.index_memory(entry_id, content.strip()[:2000], kind=kind, importance=importance, topic=topic)
        except Exception:
            pass
    return {"id": entry_id, "topic": topic, "content": content.strip()[:2000], "kind": kind}


async def retrieve(query: str, *, limit: int = 5) -> list[dict]:
    """Keyword (FTS) hits first, then semantic hits for anything FTS missed."""
    hits = await _retrieve_fts(query, limit=limit)
    if len(hits) >= limit or not settings.semantic_memory_enabled:
        return hits
    try:
        from jarvis.services.providers import vectors

        semantic = await vectors.semantic_search(query, limit=limit)
    except Exception:
        semantic = []
    seen = {h["id"] for h in hits}
    for s in semantic:
        if s["id"] in seen:
            continue
        hits.append({"id": s["id"], "topic": s.get("topic", ""), "content": s["content"], "kind": s["kind"],
                     "importance": s["importance"], "created_at": None, "score": round(s["score"], 3)})
        seen.add(s["id"])
        if len(hits) >= limit:
            break
    return hits


async def _retrieve_fts(query: str, *, limit: int = 5) -> list[dict]:
    await ensure_tables()
    fts = _fts_query(query)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if fts:
            rows = await (
                await db.execute(
                    """
                    SELECT m.id, m.topic, m.content, m.kind, m.importance, m.created_at
                    FROM memory_fts f
                    JOIN memory_entries m ON m.rowid = f.rowid
                    WHERE memory_fts MATCH ?
                    ORDER BY m.importance DESC, m.created_at DESC
                    LIMIT ?
                    """,
                    (fts, limit),
                )
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]

        like = f"%{query[:80]}%"
        rows = await (
            await db.execute(
                """
                SELECT id, topic, content, kind, importance, created_at
                FROM memory_entries
                WHERE content LIKE ? OR topic LIKE ?
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (like, like, limit),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def get_block(query: str, *, limit: int = 4) -> str:
    hits = await retrieve(query, limit=limit)
    if not hits:
        return ""
    lines = ["RELEVANT MEMORY (use if helpful, never invent beyond this):"]
    for hit in hits:
        lines.append(f"- [{hit['kind']}] {hit['content'][:240]}")
    return "\n".join(lines)


def wants_remember(text: str) -> str | None:
    m = REMEMBER_RE.search(text.strip())
    if not m:
        return None
    payload = m.group(2).strip(" .,!?:;")
    return payload[:500] if payload else None


def wants_recall(text: str) -> bool:
    return bool(RECALL_RE.search(text))


async def compress_stale_sessions(*, min_messages: int = 4, idle_minutes: int = 45) -> dict:
    """Summarize idle conversations into long-term memory."""
    from jarvis.services.sessions import ensure_tables as ensure_session_tables

    await ensure_tables()
    await ensure_session_tables()

    compressed = 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT c.id, c.topic,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS msg_count
                FROM conversations c
                WHERE datetime(c.last_active_at) <= datetime('now', ?)
                  AND NOT EXISTS (
                    SELECT 1 FROM memory_entries me
                    WHERE me.source = c.id AND me.kind = 'session'
                  )
                  AND (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) >= ?
                ORDER BY c.last_active_at DESC
                LIMIT 5
                """,
                (f"-{idle_minutes} minutes", min_messages),
            )
        ).fetchall()

    for row in rows:
        conv_id = row["id"]
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            msgs = await (
                await db.execute(
                    """
                    SELECT role, content FROM messages
                    WHERE conversation_id = ?
                    ORDER BY id ASC LIMIT 20
                    """,
                    (conv_id,),
                )
            ).fetchall()

        if len(msgs) < min_messages:
            continue

        transcript = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in msgs)
        prompt = (
            f"Summarize this conversation in 2-3 factual sentences for future recall. "
            f"Topic hint: {row['topic'] or 'general'}.\n\n{transcript}"
        )
        try:
            summary = await ollama.chat(
                prompt,
                system="Extract durable facts and preferences only. Plain text, no lists.",
            )
            summary = summary.strip()[:600]
            if summary:
                await store(
                    summary,
                    kind="session",
                    topic=row["topic"] or _topic_key(summary),
                    importance=2,
                    source=conv_id,
                )
                compressed += 1
        except Exception:
            continue

    return {"compressed": compressed}


async def list_recent(*, limit: int = 20) -> list[dict]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT id, topic, content, kind, importance, source, created_at
                FROM memory_entries ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def stats() -> dict:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        total = await (await db.execute("SELECT COUNT(*) FROM memory_entries")).fetchone()
        by_kind = await (
            await db.execute(
                "SELECT kind, COUNT(*) AS n FROM memory_entries GROUP BY kind ORDER BY n DESC"
            )
        ).fetchall()
    return {
        "total": total[0] if total else 0,
        "by_kind": {row[0]: row[1] for row in by_kind},
        "data_dir": str(settings.data_dir),
    }
