"""/api/reticulum-repeaters/* -- the Reticulum Repeaters page.

Reads (latest snapshots, history) are open to any logged-in user, like the
rest of meshpoint's dashboards; everything that changes config or puts
packets on the air (add, remove, refresh, settings) is admin only.
"""

from __future__ import annotations

import asyncio
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth.dependencies import require_admin, require_auth
from src.api.auth.jwt_session import SessionClaims

from . import state
from .monitor import RepeaterMonitor

router = APIRouter(prefix="/api/reticulum-repeaters", tags=["reticulum-repeaters"])

_monitor: RepeaterMonitor | None = None


def init_routes(monitor: RepeaterMonitor) -> None:
    global _monitor
    _monitor = monitor


def reset_routes() -> None:
    global _monitor
    _monitor = None


def _mon() -> RepeaterMonitor:
    if _monitor is None:
        raise HTTPException(503, "Reticulum repeaters service is not running")
    return _monitor


def _hash(h: str) -> str:
    h = (h or "").strip().lower()
    if not state.valid_hash(h):
        raise HTTPException(400, "Not a NomadNet address (32 hex characters)")
    return h


@router.get("/repeaters")
async def list_repeaters(_claims: SessionClaims = Depends(require_auth)):
    mon = _mon()
    out = []
    for r in state.repeaters():
        snap = {k: v for k, v in (mon.latest.get(r["hash"]) or {}).items() if not k.startswith("_")}
        out.append({**r, "snapshot": snap})
    return {"repeaters": out, "settings": {k: v for k, v in state.to_dict().items() if k != "repeaters"}}


@router.get("/repeaters/{h}/history")
async def history(h: str, hours: float = 24 * 7, _claims: SessionClaims = Depends(require_auth)):
    h = _hash(h)
    hours = max(1.0, min(hours, 24 * 60))
    return {"samples": await _mon()._store.history(h, hours)}


class AddRequest(BaseModel):
    hash: str = Field(..., min_length=32, max_length=32)
    name: str = ""


@router.post("/repeaters")
async def add_repeater(req: AddRequest, _claims: SessionClaims = Depends(require_admin)):
    h = _hash(req.hash)
    mon = _mon()
    if not state.add_repeater(h, req.name.strip()):
        raise HTTPException(409, "That repeater is already in the list")
    # Fetch everything (plus the 7-day history) in the background: over LoRa
    # this takes a while, the page shows "Fetching..." until it lands.
    mon.mark_refresh(h)
    asyncio.create_task(mon.add(h))
    return {"added": True}


@router.delete("/repeaters/{h}")
async def remove_repeater(h: str, _claims: SessionClaims = Depends(require_admin)):
    h = _hash(h)
    if not state.remove_repeater(h):
        raise HTTPException(404, "Not in the list")
    await _mon().forget(h)
    return {"removed": True}


@router.post("/repeaters/{h}/refresh")
async def refresh(h: str, _claims: SessionClaims = Depends(require_admin)):
    h = _hash(h)
    if not any(r["hash"] == h for r in state.repeaters()):
        raise HTTPException(404, "Not in the list")
    mon = _mon()
    allowed, wait = mon.can_refresh(h)
    if not allowed:
        raise HTTPException(429, f"Just refreshed -- try again in {wait} s")
    mon.mark_refresh(h)
    asyncio.create_task(mon.refresh(h))   # over LoRa this can take a minute; the page follows "polling"
    return {"started": True}


class SettingsRequest(BaseModel):
    polling_enabled: bool = True
    interval_min: int = Field(15, ge=5, le=1440)
    lists_interval_min: int = Field(60, ge=15, le=1440)


@router.put("/settings")
async def put_settings(req: SettingsRequest, _claims: SessionClaims = Depends(require_admin)):
    state.set_settings(req.polling_enabled, req.interval_min, req.lists_interval_min)
    return {"saved": True}


@router.get("/candidates")
async def candidates(_claims: SessionClaims = Depends(require_admin)):
    """NomadNet nodes the reticulum plugin has heard, for "Add repeater" --
    a pick list only; nothing is contacted until one is added."""
    routes = sys.modules.get("meshpoint_plugin_reticulum.routes")
    svc = getattr(routes, "_service", None) if routes else None
    if svc is None:
        return {"nodes": []}
    added = {r["hash"] for r in state.repeaters()}
    peers = await svc.list_peers(aspect="nomadnetwork.node", limit=300)
    return {"nodes": [
        {"hash": p.destination_hash, "name": p.display_name or "", "last_seen": p.last_seen}
        for p in peers if p.destination_hash not in added
    ]}
