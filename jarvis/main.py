from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from jarvis.config import settings
from jarvis.database import init_db
from jarvis.routers import api
from jarvis.services import scheduler, self_modify, worker

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from jarvis.services import macos, remote_control, security

    await init_db()
    self_modify.ensure_repo()
    if settings.auto_full_access:
        await security.set_full_access(True)
    if settings.remote_control_enabled:
        await remote_control.set_enabled(True)
    scheduler.start()
    worker.start()
    from jarvis.services import link, resource_governor, vigil_metrics

    if settings.governor_enabled:
        resource_governor.start()
    if settings.link_enabled:
        link.start()
    vigil_metrics.start()
    from jarvis.services import vigil_proxy

    vst = vigil_proxy.status()
    if settings.vigil_proxy_enabled and not vigil_proxy.configured():
        log = __import__("logging").getLogger("jarvis")
        log.warning("Vigil proxy enabled but missing: %s", ", ".join(vst.get("missing") or []))
    yield
    worker.stop()
    scheduler.stop()
    resource_governor.stop()
    await link.stop()
    await vigil_metrics.close()
    await macos.close_helper_client()


app = FastAPI(title=settings.agent_name, lifespan=lifespan)
app.include_router(api.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
async def chat():
    return FileResponse(STATIC / "chat.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    return FileResponse(STATIC / "panel.html")


@app.get("/minis", response_class=HTMLResponse)
async def minis_panel():
    return FileResponse(STATIC / "minis.html")


@app.get("/map", response_class=HTMLResponse)
async def system_map_page():
    return FileResponse(STATIC / "system-map.html")
