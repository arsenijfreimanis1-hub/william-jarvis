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
) -> str:
    built: list[dict] = []
    if messages:
        built.extend(messages)
    elif prompt:
        built.append({"role": "user", "content": prompt})

    from jarvis.services import vigil_proxy

    if vigil_proxy.configured():
        return await vigil_proxy.chat(system=system, messages=built, prompt=prompt)

    if system:
        built.insert(0, {"role": "system", "content": system})
    elif not built:
        raise ValueError("prompt or messages required")

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={"model": settings.ollama_model, "messages": built, "stream": False, "options": {"temperature": 0.1}},
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


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
