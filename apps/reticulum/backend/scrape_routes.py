"""Scrape-a-website endpoints backing the reticulum-scraper plugin's
"Scrape" tab (hooked into this plugin's own Reticulum page).

Mirrors the shape of ``nomad_routes.py``'s Pages-tab endpoints: GET status
is readable by any authenticated session, everything that starts/stops a
job or touches disk is admin-only -- same tier as ``offline-map``'s own
``/start``/``/stop`` (this hits the open internet from the device, same
concern).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth.dependencies import require_admin, require_auth
from src.api.auth.jwt_session import SessionClaims

from . import scraper, state
from .lxmf_service import LxmfService

router = APIRouter(prefix="/api/reticulum/scrape", tags=["reticulum"])

_scraper: scraper.Scraper | None = None


def init_routes(service: LxmfService) -> None:
    global _scraper
    _scraper = scraper.Scraper(
        pages_dir_provider=lambda: state.node_config()["pages_dir"],
        reload_callback=service.reload_node_pages,
    )


def reset_routes() -> None:
    global _scraper
    _scraper = None


def _require_scraper() -> scraper.Scraper:
    if _scraper is None:
        raise HTTPException(503, "Reticulum companion is disabled")
    return _scraper


class StartRequest(BaseModel):
    source_url: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    max_pages: int | None = None
    max_depth: int | None = None


@router.post("/start")
async def start(
    req: StartRequest, _claims: SessionClaims = Depends(require_admin),
):
    try:
        return await _require_scraper().start(
            req.source_url, req.name, req.max_pages, req.max_depth,
        )
    except scraper.ScraperError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/stop")
async def stop(_claims: SessionClaims = Depends(require_admin)):
    return await _require_scraper().stop()


@router.get("/status")
async def status(_claims: SessionClaims = Depends(require_auth)):
    return _require_scraper().status()


@router.get("/collections")
async def collections(_claims: SessionClaims = Depends(require_auth)):
    return {"collections": scraper.list_collections(state.node_config()["pages_dir"])}


@router.delete("/collections/{name}")
async def delete_collection(
    name: str, _claims: SessionClaims = Depends(require_admin),
):
    try:
        scraper.delete_collection(state.node_config()["pages_dir"], name)
    except scraper.ScraperError as exc:
        raise HTTPException(400, str(exc)) from exc
    reloaded = _require_scraper().reload_node()
    return {"deleted": True, "name": name, "served": reloaded}
