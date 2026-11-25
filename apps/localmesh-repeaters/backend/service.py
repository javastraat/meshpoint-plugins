"""Takes every repeater status the localmesh plugin could open (its hooks.py), keeps it, and lists the repeaters.

Statuses are unverified (see localmesh's repeater_status.py): good for a page, not for decisions. They contain nothing
about members; localmesh leaves that out.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import types
from typing import Any, Optional

from .store import StatusStore

logger = logging.getLogger(__name__)

REGISTRY_MODULE = "meshpoint_localmesh_hooks"
DEFAULTS: dict[str, Any] = {"keep_days": 30, "hidden": []}
PRUNE_EVERY_S = 86400
RESTART_SLACK_H = 2          # whole hours and delays on the way: less than this is not a restart
MILLIS_WRAP_H = 2 ** 32 / 3_600_000   # the board's uptime starts again at 0 after 49.7 days without a restart
LATE_COPY_S = 600            # an older counter within this time is a copy that took a detour


def registry() -> types.ModuleType:
    """The same registry as localmesh's hooks.registry(): whichever plugin comes first creates it."""
    reg = sys.modules.get(REGISTRY_MODULE)
    if reg is None:
        reg = types.ModuleType(REGISTRY_MODULE)
        reg.sources = ()
        reg.message_handlers = []
        sys.modules[REGISTRY_MODULE] = reg
    if not hasattr(reg, "status_handlers"):
        reg.status_handlers = []
    return reg


def clean_config(raw: dict) -> dict:
    try:
        keep = max(1, min(365, int(raw.get("keep_days", DEFAULTS["keep_days"]))))
    except (TypeError, ValueError):
        keep = DEFAULTS["keep_days"]
    hidden = raw.get("hidden")
    nodes = {n for n in hidden if isinstance(n, int) and 0 <= n <= 0xFFFF} if isinstance(hidden, list) else set()
    return {"keep_days": keep, "hidden": sorted(nodes)}


def restarted(prev: dict, st: dict) -> bool:
    """Did it restart between two statuses? Its uptime is then less than it should have become."""
    expected = prev["uptime_h"] + max(0.0, st["time"] - prev["time"]) / 3600
    if st["uptime_h"] >= expected - RESTART_SLACK_H:
        return False
    if expected >= MILLIS_WRAP_H - RESTART_SLACK_H and abs(st["uptime_h"] - expected % MILLIS_WRAP_H) <= RESTART_SLACK_H:
        return False
    return True


class RepeaterService:
    def __init__(self, store: StatusStore, ws_manager=None) -> None:
        self._store = store
        self._ws = ws_manager
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._prune_task: Optional[asyncio.Task] = None
        self.latest: dict[int, dict] = {}
        self.config = dict(DEFAULTS)

    async def start(self) -> None:
        await self._store.ensure_schema()
        self.config = clean_config(await self._store.load_config())
        self.latest = await self._store.load_latest()
        registry().status_handlers.append(self._on_status)
        self._prune_task = asyncio.create_task(self._prune_loop())

    async def stop(self) -> None:
        if self._on_status in registry().status_handlers:
            registry().status_handlers.remove(self._on_status)
        if self._prune_task:
            self._prune_task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _prune_loop(self) -> None:
        while True:
            try:
                await self._store.prune(time.time() - self.config["keep_days"] * 86400)
            except Exception:
                logger.exception("localmesh-repeaters: could not remove old samples")
            await asyncio.sleep(PRUNE_EVERY_S)

    def _on_status(self, _source, st: dict) -> None:
        """Called on the event loop by localmesh; must not block."""
        task = asyncio.ensure_future(self._keep(st))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _keep(self, st: dict) -> None:
        try:
            if await self.add(st) and self._ws is not None:
                await self._ws.broadcast("localmesh_repeater", {"node": st["node"]})
        except Exception:
            logger.exception("localmesh-repeaters: could not keep a status")

    async def add(self, st: dict) -> bool:
        """False for a status we have already (the same one through another repeater or receiver)."""
        node = st["node"]
        async with self._lock:
            if await self._store.has(node, st["counter"]):
                return False
            prev = self.latest.get(node)
            late = prev is not None and st["counter"] < prev["counter"] and st["time"] - prev["time"] < LATE_COPY_S
            latest = None
            if not late:
                latest = {**st, "restart_at": (prev or {}).get("restart_at"), "restarts": (prev or {}).get("restarts", 0)}
                if prev and restarted(prev, st):
                    latest["restart_at"] = st["time"] - st["uptime_h"] * 3600
                    latest["restarts"] += 1
                latest["name"] = st.get("name") or (prev or {}).get("name", "")
            await self._store.add(node, st, latest)
            if latest is not None:
                self.latest[node] = latest
            return True

    async def set_config(self, cfg: dict) -> None:
        self.config = clean_config(cfg)
        await self._store.save_config(self.config)
        await self._store.prune(time.time() - self.config["keep_days"] * 86400)

    def overview(self) -> dict[str, Any]:
        """Every repeater we know: from the server's certificates (the receivers' lists) and from a status."""
        known: dict[int, dict] = {}
        for source in registry().sources:
            for node, r in source.page_data().get("repeaters", {}).items():
                known.setdefault(node, r)
        names = {n: d.get("name", "") for n, d in self.latest.items()}
        names.update({n: r["name"] for n, r in known.items() if r.get("name")})
        out = []
        for node in set(known) | set(self.latest):
            cert = known.get(node)
            st = self.latest.get(node)
            if cert and cert.get("revoked") and st is None:
                continue
            if st:
                st = {**st, "neighbors": [{**n, "name": names.get(n["node"], "")} for n in st.get("neighbors", [])]}
            out.append({"node": node, "name": names.get(node, ""), "listed": cert is not None,
                        "revoked": bool(cert and cert.get("revoked")), "hidden": node in self.config["hidden"],
                        "region": cert["region"] if cert else (st or {}).get("region"), "status": st})
        out.sort(key=lambda r: (r["status"] is None, r["name"] or "~", r["node"]))
        return {"repeaters": out, "settings": dict(self.config), "status_key_set": status_key_set()}


def status_key_set() -> Optional[bool]:
    """Does localmesh have a status key? None when that cannot be seen (an older localmesh)."""
    state = sys.modules.get("meshpoint_plugin_localmesh.state")
    fn = getattr(state, "status_key_set", None)
    return bool(fn()) if fn else None
