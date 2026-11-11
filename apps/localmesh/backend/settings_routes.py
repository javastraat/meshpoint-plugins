"""GET/PUT /api/localmesh/settings: the receiver list for the LocalMesh page's Settings tab.

Saving is admin-only and audited (same as the dapnet plugin). A receiver opens its serial port once at startup, so a
saved change needs a service restart; the reply says so with ``restart_required``.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.audit import AuditLogWriter
from src.api.audit.dependencies import get_audit_writer
from src.api.auth.dependencies import require_admin
from src.api.auth.jwt_session import SessionClaims

from . import state

router = APIRouter(prefix="/api/localmesh", tags=["config", "localmesh"])

MAX_DEVICES = 4
_LABEL = re.compile(r"^[a-z0-9_-]{0,16}$")   # becomes part of the capture source name: localmesh_<label>


class DeviceEntry(BaseModel):
    serial_port: str = ""
    label: str = ""


class LocalMeshSettingsUpdate(BaseModel):
    devices: list[DeviceEntry]


def validate_devices(devices: list[DeviceEntry]) -> list[dict]:
    """Clean and check a device list; raises HTTPException(400) with a message the Settings tab shows."""
    if len(devices) > MAX_DEVICES:
        raise HTTPException(400, f"at most {MAX_DEVICES} receivers")
    out: list[dict] = []
    for n, d in enumerate(devices, 1):
        port, label = d.serial_port.strip(), d.label.strip().lower()
        if not port:
            raise HTTPException(400, f"receiver {n}: serial port is empty")
        if not _LABEL.match(label):
            raise HTTPException(400, f"receiver {n}: label may only use a-z, 0-9, - and _ (max. 16)")
        out.append({"serial_port": port, "label": label})
    labels = [d["label"] for d in out]
    if len(set(labels)) != len(labels):
        raise HTTPException(400, "every receiver needs its own label")
    ports = [d["serial_port"] for d in out]
    if len(set(ports)) != len(ports):
        raise HTTPException(400, "two receivers on the same serial port")
    return out


@router.get("/settings")
async def get_localmesh_settings() -> dict:
    return state.to_dict()


@router.put("/settings")
async def update_localmesh_settings(
    req: LocalMeshSettingsUpdate,
    _claims: SessionClaims = Depends(require_admin),
    audit: AuditLogWriter = Depends(get_audit_writer),
) -> dict:
    devices = validate_devices(req.devices)
    names = {d.get("label", ""): d["name"] for d in state.devices() if d.get("name")}
    for d in devices:   # the name on the network (set on the Send tab) stays with its receiver
        if d["label"] in names:
            d["name"] = names[d["label"]]
    with audit.timed_action(user=_claims.subject, action="localmesh.settings_update", params={}):
        state.set_devices(devices)
    return {"saved": True, "restart_required": True, "devices": state.devices()}
