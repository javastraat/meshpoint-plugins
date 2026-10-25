"""Tests for the NomadNet fetcher's non-RNS paths.

On a machine without ``rns`` installed (the dev Mac), ``nomad.RNS`` is
``None`` and ``available()`` is ``False`` -- ``fetch_page`` returns an
error result instead of raising, which is what's covered here. The real
Link/Request round trip is integration-level (needs rnsd + a live
NomadNet node) and lives on the Pi.
"""

from __future__ import annotations

import asyncio
import unittest

from plugins.apps.reticulum.backend import nomad


@unittest.skipIf(nomad.RNS is not None, "rns installed -- covers only the not-available path")
class TestNomadWithoutRns(unittest.TestCase):
    def tearDown(self) -> None:
        nomad.reset()

    def test_available_is_false_without_rns(self) -> None:
        self.assertFalse(nomad.available())

    def test_fetch_page_returns_an_error_result_not_an_exception(self) -> None:
        result = asyncio.run(nomad.fetch_page("abcd1234", "/page/index.mu"))
        self.assertFalse(result.ok)
        self.assertIsNone(result.content)
        self.assertIn("Reticulum is not running", result.error)
        self.assertEqual(result.destination_hash, "abcd1234")
        self.assertEqual(result.path, "/page/index.mu")

    def test_reset_clears_the_link_cache(self) -> None:
        nomad._links["deadbeef"] = object()
        nomad.reset()
        self.assertEqual(nomad._links, {})

    def test_identify_link_returns_an_error_result_not_an_exception(self) -> None:
        ok, result = asyncio.run(nomad.identify_link("abcd1234", object()))
        self.assertFalse(ok)
        self.assertIn("Reticulum is not running", result)

    def test_reset_clears_fingerprints_too(self) -> None:
        nomad._fingerprints["deadbeef"] = "some-hash"
        nomad.reset()
        self.assertEqual(nomad._fingerprints, {})

    def test_drop_link_tears_down_and_forgets_both_caches(self) -> None:
        class _FakeLink:
            def __init__(self) -> None:
                self.torn_down = False

            def teardown(self) -> None:
                self.torn_down = True

        link = _FakeLink()
        nomad._links["deadbeef"] = link
        nomad._fingerprints["deadbeef"] = "some-hash"
        self.assertTrue(nomad.drop_link("deadbeef"))
        self.assertTrue(link.torn_down)
        self.assertNotIn("deadbeef", nomad._links)
        self.assertNotIn("deadbeef", nomad._fingerprints)

    def test_drop_link_on_nothing_cached_is_false_not_an_error(self) -> None:
        self.assertFalse(nomad.drop_link("neverseen"))

    def test_drop_link_survives_teardown_raising(self) -> None:
        class _BrokenLink:
            def teardown(self) -> None:
                raise RuntimeError("already gone")

        nomad._links["deadbeef"] = _BrokenLink()
        self.assertTrue(nomad.drop_link("deadbeef"))  # still reports it dropped the cache entry
        self.assertNotIn("deadbeef", nomad._links)


class TestFingerprintMergesIntoFieldData(unittest.TestCase):
    """The `dest` merge in fetch_page() is plain dict logic, checkable
    without RNS -- monkeypatch `_request` to capture what it was handed."""

    def tearDown(self) -> None:
        nomad.reset()

    def test_no_fingerprint_leaves_field_data_untouched(self) -> None:
        seen = {}

        async def fake_request(dest_hash_hex, path, field_data):
            seen["field_data"] = field_data
            return "err", "stub"

        original = nomad._request
        nomad._request = fake_request
        try:
            asyncio.run(nomad.fetch_page("deadbeef", "/page/index.mu", {"field_x": "1"}))
        finally:
            nomad._request = original
        self.assertEqual(seen["field_data"], {"field_x": "1"})

    def test_cached_fingerprint_merges_in_as_dest(self) -> None:
        nomad._fingerprints["deadbeef"] = "aabbcc"
        seen = {}

        async def fake_request(dest_hash_hex, path, field_data):
            seen["field_data"] = field_data
            return "err", "stub"

        original = nomad._request
        nomad._request = fake_request
        try:
            asyncio.run(nomad.fetch_page("deadbeef", "/page/index.mu", {"field_x": "1"}))
        finally:
            nomad._request = original
        self.assertEqual(seen["field_data"], {"field_x": "1", "dest": "aabbcc"})

    def test_cached_fingerprint_merges_in_even_with_no_other_field_data(self) -> None:
        nomad._fingerprints["deadbeef"] = "aabbcc"
        seen = {}

        async def fake_request(dest_hash_hex, path, field_data):
            seen["field_data"] = field_data
            return "err", "stub"

        original = nomad._request
        nomad._request = fake_request
        try:
            asyncio.run(nomad.fetch_page("deadbeef", "/page/index.mu"))
        finally:
            nomad._request = original
        self.assertEqual(seen["field_data"], {"dest": "aabbcc"})


class TestNomadResultShape(unittest.TestCase):
    def test_defaults(self) -> None:
        r = nomad.NomadResult(ok=True, content="`F222`b heading")
        self.assertTrue(r.ok)
        self.assertEqual(r.content, "`F222`b heading")
        self.assertIsNone(r.error)


class TestNomadTimeouts(unittest.TestCase):
    def tearDown(self) -> None:
        nomad.set_timeouts(None)

    def test_none_restores_defaults(self) -> None:
        nomad.set_timeouts(None)
        self.assertEqual(nomad._path_lookup_timeout_s, 20)
        self.assertEqual(nomad._request_timeout_s, 30)

    def test_scales_from_one_knob(self) -> None:
        nomad.set_timeouts(40)
        self.assertEqual(nomad._link_timeout_s, 40)
        self.assertEqual(nomad._request_timeout_s, 60)  # 1.5x

    def test_floor(self) -> None:
        nomad.set_timeouts(1)
        self.assertGreaterEqual(nomad._link_timeout_s, 5)


