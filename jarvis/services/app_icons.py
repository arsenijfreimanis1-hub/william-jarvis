"""Generate and apply app icons for every app William builds or ships.

Stdlib only (no Pillow) so installer shell scripts can run it directly:

    python3 jarvis/services/app_icons.py --target macos-helper/WilliamKiosk.app --name "William Agent"
    python3 jarvis/services/app_icons.py --all

Pipeline: SVG (gradient rounded square + initials) → PNG (qlmanage on macOS, pure-Python fallback)
→ .icns via iconutil (macOS) → applied to .app bundles (CFBundleIconFile) or project folders
(web favicon / PWA icons, Swift resources, generic assets/).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ICON_CACHE = ROOT / "exports" / "icons"

PALETTES: tuple[tuple[str, str], ...] = (
    ("#0f172a", "#2563eb"),  # midnight → blue
    ("#111827", "#7c3aed"),  # graphite → violet
    ("#052e16", "#16a34a"),  # forest → green
    ("#431407", "#ea580c"),  # ember → orange
    ("#0c4a6e", "#06b6d4"),  # deep sea → cyan
    ("#3f0d2a", "#db2777"),  # plum → pink
    ("#1c1917", "#f59e0b"),  # charcoal → amber
    ("#082f49", "#14b8a6"),  # navy → teal
)

# 5x7 bitmap font for the pure-Python fallback (A-Z, 0-9).
_FONT: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "01010", "00100", "00100", "00100", "01010", "10001"),
    "Y": ("10001", "01010", "00100", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10000", "11110", "10001", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
}

ICONSET_SIZES = (16, 32, 128, 256, 512)
WEB_SIZES = (32, 180, 192, 512)


# ─── Design ─────────────────────────────────────────────────────────────────


def initials_for(name: str) -> str:
    words = [w for w in re.split(r"[\s_\-./]+", name.strip()) if w]
    words = [w for w in words if not w.isdigit()] or words
    words = [w for w in words if w.lower() not in ("the", "a", "an", "willy", "build")] or words
    if not words:
        return "W"
    if len(words) == 1:
        w = re.sub(r"[^A-Za-z0-9]", "", words[0])
        return (w[:2] if len(w) > 1 and w[:2].isupper() else w[:1]).upper() or "W"
    return (words[0][0] + words[1][0]).upper()


def palette_for(seed: str) -> tuple[str, str]:
    digest = hashlib.sha256(seed.strip().lower().encode("utf-8")).digest()
    return PALETTES[digest[0] % len(PALETTES)]


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def build_svg(name: str, *, initials: str | None = None, seed: str | None = None, size: int = 1024) -> str:
    c1, c2 = palette_for(seed or name)
    text = initials or initials_for(name)
    radius = int(size * 0.22)
    font_size = int(size * (0.46 if len(text) == 1 else 0.36))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.3" cy="0.25" r="0.8">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.22"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect x="{int(size * 0.06)}" y="{int(size * 0.06)}" width="{int(size * 0.88)}" height="{int(size * 0.88)}" rx="{radius}" fill="url(#bg)"/>
  <rect x="{int(size * 0.06)}" y="{int(size * 0.06)}" width="{int(size * 0.88)}" height="{int(size * 0.88)}" rx="{radius}" fill="url(#glow)"/>
  <circle cx="{int(size * 0.80)}" cy="{int(size * 0.21)}" r="{int(size * 0.035)}" fill="#ffffff" fill-opacity="0.85"/>
  <text x="50%" y="50%" dy="{int(font_size * 0.36)}" text-anchor="middle" font-family="-apple-system, 'SF Pro Display', 'Helvetica Neue', Helvetica, Arial, sans-serif" font-weight="700" font-size="{font_size}" fill="#ffffff" letter-spacing="{-int(font_size * 0.04)}">{text}</text>
</svg>
"""


