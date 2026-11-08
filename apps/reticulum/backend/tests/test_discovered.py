"""backend/discovered.py -- reading rnsd's discovered-interface files.

Stdlib only: a JSON decoder stands in for RNS's umsgpack, so this runs on
the dev Mac.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import discovered as d  # noqa: E402

NOW = 1_800_000_000.0


def _rnode(**over) -> dict:
    info = {
        "type": "RNodeInterface", "name": "NL-AMS-PD2EMC P1", "transport": True,
        "impl_name": "microReticulum", "version": "1.86-scotmesh",
        "transport_id": "ab" * 16, "network_id": "cd" * 16, "hops": 1,
        "latitude": 52.37, "longitude": 4.89, "height": 30.0,
        "frequency": 869463000, "bandwidth": 125000, "sf": 8, "cr": 5,
        "discovered": NOW - 3600, "last_heard": NOW - 60, "heard_count": 4,
        "stamp": "x", "ifac_netkey": "secret", "config_entry": "passphrase = secret",
    }
    info.update(over)
    return info


class TestDestinationsOf(unittest.TestCase):
    def test_known_identity(self) -> None:
        # Reference values computed with RNS.Destination.hash() (RNS 1.5.7)
        # for an identity whose hash is 00..0f.
        got = d.destinations_of(bytes(range(16)).hex())
        self.assertEqual(got, {
            "lxmf": "a953c22cba1cff60cbaf4d3c75ecf35a",
            "nomadnet": "2559e601e79041e4289105d63fd1c83d",
        })

    def test_bad_network_id_gives_nothing(self) -> None:
        for bad in (None, "", "zz", "abcd"):
            self.assertEqual(d.destinations_of(bad), {})


class TestReadDiscovered(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = self._tmp.name
        self.dir = d.storage_dir(self.cfg)
        os.makedirs(self.dir)
        d.reset_cache()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _put(self, name: str, info) -> None:
        with open(os.path.join(self.dir, name), "w") as fh:
            fh.write(info if isinstance(info, str) else json.dumps(info))

    def _read(self) -> list[dict]:
        return d.read_discovered(self.cfg, now=NOW, unpackb=json.loads)

    def test_missing_directory_is_empty(self) -> None:
        self.assertEqual(d.read_discovered(os.path.join(self.cfg, "nope"), now=NOW, unpackb=json.loads), [])

    def test_rnode_entry_is_whitelisted(self) -> None:
        self._put("a", _rnode())
        (e,) = self._read()
        self.assertEqual(e["name"], "NL-AMS-PD2EMC P1")
        self.assertEqual((e["frequency"], e["bandwidth"], e["sf"], e["cr"]), (869463000, 125000, 8, 5))
        self.assertEqual(e["status"], "available")
        self.assertEqual(e["software"], "microReticulum")
        for secret in ("ifac_netkey", "config_entry", "stamp"):
            self.assertNotIn(secret, e)

    def test_status_thresholds_and_removal_age(self) -> None:
        self._put("a", _rnode(name="fresh", last_heard=NOW - 3600))
        self._put("b", _rnode(name="unknown", last_heard=NOW - 2 * 86400))
        self._put("c", _rnode(name="stale", last_heard=NOW - 5 * 86400))
        self._put("d", _rnode(name="gone", last_heard=NOW - 8 * 86400))
        got = {e["name"]: e["status"] for e in self._read()}
        self.assertEqual(got, {"fresh": "available", "unknown": "unknown", "stale": "stale"})

    def test_no_location_or_null_island_is_skipped(self) -> None:
        self._put("a", _rnode(latitude=None, longitude=None))
        self._put("b", _rnode(latitude=0.0, longitude=0.0))
        self._put("c", _rnode(latitude=95.0))
        self.assertEqual(self._read(), [])

    def test_broken_file_is_skipped_not_fatal(self) -> None:
        self._put("bad", "{not json")
        self._put("list", "[1, 2]")
        self._put("ok", _rnode())
        self.assertEqual([e["name"] for e in self._read()], ["NL-AMS-PD2EMC P1"])

    def test_newest_first(self) -> None:
        self._put("a", _rnode(name="old", last_heard=NOW - 7200))
        self._put("b", _rnode(name="new", last_heard=NOW - 10))
        self.assertEqual([e["name"] for e in self._read()], ["new", "old"])

    def test_address_only_for_admins(self) -> None:
        self._put("a", _rnode(type="BackboneInterface", name="node.reticulumnet.nl",
                              reachable_on="node.reticulumnet.nl", port=4242))
        # prime the cache, so list_for doesn't import RNS's umsgpack
        d._cache.update(key=self.cfg, at=time.monotonic(), items=self._read())
        admin = d.list_for(self.cfg, include_address=True)[0]
        viewer = d.list_for(self.cfg, include_address=False)[0]
        self.assertEqual((admin["reachable_on"], admin["port"]), ("node.reticulumnet.nl", 4242))
        self.assertNotIn("reachable_on", viewer)
        self.assertNotIn("port", viewer)
        self.assertNotIn("frequency", viewer)  # not an RNode


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
