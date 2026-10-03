"""``/api/bluetooth-scanner/*`` -- Start/Stop/Clear + live status.

Same shape as every other RTL-SDR-family listener's routes (see
``plugins/apps/rtl433/backend/routes.py`` in the main meshpoint repo):
``/status`` is readable by any session (``reg.add_router``'s default
``public=False`` still requires a valid session, just not admin);
``/start``, ``/stop``, and ``/clear`` require admin, matching every
other write action in the dashboard.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from src.api.auth.dependencies import require_admin
from src.api.auth.jwt_session import SessionClaims

from . import vendor_db
from .listener import BluetoothScannerListener

router = APIRouter(prefix="/api/bluetooth-scanner", tags=["bluetooth-scanner"])

_listener: Optional[BluetoothScannerListener] = None


def init_routes(listener: BluetoothScannerListener) -> None:
    global _listener
    _listener = listener


def reset_routes() -> None:
    """Test helper: clear module-level state between cases."""
    global _listener
    _listener = None


def _require_listener() -> BluetoothScannerListener:
    if _listener is None:
        raise HTTPException(503, "Listener not initialised")
    return _listener


@router.get("/status")
async def status():
    return _require_listener().poll()


@router.post("/start")
async def start(_claims: SessionClaims = Depends(require_admin)):
    listener = _require_listener()
    try:
        await listener.start()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return listener.status()


@router.post("/stop")
async def stop(_claims: SessionClaims = Depends(require_admin)):
    listener = _require_listener()
    await listener.stop()
    return listener.status()


@router.post("/clear")
async def clear(_claims: SessionClaims = Depends(require_admin)):
    listener = _require_listener()
    listener.clear()
    return listener.status()


@router.get("/vendor-db")
async def vendor_db_status():
    """Whether the offline lookup files are present, when they were
    downloaded, and how many entries each holds."""
    return vendor_db.status()


@router.post("/vendor-db/refresh")
async def vendor_db_refresh(_claims: SessionClaims = Depends(require_admin)):
    """Re-download the MAC vendor + Bluetooth SIG lookup files (the same
    thing setup.sh does). The old files stay in place if a download fails."""
    try:
        return await asyncio.to_thread(vendor_db.update)
    except Exception as exc:  # noqa: BLE001 -- network/parse errors alike
        raise HTTPException(502, f"Vendor database download failed: {exc}") from exc
