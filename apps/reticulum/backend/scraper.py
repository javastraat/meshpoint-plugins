"""Scrape a website into a hosted node's ``.mu`` pages.

Backs the ``reticulum-scraper`` plugin's "Scrape" tab (a hook into this
plugin's own Reticulum page) -- see ``scrape_routes.py`` for the HTTP
surface. One job at a time, same shape as ``offline-map``'s own
``OfflineMapProcess`` (module-level lock, bounded log deque, start/stop/
status) except this is a plain ``asyncio.Task`` doing stdlib HTTP fetches
rather than a subprocess.

A crawl writes a *collection*: a subfolder of ``node_pages_dir`` (one level
deep, see the matching change in ``nomad_node.py``) holding an ``index.mu``
for the seed URL plus one ``.mu`` per other page visited, and a ``_meta.json``
sidecar (ignored by ``nomad_node.py``'s own ``*.mu`` glob) recording what was
scraped, for the "existing collections" list.

Deliberately simple:
  * same-host crawl only, BFS, bounded by ``max_pages``/``max_depth``.
  * a MediaWiki-flavoured skip-list for junk links (no-op elsewhere).
  * no ``robots.txt`` handling -- this is a single-operator tool against a
    site the operator already chose to mirror, not a crawler aimed at
    arbitrary third parties. A small delay between fetches is still kept as
    basic politeness.
  * no image/attachment downloading -- ``html_to_micron`` emits a text
    placeholder instead (see its own docstring).

FastAPI/RNS-free; stdlib only (``urllib``, ``html.parser``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urldefrag, urljoin, urlsplit

from . import html_to_micron, node_pages

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10
_MAX_PAGE_BYTES = 2 * 1024 * 1024  # a wiki article page, generously capped
_FETCH_DELAY_S = 0.7  # basic politeness between requests to the same host
_MAX_LOG_LINES = 200

DEFAULT_MAX_PAGES = 40
DEFAULT_MAX_DEPTH = 3
HARD_MAX_PAGES = 200
HARD_MAX_DEPTH = 6

# MediaWiki (and skins in general) link patterns that are never article
# content -- meta/talk/admin pages, action links, revision diffs. Matched
# as a plain substring against the link's path+query; a non-MediaWiki site
# simply never matches any of these.
_SKIP_LINK_SUBSTRINGS = (
    "Special:", "Talk:", "User:", "User_talk:", "File:", "Category:",
    "Help:", "Template:", "action=", "oldid=", "diff=", "printable=",
    "Main_Page", "/w/index.php",
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ScraperError(node_pages.PageError):
    """A bad collection name, or a start request while one is already running."""


def validate_collection_name(name: str) -> str:
    """Thin wrapper -- node_pages.py owns the actual rule now (shared with
    the Pages tab's own collection paths); re-raised as ScraperError so
    every call site in this module can catch one consistent exception
    type, same as before the rule moved."""
    try:
        return node_pages.validate_collection_slug(name)
    except node_pages.PageError as exc:
        raise ScraperError(str(exc)) from exc


def _resolved_collection(pages_dir: str | Path, name: str) -> Path:
    base = Path(pages_dir).resolve()
    target = (base / validate_collection_name(name)).resolve()
    if target.parent != base:
        raise ScraperError("Collection name escapes the pages directory.")
    return target


def slugify(text: str, fallback: str) -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return (slug or fallback)[:60]


class _DiscoveryParser(HTMLParser):
    """First pass over a fetched page: just the ``<title>`` and every
    ``<a href>`` on the page (resolved to absolute). Deliberately not
    content-aware like ``html_to_micron``'s parser -- junk links (nav,
    sidebar) are filtered afterwards by ``_is_crawlable``, and finding them
    here too would just mean doing the filtering twice."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self.title = ""
        self.links: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(urljoin(self._base_url, href))

    def handle_endtag(self, tag) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data) -> None:
        if self._in_title:
            self.title += data


def _normalize(url: str) -> str:
    """Drop the fragment -- '#section' links are the same page for crawl
    purposes."""
    return urldefrag(url)[0]