# ─── Rasterising ────────────────────────────────────────────────────────────


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def render_png_pure(size: int, *, initials: str, c1: str, c2: str) -> bytes:
    """Dependency-free RGBA PNG: gradient rounded square + bitmap initials. Slow but always works."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    pad = size * 0.06
    side = size * 0.88
    radius = side * 0.25
    inner_x0, inner_y0 = pad + radius, pad + radius
    inner_x1, inner_y1 = pad + side - radius, pad + side - radius

    glyphs = [_FONT.get(ch, _FONT["W"]) for ch in initials[:2]] or [_FONT["W"]]
    cols = 5 * len(glyphs) + (len(glyphs) - 1)
    rows = 7
    cell = int(side * (0.48 if len(glyphs) == 1 else 0.70) / cols)
    cell = max(1, cell)
    text_w, text_h = cols * cell, rows * cell
    tx0 = int((size - text_w) / 2)
    ty0 = int((size - text_h) / 2)
    lit: set[tuple[int, int]] = set()
    for gi, glyph in enumerate(glyphs):
        for ry, row in enumerate(glyph):
            for rx, bit in enumerate(row):
                if bit == "1":
                    lit.add((gi * 6 + rx, ry))

    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter none
        for x in range(size):
            # inside rounded square?
            cx = min(max(x, inner_x0), inner_x1)
            cy = min(max(y, inner_y0), inner_y1)
            inside = (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius
            if not inside or x < pad or y < pad or x >= pad + side or y >= pad + side:
                raw += b"\x00\x00\x00\x00"
                continue
            t = (x + y) / (2 * size)
            r = int(r1 + (r2 - r1) * t)
            g = int(g1 + (g2 - g1) * t)
            b = int(b1 + (b2 - b1) * t)
            if tx0 <= x < tx0 + text_w and ty0 <= y < ty0 + text_h:
                col = (x - tx0) // cell
                row = (y - ty0) // cell
                if (col, row) in lit:
                    r, g, b = 255, 255, 255
            raw += bytes((r, g, b, 255))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def _render_with_qlmanage(svg_path: Path, size: int, out_png: Path) -> bool:
    if not shutil.which("qlmanage"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                ["qlmanage", "-t", "-s", str(size), "-o", tmp, str(svg_path)],
                capture_output=True, timeout=30, check=False,
            )
        except Exception:
            return False
        produced = Path(tmp) / f"{svg_path.name}.png"
        if produced.is_file() and produced.stat().st_size > 500:
            shutil.copy(produced, out_png)
            return True
    return False


def _render_with_rsvg(svg_path: Path, size: int, out_png: Path) -> bool:
    binary = shutil.which("rsvg-convert")
    if not binary:
        return False
    try:
        proc = subprocess.run([binary, "-w", str(size), "-h", str(size), "-o", str(out_png), str(svg_path)],
                              capture_output=True, timeout=30)
        return proc.returncode == 0 and out_png.is_file()
    except Exception:
        return False


def render_png(svg_path: Path, size: int, out_png: Path, *, name: str, seed: str | None = None,
               initials: str | None = None) -> str:
    """Returns the renderer used: rsvg | qlmanage | pure."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if _render_with_rsvg(svg_path, size, out_png):
        return "rsvg"
    if _render_with_qlmanage(svg_path, size, out_png):
        return "qlmanage"
    c1, c2 = palette_for(seed or name)
    out_png.write_bytes(render_png_pure(size, initials=initials or initials_for(name), c1=c1, c2=c2))
    return "pure"


def resize_png(src: Path, size: int, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("sips"):
        proc = subprocess.run(["sips", "-z", str(size), str(size), str(src), "--out", str(dest)],
                              capture_output=True, timeout=30)
        return proc.returncode == 0 and dest.is_file()
    return False


def write_ico(png_path: Path, dest: Path) -> bool:
    """PNG-in-ICO (supported by all modern browsers)."""
    data = png_path.read_bytes()
    try:
        width, height = struct.unpack(">II", data[16:24])
    except Exception:
        return False
    w = 0 if width >= 256 else width
    h = 0 if height >= 256 else height
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), 6 + 16)
    dest.write_bytes(header + entry + data)
    return True


