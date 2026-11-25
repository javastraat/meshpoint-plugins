"""StatusStore and RepeaterService against an in-memory SQLite database. Stdlib only."""

from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
import time
import unittest
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[2]   # load backend/ as a package (the folder has a hyphen)
_spec = importlib.util.spec_from_file_location("lmr_backend", _PLUGIN / "backend" / "__init__.py",
                                               submodule_search_locations=[str(_PLUGIN / "backend")])
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["lmr_backend"] = _pkg
_spec.loader.exec_module(_pkg)

from lmr_backend import service as sv  # noqa: E402
from lmr_backend.store import StatusStore  # noqa: E402

NOW = time.time() - 3600   # recent: start() removes what is older than keep_days


class FakeDB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:"); self.c.row_factory = sqlite3.Row
    async def execute(self, sql, params=()): return self.c.execute(sql, params)
    async def fetch_one(self, sql, params=()):
        r = self.c.execute(sql, params).fetchone(); return dict(r) if r else None
    async def fetch_all(self, sql, params=()): return [dict(r) for r in self.c.execute(sql, params).fetchall()]
    async def commit(self): self.c.commit()


class Source:
    def __init__(self, repeaters): self._r = repeaters
    def page_data(self): return {"repeaters": self._r}


def st(node=7, counter=1, t=NOW, up=10, **kw):
    return {"node": node, "counter": counter, "time": t, "uptime_h": up, "battery_mv": 4100, "noise_floor": -110,
            "airtime_pct": 1, "queue": 0, "rx_ok": 5, "rx_rejected": 0, "version": "0.10.7", "neighbor_count": 1,
            "neighbors": [{"node": 8, "snr": 3.0}], "reasons": {}, "name": "", "region": 1, **kw}


class TestService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDB()
        self.svc = sv.RepeaterService(StatusStore(self.db))
        await self.svc.start()
        self.reg = sv.registry(); self.reg.sources = ()

    async def asyncTearDown(self):
        await self.svc.stop(); self.reg.sources = ()

    def count(self):
        return self.db.c.execute("SELECT COUNT(*) FROM lmr_samples").fetchone()[0]

    async def test_a_status_is_kept_once(self):
        self.assertTrue(await self.svc.add(st()))
        self.assertFalse(await self.svc.add(st(t=NOW + 3)))              # the same one through another repeater
        results = await asyncio.gather(*(self.svc.add(st(counter=2)) for _ in range(3)))
        self.assertEqual((sum(results), self.count()), (1, 2))

    async def test_late_copy_is_history_but_not_the_latest(self):
        await self.svc.add(st(counter=5))
        await self.svc.add(st(counter=4, t=NOW + 2, battery_mv=1))
        self.assertEqual((self.svc.latest[7]["counter"], self.count()), (5, 2))

    async def test_one_forged_high_counter_does_not_freeze_a_repeater(self):
        await self.svc.add(st(counter=2 ** 32 - 1))
        await self.svc.add(st(counter=6, t=NOW + 3600, up=11))
        self.assertEqual(self.svc.latest[7]["counter"], 6)

    async def test_survives_a_restart_of_meshpoint(self):
        await self.svc.add(st(name="NL-1"))
        await self.svc.set_config({"keep_days": 60, "hidden": [9]})
        again = sv.RepeaterService(StatusStore(self.db))
        await again.start()
        self.assertEqual((again.latest[7]["name"], again.config), ("NL-1", {"keep_days": 60, "hidden": [9]}))
        await again.stop()

    async def test_status_comes_in_through_the_hook(self):
        class Ws:
            sent = []
            async def broadcast(self, *a): self.sent.append(a)
        self.svc._ws = Ws()
        for handler in list(self.reg.status_handlers): handler(None, st())
        await asyncio.gather(*self.svc._tasks)
        self.assertIn(7, self.svc.latest)
        self.assertEqual(Ws.sent, [("localmesh_repeater", {"node": 7})])
        await self.svc.stop()
        self.assertNotIn(self.svc._on_status, self.reg.status_handlers)

    async def test_restarts_are_counted(self):
        await self.svc.add(st(counter=1, up=50))
        await self.svc.add(st(counter=2, up=51, t=NOW + 3600))
        self.assertEqual(self.svc.latest[7]["restarts"], 0)
        await self.svc.add(st(counter=3, up=0, t=NOW + 7200))
        self.assertEqual((self.svc.latest[7]["restarts"], self.svc.latest[7]["restart_at"]), (1, NOW + 7200))
        await self.svc.add(st(counter=4, up=1, t=NOW + 10800))
        self.assertEqual((self.svc.latest[7]["restarts"], self.svc.latest[7]["restart_at"]), (1, NOW + 7200))

    def test_restarted(self):
        h = 3600
        self.assertFalse(sv.restarted(st(up=10), st(up=11, t=NOW + 2 * h)))         # whole hours
        self.assertTrue(sv.restarted(st(up=10), st(up=40, t=NOW + 100 * h)))        # missed some, up less than it should be
        self.assertFalse(sv.restarted(st(up=1192), st(up=0, t=NOW + 2 * h)))        # 49.7 days: the counter starts again
        self.assertTrue(sv.restarted(st(up=1000), st(up=0, t=NOW + h)))

    async def test_overview(self):
        self.reg.sources = (Source({7: {"node": 7, "name": "NL-7", "region": 1, "revoked": False},
                                    8: {"node": 8, "name": "NL-8", "region": 2, "revoked": False},
                                    9: {"node": 9, "name": "gone", "region": 2, "revoked": True}}),)
        await self.svc.add(st()); await self.svc.add(st(node=5, region=None))
        await self.svc.set_config({"hidden": [5]})
        out = self.svc.overview()
        self.assertEqual([r["node"] for r in out["repeaters"]], [7, 5, 8])          # with a status first; 9 is revoked
        r7, r5, r8 = out["repeaters"]
        self.assertEqual(r7["status"]["neighbors"], [{"node": 8, "snr": 3.0, "name": "NL-8"}])
        self.assertEqual((r5["hidden"], r5["listed"], r8["status"]), (True, False, None))

    async def test_old_samples_go(self):
        await self.svc.add(st(counter=1, t=NOW - 40 * 86400)); await self.svc.add(st(counter=2))
        await self.svc.set_config({"keep_days": 30})
        self.assertEqual(self.count(), 1)

    def test_clean_config(self):
        self.assertEqual(sv.clean_config({"keep_days": "x", "hidden": [3, "a", -1, 3]}), {"keep_days": 30, "hidden": [3]})
        self.assertEqual(sv.clean_config({"keep_days": 9999, "hidden": "no"}), {"keep_days": 365, "hidden": []})


if __name__ == "__main__":
    unittest.main()
