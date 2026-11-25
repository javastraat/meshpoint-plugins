"""/api/localmesh-repeaters/*: the list for any logged-in user, the settings for admins. Nothing here transmits."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth.dependencies import require_admin, require_auth
from src.api.auth.jwt_session import SessionClaims

router = APIRouter(prefix="/api/localmesh-repeaters", tags=["localmesh-repeaters"])
_service = None


def init_routes(service) -> None:
    global _service
    _service = service


def _svc():
    if _service is None:
        raise HTTPException(503, "LocalMesh Repeaters is not running")
    return _service


@router.get("/repeaters")
async def repeaters(_claims: SessionClaims = Depends(require_auth)) -> dict:
    return _svc().overview()


class SettingsRequest(BaseModel):
    keep_days: int = Field(30, ge=1, le=365)
    hidden: list[int] = []


@router.put("/settings")
async def put_settings(req: SettingsRequest, _claims: SessionClaims = Depends(require_admin)) -> dict:
    await _svc().set_config({"keep_days": req.keep_days, "hidden": req.hidden})
    return {"saved": True, "settings": dict(_svc().config)}
