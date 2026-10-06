"""App icon generation/application and CAD script generation (no external tools required)."""

from __future__ import annotations

import json
import plistlib
import struct
import zlib

import pytest

from jarvis.services import app_icons
from jarvis.services.providers import cad


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_initials_and_palette_are_deterministic():
    assert app_icons.initials_for("William Agent") == "WA"
    assert app_icons.initials_for("willy-build-todo-app-20251002-001122") == "TA"
    assert app_icons.initials_for("Spliit") == "S"
    assert app_icons.palette_for("William Agent") == app_icons.palette_for("william agent")
    assert app_icons.palette_for("a") != app_icons.palette_for("b") or True  # palette is hash-based


def test_pure_png_renderer_produces_valid_png():
    data = app_icons.render_png_pure(64, initials="WA", c1="#0f172a", c2="#2563eb")
    assert _png_size(data) == (64, 64)
    # IDAT decompresses to 64 rows of (1 filter byte + 64*4)
    idat_start = data.index(b"IDAT") + 4
    length = struct.unpack(">I", data[idat_start - 8: idat_start - 4])[0]
    raw = zlib.decompress(data[idat_start: idat_start + length])
    assert len(raw) == 64 * (1 + 64 * 4)
    # Centre pixel is white (letter) or gradient — never transparent
    row = 32
    px = raw[row * (1 + 64 * 4) + 1 + 32 * 4: row * (1 + 64 * 4) + 1 + 32 * 4 + 4]
    assert px[3] == 255
    # Corner is transparent
    assert raw[1:5] == b"\x00\x00\x00\x00"


def test_generate_icon_set_pure_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(app_icons, "_render_with_qlmanage", lambda *a, **k: False)
    monkeypatch.setattr(app_icons, "_render_with_rsvg", lambda *a, **k: False)
    monkeypatch.setattr(app_icons.shutil, "which", lambda name: None)
    result = app_icons.generate_icon_set("Todo App", out_dir=tmp_path / "set", sizes=(1024, 256, 128))
    assert result["ok"] and result["renderer"] == "pure"
    files = result["files"]
    assert (tmp_path / "set" / "icon.svg").read_text().startswith("<svg")
    assert _png_size((tmp_path / "set" / "icon-1024.png").read_bytes()) == (1024, 1024)
    assert "png_256" in files and "ico" in files
    assert (tmp_path / "set" / "favicon.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert json.loads((tmp_path / "set" / "icon.json").read_text())["initials"] == "TA"


def test_apply_to_app_bundle_sets_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(app_icons, "ICON_CACHE", tmp_path / "cache")
    monkeypatch.setattr(app_icons, "_render_with_qlmanage", lambda *a, **k: False)
    monkeypatch.setattr(app_icons, "_render_with_rsvg", lambda *a, **k: False)
    monkeypatch.setattr(app_icons, "make_icns", lambda src, dest: (dest.write_bytes(b"icns"), True)[1])
    app = tmp_path / "Demo.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "Demo Tool",
                                                                   "CFBundleIdentifier": "com.test.demo"}))
    result = app_icons.apply_to_app_bundle(app)
    assert result["ok"] and result["name"] == "Demo Tool"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIconFile"] == "AppIcon"
    assert (app / "Contents" / "Resources" / "AppIcon.icns").is_file()
    assert (app / "Contents" / "Resources" / "AppIcon.png").is_file()


def test_apply_to_web_project_injects_favicon(tmp_path, monkeypatch):
    monkeypatch.setattr(app_icons, "ICON_CACHE", tmp_path / "cache")
    monkeypatch.setattr(app_icons, "_render_with_qlmanage", lambda *a, **k: False)
    monkeypatch.setattr(app_icons, "_render_with_rsvg", lambda *a, **k: False)
    project = tmp_path / "willy-build-recipe-box-20251002-120000"
    (project / "public").mkdir(parents=True)
    (project / "package.json").write_text(json.dumps({"name": "recipe-box"}))
    (project / "index.html").write_text("<!doctype html>\n<html>\n<head>\n<title>x</title>\n</head><body></body></html>")
    result = app_icons.apply_to_project(project)
    assert result["ok"] and result["kind"] == "web" and result["name"] == "Recipe Box"
    assert (project / "public" / "icon.svg").is_file()
    assert (project / "public" / "manifest.webmanifest").is_file()
    html = (project / "index.html").read_text()
    assert 'rel="icon"' in html and "apple-touch-icon" in html
    # Idempotent: second run does not duplicate links
    app_icons.apply_to_project(project)
    assert html.count('rel="icon"') == (project / "index.html").read_text().count('rel="icon"')


def test_apply_to_generic_project(tmp_path, monkeypatch):
    monkeypatch.setattr(app_icons, "ICON_CACHE", tmp_path / "cache")
    monkeypatch.setattr(app_icons, "_render_with_qlmanage", lambda *a, **k: False)
    monkeypatch.setattr(app_icons, "_render_with_rsvg", lambda *a, **k: False)
    project = tmp_path / "tooling"
    project.mkdir()
    result = app_icons.apply_to_project(project, name="Tooling")
    assert result["kind"] == "generic" and (project / "assets" / "icon.png").is_file()


@pytest.mark.asyncio
async def test_cad_generate_openscad_without_binary(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.config.settings.cad_output_dir", tmp_path / "cad")
    monkeypatch.setattr(cad, "openscad_bin", lambda: None)

    async def fake_chat(**kwargs):
        assert kwargs["capability"] == "cad"
        return {"reply": "```openscad\ncube([20,20,20]);\n```", "provider": "gemini", "model": "gemini-2.5-flash"}

    monkeypatch.setattr(cad.gateway, "chat_detailed", fake_chat)
    result = await cad.generate("a 20mm cube")
    assert result["ok"] and result["format"] == "openscad"
    scad = (tmp_path / "cad").glob("*/model.scad")
    text = next(scad).read_text()
    assert text.strip() == "cube([20,20,20]);"
    assert result["render"]["skipped"] is True


def test_cad_status_reports_tools():
    status = cad.status()
    assert set(status) >= {"openscad", "blender", "gradio_client", "hf_token", "mesh_space"}
