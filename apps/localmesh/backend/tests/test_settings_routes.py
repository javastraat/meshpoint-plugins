"""Tests for GET/PUT /api/localmesh/settings: the handlers are called directly (same harness as the dapnet plugin),
with state._persist patched out (covered by test_state.py)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from plugins.apps.localmesh.backend import settings_routes as routes
from plugins.apps.localmesh.backend import state
from src.api.audit import AuditLogWriter
from src.api.auth.jwt_session import SessionClaims


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _dev(port: str, label: str = "") -> routes.DeviceEntry:
    return routes.DeviceEntry(serial_port=port, label=label)


class TestSettingsRoutes(unittest.TestCase):
    def setUp(self) -> None:
        state.init({"devices": [{"serial_port": "/dev/ttyACM0", "label": "old"}]})
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audit = AuditLogWriter(log_path=Path(self.tmp.name) / "audit.jsonl")
        self.claims = SessionClaims("test-admin", "admin", 1)
        patcher = mock.patch.object(state, "_persist")
        self.persist = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        state.init({})

    def _put(self, *devices):
        return _run(routes.update_localmesh_settings(
            routes.LocalMeshSettingsUpdate(devices=list(devices)), _claims=self.claims, audit=self.audit))

    def test_get(self) -> None:
        self.assertEqual(_run(routes.get_localmesh_settings()),
                         {"devices": [{"serial_port": "/dev/ttyACM0", "label": "old"}], "channel_names": {},
                          "status_key_set": False})

    def test_put_saves_cleaned_list_and_asks_for_restart(self) -> None:
        result = self._put(_dev(" /dev/serial/by-id/usb-Espressif-if00 ", " Base "), _dev("/dev/ttyACM1", "attic"))
        expected = [{"serial_port": "/dev/serial/by-id/usb-Espressif-if00", "label": "base"},
                    {"serial_port": "/dev/ttyACM1", "label": "attic"}]
        self.assertEqual(result, {"saved": True, "restart_required": True, "devices": expected, "channel_names": {}})
        self.assertEqual(state.devices(), expected)
        self.persist.assert_called_once()

    def test_put_empty_list_removes_all(self) -> None:
        self._put()
        self.assertEqual(state.devices(), [])

    def test_rejected_lists_change_nothing(self) -> None:
        bad = [
            [_dev("")],                                              # no port
            [_dev("/dev/a", "has space")],                           # label not usable in a source name
            [_dev("/dev/a", "x" * 17)],                              # label too long
            [_dev("/dev/a", "base"), _dev("/dev/b", "base")],        # two sources with one name
            [_dev("/dev/a"), _dev("/dev/b")],                        # two without a label: same name too
            [_dev("/dev/a", "x"), _dev("/dev/a", "y")],              # one port twice
            [_dev(f"/dev/{n}", f"r{n}") for n in range(routes.MAX_DEVICES + 1)],
        ]
        for devices in bad:
            with self.subTest(devices=devices):
                with self.assertRaises(HTTPException) as ctx:
                    self._put(*devices)
                self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(state.devices(), [{"serial_port": "/dev/ttyACM0", "label": "old"}])
        self.persist.assert_not_called()


    def test_status_key_is_write_only(self) -> None:
        put = lambda key: _run(routes.update_status_key(routes.StatusKeyUpdate(key=key), _claims=self.claims, audit=self.audit))
        good = "lmst1-" + "ab" * 32
        self.assertEqual(put(f" {good} "), {"saved": True, "status_key_set": True})
        self.assertEqual(state.status_keys(), [b"\xab" * 32])
        for bad in ("ab" * 32, "lmst1-" + "ab" * 31, "lmst1-" + "zz" * 32):   # the first one: a network key by mistake
            with self.assertRaises(HTTPException) as cm:
                put(bad)
            self.assertEqual(cm.exception.status_code, 400)
            self.assertNotIn("ababab", cm.exception.detail)
        got = _run(routes.get_localmesh_settings())
        self.assertTrue(got["status_key_set"])
        self.assertNotIn("abab", str(got))
        self.assertNotIn("abab", (Path(self.tmp.name) / "audit.jsonl").read_text())
        self.assertEqual(put(""), {"saved": True, "status_key_set": False})
        self.assertEqual(state.status_keys(), [])

    def test_channel_names_up_to_127(self) -> None:
        """20-11: channels run 0-127 in the firmware and the app (Test 126, Meldingen 127); it said 0-39."""
        self.assertEqual(routes.validate_channel_names({"126": " Test ", "127": "Meldingen", "0": "Algemeen"}),
                         {126: "Test", 127: "Meldingen", 0: "Algemeen"})
        for bad in ("128", "255", "-1"):
            with self.assertRaises(HTTPException) as cm:
                routes.validate_channel_names({bad: "x"})
            self.assertIn("0-127", cm.exception.detail)

if __name__ == "__main__":
    unittest.main()
