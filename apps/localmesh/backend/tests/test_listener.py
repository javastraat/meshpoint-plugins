"""Tests for LocalMeshSerialSource without a board: frames go straight into _handle(), and a fake port records what
the source writes. Covers the one-command-at-a-time queue, member-list paging, message dedup and channel events."""

from __future__ import annotations

import struct
import unittest

from plugins.apps.localmesh.backend import protocol as p
from plugins.apps.localmesh.backend import listener as l


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

    def test_messages_are_kept_once(self) -> None:
        self.src._handle(message(0, 4, 12, "hallo", t=1762862400))
        self.src._handle(message(0, 4, 12, "hallo", t=1762862400))   # fetched again after a reconnect
        self.src._handle(message(1, 5, 3, "geen klok", t=4000))
        msgs = self.src.page_data()["messages"]
        self.assertEqual([m["text"] for m in msgs], ["hallo", "geen klok"])
        self.assertEqual(msgs[0]["time"], 1762862400)
        self.assertIsNone(msgs[1]["time"])                            # board clock not set

    def test_channels_and_channel_events(self) -> None:
        self.src._handle(reply(l.SEQ_CHANNELS, bytes([1, 0, 0]) + s("Algemeen")))
        self.src._handle(bytes([p.CHANNEL_EVENT, 0, 2, 0]) + s("Noodnet"))
        self.assertEqual(self.src.channels, {0: "Algemeen", 2: "Noodnet"})
        self.src._handle(bytes([p.CHANNEL_EVENT, 0, 0, 1]) + s("Algemeen"))
        self.assertEqual(self.src.snapshot()["channels"], {"2": "Noodnet"})

    def test_rx_log_feeds_activity_and_heard(self) -> None:
        rx = bytes([p.RX_LOG, 0]) + struct.pack("<IIBBHhbB", 4, 77, 0, 1, 100, -90, 8, 0) + bytes([0, 0])
        self.src._handle(rx); self.src._handle(rx)
        d = self.src.page_data()
        self.assertEqual(len(d["activity"]), 2)
        self.assertEqual(d["activity"][0]["sender"], "4")
        self.assertEqual((d["heard"][4]["count"], d["heard"][4]["rssi"]), (2, -90))

    def test_neighbors_reply(self) -> None:
        self.src._handle(reply(l.SEQ_NEIGHBORS, bytes([1]) + struct.pack("<HbhII", 100, 8, -60, 3, 50)))
        self.assertEqual(self.src.snapshot()["neighbors"], [{"node": 100, "snr": 2.0, "rssi": -60, "ago_s": 3, "count": 50}])


if __name__ == "__main__":
    unittest.main()
