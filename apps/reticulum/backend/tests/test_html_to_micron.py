"""Tests for backend/html_to_micron.py -- the HTML -> Micron converter.

Pure function, no FastAPI, no RNS, no network -- feeds small HTML fixtures
straight in.
"""

from __future__ import annotations

import unittest

from plugins.apps.reticulum.backend import html_to_micron


class TestConvertBasics(unittest.TestCase):
    def test_heading_and_paragraph(self) -> None:
        out = html_to_micron.convert(
            "<h1>Title</h1><p>Hello world.</p>",
            "Page", "https://example.org/a", "2026-01-01",
        )
        self.assertIn(">Title", out)
        self.assertIn("Hello world.", out)

    def test_bold_and_italic_markers(self) -> None:
        out = html_to_micron.convert(
            "<p>a <b>bold</b> and <i>italic</i> word.</p>",
            "Page", "https://example.org/a", "2026-01-01",
        )
        self.assertIn("`!bold`!", out)
        self.assertIn("`*italic`*", out)

    def test_list_items_bulleted_and_numbered(self) -> None:
        out = html_to_micron.convert(
            "<ul><li>one</li><li>two</li></ul>"
            "<ol><li>first</li><li>second</li></ol>",
            "Page", "https://example.org/a", "2026-01-01",
        )
        self.assertIn("• one", out)
        self.assertIn("• two", out)
        self.assertIn("1. first", out)
        self.assertIn("2. second", out)

    def test_external_link_kept_as_is(self) -> None:
        out = html_to_micron.convert(
            '<p><a href="https://other.org/x">Other</a></p>',
            "Page", "https://example.org/a", "2026-01-01",
        )
        self.assertIn("`[Other`https://other.org/x]", out)

    def test_internal_link_resolved_via_resolver(self) -> None:
        # The resolver hands back the already-formatted Micron target
        # (same contract scraper.py's own resolver uses) -- convert() just
        # drops it in, it doesn't reformat it.
        out = html_to_micron.convert(
            '<p><a href="https://example.org/b">Page B</a></p>',
            "Page", "https://example.org/a", "2026-01-01",
            link_resolver=lambda url: ":/page/hulp/page-b.mu" if url.endswith("/b") else None,
        )
        self.assertIn("`[Page B`:/page/hulp/page-b.mu]", out)

    def test_image_becomes_placeholder_not_a_leaked_src(self) -> None:
        out = html_to_micron.convert(
            '<p><img src="https://example.org/photo.png" alt="A photo"></p>',
            "Page", "https://example.org/a", "2026-01-01",
        )
        self.assertIn("[image: A photo]", out)
        self.assertNotIn("photo.png", out)

    def test_backtick_in_text_is_escaped(self) -> None:
        out = html_to_micron.convert(
            "<p>use the `backtick` char</p>",
            "Page", "https://example.org/a", "2026-01-01",
        )
        self.assertIn("\\`backtick\\`", out)

    def test_script_and_nav_subtrees_are_dropped(self) -> None:
        out = html_to_micron.convert(
            "<nav>Menu</nav><script>evil()</script><p>Real content.</p>",
            "Page", "https://example.org/a", "2026-01-01",
        )
        self.assertNotIn("Menu", out)
        self.assertNotIn("evil()", out)
        self.assertIn("Real content.", out)

    def test_footer_has_source_and_scraped_date(self) -> None:
        out = html_to_micron.convert(
            "<p>x</p>", "Page", "https://example.org/a", "2026-01-01 00:00 UTC",
        )
        self.assertIn("Source: https://example.org/a", out)
        self.assertIn("Scraped: 2026-01-01 00:00 UTC", out)


class TestContentNarrowing(unittest.TestCase):
    """MediaWiki (and similarly-skinned) pages wrap the real article in a
    known container id -- chrome outside it should never show up."""

    def test_only_mw_content_text_subtree_is_kept(self) -> None:
        html = (
            '<div id="mw-panel"><a href="/wiki/Main_Page">Sidebar link</a></div>'
            '<div id="mw-content-text"><p>Actual article text.</p></div>'
            '<div id="footer">Footer junk</div>'
        )
        out = html_to_micron.convert(html, "Page", "https://example.org/a", "2026-01-01")
        self.assertIn("Actual article text.", out)
        self.assertNotIn("Sidebar link", out)
        self.assertNotIn("Footer junk", out)

    def test_nested_divs_inside_content_container_are_kept(self) -> None:
        html = (
            '<div id="mw-content-text">'
            '<div class="mw-parser-output"><p>Nested paragraph.</p></div>'
            "</div>"
        )
        out = html_to_micron.convert(html, "Page", "https://example.org/a", "2026-01-01")
        self.assertIn("Nested paragraph.", out)

    def test_falls_back_to_whole_document_when_no_known_container(self) -> None:
        html = "<body><p>Plain page, no special container.</p></body>"
        out = html_to_micron.convert(html, "Page", "https://example.org/a", "2026-01-01")
        self.assertIn("Plain page, no special container.", out)


class TestIndexPage(unittest.TestCase):
    def test_lists_every_page_as_a_link(self) -> None:
        out = html_to_micron.index_page(
            "Wiki Home", "https://example.org/", "2026-01-01",
            [("Page B", "hulp/page-b"), ("Page C", "hulp/page-c")],
        )
        self.assertIn("`[Page B`:/page/hulp/page-b.mu]", out)
        self.assertIn("`[Page C`:/page/hulp/page-c.mu]", out)
        self.assertIn("Wiki Home", out)


if __name__ == "__main__":
    unittest.main()
