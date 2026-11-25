"""restore.py: the lists after a restart, from tables with Meshpoint's own columns in an in-memory SQLite database."""

from __future__ import annotations

import json
import sqlite3
import struct
import unittest
from unittest import mock

from plugins.apps.localmesh.backend import decode, hooks
from plugins.apps.localmesh.backend import listener as l
from plugins.apps.localmesh.backend import protocol as p
from plugins.apps.localmesh.backend import restore, views

T = "2026-11-25T10:%02d:00+00:00"
REP = p.REPEATER_MEMBER_BASE | 103
STATUS = {"battery_mv": 4100, "noise_floor": -110, "uptime_h": 5, "airtime_pct": 1, "queue": 0, "rx_ok": 9, "rx_rejected": 1,
          "version": "0.10.7", "neighbor_count": 1, "neighbors": [{"node": 8, "snr": 3.0, "name": "x"}], "reasons": {},
          "time": 1000.0, "counter": 42, "restarts": 3, "node": 103}


class DB:
    def __init__(self, tables: bool = True):
        self.c = sqlite3.connect(":memory:"); self.c.row_factory = sqlite3.Row
        if tables:
            self.c.execute("CREATE TABLE packets (id INTEGER PRIMARY KEY, source_id, protocol, packet_type, decoded_payload, "
                           "rssi, snr, capture_source, timestamp)")
            self.c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, direction, text, node_id, node_name, protocol, "
                           "channel, timestamp, status, packet_id, rssi, snr)")

    async def fetch_all(self, sql, params=()): return [dict(r) for r in self.c.execute(sql, params).fetchall()]

    def packet(self, member, counter, kind, minute, source="localmesh_base", **kw):
        d = {"type": kind, "member": member, "sender": str(member), "counter": counter, "hops": 2, "via": 100,
             "decision": "not_member", "routed": False, "path": [{"node": 100, "snr": 2.0}], **kw}
        self.c.execute("INSERT INTO packets VALUES (NULL,?,?,?,?,?,?,?,?)", (p.member_label(member), "localmesh",
                       f"localmesh_{kind}", json.dumps(d), -90.0, 1.5, source, T % minute))

    def message(self, direction, text, node_id, packet_id, minute, channel=0):
        self.c.execute("INSERT INTO messages VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)", (direction, text, node_id, "N", "localmesh",
                       channel, T % minute, "sent" if direction == "sent" else "delivered", packet_id, -80.0, 2.0))


