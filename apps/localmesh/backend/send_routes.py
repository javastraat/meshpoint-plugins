"""The Send tab: the only routes that make a receiver transmit. Admin-only and audited, like the settings.

- POST /api/localmesh/send    {receiver, channel | to, text}: a channel message, or a direct message to a member
- PUT  /api/localmesh/name    {receiver, name}: the receiver's name on the network (the board broadcasts it itself
                               5 s later when it changed, and puts it in front of every channel message it sends)
- POST /api/localmesh/advert  {receiver}: broadcast the name now

Checked here first (clear message, nothing transmitted): one target, text length in bytes, a channel this receiver
has the key for. The receiver still has the last word (its airtime budget, its membership); its error codes come back
as a readable message. The text is not written to the audit log, only where it went and how long it was.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.audit import AuditLogWriter
from src.api.audit.dependencies import get_audit_writer
from src.api.auth.dependencies import require_admin
from src.api.auth.jwt_session import SessionClaims

from . import protocol as p
from . import state
from .listener import CommandError

router = APIRouter(prefix="/api/localmesh", tags=["localmesh"])
_sources: list = []

_ERRORS = {
    p.ERR_ARGUMENT: (400, "the receiver refused it: text too long or not allowed"),
    p.ERR_STATE: (409, "the receiver cannot send this now: no key for this channel, airtime budget used up, "
                       "or it is not a member (yet)"),
    p.ERR_RADIO: (502, "the receiver's radio failed to send"),
    p.ERR_UNKNOWN: (501, "the receiver's firmware does not know this command"),
    None: (504, "the receiver did not answer"),
}


def init_routes(sources) -> None:
    global _sources
    _sources = list(sources)


def _source(label: Optional[str]):
    if label is None and len(_sources) == 1:
        return _sources[0]
    for s in _sources:
        if s.snapshot()["label"] == (label or ""):
            return s
    raise HTTPException(404, "no such receiver")


async def _run(coro):
    try:
        return await coro
    except ConnectionError:
        raise HTTPException(503, "the receiver is not connected") from None
    except CommandError as e:
        status, text = _ERRORS.get(e.code, (502, f"receiver error {e.code}"))
        raise HTTPException(status, text) from None


class SendRequest(BaseModel):
    receiver: Optional[str] = None
    channel: Optional[int] = None
    to: Optional[int] = None
    text: str


def check_send(src, req: SendRequest) -> tuple[str, int]:
    """(cleaned text, byte limit), or HTTPException(400) with what is wrong."""
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "the message is empty")
    if (req.channel is None) == (req.to is None):
        raise HTTPException(400, "send to one channel or one member")
    if req.to is not None:
        if not 0 < req.to < p.REPEATER_MEMBER_BASE:
            raise HTTPException(400, "not a member number")
        limit = p.DIRECT_TEXT_MAX
    else:
        if req.channel not in src.channels:
            raise HTTPException(400, "this receiver has no key for that channel")
        limit = p.channel_text_max(src.net_name or "")
    if len(text.encode()) > limit:
        raise HTTPException(400, f"the message is {len(text.encode())} bytes, at most {limit} fit")
    return text, limit


@router.post("/send")
async def send(
    req: SendRequest,
    _claims: SessionClaims = Depends(require_admin),
    audit: AuditLogWriter = Depends(get_audit_writer),
) -> dict:
    src = _source(req.receiver)
    text, _limit = check_send(src, req)
    params = {"receiver": req.receiver, "channel": req.channel, "to": req.to, "bytes": len(text.encode())}
    with audit.timed_action(user=_claims.subject, action="localmesh.send", params=params):
        return await _run(src.send_text(text, channel=req.channel, member=req.to))


class NameRequest(BaseModel):
    receiver: Optional[str] = None
    name: str


@router.put("/name")
async def set_name(
    req: NameRequest,
    _claims: SessionClaims = Depends(require_admin),
    audit: AuditLogWriter = Depends(get_audit_writer),
) -> dict:
    src = _source(req.receiver)
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "the name is empty")
    if len(name.encode()) > p.NAME_MAX:
        raise HTTPException(400, f"the name is {len(name.encode())} bytes, at most {p.NAME_MAX} fit")
    with audit.timed_action(user=_claims.subject, action="localmesh.name", params={"receiver": req.receiver,
                                                                                  "name": name}):
        await _run(src.set_name(name))
        state.set_name(src.snapshot()["label"], src.net_name)
    return {"saved": True, "name": src.net_name}


class AdvertRequest(BaseModel):
    receiver: Optional[str] = None


@router.post("/advert")
async def advert(
    req: AdvertRequest,
    _claims: SessionClaims = Depends(require_admin),
    audit: AuditLogWriter = Depends(get_audit_writer),
) -> dict:
    src = _source(req.receiver)
    with audit.timed_action(user=_claims.subject, action="localmesh.advert", params={"receiver": req.receiver}):
        sent = await _run(src.send_advert())
    return {"counter": sent.counter, "airtime_ms": sent.airtime_ms}
