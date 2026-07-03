"""Tests for free-first routing and parallel terminal execution."""

import pytest

from jarvis.services import local_runtime, terminal


def test_free_first_blocks_cursor_reasoning(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.free_first", True)
    monkeypatch.setattr("jarvis.config.settings.cursor_api_key", "cursor_" + "x" * 40)
    assert local_runtime.should_use_cursor_reasoning("why is the sky blue", kind="reason") is False


def test_free_first_allows_cursor_for_build(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.free_first", True)
    monkeypatch.setattr("jarvis.config.settings.cursor_api_key", "cursor_" + "x" * 40)
    text = "build app a todo list with react"
    assert local_runtime.should_escalate_to_cursor(text, kind="code") is True


def test_free_first_skips_cursor_without_key(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.free_first", True)
    monkeypatch.setattr("jarvis.config.settings.cursor_api_key", "")
    assert local_runtime.should_escalate_to_cursor("build app", kind="code") is False


@pytest.mark.asyncio
async def test_terminal_parallel_runs_all(monkeypatch):
    calls: list[str] = []

    async def fake_execute(text, *, full_access=False, voice=False):
        calls.append(text)
        return {"ok": True, "reply": f"done:{text}", "command": text}

    async def noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(terminal, "execute", fake_execute)
    monkeypatch.setattr("jarvis.services.activity_stream.emit", noop_emit)

    results = await terminal.execute_parallel(
        ["run echo one", "run echo two"],
        full_access=True,
    )
    assert len(results) == 2
    assert all(r.get("ok") for r in results)
    assert len(calls) == 2
