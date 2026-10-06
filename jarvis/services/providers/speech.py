"""Speech for both devices: STT (Groq Whisper → HF Whisper → local whisper.cpp) and TTS (HF → macOS say)."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import shutil
import time
import uuid
from pathlib import Path

import httpx

from jarvis.config import settings
from jarvis.paths import ROOT
from jarvis.services.providers import catalog, keys, usage

log = logging.getLogger("jarvis.providers.speech")

AUDIO_DIR = ROOT / "exports" / "audio"
LOCAL_WHISPER_SCRIPT = ROOT / "scripts" / "local-whisper-transcribe.sh"


def _audio_out_dir() -> Path:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIO_DIR


async def _groq_transcribe(path: Path, *, language: str | None) -> dict:
    spec = catalog.PROVIDERS["groq"]
    key = keys.resolve("groq")
    if not key or await usage.is_cooling("groq"):
        raise RuntimeError("groq unavailable")
    model = spec.models.get("stt", "whisper-large-v3-turbo")
    url = f"{spec.base_url.rstrip('/')}/audio/transcriptions"
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=120.0) as client:
        with path.open("rb") as fh:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                data=data,
                files={"file": (path.name, fh, mime)},
            )
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        await usage.record("groq", capability="stt", ok=False, model=model, status_code=resp.status_code,
                           latency_ms=latency, error=resp.text[:200])
        await usage.set_cooldown("groq", seconds=90 if resp.status_code == 429 else 45, reason=f"stt {resp.status_code}")
        raise RuntimeError(f"groq stt {resp.status_code}")
    text = (resp.json().get("text") or "").strip()
    await usage.record("groq", capability="stt", ok=True, model=model, status_code=resp.status_code, latency_ms=latency)
    return {"ok": True, "text": text, "provider": "groq", "model": model, "latency_ms": latency}


async def _hf_transcribe(path: Path) -> dict:
    spec = catalog.PROVIDERS["huggingface"]
    key = keys.resolve("huggingface")
    if not key or await usage.is_cooling("huggingface"):
        raise RuntimeError("huggingface unavailable")
    model = spec.models.get("stt", "openai/whisper-large-v3")
    url = f"{spec.base_url.rstrip('/')}/hf-inference/models/{model}"
    mime = mimetypes.guess_type(str(path))[0] or "audio/wav"
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": mime},
                                 content=path.read_bytes())
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        await usage.record("huggingface", capability="stt", ok=False, model=model, status_code=resp.status_code,
                           latency_ms=latency, error=resp.text[:200])
        await usage.set_cooldown("huggingface", seconds=120 if resp.status_code in (429, 503) else 45)
        raise RuntimeError(f"hf stt {resp.status_code}")
    text = (resp.json().get("text") or "").strip()
    await usage.record("huggingface", capability="stt", ok=True, model=model, status_code=resp.status_code,
                       latency_ms=latency)
    return {"ok": True, "text": text, "provider": "huggingface", "model": model, "latency_ms": latency}


def _local_whisper_available() -> bool:
    return LOCAL_WHISPER_SCRIPT.is_file() and bool(
        shutil.which("whisper-cli") or Path("/opt/homebrew/bin/whisper-cli").exists()
    )


async def _local_transcribe(path: Path) -> dict:
    if not _local_whisper_available():
        raise RuntimeError("local whisper-cli not installed (brew install whisper-cpp)")
    model_path = ROOT / "models" / "whisper" / "ggml-base.en.bin"
    env = {"WILLIAM_AUDIO_FILE": str(path), "WILLIAM_WHISPER_MODEL": str(model_path)}
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "bash", str(LOCAL_WHISPER_SCRIPT), env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    latency = int((time.monotonic() - started) * 1000)
    if proc.returncode != 0:
        await usage.record("local_whisper", capability="stt", ok=False, latency_ms=latency, error=err.decode()[:200])
        raise RuntimeError(err.decode()[:200] or "local whisper failed")
    await usage.record("local_whisper", capability="stt", ok=True, latency_ms=latency)
    return {"ok": True, "text": out.decode().strip(), "provider": "local_whisper", "latency_ms": latency}


async def transcribe(audio_path: str | Path, *, language: str | None = None) -> dict:
    """Voice command from either device → text. Free cloud first, local whisper.cpp last."""
    path = Path(audio_path)
    if not path.is_file():
        return {"ok": False, "error": f"audio file not found: {path}"}
    errors: list[str] = []
    for step in (lambda: _groq_transcribe(path, language=language), lambda: _hf_transcribe(path),
                 lambda: _local_transcribe(path)):
        try:
            result = await step()
            if result.get("text"):
                result["attempts"] = errors
                return result
            errors.append(f"{result.get('provider')}: empty")
        except Exception as exc:
            errors.append(str(exc)[:160])
    return {"ok": False, "error": "no transcription backend succeeded", "attempts": errors}


async def _hf_tts(text: str) -> dict:
    spec = catalog.PROVIDERS["huggingface"]
    key = keys.resolve("huggingface")
    if not key or await usage.is_cooling("huggingface"):
        raise RuntimeError("huggingface unavailable")
    model = spec.models.get("tts", "facebook/mms-tts-eng")
    url = f"{spec.base_url.rstrip('/')}/hf-inference/models/{model}"
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json={"inputs": text[:1500]})
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        await usage.record("huggingface", capability="tts", ok=False, model=model, status_code=resp.status_code,
                           latency_ms=latency, error=resp.text[:200])
        await usage.set_cooldown("huggingface", seconds=120 if resp.status_code in (429, 503) else 45)
        raise RuntimeError(f"hf tts {resp.status_code}")
    ctype = resp.headers.get("content-type", "audio/flac")
    ext = ".wav" if "wav" in ctype else ".mp3" if "mpeg" in ctype else ".flac"
    out = _audio_out_dir() / f"tts-{uuid.uuid4().hex[:10]}{ext}"
    out.write_bytes(resp.content)
    await usage.record("huggingface", capability="tts", ok=True, model=model, status_code=resp.status_code,
                       latency_ms=latency, prompt_tokens=len(text) // 4)
    return {"ok": True, "path": str(out), "provider": "huggingface", "model": model, "content_type": ctype}


async def _say_tts(text: str, *, voice: str | None = None) -> dict:
    if not shutil.which("say"):
        raise RuntimeError("macOS say not available")
    out = _audio_out_dir() / f"tts-{uuid.uuid4().hex[:10]}.aiff"
    cmd = ["say", "-o", str(out)]
    if voice:
        cmd += ["-v", voice]
    cmd.append(text[:2000])
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(err.decode()[:200] or "say failed")
    await usage.record("macos_say", capability="tts", ok=True, prompt_tokens=len(text) // 4)
    return {"ok": True, "path": str(out), "provider": "macos_say", "content_type": "audio/aiff"}


async def synthesize(text: str, *, prefer_local: bool = False, voice: str | None = None) -> dict:
    """Text → audio file. HF open-source TTS when a token exists, macOS `say` otherwise."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    steps = [lambda: _hf_tts(text), lambda: _say_tts(text, voice=voice)]
    if prefer_local:
        steps.reverse()
    errors: list[str] = []
    for step in steps:
        try:
            result = await step()
            result["attempts"] = errors
            return result
        except Exception as exc:
            errors.append(str(exc)[:160])
    return {"ok": False, "error": "no TTS backend succeeded", "attempts": errors}


def status() -> dict:
    return {
        "stt": {
            "groq": bool(keys.resolve("groq")),
            "huggingface": bool(keys.resolve("huggingface")),
            "local_whisper": _local_whisper_available(),
        },
        "tts": {"huggingface": bool(keys.resolve("huggingface")), "macos_say": bool(shutil.which("say"))},
        "audio_dir": str(AUDIO_DIR),
    }
