"""Tests for backend/scraper.py -- the website-to-NomadNet-pages crawler.

Pure asyncio + filesystem, no FastAPI, no RNS. ``scraper._fetch`` is
monkeypatched to serve canned HTML -- no real network call is ever made.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugins.apps.reticulum.backend import scraper


class TestValidateCollectionName(unittest.TestCase):
    def test_accepts_plain_slugs(self) -> None:
        for name in ("hulp", "a", "my-wiki-2"):
            self.assertEqual(scraper.validate_collection_name(name), name)

    def test_rejects_bad_names(self) -> None:
        for name in ("", "Hulp", "hu lp", "../evil", "a" * 40):
            with self.assertRaises(scraper.ScraperError):
                scraper.validate_collection_name(name)

    def test_rejects_reserved_name(self) -> None:
        with self.assertRaises(scraper.ScraperError):
            scraper.validate_collection_name("files")


class TestSlugify(unittest.TestCase):
    def test_lowercases_and_dashes(self) -> None:
        self.assertEqual(
            scraper.slugify("Eerste Hulp bij Brandwonden", "x"),
            "eerste-hulp-bij-brandwonden",
        )

    def test_blank_falls_back(self) -> None:
        self.assertEqual(scraper.slugify("   ", "fallback"), "fallback")


class TestIsCrawlable(unittest.TestCase):
    HOST = "wiki.example.org"

    def test_accepts_same_host_article(self) -> None:
        self.assertTrue(scraper._is_crawlable(
            "https://wiki.example.org/wiki/index.php?title=Foo", self.HOST,
        ))

    def test_rejects_other_host(self) -> None:
        self.assertFalse(scraper._is_crawlable("https://other.org/x", self.HOST))

    def test_rejects_mediawiki_meta_pages(self) -> None:
        for bad in (
            "https://wiki.example.org/wiki/index.php?title=Special:Search",
            "https://wiki.example.org/wiki/Talk:Foo",
            "https://wiki.example.org/wiki/index.php?title=Foo&action=edit",
        ):
            self.assertFalse(scraper._is_crawlable(bad, self.HOST))

    def test_rejects_non_http_schemes(self) -> None:
        self.assertFalse(scraper._is_crawlable("mailto:x@y.org", self.HOST))


def _fake_pages(pages: dict[str, str]):
    def fetch(url: str) -> str:
        return pages[url]
    return fetch


class TestScraperCrawl(unittest.IsolatedAsyncioTestCase):
    """End-to-end crawl -> write, with ``scraper._fetch`` mocked."""

    PAGES = {
        "https://wiki.example.org/p1": (
            '<title>P1</title><p>one <a href="/p2">p2</a> '
            '<a href="/wiki/Special:Search">skip me</a></p>'
        ),
        "https://wiki.example.org/p2": (
            '<title>P2</title><p>two, back to <a href="/p1">p1</a></p>'
        ),
    }

    async def _run_job(self, pages_dir: Path, **start_kwargs) -> scraper.Scraper:
        s = scraper.Scraper(lambda: str(pages_dir), lambda: True)
        await s.start("https://wiki.example.org/p1", "hulp", **start_kwargs)
        await s._job.task
        return s

    async def test_seed_becomes_index_and_pages_cross_link(self) -> None:
        with mock.patch.object(scraper, "_fetch", _fake_pages(self.PAGES)):
            with tempfile.TemporaryDirectory() as tmp:
                pages_dir = Path(tmp) / "pages"
                await self._run_job(pages_dir, max_pages=10, max_depth=5)

                coll = pages_dir / "hulp"
                names = sorted(p.name for p in coll.iterdir())
                self.assertEqual(names, ["_meta.json", "index.mu", "p2.mu"])

                index_text = (coll / "index.mu").read_text()
                self.assertIn(":/page/hulp/p2.mu", index_text)

                p2_text = (coll / "p2.mu").read_text()
                self.assertIn(":/page/hulp/index.mu", p2_text)  # p2 links back to p1

                meta = json.loads((coll / "_meta.json").read_text())
                self.assertEqual(meta["name"], "hulp")
                self.assertEqual(len(meta["pages"]), 2)

    async def test_mediawiki_meta_links_are_never_crawled(self) -> None:
        with mock.patch.object(scraper, "_fetch", _fake_pages(self.PAGES)):
            with tempfile.TemporaryDirectory() as tmp:
                pages_dir = Path(tmp) / "pages"
                await self._run_job(pages_dir, max_pages=10, max_depth=5)
                names = sorted(p.name for p in (pages_dir / "hulp").iterdir())
                self.assertNotIn("skip-me.mu", names)

    async def test_max_pages_caps_the_crawl(self) -> None:
        with mock.patch.object(scraper, "_fetch", _fake_pages(self.PAGES)):
            with tempfile.TemporaryDirectory() as tmp:
                pages_dir = Path(tmp) / "pages"
                await self._run_job(pages_dir, max_pages=1, max_depth=5)
                names = sorted(p.name for p in (pages_dir / "hulp").iterdir())
                self.assertEqual(names, ["_meta.json", "index.mu"])

    async def test_max_depth_zero_only_scrapes_the_seed(self) -> None:
        with mock.patch.object(scraper, "_fetch", _fake_pages(self.PAGES)):
            with tempfile.TemporaryDirectory() as tmp:
                pages_dir = Path(tmp) / "pages"
                await self._run_job(pages_dir, max_pages=10, max_depth=0)
                names = sorted(p.name for p in (pages_dir / "hulp").iterdir())
                self.assertEqual(names, ["_meta.json", "index.mu"])

    async def test_second_start_while_running_is_rejected(self) -> None:
        with mock.patch.object(scraper, "_fetch", _fake_pages(self.PAGES)):
            with tempfile.TemporaryDirectory() as tmp:
                pages_dir = Path(tmp) / "pages"
                s = scraper.Scraper(lambda: str(pages_dir), lambda: True)
                await s.start("https://wiki.example.org/p1", "hulp", max_pages=10, max_depth=5)
                with self.assertRaises(scraper.ScraperError):
                    await s.start("https://wiki.example.org/p1", "other", max_pages=10, max_depth=5)
                await s.stop()
                await s._job.task

    async def test_stop_keeps_pages_already_written(self) -> None:
        with mock.patch.object(scraper, "_fetch", _fake_pages(self.PAGES)):
            with tempfile.TemporaryDirectory() as tmp:
                pages_dir = Path(tmp) / "pages"
                s = scraper.Scraper(lambda: str(pages_dir), lambda: True)
                await s.start("https://wiki.example.org/p1", "hulp", max_pages=10, max_depth=5)
                for _ in range(500):
                    if s.status()["pages_done"] >= 1:
                        break
                    await __import__("asyncio").sleep(0.01)
                await s.stop()
                await s._job.task
                self.assertEqual(s.status()["pages_done"], 1)
                names = sorted(p.name for p in (pages_dir / "hulp").iterdir())
                self.assertEqual(names, ["_meta.json", "index.mu"])

    async def test_reload_callback_fires_on_completion(self) -> None:
        calls = []
        with mock.patch.object(scraper, "_fetch", _fake_pages(self.PAGES)):
            with tempfile.TemporaryDirectory() as tmp:
                pages_dir = Path(tmp) / "pages"
                s = scraper.Scraper(lambda: str(pages_dir), lambda: calls.append(1) or True)
                await s.start("https://wiki.example.org/p1", "hulp", max_pages=10, max_depth=5)
                await s._job.task
                self.assertEqual(calls, [1])

    async def test_fetch_failure_is_skipped_not_fatal(self) -> None:
        def flaky_fetch(url: str) -> str:
            if url.endswith("/p2"):
                raise OSError("connection reset")
            return self.PAGES[url]

        with mock.patch.object(scraper, "_fetch", flaky_fetch):
            with tempfile.TemporaryDirectory() as tmp:
                pages_dir = Path(tmp) / "pages"
                s = await self._run_job(pages_dir, max_pages=10, max_depth=5)
                self.assertEqual(s.status()["pages_done"], 1)
                self.assertFalse(s.status()["last_error"])


class TestCollections(unittest.TestCase):
    def test_list_and_delete_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pages_dir = Path(tmp) / "pages"
            coll = pages_dir / "hulp"
            coll.mkdir(parents=True)
            (coll / "index.mu").write_text("hi", encoding="utf-8")
            (coll / "_meta.json").write_text(
                json.dumps({
                    "name": "hulp", "source_url": "https://wiki.example.org/p1",
                    "scraped_at": "2026-01-01", "pages": [{"slug": "index"}],
                }),
                encoding="utf-8",
            )
            cols = scraper.list_collections(pages_dir)
            self.assertEqual(len(cols), 1)
            self.assertEqual(cols[0]["name"], "hulp")
            self.assertEqual(cols[0]["page_count"], 1)

            scraper.delete_collection(pages_dir, "hulp")
            self.assertFalse(coll.exists())

    def test_folder_without_meta_json_is_not_a_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pages_dir = Path(tmp) / "pages"
            (pages_dir / "files").mkdir(parents=True)  # the attachments dir
            (pages_dir / "stray").mkdir()  # no _meta.json
            self.assertEqual(scraper.list_collections(pages_dir), [])

    def test_delete_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pages_dir = Path(tmp) / "pages"
            with self.assertRaises(scraper.ScraperError):
                scraper.delete_collection(pages_dir, "../evil")


if __name__ == "__main__":
    unittest.main()
