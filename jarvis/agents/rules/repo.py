"""Rules agents for git / files / journal lookups (0 tokens)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from jarvis.config import settings
from jarvis.services import journal


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd or settings.workspace_dir), capture_output=True, text=True,
                          timeout=20)
    return (proc.stdout or proc.stderr).strip()


async def git_status(task: str, ctx: dict) -> dict:
    """Branch, dirty files and last commits of the William repo (or a named repo under ~/Projects)."""
    cwd = settings.workspace_dir
    m = re.search(r"\b(?:repo|project)\s+([\w.-]+)", task, re.I)
    if m:
        candidate = settings.build_projects_dir / m.group(1)
        if (candidate / ".git").is_dir():
            cwd = candidate
    branch = _git("branch", "--show-current", cwd=cwd)
    dirty = _git("status", "--short", cwd=cwd)
    log = _git("log", "--oneline", "-5", cwd=cwd)
    n_dirty = len([ln for ln in dirty.splitlines() if ln.strip()])
    reply = (f"{cwd.name} on {branch or '?'}: {n_dirty} changed file(s).\n"
             f"{dirty[:800] or '(clean)'}\n\nRecent commits:\n{log}")
    return {"ok": True, "reply": reply, "data": {"repo": str(cwd), "branch": branch, "dirty": n_dirty}}


async def journal_digest(task: str, ctx: dict) -> dict:
    """Summarize the journal: open problems, recent weaknesses/strengths, counts."""
    stats = await journal.stats(days=7)
    problems = await journal.open_problems(limit=8)
    recent = await journal.list_entries(limit=10)
    lines = [f"Last 7 days: " + ", ".join(f"{k} {v}" for k, v in sorted(stats["by_kind"].items())) or "no entries",
             f"Open problems: {stats['open_problems']}"]
    for p in problems:
        lines.append(f"  ! {p['summary']}" + (f" ({p['agent']})" if p.get("agent") else ""))
    lines.append("Recent:")
    for r in recent:
        lines.append(f"  - [{r['kind']}] {r['summary'][:100]}")
    return {"ok": True, "reply": "\n".join(lines), "data": {"stats": stats, "open_problems": problems}}


async def read_file(task: str, ctx: dict) -> dict:
    """Read a text file inside the workspace or ~/Projects: 'read file docs/PRD.md'."""
    m = re.search(r"(?:read|show|cat|open)\s+(?:file\s+)?([\w./~-]+)", task, re.I)
    if not m:
        return {"ok": False, "reply": "Which file?", "error": "no path"}
    raw = m.group(1)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = settings.workspace_dir / path
    path = path.resolve()
    allowed = (settings.workspace_dir.resolve(), settings.build_projects_dir.resolve())
    if not any(str(path).startswith(str(root)) for root in allowed):
        return {"ok": False, "reply": "That path is outside the allowed folders.", "error": "path not allowed"}
    if not path.is_file():
        return {"ok": False, "reply": f"No file at {path}", "error": "not found"}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "reply": text[:6000] + ("\n…(truncated)" if len(text) > 6000 else ""),
            "data": {"path": str(path), "bytes": len(text)}}
