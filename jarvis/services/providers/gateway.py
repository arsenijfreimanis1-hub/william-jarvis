"""Capability-routed LLM gateway: free cloud keys first, Ollama always as the safety net."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from jarvis.config import settings
from jarvis.services.providers import catalog, keys, usage

log = logging.getLogger("jarvis.providers.gateway")

CHAT_CAPABILITIES = frozenset({"chat", "fast", "code", "reason", "long_context", "cad"})

# Observability for the panel / voice replies.
last_decision: dict[str, Any] = {}


class GatewayError(RuntimeError):
    pass


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _messages_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages)


def mode() -> str:
    raw = (settings.gateway_mode or "free_cloud_first").strip().lower()
    if raw not in ("free_cloud_first", "local_first", "local_only"):
        return "free_cloud_first"
    return raw


def infer_capability(text: str, *, kind: str | None = None, long: bool = False) -> str:
    """Map router intents / prompt shape onto a gateway capability."""
    if long or _estimate_tokens(text) > 6000:
        return "long_context"
    if kind == "code":
        return "code"
    if kind in ("fact", "reason"):
        return "reason"
    lowered = (text or "").lower()
    if any(w in lowered for w in ("openscad", "blender", "bpy", "3d model", "stl", ".step", "cad")):
        return "cad"
    if any(w in lowered for w in ("boilerplate", "component", "html", "css", "react", "scaffold")):
        return "fast"
    return "chat"


async def chain(capability: str, *, exclude: set[str] | None = None) -> list[tuple[catalog.ProviderSpec, str]]:
    """Ordered (provider, model) pairs that have a key, are not cooling, and have headroom."""
    exclude = exclude or set()
    out: list[tuple[catalog.ProviderSpec, str]] = []
    cloud: list[tuple[catalog.ProviderSpec, str]] = []
    for spec in catalog.providers_for(capability):
        if spec.id in exclude:
            continue
        model = spec.model_for(capability)
        if not model:
            continue
        if spec.kind == "local":
            continue
        if not keys.resolve(spec.id):
            continue
        if await usage.is_cooling(spec.id):
            continue
        head = await usage.headroom(spec.id)
        if not head["available"]:
            continue
        cloud.append((spec, model, head.get("daily_percent") or 0.0))
    # Key rotation: providers past the soft ceiling move behind fresher ones (stable sort), so
    # new tasks start on a key with room while an in-flight trace keeps its pinned provider.
    soft = float(getattr(settings, "gateway_soft_ceiling_percent", 80.0))
    cloud.sort(key=lambda item: 1 if item[2] >= soft else 0)
    cloud = [(spec, model) for spec, model, _ in cloud]
    local = (catalog.PROVIDERS["ollama"], settings.ollama_model)
    current = mode()
    if current == "local_only":
        return [local] if "ollama" not in exclude else []
    if current == "local_first":
        out.append(local)
        out.extend(cloud)
        return out
    out.extend(cloud)
    if "ollama" not in exclude:
        out.append(local)
    return out


def _headers(spec: catalog.ProviderSpec, key: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if spec.id == "openrouter":
        headers["HTTP-Referer"] = "http://127.0.0.1:8787"
        headers["X-Title"] = settings.agent_name
    return headers


def _chat_url(spec: catalog.ProviderSpec) -> str:
    base = spec.base_url.rstrip("/")
    if spec.id == "huggingface":
        return f"{base}/v1/chat/completions"
    return f"{base}/chat/completions"


def _cooldown_for(status_code: int | None, retry_after: str | None) -> int:
    if retry_after:
        try:
            return max(5, int(float(retry_after)))
        except ValueError:
            pass
    if status_code == 429:
        return 90
    if status_code in (401, 403):
        return 3600
    if status_code and status_code >= 500:
        return 120
    return 45


async def call_openai_compatible(
    spec: catalog.ProviderSpec,
    model: str,
    *,
    messages: list[dict],
    system: str | None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    capability: str = "chat",
    source: str | None = None,
) -> dict[str, Any]:
    key = keys.resolve(spec.id)
    if not key:
        raise GatewayError(f"{spec.id}: no key")
    built: list[dict] = []
    if system:
        built.append({"role": "system", "content": system})
    built.extend({"role": m["role"], "content": m["content"]} for m in messages if m.get("content"))
    payload: dict[str, Any] = {"model": model, "messages": built, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    started = time.monotonic()
    status_code: int | None = None
    try:
        async with httpx.AsyncClient(timeout=float(settings.gateway_timeout_seconds)) as client:
            resp = await client.post(_chat_url(spec), json=payload, headers=_headers(spec, key))
            status_code = resp.status_code
            latency = int((time.monotonic() - started) * 1000)
            if resp.status_code >= 400:
                detail = resp.text[:300]
                await usage.record(
                    spec.id, capability=capability, ok=False, model=model, status_code=resp.status_code,
                    latency_ms=latency, error=detail, source=source,
                )
                await usage.set_cooldown(
                    spec.id,
                    seconds=_cooldown_for(resp.status_code, resp.headers.get("retry-after")),
                    reason=f"http {resp.status_code}",
                )
                raise GatewayError(f"{spec.id} {resp.status_code}: {detail}")
            data = resp.json()
    except GatewayError:
        raise
    except Exception as exc:
        latency = int((time.monotonic() - started) * 1000)
        await usage.record(
            spec.id, capability=capability, ok=False, model=model, status_code=status_code,
            latency_ms=latency, error=str(exc), source=source,
        )
        await usage.set_cooldown(spec.id, seconds=45, reason=str(exc)[:120])
        raise GatewayError(f"{spec.id}: {exc}") from exc

    choices = data.get("choices") or []
    content = ""
    if choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    used = data.get("usage") or {}
    prompt_tokens = int(used.get("prompt_tokens") or _estimate_tokens(_messages_text(built)))
    completion_tokens = int(used.get("completion_tokens") or _estimate_tokens(content))
    await usage.record(
        spec.id, capability=capability, ok=True, model=model, status_code=status_code,
        latency_ms=latency, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, source=source,
    )
    return {"reply": content.strip(), "provider": spec.id, "model": model, "latency_ms": latency,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


async def _call_ollama(
    *, messages: list[dict], system: str | None, capability: str, source: str | None
) -> dict[str, Any]:
    from jarvis.services import ollama

    started = time.monotonic()
    try:
        reply = await ollama.local_chat(system=system, messages=messages)
    except Exception as exc:
        await usage.record("ollama", capability=capability, ok=False, model=settings.ollama_model,
                           latency_ms=int((time.monotonic() - started) * 1000), error=str(exc), source=source)
        raise GatewayError(f"ollama: {exc}") from exc
    latency = int((time.monotonic() - started) * 1000)
    await usage.record(
        "ollama", capability=capability, ok=True, model=settings.ollama_model, latency_ms=latency,
        prompt_tokens=_estimate_tokens(_messages_text(messages) + (system or "")),
        completion_tokens=_estimate_tokens(reply), source=source,
    )
    return {"reply": reply.strip(), "provider": "ollama", "model": settings.ollama_model, "latency_ms": latency}


async def _call_vigil(*, messages: list[dict], system: str | None, capability: str, source: str | None) -> dict:
    from jarvis.services import vigil_proxy

    started = time.monotonic()
    try:
        reply = await vigil_proxy.chat(system=system, messages=messages)
    except Exception as exc:
        await usage.record("vigil", capability=capability, ok=False, error=str(exc), source=source)
        raise GatewayError(f"vigil: {exc}") from exc
    await usage.record("vigil", capability=capability, ok=True, latency_ms=int((time.monotonic() - started) * 1000),
                       prompt_tokens=_estimate_tokens(_messages_text(messages)), completion_tokens=_estimate_tokens(reply),
                       source=source)
    return {"reply": reply.strip(), "provider": "vigil", "model": settings.vigil_proxy_provider}


def _with_personality(system: str | None, capability: str) -> str | None:
    try:
        from jarvis.brain import persona as _persona

        persona = _persona.prompt().strip()
    except Exception:
        persona = (settings.personality_prompt or "").strip()
    if not persona or capability not in ("chat", "reason", "long_context"):
        return system
    if system and persona in system:
        return system
    return f"{persona}\n\n{system}" if system else persona


async def chat_detailed(
    messages: list[dict] | None = None,
    *,
    prompt: str | None = None,
    system: str | None = None,
    capability: str = "chat",
    temperature: float = 0.1,
    max_tokens: int | None = None,
    source: str | None = None,
    prefer: str | None = None,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    """Try the capability chain in order; return reply plus which provider answered."""
    global last_decision
    msgs = list(messages or [])
    if prompt and not msgs:
        msgs = [{"role": "user", "content": prompt}]
    if not msgs:
        raise ValueError("prompt or messages required")
    if capability not in CHAT_CAPABILITIES:
        capability = "chat"
    system = _with_personality(system, capability)

    ordered = await chain(capability, exclude=exclude)
    # Within one trace (task) stick to the provider that already answered: never rotate mid-stream.
    pinned = None
    try:
        from jarvis.services import spans

        root = spans.root()
        pinned = (root.metadata.get("pinned_provider") if root else None)
    except Exception:
        root = None
    if pinned and not prefer and any(p.id == pinned for p, _ in ordered):
        prefer = pinned
    if prefer:
        ordered.sort(key=lambda pair: 0 if pair[0].id == prefer else 1)

    from jarvis.services import vigil_proxy

    steps: list[Any] = list(ordered)
    if vigil_proxy.configured() and "vigil" not in (exclude or set()):
        # Paid keys through Vigil sit after free cloud but before the local brain.
        insert_at = next((i for i, (s, _) in enumerate(steps) if s.id == "ollama"), len(steps))
        steps.insert(insert_at, ("vigil", None))

    attempts: list[dict[str, Any]] = []
    for step in steps:
        if step[0] == "vigil":
            try:
                result = await _call_vigil(messages=msgs, system=system, capability=capability, source=source)
            except GatewayError as exc:
                attempts.append({"provider": "vigil", "error": str(exc)[:200]})
                continue
        else:
            spec, model = step
            try:
                if spec.id == "ollama":
                    result = await _call_ollama(messages=msgs, system=system, capability=capability, source=source)
                else:
                    result = await call_openai_compatible(
                        spec, model, messages=msgs, system=system, temperature=temperature,
                        max_tokens=max_tokens, capability=capability, source=source,
                    )
            except GatewayError as exc:
                attempts.append({"provider": spec.id, "model": model, "error": str(exc)[:200]})
                log.info("gateway %s failed for %s: %s", spec.id, capability, str(exc)[:160])
                continue
        if not result.get("reply"):
            attempts.append({"provider": result.get("provider"), "error": "empty reply"})
            continue
        result["capability"] = capability
        result["attempts"] = attempts
        last_decision = {k: v for k, v in result.items() if k != "reply"}
        if root is not None and result.get("provider") and result["provider"] != "ollama":
            root.metadata.setdefault("pinned_provider", result["provider"])
        return result

    last_decision = {"capability": capability, "attempts": attempts, "provider": None}
    raise GatewayError(
        "no provider could answer — " + "; ".join(f"{a.get('provider')}: {a.get('error')}" for a in attempts)
        if attempts
        else "no provider available (set a free key or start Ollama)"
    )


async def chat(
    messages: list[dict] | None = None,
    *,
    prompt: str | None = None,
    system: str | None = None,
    capability: str = "chat",
    temperature: float = 0.1,
    max_tokens: int | None = None,
    source: str | None = None,
    prefer: str | None = None,
) -> str:
    result = await chat_detailed(
        messages, prompt=prompt, system=system, capability=capability, temperature=temperature,
        max_tokens=max_tokens, source=source, prefer=prefer,
    )
    return result["reply"]


async def fim(prefix: str, suffix: str = "", *, max_tokens: int = 256) -> dict[str, Any]:
    """Fill-in-the-middle code completion (Codestral / Mistral)."""
    for pid in ("codestral", "mistral"):
        spec = catalog.PROVIDERS[pid]
        key = keys.resolve(pid)
        if not key or await usage.is_cooling(pid):
            continue
        url = f"{spec.base_url.rstrip('/')}/fim/completions"
        payload = {"model": "codestral-latest", "prompt": prefix, "suffix": suffix, "max_tokens": max_tokens,
                   "temperature": 0.0}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=_headers(spec, key))
                latency = int((time.monotonic() - started) * 1000)
                if resp.status_code >= 400:
                    await usage.record(pid, capability="code", ok=False, model="codestral-latest",
                                       status_code=resp.status_code, latency_ms=latency, error=resp.text[:200])
                    await usage.set_cooldown(pid, seconds=_cooldown_for(resp.status_code, None))
                    continue
                data = resp.json()
        except Exception as exc:
            await usage.record(pid, capability="code", ok=False, error=str(exc))
            continue
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        await usage.record(pid, capability="code", ok=True, model="codestral-latest", latency_ms=latency,
                           prompt_tokens=_estimate_tokens(prefix + suffix), completion_tokens=_estimate_tokens(text))
        return {"ok": True, "provider": pid, "completion": text}
    return {"ok": False, "error": "no FIM-capable provider (set MISTRAL_API_KEY or CODESTRAL_API_KEY)"}


async def status() -> dict[str, Any]:
    chains: dict[str, list[dict[str, str]]] = {}
    for cap in CHAT_CAPABILITIES | {"embed", "stt", "tts"}:
        try:
            pairs = await chain(cap)
        except Exception:
            pairs = []
        chains[cap] = [{"provider": s.id, "model": m} for s, m in pairs]
    return {
        "mode": mode(),
        "configured": keys.configured_providers(),
        "chains": chains,
        "last_decision": last_decision,
        "personality_prompt_set": bool((settings.personality_prompt or "").strip()),
    }
