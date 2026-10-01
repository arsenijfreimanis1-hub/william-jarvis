"""Wake-on-LAN helpers for the Windows PC tester node."""

from __future__ import annotations

import logging
import socket
from typing import Any

from jarvis.config import settings
from jarvis.services import fleet_registry
from jarvis.services.fleet_types import normalize_mac_address

log = logging.getLogger("jarvis.fleet_power")


def build_magic_packet(mac: str) -> bytes:
    normalized = normalize_mac_address(mac)
    if not normalized:
        raise ValueError("mac address required")
    hw = bytes.fromhex(normalized.replace(":", ""))
    return b"\xff" * 6 + hw * 16


def send_wol(mac: str, *, broadcast: str | None = None, port: int | None = None) -> dict[str, Any]:
    packet = build_magic_packet(mac)
    dest = broadcast or settings.fleet_wol_broadcast or "255.255.255.255"
    dest_port = int(port or settings.fleet_wol_port or 9)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (dest, dest_port))
    return {"ok": True, "mac": normalize_mac_address(mac), "broadcast": dest, "port": dest_port}


async def wake_node_by_name(name: str) -> dict[str, Any]:
    node = await fleet_registry.get_node_by_name(name)
    if not node:
        return {"ok": False, "error": f"unknown node: {name}"}
    mac = node.mac_address or settings.fleet_pc_mac
    if not mac:
        return {"ok": False, "error": "no mac_address configured for node"}
    try:
        result = send_wol(mac)
    except Exception as exc:
        log.warning("WoL failed for %s: %s", name, exc)
        return {"ok": False, "error": str(exc)}
    updated = await fleet_registry.set_wol_sent(node.id)
    return {
        "ok": True,
        "node": updated.model_dump() if updated else node.model_dump(),
        "wol": result,
    }


async def wake_pc() -> dict[str, Any]:
    """Wake the configured Windows PC tester (by name or MAC fallback)."""
    name = (settings.fleet_pc_name or "Windows PC").strip()
    node = await fleet_registry.get_node_by_name(name)
    if node:
        return await wake_node_by_name(name)
    mac = settings.fleet_pc_mac
    if not mac:
        return {"ok": False, "error": "JARVIS_FLEET_PC_MAC not configured"}
    try:
        result = send_wol(mac)
        return {"ok": True, "wol": result, "node": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
