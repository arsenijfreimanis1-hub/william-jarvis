"""Phase 1 core: spans, journal, agent contract loader, rules engine, governor, decision policy."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.services import agent_loader, agent_registry, agent_runtime, journal, resource_governor, spans
from jarvis.services.agent_types import AgentRuntimeConfig, AgentSpec


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    db = tmp_path / "jarvis.db"
    for mod in (spans, journal, agent_registry):
        monkeypatch.setattr(mod, "DB_PATH", db)
    monkeypatch.setattr("jarvis.services.agent_learning.DB_PATH", db)
    return db


# --------------------------------------------------------------------------- spans

@pytest.mark.asyncio
async def test_spans_nest_and_attribute_tokens(isolated_db):
    async with spans.span("orchestrator", "mini", input_text="hello") as root:
        async with spans.span("agent", "worker", agent="Worker") as child:
            assert child.parent_span_id == root.span_id
            assert child.trace_id == root.trace_id
            spans.add_tokens(10, 5, provider="groq", model="llama")
            assert child.prompt_tokens == 10 and child.completion_tokens == 5
        # tokens bubble to the root
        assert root.prompt_tokens == 10 and root.completion_tokens == 5
    tree = await spans.trace_tree(root.trace_id)
    assert tree["count"] == 2
    assert tree["roots"][0]["children"][0]["agent"] == "Worker"
    assert tree["roots"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_span_failure_recorded(isolated_db):
    with pytest.raises(RuntimeError):
        async with spans.span("tool", "boom"):
            raise RuntimeError("nope")
    rows = await spans.list_spans(limit=5)
    assert rows[0]["status"] == "failed" and "nope" in rows[0]["error"]


# --------------------------------------------------------------------------- journal

@pytest.mark.asyncio
async def test_journal_write_resolve_stats(isolated_db):
    await journal.input("user said hi", source="test")
    p = await journal.problem("something broke", agent="Tester")
    await journal.strength("fast answer", agent="Tester")
    assert (await journal.open_problems())[0]["id"] == p["id"]
    assert await journal.resolve(p["id"], by="test")
    assert await journal.open_problems() == []
    stats = await journal.stats(days=1)
    assert stats["by_kind"]["problem"] == 1 and stats["by_kind"]["strength"] == 1
    assert stats["by_agent"][0]["agent"] == "Tester"
    assert "fast answer" not in await journal.block()  # block only shows problems/weaknesses/fixes


@pytest.mark.asyncio
async def test_journal_inherits_current_span(isolated_db):
    async with spans.span("agent", "x", agent="Spanny") as sp:
        row = await journal.decision("chose rules")
    assert row["agent"] == "Spanny" and row["trace_id"] == sp.trace_id


# --------------------------------------------------------------------------- agent contract

def test_parse_folder_contract(tmp_path: Path):
    folder = tmp_path / "demo-agent"
    folder.mkdir()
    (folder / "README.md").write_text(
        "---\nname: Demo Agent\nengine: rules\nentrypoint: jarvis.agents.rules.system:live\n"
        "requires: [System Monitor, journal-keeper]\ntoken_budget: 0\ntriggers:\n  - demo me\n"
        "skills: [research]\ncooldown_hours: 2\n---\n# Demo Agent\n\nDo demo things.\n",
        encoding="utf-8",
    )
    (folder / "POSTTHOUGHT.md").write_text("reflect {task}", encoding="utf-8")
    spec = agent_loader.parse_folder(folder)
    assert spec.name == "Demo Agent"
    assert spec.runtime.engine == "rules"
    assert spec.runtime.allowed_tools[0] == "rules.run"
    assert spec.runtime.requires == ["system-monitor", "journal-keeper"]
    assert spec.runtime.cooldown_hours == 2
    assert spec.runtime.post_thought == "reflect {task}"
    assert spec.instructions == "Do demo things."
    assert spec.trigger_phrases == ["demo me"]


def test_repo_agent_definitions_all_parse():
    found = agent_loader.discover()
    names = {spec.name for _, spec in found}
    assert {"Steward", "Scout", "Mentor", "Governor", "System Monitor", "Self Healer", "Key Hunter"} <= names
    for _, spec in found:
        if spec.runtime.engine == "rules":
            assert spec.runtime.entrypoint, f"{spec.name} rules agent lacks entrypoint"
            agent_runtime._load_entrypoint(spec.runtime.entrypoint)  # importable
    mentor = next(s for _, s in found if s.name == "Mentor")
    assert mentor.runtime.cooldown_hours == 24


def test_entrypoint_validation():
    with pytest.raises(ValueError):
        AgentRuntimeConfig(engine="rules", entrypoint="not a path")


# --------------------------------------------------------------------------- rules engine end-to-end

async def _demo_entry(task: str, ctx: dict) -> dict:
    return {"ok": True, "reply": f"handled: {task}", "data": {"required": [r["agent"] for r in ctx["required"]]}}


@pytest.mark.asyncio
async def test_rules_agent_runs_with_zero_tokens_and_requires(isolated_db, monkeypatch):
    monkeypatch.setattr(agent_runtime, "_load_entrypoint", lambda ep: _demo_entry)
    monkeypatch.setattr(agent_runtime, "_post_thought", _noop_post_thought)
    helper = await agent_registry.register_agent(AgentSpec(
        name="Helper", purpose="help", instructions="help",
        runtime=AgentRuntimeConfig(engine="rules", entrypoint="jarvis.agents.rules.system:live", allowed_tools=["rules.run"]),
    ))
    main = await agent_registry.register_agent(AgentSpec(
        name="Main", purpose="main", instructions="main", trigger_phrases=["do main"],
        runtime=AgentRuntimeConfig(engine="rules", entrypoint="jarvis.agents.rules.system:live",
                                   allowed_tools=["rules.run"], requires=["helper"]),
    ))
    result = await agent_runtime.execute_agent(main, "do main thing")
    assert result["ok"], result
    assert result["agent_engine"] == "rules"
    assert result["tokens"] == 0
    assert result["required"] == [{"agent": "Helper", "ok": True}]
    assert result["data"]["required"] == ["Helper"]
    tree = await spans.trace_tree(result["trace_id"])
    root = tree["roots"][0]
    kinds = {c["kind"] for c in root["children"]}
    assert "agent" in kinds and "tool" in kinds  # child agent + rules tool spans
    entries = await journal.list_entries(limit=20)
    assert any(e["kind"] == "input" and e["agent"] == "Main" for e in entries)


async def _noop_post_thought(*args, **kwargs):
    return {}


@pytest.mark.asyncio
async def test_cooldown_skips_agent(isolated_db, monkeypatch):
    monkeypatch.setattr(agent_runtime, "_post_thought", _noop_post_thought)
    rec = await agent_registry.register_agent(AgentSpec(
        name="Mentorish", purpose="m", instructions="m",
        runtime=AgentRuntimeConfig(engine="rules", entrypoint="jarvis.agents.rules.system:live",
                                   allowed_tools=["rules.run"], cooldown_hours=24),
    ))
    await agent_registry.record_agent_usage(rec.name)
    rec = await agent_registry.get_agent(rec.name)
    result = await agent_runtime.execute_agent(rec, "coach me")
    assert result["ok"] is False and result.get("cooldown") is True


# --------------------------------------------------------------------------- governor

@pytest.mark.asyncio
async def test_governor_policy_pause_and_resume(isolated_db, monkeypatch):
    resource_governor._state.update({"mode": "normal", "worker_parallel_cap": None, "paused_since": None})
    low = {"cpu": {"busy": 10.0}, "memory": {"pressure_free_percent": 5, "free_percent": 5}, "swap": {"used_mb": 0},
           "thermal": {"throttled": False}, "ours": []}
    await resource_governor.evaluate(low)
    assert resource_governor._state["mode"] == "constrained"
    assert resource_governor.worker_parallel_cap() == resource_governor.policy["worker_min_parallel"]
    ok = {"cpu": {"busy": 10.0}, "memory": {"pressure_free_percent": 60, "free_percent": 60}, "swap": {"used_mb": 0},
          "thermal": {"throttled": False}, "ours": []}
    await resource_governor.evaluate(ok)
    assert resource_governor._state["mode"] == "normal"
    assert resource_governor.worker_parallel_cap() is None
    actions = [d["action"] for d in resource_governor._decisions]
    assert actions[-2:] == ["pause", "resume"]
    decisions = await journal.list_entries(kind="decision", limit=5)
    assert any("governor pause" in d["summary"] for d in decisions)


def test_governor_sample_parses_real_tools():
    snap = resource_governor._sample_sync()
    assert snap["cpu"]["busy"] is not None
    assert snap["memory"]["total_mb"] > 1000
    assert isinstance(snap["top_processes"], list)


# --------------------------------------------------------------------------- decision policy

def test_decision_policy_prefers_rules_then_free_cloud():
    from jarvis.brain import decision

    assert decision.decide("anything", rules_available=True).engine == "rules"
    d = decision.decide("write a python function", kind="code")
    assert d.engine in ("free_cloud", "cursor", "ollama")
    assert d.capability == "code"


def test_persona_follows_role(monkeypatch):
    from jarvis.brain import persona

    monkeypatch.setattr("jarvis.config.settings.personality_prompt", "")
    monkeypatch.setattr("jarvis.config.settings.role", "macbook")
    assert "Scout" in persona.prompt()
    monkeypatch.setattr("jarvis.config.settings.role", "mini")
    assert "Steward" in persona.prompt()