class TestEvictStaleLink(unittest.TestCase):
    """Pure dict logic, no RNS involved -- runs on every machine."""

    def tearDown(self) -> None:
        nomad.reset()

    def test_evicts_when_cache_still_holds_that_exact_link(self) -> None:
        link = object()
        nomad._links["deadbeef"] = link
        nomad._evict_stale_link("deadbeef", link)
        self.assertNotIn("deadbeef", nomad._links)

    def test_leaves_a_different_cached_link_alone(self) -> None:
        stale_link = object()
        fresh_link = object()
        nomad._links["deadbeef"] = fresh_link
        nomad._evict_stale_link("deadbeef", stale_link)
        self.assertIs(nomad._links["deadbeef"], fresh_link)

    def test_missing_key_does_not_raise(self) -> None:
        nomad._evict_stale_link("not-cached", object())


class _FakeLinkStatus:
    """Stand-in for RNS.Link's status constants -- sweep_stale_links()
    compares against RNS.Link.CLOSED, so these tests monkeypatch
    nomad.RNS regardless of whether the real package is installed on
    this machine."""
    CLOSED = "closed"
    ACTIVE = "active"


class _FakeRNS:
    Link = _FakeLinkStatus


def _fake_link(status=_FakeLinkStatus.ACTIVE, inactive_for=0):
    class _Link:
        torn_down = False

        def teardown(self) -> None:
            self.torn_down = True

    link = _Link()
    link.status = status
    link.inactive_for = lambda: inactive_for
    return link


class TestSweepStaleLinks(unittest.TestCase):
    def setUp(self) -> None:
        self._original_rns = nomad.RNS
        nomad.RNS = _FakeRNS

    def tearDown(self) -> None:
        nomad.RNS = self._original_rns
        nomad.reset()

    def test_drops_links_idle_past_the_threshold(self) -> None:
        nomad._links["stale"] = _fake_link(inactive_for=400)
        nomad._links["fresh"] = _fake_link(inactive_for=10)
        dropped = nomad.sweep_stale_links(idle_threshold_s=300)
        self.assertEqual(dropped, 1)
        self.assertNotIn("stale", nomad._links)
        self.assertIn("fresh", nomad._links)

    def test_drops_links_rns_already_closed_regardless_of_idle_time(self) -> None:
        nomad._links["deadbeef"] = _fake_link(status=_FakeLinkStatus.CLOSED, inactive_for=0)
        dropped = nomad.sweep_stale_links(idle_threshold_s=300)
        self.assertEqual(dropped, 1)
        self.assertNotIn("deadbeef", nomad._links)

    def test_leaves_active_recent_links_alone(self) -> None:
        nomad._links["deadbeef"] = _fake_link(inactive_for=5)
        dropped = nomad.sweep_stale_links(idle_threshold_s=300)
        self.assertEqual(dropped, 0)
        self.assertIn("deadbeef", nomad._links)

    def test_also_forgets_the_fingerprint_for_a_swept_link(self) -> None:
        nomad._links["deadbeef"] = _fake_link(inactive_for=400)
        nomad._fingerprints["deadbeef"] = "some-hash"
        nomad.sweep_stale_links(idle_threshold_s=300)
        self.assertNotIn("deadbeef", nomad._fingerprints)

    def test_empty_cache_is_a_noop(self) -> None:
        self.assertEqual(nomad.sweep_stale_links(), 0)

    def test_uses_the_default_threshold_when_not_given(self) -> None:
        nomad._links["deadbeef"] = _fake_link(inactive_for=nomad._IDLE_SWEEP_THRESHOLD_S + 1)
        self.assertEqual(nomad.sweep_stale_links(), 1)


class TestDropAllLinks(unittest.TestCase):
    def setUp(self) -> None:
        self._original_rns = nomad.RNS
        nomad.RNS = _FakeRNS

    def tearDown(self) -> None:
        nomad.RNS = self._original_rns
        nomad.reset()

    def test_drops_every_cached_link_regardless_of_idle_time(self) -> None:
        nomad._links["a"] = _fake_link(inactive_for=0)
        nomad._links["b"] = _fake_link(inactive_for=9999)
        dropped = nomad.drop_all_links()
        self.assertEqual(dropped, 2)
        self.assertEqual(nomad._links, {})

    def test_empty_cache_is_a_noop(self) -> None:
        self.assertEqual(nomad.drop_all_links(), 0)


class TestLinkCount(unittest.TestCase):
    """Pure dict logic, no RNS involved -- runs on every machine."""

    def tearDown(self) -> None:
        nomad.reset()

    def test_counts_cached_links(self) -> None:
        self.assertEqual(nomad.link_count(), 0)
        nomad._links["a"] = object()
        nomad._links["b"] = object()
        self.assertEqual(nomad.link_count(), 2)


class TestExtractFile(unittest.TestCase):
    def test_bytes_plus_name_metadata_list(self) -> None:
        name, data = nomad._extract_file([b"payload", {"name": b"/x/report.pdf"}], None)
        self.assertEqual(name, "report.pdf")
        self.assertEqual(data, b"payload")

    def test_bare_bytes(self) -> None:
        name, data = nomad._extract_file(b"raw", None)
        self.assertEqual(data, b"raw")
        self.assertEqual(name, "downloaded_file")

    def test_unsupported_shape_raises(self) -> None:
        with self.assertRaises(ValueError):
            nomad._extract_file(42, None)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
