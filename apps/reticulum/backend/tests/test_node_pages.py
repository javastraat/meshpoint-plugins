"""Tests for backend/node_pages.py -- the Pages-tab file layer.

Pure filesystem + regex, no FastAPI, no RNS. A tmp dir stands in for
``node_pages_dir``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.apps.reticulum.backend import node_pages
from plugins.apps.reticulum.backend.node_pages import PageError


class TestValidateName(unittest.TestCase):
    def test_accepts_plain_mu_names(self) -> None:
        for name in ("index.mu", "about.mu", "my-page_2.mu"):
            self.assertEqual(node_pages.validate_name(name), name)

    def test_rejects_non_mu(self) -> None:
        for name in ("index", "index.txt", "index.mu.sh", ""):
            with self.assertRaises(PageError):
                node_pages.validate_name(name)

    def test_rejects_traversal_and_absolute(self) -> None:
        for name in ("../evil.mu", "/etc/x.mu", "..mu", "....mu", "a/../b.mu"):
            with self.assertRaises(PageError):
                node_pages.validate_name(name)

    def test_rejects_reserved_generated_names_at_top_level(self) -> None:
        for name in ("info.mu", "nodes.mu"):
            with self.assertRaises(PageError):
                node_pages.validate_name(name)

    def test_accepts_one_level_of_collection_subfolder(self) -> None:
        for path in ("hulp/index.mu", "hulp/eerste-hulp.mu", "a/b.mu"):
            self.assertEqual(node_pages.validate_name(path), path)

    def test_reserved_names_do_not_apply_inside_a_collection(self) -> None:
        # info.mu/nodes.mu are only reserved at the top level -- nomad_node.py
        # generates those there specifically; a collection-level info.mu
        # doesn't collide with anything (served at /page/hulp/info.mu).
        self.assertEqual(node_pages.validate_name("hulp/info.mu"), "hulp/info.mu")
        self.assertEqual(node_pages.validate_name("hulp/nodes.mu"), "hulp/nodes.mu")

    def test_rejects_deeper_than_one_level(self) -> None:
        for path in ("a/b/c.mu", "a/b/c/d.mu"):
            with self.assertRaises(PageError):
                node_pages.validate_name(path)

    def test_rejects_bad_collection_slug(self) -> None:
        for path in ("Hulp/index.mu", "hu lp/index.mu", "files/index.mu", "../x/y.mu"):
            with self.assertRaises(PageError):
                node_pages.validate_name(path)

    def test_rejects_bad_leaf_inside_collection(self) -> None:
        for path in ("hulp/index", "hulp/index.txt", "hulp/"):
            with self.assertRaises(PageError):
                node_pages.validate_name(path)


class TestValidateCollectionSlug(unittest.TestCase):
    def test_accepts_plain_slugs(self) -> None:
        for slug in ("hulp", "a", "my-wiki-2"):
            self.assertEqual(node_pages.validate_collection_slug(slug), slug)

    def test_rejects_bad_slugs(self) -> None:
        for slug in ("", "Hulp", "hu lp", "../evil", "a" * 40):
            with self.assertRaises(PageError):
                node_pages.validate_collection_slug(slug)

    def test_rejects_reserved_slug(self) -> None:
        with self.assertRaises(PageError):
            node_pages.validate_collection_slug("files")


class TestPageIO(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "pages"  # deliberately absent

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_creates_dir_and_read_roundtrips(self) -> None:
        meta = node_pages.write_page(self.dir, "index.mu", "`!hi`!\n")
        self.assertTrue(meta["exists"])
        self.assertEqual(node_pages.read_page(self.dir, "index.mu"), "`!hi`!\n")
        self.assertTrue((self.dir / "index.mu").is_file())

    def test_written_file_is_not_executable(self) -> None:
        node_pages.write_page(self.dir, "x.mu", "hi")
        mode = (self.dir / "x.mu").stat().st_mode
        self.assertEqual(mode & 0o111, 0, "page must never be executable")

    def test_read_missing_page_is_empty_string(self) -> None:
        self.assertEqual(node_pages.read_page(self.dir, "nope.mu"), "")

    def test_oversized_write_is_rejected(self) -> None:
        big = "a" * (node_pages.MAX_PAGE_BYTES + 1)
        with self.assertRaises(PageError):
            node_pages.write_page(self.dir, "big.mu", big)

    def test_delete_is_idempotent(self) -> None:
        node_pages.write_page(self.dir, "x.mu", "hi")
        node_pages.delete_page(self.dir, "x.mu")
        node_pages.delete_page(self.dir, "x.mu")  # no error second time
        self.assertFalse((self.dir / "x.mu").exists())

    def test_list_puts_index_first_and_always_lists_it(self) -> None:
        node_pages.write_page(self.dir, "zeta.mu", "z")
        node_pages.write_page(self.dir, "about.mu", "a")
        names = [e["name"] for e in node_pages.list_pages(self.dir)]
        self.assertEqual(names, ["index.mu", "about.mu", "zeta.mu"])
        index_entry = node_pages.list_pages(self.dir)[0]
        self.assertFalse(index_entry["exists"])  # listed but not created yet

    def test_list_ignores_reserved_and_non_mu(self) -> None:
        self.dir.mkdir(parents=True)
        (self.dir / "info.mu").write_text("x")
        (self.dir / "nodes.mu").write_text("x")
        (self.dir / "notes.txt").write_text("x")
        node_pages.write_page(self.dir, "real.mu", "x")
        names = [e["name"] for e in node_pages.list_pages(self.dir)]
        self.assertEqual(sorted(names), ["index.mu", "real.mu"])

    def test_write_rejects_reserved_name(self) -> None:
        with self.assertRaises(PageError):
            node_pages.write_page(self.dir, "info.mu", "x")


class TestCollectionPageIO(unittest.TestCase):
    """One level of subfolder -- same files a reticulum-scraper collection
    produces, but exercised directly through the read/write/delete/list
    API the Pages tab now shares with it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "pages"  # deliberately absent

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_creates_collection_folder_and_read_roundtrips(self) -> None:
        meta = node_pages.write_page(self.dir, "hulp/index.mu", "`!hi`!\n")
        self.assertTrue(meta["exists"])
        self.assertEqual(meta["path"], "hulp/index.mu")
        self.assertEqual(meta["collection"], "hulp")
        self.assertEqual(node_pages.read_page(self.dir, "hulp/index.mu"), "`!hi`!\n")
        self.assertTrue((self.dir / "hulp" / "index.mu").is_file())

    def test_top_level_write_has_no_collection(self) -> None:
        meta = node_pages.write_page(self.dir, "about.mu", "x")
        self.assertIsNone(meta["collection"])
        self.assertEqual(meta["path"], "about.mu")

    def test_delete_inside_collection(self) -> None:
        node_pages.write_page(self.dir, "hulp/burns.mu", "x")
        node_pages.delete_page(self.dir, "hulp/burns.mu")
        node_pages.delete_page(self.dir, "hulp/burns.mu")  # idempotent
        self.assertFalse((self.dir / "hulp" / "burns.mu").exists())

    def test_list_includes_collection_pages_after_top_level(self) -> None:
        node_pages.write_page(self.dir, "about.mu", "x")
        node_pages.write_page(self.dir, "hulp/index.mu", "x")
        node_pages.write_page(self.dir, "hulp/burns.mu", "x")
        entries = node_pages.list_pages(self.dir)
        top = [e for e in entries if e["collection"] is None]
        coll = [e for e in entries if e["collection"] == "hulp"]
        self.assertEqual([e["name"] for e in top], ["index.mu", "about.mu"])
        self.assertEqual([e["path"] for e in coll], ["hulp/burns.mu", "hulp/index.mu"])

    def test_list_ignores_files_dir_and_bad_collection_names(self) -> None:
        (self.dir / "files").mkdir(parents=True)
        (self.dir / "files" / "photo.png").write_bytes(b"")
        node_pages.write_page(self.dir, "hulp/index.mu", "x")
        collections = {e["collection"] for e in node_pages.list_pages(self.dir)} - {None}
        self.assertEqual(collections, {"hulp"})

    def test_list_ignores_meta_json_sidecar(self) -> None:
        node_pages.write_page(self.dir, "hulp/index.mu", "x")
        (self.dir / "hulp" / "_meta.json").write_text("{}")
        names = [e["name"] for e in node_pages.list_pages(self.dir) if e["collection"] == "hulp"]
        self.assertEqual(names, ["index.mu"])


class TestSampleIndex(unittest.TestCase):
    def test_sample_index_returns_micron_text(self) -> None:
        text = node_pages.sample_index()
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
