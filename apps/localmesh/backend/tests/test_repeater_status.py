"""Tests for repeater_status.py: RFC 8439's ChaCha20, the firmware's own sealed status, and refusing what is not one."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import os
import unittest
from pathlib import Path

from plugins.apps.localmesh.backend import repeater_status as rs

NET = b"\x5a" * 32
KEY = hmac.new(NET, b"status", hashlib.sha256).digest()
SEALED = bytes.fromhex("0170663336b39f4c0f37c15888f9e6fd")   # firmware vectors.h V_ST_FRAME, its payload
PLAIN = bytes([1, 0x0F, 0x93, 0x98, 0, 27, 3, 1, 0x04, 0xD2, 0, 5, 0, 5, 0, 4])
V3 = bytes([3]) + PLAIN[1:] + bytes([2, 0, 101, 40, 0, 7, 0xEC]) + bytes([1, 0, 0, 9, 3]) + bytes([1, 0, 0, 8, 0, 60]) \
    + bytes([8, 0, 2, 0, 0, 0, 0, 255, 0]) + bytes([1, 0, 0, 6, 4])


class TestRepeaterStatus(unittest.TestCase):
    def test_chacha20_rfc8439(self) -> None:
        # RFC 8439 2.4.2 starts at block 1, so block 0 is skipped here
        key, nonce = bytes(range(32)), bytes.fromhex("000000000000004a00000000")
        text = b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it."
        out = rs.chacha20(key, nonce, bytes(64) + text)[64:]
        self.assertEqual(out[:16].hex(), "6e2e359a2568f98041ba0728dd0d6981")
        self.assertEqual(out[-10:].hex(), "b40b8eedf2785e42874d"[-20:])

    def test_firmware_vector(self) -> None:
        self.assertEqual(rs.unseal(KEY, 0xFF0067, 42, SEALED), PLAIN)
        st = rs.open_status([KEY], 0xFF0067, 42, SEALED)
        self.assertEqual((st["battery_mv"], st["noise_floor"], st["uptime_h"], st["airtime_pct"], st["queue"], st["rx_ok"],
                          st["rx_rejected"], st["version"], st["neighbor_count"]), (3987, -104, 27, 3, 1, 1234, 5, "0.5.0", 4))

    def test_wrong_key_counter_or_member(self) -> None:
        self.assertIsNone(rs.open_status([NET], 0xFF0067, 42, SEALED))
        self.assertIsNone(rs.open_status([KEY], 0xFF0067, 43, SEALED))
        self.assertIsNone(rs.open_status([KEY], 0xFF0068, 42, SEALED))
        self.assertIsNone(rs.open_status([KEY], 0xFF0067, 42, b""))

    def test_version_3_without_the_members_it_names(self) -> None:
        st = rs.read(V3)
        self.assertEqual(st["neighbors"], [{"node": 101, "snr": 10.0}, {"node": 7, "snr": -5.0}])
        self.assertEqual(st["reasons"], {"replay": 2, "budget_exceeded": 255})
        self.assertEqual(set(st), {"battery_mv", "noise_floor", "uptime_h", "airtime_pct", "queue", "rx_ok", "rx_rejected",
                                   "version", "neighbor_count", "neighbors", "reasons"})

    def test_cut_off_or_longer_is_refused(self) -> None:
        for n in range(len(V3)):
            self.assertIsNone(rs.read(V3[:n]), n)
        for bad in (V3 + b"x", PLAIN + b"x", b"\x04" + PLAIN[1:], PLAIN[:3] + b"\x05" + PLAIN[4:]):   # noise above 0 dBm
            self.assertIsNone(rs.read(bad))

    def test_random_bytes_are_hardly_ever_plausible(self) -> None:
        self.assertLess(sum(rs.read(b"\x03" + os.urandom(50)) is not None for _ in range(2000)), 3)

    def test_status_key_script(self) -> None:
        spec = importlib.util.spec_from_file_location("status_key", Path(__file__).parents[2] / "tools" / "status_key.py")
        tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
        self.assertEqual(rs.parse_key(tool.derive(NET)), KEY)
        self.assertEqual(rs.format_key(KEY), tool.derive(NET))
        self.assertIsNone(rs.parse_key(NET.hex()))


if __name__ == "__main__":
    unittest.main()
