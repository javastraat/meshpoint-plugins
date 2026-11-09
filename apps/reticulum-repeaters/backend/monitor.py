"""RepeaterMonitor -- polls the ScotMesh repeaters you added, over NomadNet.

A ScotMesh microReticulum repeater answers JSON requests on its NomadNet
destination without needing an identity: ``/api/all`` (battery, uptime,
network, airtime), ``/api/radio``, ``/api/health``, ``/api/env`` (sensors),
``/api/sys`` (firmware, name), ``/api/pos``, the lists ``/api/nbrs``,
``/api/paths``, ``/api/links`` (paged, ``n`` <= 15) and ``/api/hist`` (the
repeater's own 7-day hourly history, paged). Each reply fits one packet.

Tiers, to keep LoRa airtime low (a full reply is ~1 s at SF8/125 kHz):

* every ``interval_min`` (default 15): all, radio, health, env
* every ``lists_interval_min`` (default 60): nbrs, paths, links
* once a day: sys, pos
* only when a repeater is added: hist (backfill), plus everything above

"Refresh now" runs everything except hist, at most once a minute per
repeater. One asyncio.Lock per repeater keeps a refresh and the timer
from polling the same node at once.

Only repeaters in the config are ever contacted. The fetch goes through
the reticulum plugin's own NomadNet client (``meshpoint_plugin_reticulum
.nomad.fetch_page``) -- same RNS attach, same link cache; injectable for
tests. FastAPI-free.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from typing import Awaitable, Callable, Optional

from . import state
from .store import RepeaterStore

logger = logging.getLogger(__name__)

STATS = ("all", "radio", "health", "env")
LISTS = ("nbrs", "paths", "links")
INFO = ("sys", "pos")
INFO_EVERY_S = 86400
REFRESH_COOLDOWN_S = 60
TICK_S = 30
LIST_PAGE = 15
LIST_MAX_ROWS = 45         # 3 pages: enough for a repeater's neighbours/routes
HIST_PAGE = 24
HIST_MAX_PAGES = 10

# (ok, parsed JSON or None, error text)
FetchFn = Callable[[str, str, Optional[dict]], Awaitable[tuple[bool, Optional[dict], str]]]


async def nomad_fetch(dest: str, path: str, data: Optional[dict] = None) -> tuple[bool, Optional[dict], str]:
    """Default fetch: the reticulum plugin's NomadNet client."""
    nomad = sys.modules.get("meshpoint_plugin_reticulum.nomad")
    if nomad is None:
        return False, None, "Reticulum plugin isn't loaded"
    result = await nomad.fetch_page(dest, path, data)
    if not result.ok:
        return False, None, result.error or "no reply"
    try:
        parsed = json.loads(result.content or "")
    except ValueError:
        return False, None, "reply is not JSON -- is this a ScotMesh repeater?"
    if not isinstance(parsed, dict):
        return False, None, "unexpected reply"
    return True, parsed, ""


def flatten(snap: dict, ts: float) -> dict:
    """One rr_samples row from a snapshot's all/radio/health/env replies."""
    a, r, h, e = (snap.get(k) or {} for k in STATS)

    def num(d, k):
        v = d.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    return {
        "ts": ts,
        "percent": num(a, "percent"), "voltage": num(a, "voltage"),
        "charging": (1.0 if a.get("charging") else 0.0) if "charging" in a else None,
        "temp": num(e, "temp"), "humidity": num(e, "humidity"), "pressure": num(e, "pressure"),
        "mcu": num(e, "mcu_temp") if "mcu_temp" in e else num(e, "mcu"),
        "noise": num(r, "noise"), "rssi": num(r, "rssi"), "snr": num(r, "snr"),
        "airtime": num(r, "airtime") if "airtime" in r else num(a, "airtime"),
        "free_pool": num(h, "free_pool"), "frag": num(h, "pool_fragmented"), "free_ram": num(h, "free_ram"),
        "links": num(a, "links"), "routes": num(a, "routes"), "neighbours": num(a, "neighbours"),
        "rx": num(a, "rx"), "tx": num(a, "tx"), "uptime": num(a, "uptime"),
    }


