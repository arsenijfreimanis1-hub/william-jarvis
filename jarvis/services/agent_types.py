"""Structured schemas for William's persisted specialist agents."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

KNOWN_AGENT_TOOLS = frozenset(
    {
        "cursor_agent.run",
        "memory.retrieve",
        "memory.store",
        "system_control.execute",
        "terminal.execute",
        "web.research",
        "web.automate",
        # Free-provider gateway tools
        "gateway.chat",
        "gateway.fim",
        "providers.scan_keys",
        "providers.probe",
        "providers.usage",
        "providers.save_key",
        "speech.transcribe",
        "speech.synthesize",
        "vectors.embed",
        "vectors.search",
        "cad.generate",
        "app_icons.apply",
        # Zero-token rules tools
        "rules.run",
        "system.live",
        "system.services",
        "git.status",
        "files.read",
        "journal.read",
        "journal.write",
        "web.search",
        "notify.user",
        "agents.invoke",
    }
)

AgentEngine = Literal["rules", "gateway", "cursor"]
DeviceAffinity = Literal["mini", "macbook", "any"]


def normalize_agent_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.strip())
    if not cleaned:
        raise ValueError("agent name cannot be empty")
    return cleaned


def agent_name_key(name: str) -> str:
    normalized = normalize_agent_name(name).lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


class AgentRuntimeConfig(BaseModel):
    # Legacy field kept for compatibility; `engine` is the real selector.
    execution_engine: Literal["cursor"] = "cursor"
    engine: AgentEngine = "cursor"
    entrypoint: str | None = None  # rules engine: "jarvis.agents.rules.system:live"
    autonomy_mode: Literal["supervised", "assisted"] = "supervised"
    model: str | None = None
    workspace_dir: str | None = None
    allowed_tools: list[str] = Field(default_factory=lambda: ["cursor_agent.run"])
    preferred_role: Literal["control", "planner", "tester", "general"] | None = None
    preferred_capabilities: list[str] = Field(default_factory=list)
    device_affinity: DeviceAffinity = "any"
    token_budget: int = 0  # 0 = no cap
    requires: list[str] = Field(default_factory=list)  # agent name_keys invoked as child spans
    skills: list[str] = Field(default_factory=list)
    cooldown_hours: float = 0.0
    group: str | None = None
    post_thought: str = ""  # POSTTHOUGHT.md template; empty = default

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            tool = str(item or "").strip()
            if not tool:
                continue
            if tool not in KNOWN_AGENT_TOOLS:
                raise ValueError(f"unknown tool allowlist entry: {tool}")
            if tool not in seen:
                cleaned.append(tool)
                seen.add(tool)
        if "cursor_agent.run" not in seen and "rules.run" not in seen:
            cleaned.insert(0, "cursor_agent.run")
        return cleaned

    @field_validator("requires", "skills")
    @classmethod
    def normalize_keys(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            key = agent_name_key(str(item or "")) if str(item or "").strip() else ""
            if key and key not in seen:
                cleaned.append(key)
                seen.add(key)
        return cleaned

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str | None) -> str | None:
        if not value:
            return None
        value = value.strip()
        if ":" not in value or not re.match(r"^[\w.]+:[\w]+$", value):
            raise ValueError("entrypoint must look like package.module:function")
        return value

    @field_validator("preferred_capabilities")
    @classmethod
    def validate_preferred_capabilities(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            cap = str(item or "").strip().lower()
            if not cap or cap in seen:
                continue
            cleaned.append(cap)
            seen.add(cap)
        return cleaned


class AgentSpec(BaseModel):
    name: str
    purpose: str
    instructions: str
    trigger_phrases: list[str] = Field(default_factory=list)
    status: Literal["active", "archived", "disabled"] = "active"
    runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    parent_agent_id: int | None = None
    learning_notes: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_agent_name(value)

    @field_validator("purpose", "instructions", "learning_notes")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("trigger_phrases")
    @classmethod
    def normalize_trigger_phrases(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            phrase = str(raw or "").strip()
            if not phrase:
                continue
            lowered = phrase.lower()
            if lowered in seen:
                continue
            cleaned.append(phrase)
            seen.add(lowered)
        return cleaned


class AgentRecord(AgentSpec):
    id: int
    name_key: str
    version: int = 1
    performance_score: float = 0.0
    last_used_at: str | None = None
    last_improved_at: str | None = None
    created_at: str
    updated_at: str
