"""Find, validate, save and mask free provider API keys (Key Scout's hands)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from jarvis.config import settings
from jarvis.paths import ROOT
from jarvis.services.providers import catalog

log = logging.getLogger("jarvis.providers.keys")

_PLACEHOLDER = re.compile(r"(your[_-]?key|changeme|^x{3,}$|^[*•]{3,}$|\.\.\.$|^<.*>$|^\s*$)", re.I)


class KeyMatch(BaseModel):
    provider: str
    found: bool
    source: str | None = None
    env_name: str | None = None
    masked: str | None = None
    valid_format: bool = False
    reason: str | None = None


def mask(key: str) -> str:
    key = key.strip()
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def looks_valid(spec: catalog.ProviderSpec, key: str) -> tuple[bool, str | None]:
    key = (key or "").strip()
    if not key or _PLACEHOLDER.search(key):
        return False, "placeholder"
    if len(key) < spec.key_min_length:
        return False, f"shorter than {spec.key_min_length} chars"
    if spec.key_prefixes and not any(key.startswith(p) for p in spec.key_prefixes):
        return False, f"expected prefix {' or '.join(spec.key_prefixes)}"
    if re.search(r"\s", key):
        return False, "contains whitespace"
    return True, None


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        if not path.is_file():
            return out
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("env parse failed for %s: %s", path, exc)
    return out


def _keychain_lookup(service: str) -> str:
    if not settings.keychain_lookup_enabled:
        return ""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


def _config_file_candidates(spec: catalog.ProviderSpec) -> list[Path]:
    home = Path.home()
    names = {spec.id, spec.id.replace("huggingface", "hf")}
    paths: list[Path] = []
    for name in names:
        paths.append(home / ".config" / name / "api_key")
        paths.append(home / ".config" / name / "token")
    if spec.id == "huggingface":
        paths.append(home / ".cache" / "huggingface" / "token")
        paths.append(home / ".huggingface" / "token")
    return paths


def candidates(spec: catalog.ProviderSpec) -> list[tuple[str, str, str | None]]:
    """Yield (source, value, env_name) in priority order. Values are raw; validate afterwards."""
    found: list[tuple[str, str, str | None]] = []
    if spec.settings_attr:
        value = str(getattr(settings, spec.settings_attr, "") or "")
        if value:
            found.append(("settings", value, f"JARVIS_{spec.settings_attr.upper()}"))
    for env_name in spec.env_keys:
        value = os.environ.get(env_name, "")
        if value:
            found.append(("environ", value, env_name))
    for label, path in (("keys.env", Path(settings.keys_file)), (".env", ROOT / ".env")):
        parsed = _parse_env_file(path)
        for env_name in spec.env_keys:
            value = parsed.get(env_name, "")
            if value:
                found.append((label, value, env_name))
    for path in _config_file_candidates(spec):
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    found.append((f"file:{path}", value, None))
        except Exception:
            continue
    kc = _keychain_lookup(f"jarvis-{spec.id}")
    if kc:
        found.append(("keychain", kc, f"jarvis-{spec.id}"))
    return found


def resolve(provider_id: str) -> str:
    """Return the first format-valid key for a provider, or ''."""
    spec = catalog.get(provider_id)
    if not spec:
        return ""
    for _source, value, _env in candidates(spec):
        ok, _ = looks_valid(spec, value)
        if ok:
            return value.strip()
    return ""


def inspect(provider_id: str) -> KeyMatch:
    spec = catalog.get(provider_id)
    if not spec:
        return KeyMatch(provider=provider_id, found=False, reason="unknown provider")
    if spec.kind == "local":
        return KeyMatch(provider=provider_id, found=True, source="local", valid_format=True)
    last_reason: str | None = None
    for source, value, env_name in candidates(spec):
        ok, reason = looks_valid(spec, value)
        if ok:
            return KeyMatch(
                provider=provider_id,
                found=True,
                source=source,
                env_name=env_name,
                masked=mask(value),
                valid_format=True,
            )
        last_reason = f"{source}: {reason}"
    return KeyMatch(provider=provider_id, found=False, reason=last_reason or "not found")


def scan_all() -> dict[str, KeyMatch]:
    return {pid: inspect(pid) for pid in catalog.PROVIDERS}


def configured_providers() -> list[str]:
    return [pid for pid, m in scan_all().items() if m.found and m.valid_format]


def missing_providers() -> list[catalog.ProviderSpec]:
    out: list[catalog.ProviderSpec] = []
    for pid, match in scan_all().items():
        spec = catalog.PROVIDERS[pid]
        if spec.kind != "local" and not match.found:
            out.append(spec)
    return out


def save_key(provider_id: str, key: str, *, env_name: str | None = None) -> dict:
    """Persist a key to the private keys file (0600). Never writes into the repo .env."""
    spec = catalog.get(provider_id)
    if not spec or spec.kind == "local":
        return {"ok": False, "error": "unknown provider"}
    ok, reason = looks_valid(spec, key)
    if not ok:
        return {"ok": False, "error": f"key rejected: {reason}"}
    name = env_name or (spec.env_keys[0] if spec.env_keys else f"JARVIS_{provider_id.upper()}_API_KEY")
    path = Path(settings.keys_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _parse_env_file(path)
    existing[name] = key.strip()
    body = "# William free-provider keys — managed by Key Scout. chmod 600.\n" + "".join(
        f"{k}={v}\n" for k, v in sorted(existing.items())
    )
    path.write_text(body, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    # Make the key visible to the running process immediately.
    os.environ[name] = key.strip()
    return {"ok": True, "provider": provider_id, "env_name": name, "masked": mask(key), "path": str(path)}


def remove_key(provider_id: str) -> dict:
    spec = catalog.get(provider_id)
    if not spec:
        return {"ok": False, "error": "unknown provider"}
    path = Path(settings.keys_file)
    existing = _parse_env_file(path)
    removed = [k for k in spec.env_keys if existing.pop(k, None) is not None]
    for k in spec.env_keys:
        os.environ.pop(k, None)
    if path.exists():
        path.write_text(
            "# William free-provider keys — managed by Key Scout. chmod 600.\n"
            + "".join(f"{k}={v}\n" for k, v in sorted(existing.items())),
            encoding="utf-8",
        )
    return {"ok": True, "removed": removed}


def report() -> dict:
    """Human + machine readable summary for the panel and voice replies."""
    matches = scan_all()
    present = []
    missing = []
    for pid, m in matches.items():
        spec = catalog.PROVIDERS[pid]
        row = {
            "provider": pid,
            "name": spec.name,
            "kind": spec.kind,
            "capabilities": spec.capabilities,
            "found": m.found,
            "source": m.source,
            "env_name": m.env_name,
            "masked": m.masked,
            "reason": m.reason,
            "signup_url": spec.signup_url,
            "env_keys": spec.env_keys,
            "extra_settings": spec.extra_settings,
        }
        (present if m.found else missing).append(row)
    return {
        "present": present,
        "missing": missing,
        "keys_file": str(settings.keys_file),
        "counts": {"present": len(present), "missing": len(missing)},
    }
