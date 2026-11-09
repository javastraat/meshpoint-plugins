"""Storage in meshpoint's own SQLite database (``context.pipeline.database``),
in two plugin-owned tables created on start:

* ``rr_latest`` -- one row per repeater: the last full snapshot (every
  /api/* reply plus poll status) as JSON, so the page has data straight
  after a meshpoint restart.
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

    async def prune(self) -> None:
        await self._db.execute("DELETE FROM rr_samples WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
        await self._db.commit()
