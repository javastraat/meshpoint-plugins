"""The Bot tab's routes. Admin only; a change of the settings goes to the audit log (not the answers' text).

- GET /api/localmesh-bot/config   the settings, the built-in commands and the receivers (name, channels)
- PUT /api/localmesh-bot/config   new settings; 400 with what is wrong
- GET /api/localmesh-bot/log      the last 50 questions with what the bot did (newest last)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.api.audit import AuditLogWriter
from src.api.audit.dependencies import get_audit_writer
from src.api.auth.dependencies import require_admin
from src.api.auth.jwt_session import SessionClaims

from . import bot

router = APIRouter(prefix="/api/localmesh-bot", tags=["localmesh-bot"])
_service = None


def init_routes(service) -> None:
    global _service
    _service = service


@router.get("/config")
async def get_config(_claims: SessionClaims = Depends(require_admin)) -> dict:
    return {"config": _service.config.to_dict(), "builtin": list(bot.BUILTIN), "answer_max": bot.ANSWER_MAX,
            "receivers": _service.receivers()}


@router.put("/config")
async def put_config(body: dict, _claims: SessionClaims = Depends(require_admin),
                     audit: AuditLogWriter = Depends(get_audit_writer)) -> dict:
    errors = bot.validate(body)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    params = {"enabled": bool(body.get("enabled")), "channels": body.get("channels"),
              "commands": len(body.get("commands") or {})}
    with audit.timed_action(user=_claims.subject, action="localmesh-bot.config_update", params=params):
        _service.set_config(body)
    return {"saved": True, "config": _service.config.to_dict()}


@router.get("/log")
async def get_log(_claims: SessionClaims = Depends(require_admin)) -> list:
    return list(_service.log)
