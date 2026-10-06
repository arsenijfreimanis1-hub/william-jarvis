"""Free-provider gateway: catalog, key discovery, usage ledger, routing chain, speech/vector fallbacks."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.routers import api
from jarvis.services import memory, ollama
from jarvis.services.providers import catalog, gateway, keys, scout, usage, vectors


@pytest.fixture
def provider_db(monkeypatch, tmp_path):
    db_path = tmp_path / "providers.db"
    monkeypatch.setattr(usage, "DB_PATH", db_path)
    monkeypatch.setattr(vectors, "DB_PATH", db_path)
    monkeypatch.setattr(memory, "DB_PATH", db_path)
    monkeypatch.setattr("jarvis.config.settings.keys_file", tmp_path / "keys.env")
    monkeypatch.setattr("jarvis.config.settings.keychain_lookup_enabled", False)
    monkeypatch.setattr("jarvis.config.settings.gateway_mode", "free_cloud_first")
    monkeypatch.setattr("jarvis.config.settings.personality_prompt", "")
    monkeypatch.setattr("jarvis.config.settings.vigil_proxy_enabled", False)
    # Keep the real .env out of the picture.
    monkeypatch.setattr(keys, "ROOT", tmp_path)
    for spec in catalog.PROVIDERS.values():
        if spec.settings_attr:
            monkeypatch.setattr(f"jarvis.config.settings.{spec.settings_attr}", "")
        for env in spec.env_keys:
            monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(keys, "_config_file_candidates", lambda spec: [])
    gateway.last_decision.clear()
    return db_path


def test_catalog_covers_master_list():
    ids = set(catalog.PROVIDERS)
    assert {"gemini", "mistral", "codestral", "groq", "huggingface", "openrouter", "pinecone", "supabase", "ollama"} <= ids
    assert catalog.PROVIDERS["gemini"].model_for("long_context") == "gemini-2.5-flash"
    assert catalog.PROVIDERS["mistral"].model_for("code") == "codestral-latest"
    assert catalog.PROVIDERS["groq"].model_for("stt").startswith("whisper")
    for cap in catalog.ALL_CAPABILITIES:
        if cap in ("vector", "mesh3d", "stt", "tts"):
            continue
        chain = catalog.providers_for(cap)
        assert chain[-1].id == "ollama", cap
    groups = catalog.capability_groups()
    assert "Website & App Development" in groups and "3D CAD & Modeling" in groups


def test_key_discovery_sources(provider_db, monkeypatch, tmp_path):
    assert keys.inspect("groq").found is False
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a" * 48)
    match = keys.inspect("groq")
    assert match.found and match.source == "environ" and match.env_name == "GROQ_API_KEY"
    assert match.masked.startswith("gsk_") and "a" * 48 not in match.masked

    # Wrong prefix is rejected with a reason
    monkeypatch.setenv("GEMINI_API_KEY", "notavalidkey-" + "b" * 30)
    bad = keys.inspect("gemini")
    assert bad.found is False and "prefix" in (bad.reason or "")

    # keys.env file is honoured
    (tmp_path / "keys.env").write_text("MISTRAL_API_KEY=" + "m" * 32 + "\n", encoding="utf-8")
    assert keys.inspect("mistral").source == "keys.env"
    assert "mistral" in keys.configured_providers()
    assert "ollama" in keys.configured_providers()


def test_save_key_writes_private_file(provider_db, tmp_path, monkeypatch):
    result = keys.save_key("gemini", "AIza" + "x" * 35)
    assert result["ok"] and result["env_name"] == "GEMINI_API_KEY"
    body = (tmp_path / "keys.env").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=AIza" in body
    assert keys.inspect("gemini").found
    rejected = keys.save_key("gemini", "your_key_here")
    assert rejected["ok"] is False
    removed = keys.remove_key("gemini")
    assert "GEMINI_API_KEY" in removed["removed"]
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert keys.inspect("gemini").found is False


@pytest.mark.asyncio
async def test_usage_ledger_cooldown_and_headroom(provider_db, monkeypatch):
    await usage.ensure_tables()
    await usage.record("groq", capability="code", ok=True, model="llama", prompt_tokens=100, completion_tokens=50)
    await usage.record("groq", capability="code", ok=False, status_code=429, error="rate limited")
    counts = await usage.counts_today("groq")
    assert counts["calls_today"] == 2 and counts["errors_today"] == 1 and counts["tokens_today"] == 150

    assert not await usage.is_cooling("groq")
    await usage.set_cooldown("groq", seconds=120, reason="429")
    assert await usage.is_cooling("groq")
    await usage.clear_cooldown("groq")
    assert not await usage.is_cooling("groq")

    # Exhaust daily headroom artificially
    monkeypatch.setattr(catalog.PROVIDERS["openrouter"].free_tier, "requests_per_day", 2)
    await usage.record("openrouter", capability="chat", ok=True)
    await usage.record("openrouter", capability="chat", ok=True)
    room = await usage.headroom("openrouter")
    assert "requests_per_day" in room["exhausted"] and room["available"] is False
    summary = await usage.summary(days=7)
    assert summary["providers"]["groq"]["calls"] == 2


@pytest.mark.asyncio
async def test_chain_respects_keys_cooldown_and_mode(provider_db, monkeypatch):
    await usage.ensure_tables()
    # No keys → Ollama only
    chain = await gateway.chain("code")
    assert [s.id for s, _ in chain] == ["ollama"]

    monkeypatch.setenv("MISTRAL_API_KEY", "m" * 32)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "g" * 48)
    chain = await gateway.chain("code")
    ids = [s.id for s, _ in chain]
    assert ids[0] == "mistral" and "groq" in ids and ids[-1] == "ollama"
    assert dict((s.id, m) for s, m in chain)["mistral"] == "codestral-latest"

    await usage.set_cooldown("mistral", seconds=300)
    ids = [s.id for s, _ in await gateway.chain("code")]
    assert "mistral" not in ids and ids[0] == "groq"

    monkeypatch.setattr("jarvis.config.settings.gateway_mode", "local_first")
    ids = [s.id for s, _ in await gateway.chain("fast")]
    assert ids[0] == "ollama" and "groq" in ids

    monkeypatch.setattr("jarvis.config.settings.gateway_mode", "local_only")
    ids = [s.id for s, _ in await gateway.chain("chat")]
    assert ids == ["ollama"]


@pytest.mark.asyncio
async def test_gateway_falls_back_on_429_then_records_usage(provider_db, monkeypatch):
    await usage.ensure_tables()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "g" * 48)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "x" * 35)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if "groq" in request.url.host:
            return httpx.Response(429, json={"error": "rate"}, headers={"retry-after": "30"})
        payload = json.loads(request.content)
        if any(m["content"] == "write boilerplate" for m in payload["messages"]):
            assert payload["messages"][0]["role"] == "system"
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "const x = 1;"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        })

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gateway.httpx, "AsyncClient", fake_client)

    result = await gateway.chat_detailed(prompt="write boilerplate", system="You are William", capability="fast")
    assert result["reply"] == "const x = 1;"
    assert result["provider"] == "gemini"
    assert result["attempts"][0]["provider"] == "groq"
    assert await usage.is_cooling("groq")
    counts = await usage.counts_today("gemini")
    assert counts["calls_today"] == 1 and counts["tokens_today"] == 17
    assert gateway.last_decision["provider"] == "gemini"

    # ollama.chat is the same path
    reply = await ollama.chat("again", capability="fast")
    assert reply == "const x = 1;"


@pytest.mark.asyncio
async def test_gateway_uses_ollama_when_cloud_fails(provider_db, monkeypatch):
    await usage.ensure_tables()

    async def fake_local(*, system=None, messages):
        return "local answer"

    monkeypatch.setattr(ollama, "local_chat", fake_local)
    result = await gateway.chat_detailed(prompt="hello", capability="chat")
    assert result["provider"] == "ollama" and result["reply"] == "local answer"


def test_infer_capability():
    assert gateway.infer_capability("x", kind="code") == "code"
    assert gateway.infer_capability("design an openscad bracket") == "cad"
    assert gateway.infer_capability("make a react component") == "fast"
    assert gateway.infer_capability("a" * 30000) == "long_context"
    assert gateway.infer_capability("how are you") == "chat"


@pytest.mark.asyncio
async def test_semantic_memory_local_vectors(provider_db, monkeypatch):
    await memory.ensure_tables()
    await vectors.ensure_tables()
    monkeypatch.setattr("jarvis.config.settings.semantic_memory_enabled", True)

    table = {
        "boss likes espresso in the morning": [1.0, 0.0, 0.0],
        "the pc sleeps to cool down": [0.0, 1.0, 0.0],
        "coffee preference": [0.9, 0.1, 0.0],
    }

    async def fake_embed(texts):
        return {"vectors": [table.get(t, [0.0, 0.0, 1.0]) for t in texts], "provider": "test", "model": "t"}

    monkeypatch.setattr(vectors, "embed", fake_embed)
    await memory.store("boss likes espresso in the morning", kind="preference", importance=3)
    await memory.store("the pc sleeps to cool down", kind="fact")

    hits = await vectors.semantic_search("coffee preference", limit=2)
    assert hits and hits[0]["content"] == "boss likes espresso in the morning"
    assert hits[0]["store"] == "local"

    merged = await memory.retrieve("coffee preference", limit=3)
    assert any("espresso" in h["content"] for h in merged)
    stats = await vectors.stats()
    assert stats["local_vectors"] == 2 and stats["dim"] == 3


@pytest.mark.asyncio
async def test_scout_status_and_overview(provider_db, monkeypatch):
    await usage.ensure_tables()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "g" * 48)
    report = await scout.scan_keys(announce=False)
    assert any(r["provider"] == "groq" for r in report["present"])
    text = await scout.status_reply()
    assert "groq" in text and "Missing" in text
    overview = await scout.overview()
    groq = next(p for p in overview["providers"] if p["id"] == "groq")
    assert groq["key"]["found"] is True
    assert overview["chains"]["stt"][0]["provider"] == "groq"
    shopping = await scout.shopping_list()
    assert all(row["provider"] != "groq" for row in shopping)
    assert shopping[0]["signup_url"]


def test_provider_api_endpoints(provider_db, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.fleet_token", "")
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)

    resp = client.get("/api/providers")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "free_cloud_first"

    resp = client.post("/api/providers/keys", json={"provider": "groq", "key": "gsk_" + "k" * 48})
    assert resp.status_code == 200 and resp.json()["masked"].startswith("gsk_")

    resp = client.get("/api/providers/usage?days=1")
    assert resp.status_code == 200 and "providers" in resp.json()

    resp = client.get("/api/gateway/status")
    assert resp.status_code == 200 and "groq" in resp.json()["configured"]

    resp = client.get("/api/speech/status")
    assert resp.status_code == 200 and resp.json()["stt"]["groq"] is True

    resp = client.get("/api/cad/status")
    assert resp.status_code == 200 and "openscad" in resp.json()

    resp = client.post("/api/providers/keys", json={"provider": "groq", "key": "bad"})
    assert resp.status_code in (400, 422)


def test_gateway_chat_requires_fleet_token(provider_db, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.fleet_token", "shared-secret")
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    resp = client.post("/api/gateway/chat", json={"message": "hi"})
    assert resp.status_code == 401
