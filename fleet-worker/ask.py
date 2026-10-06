#!/usr/bin/env python3
"""Ask William's free-provider gateway from any device — keys stay on the Mac Mini.

    export JARVIS_CORE_URL=http://<mac-mini-lan-ip>:8787
    export JARVIS_FLEET_TOKEN=some-shared-secret

    python3 ask.py "write a React navbar component"            # capability auto → code/fast
    python3 ask.py --cap long_context -f src/*.py "find bugs"   # feed files into Gemini 1M ctx
    python3 ask.py --stt recording.wav                          # Groq Whisper transcription
    python3 ask.py --tts "Hello boss" -o hello.aiff             # speech synthesis
    python3 ask.py --cad "20mm cube with 5mm centre hole"       # OpenSCAD via free code model
    python3 ask.py --status                                     # keys / usage overview
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

URL = os.environ.get("JARVIS_CORE_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ.get("JARVIS_FLEET_TOKEN", "")


def _headers(extra: dict | None = None) -> dict:
    h = {"Accept": "application/json"}
    if TOKEN:
        h["X-Jarvis-Fleet-Token"] = TOKEN
    h.update(extra or {})
    return h


def _json(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps({**(body or {}), **({"token": TOKEN} if TOKEN and body is not None else {})}).encode() \
        if body is not None else None
    req = urllib.request.Request(f"{URL}{path}", data=data, method=method,
                                 headers=_headers({"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} → {exc.code}: {exc.read().decode(errors='replace')[:400]}")


def _multipart(path: str, file_path: Path, field: str = "file") -> dict:
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{file_path.name}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    query = f"?token={TOKEN}" if TOKEN else ""
    req = urllib.request.Request(f"{URL}{path}{query}", data=body, method="POST",
                                 headers=_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}))
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode() or "{}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ask William's free-provider gateway")
    p.add_argument("prompt", nargs="*", help="prompt text")
    p.add_argument("--cap", default=None, help="chat | fast | code | reason | long_context | cad")
    p.add_argument("--prefer", default=None, help="provider id to try first (gemini, groq, mistral, …)")
    p.add_argument("--system", default=None)
    p.add_argument("-f", "--file", action="append", default=[], help="attach file contents to the prompt")
    p.add_argument("--stt", metavar="AUDIO", help="transcribe an audio file")
    p.add_argument("--tts", metavar="TEXT", help="synthesize speech")
    p.add_argument("-o", "--out", default=None, help="output path for --tts audio")
    p.add_argument("--cad", metavar="PROMPT", help="generate OpenSCAD / Blender script")
    p.add_argument("--status", action="store_true", help="show provider keys + usage")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    args = p.parse_args(argv)

    if args.status:
        data = _json("GET", "/api/providers")
        if args.json:
            print(json.dumps(data, indent=2))
            return 0
        print(f"mode: {data['mode']}")
        for prov in data["providers"]:
            key = prov.get("key") or {}
            today = prov.get("today") or {}
            flag = "✓" if key.get("found") else "✗"
            print(f" {flag} {prov['id']:<12} {today.get('calls_today', 0):>4} calls today  "
                  f"{', '.join(prov['capabilities'])}")
            if not key.get("found") and prov.get("signup_url"):
                print(f"     → {prov['signup_url']}  ({', '.join(prov['env_keys'][:1])})")
        return 0

    if args.stt:
        data = _multipart("/api/speech/transcribe", Path(args.stt))
        print(json.dumps(data, indent=2) if args.json else data.get("text") or data)
        return 0 if data.get("ok") else 1

    if args.tts:
        body = json.dumps({"text": args.tts, "token": TOKEN}).encode()
        req = urllib.request.Request(f"{URL}/api/speech/tts", data=body, method="POST",
                                     headers=_headers({"Content-Type": "application/json"}))
        with urllib.request.urlopen(req, timeout=180) as resp:
            audio = resp.read()
            provider = resp.headers.get("X-Provider", "")
            ctype = resp.headers.get("Content-Type", "audio/aiff")
        ext = ".wav" if "wav" in ctype else ".mp3" if "mpeg" in ctype else ".flac" if "flac" in ctype else ".aiff"
        out = Path(args.out or f"tts{ext}")
        out.write_bytes(audio)
        print(f"{out} ({provider}, {len(audio)} bytes)")
        return 0

    if args.cad:
        data = _json("POST", "/api/cad/generate", {"prompt": args.cad})
        print(json.dumps(data, indent=2))
        return 0 if data.get("ok") else 1

    prompt = " ".join(args.prompt).strip()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        p.print_help()
        return 2
    attachments = []
    for pattern in args.file:
        for fp in sorted(Path().glob(pattern)) or [Path(pattern)]:
            if fp.is_file():
                attachments.append(f"\n\n--- {fp} ---\n{fp.read_text(encoding='utf-8', errors='replace')}")
    cap = args.cap or ("long_context" if attachments else "chat")
    data = _json("POST", "/api/gateway/chat", {
        "message": prompt + "".join(attachments), "capability": cap, "prefer": args.prefer,
        "system": args.system, "source": f"ask.{os.uname().nodename if hasattr(os, 'uname') else 'device'}",
    })
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(data.get("reply", ""))
        print(f"\n[{data.get('provider')} / {data.get('model')} · {data.get('latency_ms')} ms]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
