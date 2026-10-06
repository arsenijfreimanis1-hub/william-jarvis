import pytest

from jarvis.services import agent_author, agent_registry, agent_runtime, router
from jarvis.services.agent_types import AgentRecord, AgentSpec


def _agent_record(**overrides) -> AgentRecord:
    payload = {
        "id": 1,
        "name": "Code Reviewer",
        "name_key": "code-reviewer",
        "purpose": "Review backend changes",
        "instructions": "Review Python backend changes and summarize issues first.",
        "trigger_phrases": ["review backend"],
        "runtime": {
            "execution_engine": "cursor",
            "autonomy_mode": "supervised",
            "model": "composer-2.5",
            "workspace_dir": "/tmp/workspace",
            "allowed_tools": ["cursor_agent.run", "memory.retrieve", "web.research"],
        },
        "status": "active",
        "version": 1,
        "parent_agent_id": None,
        "performance_score": 0.0,
        "learning_notes": "Prefer concise issue lists.",
        "last_used_at": None,
        "last_improved_at": None,
        "created_at": "2026-06-20 00:00:00",
        "updated_at": "2026-06-20 00:00:00",
    }
    payload.update(overrides)
    return AgentRecord.model_validate(payload)


@pytest.mark.anyio
async def test_register_agent_persists_and_versions(monkeypatch, tmp_path):
    db_path = tmp_path / "agents.db"
    monkeypatch.setattr(agent_registry, "DB_PATH", db_path)

    created = await agent_registry.register_agent(
        AgentSpec(
            name="Code Reviewer",
            purpose="Review backend changes",
            instructions="Review Python backend changes and summarize issues first.",
            trigger_phrases=["review backend"],
            runtime={"allowed_tools": ["cursor_agent.run", "web.research"]},
        )
    )
    assert created.version == 1
    assert created.runtime.allowed_tools == ["cursor_agent.run", "web.research"]

    updated = await agent_registry.register_agent(
        AgentSpec(
            name="Code Reviewer",
            purpose="Review API and backend changes",
            instructions="Review backend and API changes with a strict bug-first order.",
            trigger_phrases=["review backend", "review api"],
            runtime={"allowed_tools": ["cursor_agent.run", "memory.retrieve"]},
        )
    )
    assert updated.id == created.id
    assert updated.version == 2
    assert updated.purpose == "Review API and backend changes"

    fetched = await agent_registry.get_agent("code reviewer")
    assert fetched is not None
    assert fetched.version == 2
    assert fetched.trigger_phrases == ["review backend", "review api"]
    assert fetched.runtime.allowed_tools == ["cursor_agent.run", "memory.retrieve"]


@pytest.mark.anyio
async def test_update_and_archive_agent(monkeypatch, tmp_path):
    db_path = tmp_path / "agents.db"
    monkeypatch.setattr(agent_registry, "DB_PATH", db_path)

    created = await agent_registry.create_agent(
        AgentSpec(
            name="Triage Agent",
            purpose="Triage inbound issues",
            instructions="Label and summarize inbound issues.",
            trigger_phrases=["triage issue"],
            runtime={"allowed_tools": ["cursor_agent.run", "memory.retrieve"]},
        )
    )

    updated = await agent_registry.update_agent(
        created.id,
        {
            "purpose": "Triage and route inbound issues",
            "runtime": {"allowed_tools": ["cursor_agent.run", "web.research"]},
            "learning_notes": "Prefer quick summaries first.",
        },
    )
    assert updated is not None
    assert updated.version == 2
    assert updated.purpose == "Triage and route inbound issues"
    assert updated.runtime.allowed_tools == ["cursor_agent.run", "web.research"]
    assert updated.learning_notes == "Prefer quick summaries first."

    archived = await agent_registry.archive_agent(created.id)
    assert archived is not None
    assert archived.status == "archived"
    assert archived.version == 3
    assert await agent_registry.get_agent_by_id(created.id) is None
    assert await agent_registry.get_agent_by_id(created.id, include_inactive=True) is not None


@pytest.mark.anyio
async def test_agent_author_drafts_spec_from_prompt(monkeypatch):
    async def fake_run(prompt: str, *, cwd: str | None = None, model: str | None = None):
        assert "Draft a reusable specialist agent spec" in prompt
        assert "Review pull requests for backend regressions" in prompt
        assert cwd == "/tmp/agents"
        assert model == "composer-2.5"
        return {
            "ok": True,
            "result": """
            {
              "name": "Backend Reviewer",
              "purpose": "Review backend pull requests",
              "instructions": "Review Python backend diffs and list issues first.",
              "trigger_phrases": ["review backend pr"],
              "status": "active",
              "runtime": {
                "execution_engine": "cursor",
                "autonomy_mode": "supervised",
                "model": "composer-2.5",
                "workspace_dir": "/tmp/agents",
                "allowed_tools": ["cursor_agent.run", "web.research"]
              },
              "parent_agent_id": null,
              "learning_notes": "Keep findings concise."
            }
            """,
        }

    monkeypatch.setattr(agent_author.cursor_agent, "run", fake_run)

    spec = await agent_author.draft_spec_from_prompt(
        "Review pull requests for backend regressions",
        workspace_dir="/tmp/agents",
        model="composer-2.5",
    )
    assert spec.name == "Backend Reviewer"
    assert spec.runtime.allowed_tools == ["cursor_agent.run", "web.research"]
    assert spec.trigger_phrases == ["review backend pr"]


