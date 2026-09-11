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

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth.dependencies import require_admin
from src.api.auth.jwt_session import SessionClaims

from . import state
from .process import OfflineMapProcess

router = APIRouter(prefix="/api/offline-map", tags=["offline-map"])

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
