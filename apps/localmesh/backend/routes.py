"""Read-only API for the LocalMesh page and topbar chip:

- GET /api/localmesh/status: per receiver whether it is connected, its INFO, last STATUS, channels and neighbours.
- GET /api/localmesh/members, /messages, /activity: the lists of the Members, Messages and Activity tabs (views.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from . import views

router = APIRouter()
_sources: list = []


def init_routes(sources) -> None:
    global _sources
    _sources = list(sources)


def _receivers() -> list:
    return [(s.snapshot()["label"], s.page_data()) for s in _sources]


@router.get("/api/localmesh/status")
async def status() -> list[dict]:
    return [s.snapshot() for s in _sources]


@router.get("/api/localmesh/members")
async def members() -> list[dict]:
    return views.members(_receivers())


@router.get("/api/localmesh/messages")
async def messages(limit: int = Query(200, ge=1, le=300)) -> list[dict]:
    return views.messages(_receivers(), limit)


@router.get("/api/localmesh/activity")
async def activity(limit: int = Query(200, ge=1, le=300)) -> list[dict]:
    return views.activity(_receivers(), limit)
