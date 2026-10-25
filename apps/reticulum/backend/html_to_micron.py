"""Convert simple article HTML into Micron (``.mu``) markup.

Backs the scraper (``scraper.py``) -- fetches a page, hands the raw HTML
here, gets back Micron text ready to write straight into a hosted node's
pages directory. Deliberately lossy: this targets plain wiki-article-shaped
content (headings, paragraphs, links, lists, bold/italic), not a general
HTML renderer. Anything else (tables, forms, embeds) degrades to plain text
or is dropped -- see the per-tag notes below.

FastAPI/RNS-free, stdlib only (``html.parser``), same philosophy as
``node_pages.py``/``ical.py``/``spaceapi.py``.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

# Tags whose entire subtree is dropped -- chrome, not article content.
_SKIP_SUBTREE = frozenset({"script", "style", "nav", "header", "footer"})

# MediaWiki-core markers for boilerplate that's nested *inside* the real
# content container (narrowing by the container's own id alone can't catch
# these -- they're genuine descendants of #mw-content-text, not a separate
# sibling). Checked against both an element's class and its id, since
# MediaWiki core uses each for one of these: "printfooter" (the
# "Retrieved from "..."" line) is a class; "catlinks" (the category-links
# box) is an id.
_SKIP_CLASSES = frozenset({"printfooter", "catlinks"})

# Block-level tags that force a paragraph break in the plain-text output.
_BLOCK_TAGS = frozenset({
    "p", "div", "tr", "table", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "br",
})

# Known "this is the actual article" container ids -- MediaWiki (the driving
# example) and a couple of common skins, narrowest first. When one shows up,
# we narrow to just that element's subtree and discard everything before/
# after it (site nav, sidebars, footers) instead of relying on tag-based
# skipping alone, which doesn't catch a skin's <div id="mw-panel"> sidebar.
# Order matters: real MediaWiki nests #content > #bodyContent >
# #mw-content-text, and the outer two also contain sibling chrome right
# next to the real content (the FlaggedRevs review notice, a "jump to
# navigation" skip link, the category-links box) -- narrowing to whichever
# is *first in document order* would pick the broadest, chrome-including
# wrapper every time. convert() picks the narrowest one actually present
# in the document (see _pick_content_id) rather than the first one seen.
_CONTENT_ID_PRIORITY = ("mw-content-text", "bodyContent", "content")

_HEADING_PREFIX = {"h1": ">", "h2": ">>", "h3": ">>>", "h4": ">>>", "h5": ">>>", "h6": ">>>"}


def _esc(text: str) -> str:
    """Micron has no escaping need for plain text except the backtick --
    same two-replace rule ``nomad_node.py``'s own ``_esc()`` uses."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


