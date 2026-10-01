"""LAN fleet registry, routing, auth, and Wake-on-LAN tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.routers import api
from jarvis.services import compute_fleet, fleet_auth, fleet_power, fleet_registry, fleet_router
from jarvis.services.fleet_types import (
    FleetHeartbeatRequest,
    FleetNodeSpec,
    FleetRegisterRequest,
    normalize_mac_address,
)


@pytest.fixture
async def fleet_db(monkeypatch, tmp_path):
    db_path = tmp_path / "fleet.db"
    monkeypatch.setattr(fleet_registry, "DB_PATH", db_path)
    monkeypatch.setattr("jarvis.config.settings.fleet_token", "")
    monkeypatch.setattr("jarvis.config.settings.fleet_heartbeat_timeout_seconds", 60)
    monkeypatch.setattr("jarvis.config.settings.fleet_pc_mac", "AA:BB:CC:DD:EE:FF")
    monkeypatch.setattr("jarvis.config.settings.fleet_mini_name", "Mac Mini")
    monkeypatch.setattr("jarvis.config.settings.fleet_macbook_name", "MacBook")
    monkeypatch.setattr("jarvis.config.settings.fleet_pc_name", "Windows PC")
    await fleet_registry.ensure_tables()
    return db_path


def test_normalize_mac_address():
    assert normalize_mac_address("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac_address("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    with pytest.raises(ValueError):
        normalize_mac_address("bad")


@pytest.mark.asyncio
async def test_register_heartbeat_and_stale_offline(fleet_db, monkeypatch):
    node = await fleet_registry.register_node(
        FleetRegisterRequest(
            name="MacBook",
            role="planner",
            os="macos",
            capabilities=["planner", "shell"],
            lan_host="192.168.1.20",
        )
    )
    assert node.status == "online"

    hb = await fleet_registry.heartbeat(
        FleetHeartbeatRequest(name="MacBook", status="online", lan_host="192.168.1.21")
    )
    assert hb is not None
    assert hb.lan_host == "192.168.1.21"
    assert hb.status == "online"

    # Force stale by rewriting heartbeat timestamp into the past
    import aiosqlite

    async with aiosqlite.connect(fleet_db) as db:
        await db.execute(
            "UPDATE fleet_nodes SET last_heartbeat_at = '2000-01-01 00:00:00' WHERE name_key = 'macbook'"
        )
        await db.commit()

    marked = await fleet_registry.mark_stale_offline(timeout_seconds=30)
    assert marked >= 1
    refreshed = await fleet_registry.get_node_by_name("MacBook")
    assert refreshed is not None
    assert refreshed.status == "offline"


@pytest.mark.asyncio
async def test_ensure_seed_nodes_and_fleet_status(fleet_db, monkeypatch):
    async def fake_profile():
        return {"ok": True}

    monkeypatch.setattr("jarvis.services.local_runtime.execution_profile", lambda: {"mode": "test"})
    monkeypatch.setattr("jarvis.services.github_sync.configured", lambda: False)

    seeds = await compute_fleet.ensure_seed_nodes()
    assert len(seeds) == 3
    names = {s["name"] for s in seeds}
    assert "Mac Mini" in names
    assert "MacBook" in names
    assert "Windows PC" in names

    mini = await fleet_registry.get_node_by_name("Mac Mini")
    assert mini is not None
    assert mini.status == "online"
    assert mini.role == "control"

    macbook = await fleet_registry.get_node_by_name("MacBook")
    assert macbook is not None
    assert macbook.status in ("configured", "offline")

    status = await compute_fleet.fleet_status()
    assert "lan" in status
    assert len(status["lan"]["nodes"]) >= 3


@pytest.mark.asyncio
async def test_enqueue_claim_result_and_lease_reclaim(fleet_db):
    await fleet_registry.register_node(
        FleetRegisterRequest(
            name="MacBook",
            role="planner",
            os="macos",
            capabilities=["planner", "shell"],
        )
    )
    job = await fleet_registry.enqueue_job(
        title="echo hello",
        tag="shell",
        command="echo hello",
        preferred_role="planner",
    )
    assert job.status == "queued"

    node = await fleet_registry.get_node_by_name("MacBook")
    claimed = await fleet_registry.claim_job(node=node, tags=["shell", "plan"], lease_seconds=60)
    assert claimed is not None
    assert claimed.status == "claimed"
    assert claimed.command == "echo hello"

    done = await fleet_registry.complete_job(
        job_id=claimed.id, node=node, ok=True, output="hello\n"
    )
    assert done is not None
    assert done.status == "done"
    assert done.result["ok"] is True


@pytest.mark.asyncio
async def test_role_routing_queue_test_when_pc_offline(fleet_db):
    await compute_fleet.ensure_seed_nodes()
    routing = await fleet_router.pick_node_for_tag("test")
    assert routing["decision"] == "queue"

    plan_routing = await fleet_router.pick_node_for_tag("plan")
    assert plan_routing["decision"] == "fallback_local"
    assert plan_routing["node"]["role"] == "control"

    await fleet_registry.register_node(
        FleetRegisterRequest(
            name="MacBook",
            role="planner",
            os="macos",
            capabilities=["planner", "shell"],
        )
    )
    plan_online = await fleet_router.pick_node_for_tag("plan")
    assert plan_online["decision"] == "dispatch"
    assert plan_online["node"]["name"] == "MacBook"


@pytest.mark.asyncio
async def test_wake_and_queue_when_pc_sleeping(fleet_db, monkeypatch):
    await compute_fleet.ensure_seed_nodes()
    await fleet_registry.upsert_configured_node(
        FleetNodeSpec(
            name="Windows PC",
            role="tester",
            os="windows",
            capabilities=["tester", "shell", "gpu"],
            mac_address="AA:BB:CC:DD:EE:FF",
        ),
        status="sleeping",
    )
    # Force sleeping status (upsert may preserve online)
    import aiosqlite

    async with aiosqlite.connect(fleet_db) as db:
        await db.execute(
            "UPDATE fleet_nodes SET status = 'sleeping', mac_address = 'AA:BB:CC:DD:EE:FF' WHERE name_key = 'windows-pc'"
        )
        await db.commit()

    sent: list[str] = []

    def fake_send(mac, **kwargs):
        sent.append(mac)
        return {"ok": True, "mac": mac, "broadcast": "255.255.255.255", "port": 9}

    monkeypatch.setattr(fleet_power, "send_wol", fake_send)
    routing = await fleet_router.pick_node_for_tag("test")
    assert routing["decision"] == "wake_and_queue"
    assert sent


def test_fleet_token_required(monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.fleet_token", "secret-token")
    with pytest.raises(Exception) as exc:
        fleet_auth.verify_fleet_token("wrong")
    assert exc.value.status_code == 401
    fleet_auth.verify_fleet_token("secret-token")


def test_magic_packet_length():
    packet = fleet_power.build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102
    assert packet.startswith(b"\xff" * 6)


def test_fleet_api_status(monkeypatch, tmp_path):
    db_path = tmp_path / "fleet-api.db"
    monkeypatch.setattr(fleet_registry, "DB_PATH", db_path)
    monkeypatch.setattr("jarvis.config.settings.fleet_token", "")
    monkeypatch.setattr("jarvis.services.local_runtime.execution_profile", lambda: {})
    monkeypatch.setattr("jarvis.services.github_sync.configured", lambda: False)

    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    response = client.get("/api/fleet/status")
    assert response.status_code == 200
    body = response.json()
    assert "lan" in body
    assert any(n["name"] == "Mac Mini" for n in body["lan"]["nodes"])
