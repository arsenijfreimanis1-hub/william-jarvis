import asyncio
import os
import re
import shlex
from pathlib import Path

from jarvis.config import settings

UID = os.getuid()
WORKSPACE = settings.workspace_dir
LOG_DIR = WORKSPACE / "logs"
PLIST_DIR = Path.home() / "Library/LaunchAgents"

BLOCKED = (
    re.compile(r"rm\s+-rf\s+[/~]"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r":\(\)\{"),
    re.compile(r">\s*/dev/disk"),
    re.compile(r"\bsudo\s+rm\b"),
)

RUN_RE = re.compile(
    r"^\s*(?:run|execute|terminal|shell)\s+(.+)$",
    re.I,
)
BACKTICK_RE = re.compile(r"`([^`]+)`")
SHELL_PREFIX_RE = re.compile(
    r"^\s*(launchctl|brew|git|ollama|pip3?|npm|python3?|cd |tail |cat |curl |"
    r"\./scripts/|scripts/|uvicorn )",
    re.I,
)


def _restart_service(label: str) -> str:
    plist = PLIST_DIR / f"{label}.plist"
    plist_q = shlex.quote(str(plist))
    domain = f"gui/{UID}"
    return (
        f"launchctl kickstart -k {domain}/{label} 2>/dev/null || "
        f"(launchctl bootstrap {domain} {plist_q} && "
        f"launchctl enable {domain}/{label} && "
        f"launchctl kickstart {domain}/{label})"
    )


def _restart_services(*labels: str) -> str:
    return "; ".join(_restart_service(label) for label in labels)


MAINTENANCE_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brestart\s+(?:jarvis|william(?:\s+agent)?|the\s+agent)\b", re.I),
     _restart_service("com.willy.jarvis-core")),
    (re.compile(r"\brestart\s+(?:helper|macos\s+helper)\b", re.I),
     _restart_service("com.willy.jarvis-helper")),
    (re.compile(r"\brestart\s+(?:both|everything|services)\b", re.I),
     _restart_services("com.willy.jarvis-helper", "com.willy.jarvis-core")),
    (re.compile(r"\bfix\s+services?\b", re.I),
     f"cd {shlex.quote(str(WORKSPACE))} && ./scripts/install-helper.sh"),
    (re.compile(r"\b(?:check|show)\s+jarvis\s+(?:health|status)\b", re.I),
     "curl -s http://127.0.0.1:8787/api/health"),
    (re.compile(r"\b(?:check|show)\s+helper\s+(?:health|status)\b", re.I),
     "curl -s http://127.0.0.1:8788/status"),
    (re.compile(r"\b(?:check|show)\s+ollama\b", re.I),
     "ollama list"),
    (re.compile(r"\b(?:show|tail)\s+jarvis\s+logs?\b", re.I),
     f"tail -n 40 {LOG_DIR / 'jarvis.log'}"),
    (re.compile(r"\b(?:show|tail)\s+helper\s+logs?\b", re.I),
     f"tail -n 40 {LOG_DIR / 'helper.err.log'}"),
    (re.compile(r"\binstall\s+helper\b", re.I),
     f"cd {shlex.quote(str(WORKSPACE))} && ./scripts/install-helper.sh"),
    (re.compile(r"\bgrant\s+voice\s+permissions?\b", re.I),
     f"cd {shlex.quote(str(WORKSPACE))} && ./scripts/grant-voice-permissions.sh"),
    (re.compile(r"\benable\s+full\s+access\b", re.I),
     "curl -s -X POST http://127.0.0.1:8787/api/security/full-access "
     "-H 'Content-Type: application/json' -d '{\"enabled\":true}'"),
]

SAFE_PREFIXES = (
    "launchctl kickstart",
    "launchctl bootout",
    "launchctl bootstrap",
    "launchctl enable",
    "launchctl list",
    "curl -s http://127.0.0.1",
    "tail ",
    "cat ",
    "ollama list",
    "ollama ps",
    "brew list",
    "brew info",
    "git status",
    "git log",
    "git diff",
)


def _blocked(command: str) -> str | None:
    for pattern in BLOCKED:
        if pattern.search(command):
            return "That command is blocked, boss."
    return None


def _allowed_without_full_access(command: str) -> bool:
    if "com.willy" in command and command.strip().startswith("launchctl"):
        return True
    if str(WORKSPACE) in command or str(LOG_DIR) in command:
        if command.strip().startswith(("tail ", "cat ", "cd ")):
            return True
    if "./scripts/" in command or "scripts/" in command:
        return True
    return any(command.strip().startswith(prefix) for prefix in SAFE_PREFIXES)


def _is_deferred_restart(command: str) -> bool:
    return "kickstart" in command and "com.willy" in command


