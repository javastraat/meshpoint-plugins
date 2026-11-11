"""GET /api/localmesh/status: per receiver whether it is connected, its INFO and its last STATUS (topbar chip)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
_sources: list = []


def init_routes(sources) -> None:
    global _sources
    _sources = list(sources)


@router.get("/api/localmesh/status")
async def status() -> list[dict]:
    return [s.snapshot() for s in _sources]
