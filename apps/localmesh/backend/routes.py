"""Read-only API for the LocalMesh page and topbar chip:

- GET /api/localmesh/status: per receiver whether it is connected, its INFO, last STATUS, channels and neighbours.
- GET /api/localmesh/members, /messages, /activity: the lists of the Members, Messages and Activity tabs (views.py).
- GET /api/localmesh/repeaters: the repeaters (name from the server's certificates, last heard, direct or not).
- GET /api/localmesh/packet-stats: how many LocalMesh packets Meshpoint stored (total, 24 h, per minute, average RSSI).
- GET /api/localmesh/channels: channel number -> name, for the conversation titles in the core Messages page.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from . import views

router = APIRouter()
_sources: list = []
_db = None
_stats: tuple[float, dict] = (0.0, {})
STATS_EVERY_S = 30   # every open dashboard asks; the packets table is large


def init_routes(sources, db=None) -> None:
    global _sources, _db, _stats
    _sources, _db, _stats = list(sources), db, (0.0, {})


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


@router.get("/api/localmesh/repeaters")
async def repeaters() -> list[dict]:
    return views.repeaters(_receivers())


@router.get("/api/localmesh/channels")
async def channels() -> list[dict]:
    return views.channels(_receivers())


@router.get("/api/localmesh/activity")
async def activity(limit: int = Query(200, ge=1, le=300)) -> list[dict]:
    return views.activity(_receivers(), limit)


@router.get("/api/localmesh/packet-stats")
async def packet_stats() -> dict:
    global _stats
    if _db is None:
        return {}
    if time.monotonic() - _stats[0] > STATS_EVERY_S or not _stats[1]:
        now = datetime.now(timezone.utc)
        since = lambda **kw: (now - timedelta(**kw)).isoformat()
        day = await _db.fetch_one("SELECT COUNT(*) AS n, AVG(rssi) AS rssi FROM packets WHERE protocol = 'localmesh' "
                                  "AND timestamp >= ?", (since(days=1),))
        recent = await _db.fetch_one("SELECT COUNT(*) AS n FROM packets WHERE protocol = 'localmesh' AND timestamp >= ?",
                                     (since(minutes=10),))
        total = await _db.fetch_one("SELECT COUNT(*) AS n FROM packets WHERE protocol = 'localmesh'")
        _stats = (time.monotonic(), {
            "total": total["n"], "day": day["n"], "per_minute": round(recent["n"] / 10, 1),
            "avg_rssi": None if day["rssi"] is None else round(day["rssi"], 1)})
    return _stats[1]