def resolve_command(text: str) -> str | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    for pattern, command in MAINTENANCE_PHRASES:
        if pattern.search(cleaned):
            return command

    m = RUN_RE.match(cleaned)
    if m:
        return m.group(1).strip()

    m = BACKTICK_RE.search(cleaned)
    if m and re.search(r"\b(run|execute|terminal|shell)\b", cleaned, re.I):
        return m.group(1).strip()

    if SHELL_PREFIX_RE.match(cleaned):
        return cleaned

    if re.search(r"\b(launchctl|brew\s|git\s|ollama\s|pip\s|npm\s|python\s|uvicorn\s)\b", cleaned, re.I):
        if re.search(r"\b(run|execute|do|use)\b", cleaned, re.I):
            for token in ("launchctl", "brew", "git", "ollama", "pip", "npm", "python", "uvicorn"):
                idx = cleaned.lower().find(token)
                if idx >= 0:
                    return cleaned[idx:].strip(" .")

    return None


def validate_command(command: str, *, full_access: bool) -> tuple[bool, str]:
    reason = _blocked(command)
    if reason:
        return False, reason
    if full_access:
        return True, ""
    if _allowed_without_full_access(command):
        return True, ""
    return False, "Enable Full access in the panel for that command, boss."


async def _run_deferred(command: str) -> None:
    wrapped = f"sleep 2; {command}"
    await asyncio.create_subprocess_shell(
        f"nohup sh -c {shlex.quote(wrapped)} >/dev/null 2>&1 &",
        cwd=WORKSPACE,
    )


async def run_command(
    command: str,
    *,
    full_access: bool = False,
    timeout: int = 120,
    cwd: Path | None = None,
) -> dict:
    allowed, reason = validate_command(command, full_access=full_access)
    if not allowed:
        return {"ok": False, "error": reason, "command": command}

    if _is_deferred_restart(command):
        await _run_deferred(command)
        return {
            "ok": True,
            "command": command,
            "stdout": "Service restart scheduled.",
            "deferred": True,
            "returncode": 0,
        }

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or WORKSPACE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {"ok": False, "error": f"Timed out after {timeout}s", "command": command}

    stdout = stdout_b.decode(errors="replace").strip()
    stderr = stderr_b.decode(errors="replace").strip()
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "returncode": proc.returncode,
    }


def _reply(result: dict, *, voice: bool) -> str:
    if not result.get("ok"):
        err = result.get("error") or result.get("stderr") or result.get("stdout") or "failed"
        first = str(err).strip().splitlines()[0][:120]
        return f"Failed, boss. {first}" if voice else f"Command failed: {first}"

    if result.get("deferred"):
        return "Restarting service, boss." if voice else "Service restart scheduled."

    stdout = (result.get("stdout") or "").strip()
    if voice:
        if stdout and len(stdout) < 80 and "\n" not in stdout:
            return f"Done, boss. {stdout}"
        return "Done, boss."
    if stdout:
        return stdout if len(stdout) < 2000 else stdout[:2000] + "\n…"
    return "Command completed successfully."


async def execute(text: str, *, full_access: bool = False, voice: bool = False) -> dict:
    command = resolve_command(text)
    if not command:
        return {"ok": False, "error": "not a terminal command"}

    result = await run_command(command, full_access=full_access)
    result["reply"] = _reply(result, voice=voice)
    return result


async def execute_parallel(
    texts: list[str],
    *,
    full_access: bool = False,
    voice: bool = False,
    max_parallel: int | None = None,
) -> list[dict]:
    """Run multiple shell commands concurrently (capped for safety)."""
    from jarvis.services import activity_stream, compute_fleet

    if not texts:
        return []

    cap = max_parallel or compute_fleet.terminal_parallel()
    sem = asyncio.Semaphore(cap)

    async def _one(text: str) -> dict:
        async with sem:
            return await execute(text, full_access=full_access, voice=voice)

    if len(texts) > 1:
        await activity_stream.emit(
            "task",
            f"Running {len(texts)} terminal jobs",
            detail=f"Up to {cap} parallel shells",
            status="running",
            engine="terminal",
        )

    results = await asyncio.gather(*[_one(t) for t in texts], return_exceptions=True)
    out: list[dict] = []
    for item in results:
        if isinstance(item, Exception):
            out.append({"ok": False, "error": str(item), "reply": str(item)})
        else:
            out.append(item)

    if len(texts) > 1:
        ok = sum(1 for r in out if r.get("ok"))
        await activity_stream.emit(
            "task",
            f"Terminal batch done ({ok}/{len(texts)})",
            status="done" if ok == len(texts) else "error",
            engine="terminal",
        )
    return out
