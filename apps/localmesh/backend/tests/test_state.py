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
        self.assertEqual(state.to_dict(), {"devices": [{"serial_port": "/dev/ttyACM0", "label": "base"}]})

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
                                               "devices": [{"serial_port": "/dev/ttyACM1", "label": "base"}]})


if __name__ == "__main__":
    unittest.main()
