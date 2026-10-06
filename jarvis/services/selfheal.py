"""Self-heal: diagnostics → known fixes → sandbox-branch escalation. One button.

Checks are cheap and local. Fixes are limited to William's own services, models, logs and
provider state. Anything unknown is written up and handed to `self_modify.propose` so a
sandbox branch (and approval) exists for the follow-up.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx

from jarvis.config import settings
from jarvis.paths import ROOT
from jarvis.services import journal, spans

log = logging.getLogger("jarvis.selfheal")

SERVICES = {
    "com.willy.jarvis-core": {"port": 8787, "health": "http://127.0.0.1:8787/api/health", "mini_only": False},
    "com.willy.jarvis-helper": {"port": 8788, "health": "http://127.0.0.1:8788/status", "mini_only": True},
    "homebrew.mxcl.ollama": {"port": 11434, "health": "http://127.0.0.1:11434/api/version", "mini_only": False},
}
LOG_ROTATE_MB = 200
REQUIRED_MODELS = ("llama3.2:3b", "nomic-embed-text")

_last_run: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()


def _launchctl_list() -> dict[str, dict]:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15).stdout
    rows = {}
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3:
            pid, code, label = parts
            rows[label] = {"pid": None if pid == "-" else int(pid), "last_exit": int(code or 0)}
    return rows


async def _http_ok(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url)
            return resp.status_code < 500
    except Exception:
        return False


async def diagnose() -> dict:
    """Collect findings without changing anything."""
    findings: list[dict] = []
    services = await asyncio.to_thread(_launchctl_list)
    role = getattr(settings, "role", "mini")
    for label, spec in SERVICES.items():
        if spec["mini_only"] and role != "mini":
            continue
        info = services.get(label)
        healthy = await _http_ok(spec["health"])
        if info is None and label.startswith("com.willy."):
            findings.append({"kind": "service_missing", "target": label, "severity": 3,
                             "summary": f"{label} is not registered with launchd"})
        elif not healthy:
            findings.append({"kind": "service_unhealthy", "target": label, "severity": 4,
                             "summary": f"{label} not answering on :{spec['port']}", "detail": str(info)})

    # Ollama models
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            tags = (await client.get(f"{settings.ollama_base_url}/api/tags")).json()
        have = {m.get("name", "").split(":")[0] + ":" + m.get("name", "").split(":")[1] if ":" in m.get("name", "")
                else m.get("name", "") for m in tags.get("models", [])}
        for model in REQUIRED_MODELS:
            base = model.split(":")[0]
            if not any(h.startswith(base) for h in have):
                findings.append({"kind": "model_missing", "target": model, "severity": 2,
                                 "summary": f"Ollama model {model} is not pulled"})
    except Exception as exc:
        findings.append({"kind": "ollama_unreachable", "target": "ollama", "severity": 4,
                         "summary": f"Ollama not reachable: {str(exc)[:100]}"})

    # Oversized logs
    for folder in {ROOT / "logs", Path.home() / "Library" / "Logs" / "Jarvis"}:
        if not folder.is_dir():
            continue
        for path in folder.glob("*.log"):
            try:
                mb = path.stat().st_size / 1048576
            except OSError:
                continue
            if mb > LOG_ROTATE_MB:
                findings.append({"kind": "log_oversized", "target": str(path), "severity": 1,
                                 "summary": f"{path.name} is {mb:.0f} MB"})

    # Provider cooldowns that are stale
    try:
        from jarvis.services.providers import usage

        summary = await usage.summary(days=1)
        for pid, st in summary["state"].items():
            if st.get("cooling") and (st.get("consecutive_errors") or 0) < 3:
                findings.append({"kind": "provider_cooldown", "target": pid, "severity": 1,
                                 "summary": f"{pid} cooling after few errors: {st.get('last_error', '')[:80]}"})
    except Exception:
        pass

    # Open journal problems
    problems = await journal.open_problems(limit=20)
    for p in problems:
        findings.append({"kind": "journal_problem", "target": p["id"], "severity": p.get("severity", 3),
                         "summary": p["summary"], "agent": p.get("agent")})

    # Disk
    try:
        st = os.statvfs(str(ROOT))
        free_gb = st.f_bavail * st.f_frsize / 1e9
        if free_gb < 10:
            findings.append({"kind": "disk_low", "target": str(ROOT), "severity": 3,
                             "summary": f"only {free_gb:.1f} GB free"})
    except OSError:
        pass

    return {"ts": _now(), "device": role, "findings": findings, "count": len(findings)}


async def _fix(finding: dict) -> dict:
    kind = finding["kind"]
    target = finding["target"]
    try:
        if kind in ("service_unhealthy", "service_missing") and isinstance(target, str):
            if kind == "service_missing":
                plist = _find_plist(target)
                if not plist:
                    return {"ok": False, "fix": "none", "why": "plist not found"}
                subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)], capture_output=True, timeout=20)
            proc = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{_uid()}/{target}"], capture_output=True,
                                  text=True, timeout=20)
            await asyncio.sleep(3)
            spec = SERVICES.get(target, {})
            healthy = await _http_ok(spec.get("health", "")) if spec.get("health") else proc.returncode == 0
            return {"ok": healthy, "fix": "kickstart", "detail": proc.stderr.strip()[:200]}
        if kind == "model_missing":
            proc = await asyncio.create_subprocess_exec("ollama", "pull", str(target), stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), timeout=900)
            return {"ok": proc.returncode == 0, "fix": "ollama pull", "detail": (err or b"").decode()[-200:]}
        if kind == "log_oversized":
            path = Path(str(target))
            rotated = path.with_suffix(path.suffix + ".1")
            if rotated.exists():
                rotated.unlink()
            path.rename(rotated)
            path.touch()
            return {"ok": True, "fix": "rotate"}
        if kind == "provider_cooldown":
            from jarvis.services.providers import usage

            await usage.clear_cooldown(str(target))
            return {"ok": True, "fix": "clear cooldown"}
        if kind == "ollama_unreachable":
            proc = subprocess.run(["brew", "services", "restart", "ollama"], capture_output=True, text=True, timeout=60)
            await asyncio.sleep(4)
            return {"ok": await _http_ok(f"{settings.ollama_base_url}/api/version"), "fix": "brew services restart",
                    "detail": proc.stderr.strip()[:200]}
        if kind == "journal_problem":
            return {"ok": False, "fix": "escalate"}
        return {"ok": False, "fix": "none", "why": "no known fix"}
    except Exception as exc:
        return {"ok": False, "fix": "error", "detail": str(exc)[:200]}


def _find_plist(label: str) -> Path | None:
    for folder in (ROOT / "launchd", ROOT / "macos-helper" / "launchd", Path.home() / "Library" / "LaunchAgents"):
        candidate = folder / f"{label}.plist"
        if candidate.is_file():
            return candidate
    return None


async def run(*, apply: bool = True, run_tests: bool = False, by: str = "user") -> dict:
    """Diagnose, fix what we know, escalate what we don't. Returns a report."""
    global _last_run
    async with spans.span("agent", "self-healer", agent="Self Healer", input_text=f"selfheal apply={apply}") as sp:
        report = await diagnose()
        fixes: list[dict] = []
        escalations: list[dict] = []
        if apply:
            for finding in report["findings"]:
                result = await _fix(finding)
                entry = {**finding, "result": result}
                if result.get("ok"):
                    fixes.append(entry)
                    await journal.fix(f"self-heal: {finding['summary']} → {result.get('fix')}", source="selfheal",
                                      agent="Self Healer", metadata={"finding": finding, "by": by})
                    if finding["kind"] == "journal_problem":
                        await journal.resolve(int(finding["target"]), by="self-heal")
                else:
                    escalations.append(entry)
            # Resolve journal problems that we could not fix only when a sandbox branch was opened.
            if escalations:
                summary = "; ".join(e["summary"][:80] for e in escalations[:6])
                try:
                    from jarvis.services import self_modify

                    proposal = await self_modify.propose(
                        "Self-heal escalation: " + summary + ". Investigate root cause, add a fix or a rules agent, "
                        "and add a regression test."
                    )
                    report["sandbox"] = {k: proposal.get(k) for k in ("ok", "branch", "approval_id", "error")}
                except Exception as exc:
                    report["sandbox"] = {"ok": False, "error": str(exc)[:200]}
                await journal.problem(f"self-heal could not fix {len(escalations)} finding(s): {summary[:200]}",
                                      source="selfheal", agent="Self Healer", severity=3,
                                      metadata={"sandbox": report.get("sandbox"), "by": by})
        if run_tests:
            from jarvis.services import self_modify

            report["tests"] = await self_modify.run_tests()
        report.update({"applied": apply, "fixed": fixes, "escalated": escalations, "by": by})
        sp.metadata.update({"fixed": len(fixes), "escalated": len(escalations)})
        spans.set_output(f"{len(fixes)} fixed, {len(escalations)} escalated")
        _last_run = report
        if fixes or escalations:
            try:
                from jarvis.services import macos

                await macos.notify("William self-heal", f"{len(fixes)} fixed, {len(escalations)} need you.")
            except Exception:
                pass
        return report


def last_report() -> dict | None:
    return _last_run


async def rules_entry(task: str, ctx: dict) -> dict:
    """Rules-agent entrypoint for the Self Healer."""
    apply = not any(w in task.lower() for w in ("diagnose only", "dry run", "just check"))
    report = await run(apply=apply, by="agent")
    lines = [f"Self-heal: {report['count']} finding(s), {len(report['fixed'])} fixed, "
             f"{len(report['escalated'])} escalated."]
    for f in report["fixed"]:
        lines.append(f"  ✓ {f['summary']} → {f['result'].get('fix')}")
    for e in report["escalated"]:
        lines.append(f"  ! {e['summary']}")
    if report.get("sandbox", {}).get("branch"):
        lines.append(f"  sandbox branch: {report['sandbox']['branch']} (approval #{report['sandbox'].get('approval_id')})")
    return {"ok": True, "reply": "\n".join(lines), "data": report}
