"""offline-map plugin endpoints: settings + start/stop/status.

Mounted with the router default (``public=False``, see
``reg.add_router()``), so every route here already requires *some*
authenticated session (viewer or admin) -- ``/status``/``/settings`` (GET)
rely on that alone, same as rtl433's own ``/status``. Changing settings or
starting/stopping the subprocess needs ``require_admin`` explicitly, same
as rtl433's ``/start``/``/stop``/``/clear`` -- this controls an external
process that (today) opens an unauthenticated port on the LAN, so it gets
the stricter gate.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.api.auth.dependencies import require_admin
from src.api.auth.jwt_session import SessionClaims

from . import state
from .process import OfflineMapProcess

router = APIRouter(prefix="/api/offline-map", tags=["offline-map"])

# Matches offline-map-tile-downloader's own sanitizeStyleName() charset
# (main.go: `[^a-zA-Z0-9-_]+` stripped) -- a collection/style name on disk
# never contains anything outside this, so rejecting anything else here
# (rather than trying to "clean" it) also rejects a bare ".." path segment,
# which -- unlike "../foo" -- Starlette's per-segment route matching does
# NOT stop on its own (no slash to cross).
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_process: Optional[OfflineMapProcess] = None


def init_routes(process: OfflineMapProcess) -> None:
    global _process
    _process = process


def reset_routes() -> None:
    global _process
    _process = None


def _require_process() -> OfflineMapProcess:
    if _process is None:
        raise HTTPException(503, "Plugin not initialised")
    return _process


@router.get("/status")
async def status():
    return _require_process().status()


@router.get("/settings")
async def get_settings():
    return state.to_dict()


class SettingsUpdate(BaseModel):
    port: int = Field(8081, ge=1, le=65535)  # not 8080 -- see state.py's _DEFAULTS comment
    maps_directory: str = Field(..., min_length=1)
    presets_directory: str = Field(..., min_length=1)
    log_file: str = Field(..., min_length=1)
    max_workers: int = Field(50, ge=1, le=500)
    rate_limit: int = Field(50, ge=1, le=1000)
    max_retries: int = Field(5, ge=1, le=50)
    quiet: bool = False


@router.put("/settings")
async def update_settings(
    req: SettingsUpdate, _claims: SessionClaims = Depends(require_admin),
):
    """Persisted immediately; picked up by the *next* Start (process.py
    reads state fresh each time it spawns, nothing is cached at
    construction) -- no Meshpoint restart needed. Refuses while the
    process is running so a change can't silently diverge from what's
    actually serving right now; stop first."""
    proc = _require_process()
    if proc.running:
        raise HTTPException(409, "Stop the downloader before changing settings")
    state.set_config(req.model_dump())
    return state.to_dict()


@router.post("/start")
async def start(_claims: SessionClaims = Depends(require_admin)):
    proc = _require_process()
    try:
        await proc.start()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return proc.status()


@router.post("/stop")
async def stop(_claims: SessionClaims = Depends(require_admin)):
    proc = _require_process()
    await proc.stop()
    return proc.status()


# --- Serving already-downloaded tiles, without the downloader running ------
#
# Same URL shape as the downloader's own `/tiles/<collection>/<style>/
# <z>/<x>/<y>.png` (main.go's serveTile()), so a `dashboard.map_tile_url`
# pointed here is a drop-in swap for pointing at the downloader itself --
# except this reads straight off disk, so nothing needs to be left running
# just to view tiles you already have. Read-only, so no admin gate: viewing
# the map is already something any logged-in session (viewer included) can
# do today.

@router.get("/collections")
async def list_collections():
    """Every ``<collection>/<style>`` pair actually present on disk, so the
    Settings page (or a docs link) can tell you what to put in
    ``dashboard.map_tile_url`` without needing to SSH in and look."""
    maps_dir = Path(state.to_dict()["maps_directory"])
    if not maps_dir.is_dir():
        return {"collections": []}
    found = []
    for collection_dir in sorted(maps_dir.iterdir()):
        if not collection_dir.is_dir():
            continue
        for style_dir in sorted(collection_dir.iterdir()):
            if style_dir.is_dir():
                found.append({"collection": collection_dir.name, "style": style_dir.name})
    return {"collections": found}


@router.get("/tiles/{collection}/{style}/{z}/{x}/{y}.png")
async def serve_tile(collection: str, style: str, z: int, x: int, y: int):
    if not (_SAFE_NAME_RE.match(collection) and _SAFE_NAME_RE.match(style)):
        raise HTTPException(400, "invalid collection/style name")

    maps_dir = Path(state.to_dict()["maps_directory"]).resolve()
    tile_path = maps_dir / collection / style / str(z) / str(x) / f"{y}.png"
    if not tile_path.is_file():
        raise HTTPException(404, "tile not found")
    return FileResponse(tile_path, media_type="image/png")
