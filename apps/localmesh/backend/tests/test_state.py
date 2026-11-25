"""Tests for the receiver list in state.py: init from config, and saving without wiping other plugins.localmesh keys."""

from __future__ import annotations

import unittest
from unittest import mock

from plugins.apps.localmesh.backend import state


class TestState(unittest.TestCase):
    def tearDown(self) -> None:
        state.init({})

    def test_init_and_copy(self) -> None:
        state.init({"devices": [{"serial_port": "/dev/ttyACM0", "label": "base"}]})
        devices = state.devices()
        devices[0]["label"] = "changed"
        self.assertEqual(state.devices(), [{"serial_port": "/dev/ttyACM0", "label": "base"}])
        self.assertEqual(state.to_dict(), {"devices": [{"serial_port": "/dev/ttyACM0", "label": "base"}],
                                           "channel_names": {}})   # 18-11: channel-name overrides too

    def test_status_key_change_keeps_the_old_one_24_hours(self) -> None:
        a, b = b"a" * 32, b"b" * 32
        with mock.patch.object(state, "_persist"):
            state.set_status_key(a, now=1000.0)
            self.assertEqual(state.status_keys(now=1000.0), [a])
            state.set_status_key(b, now=2000.0)
            state.set_status_key(b, now=3000.0)                    # the same one again changes nothing
        self.assertEqual(state.status_keys(now=2000.0 + 86399), [b, a])
        self.assertEqual(state.status_keys(now=2000.0 + 86401), [b])
        with mock.patch.object(state, "_persist"):
            state.set_status_key(None)
        self.assertEqual(state.status_keys(now=2001.0), [])

    def test_status_key_is_saved_read_back_and_removed(self) -> None:
        saved = {}
        with mock.patch.object(state, "_current_saved_config", side_effect=lambda: dict(saved)), \
                mock.patch("src.config.save_section_to_yaml", side_effect=lambda _s, v: (saved.clear(), saved.update(v["localmesh"]))):
            state.set_status_key(b"a" * 32); state.set_status_key(b"b" * 32)
            self.assertNotIn("status_key", state.to_dict())
            state.init(dict(saved))
            self.assertEqual(state.status_keys(), [b"b" * 32, b"a" * 32])
            state.set_channel_names({1: "x"})                       # another save keeps it
            self.assertEqual(saved["status_key"], "lmst1-" + "62" * 32)
            state.set_status_key(None)
        self.assertFalse([k for k in saved if k.startswith("status_key")])
        state.init({"status_key": 5, "status_key_prev_until": "x"})  # a hand-edited file
        self.assertEqual(state.status_keys(), [])

    def test_no_devices(self) -> None:
        state.init({"enabled": True})
        self.assertEqual(state.devices(), [])

    def test_persist_merges_over_current_saved_config(self) -> None:
        """"enabled" belongs to Settings -> Plugins and must survive a save from the Settings tab."""
        with mock.patch.object(state, "_current_saved_config", return_value={"enabled": True, "extra": "kept"}):
            with mock.patch("src.config.save_section_to_yaml") as save:
                state.set_devices([{"serial_port": "/dev/ttyACM1", "label": "base"}])
        (section, values), _ = save.call_args
        self.assertEqual(section, "plugins")
        self.assertEqual(values["localmesh"], {"enabled": True, "extra": "kept",
                                               "devices": [{"serial_port": "/dev/ttyACM1", "label": "base"}],
                                               "channel_names": {}})


if __name__ == "__main__":
    unittest.main()
