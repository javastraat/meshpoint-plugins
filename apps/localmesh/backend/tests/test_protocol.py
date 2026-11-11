"""Tests for the LocalMesh companion protocol: SLIP framing and the frames the plugin reads.

The frames are built here field by field in the order the firmware writes them (src/helpers/Node.cpp fill_info,
fill_status, log_rx; src/helpers/CompanionNode.h MESSAGE and ADVERT), so a layout change on either side shows up.
Pure stdlib.
"""

from __future__ import annotations

import struct
import unittest

from plugins.apps.localmesh.backend import protocol as p


def s(text: str) -> bytes:
    b = text.encode(); return bytes([len(b)]) + b


class TestSlip(unittest.TestCase):
    def test_round_trip_with_escapes(self) -> None:
        payload = bytes([0x82, 0, 0xC0, 0xDB, 1, 2])
        frames, lines = p.SlipDecoder().feed(p.slip_encode(payload))
        self.assertEqual(frames, [payload]); self.assertEqual(lines, [])

    def test_split_across_reads_and_text_lines_between(self) -> None:
        dec = p.SlipDecoder(); data = b"RX m4 #12\r\n" + p.slip_encode(b"\x83\x00abc")
        got = [dec.feed(data[i:i + 1]) for i in range(len(data))]
        self.assertEqual([f for fs, _ in got for f in fs], [b"\x83\x00abc"])
        self.assertEqual([l for _, ls in got for l in ls], ["RX m4 #12"])

    def test_empty_frame_is_ignored(self) -> None:
        frames, _ = p.SlipDecoder().feed(b"\xC0\xC0" + p.slip_encode(b"\x80\x01\x00"))
        self.assertEqual(frames, [b"\x80\x01\x00"])

    def test_too_long_frame_is_dropped(self) -> None:
        dec = p.SlipDecoder()
        frames, _ = dec.feed(p.slip_encode(bytes(p.MAX_FRAME + 1)) + p.slip_encode(b"\x85\x00"))
        self.assertEqual(frames, [b"\x85\x00"])

    def test_hello(self) -> None:
        frames, _ = p.SlipDecoder().feed(p.hello(1, "mp"))
        self.assertEqual(frames, [bytes([p.HELLO, 1, p.PROTOCOL_VERSION, 2]) + b"mp"])


class TestParsers(unittest.TestCase):
    def test_info(self) -> None:
        pub = bytes(range(32))
        raw = (bytes([1]) + s("0.4.2") + s("Heltec V4") + bytes([1]) + struct.pack("<HHI", 100, 7, 4) + pub
               + bytes([1]) + struct.pack("<f", 869.556) + bytes([7]) + struct.pack("<f", 62.5) + bytes([5])
               + struct.pack("<b", 21) + struct.pack("<I", 123456))   # free heap: not read
        i = p.parse_info(raw)
        self.assertEqual((i.firmware, i.board, i.role, i.node, i.net_id, i.member), ("0.4.2", "Heltec V4", "companion", 100, 7, 4))
        self.assertEqual(i.pub, pub.hex()); self.assertTrue(i.serverpub_set)
        self.assertEqual((i.freq_mhz, i.sf, i.bw_khz, i.cr, i.power_dbm), (869.556, 7, 62.5, 5, 21))

    def _status_base(self, battery: int) -> bytes:
        return bytes([battery]) + struct.pack("<IIIII", 1200, 360000, 50, 3, 9) + struct.pack("<hb", -88, 26)

    def test_status_short(self) -> None:
        st = p.parse_status(self._status_base(255))
        self.assertIsNone(st.battery); self.assertEqual((st.rx_ok, st.rx_rejected, st.tx, st.rssi, st.snr), (50, 3, 9, -88, 6.5))
        self.assertIsNone(st.uptime_s)

    def test_status_extended(self) -> None:
        ext = struct.pack("<hIIIIHBB", -120, 1, 2, 3, 3600, 4100, 0, 2)
        for tail in (b"", struct.pack("<I", 0)):   # with and without the radio restarts counter
            st = p.parse_status(self._status_base(87) + ext + tail)
            self.assertEqual((st.battery, st.noise_floor, st.uptime_s, st.battery_mv, st.duty), (87, -120, 3600, 4100, 2))

    def test_rx_log_with_path_and_out_of_region(self) -> None:
        raw = (struct.pack("<IIBBHhbB", 4, 77, 0, 2, 101, -95, -10, 18) + bytes([1, 2])
               + struct.pack("<Hb", 100, 20) + struct.pack("<Hb", 101, -8))
        log = p.parse_rx_log(raw)
        self.assertEqual((log.member, log.counter, log.type, log.hops, log.via, log.rssi, log.snr),
                         (4, 77, "channel", 2, 101, -95, -2.5))
        self.assertEqual(log.decision, "out_of_region"); self.assertTrue(log.routed)
        self.assertEqual(log.path, [(100, 5.0), (101, -2.0)])

    def test_rx_log_old_firmware_and_unknown_codes(self) -> None:
        log = p.parse_rx_log(struct.pack("<IIBBHhbB", 0xFF0000 | 12, 1, 99, 0, 0, -60, 0, 200))
        self.assertEqual((log.type, log.decision, log.path), ("type99", "decision200", []))
        self.assertEqual(p.member_label(log.member), "repeater 12")

    def test_short_frame_raises(self) -> None:
        with self.assertRaises(ValueError):
            p.parse_rx_log(b"\x04\x00\x00")

    def test_message(self) -> None:
        raw = struct.pack("<HBIIIBHhb", 3, 0, 4, 12, 1762862400, 1, 100, -70, 30) + s("hallo")
        m = p.parse_message(raw)
        self.assertEqual((m.index, m.channel, m.member, m.counter, m.hops, m.via, m.rssi, m.snr, m.text),
                         (3, 0, 4, 12, 1, 100, -70, 7.5, "hallo"))

    def test_advert_position_and_none(self) -> None:
        a = p.parse_advert(struct.pack("<IBii", 4, 1, 521234567, 48765432) + s("base") + struct.pack("<BHh", 0, 0, -50))
        self.assertAlmostEqual(a.lat, 52.1234567); self.assertAlmostEqual(a.lon, 4.8765432)
        self.assertEqual((a.name, a.rssi), ("base", -50))
        a = p.parse_advert(struct.pack("<IBii", 4, 0, 0, 0) + s("x") + struct.pack("<BHh", 0, 0, -50))
        self.assertIsNone(a.lat); self.assertIsNone(a.lon)

    def test_member_label(self) -> None:
        self.assertEqual([p.member_label(m) for m in (0, 4, 0xFF0000 | 100)], ["server", "4", "repeater 100"])

    def test_decision_list_matches_firmware_count(self) -> None:
        self.assertEqual(len(p.DECISIONS), 19); self.assertEqual(p.DECISIONS[18], "out_of_region")
        self.assertEqual(len(p.PACKET_TYPES), 16)


if __name__ == "__main__":
    unittest.main()
