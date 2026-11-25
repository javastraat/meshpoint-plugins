"""Storage in Meshpoint's own database, in three tables of this plugin:

* ``lmr_latest``   one row per repeater: its last status as JSON (so the page is filled right after a restart).
* ``lmr_samples``  one flat row per status: the history. Once per (node, counter): a status is heard several times.
* ``lmr_config``   the settings (here and not in local.yaml, like the reticulum-repeaters plugin).

``db`` is anything with Meshpoint's Database interface (async execute / fetch_one / fetch_all / commit).
"""

from __future__ import annotations

import json
from typing import Optional

SAMPLE_COLS = ("battery_mv", "noise_floor", "uptime_h", "airtime_pct", "queue", "rx_ok", "rx_rejected", "neighbor_count")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS lmr_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS lmr_latest (node INTEGER PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS lmr_samples (node INTEGER NOT NULL, counter INTEGER NOT NULL, ts REAL NOT NULL, "
    + ", ".join(f"{c} REAL" for c in SAMPLE_COLS) + ", PRIMARY KEY (node, counter))",
    "CREATE INDEX IF NOT EXISTS idx_lmr_samples_ts ON lmr_samples(ts)",
)


class StatusStore:
    def __init__(self, db) -> None:
        self._db = db

    async def ensure_schema(self) -> None:
        for sql in _SCHEMA:
            await self._db.execute(sql)
        await self._db.commit()

    async def load_config(self) -> dict:
        row = await self._db.fetch_one("SELECT value FROM lmr_config WHERE key = 'config'")
        return _json(row["value"]) if row else {}

    async def save_config(self, cfg: dict) -> None:
        await self._db.execute("INSERT INTO lmr_config (key, value) VALUES ('config', ?) "
                               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(cfg),))
        await self._db.commit()

    async def load_latest(self) -> dict[int, dict]:
        rows = await self._db.fetch_all("SELECT node, data FROM lmr_latest")
        out = {int(r["node"]): _json(r["data"]) for r in rows}
        return {node: d for node, d in out.items() if d}

    async def has(self, node: int, counter: int) -> bool:
        return await self._db.fetch_one("SELECT 1 AS x FROM lmr_samples WHERE node = ? AND counter = ?",
                                        (node, counter)) is not None

    async def add(self, node: int, st: dict, latest: Optional[dict]) -> None:
        """One status; ``latest`` too when it is the newest of this repeater."""
        await self._db.execute(
            f"INSERT OR IGNORE INTO lmr_samples (node, counter, ts, {', '.join(SAMPLE_COLS)}) "
            f"VALUES (?, ?, ?, {', '.join('?' for _ in SAMPLE_COLS)})",
            (node, st["counter"], st["time"], *(st.get(c) for c in SAMPLE_COLS)))
        if latest is not None:
            await self._db.execute("INSERT INTO lmr_latest (node, data) VALUES (?, ?) "
                                   "ON CONFLICT(node) DO UPDATE SET data = excluded.data", (node, json.dumps(latest)))
        await self._db.commit()

    async def prune(self, before: float) -> None:
        await self._db.execute("DELETE FROM lmr_samples WHERE ts < ?", (before,))
        await self._db.commit()


def _json(text) -> dict:
    try:
        out = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}
