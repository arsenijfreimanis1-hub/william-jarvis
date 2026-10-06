import httpx

from jarvis.config import settings


async def health() -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return {"ok": True, "models": models, "default": settings.ollama_model, "name": "Willy", "local": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "name": "Willy", "local": True}


async def chat(
    prompt: str | None = None,
    *,
    system: str | None = None,
    messages: list[dict] | None = None,
    capability: str = "chat",
    source: str | None = None,
) -> str:
    """William's brain entry point.

    Routed through the free-provider gateway (Gemini / Codestral / Groq / OpenRouter / HF …)
    according to `capability`; Ollama stays the always-available local fallback.
    """
    built: list[dict] = []
    if messages:
        built.extend(messages)
    elif prompt:
        built.append({"role": "user", "content": prompt})
    if not built:
        raise ValueError("prompt or messages required")

    from jarvis.services.providers import gateway

    return await gateway.chat(built, system=system, capability=capability, source=source)


async def local_chat(*, system: str | None = None, messages: list[dict]) -> str:
    """Raw Ollama call — used by the gateway as the final fallback."""
    built: list[dict] = list(messages)
    if system:
        built.insert(0, {"role": "system", "content": system})
    if not built:
        raise ValueError("messages required")

    from jarvis.services import resource_governor

    # 16 GB: one local model call in flight at a time (governor policy `ollama_single_flight`).
    async with resource_governor.ollama_gate():
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json={"model": settings.ollama_model, "messages": built, "stream": False,
                      "options": {"temperature": 0.1}},
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]


async def embed(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Local embeddings via Ollama (/api/embed). Falls back to the chat model if no embed model."""
    use_model = model or "nomic-embed-text"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{settings.ollama_base_url}/api/embed", json={"model": use_model, "input": texts})
        if resp.status_code == 404 and use_model != settings.ollama_model:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/embed", json={"model": settings.ollama_model, "input": texts}
            )
        resp.raise_for_status()
        return resp.json().get("embeddings") or []


async def vision(image_path: str, prompt: str) -> str:
    import base64
    from pathlib import Path

    data = base64.b64encode(Path(image_path).read_bytes()).decode()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_vision_model,
                "messages": [{"role": "user", "content": prompt, "images": [data]}],
                "stream": False,
                "options": {"temperature": 0.1},
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