def _is_crawlable(url: str, host: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.netloc != host:
        return False
    tail = parts.path + ("?" + parts.query if parts.query else "")
    return not any(s in tail for s in _SKIP_LINK_SUBSTRINGS)


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Meshpoint-NomadNet-scraper"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        raw = resp.read(_MAX_PAGE_BYTES + 1)
    if len(raw) > _MAX_PAGE_BYTES:
        raise ValueError(f"page exceeds {_MAX_PAGE_BYTES} bytes, refusing")
    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


@dataclass
class _Page:
    url: str
    title: str
    html: str
    slug: str  # "index" for the seed page, else a slugified title/path


@dataclass
class _JobState:
    source_url: str = ""
    name: str = ""
    max_pages: int = DEFAULT_MAX_PAGES
    max_depth: int = DEFAULT_MAX_DEPTH
    running: bool = False
    pages_done: int = 0
    pages_queued: int = 0
    current_url: str = ""
    last_error: str = ""
    log_lines: deque = field(default_factory=lambda: deque(maxlen=_MAX_LOG_LINES))
    task: Optional[asyncio.Task] = None
    stop_requested: bool = False


class Scraper:
    """Owns at most one scrape job. ``pages_dir_provider`` and
    ``reload_callback`` are late-bound (same pattern as ``nomad_routes.py``'s
    own module-level ``_service``) since this is constructed before the
    reticulum service exists."""

    def __init__(
        self,
        pages_dir_provider: Callable[[], str],
        reload_callback: Callable[[], bool],
    ):
        self._pages_dir_provider = pages_dir_provider
        self._reload_callback = reload_callback
        self._job = _JobState()
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._job.running

    def reload_node(self) -> bool:
        """Ask the running node to re-register its ``.mu`` handlers --
        exposed so route handlers (e.g. after a delete) don't need to reach
        into the private ``_reload_callback``."""
        return bool(self._reload_callback())

    def status(self) -> dict:
        j = self._job
        return {
            "running": j.running,
            "source_url": j.source_url,
            "name": j.name,
            "max_pages": j.max_pages,
            "max_depth": j.max_depth,
            "pages_done": j.pages_done,
            "pages_queued": j.pages_queued,
            "current_url": j.current_url,
            "last_error": j.last_error,
            "log_lines": list(j.log_lines),
        }

    async def start(
        self, source_url: str, name: str,
        max_pages: Optional[int] = None, max_depth: Optional[int] = None,
    ) -> dict:
        async with self._lock:
            if self._job.running:
                raise ScraperError("A scrape is already running -- stop it first.")
            validate_collection_name(name)
            parts = urlsplit(source_url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ScraperError("Source URL must be a full http(s):// URL.")

            # NOT "max_depth or DEFAULT" -- 0 is a legitimate, falsy value
            # ("just the seed page, don't follow any links") that `or`
            # would silently replace with the default.
            effective_pages = max_pages if max_pages is not None else DEFAULT_MAX_PAGES
            effective_depth = max_depth if max_depth is not None else DEFAULT_MAX_DEPTH
            job = _JobState(
                source_url=source_url,
                name=name,
                max_pages=min(HARD_MAX_PAGES, max(1, int(effective_pages))),
                max_depth=min(HARD_MAX_DEPTH, max(0, int(effective_depth))),
                running=True,
            )
            self._job = job
            job.task = asyncio.get_running_loop().create_task(self._run(job))
        return self.status()

    async def stop(self) -> dict:
        job = self._job
        job.stop_requested = True
        return self.status()

    async def _run(self, job: _JobState) -> None:
        try:
            await self._crawl(job)
        except Exception as exc:  # noqa: BLE001 -- a job must never crash the loop
            job.last_error = str(exc)
            logger.exception("Scrape job failed")
        finally:
            job.running = False
            self._reload_callback()

    def _log(self, job: _JobState, line: str) -> None:
        job.log_lines.append(line)
        logger.info("scrape[%s]: %s", job.name, line)

    async def _crawl(self, job: _JobState) -> None:
        host = urlsplit(job.source_url).netloc
        seed = _normalize(job.source_url)
        queue: deque[tuple[str, int]] = deque([(seed, 0)])
        visited: set[str] = {seed}
        pages: list[_Page] = []
        used_slugs = {"index"}

        while queue and len(pages) < job.max_pages:
            if job.stop_requested:
                self._log(job, "Stopped by request.")
                break
            url, depth = queue.popleft()
            job.current_url = url
            job.pages_queued = len(queue)

            try:
                html = await asyncio.to_thread(_fetch, url)
            except (urllib.error.URLError, ValueError, OSError) as exc:
                self._log(job, f"Fetch failed, skipping: {url} ({exc})")
                continue

            discovery = _DiscoveryParser(url)
            discovery.feed(html)
            discovery.close()
            title = discovery.title.strip() or url

            if not pages:
                slug = "index"
            else:
                slug = slugify(title, fallback=f"page-{len(pages) + 1}")
                base_slug, n = slug, 2
                while slug in used_slugs:
                    slug = f"{base_slug}-{n}"
                    n += 1
            used_slugs.add(slug)
            pages.append(_Page(url=url, title=title, html=html, slug=slug))
            job.pages_done = len(pages)
            self._log(job, f"[{len(pages)}/{job.max_pages}] {title} -> {slug}.mu")

            if depth < job.max_depth:
                for link in discovery.links:
                    link = _normalize(link)
                    if link in visited or not _is_crawlable(link, host):
                        continue
                    visited.add(link)
                    queue.append((link, depth + 1))

            await asyncio.sleep(_FETCH_DELAY_S)

        if not pages:
            job.last_error = "Nothing was scraped -- check the URL and try again."
            return

        self._write_collection(job, pages)
        self._log(job, f"Done: {len(pages)} page(s) written to {job.name}/")

    def _write_collection(self, job: _JobState, pages: list[_Page]) -> None:
        collection_dir = _resolved_collection(self._pages_dir_provider(), job.name)
        collection_dir.mkdir(parents=True, exist_ok=True)
        scraped_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

        url_to_slug = {p.url: f"{job.name}/{p.slug}" for p in pages}

        def resolver(href: str) -> Optional[str]:
            target = url_to_slug.get(_normalize(href))
            return f":/page/{target}.mu" if target else None

        # Every page -- the seed included -- gets its own real content.
        # The seed (-> index.mu) additionally gets a "Pages" section
        # linking to whatever else the crawl captured, since it's also the
        # collection's natural landing page; past commits rendered it as a
        # separate table-of-contents stub instead that discarded the
        # seed's own article text entirely -- a one-page crawl (the common
        # "just mirror this one article" case) produced an empty page.
        other_pages = [(p.title, f"{job.name}/{p.slug}") for p in pages if p.slug != "index"]
        for page in pages:
            extra_links = other_pages if page.slug == "index" else None
            content = html_to_micron.convert(
                page.html, page.title, page.url, resolver, extra_links,
            )
            (collection_dir / f"{page.slug}.mu").write_text(content, encoding="utf-8")

        meta = {
            "source_url": job.source_url,
            "name": job.name,
            "scraped_at": scraped_at,
            "pages": [{"slug": p.slug, "title": p.title, "url": p.url} for p in pages],
        }
        (collection_dir / "_meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8",
        )


def list_collections(pages_dir: str | Path) -> list[dict]:
    """Every scraped collection -- subfolders of ``pages_dir`` carrying a
    ``_meta.json`` sidecar (written by ``_write_collection`` above)."""
    base = Path(pages_dir)
    if not base.is_dir():
        return []
    out = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name in node_pages.RESERVED_COLLECTIONS:
            continue
        meta_path = entry / "_meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({
            "name": meta.get("name") or entry.name,
            "source_url": meta.get("source_url") or "",
            "scraped_at": meta.get("scraped_at") or "",
            "page_count": len(meta.get("pages") or []),
        })
    return out


def delete_collection(pages_dir: str | Path, name: str) -> None:
    target = _resolved_collection(pages_dir, name)
    if target.is_dir():
        shutil.rmtree(target)
