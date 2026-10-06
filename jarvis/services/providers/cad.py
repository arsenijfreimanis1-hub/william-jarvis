"""3D CAD via free models: generate OpenSCAD / Blender scripts, compile locally, or pull meshes from HF Spaces."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.config import settings
from jarvis.services.providers import gateway, keys, usage

log = logging.getLogger("jarvis.providers.cad")

OPENSCAD_CANDIDATES = (
    "openscad",
    "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
    "/opt/homebrew/bin/openscad",
    "/usr/local/bin/openscad",
)
BLENDER_CANDIDATES = (
    "blender",
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/opt/homebrew/bin/blender",
)

OPENSCAD_SYSTEM = """You are an expert OpenSCAD engineer. Output ONLY valid OpenSCAD code, no markdown fences,
no commentary. Use millimetres. Parameterise key dimensions as variables at the top. Use $fn=64 for round parts.
Make the model manifold and printable (no zero-thickness walls). Every top-level object must be a single union."""

BLENDER_SYSTEM = """You are an expert Blender Python (bpy) engineer. Output ONLY a complete Python script, no markdown
fences, no commentary. The script runs headless with `blender -b -P script.py`. Clear the default scene, build the
requested object with primitives/modifiers, apply transforms, then export with
bpy.ops.wm.obj_export(filepath=OUTPUT_PATH) if available else bpy.ops.export_scene.obj(filepath=OUTPUT_PATH).
Read OUTPUT_PATH from the environment variable JARVIS_CAD_OUTPUT."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()[:40]).strip("-") or "model"


def _out_dir(prompt: str) -> Path:
    base = Path(settings.cad_output_dir)
    d = base / f"{_slug(prompt)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_tool(candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if "/" in c:
            if Path(c).exists():
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None


def openscad_bin() -> str | None:
    return _find_tool(OPENSCAD_CANDIDATES)


def blender_bin() -> str | None:
    return _find_tool(BLENDER_CANDIDATES)


def _strip_fences(code: str) -> str:
    code = code.strip()
    code = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n", "", code)
    code = re.sub(r"\n```\s*$", "", code)
    return code.strip() + "\n"


async def generate_openscad(prompt: str, *, render: bool = True) -> dict[str, Any]:
    """Prompt → .scad (via free code model) → .stl when the OpenSCAD CLI is installed."""
    out_dir = _out_dir(prompt)
    try:
        result = await gateway.chat_detailed(
            prompt=f"Design request: {prompt}\n\nReturn the OpenSCAD source only.",
            system=OPENSCAD_SYSTEM,
            capability="cad",
            source="cad.openscad",
            temperature=0.1,
        )
    except Exception as exc:
        return {"ok": False, "error": f"model generation failed: {exc}"[:300]}
    code = _strip_fences(result["reply"])
    scad_path = out_dir / "model.scad"
    scad_path.write_text(code, encoding="utf-8")
    payload: dict[str, Any] = {
        "ok": True, "format": "openscad", "scad_path": str(scad_path), "provider": result["provider"],
        "model": result["model"], "dir": str(out_dir),
    }
    if render:
        payload["render"] = await render_openscad(scad_path)
    return payload


async def render_openscad(scad_path: str | Path, *, fmt: str = "stl") -> dict[str, Any]:
    binary = openscad_bin()
    scad = Path(scad_path)
    if not binary:
        return {"ok": False, "error": "OpenSCAD not installed — brew install --cask openscad", "skipped": True}
    out = scad.with_suffix(f".{fmt}")
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        binary, "-o", str(out), str(scad), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "error": "openscad render timed out"}
    latency = int((time.monotonic() - started) * 1000)
    ok = proc.returncode == 0 and out.exists()
    await usage.record("openscad", capability="cad", ok=ok, latency_ms=latency,
                       error=None if ok else err.decode()[:200])
    if not ok:
        return {"ok": False, "error": err.decode()[-400:] or "render failed"}
    return {"ok": True, "path": str(out), "bytes": out.stat().st_size, "latency_ms": latency}


async def generate_blender(prompt: str, *, run: bool = True) -> dict[str, Any]:
    """Prompt → bpy script → .obj when Blender is installed."""
    out_dir = _out_dir(prompt)
    try:
        result = await gateway.chat_detailed(
            prompt=f"Build this in Blender: {prompt}\n\nReturn the Python script only.",
            system=BLENDER_SYSTEM,
            capability="cad",
            source="cad.blender",
            temperature=0.1,
        )
    except Exception as exc:
        return {"ok": False, "error": f"model generation failed: {exc}"[:300]}
    script = _strip_fences(result["reply"])
    script_path = out_dir / "build.py"
    script_path.write_text(script, encoding="utf-8")
    payload: dict[str, Any] = {
        "ok": True, "format": "blender", "script_path": str(script_path), "provider": result["provider"],
        "model": result["model"], "dir": str(out_dir),
    }
    if run:
        payload["render"] = await run_blender(script_path, out_dir / "model.obj")
    return payload


