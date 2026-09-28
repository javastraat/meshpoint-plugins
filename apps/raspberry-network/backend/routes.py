"""WiFi scan/status/connect -- the raspberry-network plugin's whole API.

Status is read-only, session-gated like any other `/api/*` route.
Scanning and connecting are admin-only: a scan briefly disrupts the wifi
radio, and connecting can change (or break) how this device is reached
at all. See `src/api/nmcli.py` (core) for the actual `nmcli` calls and,
more importantly, why `wifi_connect()` never tears down a working
connection to try a failing one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.audit import AuditLogWriter
from src.api.audit.dependencies import get_audit_writer
from src.api.auth.dependencies import require_admin
from src.api.auth.jwt_session import SessionClaims
from src.api.nmcli import ethernet_status, wifi_connect, wifi_scan, wifi_status

router = APIRouter(prefix="/api/raspberry-network", tags=["raspberry-network"])


class ConnectRequest(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=32)
    password: str = Field(default="", max_length=63)


@router.get("/status")
async def get_status():
    """Current wifi device state -- `None` (as `null`) if this box has
    no wifi device at all (Ethernet-only carriers)."""
    return {"status": await wifi_status()}


@router.get("/status/ethernet")
async def get_ethernet_status():
    """Current ethernet device state -- `None` (as `null`) if this box
    has no ethernet device at all (WiFi-only carriers)."""
    return {"status": await ethernet_status()}


@router.post("/scan")
async def scan(_claims: SessionClaims = Depends(require_admin)):
    return {"networks": await wifi_scan()}


@router.post("/connect")
async def connect(
    req: ConnectRequest,
    claims: SessionClaims = Depends(require_admin),
    audit: AuditLogWriter = Depends(get_audit_writer),
):
    # Never logs the password -- same reasoning as every other admin
    # action in this codebase that touches a secret.
    with audit.timed_action(
        user=claims.subject,
        action="raspberry_network.wifi_connect",
        params={"ssid": req.ssid},
    ):
        rc, out = await wifi_connect(req.ssid, req.password)
    if rc != 0:
        raise HTTPException(
            502,
            f"nmcli connect to {req.ssid!r} failed (exit {rc}): {out or 'no output'}",
        )
    return {"success": True, "output": out}
