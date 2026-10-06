"""Key rotation: soft-ceiling ordering between tasks, provider pinning within a trace."""

from __future__ import annotations

import pytest

from jarvis.services import spans
from jarvis.services.providers import catalog, gateway, keys, usage


@pytest.mark.asyncio
async def test_chain_demotes_provider_past_soft_ceiling(monkeypatch):
    cloud_ids = [spec.id for spec in catalog.providers_for("chat") if spec.kind != "local"][:2]
    if len(cloud_ids) < 2:
        pytest.skip("need two cloud providers in catalog")
    first, second = cloud_ids
    monkeypatch.setattr(keys, "resolve", lambda pid: "k" if pid in (first, second) else "")

    async def fake_headroom(pid):
        return {"available": True, "daily_percent": 95.0 if pid == first else 10.0}

    monkeypatch.setattr(usage, "headroom", fake_headroom)
    monkeypatch.setattr("jarvis.config.settings.gateway_mode", "free_cloud_first")
    ordered = await gateway.chain("chat")
    ids = [spec.id for spec, _ in ordered]
    assert ids.index(second) < ids.index(first), ids
    assert ids[-1] == "ollama"


@pytest.mark.asyncio
async def test_provider_pinned_within_trace(monkeypatch, tmp_path):
    monkeypatch.setattr(spans, "DB_PATH", tmp_path / "s.db")
    calls: list[str] = []

    async def fake_chain(capability, *, exclude=None):
        a = catalog.providers_for("chat")
        cloud = [s for s in a if s.kind != "local"][:2]
        return [(s, "m") for s in cloud] + [(catalog.PROVIDERS["ollama"], "llama")]

    async def fake_call(spec, model, **kw):
        calls.append(spec.id)
        return {"reply": "ok", "provider": spec.id, "model": model}

    monkeypatch.setattr(gateway, "chain", fake_chain)
    monkeypatch.setattr(gateway, "call_openai_compatible", fake_call)
    monkeypatch.setattr("jarvis.services.vigil_proxy.configured", lambda: False)

    async with spans.span("orchestrator", "t"):
        first = await gateway.chat_detailed(prompt="hi")
        # Second call in the same trace prefers the same provider even if told to prefer nothing.
        second = await gateway.chat_detailed(prompt="again")
        root = spans.root()
        assert root.metadata["pinned_provider"] == first["provider"]
    assert first["provider"] == second["provider"]
