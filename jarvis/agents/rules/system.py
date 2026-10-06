"""Rules agents for system telemetry, services and the governor (0 tokens)."""

from __future__ import annotations

import re
import subprocess

from jarvis.services import resource_governor

_SERVICE_LABELS = {
    "core": "com.willy.jarvis-core",
    "jarvis": "com.willy.jarvis-core",
    "helper": "com.willy.jarvis-helper",
    "voice": "com.willy.jarvis-helper",
    "kiosk": "com.willy.william-kiosk",
    "desktop": "com.willy.william-desktop",
    "map": "com.willy.william-system-map",
    "ollama": "homebrew.mxcl.ollama",
    "openclaw": "ai.openclaw.gateway",
}


def _uid() -> str:
    return subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=15)


async def live(task: str, ctx: dict) -> dict:
    """Describe the machine right now from the governor's latest sample."""
    snap = resource_governor._state.get("last_sample") or await resource_governor.sample()
    cpu = snap.get("cpu") or {}
    mem = snap.get("memory") or {}
    swap = snap.get("swap") or {}
    therm = snap.get("thermal") or {}
    top = snap.get("top_processes") or []
    hogs = ", ".join(f"{p['name']} {p['cpu']:.0f}%" for p in top[:4]) or "none"
    free_pct = mem.get("pressure_free_percent", mem.get("free_percent"))
    voice = ctx.get("voice")
    if voice:
        reply = (f"CPU {cpu.get('busy', 0):.0f} percent busy, {free_pct} percent memory free, "
                 f"{'throttling' if therm.get('throttled') else 'cool'}. Mode {snap.get('mode', 'normal')}.")
    else:
        reply = (
            f"Mode: {snap.get('mode', 'normal')}\n"
            f"CPU busy: {cpu.get('busy')}% (load {snap.get('load')})\n"
            f"Memory free: {mem.get('free_mb')} MB ({free_pct}%), compressor {mem.get('compressor_mb')} MB, "
            f"swap used {swap.get('used_mb')} MB\n"
            f"Thermal: {'THROTTLED' if therm.get('throttled') else 'normal'}\n"
            f"Loaded Ollama models: {', '.join(m['name'] for m in snap.get('ollama') or []) or 'none'}\n"
            f"Top CPU: {hogs}"
        )
    return {"ok": True, "reply": reply, "data": resource_governor.snapshot_compact()}


async def services(task: str, ctx: dict) -> dict:
    """List or restart William's launchd services. 'restart core' / 'status of services'."""
    lowered = task.lower()
    uid = _uid()
    action = "restart" if re.search(r"\b(restart|kick|bounce)\b", lowered) else (
        "stop" if re.search(r"\b(stop|bootout)\b", lowered) else "status")
    targets = [label for key, label in _SERVICE_LABELS.items() if re.search(rf"\b{key}\b", lowered)]
    if action != "status" and not targets:
        return {"ok": False, "reply": "Which service? core, helper, kiosk, desktop, map, ollama or openclaw.",
                "error": "no service named"}
    if action == "restart":
        done = []
        for label in dict.fromkeys(targets):
            proc = _launchctl("kickstart", "-k", f"gui/{uid}/{label}")
            done.append(f"{label}: {'restarted' if proc.returncode == 0 else proc.stderr.strip() or 'failed'}")
        return {"ok": True, "reply": "\n".join(done), "data": {"restarted": targets}}
    if action == "stop":
        done = []
        for label in dict.fromkeys(targets):
            proc = _launchctl("bootout", f"gui/{uid}/{label}")
            done.append(f"{label}: {'stopped' if proc.returncode == 0 else proc.stderr.strip() or 'failed'}")
        return {"ok": True, "reply": "\n".join(done), "data": {"stopped": targets}}
    listing = _launchctl("list").stdout
    rows = []
    for ln in listing.splitlines():
        parts = ln.split("\t")
        if len(parts) == 3 and any(parts[2].startswith(p) for p in ("com.willy.", "ai.openclaw", "homebrew.mxcl")):
            pid, code, label = parts
            rows.append({"label": label, "pid": None if pid == "-" else int(pid), "last_exit": int(code or 0)})
    text = "\n".join(f"- {r['label']}: {'running pid ' + str(r['pid']) if r['pid'] else 'stopped'}"
                     f"{'' if r['last_exit'] == 0 else f' (last exit {r['last_exit']})'}" for r in rows)
    return {"ok": True, "reply": text or "No William services registered with launchd.", "data": rows}


async def governor(task: str, ctx: dict) -> dict:
    """Explain or steer the governor: 'pause background work', 'resume', 'governor status'."""
    lowered = task.lower()
    if re.search(r"\b(pause|hold|throttle)\b", lowered):
        entry = await resource_governor.override("pause", by="rules")
        return {"ok": True, "reply": "Paused heavy background work.", "data": entry}
    if re.search(r"\b(resume|continue|release|keep going)\b", lowered):
        entry = await resource_governor.override("resume", by="rules")
        return {"ok": True, "reply": "Resumed normal parallelism.", "data": entry}
    state = resource_governor.live()
    decisions = state["decisions"][-5:]
    lines = [f"Mode {state['mode']}, worker cap {state['worker_parallel_cap'] or 'none'}."]
    lines += [f"- {d['ts'][11:19]} {d['action']}: {d['reason']}" for d in decisions] or ["- no decisions yet"]
    return {"ok": True, "reply": "\n".join(lines), "data": {"mode": state["mode"], "decisions": decisions}}
