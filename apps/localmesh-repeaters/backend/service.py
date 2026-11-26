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
CATCH_UP_EVERY_S = 30         # what the hook missed (a status that came in before this service had started)
RESTART_SLACK_H = 2          # whole hours and delays on the way: less than this is not a restart
MILLIS_WRAP_H = 2 ** 32 / 3_600_000   # the board's uptime starts again at 0 after 49.7 days without a restart
UNLISTED_KEEP_S = 7 * 86400  # as localmesh's views.repeaters
REPEATER_MEMBER_BASE = 0xFF0000
RECENT = 8
HEARD_KEYS = ("last_heard", "rssi", "snr", "via", "direct", "count", "type", "version", "announced")
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
        self._catch_up_task: Optional[asyncio.Task] = None
        self.latest: dict[int, dict] = {}
        self.config = dict(DEFAULTS)

    async def start(self) -> None:
        await self._store.ensure_schema()
        self.config = clean_config(await self._store.load_config())
        self.latest = await self._store.load_latest()
        registry().status_handlers.append(self._on_status)
        self._prune_task = asyncio.create_task(self._prune_loop())
        self._catch_up_task = asyncio.create_task(self._catch_up_loop())

    async def stop(self) -> None:
        if self._on_status in registry().status_handlers:
            registry().status_handlers.remove(self._on_status)
        for task in (self._prune_task, self._catch_up_task):
            if task:
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _prune_loop(self) -> None:
        while True:
            try:
                await self._store.prune(time.time() - self.config["keep_days"] * 86400)
            except Exception:
                logger.exception("localmesh-repeaters: could not remove old samples")
            await asyncio.sleep(PRUNE_EVERY_S)

    async def _catch_up_loop(self) -> None:
        while True:
            await self.catch_up()
            await asyncio.sleep(CATCH_UP_EVERY_S)

    async def catch_up(self) -> None:
        """The receivers' own last status per repeater, in case the hook missed one."""
        for source in registry().sources:
            try:
                d = source.page_data()
                for node, st in d.get("statuses", {}).items():
                    if "counter" in st:   # an older localmesh does not keep it
                        cert = d.get("repeaters", {}).get(node) or {}
                        await self._keep({**st, "node": node, "name": cert.get("name", ""), "region": cert.get("region")})
            except Exception:
                logger.exception("localmesh-repeaters: could not read a receiver's statuses")

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
        """Every repeater we know: what localmesh knows of it (certificate, last heard, its packets) and its last status."""
        heard = heard_repeaters()
        names = {n: d.get("name", "") for n, d in self.latest.items()}
        names.update({n: r["name"] for n, r in heard.items() if r.get("name")})
        recent = recent_packets(set(heard))
        now = time.time()
        out = []
        for node in set(heard) | set(self.latest):
            r = heard.get(node, {})
            st = self.latest.get(node)
            if r.get("revoked") and st is None:
                continue
            if not r and st and now - st["time"] > UNLISTED_KEEP_S:
                continue   # an old one localmesh does not know any more
            if st:
                st = {**st, "neighbors": [{**n, "name": names.get(n["node"], "")} for n in st.get("neighbors", [])]}
            out.append({"node": node, "name": names.get(node, ""), "listed": bool(r.get("listed")),
                        "revoked": bool(r.get("revoked")), "hidden": node in self.config["hidden"],
                        "region": r["region"] if r.get("region") is not None else (st or {}).get("region"), "status": st,
                        "heard": {k: r.get(k) for k in HEARD_KEYS} if r.get("last_heard") else None,
                        "recent": recent.get(node, [])})
        out.sort(key=lambda r: (r["status"] is None and r["heard"] is None, r["name"] or "~", r["node"]))
        return {"repeaters": out, "settings": dict(self.config), "status_key_set": status_key_set(),
                "localmesh_too_old": localmesh_too_old()}


def status_key_set() -> Optional[bool]:
    """Does localmesh have a status key? None when that cannot be seen (an older localmesh)."""
    state = sys.modules.get("meshpoint_plugin_localmesh.state")
    fn = getattr(state, "status_key_set", None)
    return bool(fn()) if fn else None


def localmesh_too_old() -> bool:
    """A localmesh without the status hook opens statuses but never hands them over."""
    hooks = sys.modules.get("meshpoint_plugin_localmesh.hooks")
    return hooks is not None and not hasattr(hooks, "emit_repeater_status")


def heard_repeaters() -> dict[int, dict]:
    """localmesh's own list of repeaters (its views.repeaters, the same the LocalMesh page shows), by node."""
    sources = registry().sources
    views = sys.modules.get("meshpoint_plugin_localmesh.views")
    try:
        if views is not None:
            return {r["node"]: r for r in views.repeaters([(s.snapshot()["label"], s.page_data()) for s in sources])}
    except Exception:
        logger.exception("localmesh-repeaters: could not read localmesh's repeaters")
    out: dict[int, dict] = {}
    for s in sources:   # at least the certificates
        for node, r in s.page_data().get("repeaters", {}).items():
            out.setdefault(node, {**r, "listed": True})
    return out


def recent_packets(nodes: set[int]) -> dict[int, list[dict]]:
    """Each repeater's own last packets, newest first (from the receivers' Activity lists)."""
    out: dict[int, list[dict]] = {}
    rows = [a for s in registry().sources for a in s.page_data().get("activity", [])
            if a["member"] - REPEATER_MEMBER_BASE in nodes]
    seen: set = set()
    for a in sorted(rows, key=lambda a: -a["time"]):
        node, key = a["member"] - REPEATER_MEMBER_BASE, (a["member"], a["counter"])
        if key in seen or len(out.setdefault(node, [])) >= RECENT:   # the same one again, through another repeater
            continue
        seen.add(key)
        out[node].append({k: a.get(k) for k in ("time", "type", "counter", "hops", "via", "rssi", "snr")})
    return out