async def run_blender(script_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    binary = blender_bin()
    if not binary:
        return {"ok": False, "error": "Blender not installed — brew install --cask blender", "skipped": True}
    import os

    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        binary, "-b", "-P", str(script_path),
        env={**os.environ, "JARVIS_CAD_OUTPUT": str(output_path)},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "error": "blender run timed out"}
    latency = int((time.monotonic() - started) * 1000)
    ok = proc.returncode == 0 and Path(output_path).exists()
    await usage.record("blender", capability="cad", ok=ok, latency_ms=latency,
                       error=None if ok else (err or out).decode()[:200])
    if not ok:
        return {"ok": False, "error": (err or out).decode()[-400:] or "blender failed"}
    return {"ok": True, "path": str(output_path), "latency_ms": latency}


def gradio_available() -> bool:
    try:
        import gradio_client  # noqa: F401

        return True
    except Exception:
        return False


async def mesh_from_space(*, image_path: str | None = None, prompt: str | None = None,
                          space: str | None = None) -> dict[str, Any]:
    """Send an image (or prompt) to a public HF Space (TripoSR / InstantMesh) and download the mesh."""
    if not gradio_available():
        return {"ok": False, "error": "gradio_client not installed — .venv/bin/pip install gradio_client",
                "skipped": True}
    space_id = space or settings.hf_mesh_space
    token = keys.resolve("huggingface") or None
    if not image_path and not prompt:
        return {"ok": False, "error": "image_path or prompt required"}
    out_dir = _out_dir(prompt or Path(image_path or "image").stem)

    def _run() -> dict[str, Any]:
        from gradio_client import Client, handle_file

        client = Client(space_id, hf_token=token)
        if image_path:
            # TripoSR-style spaces: preprocess then generate; fall back to a single predict call.
            try:
                processed = client.predict(handle_file(image_path), True, 0.85, api_name="/preprocess")
                result = client.predict(processed, 256, api_name="/generate")
            except Exception:
                result = client.predict(handle_file(image_path))
        else:
            result = client.predict(prompt)
        paths: list[str] = []
        items = result if isinstance(result, (list, tuple)) else [result]
        for item in items:
            p = item if isinstance(item, str) else (item.get("path") if isinstance(item, dict) else None)
            if p and Path(p).exists():
                dest = out_dir / Path(p).name
                shutil.copy(p, dest)
                paths.append(str(dest))
        return {"ok": bool(paths), "paths": paths}

    started = time.monotonic()
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_run), timeout=900)
    except Exception as exc:
        await usage.record("huggingface", capability="mesh3d", ok=False, model=space_id, error=str(exc)[:200])
        return {"ok": False, "error": f"space call failed: {exc}"[:300], "space": space_id}
    await usage.record("huggingface", capability="mesh3d", ok=result["ok"], model=space_id,
                       latency_ms=int((time.monotonic() - started) * 1000))
    return {**result, "space": space_id, "dir": str(out_dir)}


async def generate(prompt: str, *, engine: str = "auto", image_path: str | None = None) -> dict[str, Any]:
    """One entry point: auto picks OpenSCAD for parametric parts, Blender for organic, Spaces for images."""
    engine = (engine or "auto").lower()
    if image_path or engine == "mesh":
        return await mesh_from_space(image_path=image_path, prompt=None if image_path else prompt)
    if engine == "blender":
        return await generate_blender(prompt)
    if engine == "openscad":
        return await generate_openscad(prompt)
    lowered = prompt.lower()
    organic = any(w in lowered for w in ("organic", "character", "sculpt", "creature", "smooth blob", "statue"))
    if organic and blender_bin():
        return await generate_blender(prompt)
    return await generate_openscad(prompt)


def status() -> dict[str, Any]:
    return {
        "openscad": openscad_bin(),
        "blender": blender_bin(),
        "gradio_client": gradio_available(),
        "hf_token": bool(keys.resolve("huggingface")),
        "mesh_space": settings.hf_mesh_space,
        "output_dir": str(settings.cad_output_dir),
        "viewer_skill": str(Path(settings.workspace_dir) / ".agents" / "skills" / "cad-viewer"),
    }