def make_icns(png_1024: Path, dest_icns: Path) -> bool:
    if not (shutil.which("iconutil") and shutil.which("sips")):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for s in ICONSET_SIZES:
            resize_png(png_1024, s, iconset / f"icon_{s}x{s}.png")
            resize_png(png_1024, s * 2, iconset / f"icon_{s}x{s}@2x.png")
        proc = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest_icns)],
                              capture_output=True, timeout=60)
        return proc.returncode == 0 and dest_icns.is_file()


# ─── Icon sets ──────────────────────────────────────────────────────────────


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"


def generate_icon_set(name: str, *, out_dir: Path | str | None = None, seed: str | None = None,
                      initials: str | None = None, sizes: tuple[int, ...] = (1024, 512, 256, 192, 180, 128, 64, 32, 16),
                      icns: bool = True) -> dict[str, Any]:
    out = Path(out_dir) if out_dir else ICON_CACHE / _slug(name)
    out.mkdir(parents=True, exist_ok=True)
    svg_path = out / "icon.svg"
    svg_path.write_text(build_svg(name, initials=initials, seed=seed), encoding="utf-8")
    master = out / "icon-1024.png"
    renderer = render_png(svg_path, 1024, master, name=name, seed=seed, initials=initials)
    files: dict[str, str] = {"svg": str(svg_path), "png_1024": str(master)}
    for s in sizes:
        if s == 1024:
            continue
        dest = out / f"icon-{s}.png"
        if resize_png(master, s, dest):
            files[f"png_{s}"] = str(dest)
        elif renderer == "pure" and s >= 128:
            c1, c2 = palette_for(seed or name)
            dest.write_bytes(render_png_pure(s, initials=initials or initials_for(name), c1=c1, c2=c2))
            files[f"png_{s}"] = str(dest)
    fav_src = Path(files.get("png_256") or files.get("png_128") or master)
    ico = out / "favicon.ico"
    if write_ico(fav_src, ico):
        files["ico"] = str(ico)
    if icns:
        icns_path = out / "AppIcon.icns"
        if make_icns(master, icns_path):
            files["icns"] = str(icns_path)
    (out / "icon.json").write_text(json.dumps({"name": name, "initials": initials or initials_for(name),
                                                "palette": palette_for(seed or name), "renderer": renderer,
                                                "files": files}, indent=2), encoding="utf-8")
    return {"ok": True, "name": name, "initials": initials or initials_for(name), "renderer": renderer,
            "palette": palette_for(seed or name), "dir": str(out), "files": files}


# ─── Applying ───────────────────────────────────────────────────────────────


def apply_to_app_bundle(app_path: Path | str, *, name: str | None = None, seed: str | None = None) -> dict[str, Any]:
    app = Path(app_path)
    contents = app / "Contents"
    plist_path = contents / "Info.plist"
    if not contents.is_dir():
        return {"ok": False, "error": f"not an app bundle: {app}"}
    display = name
    info: dict[str, Any] = {}
    if plist_path.is_file():
        try:
            info = plistlib.loads(plist_path.read_bytes())
        except Exception:
            info = {}
    display = display or info.get("CFBundleDisplayName") or info.get("CFBundleName") or app.stem
    icon_set = generate_icon_set(display, seed=seed or info.get("CFBundleIdentifier") or display)
    resources = contents / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    if icon_set["files"].get("icns"):
        shutil.copy(icon_set["files"]["icns"], resources / "AppIcon.icns")
        applied.append("AppIcon.icns")
    shutil.copy(icon_set["files"]["png_1024"], resources / "AppIcon.png")
    applied.append("AppIcon.png")
    if plist_path.is_file() or info:
        info["CFBundleIconFile"] = "AppIcon"
        info.setdefault("CFBundleIconName", "AppIcon")
        try:
            plist_path.write_bytes(plistlib.dumps(info))
            applied.append("Info.plist CFBundleIconFile")
        except Exception as exc:
            return {"ok": False, "error": f"plist write failed: {exc}", "applied": applied}
    # Nudge Finder / LaunchServices to refresh the cached icon.
    try:
        os.utime(app, None)
        if shutil.which("touch"):
            subprocess.run(["touch", str(app)], capture_output=True, timeout=5)
    except Exception:
        pass
    return {"ok": True, "target": str(app), "name": display, "applied": applied, "icon_dir": icon_set["dir"]}