class _ArticleParser(HTMLParser):
    """Walks the HTML, emitting a flat list of Micron-ready lines.

    ``link_resolver(url) -> str | None`` maps an absolute URL to a Micron
    internal-link target (``:/page/<collection>/<slug>.mu``) when that URL
    was captured in the same crawl, or ``None`` to keep it as an external
    link. Every ``href`` is resolved against ``base_url`` before being
    passed to it or kept as a fallback -- a scraped page's links are almost
    always page-relative (``/wiki/Burns``), and the resolver's lookup table
    (and any kept external link) needs an absolute URL to match against.
    """

    def __init__(self, link_resolver, base_url: str = "", content_ids: frozenset[str] = frozenset()):
        super().__init__(convert_charrefs=True)
        self._link_resolver = link_resolver
        self._base_url = base_url
        self._content_ids = content_ids
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._skip_depth = 0
        self._list_stack: list[str] = []  # "ul" / "ol" per nesting level
        self._list_counters: list[int] = []
        self._bold = 0
        self._italic = 0
        self._heading: str | None = None
        self._link_href: str | None = None
        self._link_text: list[str] = []
        # Content-container narrowing -- see _CONTENT_ID_PRIORITY above.
        self._narrowed = False
        self._capture_tag: str | None = None
        self._capture_depth = 0
        self._past_container = False
        # Boilerplate nested *inside* the content container -- see
        # _SKIP_CLASSES. Single-slot (not a stack): printfooter/catlinks
        # aren't nested inside each other in practice, and a leading
        # metadata table (below) is the other thing this tracks.
        self._skip_class_tag: str | None = None
        self._skip_class_depth = 0
        # Has any real prose (a flushed paragraph or heading) been emitted
        # yet? MediaWiki articles commonly open the content container with
        # an editorial metadata table (author/reviewer box, accuracy
        # rating, "we need writers" banners) that has no distinguishing
        # class -- unlike printfooter/catlinks, it's site-template content,
        # not a MediaWiki-core marker. A <table> before any real text is
        # assumed to be exactly that and dropped; a table *after* real text
        # has started (a dosage table mid-article, say) is kept.
        self._seen_block_content = False

    # -- helpers -----------------------------------------------------------

    def _skipping(self) -> bool:
        return self._skip_depth > 0 or self._skip_class_tag is not None

    def _flush(self) -> None:
        text = "".join(self._buf).strip()
        self._buf.clear()
        if text:
            self.lines.append(text)
            self._seen_block_content = True
        self.lines.append("")  # paragraph break

    def _emit(self, text: str) -> None:
        if self._skipping():
            return
        if self._link_href is not None:
            self._link_text.append(text)
        else:
            self._buf.append(text)

    # -- HTMLParser overrides ------------------------------------------------

    def handle_starttag(self, tag, attrs) -> None:
        if self._past_container:
            return
        attrs_d = dict(attrs)
        if not self._narrowed and attrs_d.get("id") in self._content_ids:
            # First sight of the chosen article container -- discard
            # whatever we've captured so far (site chrome before it) and
            # start fresh, scoped to just this element's subtree.
            self._narrowed = True
            self.lines.clear()
            self._buf.clear()
            self._seen_block_content = False  # discard pre-narrowing chrome's own flag state
            self._capture_tag = tag
            self._capture_depth = 1
            return  # the container tag itself carries no content
        if self._narrowed and self._capture_tag is not None and tag == self._capture_tag:
            self._capture_depth += 1
        if tag in _SKIP_SUBTREE:
            self._skip_depth += 1
            return
        if self._skip_class_tag is not None:
            if tag == self._skip_class_tag:
                self._skip_class_depth += 1
            return
        if self._skip_depth:
            return
        classes = (attrs_d.get("class") or "").split()
        if (
            any(c in _SKIP_CLASSES for c in classes)
            or attrs_d.get("id") in _SKIP_CLASSES  # catlinks is an id, not a class
            or (tag == "table" and not self._seen_block_content)
        ):
            self._skip_class_tag = tag
            self._skip_class_depth = 1
            return
        if tag in _HEADING_PREFIX:
            if self._buf:
                self._flush()
            self._heading = _HEADING_PREFIX[tag]
        elif tag in ("b", "strong"):
            self._emit("`!")
            self._bold += 1
        elif tag in ("i", "em"):
            self._emit("`*")
            self._italic += 1
        elif tag == "a":
            href = attrs_d.get("href")
            self._link_href = href
            self._link_text = []
        elif tag == "img":
            alt = (attrs_d.get("alt") or attrs_d.get("title") or "image").strip()
            self._emit(f"`F888[image: {_esc(alt)}]`f")
        elif tag in ("ul", "ol"):
            if self._buf:
                self._flush()
            self._list_stack.append(tag)
            self._list_counters.append(0)
        elif tag == "li":
            if self._buf:
                self._flush()
            if self._list_stack and self._list_stack[-1] == "ol":
                self._list_counters[-1] += 1
                self._buf.append(f"{self._list_counters[-1]}. ")
            else:
                self._buf.append("• ")
        elif tag == "br":
            self._buf.append("\n")
        elif tag in _BLOCK_TAGS and self._buf:
            self._flush()

    def handle_endtag(self, tag) -> None:
        if self._past_container:
            return
        if self._narrowed and self._capture_tag is not None and tag == self._capture_tag:
            self._capture_depth -= 1
            if self._capture_depth <= 0:
                if self._buf:
                    self._flush()
                self._past_container = True
                return
        if tag in _SKIP_SUBTREE:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_class_tag is not None:
            if tag == self._skip_class_tag:
                self._skip_class_depth -= 1
                if self._skip_class_depth <= 0:
                    self._skip_class_tag = None
                    self._skip_class_depth = 0
            return
        if self._skip_depth:
            return
        if tag in _HEADING_PREFIX:
            text = "".join(self._buf).strip()
            self._buf.clear()
            if text:
                self.lines.append(f"{self._heading}{_esc(text)}")
                self.lines.append("")
                self._seen_block_content = True
            self._heading = None
        elif tag in ("b", "strong"):
            self._bold = max(0, self._bold - 1)
            self._emit("`!")
        elif tag in ("i", "em"):
            self._italic = max(0, self._italic - 1)
            self._emit("`*")
        elif tag == "a":
            label = "".join(self._link_text).strip()
            href = self._link_href
            self._link_href = None
            self._link_text = []
            if not label:
                return
            if not href:
                self._buf.append(_esc(label))
                return
            absolute = urljoin(self._base_url, href) if self._base_url else href
            target = self._link_resolver(absolute)
            url = target if target is not None else absolute
            self._buf.append(f"`[{_esc(label)}`{url}]")
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
                self._list_counters.pop()
            if self._buf:
                self._flush()
        elif tag in _BLOCK_TAGS and self._buf:
            self._flush()

    def handle_data(self, data) -> None:
        if self._past_container:
            return
        if self._heading is not None:
            if self._link_href is not None:
                self._link_text.append(data)
            else:
                self._buf.append(data)
            return
        text = " ".join(data.split())
        if not text:
            return
        if data[:1].isspace() and self._buf and not self._buf[-1].endswith((" ", "\n")):
            text = " " + text
        if data[-1:].isspace():
            text += " "
        self._emit(_esc(text) if self._link_href is None else text)

    def finish(self) -> list[str]:
        """Call after ``feed()``/``close()`` -- flushes any trailing
        buffered text and returns the collected lines."""
        if self._buf:
            self._flush()
        return self.lines


