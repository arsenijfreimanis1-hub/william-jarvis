"""Embeddings (Gemini → Mistral → HF → Ollama) and vector memory (Pinecone → Supabase → local SQLite)."""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

import aiosqlite
import httpx

from jarvis.config import settings
from jarvis.database import DB_PATH
from jarvis.services.providers import catalog, keys, usage

log = logging.getLogger("jarvis.providers.vectors")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_vectors (
    entry_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'fact',
    importance INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


# ─── Embeddings ─────────────────────────────────────────────────────────────


async def _embed_openai_compatible(spec: catalog.ProviderSpec, texts: list[str]) -> dict[str, Any]:
    key = keys.resolve(spec.id)
    if not key:
        raise RuntimeError(f"{spec.id}: no key")
    model = spec.models.get("embed")
    url = f"{spec.base_url.rstrip('/')}/embeddings"
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json={"model": model, "input": texts},
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        await usage.record(spec.id, capability="embed", ok=False, model=model, status_code=resp.status_code,
                           latency_ms=latency, error=resp.text[:200])
        await usage.set_cooldown(spec.id, seconds=90 if resp.status_code == 429 else 45, reason=f"embed {resp.status_code}")
        raise RuntimeError(f"{spec.id} embed {resp.status_code}")
    data = resp.json().get("data") or []
    vectors = [d.get("embedding") for d in sorted(data, key=lambda d: d.get("index", 0))]
    await usage.record(spec.id, capability="embed", ok=True, model=model, status_code=resp.status_code,
                       latency_ms=latency, prompt_tokens=sum(len(t) // 4 for t in texts))
    return {"vectors": vectors, "provider": spec.id, "model": model}


async def _embed_hf(texts: list[str]) -> dict[str, Any]:
    spec = catalog.PROVIDERS["huggingface"]
    key = keys.resolve("huggingface")
    if not key:
        raise RuntimeError("huggingface: no key")
    model = spec.models.get("embed", "sentence-transformers/all-MiniLM-L6-v2")
    url = f"{spec.base_url.rstrip('/')}/hf-inference/models/{model}/pipeline/feature-extraction"
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json={"inputs": texts}, headers={"Authorization": f"Bearer {key}"})
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        await usage.record("huggingface", capability="embed", ok=False, model=model, status_code=resp.status_code,
                           latency_ms=latency, error=resp.text[:200])
        await usage.set_cooldown("huggingface", seconds=120 if resp.status_code in (429, 503) else 45)
        raise RuntimeError(f"hf embed {resp.status_code}")
    vectors = resp.json()
    # Some models return token-level vectors: mean-pool to a single vector per input.
    pooled: list[list[float]] = []
    for v in vectors:
        if v and isinstance(v[0], list):
            dim = len(v[0])
            pooled.append([sum(tok[i] for tok in v) / len(v) for i in range(dim)])
        else:
            pooled.append(v)
    await usage.record("huggingface", capability="embed", ok=True, model=model, latency_ms=latency,
                       prompt_tokens=sum(len(t) // 4 for t in texts))
    return {"vectors": pooled, "provider": "huggingface", "model": model}


async def _embed_ollama(texts: list[str]) -> dict[str, Any]:
    from jarvis.services import ollama

    started = time.monotonic()
    try:
        vectors = await ollama.embed(texts)
    except Exception as exc:
        await usage.record("ollama", capability="embed", ok=False, error=str(exc)[:200])
        raise
    await usage.record("ollama", capability="embed", ok=True, model="nomic-embed-text",
                       latency_ms=int((time.monotonic() - started) * 1000))
    return {"vectors": vectors, "provider": "ollama", "model": "nomic-embed-text"}


async def embed(texts: list[str]) -> dict[str, Any]:
    """Return {vectors, provider, model}; raises if every backend fails."""
    texts = [t[:8000] for t in texts if t and t.strip()]
    if not texts:
        return {"vectors": [], "provider": None, "model": None}
    errors: list[str] = []
    mode = (settings.gateway_mode or "free_cloud_first").lower()
    steps: list = []
    if mode != "local_only":
        for pid in catalog.CHAINS["embed"]:
            if not keys.resolve(pid) or await usage.is_cooling(pid):
                continue
            spec = catalog.PROVIDERS[pid]
            if pid == "huggingface":
                steps.append(lambda: _embed_hf(texts))
            else:
                steps.append(lambda s=spec: _embed_openai_compatible(s, texts))
    if mode == "local_first":
        steps.insert(0, lambda: _embed_ollama(texts))
    else:
        steps.append(lambda: _embed_ollama(texts))
    for step in steps:
        try:
            result = await step()
            if result.get("vectors"):
                return result
        except Exception as exc:
            errors.append(str(exc)[:160])
    raise RuntimeError("embedding failed: " + "; ".join(errors))


# ─── Vector stores ──────────────────────────────────────────────────────────


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def pinecone_configured() -> bool:
    return bool(keys.resolve("pinecone") and (settings.pinecone_index_host or "").strip())


def supabase_configured() -> bool:
    return bool(keys.resolve("supabase") and (settings.supabase_url or "").strip())


def _pinecone_url(path: str) -> str:
    host = settings.pinecone_index_host.strip()
    if not host.startswith("http"):
        host = f"https://{host}"
    return f"{host.rstrip('/')}/{path.lstrip('/')}"


async def _pinecone_upsert(entry_id: str, vector: list[float], meta: dict) -> None:
    key = keys.resolve("pinecone")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            _pinecone_url("vectors/upsert"),
            json={"vectors": [{"id": entry_id, "values": vector, "metadata": meta}], "namespace": "william"},
            headers={"Api-Key": key, "Content-Type": "application/json"},
        )
    ok = resp.status_code < 400
    await usage.record("pinecone", capability="vector", ok=ok, status_code=resp.status_code,
                       error=None if ok else resp.text[:200])
    if not ok:
        raise RuntimeError(f"pinecone upsert {resp.status_code}")


async def _pinecone_query(vector: list[float], *, limit: int) -> list[dict]:
    key = keys.resolve("pinecone")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            _pinecone_url("query"),
            json={"vector": vector, "topK": limit, "includeMetadata": True, "namespace": "william"},
            headers={"Api-Key": key, "Content-Type": "application/json"},
        )
    ok = resp.status_code < 400
    await usage.record("pinecone", capability="vector", ok=ok, status_code=resp.status_code,
                       error=None if ok else resp.text[:200])
    if not ok:
        raise RuntimeError(f"pinecone query {resp.status_code}")
    out = []
    for m in resp.json().get("matches") or []:
        meta = m.get("metadata") or {}
        out.append({"id": m.get("id"), "score": float(m.get("score") or 0), "content": meta.get("content", ""),
                    "kind": meta.get("kind", "fact"), "importance": int(meta.get("importance", 1)),
                    "topic": meta.get("topic", ""), "store": "pinecone"})
    return out


def _supabase_headers() -> dict[str, str]:
    key = keys.resolve("supabase")
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates"}


async def _supabase_upsert(entry_id: str, vector: list[float], meta: dict) -> None:
    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/jarvis_memories"
    row = {"id": entry_id, "embedding": vector, "content": meta.get("content", ""), "kind": meta.get("kind", "fact"),
           "importance": meta.get("importance", 1), "topic": meta.get("topic", "")}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=row, headers=_supabase_headers())
    ok = resp.status_code < 400
    await usage.record("supabase", capability="vector", ok=ok, status_code=resp.status_code,
                       error=None if ok else resp.text[:200])
    if not ok:
        raise RuntimeError(f"supabase upsert {resp.status_code}")


async def _supabase_query(vector: list[float], *, limit: int) -> list[dict]:
    url = f"{settings.supabase_url.rstrip('/')}/rest/v1/rpc/match_jarvis_memories"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json={"query_embedding": vector, "match_count": limit},
                                 headers=_supabase_headers())
    ok = resp.status_code < 400
    await usage.record("supabase", capability="vector", ok=ok, status_code=resp.status_code,
                       error=None if ok else resp.text[:200])
    if not ok:
        raise RuntimeError(f"supabase query {resp.status_code}")
    out = []
    for r in resp.json() or []:
        out.append({"id": r.get("id"), "score": float(r.get("similarity") or 0), "content": r.get("content", ""),
                    "kind": r.get("kind", "fact"), "importance": int(r.get("importance", 1)),
                    "topic": r.get("topic", ""), "store": "supabase"})
    return out


