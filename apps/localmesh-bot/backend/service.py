"""The running bot: gets every new LocalMesh message from the localmesh plugin (its hooks.py registry), lets bot.decide()
judge it and sends the answer through the same receiver. Settings live in ``plugins.localmesh-bot`` in local.yaml.

The registry is found through sys.modules (see the localmesh plugin's backend/hooks.py): no import between the two
plugins, whichever Meshpoint loads first creates it. registry() below is the same code, on purpose.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import sys
import time
import types
from typing import Optional

from . import bot

logger = logging.getLogger(__name__)

REGISTRY_MODULE = "meshpoint_localmesh_hooks"
CONFIG_SECTION = "localmesh-bot"
NAME_MAX = 32
CHANNEL_TEXT_MAX = 151   # as the localmesh plugin's protocol.py (firmware 8e12ea5): includes "Name: " in front


def registry() -> types.ModuleType:
    """Same as the localmesh plugin's hooks.registry(): whichever plugin loads first creates it."""
    reg = sys.modules.get(REGISTRY_MODULE)
    if reg is None:
        reg = types.ModuleType(REGISTRY_MODULE)
        reg.sources = ()
        reg.message_handlers = []
        sys.modules[REGISTRY_MODULE] = reg
    return reg


def source_label(source) -> str:
    return getattr(source, "_label", "") or ""


def text_max(source) -> int:
    """Bytes of text the receiver can send: the firmware puts its own name in front ("Name: ")."""
    name = (getattr(source, "net_name", None) or "")[:NAME_MAX]
    return CHANNEL_TEXT_MAX - (len(name.encode()) + 2 if name else 0)


class BotService:
    def __init__(self, config: Optional[dict] = None, save=None) -> None:
        self.config = bot.Config.from_dict(config or {})
        self.limiter = bot.Limiter()
        self.log: collections.deque = collections.deque(maxlen=50)
        self._save = save   # callable(dict) that persists the config; None in tests

    # --- the receiver ---

    def sources(self) -> tuple:
        return tuple(registry().sources)

    def source(self):
        """The receiver the bot uses: the configured label, else the first one."""
        sources = self.sources()
        for s in sources:
            if source_label(s) == self.config.receiver:
                return s
        return sources[0] if sources and not self.config.receiver else None

    def attach(self) -> None:
        handlers = registry().message_handlers
        if self.on_message not in handlers:
            handlers.append(self.on_message)

    def detach(self) -> None:
        handlers = registry().message_handlers
        if self.on_message in handlers:
            handlers.remove(self.on_message)

    # --- messages ---

    def on_message(self, source, msg: dict) -> None:
        """Called by the localmesh plugin on the event loop, once per new message. Never blocks: the send is a task."""
        if source is not self.source():
            return
        info = getattr(source, "info", None)
        repeaters = getattr(source, "repeaters", {}) or {}

        def repeater_name(node: int) -> str:
            name = (repeaters.get(node) or {}).get("name")
            return f"{name} ({node})" if name else str(node)

        ctx = bot.Context(own_member=getattr(info, "member", 0) if info else 0,
                          own_name=getattr(source, "net_name", None) or "", now=time.time(),
                          text_max=text_max(source), repeater_name=repeater_name)
        reply, why = bot.decide(msg, self.config, ctx, self.limiter)
        if reply is None:
            if why not in ("off", "not a bot channel", "not a command", "own message"):   # keep the log readable
                self._note(msg, None, why)
            return
        entry = self._note(msg, reply, why)
        asyncio.ensure_future(self._send(source, msg["channel"], reply, entry))

    async def _send(self, source, channel: int, reply: str, entry: dict) -> None:
        try:
            await source.send_text(reply, channel=channel)
            entry["status"] = "sent"
        except Exception as e:   # the receiver's airtime budget, no key, gone: the log says so, the bot carries on
            entry["status"] = f"not sent: {e}"
            logger.info("localmesh-bot: answer not sent: %s", e)

    def _note(self, msg: dict, reply: Optional[str], why: str) -> dict:
        entry = {"time": time.time(), "channel": msg.get("channel"), "member": msg.get("member"),
                 "text": msg.get("text", ""), "reply": reply, "why": why, "status": "" if reply else "quiet"}
        self.log.append(entry)
        return entry

    # --- settings ---

    def set_config(self, d: dict) -> None:
        self.config = bot.Config.from_dict(d)
        if self._save:
            self._save(self.config.to_dict())

    def receivers(self) -> list[dict]:
        """For the Bot tab: each receiver with its name (= the bot's name) and the channels it has a key for."""
        out = []
        for s in self.sources():
            info = getattr(s, "info", None)
            out.append({"label": source_label(s), "name": getattr(s, "net_name", None) or "",
                        "member": getattr(info, "member", None) if info else None,
                        "connected": bool(getattr(s, "connected", False)),
                        "channels": {str(k): v for k, v in (getattr(s, "channels", {}) or {}).items()}})
        return out