def detect_project_kind(project: Path) -> str:
    if (project / "Package.swift").is_file():
        return "swift"
    if (project / "package.json").is_file() or (project / "index.html").is_file() or (project / "public").is_dir():
        return "web"
    if (project / "pyproject.toml").is_file() or (project / "requirements.txt").is_file():
        return "python"
    return "generic"


def detect_project_name(project: Path) -> str:
    pkg = project / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            if data.get("name"):
                return str(data["name"]).split("/")[-1].replace("-", " ").title()
        except Exception:
            pass
    stem = project.name
    stem = re.sub(r"^willy-build-", "", stem)
    stem = re.sub(r"-\d{8}-\d{6}$", "", stem)
    return stem.replace("-", " ").replace("_", " ").title() or "App"


def _inject_favicon_links(index_html: Path, *, prefix: str) -> bool:
    try:
        html = index_html.read_text(encoding="utf-8")
    except Exception:
        return False
    if 'rel="icon"' in html or "rel='icon'" in html:
        return False
    links = (
        f'    <link rel="icon" type="image/svg+xml" href="{prefix}icon.svg">\n'
        f'    <link rel="icon" type="image/png" sizes="32x32" href="{prefix}icon-32.png">\n'
        f'    <link rel="apple-touch-icon" sizes="180x180" href="{prefix}icon-180.png">\n'
        f'    <link rel="manifest" href="{prefix}manifest.webmanifest">\n'
    )
    if re.search(r"<head[^>]*>", html, re.I):
        html = re.sub(r"(<head[^>]*>\s*\n?)", lambda m: m.group(1) + links, html, count=1, flags=re.I)
    else:
        html = links + html
    index_html.write_text(html, encoding="utf-8")
    return True


