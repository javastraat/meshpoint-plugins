"""Storage in meshpoint's own SQLite database (``context.pipeline.database``),
in two plugin-owned tables created on start:

* ``rr_latest`` -- one row per repeater: the last full snapshot (every
  /api/* reply plus poll status) as JSON, so the page has data straight
  after a meshpoint restart.
* ``rr_config`` -- the plugin's settings and repeater list (one JSON row).
  Kept here, not in local.yaml: meshpoint core rewrites a plugin's
  local.yaml section from its startup copy when the plugin is updated
  through Plugin sources or switched on/off, which wiped the list
  (2026-11-09). ``enabled`` / ``source`` stay in local.yaml (core's).
* ``rr_samples`` -- the history behind the Trends chart: one flat row per
  stats poll, plus the rows backfilled from the repeater's own /api/hist
  (``source = 'hist'``) when it's added. Pruned after ``KEEP_DAYS``.

``db`` is anything with meshpoint's ``Database`` interface (async
``execute`` / ``fetch_all`` / ``fetch_one`` / ``commit``).
"""

from __future__ import annotations

import json
import time
from typing import Optional

KEEP_DAYS = 60

# Flat sample columns, in table order (all nullable REALs except the keys).
SAMPLE_COLS = (
    "percent", "voltage", "charging", "temp", "humidity", "pressure", "mcu",
    "noise", "rssi", "snr", "airtime", "free_pool", "frag", "free_ram",
    "links", "routes", "neighbours", "rx", "tx", "uptime",
)

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS rr_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS rr_latest (
        repeater TEXT PRIMARY KEY,
        data TEXT NOT NULL
    )""",
    "CREATE TABLE IF NOT EXISTS rr_samples (repeater TEXT NOT NULL, ts REAL NOT NULL, source TEXT NOT NULL, "
    + ", ".join(f"{c} REAL" for c in SAMPLE_COLS) + ")",
    "CREATE INDEX IF NOT EXISTS idx_rr_samples_rep_ts ON rr_samples(repeater, ts)",
]


class RepeaterStore:
    def __init__(self, db) -> None:
        self._db = db

    async def ensure_schema(self) -> None:
        for sql in _SCHEMA:
            await self._db.execute(sql)
        await self._db.commit()

    async def load_config(self) -> Optional[dict]:
        row = await self._db.fetch_one("SELECT value FROM rr_config WHERE key = 'config'")
        if not row:
            return None
        try:
            cfg = json.loads(row["value"])
        except (TypeError, ValueError):
            return None
        return cfg if isinstance(cfg, dict) else None

    async def save_config(self, cfg: dict) -> None:
        await self._db.execute(
            "INSERT INTO rr_config (key, value) VALUES ('config', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(cfg),),
        )
        await self._db.commit()

    async def load_latest(self) -> dict[str, dict]:
        rows = await self._db.fetch_all("SELECT repeater, data FROM rr_latest")
        out = {}
        for r in rows:
            try:
                out[r["repeater"]] = json.loads(r["data"])
            except (TypeError, ValueError):
                continue
        return out

    async def save_latest(self, repeater: str, snapshot: dict) -> None:
        await self._db.execute(
            "INSERT INTO rr_latest (repeater, data) VALUES (?, ?) "
            "ON CONFLICT(repeater) DO UPDATE SET data = excluded.data",
            (repeater, json.dumps(snapshot)),
        )
        await self._db.commit()

    async def delete(self, repeater: str) -> None:
        await self._db.execute("DELETE FROM rr_latest WHERE repeater = ?", (repeater,))
        await self._db.execute("DELETE FROM rr_samples WHERE repeater = ?", (repeater,))
        await self._db.commit()

    async def add_samples(self, repeater: str, samples: list[dict], source: str) -> None:
        cols = ", ".join(SAMPLE_COLS)
        marks = ", ".join("?" for _ in SAMPLE_COLS)
        for s in samples:
            await self._db.execute(
                f"INSERT INTO rr_samples (repeater, ts, source, {cols}) VALUES (?, ?, ?, {marks})",
                (repeater, float(s["ts"]), source, *(s.get(c) for c in SAMPLE_COLS)),
            )
        await self._db.commit()

    async def oldest_poll_ts(self, repeater: str) -> Optional[float]:
        row = await self._db.fetch_one(
            "SELECT MIN(ts) AS t FROM rr_samples WHERE repeater = ? AND source = 'poll'", (repeater,),
        )
        return row["t"] if row and row["t"] is not None else None

    async def history(self, repeater: str, hours: float, limit: int = 800) -> list[dict]:
        since = time.time() - hours * 3600
        rows = await self._db.fetch_all(
            "SELECT ts, source, " + ", ".join(SAMPLE_COLS)
            + " FROM rr_samples WHERE repeater = ? AND ts >= ? ORDER BY ts ASC",
            (repeater, since),
        )
        if len(rows) > limit:  # keep the newest point, thin the rest evenly
            step = -(-len(rows) // limit)
            rows = rows[::step] + ([rows[-1]] if (len(rows) - 1) % step else [])
        return rows

    async def peer_names(self, prefixes: set[str]) -> dict[str, str]:
        """Names from the reticulum plugin's peer roster (``reticulum_peers``,
        built from announces) for hash prefixes as a repeater's lists send
        them (8 hex with s=1). A prefix matching peers with different names
        gets none; a missing table (reticulum never ran) gives {}."""
        by_len: dict[int, list[str]] = {}
        for p in prefixes:
            p = (p or "").lower()
            if 4 <= len(p) <= 32 and all(c in "0123456789abcdef" for c in p):
                by_len.setdefault(len(p), []).append(p)
        found: dict[str, set[str]] = {}
        for n, group in by_len.items():
            for i in range(0, len(group), 200):
                chunk = group[i:i + 200]
                try:
                    rows = await self._db.fetch_all(
                        f"SELECT substr(destination_hash, 1, {n}) AS p, display_name FROM reticulum_peers "
                        f"WHERE substr(destination_hash, 1, {n}) IN ({', '.join('?' for _ in chunk)}) "
                        "AND display_name IS NOT NULL AND display_name != ''",
                        tuple(chunk),
                    )
                except Exception:  # noqa: BLE001 -- no roster table: no names
                    return {}
                for r in rows:
                    found.setdefault(r["p"], set()).add(r["display_name"])
        return {p: next(iter(names)) for p, names in found.items() if len(names) == 1}

    async def prune(self) -> None:
        await self._db.execute("DELETE FROM rr_samples WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
        await self._db.commit()