def hist_samples(pages: list[dict], fetched_at: float) -> list[dict]:
    """/api/hist pages -> rr_samples rows. Rows are newest first, ``h`` =
    hours before the newest record, which was written ``last_s`` before
    the fetch."""
    out = []
    for page in pages:
        cols = page.get("cols") or []
        newest = fetched_at - float(page.get("last_s") or 0)
        for row in page.get("rows") or []:
            if not isinstance(row, list) or len(row) != len(cols):
                continue
            d = dict(zip(cols, row))
            if not isinstance(d.get("h"), (int, float)):
                continue
            out.append({
                "ts": newest - float(d["h"]) * 3600,
                "percent": d.get("pct"),
                "voltage": d["mv"] / 1000.0 if isinstance(d.get("mv"), (int, float)) and d["mv"] else None,
                "charging": d.get("chg"),
                "temp": d.get("t"), "humidity": d.get("hum"), "pressure": d.get("hpa"), "mcu": d.get("mcu"),
                "neighbours": d.get("nbrs"), "routes": d.get("paths"),
            })
    return out


class RepeaterMonitor:
    def __init__(self, store: RepeaterStore, fetch: FetchFn = nomad_fetch, ws_manager=None) -> None:
        self._store = store
        self._fetch = fetch
        self._ws = ws_manager
        self._task: Optional[asyncio.Task] = None
        self._locks: dict[str, asyncio.Lock] = {}
        self.latest: dict[str, dict] = {}

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        await self._store.ensure_schema()
        self.latest = await self._store.load_latest()
        self._task = asyncio.create_task(self._loop())
        logger.info("Reticulum repeaters: monitoring %d repeater(s)", len(state.repeaters()))

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        last_prune = 0.0
        while True:
            try:
                if state.polling_enabled():
                    for rep in state.repeaters():
                        what = self._due(rep["hash"], time.time())
                        if what:
                            await self.poll(rep["hash"], what)
                if time.time() - last_prune > 86400:
                    last_prune = time.time()
                    await self._store.prune()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 -- one bad poll never stops the loop
                logger.exception("Reticulum repeaters: poll loop error")
            await asyncio.sleep(TICK_S)

    def _due(self, h: str, now: float) -> set[str]:
        snap = self.latest.get(h) or {}
        at = snap.get("at") or {}
        what: set[str] = set()
        if now - (at.get("stats") or 0) >= state.interval_s():
            what.update(STATS)
        if now - (at.get("lists") or 0) >= state.lists_interval_s():
            what.update(LISTS)
        if now - (at.get("info") or 0) >= INFO_EVERY_S:
            what.update(INFO)
        return what

    # --- polling ----------------------------------------------------------

    def can_refresh(self, h: str, now: Optional[float] = None) -> tuple[bool, int]:
        """(allowed, seconds to wait)."""
        last = (self.latest.get(h) or {}).get("refresh_at") or 0
        wait = int(REFRESH_COOLDOWN_S - ((now or time.time()) - last))
        return wait <= 0, max(0, wait)

    def mark_refresh(self, h: str) -> None:
        """Start the cooldown and show "Fetching..." straight away."""
        snap = self.latest.setdefault(h, {})
        snap["refresh_at"] = time.time()
        snap["polling"] = True

    async def refresh(self, h: str) -> dict:
        self.mark_refresh(h)
        return await self.poll(h, set(STATS + LISTS + INFO))

    async def add(self, h: str) -> dict:
        """First contact: everything, plus the 7-day history backfill."""
        return await self.poll(h, set(STATS + LISTS + INFO + ("hist",)))

    async def forget(self, h: str) -> None:
        self.latest.pop(h, None)
        await self._store.delete(h)

    async def poll(self, h: str, what: set[str]) -> dict:
        lock = self._locks.setdefault(h, asyncio.Lock())
        async with lock:
            return await self._poll_locked(h, what)

    async def _poll_locked(self, h: str, what: set[str]) -> dict:
        now = time.time()
        snap = self.latest.setdefault(h, {})
        snap.setdefault("at", {})
        prev_reboots = (snap.get("health") or {}).get("reboots")
        snap["polling"] = True
        errors: list[str] = []
        got_any = False

        # /api lists the endpoints this build has: /api/env and /api/hist
        # exist only on boards with sensors / history (P1), and a request to
        # one that isn't registered just times out. Read once, then daily.
        if not snap.get("endpoints") or any(e in what for e in INFO):
            ok, data, err = await self._fetch(h, "/api", None)
            if ok and isinstance(data.get("api"), list):
                snap["endpoints"] = [str(x) for x in data["api"]]
                got_any = True
            elif not snap.get("endpoints"):
                errors.append(f"api: {err or 'not a ScotMesh repeater'}")
        have = set(snap.get("endpoints") or [])

        for ep in [e for e in INFO + STATS if e in what and e in have]:
            if errors and not got_any:
                break                           # node unreachable, don't hammer it
            ok, data, err = await self._fetch(h, f"/api/{ep}", None)
            if ok:
                got_any = True
                if ep == "env" and data.get("error"):
                    snap["env"] = None          # no sensor on this board
                elif ep == "pos" and data.get("error"):
                    snap["pos"] = None          # location not shared
                else:
                    snap[ep] = data
            else:
                errors.append(f"{ep}: {err}")

        if got_any and any(e in what for e in LISTS):
            for ep in [e for e in LISTS if e in have]:
                rows, total = await self._fetch_list(h, ep, errors)
                if rows is not None:
                    snap[ep] = {"total": total, "rows": rows, "cols": snap.get(f"_{ep}_cols")}

        if got_any and "hist" in what and "hist" in have:
            await self._backfill(h, now, errors)

        if got_any:
            if any(e in what for e in STATS):
                snap["at"]["stats"] = now
                await self._store.add_samples(h, [flatten(snap, now)], "poll")
            if any(e in what for e in LISTS):
                snap["at"]["lists"] = now
            if any(e in what for e in INFO):
                snap["at"]["info"] = now
            snap["last_ok"] = now
            reboots = (snap.get("health") or {}).get("reboots")
            if isinstance(prev_reboots, int) and isinstance(reboots, int) and reboots > prev_reboots:
                snap["restart"] = {"at": now, "from": prev_reboots, "to": reboots,
                                   "reason": (snap.get("health") or {}).get("reset")}
                logger.warning("Reticulum repeaters: %s restarted (#%s, %s)", h[:8], reboots,
                               snap["restart"]["reason"])
            name = (snap.get("sys") or {}).get("name")
            if name:
                state.set_name(h, name)
        else:
            # Unreachable: leave the "due" times alone so it's retried next
            # tick, but no faster than the stats interval would allow.
            snap["at"]["stats"] = now - state.interval_s() + max(TICK_S, min(state.interval_s(), 300))

        snap["polled_at"] = now
        snap["ok"] = got_any and not errors
        snap["error"] = "; ".join(errors)[:300] if errors else ""
        snap["polling"] = False
        await self._store.save_latest(h, snap)
        if self._ws is not None:
            try:
                await self._ws.broadcast("reticulum_repeater", {"hash": h})
            except Exception:  # noqa: BLE001
                pass
        return snap

    async def _fetch_list(self, h: str, ep: str, errors: list[str]) -> tuple[Optional[list], int]:
        rows: list = []
        total = 0
        o = 0
        while len(rows) < LIST_MAX_ROWS:
            ok, data, err = await self._fetch(h, f"/api/{ep}", {"o": str(o), "n": str(LIST_PAGE), "s": "1"})
            if not ok:
                errors.append(f"{ep}: {err}")
                return (rows or None), total
            self.latest[h][f"_{ep}_cols"] = data.get("cols")
            page = data.get("rows") or []
            total = int(data.get("total") or 0)
            rows.extend(page)
            o += len(page)
            if not page or not data.get("more"):
                break
        return rows, total

    async def _backfill(self, h: str, now: float, errors: list[str]) -> None:
        """The repeater's own 7-day hourly history, only for the hours
        before this plugin's first own poll (so a re-add doesn't double)."""
        first_own = await self._store.oldest_poll_ts(h)
        pages, o = [], 0
        for _ in range(HIST_MAX_PAGES):
            ok, data, err = await self._fetch(h, "/api/hist", {"o": str(o), "n": str(HIST_PAGE)})
            if not ok:
                if "unknown endpoint" not in (err or ""):
                    errors.append(f"hist: {err}")
                break
            if data.get("error"):
                break                           # board without history
            pages.append(data)
            got = len(data.get("rows") or [])
            o += got
            if not got or not data.get("more"):
                break
        samples = hist_samples(pages, now)
        if first_own is not None:
            samples = [s for s in samples if s["ts"] < first_own]
        if samples:
            await self._store.add_samples(h, samples, "hist")
