"""backend/regional.py -- RNode starting profiles (TODO 41, upstream regional.py).

Ours adds "NL / our network" (the plugin's own defaults) as the first and EU
profile; the others are upstream's. Stdlib only, runs on the dev Mac.
"""

from __future__ import annotations

import unittest

from plugins.apps.reticulum.backend import regional, state

# Band edges (Hz) each profile's whole channel must fit in.
_BANDS = {
    "nl": (869_400_000, 869_650_000),
    "eu": (869_400_000, 869_650_000),
    "us": (902_000_000, 928_000_000),
    "anz": (915_000_000, 928_000_000),
    "in": (865_000_000, 867_000_000),
    "kr": (920_900_000, 923_300_000),
    "sg": (920_000_000, 925_000_000),
}


class TestProfiles(unittest.TestCase):
    def test_ids_in_order_ours_first(self) -> None:
        self.assertEqual([p["id"] for p in regional.profiles()], list(_BANDS))

    def test_every_channel_fits_its_band(self) -> None:
        for p in regional.profiles():
            low, high = _BANDS[p["id"]]
            half = p["settings"]["rnode_bandwidth_hz"] / 2
            with self.subTest(profile=p["id"]):
                self.assertGreaterEqual(p["settings"]["rnode_frequency_hz"] - half, low)
                self.assertLessEqual(p["settings"]["rnode_frequency_hz"] + half, high)

    def test_ours_is_exactly_the_plugin_defaults(self) -> None:
        (nl,) = [p for p in regional.profiles() if p["id"] == "nl"]
        for key, value in nl["settings"].items():
            with self.subTest(key=key):
                self.assertEqual(value, state._DEFAULTS[key])

    def test_profiles_only_touch_the_five_radio_keys(self) -> None:
        keys = {"rnode_frequency_hz", "rnode_bandwidth_hz", "rnode_spreading_factor",
                "rnode_coding_rate", "rnode_tx_power"}
        for p in regional.profiles():
            with self.subTest(profile=p["id"]):
                self.assertEqual(set(p["settings"]), keys)

    def test_a_copy_each_time(self) -> None:
        regional.profiles()[0]["settings"]["rnode_tx_power"] = 99
        self.assertEqual(regional.profiles()[0]["settings"]["rnode_tx_power"], 20)


class TestPreselection(unittest.TestCase):
    def test_region_picks_its_profile(self) -> None:
        cases = {"EU_868": "nl", "eu_868": "nl", "US": "us", "ANZ": "anz", "IN": "in",
                 "KR": "kr", "SG_923": "sg"}
        for region, profile in cases.items():
            with self.subTest(region=region):
                self.assertEqual(regional.profile_for_region(region), profile)

    def test_unknown_or_missing_region_falls_back_to_ours(self) -> None:
        for region in ("", None, "UNSET", "CN", "EU_433"):
            with self.subTest(region=region):
                self.assertEqual(regional.profile_for_region(region), "nl")

    def test_describe(self) -> None:
        info = regional.describe("US")
        self.assertEqual(info["region"], "US")
        self.assertEqual(info["selected"], "us")
        self.assertEqual(len(info["profiles"]), len(_BANDS))
        self.assertIn("peer", info["note"])



class TestDeviceRegion(unittest.TestCase):
    def tearDown(self) -> None:
        state.set_region_provider(lambda: "")

    def test_read_live_from_the_provider(self) -> None:
        current = {"region": "US"}
        state.set_region_provider(lambda: current["region"])
        self.assertEqual(state.device_region(), "US")
        current["region"] = "EU_868"
        self.assertEqual(state.device_region(), "EU_868")

    def test_broken_provider_means_unknown(self) -> None:
        def boom():
            raise AttributeError("no radio")
        state.set_region_provider(boom)
        self.assertEqual(state.device_region(), "")
        self.assertEqual(regional.describe(state.device_region())["selected"], "nl")


if __name__ == "__main__":
    unittest.main()
