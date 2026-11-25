"""Tests for LocalMeshSerialSource without a board: frames go straight into _handle(), and a fake port records what
the source writes. Covers the one-command-at-a-time queue, member-list paging, message dedup and channel events."""

from __future__ import annotations

import hashlib
import hmac
import struct
import unittest
from unittest import mock

from plugins.apps.localmesh.backend import protocol as p
from plugins.apps.localmesh.backend import listener as l
from plugins.apps.localmesh.backend import hooks


def s(text: str) -> bytes:
    b = text.encode(); return bytes([len(b)]) + b


class FakePort:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        frames, _ = p.SlipDecoder().feed(data)
        self.frames += frames


def reply(seq: int, payload: bytes = b"", err: int = 0) -> bytes:
    return bytes([p.REPLY, seq, err]) + payload


def member(mid: int, flags: int = 0) -> bytes:
    return struct.pack("<I", mid) + bytes(32) + bytes([flags]) + struct.pack("<I", 1)


def message(index: int, member_id: int, counter: int, text: str, channel: int = 0, t: int = 0) -> bytes:
    return bytes([p.MESSAGE, 0]) + struct.pack("<HBIIIBHhb", index, channel, member_id, counter, t, 1, 100, -70, 8) + s(text)


class TestListener(unittest.TestCase):
    def setUp(self) -> None:
        self.src = l.LocalMeshSerialSource(serial_port="/dev/null", label="base")
        self.port = FakePort()
        self.src._serial = self.port
        self.src._next_neighbors = self.src._next_members = 1e12   # no periodic requests unless a test asks

    def sent(self) -> list[int]:
        return [f[0] for f in self.port.frames]

    def test_one_command_at_a_time(self) -> None:
        self.src._queue_cmd(l.SEQ_STATUS, p.command(p.STATUS, l.SEQ_STATUS))
        self.src._queue_cmd(l.SEQ_CHANNELS, p.command(p.CHANNELS_GET, l.SEQ_CHANNELS))
        self.src._tick(0.0); self.src._tick(1.0)
        self.assertEqual(self.sent(), [p.STATUS])                    # waits for the reply
        self.src._handle(reply(l.SEQ_STATUS, bytes(26)))
        self.src._tick(2.0)
        self.assertEqual(self.sent(), [p.STATUS, p.CHANNELS_GET])

    def test_is_running_only_with_the_port_open(self) -> None:
        """Meshpoint's status LED blinks "a source is down" for a source that is not running (the base class says never)."""
        self.assertFalse(self.src.is_running)                       # not started
        self.src._running = True
        self.assertFalse(self.src.is_running)                       # started, port not open
        self.src._connected = True
        self.assertTrue(self.src.is_running)
        self.src._close()                                           # cable out
        self.assertFalse(self.src.is_running)

    def test_no_reply_times_out(self) -> None:
        self.src._queue_cmd(1, p.command(p.STATUS, 1)); self.src._queue_cmd(2, p.command(p.INFO, 2))
        self.src._tick(0.0); self.src._tick(l.REPLY_TIMEOUT_S + 0.5)
        self.assertEqual(self.sent(), [p.STATUS, p.INFO])

    def test_error_reply_also_frees_the_queue(self) -> None:
        self.src._queue_cmd(l.SEQ_CHANNELS, p.command(p.CHANNELS_GET, l.SEQ_CHANNELS))
        self.src._queue_cmd(l.SEQ_STATUS, p.command(p.STATUS, l.SEQ_STATUS))
        self.src._tick(0.0)
        self.src._handle(reply(l.SEQ_CHANNELS, err=1))
        self.src._tick(0.5)
        self.assertEqual(self.sent(), [p.CHANNELS_GET, p.STATUS])
        self.assertEqual(self.src.channels, {})

    def test_hello_again_every_minute(self) -> None:
        """The board stops pushing events (messages, RX_LOG) over USB once it thinks the host went away (firmware 0.10.4+,
        a USB pause of a few ms is enough) and only starts again on HELLO: say it again with the neighbours request."""
        self.src._next_neighbors = 0.0
        self.src._tick(0.0)
        self.assertEqual(self.sent(), [p.HELLO])
        self.src._handle(reply(l.SEQ_HELLO, b""))                   # an empty INFO is ignored, the queue moves on
        self.src._tick(0.5)
        self.assertEqual(self.sent(), [p.HELLO, p.NEIGHBORS_GET])
        self.src._handle(reply(l.SEQ_NEIGHBORS, bytes([0])))
        self.src._tick(1.0)
        self.assertEqual(self.port.frames[-1], bytes([p.RAW, l.SEQ_RAWLOG]) + s("RAWLOG SHORT"))
        self.src._handle(reply(l.SEQ_RAWLOG))
        self.src._tick(30.0)
        self.assertEqual(self.sent(), [p.HELLO, p.NEIGHBORS_GET, p.RAW])   # nothing new within the minute
        self.src._tick(l.NEIGHBORS_EVERY_S + 0.1)
        self.assertEqual(self.sent()[-1], p.HELLO)

    def test_member_list_is_fetched_page_by_page(self) -> None:
        self.src._next_members = 0.0
        self.src._tick(0.0)
        self.assertEqual(self.port.frames[-1], bytes([p.MEMBERS_GET, l.SEQ_MEMBERS, 0, 0]))
        self.src._handle(reply(l.SEQ_MEMBERS, struct.pack("<HB", 6, 5) + b"".join(member(i) for i in range(1, 6))))
        self.assertEqual(self.src.members, {})                       # not replaced until complete
        self.src._tick(1.0)
        self.assertEqual(self.port.frames[-1], bytes([p.MEMBERS_GET, l.SEQ_MEMBERS, 5, 0]))
        self.src._handle(reply(l.SEQ_MEMBERS, struct.pack("<HB", 6, 1) + member(6, p.MEMBER_BLOCKED)))
        self.assertEqual(sorted(self.src.members), [1, 2, 3, 4, 5, 6])
        self.assertTrue(self.src.members[6]["blocked"])

    def test_names_and_repeaters_are_fetched_page_by_page(self) -> None:
        self.src._next_members = 0.0
        self.src._tick(0.0)
        self.src._handle(reply(l.SEQ_MEMBERS, struct.pack("<HB", 0, 0)))   # the member list is complete: names next
        self.src._tick(1.0)
        self.assertEqual(self.port.frames[-1], bytes([p.NAMES_GET, l.SEQ_NAMES, 0, 0]))
        self.src._handle(reply(l.SEQ_NAMES, struct.pack("<HB", 2, 1) + struct.pack("<I", 4) + s("Albert")))
        self.assertEqual(self.src.names, {})                          # not replaced until complete
        self.src._tick(2.0)
        self.assertEqual(self.port.frames[-1], bytes([p.NAMES_GET, l.SEQ_NAMES, 1, 0]))
        self.src._handle(reply(l.SEQ_NAMES, struct.pack("<HB", 2, 1) + struct.pack("<I", 5) + s("Bob")))
        self.assertEqual(self.src.names, {4: "Albert", 5: "Bob"})
        self.src._tick(3.0)
        self.assertEqual(self.port.frames[-1], bytes([p.REPEATERS_GET, l.SEQ_REPEATERS, 0]))
        self.src._handle(reply(l.SEQ_REPEATERS, bytes([1, 1]) + struct.pack("<HBB", 11, 3, 0) + s("NL-AMS-1076")))
        self.assertEqual(self.src.repeaters[11]["name"], "NL-AMS-1076")
        self.src._handle(bytes([p.REPEATER_EVENT, 0]) + struct.pack("<HBB", 11, 3, 1) + s("NL-AMS-1076"))
        self.assertTrue(self.src.repeaters[11]["revoked"])

    def test_repeaters_still_fetched_when_the_board_has_no_names_command(self) -> None:
        self.src._queue_names()
        self.src._tick(0.0)
        self.src._handle(reply(l.SEQ_NAMES, err=p.ERR_UNKNOWN))
        self.src._tick(1.0)
        self.assertEqual(self.port.frames[-1], bytes([p.REPEATERS_GET, l.SEQ_REPEATERS, 0]))

    def test_time_set_frame(self) -> None:
        frames, _ = p.SlipDecoder().feed(p.time_set(l.SEQ_TIME, 1_762_862_400))
        self.assertEqual(frames, [bytes([p.TIME_SET, l.SEQ_TIME]) + struct.pack("<I", 1_762_862_400)])

    def test_messages_are_kept_once(self) -> None:
        self.src._handle(message(0, 4, 12, "hallo", t=1762862400))
        self.src._handle(message(0, 4, 12, "hallo", t=1762862400))   # fetched again after a reconnect
        self.src._handle(message(1, 5, 3, "geen klok", t=4000))
        msgs = self.src.page_data()["messages"]
        self.assertEqual([m["text"] for m in msgs], ["hallo", "geen klok"])
        self.assertEqual(msgs[0]["time"], 1762862400)
        self.assertIsNone(msgs[1]["time"])                            # board clock not set

    def test_mirrored_messages_carry_a_stable_packet_id(self) -> None:
        """The core store only dedupes on packet_id, and the board's buffer is fetched again after a restart."""
        class Loop:
            def __init__(self) -> None: self.calls = []
            def call_soon_threadsafe(self, fn, *args):   # only the core-store mirror (hooks.emit_message is tested apart)
                if fn is not hooks.emit_message: self.calls.append(args)
        loop = Loop()
        self.src._loop, self.src.message_repo = loop, object()
        self.src._handle(message(0, 4, 12, "kanaal", channel=0))
        self.src._handle(message(1, 5, 3, "dm", channel=p.CHANNEL_DIRECT))
        self.assertEqual([c[-1] for c in loop.calls], ["localmesh:4:0:12", f"localmesh:5:{p.CHANNEL_DIRECT}:3"])

    def test_mirrored_sender_name_comes_from_the_text_prefix(self) -> None:
        class Loop:
            def __init__(self) -> None: self.calls = []
            def call_soon_threadsafe(self, fn, *args):   # only the core-store mirror (hooks.emit_message is tested apart)
                if fn is not hooks.emit_message: self.calls.append(args)
        loop = Loop()
        self.src._loop, self.src.message_repo = loop, object()
        self.src._handle(message(0, 4, 1, "Einstein Tag: nou zeg"))
        self.src._handle(message(1, 4, 2, "dm without a name", channel=p.CHANNEL_DIRECT))
        self.src._handle(message(2, 7, 3, "no name here"))
        self.src.adverts[8] = {"name": "Bob"}
        self.src._handle(message(3, 8, 4, "Bijnaam: hoi"))
        got = [(c[0], c[2]) for c in loop.calls]   # (text, node_name)
        self.assertEqual(got, [("nou zeg", "Einstein Tag"), ("dm without a name", "Einstein Tag"),
                               ("no name here", "7"), ("Bijnaam: hoi", "Bob")])

    def test_new_messages_go_to_the_hooks_once(self) -> None:
        """20-11: plugins that build on this one (localmesh-bot) get every NEW message, with its path, on the loop."""
        class Loop:
            def __init__(self) -> None: self.calls = []
            def call_soon_threadsafe(self, fn, *args): self.calls.append((fn, args))
        loop = Loop()
        self.src._loop = loop
        got = []
        handler = lambda source, m: got.append((source, m["member"], m["text"], m["path"]))
        hooks.registry().message_handlers.append(handler)
        try:
            raw = message(0, 4, 12, ".ping") + struct.pack("<BHH", 2, 58159, 42978)
            self.src._handle(raw); self.src._handle(raw)   # the same message again (buffer after a reconnect)
            for fn, args in loop.calls:
                if fn is hooks.emit_message: fn(*args)
        finally:
            hooks.registry().message_handlers.remove(handler)
        self.assertEqual(got, [(self.src, 4, ".ping", [58159, 42978])])

    def test_channels_and_channel_events(self) -> None:
        self.src._handle(reply(l.SEQ_CHANNELS, bytes([1, 0, 0]) + s("Algemeen")))
        self.src._handle(bytes([p.CHANNEL_EVENT, 0, 2, 0]) + s("Noodnet"))
        self.assertEqual(self.src.channels, {0: "Algemeen", 2: "Noodnet"})
        self.src._handle(bytes([p.CHANNEL_EVENT, 0, 0, 1]) + s("Algemeen"))
        self.assertEqual(self.src.snapshot()["channels"], {"2": "Noodnet"})

    ANNOUNCE = "4d810003ffa7e201aa8d94a7e2" + "0103000a09" + "00" * 8

    def rx_announce(self, counter: int = 0x01AA8D94, decision: str = "not_member") -> bytes:
        return bytes([p.RX_LOG, 0]) + struct.pack("<IIBBHhbB", 0xFFA7E2, counter, 13, 0, 0xA7E2, -48, 45,
                                                  p.DECISIONS.index(decision)) + bytes([0, 0])

    def test_announce_from_the_raw_line_before_its_rx_log(self) -> None:
        self.src._handle(bytes([p.TEXT, 0]) + s("RAW " + self.ANNOUNCE))
        self.src._handle(bytes([p.TEXT, 0]) + s("RX member=16754658 ..."))      # other log lines in between
        self.src._handle(self.rx_announce())
        d = self.src.page_data()
        self.assertEqual(d["activity"][-1]["announce"], {"region": 3, "version": "0.10.9"})
        self.assertEqual((d["announces"][0xA7E2]["region"], d["announces"][0xA7E2]["version"]), (3, "0.10.9"))

    def test_announce_only_with_its_own_rx_log(self) -> None:
        self.src._handle(bytes([p.TEXT, 0]) + s("RAW " + self.ANNOUNCE))
        self.src._handle(self.rx_announce(counter=5))
        self.src._handle(self.rx_announce())                                     # used up by the one above
        self.src._handle(bytes([p.TEXT, 0]) + s("RAW " + self.ANNOUNCE[:41]))    # cut off
        self.src._handle(self.rx_announce())
        self.assertFalse(any("announce" in a for a in self.src.page_data()["activity"]))

    # firmware test/test_core/vectors.h V_ST_FRAME: a sealed status of repeater 103, network key 5a..5a
    STATUS = "4c8d0401ff00670000002a00670170663336b39f4c0f37c15888f9e6fd03bef6abb72576a4"
    STATUS_KEY = hmac.new(b"\x5a" * 32, b"status", hashlib.sha256).digest()

    def rx_status(self) -> bytes:
        return bytes([p.RX_LOG, 0]) + struct.pack("<IIBBHhbB", 0xFF0067, 42, 12, 4, 103, -48, 45, 3) + bytes([0, 0])

    def hear_status(self, *lines: str) -> dict:
        for line in lines:
            self.src._handle(bytes([p.TEXT, 0]) + s(line))
        self.src._handle(self.rx_status())
        return self.src.page_data()

    def test_status_is_opened_with_the_key(self) -> None:
        with mock.patch.object(l.state, "status_keys", return_value=[b"x" * 32, self.STATUS_KEY]):
            d = self.hear_status("RAW " + self.STATUS[:30], self.STATUS[30:], "RX member=16711783")   # in two TEXT events
        st = d["activity"][-1]["repeater_status"]
        self.assertEqual((st["battery_mv"], st["noise_floor"], st["version"]), (3987, -104, "0.5.0"))
        self.assertEqual(d["statuses"][103]["uptime_h"], 27)

    def test_opened_status_goes_to_the_hooks(self) -> None:
        got = []
        class Loop:
            def call_soon_threadsafe(self, fn, *args):
                if fn is hooks.emit_repeater_status: fn(*args)
        handler = lambda source, st: got.append((source, st))
        hooks.registry().status_handlers.append(handler)
        self.src._loop = Loop()
        self.src.repeaters = {103: {"node": 103, "region": 1, "flags": 0, "name": "NL-AMS-103"}}
        try:
            with mock.patch.object(l.state, "status_keys", return_value=[self.STATUS_KEY]):
                self.hear_status("RAW " + self.STATUS)
            with mock.patch.object(l.state, "status_keys", return_value=[]):
                self.hear_status("RAW " + self.STATUS)
        finally:
            hooks.registry().status_handlers.remove(handler)
        self.assertEqual(len(got), 1)
        st = got[0][1]
        self.assertEqual((st["node"], st["counter"], st["name"], st["battery_mv"], st["rssi"]), (103, 42, "NL-AMS-103", 3987, -48))

    def test_status_stays_closed_without_the_right_key(self) -> None:
        for keys in ([], [b"x" * 32]):
            with mock.patch.object(l.state, "status_keys", return_value=keys):
                d = self.hear_status("RAW " + self.STATUS)
            self.assertNotIn("repeater_status", d["activity"][-1])
        with mock.patch.object(l.state, "status_keys", return_value=[self.STATUS_KEY]):
            d = self.hear_status("RAW " + self.STATUS[:30], "RX member=1", self.STATUS[30:])   # not one after the other
            self.assertNotIn("repeater_status", d["activity"][-1])
        self.assertEqual(d["statuses"], {})

    def test_announce_of_another_net_is_not_kept(self) -> None:
        self.src._handle(bytes([p.TEXT, 0]) + s("RAW " + self.ANNOUNCE))
        self.src._handle(self.rx_announce(decision="wrong_net"))
        self.assertEqual(self.src.page_data()["announces"], {})

    def test_rx_log_feeds_activity_and_heard(self) -> None:
        rx = bytes([p.RX_LOG, 0]) + struct.pack("<IIBBHhbB", 4, 77, 0, 1, 100, -90, 8, 0) + bytes([0, 0])
        self.src._handle(rx); self.src._handle(rx)
        d = self.src.page_data()
        self.assertEqual(len(d["activity"]), 2)
        self.assertEqual(d["activity"][0]["sender"], "4")
        self.assertEqual((d["heard"][4]["count"], d["heard"][4]["rssi"]), (2, -90))

    def test_neighbors_reply(self) -> None:
        self.src._handle(reply(l.SEQ_NEIGHBORS, bytes([1]) + struct.pack("<HbhII", 100, 8, -60, 3, 50)))
        [n] = self.src.snapshot()["neighbors"]
        self.assertGreater(n.pop("at"), 0)                                       # when the list came in
        self.assertEqual([n], [{"node": 100, "snr": 2.0, "rssi": -60, "ago_s": 3, "count": 50}])


    def _info(self, name: bytes = b"") -> bytes:
        return (bytes([1]) + s("0.10.3") + s("Heltec V4") + bytes([1]) + struct.pack("<HHI", 100, 7, 4) + bytes(32)
                + bytes([1]) + struct.pack("<f", 869.556) + bytes([7]) + struct.pack("<f", 62.5) + bytes([5])
                + struct.pack("<b", 21) + struct.pack("<I", 123456) + name)

    def test_name_from_board(self) -> None:
        """Firmware 13-11 tells its name in INFO: it replaces the copy from the config (lost on an uninstall)."""
        src = l.LocalMeshSerialSource(serial_port="/dev/null", label="base", name="old copy")
        src._serial = self.port
        src._handle(reply(l.SEQ_HELLO, self._info(s("Meshpoint attic"))))
        snap = src.snapshot()
        self.assertEqual((snap["net_name"], snap["name_from_board"]), ("Meshpoint attic", True))

    def test_name_kept_with_older_firmware(self) -> None:
        """Older firmware sends no name: the copy this Meshpoint last set stays."""
        src = l.LocalMeshSerialSource(serial_port="/dev/null", label="base", name="old copy")
        src._serial = self.port
        src._handle(reply(l.SEQ_HELLO, self._info()))
        snap = src.snapshot()
        self.assertEqual((snap["net_name"], snap["name_from_board"]), ("old copy", False))

if __name__ == "__main__":
    unittest.main()