def _pick_content_id(html: str) -> frozenset[str]:
    """Which known container id to narrow to, if any -- the narrowest one
    actually present (``_CONTENT_ID_PRIORITY`` order), not simply the first
    one document order happens to hit. A cheap substring check is enough:
    worst case (the id string appears only in a comment or an unrelated
    attribute value) is the same graceful fallback as not finding it at
    all -- the real match, scanned for by the parser itself, still wins."""
    for content_id in _CONTENT_ID_PRIORITY:
        if f'id="{content_id}"' in html or f"id='{content_id}'" in html:
            return frozenset({content_id})
    return frozenset()


def convert(
    html: str,
    title: str,
    source_url: str,
    link_resolver=lambda url: None,
    extra_links: list[tuple[str, str]] | None = None,
) -> str:
    """Render one scraped page as a complete Micron document: a title
    header and the converted body -- no source-URL/scraped-date footer
    (deliberately left out of the page itself; that's what the Scrape
    tab's own "Scraped collections" list is for).

    ``source_url`` is the page's own URL -- needed to resolve a
    page-relative ``href`` (``/wiki/Burns``) to an absolute one before
    ``link_resolver(absolute_url) -> str | None`` gets to decide whether
    it's an internal link (see ``_ArticleParser``).

    ``extra_links``, when given a non-empty ``[(label, slug), ...]``,
    appends a ``>Pages`` section linking to each -- used for the seed
    page (``index.mu``) so it both reads as the real article *and* points
    at whatever else the crawl captured, instead of a separate stub page
    that discarded the seed's own content entirely.
    """
    parser = _ArticleParser(
        link_resolver, base_url=source_url, content_ids=_pick_content_id(html),
    )
    parser.feed(html)
    parser.close()

    body_lines = []
    prev_blank = True
    for line in parser.finish():
        if line == "":
            if not prev_blank:
                body_lines.append("")
            prev_blank = True
        else:
            body_lines.append(line)
            prev_blank = False
    while body_lines and body_lines[-1] == "":
        body_lines.pop()

    out = [
        "`F38f`!" + _esc(title or "Untitled") + "`!`f",
        "-",
        "",
        *body_lines,
    ]
    if extra_links:
        out += ["", ">Pages", ""]
        out += [f"`[{_esc(label)}`:/page/{slug}.mu]" for label, slug in extra_links]
    return "\n".join(out) + "\n"
