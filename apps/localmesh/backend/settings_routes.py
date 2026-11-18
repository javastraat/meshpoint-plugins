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
MAX_CHANNEL = 39   # LocalMesh channel numbers run 0..39 (up to 40 channels)
_LABEL = re.compile(r"^[a-z0-9_-]{0,16}$")   # becomes part of the capture source name: localmesh_<label>


class DeviceEntry(BaseModel):
    serial_port: str = ""
    label: str = ""


class LocalMeshSettingsUpdate(BaseModel):
    devices: list[DeviceEntry]
    channel_names: dict[str, str] = {}


def validate_channel_names(raw: dict[str, str]) -> dict[int, str]:
    """Clean and check the channel -> name map; raises HTTPException(400) with a message the Settings tab shows.
    An empty name for a channel just means "no override" -- not an error, it's dropped rather than stored blank."""
    out: dict[int, str] = {}
    for key, value in raw.items():
        try:
            channel = int(key)
        except ValueError:
            raise HTTPException(400, f"channel {key!r} is not a number") from None
        if not 0 <= channel <= MAX_CHANNEL:
            raise HTTPException(400, f"channel {channel} is out of range (0-{MAX_CHANNEL})")
        name = value.strip()
        if name:
            out[channel] = name
    return out


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
    channel_names = validate_channel_names(req.channel_names)
    names = {d.get("label", ""): d["name"] for d in state.devices() if d.get("name")}
    for d in devices:   # the name on the network (set on the Send tab) stays with its receiver
        if d["label"] in names:
            d["name"] = names[d["label"]]
    # Only the device list needs a service restart (a receiver opens its serial port once at startup) --
    # channel_names is just a lookup table, read fresh on every message, so changing only that shouldn't
    # claim a restart is needed when it genuinely isn't.
    devices_changed = devices != state.devices()
    with audit.timed_action(user=_claims.subject, action="localmesh.settings_update",
                            params={"channel_names_count": len(channel_names)}):
        state.set_devices(devices)
        state.set_channel_names(channel_names)
    return {
        "saved": True, "restart_required": devices_changed,
        "devices": state.devices(), "channel_names": state.channel_names(),
    }
