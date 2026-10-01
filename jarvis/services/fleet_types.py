"""Typed schemas for the LAN compute fleet."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

FleetRole = Literal["control", "planner", "tester", "general"]
FleetNodeStatus = Literal["online", "sleeping", "offline", "configured"]
FleetJobStatus = Literal["queued", "claimed", "done", "failed", "cancelled"]
FleetJobTag = Literal["plan", "test", "integrate", "gpu", "general", "shell"]

KNOWN_CAPABILITIES = frozenset(
    {
        "control",
        "planner",
        "tester",
        "gpu",
        "dual_monitor",
        "ollama",
        "shell",
        "cursor",
    }
)


def normalize_node_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.strip())
    if not cleaned:
        raise ValueError("node name cannot be empty")
    return cleaned


def node_name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_node_name(name).lower()).strip("-")


def normalize_mac_address(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    hex_only = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(hex_only) != 12:
        raise ValueError("mac_address must be 12 hex digits")
    parts = [hex_only[i : i + 2].upper() for i in range(0, 12, 2)]
    return ":".join(parts)


class FleetNodeSpec(BaseModel):
    name: str
    role: FleetRole = "general"
    os: str = "unknown"
    capabilities: list[str] = Field(default_factory=list)
    lan_host: str | None = None
    mac_address: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_node_name(value)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            cap = str(item or "").strip().lower()
            if not cap or cap in seen:
                continue
            cleaned.append(cap)
            seen.add(cap)
        return cleaned

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, value: str | None) -> str | None:
        return normalize_mac_address(value)


class FleetNodeRecord(FleetNodeSpec):
    id: int
    name_key: str
    status: FleetNodeStatus = "offline"
    last_heartbeat_at: str | None = None
    last_wol_at: str | None = None
    created_at: str
    updated_at: str


class FleetRegisterRequest(BaseModel):
    name: str
    role: FleetRole = "general"
    os: str = "unknown"
    capabilities: list[str] = Field(default_factory=list)
    lan_host: str | None = None
    mac_address: str | None = None
    token: str | None = None


class FleetHeartbeatRequest(BaseModel):
    name: str
    status: Literal["online", "sleeping"] = "online"
    lan_host: str | None = None
    capabilities: list[str] | None = None
    token: str | None = None


class FleetEnqueueRequest(BaseModel):
    title: str
    tag: FleetJobTag = "general"
    body: str = ""
    command: str | None = None
    preferred_role: FleetRole | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    token: str | None = None


class FleetClaimRequest(BaseModel):
    name: str
    tags: list[FleetJobTag] = Field(default_factory=lambda: ["general", "shell"])
    lease_seconds: int = Field(default=120, ge=30, le=3600)
    token: str | None = None


class FleetResultRequest(BaseModel):
    name: str
    job_id: int
    ok: bool
    output: str = ""
    error: str = ""
    token: str | None = None


class FleetJobRecord(BaseModel):
    id: int
    title: str
    tag: FleetJobTag
    body: str = ""
    command: str | None = None
    preferred_role: FleetRole | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    status: FleetJobStatus = "queued"
    assigned_node_id: int | None = None
    assigned_node_name: str | None = None
    lease_expires_at: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
