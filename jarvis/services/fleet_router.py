"""Role and capability routing for LAN fleet jobs."""

from __future__ import annotations

from typing import Any

from jarvis.services import fleet_power, fleet_registry
from jarvis.services.fleet_types import FleetJobTag, FleetNodeRecord, FleetRole

TAG_PREFERRED_ROLE: dict[FleetJobTag, FleetRole] = {
    "plan": "planner",
    "test": "tester",
    "gpu": "tester",
    "integrate": "control",
    "general": "general",
    "shell": "general",
}

# Tags that must wait for their preferred node (no Mini fallback).
QUEUE_ONLY_TAGS = frozenset({"test", "gpu"})


def preferred_role_for_tag(tag: FleetJobTag) -> FleetRole:
    return TAG_PREFERRED_ROLE.get(tag, "general")


def node_matches(node: FleetNodeRecord, *, role: FleetRole | None, capabilities: list[str]) -> bool:
    if role and role not in ("general",) and node.role != role:
        return False
    if capabilities and not set(capabilities).issubset(set(node.capabilities)):
        return False
    return True


async def pick_node_for_tag(
    tag: FleetJobTag,
    *,
    required_capabilities: list[str] | None = None,
    preferred_role: FleetRole | None = None,
) -> dict[str, Any]:
    """Choose where work should run; never crashes when peers are offline."""
    from jarvis.config import settings

    await fleet_registry.mark_stale_offline(
        timeout_seconds=int(settings.fleet_heartbeat_timeout_seconds)
    )
    role = preferred_role or preferred_role_for_tag(tag)
    caps = list(required_capabilities or [])
    nodes = await fleet_registry.list_nodes()
    online = [n for n in nodes if n.status == "online"]

    preferred_online = [n for n in online if node_matches(n, role=role, capabilities=caps)]
    if preferred_online:
        node = preferred_online[0]
        return {
            "decision": "dispatch",
            "node": node.model_dump(),
            "reason": f"{tag} → online {node.role} node {node.name}",
        }

    sleeping = [
        n
        for n in nodes
        if n.status == "sleeping" and node_matches(n, role=role, capabilities=caps)
    ]
    if sleeping and tag in QUEUE_ONLY_TAGS:
        target = sleeping[0]
        wol = await fleet_power.wake_node_by_name(target.name)
        return {
            "decision": "wake_and_queue",
            "node": target.model_dump(),
            "wol": wol,
            "reason": f"{tag} prefers {target.name}; sent WoL and queued",
        }

    if tag in QUEUE_ONLY_TAGS:
        return {
            "decision": "queue",
            "node": None,
            "reason": f"{tag} waits for tester; no online/sleeping match",
        }

    # Fallback: control plane (Mini) for plan/integrate/general/shell
    control = [n for n in online if n.role == "control"]
    if control:
        node = control[0]
        return {
            "decision": "fallback_local",
            "node": node.model_dump(),
            "reason": f"{tag} preferred offline; falling back to {node.name}",
        }

    any_online = online[0] if online else None
    if any_online:
        return {
            "decision": "fallback_any",
            "node": any_online.model_dump(),
            "reason": f"{tag} using available node {any_online.name}",
        }

    return {
        "decision": "queue",
        "node": None,
        "reason": "no online fleet nodes; job stays queued",
    }


async def enqueue_routed(
    *,
    title: str,
    tag: FleetJobTag = "general",
    body: str = "",
    command: str | None = None,
    preferred_role: FleetRole | None = None,
    required_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    role = preferred_role or preferred_role_for_tag(tag)
    routing = await pick_node_for_tag(
        tag,
        required_capabilities=required_capabilities,
        preferred_role=role,
    )
    job = await fleet_registry.enqueue_job(
        title=title,
        tag=tag,
        body=body,
        command=command,
        preferred_role=role,
        required_capabilities=required_capabilities,
    )
    return {"ok": True, "job": job.model_dump(), "routing": routing}


async def status_reply() -> str:
    """Human-readable LAN fleet presence summary for chat/voice."""
    from jarvis.services import compute_fleet

    status = await compute_fleet.fleet_status()
    lan = status.get("lan") or {}
    lines = ["LAN fleet:"]
    for node in lan.get("nodes") or []:
        caps = ",".join(node.get("capabilities") or []) or "-"
        host = node.get("lan_host") or "-"
        lines.append(
            f"- {node.get('name')} ({node.get('role')}): {node.get('status')} @ {host} [{caps}]"
        )
    jobs = lan.get("jobs") or {}
    if jobs:
        parts = [f"{k}={v}" for k, v in sorted(jobs.items())]
        lines.append("jobs: " + ", ".join(parts))
    else:
        lines.append("jobs: none")
    return "\n".join(lines)
