"""Resource governor: real-time system telemetry + keep / pause / cancel decisions for OUR work.

Reads native macOS tools (no sudo, no extra deps): `top`, `vm_stat`, `sysctl vm.swapusage`,
`pmset -g therm`, `ps`, `memory_pressure`. Publishes a live snapshot for Studio
(`GET /api/system/live`) and a decision stream.

Policy acts only on work William owns: the background worker's admission, Ollama
concurrency, and subprocesses William spawned (tracked via `register_process`). It never
kills the user's apps; for those it can only *recommend* and ask for approval.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from jarvis.config import settings
from jarvis.services import activity_stream, journal

log = logging.getLogger("jarvis.governor")

SAMPLE_INTERVAL_SECONDS = 5
HISTORY = 120  # 10 minutes at 5 s

# Thresholds (overridable at runtime through `set_policy`).
policy: dict[str, Any] = {
    "enabled": True,
    "cpu_high_percent": 90.0,        # sustained CPU (user+sys) that triggers pause
    "cpu_high_seconds": 60,
    "mem_free_low_percent": 10.0,    # free RAM % below which heavy admission pauses
    "swap_used_high_mb": 1800,       # swap in use that signals pressure
    "thermal_pause": True,           # pause heavy work when pmset reports throttling
    "ollama_single_flight": True,    # serialize local model calls on 16 GB
    "worker_min_parallel": 1,
    "cancel_our_cpu_hog_after_seconds": 300,  # our subprocess > 95% CPU for this long → cancel
}

_samples: deque[dict] = deque(maxlen=HISTORY)
_decisions: deque[dict] = deque(maxlen=200)
_task: asyncio.Task | None = None
_state: dict[str, Any] = {"mode": "normal", "worker_parallel_cap": None, "paused_since": None, "last_sample": None}
_our_processes: dict[int, dict] = {}  # pid -> {label, started, task_id}
_cpu_high_since: float | None = None
_hog_since: dict[int, float] = {}
_ollama_lock = asyncio.Semaphore(1)
_page_size = 16384
_total_mem = 0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


_BIN = {  # launchd PATH lacks /usr/sbin; resolve the native tools explicitly
    "top": "/usr/bin/top", "vm_stat": "/usr/bin/vm_stat", "sysctl": "/usr/sbin/sysctl", "pmset": "/usr/bin/pmset",
    "memory_pressure": "/usr/bin/memory_pressure", "ps": "/bin/ps", "ollama": "/opt/homebrew/bin/ollama",
}


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        exe = _BIN.get(cmd[0], cmd[0])
        if not os.path.exists(exe):
            exe = cmd[0]
        return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


# --------------------------------------------------------------------------- sampling

def _sample_sync() -> dict[str, Any]:
    global _page_size, _total_mem
    top = _run(["top", "-l", "1", "-n", "0", "-stats", "pid"])
    cpu_m = re.search(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys,\s*([\d.]+)% idle", top)
    load_m = re.search(r"Load Avg:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", top)
    proc_m = re.search(r"Processes:\s*(\d+) total", top)
    phys_m = re.search(r"PhysMem:\s*(\d+)([MG]) used \((\d+)M wired,\s*(\d+)M compressor\),\s*(\d+)([MG]) unused", top)

    vm = _run(["vm_stat"])
    ps_m = re.search(r"page size of (\d+) bytes", vm)
    if ps_m:
        _page_size = int(ps_m.group(1))
    pages = {k.strip().lower(): int(v.strip(" .")) for k, v in
             (ln.split(":", 1) for ln in vm.splitlines() if ":" in ln and ln.strip().endswith("."))}
    if not _total_mem:
        try:
            _total_mem = int(_run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
        except ValueError:
            _total_mem = 0
    free_pages = pages.get("pages free", 0) + pages.get("pages speculative", 0)
    free_mb = free_pages * _page_size / 1048576
    total_mb = _total_mem / 1048576 if _total_mem else 0
    free_pct = round(100.0 * free_mb / total_mb, 1) if total_mb else None

    swap = _run(["sysctl", "-n", "vm.swapusage"])
    swap_m = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M\s+free = ([\d.]+)M", swap)

    therm = _run(["pmset", "-g", "therm"])
    cpu_speed_m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", therm)
    sched_m = re.search(r"CPU_Scheduler_Limit\s*=\s*(\d+)", therm)
    throttled = bool((cpu_speed_m and int(cpu_speed_m.group(1)) < 100) or (sched_m and int(sched_m.group(1)) < 100))

    mp = _run(["memory_pressure"])
    mp_m = re.search(r"System-wide memory free percentage:\s*(\d+)%", mp)

    ps = _run(["ps", "-axo", "pid,ppid,%cpu,%mem,rss,etime,comm", "-r"])
    procs: list[dict] = []
    for ln in ps.splitlines()[1:21]:
        parts = ln.split(None, 6)
        if len(parts) < 7:
            continue
        try:
            procs.append({"pid": int(parts[0]), "ppid": int(parts[1]), "cpu": float(parts[2]),
                          "mem": float(parts[3]), "rss_mb": round(int(parts[4]) / 1024, 1),
                          "elapsed": parts[5], "name": os.path.basename(parts[6])[:60], "path": parts[6][:200]})
        except ValueError:
            continue

    ollama_models = _ollama_ps()

    return {
        "ts": _now(),
        "device": getattr(settings, "role", "mini"),
        "cpu": {"user": float(cpu_m.group(1)) if cpu_m else None, "sys": float(cpu_m.group(2)) if cpu_m else None,
                "idle": float(cpu_m.group(3)) if cpu_m else None,
                "busy": round(100.0 - float(cpu_m.group(3)), 1) if cpu_m else None},
        "load": [float(load_m.group(i)) for i in (1, 2, 3)] if load_m else None,
        "processes": int(proc_m.group(1)) if proc_m else None,
        "memory": {
            "total_mb": round(total_mb), "free_mb": round(free_mb), "free_percent": free_pct,
            "used_mb": _to_mb(phys_m.group(1), phys_m.group(2)) if phys_m else None,
            "wired_mb": int(phys_m.group(3)) if phys_m else None,
            "compressor_mb": int(phys_m.group(4)) if phys_m else None,
            "pressure_free_percent": int(mp_m.group(1)) if mp_m else None,
        },
        "swap": {"total_mb": float(swap_m.group(1)) if swap_m else None,
                 "used_mb": float(swap_m.group(2)) if swap_m else None},
        "thermal": {"throttled": throttled,
                    "cpu_speed_limit": int(cpu_speed_m.group(1)) if cpu_speed_m else 100,
                    "scheduler_limit": int(sched_m.group(1)) if sched_m else 100},
        "top_processes": procs,
        "ollama": ollama_models,
    }


def _to_mb(value: str, unit: str) -> int:
    return int(value) * (1024 if unit == "G" else 1)


def _ollama_ps() -> list[dict]:
    out = _run(["ollama", "ps"], timeout=3.0)
    models = []
    for ln in out.splitlines()[1:]:
        parts = ln.split()
        if len(parts) >= 3:
            models.append({"name": parts[0], "size": " ".join(parts[2:4])})
    return models


async def sample() -> dict[str, Any]:
    snap = await asyncio.to_thread(_sample_sync)
    snap["ours"] = _our_snapshot(snap)
    snap["mode"] = _state["mode"]
    snap["worker_parallel_cap"] = _state["worker_parallel_cap"]
    _samples.append(snap)
    _state["last_sample"] = snap
    return snap


def _our_snapshot(snap: dict) -> list[dict]:
    by_pid = {p["pid"]: p for p in snap.get("top_processes", [])}
    ours = []
    for pid, meta in list(_our_processes.items()):
        alive = _pid_alive(pid)
        if not alive:
            _our_processes.pop(pid, None)
            _hog_since.pop(pid, None)
            continue
        live = by_pid.get(pid, {})
        ours.append({"pid": pid, **meta, "cpu": live.get("cpu"), "rss_mb": live.get("rss_mb")})
    return ours


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- our processes

def register_process(pid: int, *, label: str, task_id: int | None = None, cancellable: bool = True) -> None:
    """Tell the governor about a subprocess William started so it may be governed."""
    _our_processes[pid] = {"label": label[:80], "task_id": task_id, "started": _now(), "cancellable": cancellable}


def unregister_process(pid: int) -> None:
    _our_processes.pop(pid, None)
    _hog_since.pop(pid, None)


async def cancel_process(pid: int, *, reason: str, by: str = "governor") -> dict:
    meta = _our_processes.get(pid)
    if not meta:
        return {"ok": False, "error": "not one of our processes"}
    if not meta.get("cancellable", True):
        return {"ok": False, "error": "marked non-cancellable"}
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    await _decide("cancel", f"terminated {meta['label']} (pid {pid}): {reason}", by=by, target=pid)
    unregister_process(pid)
    return {"ok": True, "pid": pid}


# --------------------------------------------------------------------------- policy

async def _decide(action: str, reason: str, *, by: str = "governor", target: Any = None) -> dict:
    entry = {"ts": _now(), "action": action, "reason": reason, "by": by, "target": target, "mode": _state["mode"]}
    _decisions.append(entry)
    await journal.decision(f"governor {action}: {reason}", source="governor", agent="Governor",
                           metadata={"target": target, "by": by})
    try:
        await activity_stream.broadcast({"kind": "governor", "title": f"{action}: {reason[:80]}", "detail": reason,
                                         "status": "done", "engine": by, "metadata": entry})
    except Exception:
        pass
    return entry


async def evaluate(snap: dict) -> None:
    """Apply policy to the latest sample."""
    global _cpu_high_since
    if not policy["enabled"]:
        return
    now = time.monotonic()
    cpu_busy = (snap.get("cpu") or {}).get("busy") or 0.0
    mem = snap.get("memory") or {}
    free_pct = mem.get("pressure_free_percent") if mem.get("pressure_free_percent") is not None else mem.get("free_percent")
    swap_used = (snap.get("swap") or {}).get("used_mb") or 0.0
    throttled = (snap.get("thermal") or {}).get("throttled", False)

    if cpu_busy >= policy["cpu_high_percent"]:
        _cpu_high_since = _cpu_high_since or now
    else:
        _cpu_high_since = None
    cpu_sustained = _cpu_high_since is not None and (now - _cpu_high_since) >= policy["cpu_high_seconds"]

    reasons = []
    if free_pct is not None and free_pct < policy["mem_free_low_percent"]:
        reasons.append(f"free memory {free_pct}% < {policy['mem_free_low_percent']}%")
    if swap_used >= policy["swap_used_high_mb"]:
        reasons.append(f"swap {int(swap_used)} MB in use")
    if throttled and policy["thermal_pause"]:
        reasons.append("thermal throttling")
    if cpu_sustained:
        reasons.append(f"CPU {cpu_busy}% for {policy['cpu_high_seconds']}s")

    want_mode = "constrained" if reasons else "normal"
    if want_mode != _state["mode"]:
        _state["mode"] = want_mode
        if want_mode == "constrained":
            _state["worker_parallel_cap"] = policy["worker_min_parallel"]
            _state["paused_since"] = _now()
            await _decide("pause", "limiting background work: " + "; ".join(reasons))
        else:
            _state["worker_parallel_cap"] = None
            _state["paused_since"] = None
            await _decide("resume", "resources recovered; restoring normal parallelism")

    # Our own runaway subprocesses.
    for proc in snap.get("ours", []):
        pid = proc["pid"]
        if (proc.get("cpu") or 0) >= 95.0:
            _hog_since.setdefault(pid, now)
            if now - _hog_since[pid] >= policy["cancel_our_cpu_hog_after_seconds"]:
                await cancel_process(pid, reason=f"{proc.get('cpu')}% CPU for {policy['cancel_our_cpu_hog_after_seconds']}s")
        else:
            _hog_since.pop(pid, None)


def worker_parallel_cap() -> int | None:
    """Hook for worker.py: None = no cap, else max concurrent tasks right now."""
    return _state["worker_parallel_cap"]


def ollama_gate():
    """Async context manager: serialize local model calls when single_flight is on."""
    if policy["ollama_single_flight"]:
        return _ollama_lock
    return _NullGate()


class _NullGate:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


# --------------------------------------------------------------------------- public views

def snapshot_compact() -> dict[str, Any]:
    snap = _state.get("last_sample") or {}
    cpu = snap.get("cpu") or {}
    mem = snap.get("memory") or {}
    return {"mode": _state["mode"], "cpu_busy": cpu.get("busy"), "load": snap.get("load"),
            "free_mb": mem.get("free_mb"), "free_percent": mem.get("pressure_free_percent", mem.get("free_percent")),
            "swap_used_mb": (snap.get("swap") or {}).get("used_mb"),
            "throttled": (snap.get("thermal") or {}).get("throttled"), "ollama": snap.get("ollama"), "ts": snap.get("ts")}


def live() -> dict[str, Any]:
    return {
        "device": getattr(settings, "role", "mini"),
        "mode": _state["mode"],
        "paused_since": _state["paused_since"],
        "worker_parallel_cap": _state["worker_parallel_cap"],
        "policy": policy,
        "sample": _state.get("last_sample"),
        "history": [
            {"ts": s["ts"], "cpu_busy": (s.get("cpu") or {}).get("busy"),
             "free_percent": (s.get("memory") or {}).get("pressure_free_percent", (s.get("memory") or {}).get("free_percent")),
             "swap_used_mb": (s.get("swap") or {}).get("used_mb"), "load1": (s.get("load") or [None])[0]}
            for s in _samples
        ],
        "decisions": list(_decisions)[-50:],
        "ours": list(_our_processes.items()),
    }


def set_policy(updates: dict) -> dict:
    for key, value in updates.items():
        if key in policy and value is not None:
            policy[key] = type(policy[key])(value) if not isinstance(policy[key], bool) else bool(value)
    return policy


async def override(action: str, *, target: int | None = None, by: str = "user") -> dict:
    """Studio buttons: keep | pause | resume | cancel."""
    if action == "cancel" and target:
        return await cancel_process(target, reason="requested from Studio", by=by)
    if action == "pause":
        _state["mode"] = "constrained"
        _state["worker_parallel_cap"] = policy["worker_min_parallel"]
        _state["paused_since"] = _now()
        return await _decide("pause", "manual pause", by=by)
    if action in ("resume", "keep"):
        _state["mode"] = "normal"
        _state["worker_parallel_cap"] = None
        _state["paused_since"] = None
        return await _decide("resume", f"manual {action}", by=by)
    return {"ok": False, "error": f"unknown action {action}"}


# --------------------------------------------------------------------------- loop

async def tick() -> dict:
    snap = await sample()
    try:
        await evaluate(snap)
    except Exception as exc:  # pragma: no cover
        log.warning("governor evaluate failed: %s", exc)
    try:
        await activity_stream.broadcast({"kind": "system", "title": "system sample", "detail": "",
                                         "status": "done", "engine": "governor",
                                         "metadata": {"system": snapshot_compact()}})
    except Exception:
        pass
    return snap


async def _loop() -> None:
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            log.warning("governor tick failed: %s", exc)
        await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)


def start() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
