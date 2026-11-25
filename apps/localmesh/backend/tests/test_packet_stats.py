"""GET /api/localmesh/packet-stats, on an in-memory packets table."""

from __future__ import annotations

import asyncio
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from plugins.apps.localmesh.backend import routes


class DB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:"); self.c.row_factory = sqlite3.Row; self.asked = 0
        self.c.execute("CREATE TABLE packets (protocol, rssi, timestamp)")

    def add(self, minutes, rssi, protocol="localmesh"):
        t = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        self.c.execute("INSERT INTO packets VALUES (?, ?, ?)", (protocol, rssi, t.isoformat()))

    async def fetch_one(self, sql, params=()):
        self.asked += 1
        return dict(self.c.execute(sql, params).fetchone())


class TestPacketStats(unittest.TestCase):
    def tearDown(self): routes.init_routes([])

    def get(self): return asyncio.run(routes.packet_stats())

    def test_counts(self):
        db = DB(); db.add(1, -50); db.add(60, -70); db.add(3000, -10); db.add(1, -1, "meshtastic")
        routes.init_routes([], db)
        self.assertEqual(self.get(), {"total": 3, "day": 2, "per_minute": 0.1, "avg_rssi": -60.0})
        asked = db.asked
        self.get()
        self.assertEqual(db.asked, asked)                         # kept for a while
        with mock.patch.object(routes, "STATS_EVERY_S", -1): self.get()
        self.assertGreater(db.asked, asked)

    def test_nothing_yet(self):
        routes.init_routes([], DB())
        self.assertEqual(self.get(), {"total": 0, "day": 0, "per_minute": 0.0, "avg_rssi": None})
        routes.init_routes([])
        self.assertEqual(self.get(), {})


if __name__ == "__main__":
    unittest.main()
