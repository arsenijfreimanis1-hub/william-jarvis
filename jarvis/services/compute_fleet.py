"""Execution runtime selection for local workers or cloud agents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from jarvis.config import settings
from jarvis.services import cursor_agent, github_sync

log = logging.getLogger("jarvis.compute_fleet")

RuntimeKind = Literal["local", "cloud", "fleet", "auto"]


def local_worker_count() -> int:
    return max(3, int(getattr(settings, "build_parallel", 3)))


def worker_parallel() -> int:
    """Background task + terminal worker slots."""
    return max(3, int(getattr(settings, "worker_parallel", 6)))


def terminal_parallel() -> int:
    return max(2, int(getattr(settings, "terminal_parallel", 4)))


def cloud_worker_count() -> int:
    return max(3, int(getattr(settings, "compute_cloud_workers", 3)))


def resolve_runtime(explicit: str | None = None) -> str:
    """Pick execution runtime for build slices."""
    raw = (explicit or getattr(settings, "cursor_runtime", "local") or "local").lower()
    if raw == "auto":
        return "local"
    return raw


def effective_parallel(runtime: str | None = None) -> int:
    rt = resolve_runtime(runtime)
    if rt == "local":
        return local_worker_count()
    if rt == "cloud":
        return cloud_worker_count()
    if rt == "fleet":
        return max(local_worker_count(), cloud_worker_count())
    return local_worker_count()


async def dispatch_slice(
    prompt: str,
    *,
    build_id: int,
    slice_id: str,
    cwd: str | None = None,
    repo_url: str | None = None,
    branch: str | None = None,
    runtime: str | None = None,
    source: str = "build_fleet",
    timeout_sec: int = 300,
) -> dict[str, Any]:
    """Run one slice on local disk or a Cursor Cloud VM."""
    rt = resolve_runtime(runtime)

    if rt in ("cloud", "fleet") and repo_url:
        try:
            result = await asyncio.wait_for(
                cursor_agent.run(
                    prompt,
                    runtime="cloud",
                    repo_url=repo_url,
                    branch=branch or f"slice/{slice_id}",
                    handle_popups=False,
                    source=source,
                ),
                timeout=timeout_sec,
            )
            result["runtime"] = "cloud"
            result["slice_id"] = slice_id
            return result
        except asyncio.TimeoutError:
            return {"ok": False, "error": "cloud slice timed out", "runtime": "cloud", "slice_id": slice_id}
        except Exception as exc:
            log.warning("cloud dispatch failed for %s, falling back to local: %s", slice_id, exc)
            if rt == "cloud":
                return {"ok": False, "error": str(exc), "runtime": "cloud", "slice_id": slice_id}

    if not cwd:
        return {"ok": False, "error": "local runtime requires cwd", "slice_id": slice_id}

    try:
        result = await asyncio.wait_for(
            cursor_agent.run(
                prompt,
                cwd=cwd,
                handle_popups=False,
                source=source,
            ),
            timeout=timeout_sec,
        )
        result["runtime"] = "local"
        result["slice_id"] = slice_id
        return result
    except asyncio.TimeoutError:
        return {"ok": False, "error": "local slice timed out", "runtime": "local", "slice_id": slice_id}


async def ensure_seed_nodes() -> list[dict[str, Any]]:
    """Upsert the three configured LAN machines (Mini always present)."""
    from jarvis.services import fleet_registry
    from jarvis.services.fleet_types import FleetNodeSpec

    seeds = [
        FleetNodeSpec(
            name=settings.fleet_mini_name or "Mac Mini",
            role="control",
            os="macos",
            capabilities=["control", "shell", "ollama", "cursor", "planner"],
            lan_host=settings.fleet_mini_lan_host or "127.0.0.1",
        ),
        FleetNodeSpec(
            name=settings.fleet_macbook_name or "MacBook",
            role="planner",
            os="macos",
            capabilities=["planner", "shell", "ollama", "cursor"],
            lan_host=settings.fleet_macbook_lan_host or None,
        ),
        FleetNodeSpec(
            name=settings.fleet_pc_name or "Windows PC",
            role="tester",
            os="windows",
            capabilities=["tester", "shell", "gpu", "dual_monitor"],
            lan_host=settings.fleet_pc_lan_host or None,
            mac_address=settings.fleet_pc_mac or None,
        ),
    ]
    out: list[dict[str, Any]] = []
    for spec in seeds:
        # Mini is local control plane — mark online without a peer worker.
        status = "online" if spec.role == "control" else "configured"
        if status == "online":
            from jarvis.services.fleet_types import FleetRegisterRequest

            node = await fleet_registry.register_node(
                FleetRegisterRequest(
                    name=spec.name,
                    role=spec.role,
                    os=spec.os,
                    capabilities=spec.capabilities,
                    lan_host=spec.lan_host,
                    mac_address=spec.mac_address,
                )
            )
        else:
            node = await fleet_registry.upsert_configured_node(spec, status=status)
        out.append(node.model_dump())
    return out


async def fleet_status() -> dict[str, Any]:
    from jarvis.services import fleet_auth, fleet_registry, local_runtime

    await ensure_seed_nodes()
    lan = await fleet_registry.fleet_snapshot(
        heartbeat_timeout_seconds=int(settings.fleet_heartbeat_timeout_seconds)
    )
    return {
        "runtime": resolve_runtime(),
        "local_workers": local_worker_count(),
        "worker_parallel": worker_parallel(),
        "terminal_parallel": terminal_parallel(),
        "cloud_workers": cloud_worker_count(),
        "parallel": effective_parallel(),
        "execution": local_runtime.execution_profile(),
        "github_configured": github_sync.configured(),
        "hub_repo": github_sync.hub_repo_url() if github_sync.configured() else None,
        "lan": {
            **lan,
            "token_configured": fleet_auth.fleet_token_configured(),
            "heartbeat_timeout_seconds": int(settings.fleet_heartbeat_timeout_seconds),
            "bind_host": settings.host,
        },
    }