async def _local_upsert(entry_id: str, vector: list[float], meta: dict, *, provider: str, model: str) -> None:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO memory_vectors (entry_id, provider, model, dim, vector, content, kind, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET provider = excluded.provider, model = excluded.model,
                dim = excluded.dim, vector = excluded.vector, content = excluded.content,
                kind = excluded.kind, importance = excluded.importance
            """,
            (entry_id, provider, model, len(vector), json.dumps(vector), meta.get("content", ""),
             meta.get("kind", "fact"), int(meta.get("importance", 1))),
        )
        await db.commit()


async def _local_query(vector: list[float], *, limit: int, model: str | None) -> list[dict]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if model:
            rows = await (await db.execute(
                "SELECT * FROM memory_vectors WHERE dim = ? AND model = ?", (len(vector), model))).fetchall()
        else:
            rows = await (await db.execute("SELECT * FROM memory_vectors WHERE dim = ?", (len(vector),))).fetchall()
    scored = []
    for r in rows:
        try:
            vec = json.loads(r["vector"])
        except Exception:
            continue
        scored.append({"id": r["entry_id"], "score": cosine(vector, vec), "content": r["content"], "kind": r["kind"],
                       "importance": int(r["importance"]), "topic": "", "store": "local"})
    scored.sort(key=lambda s: (s["score"], s["importance"]), reverse=True)
    return scored[:limit]


async def index_memory(entry_id: str, content: str, *, kind: str = "fact", importance: int = 1,
                       topic: str = "") -> dict[str, Any]:
    """Embed one memory entry and store it locally plus in Pinecone/Supabase if configured."""
    if not settings.semantic_memory_enabled:
        return {"ok": False, "skipped": "semantic memory disabled"}
    try:
        emb = await embed([content])
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    vector = emb["vectors"][0]
    meta = {"content": content[:1500], "kind": kind, "importance": importance, "topic": topic}
    stores = ["local"]
    await _local_upsert(entry_id, vector, meta, provider=emb["provider"], model=emb["model"])
    for name, fn, configured in (("pinecone", _pinecone_upsert, pinecone_configured),
                                 ("supabase", _supabase_upsert, supabase_configured)):
        if configured():
            try:
                await fn(entry_id, vector, meta)
                stores.append(name)
            except Exception as exc:
                log.info("%s upsert failed: %s", name, str(exc)[:120])
    return {"ok": True, "provider": emb["provider"], "model": emb["model"], "dim": len(vector), "stores": stores}


async def semantic_search(query: str, *, limit: int = 5, min_score: float = 0.35) -> list[dict]:
    if not settings.semantic_memory_enabled or not query.strip():
        return []
    try:
        emb = await embed([query])
    except Exception as exc:
        log.debug("semantic search embed failed: %s", exc)
        return []
    vector = emb["vectors"][0]
    hits: list[dict] = []
    for configured, fn in ((pinecone_configured, _pinecone_query), (supabase_configured, _supabase_query)):
        if configured():
            try:
                hits = await fn(vector, limit=limit)
                break
            except Exception as exc:
                log.info("remote vector query failed, using local: %s", str(exc)[:120])
    if not hits:
        hits = await _local_query(vector, limit=limit, model=emb["model"])
    return [h for h in hits if h["score"] >= min_score]


async def stats() -> dict[str, Any]:
    await ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(dim), 0) AS dim FROM memory_vectors")).fetchone()
    return {
        "enabled": settings.semantic_memory_enabled,
        "local_vectors": int(row[0]) if row else 0,
        "dim": int(row[1]) if row else 0,
        "pinecone": pinecone_configured(),
        "supabase": supabase_configured(),
        "embed_chain": [p for p in catalog.CHAINS["embed"] if keys.resolve(p)] + ["ollama"],
    }