def apply_to_project(project_path: Path | str, *, name: str | None = None, seed: str | None = None) -> dict[str, Any]:
    project = Path(project_path)
    if not project.is_dir():
        return {"ok": False, "error": f"not a directory: {project}"}
    display = name or detect_project_name(project)
    kind = detect_project_kind(project)
    icon_set = generate_icon_set(display, seed=seed or project.name)
    files = icon_set["files"]
    applied: list[str] = []

    if kind == "web":
        public = project / "public" if (project / "public").is_dir() or (project / "package.json").is_file() else project
        public.mkdir(parents=True, exist_ok=True)
        shutil.copy(files["svg"], public / "icon.svg")
        applied.append(str(public / "icon.svg"))
        for s in WEB_SIZES:
            src = files.get(f"png_{s}")
            if src:
                shutil.copy(src, public / f"icon-{s}.png")
                applied.append(str(public / f"icon-{s}.png"))
        if files.get("ico"):
            shutil.copy(files["ico"], public / "favicon.ico")
            applied.append(str(public / "favicon.ico"))
        manifest = {
            "name": display,
            "short_name": display[:12],
            "icons": [{"src": f"/icon-{s}.png", "sizes": f"{s}x{s}", "type": "image/png"} for s in (192, 512)
                      if files.get(f"png_{s}")],
            "theme_color": icon_set["palette"][1],
            "background_color": icon_set["palette"][0],
            "display": "standalone",
            "start_url": "/",
        }
        (public / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        applied.append(str(public / "manifest.webmanifest"))
        for candidate in (project / "index.html", public / "index.html", project / "src" / "index.html"):
            if candidate.is_file() and _inject_favicon_links(candidate, prefix="/"):
                applied.append(f"{candidate} <link rel=icon>")
                break
    elif kind == "swift":
        res = project / "Resources"
        res.mkdir(parents=True, exist_ok=True)
        if files.get("icns"):
            shutil.copy(files["icns"], res / "AppIcon.icns")
            applied.append(str(res / "AppIcon.icns"))
        shutil.copy(files["png_1024"], res / "AppIcon.png")
        applied.append(str(res / "AppIcon.png"))
        for app in project.glob("*.app"):
            result = apply_to_app_bundle(app, name=display, seed=seed)
            if result.get("ok"):
                applied.append(str(app))
    else:
        assets = project / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        shutil.copy(files["svg"], assets / "icon.svg")
        shutil.copy(files["png_1024"], assets / "icon.png")
        applied += [str(assets / "icon.svg"), str(assets / "icon.png")]
        if files.get("icns"):
            shutil.copy(files["icns"], assets / "AppIcon.icns")
            applied.append(str(assets / "AppIcon.icns"))
        if files.get("ico"):
            shutil.copy(files["ico"], assets / "favicon.ico")
            applied.append(str(assets / "favicon.ico"))

    return {"ok": True, "target": str(project), "kind": kind, "name": display, "applied": applied,
            "icon_dir": icon_set["dir"], "renderer": icon_set["renderer"]}


def apply(target: Path | str, *, name: str | None = None, kind: str = "auto", seed: str | None = None) -> dict[str, Any]:
    path = Path(target).expanduser()
    if kind == "app" or (kind == "auto" and path.suffix == ".app"):
        return apply_to_app_bundle(path, name=name, seed=seed)
    return apply_to_project(path, name=name, seed=seed)


def known_app_bundles() -> list[tuple[Path, str]]:
    """Every .app William ships (repo bundles + Desktop copies made by the install scripts)."""
    helper = ROOT / "macos-helper"
    desktop = Path.home() / "Desktop"
    out = [
        (helper / "JarvisHelper.app", "Jarvis Helper"),
        (helper / "WilliamKiosk.app", "William Agent"),
        (helper / "WilliamDesktop.app", "William Agent"),
        (helper / "WilliamSystemMap.app", "William System Map"),
        (desktop / "William Agent.app", "William Agent"),
        (desktop / "William System Map.app", "William System Map"),
    ]
    return [(p, n) for p, n in out if p.is_dir()]


def build_projects() -> list[Path]:
    base = Path(os.environ.get("JARVIS_BUILD_PROJECTS_DIR", str(Path.home() / "Projects")))
    if not base.is_dir():
        return []
    return sorted(p for p in base.glob("willy-build-*") if p.is_dir())


def apply_everywhere() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for app, name in known_app_bundles():
        results.append(apply_to_app_bundle(app, name=name))
    for project in build_projects():
        results.append(apply_to_project(project))
    return {"ok": all(r.get("ok") for r in results) if results else True, "count": len(results), "results": results}


# ─── CLI (used by scripts/install-*.sh) ─────────────────────────────────────


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate / apply William app icons")
    parser.add_argument("--target", help=".app bundle or project directory")
    parser.add_argument("--name", help="display name (initials + palette derive from it)")
    parser.add_argument("--seed", help="palette seed (defaults to bundle id / name)")
    parser.add_argument("--kind", default="auto", choices=("auto", "app", "project"))
    parser.add_argument("--out", help="only generate the icon set into this directory")
    parser.add_argument("--all", action="store_true", help="apply to every known app bundle and build project")
    args = parser.parse_args(argv)
    if args.all:
        result = apply_everywhere()
    elif args.out:
        result = generate_icon_set(args.name or "William", out_dir=args.out, seed=args.seed)
    elif args.target:
        result = apply(args.target, name=args.name, kind=args.kind, seed=args.seed)
    else:
        parser.print_help()
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(_main())
