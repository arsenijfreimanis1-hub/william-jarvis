"""Key Scout + Quota Keeper: find keys, verify them cheaply, track usage, and report in plain English."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import Any

import httpx

from jarvis.config import settings
from jarvis.services.providers import catalog, gateway, keys, usage

log = logging.getLogger("jarvis.providers.scout")

_last_scan: dict[str, Any] = {}
_last_probe: dict[str, Any] = {}


async def scan_keys(*, announce: bool = True) -> dict[str, Any]:
    """Key Scout job: discover keys everywhere William is allowed to look, remember what changed."""
    global _last_scan
    report = keys.report()
    changes: list[str] = []
    previous = _last_scan.get("present_ids", set())
    present_ids = {row["provider"] for row in report["present"]}
    for pid in present_ids - previous:
        if _last_scan:
            changes.append(f"new key found: {pid}")
    for pid in previous - present_ids:
        changes.append(f"key disappeared: {pid}")
    for row in report["present"]:
        await usage.record_key_state(row["provider"], present=True, source=row["source"])
    for row in report["missing"]:
        await usage.record_key_state(row["provider"], present=False, source=None)
    _last_scan = {**report, "present_ids": present_ids, "changes": changes, "scanned_at": time.time()}
    if changes and announce:
        try:
            from jarvis.services import event_log

            await event_log.log_integration("providers", source="scout", detail="; ".join(changes)[:400],
                                            metadata={"present": sorted(present_ids)})
        except Exception:
            pass
    return {**report, "changes": changes}


def _probe_target(spec: catalog.ProviderSpec, key: str) -> tuple[str, dict[str, str]] | None:
    if spec.id == "huggingface":
        return "https://huggingface.co/api/whoami-v2", {"Authorization": f"Bearer {key}"}
    if spec.id == "pinecone":
        return "https://api.pinecone.io/indexes", {"Api-Key": key, "X-Pinecone-API-Version": "2025-01"}
    if spec.id == "supabase":
        base = (settings.supabase_url or "").rstrip("/")
        if not base:
            return None
        return f"{base}/rest/v1/", {"apikey": key, "Authorization": f"Bearer {key}"}
    if spec.openai_compatible and spec.base_url:
        return f"{spec.base_url.rstrip('/')}/models", {"Authorization": f"Bearer {key}"}
    return None


async def probe(provider_id: str) -> dict[str, Any]:
    """Cheap validity check (list models / whoami) — never burns generation quota."""
    spec = catalog.get(provider_id)
    if not spec:
        return {"ok": False, "provider": provider_id, "error": "unknown provider"}
    if spec.kind == "local":
        from jarvis.services import ollama

        health = await ollama.health()
        await usage.record_probe("ollama", ok=bool(health.get("ok")), detail=str(health.get("error") or "ok"))
        return {"ok": bool(health.get("ok")), "provider": "ollama", "detail": health}
    key = keys.resolve(provider_id)
    if not key:
        return {"ok": False, "provider": provider_id, "error": "no key", "signup_url": spec.signup_url}
    target = _probe_target(spec, key)
    if not target:
        return {"ok": None, "provider": provider_id, "detail": "no probe endpoint; will verify on first use"}
    url, headers = target
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:
        await usage.record_probe(provider_id, ok=False, detail=str(exc)[:200])
        return {"ok": False, "provider": provider_id, "error": str(exc)[:200]}
    latency = int((time.monotonic() - started) * 1000)
    ok = resp.status_code < 400
    detail = f"http {resp.status_code}"
    if ok:
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("data"):
                detail = f"{len(data['data'])} models"
            elif isinstance(data, dict) and data.get("name"):
                detail = f"user {data['name']}"
        except Exception:
            pass
        await usage.clear_cooldown(provider_id)
    elif resp.status_code in (401, 403):
        detail = "key rejected"
        await usage.set_cooldown(provider_id, seconds=3600, reason="probe: key rejected")
    await usage.record_probe(provider_id, ok=ok, detail=detail)
    return {"ok": ok, "provider": provider_id, "status_code": resp.status_code, "detail": detail,
            "latency_ms": latency}


async def probe_all() -> dict[str, Any]:
    global _last_probe
    configured = keys.configured_providers()
    results = await asyncio.gather(*(probe(pid) for pid in configured), return_exceptions=True)
    out: dict[str, Any] = {}
    for pid, res in zip(configured, results):
        out[pid] = res if isinstance(res, dict) else {"ok": False, "error": str(res)[:200]}
    _last_probe = {"results": out, "probed_at": time.time()}
    return out


async def usage_rollup() -> dict[str, Any]:
    """Quota Keeper job: prune old rows, flag providers near their free ceilings."""
    pruned = await usage.prune(keep_days=30)
    summary = await usage.summary(days=1)
    warnings: list[str] = []
    for pid, data in summary["providers"].items():
        today = data.get("today") or {}
        pct = today.get("daily_percent")
        if today.get("exhausted"):
            warnings.append(f"{pid}: free limit hit ({', '.join(today['exhausted'])})")
        elif pct is not None and pct >= 80:
            warnings.append(f"{pid}: {pct}% of today's free requests used")
    if warnings:
        try:
            from jarvis.services import event_log

            await event_log.log_integration("providers", source="quota", detail="; ".join(warnings)[:400])
        except Exception:
            pass
    return {"pruned": pruned, "warnings": warnings}


def open_signup(provider_id: str) -> dict[str, Any]:
    spec = catalog.get(provider_id)
    if not spec or not spec.signup_url:
        return {"ok": False, "error": "no signup url"}
    opener = shutil.which("open")
    if not opener:
        return {"ok": False, "error": "cannot open browser on this host", "url": spec.signup_url}
    import subprocess

    subprocess.Popen([opener, spec.signup_url])
    return {"ok": True, "url": spec.signup_url, "env_keys": spec.env_keys}


async def overview() -> dict[str, Any]:
    """Everything the panel needs in one call."""
    report = keys.report()
    summary = await usage.summary(days=7)
    gw = await gateway.status()
    providers = []
    for pid, spec in catalog.PROVIDERS.items():
        match = next((r for r in report["present"] + report["missing"] if r["provider"] == pid), None)
        state = summary["state"].get(pid, {})
        usage_row = summary["providers"].get(pid, {})
        providers.append({
            "id": pid,
            "name": spec.name,
            "kind": spec.kind,
            "free": spec.free,
            "capabilities": spec.capabilities,
            "models": spec.models,
            "free_tier": spec.free_tier.model_dump(),
            "signup_url": spec.signup_url,
            "docs_url": spec.docs_url,
            "env_keys": spec.env_keys,
            "extra_settings": spec.extra_settings,
            "key": {k: match.get(k) for k in ("found", "source", "env_name", "masked", "reason")} if match else {},
            "state": {k: state.get(k) for k in ("cooling", "cooldown_until", "consecutive_errors", "last_ok_at",
                                                 "last_error", "last_probe_ok", "last_probe_at", "last_probe_detail")},
            "usage_7d": {k: usage_row.get(k) for k in ("calls", "errors", "tokens", "by_capability")},
            "today": usage_row.get("today"),
        })
    return {
        "mode": gw["mode"],
        "chains": gw["chains"],
        "last_decision": gw["last_decision"],
        "groups": catalog.capability_groups(),
        "providers": providers,
        "keys_file": report["keys_file"],
        "counts": report["counts"],
        "last_scan_at": _last_scan.get("scanned_at"),
        "last_probe_at": _last_probe.get("probed_at"),
    }


async def status_reply(*, voice: bool = False) -> str:
    """Plain-English answer for 'which AI keys do we have / how much have we used'."""
    report = keys.report()
    present = [r["provider"] for r in report["present"] if r["kind"] != "local"]
    missing = [r["provider"] for r in report["missing"]]
    summary = await usage.summary(days=1)
    busiest = sorted(((pid, d["calls"]) for pid, d in summary["providers"].items() if d["calls"]),
                     key=lambda x: x[1], reverse=True)[:3]
    parts = []
    if present:
        parts.append(f"Free keys ready: {', '.join(present)}.")
    else:
        parts.append("No free provider keys yet — Ollama is doing everything locally.")
    if missing:
        parts.append(f"Missing: {', '.join(missing)}.")
    if busiest:
        parts.append("Today's usage: " + ", ".join(f"{p} {c} calls" for p, c in busiest) + ".")
    warnings = [pid for pid, d in summary["providers"].items() if (d.get("today") or {}).get("exhausted")]
    if warnings:
        parts.append(f"At free limit: {', '.join(warnings)}.")
    text = " ".join(parts)
    if voice:
        return text.replace("—", ",")
    return text + f"\nSave a key: POST /api/providers/keys {{provider, key}} → {report['keys_file']}"


async def shopping_list() -> list[dict[str, Any]]:
    """What to sign up for next, in the order it unlocks the most capabilities."""
    missing = keys.missing_providers()
    rows = []
    for spec in missing:
        rows.append({
            "provider": spec.id,
            "name": spec.name,
            "unlocks": spec.capabilities,
            "signup_url": spec.signup_url,
            "env_keys": spec.env_keys,
            "note": spec.free_tier.note,
        })
    rows.sort(key=lambda r: len(r["unlocks"]), reverse=True)
    return rows
