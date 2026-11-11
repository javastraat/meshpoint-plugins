"""Tests for adapt_rx_log(): one RX_LOG frame to a Meshpoint Packet in the packet log."""

from __future__ import annotations

import struct
import unittest

from plugins.apps.localmesh.backend.decode import adapt_rx_log
from src.models.packet import OpenPacketType, OpenProtocol


class TestAdaptRxLog(unittest.TestCase):
    def test_channel_packet(self) -> None:
        raw = struct.pack("<IIBBHhbB", 4, 77, 0, 1, 100, -95, 8, 1) + bytes([0, 1]) + struct.pack("<Hb", 100, 8)
        pkt = adapt_rx_log(raw)
        self.assertIsNotNone(pkt)
        self.assertEqual(pkt.protocol, OpenProtocol("localmesh"))
        self.assertEqual(pkt.packet_type, OpenPacketType("localmesh_channel"))
        self.assertEqual((pkt.source_id, pkt.destination_id), ("4", "broadcast"))
        self.assertFalse(pkt.decrypted)
        d = pkt.decoded_payload
        self.assertEqual((d["sender"], d["counter"], d["hops"], d["decision"]), ("4", 77, 1, "deliver"))
        self.assertEqual(d["path"], [{"node": 100, "snr": 2.0}])

    def test_repeater_status(self) -> None:
        pkt = adapt_rx_log(struct.pack("<IIBBHhbB", 0xFF0000 | 12, 5, 12, 0, 0, -60, 0, 0))
        self.assertEqual(pkt.source_id, "repeater 12")
        self.assertEqual(pkt.packet_type, OpenPacketType("localmesh_status"))

    def test_garbage_is_none(self) -> None:
        self.assertIsNone(adapt_rx_log(b"\x01\x02"))


if __name__ == "__main__":
    unittest.main()
