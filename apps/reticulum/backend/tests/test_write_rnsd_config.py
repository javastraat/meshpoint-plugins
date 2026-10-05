"""write_rnsd_config.py -- the extra-interface INI block builder.

Pure string generation; `load_config` is deferred into main() so this
imports on the dev Mac.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parents[2]
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import write_rnsd_config as w  # noqa: E402


class TestExtraInterfaceBlocks(unittest.TestCase):
    def test_tcp_client_block(self) -> None:
        out = w._extra_interface_blocks([
            {"name": "Second backbone", "type": "TCPClientInterface",
             "enabled": True, "target_host": "node.example.com", "target_port": 4242},
        ])
        self.assertIn("[[Second backbone]]", out)
        self.assertIn("type = TCPClientInterface", out)
        self.assertIn("target_host = node.example.com", out)
        self.assertIn("target_port = 4242", out)
        self.assertIn("enabled = Yes", out)

    def test_udp_block_has_all_four_fields(self) -> None:
        out = w._extra_interface_blocks([
            {"name": "LAN", "type": "UDPInterface", "listen_ip": "0.0.0.0",
             "listen_port": 4242, "forward_ip": "255.255.255.255", "forward_port": 4242},
        ])
        for f in ("listen_ip", "listen_port", "forward_ip", "forward_port"):
            self.assertIn(f + " = ", out)

    def test_disabled_entry_is_skipped(self) -> None:
        out = w._extra_interface_blocks([
            {"name": "Off", "type": "TCPClientInterface", "enabled": False,
             "target_host": "x", "target_port": 1},
        ])
        self.assertEqual(out, "")

    def test_unknown_type_reserved_dup_and_missing_field_all_skipped(self) -> None:
        out = w._extra_interface_blocks([
            {"name": "A", "type": "Nope"},
            {"name": "RNode LoRa", "type": "TCPClientInterface", "target_host": "x", "target_port": 1},
            {"name": "Dup", "type": "TCPClientInterface", "target_host": "x", "target_port": 1},
            {"name": "Dup", "type": "TCPClientInterface", "target_host": "y", "target_port": 2},
            {"name": "NoPort", "type": "TCPServerInterface", "listen_ip": "0.0.0.0"},
        ])
        self.assertEqual(out.count("[["), 1)  # only the first "Dup" survives
        self.assertIn("[[Dup]]", out)
        self.assertNotIn("RNode LoRa", out)

    def test_bracket_stripped_from_name(self) -> None:
        out = w._extra_interface_blocks([
            {"name": "[[weird]]", "type": "TCPClientInterface",
             "target_host": "x", "target_port": 1},
        ])
        self.assertIn("[[weird]]", out)
        self.assertNotIn("[[[[", out)

    def test_non_list_input_is_empty(self) -> None:
        self.assertEqual(w._extra_interface_blocks(None), "")
        self.assertEqual(w._extra_interface_blocks("nope"), "")
        self.assertEqual(w._extra_interface_blocks([]), "")

    def test_template_has_the_extra_slot(self) -> None:
        # a formatting regression here means rnsd gets a broken config
        rendered = w._TEMPLATE.format(
            lan_autodiscovery_enabled="No", rnode_block="", backbone_block="", extra_block="X",
        )
        self.assertIn("X", rendered)
        self.assertIn("[interfaces]", rendered)

    def test_lan_autodiscovery_defaults_off(self) -> None:
        self.assertFalse(w._DEFAULTS["lan_autodiscovery_enabled"])

    def test_lan_autodiscovery_enabled_renders_yes(self) -> None:
        rendered = w._TEMPLATE.format(
            lan_autodiscovery_enabled="Yes", rnode_block="", backbone_block="", extra_block="",
        )
        self.assertIn("[[Default Interface]]\n    type = AutoInterface\n    enabled = Yes", rendered)


class TestInterfaceModes(unittest.TestCase):
    def test_defaults_keep_backbone_announces_off_rf(self) -> None:
        self.assertEqual(w._DEFAULTS["rnode_interface_mode"], "access_point")
        self.assertEqual(w._DEFAULTS["backbone_interface_mode"], "full")

    def test_templates_render_mode(self) -> None:
        rnode = w._RNODE_TEMPLATE.format(
            rnode_serial_port="/dev/ttyACM0", rnode_frequency_hz=1, rnode_bandwidth_hz=1,
            rnode_tx_power=1, rnode_spreading_factor=8, rnode_coding_rate=5,
            rnode_interface_mode="roaming",
        )
        self.assertIn("mode = roaming", rnode)
        backbone = w._BACKBONE_TEMPLATE.format(
            backbone_host="h", backbone_port=4242, backbone_interface_mode="boundary",
        )
        self.assertIn("mode = boundary", backbone)

    def test_invalid_mode_falls_back_to_default(self) -> None:
        rc = dict(w._DEFAULTS, rnode_interface_mode="bogus", backbone_interface_mode="FULL")
        self.assertEqual(w._interface_mode(rc, "rnode_interface_mode"), "access_point")
        self.assertEqual(w._interface_mode(rc, "backbone_interface_mode"), "full")

    def test_extra_interface_mode(self) -> None:
        base = {"type": "TCPClientInterface", "target_host": "x", "target_port": 1}
        out = w._extra_interface_blocks([
            dict(base, name="A", mode="boundary"),
            dict(base, name="B"),               # no mode -> RNS default, no line
            dict(base, name="C", mode="weird"),  # unknown -> full, no line
        ])
        self.assertEqual(out.count("mode = "), 1)
        self.assertIn("[[A]]\n    type = TCPClientInterface\n    enabled = Yes\n    mode = boundary", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
