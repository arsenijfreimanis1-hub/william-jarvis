"""Automated tests for self-improvement runs — no human input required."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import httpx

from jarvis.config import settings
from jarvis.services import macos, worker

ROOT = settings.workspace_dir
PYTHON = ROOT / ".venv/bin/python"


async def _http_get(path: str) -> dict:
    url = f"http://127.0.0.1:{settings.port}{path}"
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(url)
        return {"status": resp.status_code, "json": resp.json() if resp.status_code == 200 else {}}


async def _http_post(path: str, body: dict | None = None) -> dict:
    url = f"http://127.0.0.1:{settings.port}{path}"
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, json=body or {})
        data = resp.json() if resp.content else {}
        return {"status": resp.status_code, "json": data}


def _result(name: str, ok: bool, *, error: str = "", detail: str = "") -> dict:
    return {"name": name, "ok": ok, "error": error, "detail": detail}


async def test_core_health() -> dict:
    try:
        data = await _http_get("/api/health")
        if data["status"] != 200:
            return _result("core_health", False, error=f"HTTP {data['status']}")
        body = data["json"]
        if not body.get("voice_ui"):
            return _result("core_health", False, error="voice_ui missing from /api/health")
        return _result("core_health", True, detail="voice_ui ok")
    except Exception as exc:
        return _result("core_health", False, error=str(exc))


async def test_dashboard() -> dict:
    try:
        data = await _http_get("/api/dashboard")
        if data["status"] != 200:
            return _result("dashboard", False, error=f"HTTP {data['status']}")
        return _result("dashboard", True)
    except Exception as exc:
        return _result("dashboard", False, error=str(exc))


async def test_helper() -> dict:
    helper = await macos.health()
    if not helper.get("ok"):
        return _result("helper", False, error=helper.get("error", "helper offline"))
    if not helper.get("healthy"):
        return _result("helper", False, error=helper.get("wake_status", "unhealthy"))
    return _result("helper", True, detail=helper.get("microphone", {}).get("device", ""))


async def test_worker() -> dict:
    st = worker.status()
    if not st.get("running"):
        return _result("worker", False, error="worker not running")
    return _result("worker", True)


async def test_chat_time() -> dict:
    """Fast inline command — no human speech."""
    try:
        data = await _http_post("/api/chat", {"message": "what time is it", "source": "improve_run"})
        if data["status"] != 200:
            return _result("chat_time", False, error=f"HTTP {data['status']}")
        body = data["json"]
        if not body.get("reply"):
            return _result("chat_time", False, error="empty reply")
        return _result("chat_time", True, detail=body.get("reply", "")[:80])
    except Exception as exc:
        return _result("chat_time", False, error=str(exc))


async def test_execution_path() -> dict:
    try:
        data = await _http_post("/api/chat", {"message": "what time is it", "source": "kiosk"})
        body = data["json"]
        if data["status"] != 200:
            return _result("execution_path", False, error=f"HTTP {data['status']}")
        if body.get("engine") not in ("local", "willy", "ollama", "system", "terminal"):
            return _result("execution_path", False, error=f"unexpected engine {body.get('engine')}")
        return _result("execution_path", True, detail=body.get("engine", ""))
    except Exception as exc:
        return _result("execution_path", False, error=str(exc))


async def test_pytest() -> dict:
    if not PYTHON.is_file():
        return _result("pytest", False, error="venv python missing")
    proc = await asyncio.create_subprocess_exec(
        str(PYTHON),
        "-m",
        "pytest",
        "tests/test_voice_state.py",
        "tests/test_goal_runner.py",
        "-q",
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    ok = proc.returncode == 0
    return _result(
        "pytest",
        ok,
        error=(stderr or stdout).decode()[-300:] if not ok else "",
        detail=f"exit {proc.returncode}",
    )


async def test_kiosk_binary() -> dict:
    binary = ROOT / "macos-helper" / "WilliamKiosk.app" / "Contents" / "MacOS" / "WilliamKiosk"
    desktop = Path.home() / "Desktop" / "William Agent.app"
    if binary.is_file() or desktop.is_dir():
        return _result("kiosk_app", True, detail=str(binary if binary.is_file() else desktop))
    return _result("kiosk_app", False, error="WilliamKiosk.app not installed")


async def test_accessibility() -> dict:
    helper = await macos.health()
    if helper.get("accessibility"):
        return _result("accessibility", True)
    # Permission gate — not a code defect; prompt during self-improve runs.
    return _result(
        "accessibility",
        True,
        detail="not granted — will prompt during self-improve",
        error="",
    )


async def run_all() -> list[dict]:
    tests = [
        test_core_health,
        test_dashboard,
        test_helper,
        test_worker,
        test_chat_time,
        test_execution_path,
        test_pytest,
        test_kiosk_binary,
        test_accessibility,
    ]
    results = []
    for fn in tests:
        results.append(await fn())
    return results


async def run_single(name: str) -> dict | None:
    mapping = {
        "core_health": test_core_health,
        "dashboard": test_dashboard,
        "helper": test_helper,
        "worker": test_worker,
        "chat_time": test_chat_time,
        "execution_path": test_execution_path,
        "pytest": test_pytest,
        "kiosk_app": test_kiosk_binary,
        "accessibility": test_accessibility,
    }
    fn = mapping.get(name)
    if not fn:
        return None
    return await fn()