@pytest.mark.anyio
async def test_execute_agent_uses_runtime_config(monkeypatch):
    agent = _agent_record()
    captured: dict = {}

    async def fake_get_timeline_block(*, limit=5):
        assert limit == 5
        return "RECENT ACTIVITY"

    async def fake_get_block(task: str, *, limit: int = 4):
        captured["memory_task"] = task
        return "RELEVANT MEMORY"

    async def fake_get_lessons_block():
        return "LESSONS"

    async def fake_get_agent_lessons_block(name: str, *, limit: int = 3):
        assert name == "Code Reviewer"
        assert limit == 3
        return "AGENT-SPECIFIC LEARNING"

    async def fake_run(prompt: str, *, cwd: str | None = None, model: str | None = None):
        captured["prompt"] = prompt
        captured["cwd"] = cwd
        captured["model"] = model
        return {"ok": True, "result": "Agent reply", "run_id": "run-123"}

    async def fake_record_agent_execution(agent_record, task: str, result: dict, *, voice: bool = False, conversation_id=None):
        captured["recorded_name"] = agent_record.name
        captured["recorded_task"] = task
        captured["recorded_result"] = result
        captured["recorded_voice"] = voice
        captured["recorded_conversation"] = conversation_id
        return {"score": 0.91, "outcome": "success"}

    monkeypatch.setattr(agent_runtime.event_log, "get_timeline_block", fake_get_timeline_block)
    monkeypatch.setattr(agent_runtime.memory, "get_block", fake_get_block)
    monkeypatch.setattr(agent_runtime.learning, "get_lessons_block", fake_get_lessons_block)
    monkeypatch.setattr(agent_runtime.agent_learning, "get_agent_lessons_block", fake_get_agent_lessons_block)
    monkeypatch.setattr(agent_runtime.sessions, "get_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(agent_runtime.sessions, "format_context", lambda *args, **kwargs: "")
    monkeypatch.setattr(agent_runtime.cursor_agent, "run", fake_run)
    monkeypatch.setattr(agent_runtime.agent_learning, "record_agent_execution", fake_record_agent_execution)

    result = await agent_runtime.execute_agent(agent, "Review the latest backend diff")

    assert result["ok"] is True
    assert result["engine"] == "agent"
    assert result["agent_name"] == "Code Reviewer"
    assert captured["cwd"] == "/tmp/workspace"
    assert captured["model"] == "composer-2.5"
    assert captured["recorded_name"] == "Code Reviewer"
    assert captured["recorded_task"] == "Review the latest backend diff"
    assert captured["memory_task"] == "Review the latest backend diff"
    assert "Allowed tools: cursor_agent.run, memory.retrieve, web.research" in captured["prompt"]
    assert "AGENT-SPECIFIC LEARNING" in captured["prompt"]
    assert "SPECIALIST TASK:\nReview the latest backend diff" in captured["prompt"]
    assert result["agent_score"] == 0.91
    assert result["agent_outcome"] == "success"


@pytest.mark.anyio
async def test_router_routes_explicit_named_agent(monkeypatch):
    agent = _agent_record()

    async def fake_try_local_execute(text: str, *, voice: bool = False):
        return None

    async def fake_resolve_invocation(text: str):
        assert text == "use agent Code Reviewer: review this service"
        return agent, "review this service"

    async def fake_execute_agent(agent_record, task: str, *, voice: bool = False, conversation_id=None, task_id=None):
        assert agent_record.name == "Code Reviewer"
        assert task == "review this service"
        return {
            "ok": True,
            "reply": "Found two likely issues.",
            "engine": "agent",
            "intent": "agent",
            "agent_name": "Code Reviewer",
            "agent_version": 1,
            "allowed_tools": ["cursor_agent.run"],
        }

    monkeypatch.setattr(router.executor, "try_local_execute", fake_try_local_execute)
    monkeypatch.setattr(router.agent_runtime, "resolve_invocation", fake_resolve_invocation)
    monkeypatch.setattr(router.agent_runtime, "execute_agent", fake_execute_agent)

    result = await router.route("use agent Code Reviewer: review this service")
    assert result["engine"] == "agent"
    assert result["intent"] == "agent"
    assert result["agent_name"] == "Code Reviewer"
    assert result["reply"] == "Found two likely issues."
