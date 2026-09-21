"""oled-display plugin endpoints: settings + a live preview of the
physical screen.

Mounted with the router default (``public=False``), so every route here
already requires *some* authenticated session. GET routes (status/preview)
work for any logged-in session, same as offline-map's own read routes --
changing settings needs ``require_admin``, same reasoning as every other
plugin's settings-write route.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from src.api.auth.dependencies import require_admin
from src.api.auth.jwt_session import SessionClaims

from . import state
from .display_service import DisplayService

router = APIRouter(prefix="/api/oled-display", tags=["oled-display"])

_service: Optional[DisplayService] = None


def init_routes(service: DisplayService) -> None:
    global _service
    _service = service


def reset_routes() -> None:
    global _service
    _service = None


@router.get("/status")
async def status():
    return {
        **state.to_dict(),
        "display_open": _service.is_open if _service else False,
        "blanked": _service.is_blanked if _service else False,
        "last_rendered_at": (
            _service.last_status_rendered_at.isoformat()
            if _service and _service.last_status_rendered_at else None
        ),
    }


@router.get("/preview.png")
async def preview():
    """The last real status frame drawn -- not necessarily what's on
    the hardware at this exact instant. Deliberately serves
    `last_status_png` rather than `last_frame_png`: once the physical
    panel auto-blanks for burn-in, mirroring it exactly would make the
    settings-page preview go black too, which defeats its purpose
    ("what did it last say") right when auto-blank kicks in. `status`
    above reports `blanked` separately so the frontend can still say
    the physical screen is currently off. 503 (not a blank image) when
    nothing's been rendered yet, so the frontend can tell "not
    started" apart from "genuinely blank"."""
    if _service is None or _service.last_status_png is None:
        raise HTTPException(503, "No frame rendered yet")
    return Response(content=_service.last_status_png, media_type="image/png")


@router.post("/wake")
async def wake(_claims: SessionClaims = Depends(require_admin)):
    """Force the physical panel back on right now and restart its
    blank timer -- the settings page's Wake button. `require_admin`,
    same gate as every other plugin's start/stop-style control route
    (offline-map, rtl433, ...): this mutates live device state, not a
    read."""
    if _service is None or not await _service.wake():
        raise HTTPException(503, "Display not available")
    return {"woke": True}


class SettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    i2c_address: Optional[str] = Field(None, pattern=r"^0[xX][0-9a-fA-F]{2}$")
    driver: Optional[str] = Field(None, pattern=r"^(ssd1306|sh1106|ssd1309)$")
    width: Optional[int] = Field(None, ge=32, le=256)
    height: Optional[int] = Field(None, ge=16, le=128)
    blank_after_minutes: Optional[int] = Field(None, ge=0, le=1440)
    refresh_seconds: Optional[int] = Field(None, ge=1, le=300)


@router.put("/settings")
async def update_settings(
    body: SettingsUpdate, _claims: SessionClaims = Depends(require_admin),
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    state.set_config(updates)
    return {"saved": True, "settings": state.to_dict(), "restart_required": True}
