"""Device link: full-duplex WebSocket between the Mac mini and the MacBook (docs/LINK_PROTOCOL.md).

Both roles run the same module:
  * server side — `/ws/link` endpoint (api.py) hands accepted sockets to `serve()`.
  * client side — when `JARVIS_LINK_PEER_URL` is set (normally on the MacBook) `start()`
    dials the peer, reconnects with backoff, and keeps a heartbeat.

Everything observable (spans, journal, governor samples) is mirrored to the peer so Studio
sees both devices in one call graph. Prompts from the MacBook are executed by the Mini's
planner; replies flow back on the same socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from jarvis.config import settings
from jarvis.services import activity_stream, journal, spans

log = logging.getLogger("jarvis.link")

PROTOCOL_VERSION = 1
_outbox: deque[dict] = deque(maxlen=max(50, int(getattr(settings, "link_backlog", 500))))
_peers: dict[str, "Peer"] = {}
_client_task: asyncio.Task | None = None
_pending_replies: dict[str, asyncio.Future] = {}
_handlers: dict[str, Callable[["Peer", dict], Awaitable[None]]] = {}
_sinks_registered = False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def my_role() -> str:
    return getattr(settings, "role", "mini") or "mini"


def my_device_name() -> str:
    return getattr(settings, "device_name", "") or socket.gethostname().split(".")[0]


def envelope(type_: str, payload: dict | None = None, *, to_device: str | None = None,
             trace_id: str | None = None, span_id: str | None = None, parent_span_id: str | None = None,
             from_agent: str | None = None, to_agent: str | None = None) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "type": type_,
        "ts": _now(),
        "from_device": my_role(),
        "to_device": to_device,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "payload": payload or {},
    }


class Peer:
    """One connected remote device (either direction)."""

    def __init__(self, send: Callable[[str], Awaitable[None]], *, remote: str) -> None:
        self._send = send
        self.remote = remote
        self.role: str = "unknown"
        self.hostname: str = ""
        self.last_seen_id: str | None = None
        self.last_heartbeat: str | None = None
        self.connected_at = _now()
        self.system: dict = {}
        self.capabilities: list[str] = []
        self.id = uuid.uuid4().hex[:8]

    async def send(self, msg: dict) -> None:
        try:
            await self._send(json.dumps(msg, default=str))
        except Exception as exc:
            log.debug("link send to %s failed: %s", self.remote, exc)

    def to_dict(self) -> dict:
        return {"id": self.id, "role": self.role, "hostname": self.hostname, "remote": self.remote,
                "connected_at": self.connected_at, "last_heartbeat": self.last_heartbeat,
                "system": self.system, "capabilities": self.capabilities}


# --------------------------------------------------------------------------- outbound

async def broadcast(msg: dict) -> None:
    """Send to every connected peer and keep a backlog for resume."""
    _outbox.append(msg)
    for peer in list(_peers.values()):
        await peer.send(msg)


async def send_prompt(text: str, *, session_id: str | None = None, voice: bool = False,
                      timeout: float = 180.0) -> dict:
    """MacBook → Mini: run a prompt on the control plane and wait for the reply."""
    if not _peers:
        return {"ok": False, "error": "no peer connected"}
    trace_id = spans.current_trace_id() or uuid.uuid4().hex
    msg = envelope("prompt", {"text": text, "session_id": session_id, "voice": voice}, trace_id=trace_id,
                   from_agent="scout" if my_role() == "macbook" else None, to_agent="steward")
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_replies[msg["id"]] = fut
    await broadcast(msg)
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "error": "peer reply timed out"}
    finally:
        _pending_replies.pop(msg["id"], None)


async def send_control(action: str, target: Any = None) -> None:
    await broadcast(envelope("control", {"action": action, "target": target}))


# --------------------------------------------------------------------------- inbound handlers

def handler(type_: str):
    def deco(fn):
        _handlers[type_] = fn
        return fn
    return deco


@handler("hello")
async def _on_hello(peer: Peer, msg: dict) -> None:
    p = msg.get("payload") or {}
    peer.role = str(p.get("role") or msg.get("from_device") or "unknown")
    peer.hostname = str(p.get("hostname") or "")
    peer.capabilities = list(p.get("capabilities") or [])
    peer.last_seen_id = p.get("last_seen_id")
    await journal.write("note", f"link: {peer.role} ({peer.hostname or peer.remote}) connected", source="link")
    await activity_stream.emit("link", f"{peer.role} connected", detail=peer.hostname or peer.remote, status="done",
                               engine="link")
    # Replay what the peer missed.
    if peer.last_seen_id:
        replay, seen = [], False
        for m in _outbox:
            if seen:
                replay.append(m)
            elif m["id"] == peer.last_seen_id:
                seen = True
        for m in replay:
            await peer.send(m)
    await _fleet_touch(peer, "online")


@handler("heartbeat")
async def _on_heartbeat(peer: Peer, msg: dict) -> None:
    peer.last_heartbeat = msg.get("ts") or _now()
    peer.system = (msg.get("payload") or {}).get("system") or {}
    await _fleet_touch(peer, "online")
    await activity_stream.broadcast({"kind": "system", "title": f"{peer.role} heartbeat", "detail": "",
                                     "status": "done", "engine": "link",
                                     "metadata": {"system": {**peer.system, "device": peer.role}}})


@handler("prompt")
async def _on_prompt(peer: Peer, msg: dict) -> None:
    from jarvis.services import planner

    p = msg.get("payload") or {}
    text = str(p.get("text") or "").strip()
    if not text:
        return
    source = "voice" if p.get("voice") else "link"
    async with spans.span("link", f"prompt-from-{peer.role}", trace_id=msg.get("trace_id"), input_text=text,
                          metadata={"from_device": peer.role}):
        try:
            result = await planner.handle_message(text, source=source, session_id=p.get("session_id"))
            payload = {"ok": True, **{k: v for k, v in result.items() if k != "raw"}, "in_reply_to": msg["id"]}
        except Exception as exc:
            log.exception("link prompt failed")
            payload = {"ok": False, "error": str(exc)[:300], "in_reply_to": msg["id"]}
    await peer.send(envelope("reply", payload, trace_id=msg.get("trace_id"), from_agent="steward",
                             to_agent=msg.get("from_agent")))


@handler("reply")
async def _on_reply(peer: Peer, msg: dict) -> None:
    p = msg.get("payload") or {}
    fut = _pending_replies.get(str(p.get("in_reply_to")))
    if fut and not fut.done():
        fut.set_result(p)
    await activity_stream.broadcast({"kind": "reply", "title": f"reply from {peer.role}",
                                     "detail": str(p.get("reply") or p.get("error") or "")[:200],
                                     "status": "done" if p.get("ok") else "failed", "engine": p.get("engine"),
                                     "metadata": {"reply": p, "trace_id": msg.get("trace_id")}})


@handler("span")
async def _on_span(peer: Peer, msg: dict) -> None:
    sp = msg.get("payload") or {}
    sp.setdefault("device", peer.role)
    await activity_stream.broadcast({"kind": "span", "title": f"{sp.get('kind')}:{sp.get('name')}",
                                     "detail": sp.get("agent") or "", "status": sp.get("status"),
                                     "engine": sp.get("provider"), "metadata": {**sp, "remote": True}})


@handler("journal")
async def _on_journal(peer: Peer, msg: dict) -> None:
    row = msg.get("payload") or {}
    await activity_stream.broadcast({"kind": "journal", "title": f"{row.get('kind')}: {str(row.get('summary'))[:80]}",
                                     "detail": row.get("detail") or "", "status": "done", "engine": row.get("agent"),
                                     "metadata": {"journal": {**row, "remote": True}}})


@handler("system")
async def _on_system(peer: Peer, msg: dict) -> None:
    peer.system = (msg.get("payload") or {}).get("system") or peer.system
    await activity_stream.broadcast({"kind": "system", "title": f"{peer.role} system", "detail": "", "status": "done",
                                     "engine": "link", "metadata": {"system": {**peer.system, "device": peer.role}}})


@handler("control")
async def _on_control(peer: Peer, msg: dict) -> None:
    from jarvis.services import resource_governor

    p = msg.get("payload") or {}
    action = str(p.get("action") or "")
    if action in ("pause", "resume", "keep", "cancel"):
        result = await resource_governor.override(action, target=p.get("target"), by=f"link:{peer.role}")
    elif action == "selfheal":
        from jarvis.services import selfheal

        result = await selfheal.run(apply=True, by=f"link:{peer.role}")
    else:
        result = {"ok": False, "error": f"unknown control {action}"}
    await peer.send(envelope("ack", {"ack_id": msg["id"], "result": result}))


@handler("ack")
async def _on_ack(peer: Peer, msg: dict) -> None:
    return None


async def dispatch(peer: Peer, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(msg, dict) or "type" not in msg:
        return
    peer.last_seen_id = msg.get("id") or peer.last_seen_id
    fn = _handlers.get(str(msg["type"]))
    if fn is None:
        return
    try:
        await fn(peer, msg)
    except Exception as exc:
        log.warning("link handler %s failed: %s", msg["type"], exc)


async def _fleet_touch(peer: Peer, status: str) -> None:
    """Keep the LAN fleet registry in sync with link presence (best effort)."""
    try:
        from jarvis.services import fleet_registry
        from jarvis.services.fleet_types import FleetHeartbeatRequest

        name = settings.fleet_macbook_name if peer.role == "macbook" else (
            settings.fleet_mini_name if peer.role == "mini" else peer.hostname or peer.role)
        if status == "online":
            await fleet_registry.heartbeat(FleetHeartbeatRequest(name=name, status="online"))
        else:
            await fleet_registry.mark_stale_offline(timeout_seconds=0)
    except Exception:
        pass


# --------------------------------------------------------------------------- server side

async def serve(websocket, *, remote: str) -> None:
    """Run the protocol over an accepted FastAPI WebSocket until it closes."""
    peer = Peer(websocket.send_text, remote=remote)
    _peers[peer.id] = peer
    _ensure_sinks()
    await peer.send(hello_message())
    try:
        while True:
            raw = await websocket.receive_text()
            await dispatch(peer, raw)
    except Exception:
        pass
    finally:
        _peers.pop(peer.id, None)
        await journal.write("note", f"link: {peer.role} disconnected", source="link")
        await activity_stream.emit("link", f"{peer.role} disconnected", status="failed", engine="link")
        await _fleet_touch(peer, "offline")


def hello_message() -> dict:
    caps = ["prompt", "spans", "journal", "governor"]
    if my_role() == "mini":
        caps += ["gateway", "keys", "agents", "selfheal"]
    return envelope("hello", {"role": my_role(), "hostname": my_device_name(), "version": PROTOCOL_VERSION,
                              "capabilities": caps,
                              "last_seen_id": next(iter(_peers.values())).last_seen_id if _peers else None})


# --------------------------------------------------------------------------- client side

async def _client_loop(url: str) -> None:
    import websockets

    backoff = 2.0
    headers = {"X-Jarvis-Fleet-Token": settings.fleet_token or ""}
    while True:
        try:
            async with websockets.connect(url, additional_headers=headers, ping_interval=None, max_size=4 * 1024 * 1024) as ws:
                peer = Peer(ws.send, remote=url)
                _peers[peer.id] = peer
                _ensure_sinks()
                backoff = 2.0
                await peer.send(hello_message())
                hb = asyncio.create_task(_heartbeat_loop(peer))
                try:
                    async for raw in ws:
                        await dispatch(peer, raw if isinstance(raw, str) else raw.decode())
                finally:
                    hb.cancel()
                    _peers.pop(peer.id, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("link to %s unavailable (%s); retry in %.0fs", url, str(exc)[:120], backoff)
            await activity_stream.emit("link", "peer unreachable", detail=str(exc)[:120], status="failed", engine="link")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.7, 60.0)


async def _heartbeat_loop(peer: Peer) -> None:
    from jarvis.services import resource_governor

    interval = max(3, int(getattr(settings, "link_heartbeat_seconds", 10)))
    while True:
        await peer.send(envelope("heartbeat", {"system": resource_governor.snapshot_compact()}))
        await asyncio.sleep(interval)


def _ensure_sinks() -> None:
    """Mirror local spans / journal / governor samples to peers (registered once)."""
    global _sinks_registered
    if _sinks_registered:
        return
    _sinks_registered = True

    async def span_sink(payload: dict) -> None:
        if _peers:
            await broadcast(envelope("span", payload, trace_id=payload.get("trace_id"), span_id=payload.get("span_id"),
                                     parent_span_id=payload.get("parent_span_id"), from_agent=payload.get("agent")))

    async def journal_sink(row: dict) -> None:
        if _peers:
            await broadcast(envelope("journal", row, trace_id=row.get("trace_id"), from_agent=row.get("agent")))

    spans.register_sink(span_sink)
    journal.register_sink(journal_sink)


def start() -> None:
    global _client_task
    url = (getattr(settings, "link_peer_url", "") or "").strip()
    if not url:
        return
    if _client_task and not _client_task.done():
        return
    _client_task = asyncio.create_task(_client_loop(url))


async def stop() -> None:
    global _client_task
    if _client_task:
        _client_task.cancel()
        _client_task = None


def status() -> dict:
    return {
        "role": my_role(),
        "device_name": my_device_name(),
        "peer_url": getattr(settings, "link_peer_url", "") or None,
        "client_running": bool(_client_task and not _client_task.done()),
        "peers": [p.to_dict() for p in _peers.values()],
        "backlog": len(_outbox),
        "protocol": PROTOCOL_VERSION,
    }
