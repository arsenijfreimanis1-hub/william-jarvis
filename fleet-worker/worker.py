"""Thin LAN fleet peer worker — registers with Jarvis Core and runs allowlisted shell jobs."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = os.environ.get("JARVIS_CORE_URL", "http://127.0.0.1:8787").rstrip("/")
DEFAULT_TOKEN = os.environ.get("JARVIS_FLEET_TOKEN", "")
DEFAULT_NAME = os.environ.get("JARVIS_FLEET_NODE_NAME", platform.node() or "fleet-worker")
DEFAULT_ROLE = os.environ.get("JARVIS_FLEET_NODE_ROLE", "general")
DEFAULT_CAPS = os.environ.get("JARVIS_FLEET_CAPABILITIES", "shell")
DEFAULT_WORKSPACE = os.environ.get("JARVIS_FLEET_WORKSPACE", str(Path.cwd()))
ALLOWED_PREFIXES = ("echo ", "pwd", "uname", "dir", "whoami", "hostname", "python ", "python3 ", "pytest ")


def _request(method: str, path: str, body: dict | None = None, token: str = "") -> dict:
    url = f"{DEFAULT_URL}{path}"
    data = None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-Jarvis-Fleet-Token"] = token
    if body is not None:
        payload = dict(body)
        if token and "token" not in payload:
            payload["token"] = token
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc


def command_allowed(command: str) -> bool:
    cmd = (command or "").strip()
    if not cmd:
        return False
    lowered = cmd.lower()
    return any(lowered == p.strip() or lowered.startswith(p) for p in ALLOWED_PREFIXES)


def run_command(command: str, workspace: str) -> tuple[bool, str, str]:
    if not command_allowed(command):
        return False, "", f"command not allowlisted: {command}"
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        out = (completed.stdout or "")[-8000:]
        err = (completed.stderr or "")[-4000:]
        return completed.returncode == 0, out, err
    except Exception as exc:
        return False, "", str(exc)


def parse_caps(raw: str) -> list[str]:
    return [c.strip() for c in raw.split(",") if c.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis LAN fleet worker")
    parser.add_argument("--url", default=os.environ.get("JARVIS_CORE_URL", "http://127.0.0.1:8787").rstrip("/"))
    parser.add_argument("--token", default=os.environ.get("JARVIS_FLEET_TOKEN", ""))
    parser.add_argument("--name", default=os.environ.get("JARVIS_FLEET_NODE_NAME", platform.node() or "fleet-worker"))
    parser.add_argument("--role", default=os.environ.get("JARVIS_FLEET_NODE_ROLE", "general"), choices=["control", "planner", "tester", "general"])
    parser.add_argument("--capabilities", default=os.environ.get("JARVIS_FLEET_CAPABILITIES", "shell"))
    parser.add_argument("--workspace", default=os.environ.get("JARVIS_FLEET_WORKSPACE", str(Path.cwd())))
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="Register, one claim attempt, exit")
    parser.add_argument(
        "--tags",
        default="shell,general,plan,test,gpu,integrate",
        help="Comma-separated job tags this worker will claim",
    )
    args = parser.parse_args(argv)

    base_url = args.url.rstrip("/")

    def request(method: str, path: str, body: dict | None = None) -> dict:
        url = f"{base_url}{path}"
        data = None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = args.token
        if token:
            headers["X-Jarvis-Fleet-Token"] = token
        if body is not None:
            payload = dict(body)
            if token and "token" not in payload:
                payload["token"] = token
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc

    caps = parse_caps(args.capabilities)
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    os_name = platform.system().lower()

    print(f"registering {args.name} ({args.role}) → {base_url}")
    request(
        "POST",
        "/api/fleet/register",
        {
            "name": args.name,
            "role": args.role,
            "os": os_name,
            "capabilities": caps,
            "lan_host": None,
        },
    )

    while True:
        request(
            "POST",
            "/api/fleet/heartbeat",
            {"name": args.name, "status": "online"},
        )
        claimed = request(
            "POST",
            "/api/fleet/claim",
            {"name": args.name, "tags": tags, "lease_seconds": 180},
        )
        job = claimed.get("job")
        if job:
            command = job.get("command") or ""
            print(f"claimed job #{job['id']}: {job.get('title')} → {command!r}")
            ok, output, error = run_command(command, args.workspace) if command else (True, "(no command)", "")
            request(
                "POST",
                "/api/fleet/result",
                {
                    "name": args.name,
                    "job_id": job["id"],
                    "ok": ok,
                    "output": output,
                    "error": error,
                },
            )
            print(f"reported job #{job['id']} ok={ok}")
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        raise SystemExit(0)