class TestRestore(unittest.IsolatedAsyncioTestCase):
    def source(self, db, messages=True):
        src = l.LocalMeshSerialSource(serial_port="/dev/null", label="base")
        src._started = restore.unix(T % 30)
        src.restore = lambda: src._restore(db, messages)
        return src

    async def test_activity_heard_and_announces(self):
        db = DB()
        db.packet(4, 1, "channel", 1); db.packet(4, 2, "measure", 2); db.packet(9, 1, "advert", 3, source="localmesh_other")
        db.packet(REP, 7, "announce", 4, announce={"region": 3, "version": "0.10.7"}); db.packet(REP, 8, "announce", 5)
        db.c.execute("INSERT INTO packets VALUES (NULL,'x','localmesh','localmesh_x','{broken',1,1,'localmesh_base',?)", (T % 6,))
        db.c.execute("INSERT INTO packets VALUES (NULL,'4','meshtastic','text','{}',1,1,'localmesh_base',?)", (T % 7,))
        src = self.source(db); await src.restore()
        d = src.page_data()
        self.assertEqual([(a["member"], a["type"]) for a in d["activity"]],
                         [(4, "channel"), (4, "cert_request"), (REP, "announce"), (REP, "announce")])
        self.assertEqual(d["activity"][0]["path"], [{"node": 100, "snr": 2.0}])
        self.assertEqual((d["heard"][4]["count"], d["heard"][4]["type"], d["heard"][4]["rssi"]), (2, "cert_request", -90))
        self.assertEqual((d["announces"][103]["version"], d["announces"][103]["region"]), ("0.10.7", 3))
        self.assertEqual(src.last_heard, restore.unix(T % 5))
        self.assertEqual(views.members([("base", d)])[0]["member"], 4)
        self.assertEqual(views.repeaters([("base", d)])[0]["version"], "0.10.7")
        self.assertEqual(len(views.activity([("base", d)])), 4)

    async def test_activity_keeps_the_newest_300(self):
        db = DB()
        for n in range(320): db.packet(4, n, "channel", 1)
        src = self.source(db); await src.restore()
        self.assertEqual((len(src.activity), src.activity[0]["counter"], src.activity[-1]["counter"]), (300, 20, 319))
        self.assertEqual(src.heard[4]["count"], 320)

    async def test_messages_received_and_sent(self):
        db = DB()
        db.message("received", "hoi", "broadcast:localmesh:0", "localmesh:4:0:11", 1)
        db.message("received", "old", "broadcast:localmesh:0", "", 2)                       # no number: skipped
        db.message("sent", "dag", "localmesh:4", "", 3); db.message("sent", "all", "broadcast:localmesh:2", "", 4)
        src = self.source(db); await src.restore()
        out = views.messages([("base", src.page_data())])
        self.assertEqual([(m["text"], m.get("outgoing", False), m["channel"]) for m in out],
                         [("all", True, 2), ("dag", True, p.CHANNEL_DIRECT), ("hoi", False, 0)])
        other = self.source(db, messages=False); await other.restore()
        self.assertEqual(len(other.messages), 0)

    async def test_the_boards_copy_replaces_a_restored_message_without_being_new(self):
        db = DB(); db.message("received", "hoi", "broadcast:localmesh:0", "localmesh:4:0:11", 1)
        src = self.source(db); await src.restore()
        called = []
        src._loop = type("Loop", (), {"call_soon_threadsafe": lambda self, fn, *a: called.append(fn)})()
        board = p.Message(0, 0, 4, 11, 1_800_000_000, 2, 100, -70, 3.0, "Albert: hoi", [100])
        src._note_message(board, 5.0); src._note_message(board, 6.0)
        self.assertEqual(len(src.messages), 1)
        m = src.messages[0]
        self.assertEqual((m["time"], m["path"], m["text"], m["received"]), (1_800_000_000, [100], "Albert: hoi", restore.unix(T % 1)))
        self.assertNotIn(hooks.emit_message, called)                                       # the bot must not answer it again

    async def test_statuses_of_the_repeaters_plugin(self):
        db = DB(); db.packet(REP, 42, "status", 1); db.packet(REP, 43, "status", 2)
        db.c.execute("CREATE TABLE lmr_latest (node INTEGER, data TEXT)")
        db.c.execute("INSERT INTO lmr_latest VALUES (103, ?), (104, 'broken')", (json.dumps(STATUS),))
        src = self.source(db)
        with mock.patch.object(l.state, "status_keys", return_value=[b"k" * 32]): await src.restore()
        self.assertEqual(src.statuses[103]["battery_mv"], 4100)
        self.assertNotIn("restarts", src.statuses[103])
        self.assertEqual([("repeater_status" in a) for a in src.activity], [True, False])
        nokey = self.source(db)
        with mock.patch.object(l.state, "status_keys", return_value=[]): await nokey.restore()
        self.assertEqual(nokey.statuses, {})

    async def test_behind_what_came_in_live(self):
        db = DB()
        db.packet(4, 1, "channel", 1); db.packet(4, 2, "channel", 31)                        # the second is after start(): live
        db.message("received", "old", "broadcast:localmesh:0", "localmesh:4:0:1", 1)
        src = self.source(db)
        live = restore.unix(T % 31)
        src._note_rx(p.RxLog(4, 2, "channel", 3, 100, -50, 9.0, "deliver"), live)
        src._note_message(p.Message(0, 0, 4, 2, 1_800_000_000, 2, 100, -70, 3.0, "new"), live)
        src.last_heard = live
        await src.restore()
        self.assertEqual([a["counter"] for a in src.activity], [1, 2])
        self.assertEqual((src.heard[4]["count"], src.heard[4]["rssi"], src.last_heard), (2, -50, live))
        self.assertEqual([m["text"] for m in src.messages], ["old", "new"])

    async def test_wire_starts_it_after_the_source(self):
        """The core starts the sources before it calls wire(): the restore must not depend on start() coming later."""
        db = DB(); db.packet(4, 1, "channel", 1)
        src = l.LocalMeshSerialSource(serial_port="/dev/null", label="base")
        src._started = restore.unix(T % 30)
        src.restore_from(db, messages=True)
        await src._restore_task
        self.assertEqual(len(src.activity), 1)

    async def test_no_tables_or_no_database(self):
        src = self.source(DB(tables=False)); await src.restore()
        self.assertEqual(len(src.activity), 0)
        src = self.source(None); await src.restore()

    async def test_what_is_stored_comes_back(self):
        """A packet through decode.adapt_rx_log and back gives the same Activity entry as the live one."""
        rx = struct.pack("<IIBBHhbB", REP, 7, 13, 0, 103, -48, 45, 3) + bytes([0, 1]) + struct.pack("<Hb", 100, 8)
        src = l.LocalMeshSerialSource(serial_port="/dev/null", label="base")
        src._note_rx(p.parse_rx_log(rx), 1.0)
        live = src.activity[0]
        pkt = decode.adapt_rx_log(rx, extra={"announce": {"region": 1, "version": "0.10.7"}})
        back = restore.packet_entry({"decoded_payload": json.dumps(pkt.decoded_payload), "rssi": -48.0, "snr": 11.25, "timestamp": T % 1})
        self.assertEqual(back.pop("announce"), {"region": 1, "version": "0.10.7"})
        self.assertEqual({**back, "time": 0}, {**live, "time": 0})


if __name__ == "__main__":
    unittest.main()
