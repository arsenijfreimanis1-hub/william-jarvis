"""Load agent definitions from `jarvis/agents/<group>/<slug>/README.md` (+ POSTTHOUGHT.md).

Two formats are accepted:

1. Folder contract (preferred): a directory holding `README.md` with YAML front matter and
   an optional `POSTTHOUGHT.md`. See docs/AGENT_CONTRACT.md.
2. Legacy single file `<slug>.md` using `**purpose:**` style fields (the original rosters).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from jarvis.paths import ROOT
from jarvis.services.agent_types import KNOWN_AGENT_TOOLS, AgentRuntimeConfig, AgentSpec

AGENTS_DIR = ROOT / "jarvis" / "agents"
DEFAULT_TOOLS = ["cursor_agent.run", "terminal.execute", "memory.retrieve", "memory.store"]
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
_ROLES = ("control", "planner", "tester", "general")


def _split_front_matter(text: str) -> tuple[dict, str]:
    m = _FRONT.match(text.strip())
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front matter: {exc}") from exc
    if not isinstance(meta, dict):
        meta = {}
    return meta, m.group(2)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _strip_title(body: str) -> str:
    return re.sub(r"^#\s+.+\n+", "", body.strip(), count=1).strip()


def parse_folder(folder: Path) -> AgentSpec:
    readme = folder / "README.md"
    if not readme.is_file():
        raise FileNotFoundError(readme)
    meta, body = _split_front_matter(readme.read_text(encoding="utf-8"))
    post = folder / "POSTTHOUGHT.md"
    post_thought = post.read_text(encoding="utf-8").strip() if post.is_file() else ""

    name = str(meta.get("name") or folder.name.replace("-", " ").title()).strip()
    engine = str(meta.get("engine") or "cursor").strip().lower()
    if engine not in ("rules", "gateway", "cursor"):
        engine = "cursor"
    tools = [t for t in _as_list(meta.get("tools")) if t in KNOWN_AGENT_TOOLS]
    if engine == "rules":
        tools = ["rules.run", *[t for t in tools if t != "rules.run"]]
    elif not tools:
        tools = DEFAULT_TOOLS
    role = str(meta.get("preferred_role") or "").strip().lower() or None
    if role not in _ROLES:
        role = None
    affinity = str(meta.get("device_affinity") or "any").strip().lower()
    if affinity not in ("mini", "macbook", "any"):
        affinity = "any"
    model = meta.get("model")
    if engine == "gateway" and not model:
        model = "gateway"

    return AgentSpec(
        name=name,
        purpose=str(meta.get("purpose") or name).strip(),
        instructions=_strip_title(body) or str(meta.get("purpose") or name),
        trigger_phrases=_as_list(meta.get("triggers")),
        status=str(meta.get("status") or "active"),
        runtime=AgentRuntimeConfig(
            engine=engine,
            entrypoint=meta.get("entrypoint") or None,
            model=str(model).strip() if model else None,
            allowed_tools=tools,
            preferred_role=role,
            preferred_capabilities=_as_list(meta.get("preferred_capabilities")),
            device_affinity=affinity,
            token_budget=int(meta.get("token_budget") or 0),
            requires=_as_list(meta.get("requires")),
            skills=_as_list(meta.get("skills")),
            cooldown_hours=float(meta.get("cooldown_hours") or 0),
            group=str(meta.get("group") or folder.parent.name),
            post_thought=post_thought,
            workspace_dir=meta.get("workspace_dir") or None,
        ),
    )


def _legacy_list_field(text: str, label: str) -> list[str]:
    m = re.search(rf"\*\*{label}:\*\*\s*(.+)$", text, re.M | re.I)
    return [p.strip() for p in m.group(1).split(",") if p.strip()] if m else []


def parse_legacy_file(path: Path) -> AgentSpec:
    text = path.read_text(encoding="utf-8")
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    purpose_m = re.search(r"\*\*purpose:\*\*\s*(.+)$", text, re.M | re.I)
    role_m = re.search(r"\*\*preferred_role:\*\*\s*(\w+)", text, re.M | re.I)
    instructions_m = re.search(r"\*\*instructions:\*\*\s*\n(.*)$", text, re.S | re.I)
    triggers_block = re.search(r"\*\*triggers:\*\*\s*\n((?:- .+\n?)+)", text, re.I)
    triggers = []
    if triggers_block:
        triggers = [re.sub(r"^-+\s*", "", ln).strip() for ln in triggers_block.group(1).splitlines()
                    if ln.strip().startswith("-")]
    name = (title_m.group(1) if title_m else path.stem).strip()
    role = role_m.group(1).lower() if role_m else None
    model_m = re.search(r"\*\*model:\*\*\s*([\w.:/-]+)", text, re.M | re.I)
    model = model_m.group(1).strip() if model_m else None
    tools = [t for t in _legacy_list_field(text, "tools") if t in KNOWN_AGENT_TOOLS] or DEFAULT_TOOLS
    return AgentSpec(
        name=name,
        purpose=(purpose_m.group(1) if purpose_m else name).strip(),
        instructions=(instructions_m.group(1) if instructions_m else text).strip(),
        trigger_phrases=triggers,
        runtime=AgentRuntimeConfig(
            engine="gateway" if model == "gateway" else "cursor",
            preferred_role=role if role in _ROLES else None,
            preferred_capabilities=_legacy_list_field(text, "preferred_capabilities"),
            allowed_tools=tools,
            model=model,
            group=path.parent.name,
        ),
    )


def discover(only_group: str | None = None) -> list[tuple[Path, AgentSpec]]:
    """Return (path, spec) for every agent definition under jarvis/agents."""
    out: list[tuple[Path, AgentSpec]] = []
    groups = [AGENTS_DIR / only_group] if only_group else sorted(
        p for p in AGENTS_DIR.iterdir() if p.is_dir() and not p.name.startswith(("__", "."))
    )
    for group in groups:
        if not group.is_dir() or group.name == "rules":
            continue
        for entry in sorted(group.iterdir()):
            if entry.is_dir() and (entry / "README.md").is_file():
                out.append((entry, parse_folder(entry)))
            elif entry.is_file() and entry.suffix == ".md" and entry.name.lower() not in ("readme.md", "postthought.md"):
                out.append((entry, parse_legacy_file(entry)))
    return out
