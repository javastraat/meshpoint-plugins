"""RepeaterMonitor / RepeaterStore / state against a fake repeater and an
in-memory SQLite database. Stdlib only -- runs on the dev Mac."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import unittest
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[2]
if str(_PLUGIN.parent) not in sys.path:
    sys.path.insert(0, str(_PLUGIN.parent))

import importlib.util  # noqa: E402

# Load backend/ as a package under a test name (the folder has a hyphen).
_spec = importlib.util.spec_from_file_location(
    "rr_backend", _PLUGIN / "backend" / "__init__.py",
    submodule_search_locations=[str(_PLUGIN / "backend")],
)
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["rr_backend"] = _pkg
_spec.loader.exec_module(_pkg)

from rr_backend import monitor as m  # noqa: E402
from rr_backend import state  # noqa: E402
from rr_backend.store import RepeaterStore  # noqa: E402

P1 = "c5861341a55adf62b385af734bf87c4a"
V4 = "cc61e4817cff913ad2dedd4780bbba5a"
NOW = 1_800_000_000.0


class FakeDB:
    """meshpoint's Database interface over sqlite3 (sync is fine here)."""

    def __init__(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row

    async def execute(self, sql, params=()):
        return self.c.execute(sql, params)

    async def fetch_one(self, sql, params=()):
        r = self.c.execute(sql, params).fetchone()
        return dict(r) if r else None

    async def fetch_all(self, sql, params=()):
        return [dict(r) for r in self.c.execute(sql, params).fetchall()]

    async def commit(self):
        self.c.commit()


def fake_repeater(env=True, reboots=7, down=False):
    """A ScotMesh repeater's /api replies; records every request."""
    calls = []
    api = ["bat", "up", "radio", "net", "sys", "health", "pos", "paths", "nbrs", "links", "all"]
    if env:
        api += ["env", "hist"]
    box = {"reboots": reboots}

    async def fetch(dest, path, data):
        calls.append((path, dict(data or {})))
        if down:
            return False, None, "No path to that node"
        ep = path[len("/api/"):] if path.startswith("/api/") else ""
        if path == "/api":
            return True, {"v": 1, "api": api}, ""
        if ep not in api:
            return False, None, "timed out"
        if ep == "all":
            return True, {"percent": 94, "voltage": 4.11, "charging": True, "uptime": 3600, "reset": "soft-reset",
                          "routes": 10, "neighbours": 4, "rx": 500, "tx": 300, "links": 1, "airtime": 2.5}, ""
        if ep == "radio":
            return True, {"freq": 869463000, "bw": 125000, "sf": 8, "cr": 5, "txp": 21, "airtime": 2.5,
                          "noise": -80, "rssi": -60, "snr": 9}, ""
        if ep == "health":
            return True, {"free_ram": 19000, "free_pool": 44000, "pool_fragmented": 11, "reboots": box["reboots"],
                          "reset": "soft-reset", "paths": 10, "neighbours": 4}, ""
        if ep == "env":
            return True, {"temp": 19.1, "humidity": 78.0, "pressure": 1019.8, "sensor": "SHT30", "mcu_temp": 24.0}, ""
        if ep == "sys":
            return True, {"fw": "1.86-scotmesh", "board": "P1", "name": "NL-AMS-PD2EMC P1"}, ""
        if ep == "pos":
            return True, {"lat": 52.37, "lon": 4.89, "height": 30}, ""
        if ep in ("nbrs", "paths", "links"):
            o, n = int(data["o"]), int(data["n"])
            total = 20 if ep == "paths" else 3
            rows = [[f"{ep}{i}", 1] for i in range(o, min(total, o + n))]
            return True, {"total": total, "o": o, "cols": ["dest", "hops"], "rows": rows,
                          **({"more": True} if o + n < total else {})}, ""
        if ep == "hist":
            o, n = int(data["o"]), int(data["n"])
            total = 30
            rows = [[h, 90, 4100, 1, 18.5, 70, 1015.0, 22, 3, 8, 0] for h in range(o, min(total, o + n))]
            return True, {"total": total, "o": o, "last_s": 600,
                          "cols": ["h", "pct", "mv", "chg", "t", "hum", "hpa", "mcu", "nbrs", "paths", "boot"],
                          "rows": rows, **({"more": True} if o + n < total else {})}, ""
        return False, None, "?"

    return fetch, calls, box


def run(coro):
    return asyncio.run(coro)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        state.init({"repeaters": [{"hash": P1, "name": ""}]})
        self.db = FakeDB()
        self.store = RepeaterStore(self.db)
        run(self.store.ensure_schema())

    def _mon(self, fetch):
        return m.RepeaterMonitor(self.store, fetch=fetch)

    def test_add_fetches_everything_and_backfills_history(self):
        fetch, calls, _ = fake_repeater()
        mon = self._mon(fetch)
        snap = run(mon.add(P1))
        paths = [p for p, _ in calls]
        for ep in ("/api", "/api/all", "/api/radio", "/api/health", "/api/env", "/api/sys", "/api/pos",
                   "/api/nbrs", "/api/paths", "/api/links", "/api/hist"):
            self.assertIn(ep, paths)
        self.assertTrue(snap["ok"], snap["error"])
        self.assertEqual(snap["radio"]["noise"], -80)
        self.assertEqual(snap["paths"]["total"], 20)
        self.assertEqual(len(snap["paths"]["rows"]), 20)        # paged 15 + 5
        self.assertEqual(state.repeaters()[0]["name"], "NL-AMS-PD2EMC P1")
        rows = self.db.c.execute("SELECT source, COUNT(*) FROM rr_samples GROUP BY source").fetchall()
        got = {r[0]: r[1] for r in rows}
        self.assertEqual(got.get("poll"), 1)
        self.assertEqual(got.get("hist"), 30)                   # 24 + 6, two pages

    def test_radio_is_asked_right_after_the_index(self):
        # Its airtime is a short window: asked later it mostly measures our
        # own replies to this poll (36.8 % seen on the P1 vs 2-4 % normally).
        fetch, calls, _ = fake_repeater()
        run(self._mon(fetch).add(P1))
        self.assertEqual([p for p, _ in calls][:2], ["/api", "/api/radio"])

    def test_board_without_sensors_never_asks_env_or_hist(self):
        fetch, calls, _ = fake_repeater(env=False)
        snap = run(self._mon(fetch).add(P1))
        paths = [p for p, _ in calls]
        self.assertNotIn("/api/env", paths)
        self.assertNotIn("/api/hist", paths)
        self.assertTrue(snap["ok"], snap["error"])

    def test_unreachable_stops_after_the_first_request(self):
        fetch, calls, _ = fake_repeater(down=True)
        snap = run(self._mon(fetch).poll(P1, set(m.STATS)))
        self.assertEqual(len(calls), 1)
        self.assertFalse(snap["ok"])
        self.assertIn("No path", snap["error"])

    def test_restart_is_flagged(self):
        fetch, _, box = fake_repeater(reboots=7)
        mon = self._mon(fetch)
        run(mon.poll(P1, set(m.STATS)))
        self.assertNotIn("restart", mon.latest[P1])
        box["reboots"] = 8
        run(mon.poll(P1, set(m.STATS)))
        self.assertEqual(mon.latest[P1]["restart"]["to"], 8)
        self.assertEqual(mon.latest[P1]["restart"]["reason"], "soft-reset")

    def test_due_tiers(self):
        mon = self._mon(fake_repeater()[0])
        mon.latest[P1] = {"at": {"stats": NOW - 100, "lists": NOW - 100, "info": NOW - 100}}
        self.assertEqual(mon._due(P1, NOW), set())
        self.assertEqual(mon._due(P1, NOW + 15 * 60), set(m.STATS))
        self.assertEqual(mon._due(P1, NOW + 60 * 60), set(m.STATS + m.LISTS))
        self.assertEqual(mon._due(P1, NOW + 86400), set(m.STATS + m.LISTS + m.INFO))

    def test_refresh_cooldown(self):
        mon = self._mon(fake_repeater()[0])
        self.assertTrue(mon.can_refresh(P1)[0])
        mon.mark_refresh(P1)
        ok, wait = mon.can_refresh(P1)
        self.assertFalse(ok)
        self.assertGreater(wait, 50)

    def test_latest_survives_a_restart(self):
        fetch, _, _ = fake_repeater()
        run(self._mon(fetch).poll(P1, set(m.STATS)))
        fresh = self._mon(fetch)
        fresh.latest = run(self.store.load_latest())
        self.assertEqual(fresh.latest[P1]["all"]["percent"], 94)

    def test_forget_removes_data(self):
        fetch, _, _ = fake_repeater()
        mon = self._mon(fetch)
        run(mon.add(P1))
        run(mon.forget(P1))
        self.assertEqual(self.db.c.execute("SELECT COUNT(*) FROM rr_samples").fetchone()[0], 0)
        self.assertNotIn(P1, mon.latest)


class PureTest(unittest.TestCase):
    def test_hist_timestamps(self):
        page = {"last_s": 600, "cols": ["h", "pct", "mv", "chg", "t", "hum", "hpa", "mcu", "nbrs", "paths", "boot"],
                "rows": [[0, 90, 4100, 1, 18.5, 70, 1015.0, 22, 3, 8, 0], [2, 80, 0, 0, None, None, None, None, 2, 5, 1]]}
        a, b = m.hist_samples([page], NOW)
        self.assertEqual(a["ts"], NOW - 600)
        self.assertEqual(b["ts"], NOW - 600 - 7200)
        self.assertEqual(a["voltage"], 4.1)
        self.assertIsNone(b["voltage"])                          # mv 0 = no reading

    def test_flatten(self):
        snap = {"all": {"percent": 94, "voltage": 4.11, "charging": False, "airtime": 1.0},
                "radio": {"noise": -80, "airtime": 2.5}, "health": {"pool_fragmented": 11},
                "env": {"temp": 19.1, "mcu_temp": 24.0}}
        f = m.flatten(snap, NOW)
        self.assertEqual((f["percent"], f["charging"], f["noise"], f["airtime"], f["frag"], f["mcu"]),
                         (94.0, 0.0, -80.0, 2.5, 11.0, 24.0))
        self.assertIsNone(f["humidity"])


class StateTest(unittest.TestCase):
    def test_add_remove_and_validation(self):
        state.init({})
        self.assertFalse(state.add_repeater("nothex"))
        self.assertTrue(state.add_repeater(P1.upper()))
        self.assertFalse(state.add_repeater(P1))                 # duplicate
        self.assertTrue(state.add_repeater(V4, "bench"))
        self.assertEqual([r["hash"] for r in state.repeaters()], [P1, V4])
        self.assertTrue(state.remove_repeater(P1))
        self.assertFalse(state.remove_repeater(P1))

    def test_defaults_and_clamps(self):
        state.init({"interval_min": 1, "lists_interval_min": 99999, "repeaters": [{"hash": "bad"}]})
        self.assertEqual(state.interval_s(), 5 * 60)
        self.assertEqual(state.lists_interval_s(), 1440 * 60)
        self.assertEqual(state.repeaters(), [])
        state.init({})
        self.assertEqual(state.interval_s(), 15 * 60)            # the default the user asked for
        self.assertTrue(state.polling_enabled())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
