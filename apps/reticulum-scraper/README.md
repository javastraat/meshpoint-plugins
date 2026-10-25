# Reticulum Scraper plugin

Scrapes a website into Micron (`.mu`) pages a hosted Reticulum node can
serve with **zero internet** — the driving use case is an offline copy of
a reference site (e.g. a first-aid wiki) reachable over LoRa when there's
no connectivity left. Adds a **Scrape** tab to the Reticulum page (hooked
in via `window.registerPageHook`, not a sidebar page of its own, same
pattern as Reticulum Call) — give it a source URL and a short name, it
crawls same-host links, converts each page, and writes the result under
`node_pages_dir/<name>/`.

## What's here

- A form: **Source URL** + **Name** (the collection's folder, e.g. `hulp`
  → `node_pages_dir/hulp/`), optional **Max pages** / **Max link depth**
  overrides.
- The seed URL always becomes that collection's `index.mu`; every other
  page visited gets its own `.mu` file, cross-linked to each other where
  the crawl captured both ends — otherwise kept as a plain external link.
- A **Scraped collections** list (name, page count, source, scraped-at)
  with a Delete button per collection.
- Live progress while a crawl runs (pages done/queued, current URL, a log
  tail), polled from the same job the Stop button targets.

All of the above lives in the `reticulum` plugin itself
(`backend/scraper.py`, `backend/html_to_micron.py`, `backend/scrape_routes.py`,
and the subfolder-serving addition in `backend/nomad_node.py`) — this
plugin is the tab UI only, same reason Reticulum Call's own backend does
nothing (`backend/__init__.py`): the crawl needs that plugin's
`node_pages_dir` config and its live `LxmfService.reload_node_pages()`
directly, not through a second cross-plugin seam.

## What's not here (yet)

- **No image/attachment downloading.** `html_to_micron.py` drops `<img>`
  tags and leaves a `[image: <alt text>]` placeholder line instead —
  real fidelity gap, deliberately deferred.
- **No `robots.txt` handling.** This is a single-operator tool against a
  site you've already chosen to mirror, not a crawler aimed at arbitrary
  third parties — a small delay between requests is kept as basic
  politeness regardless.
- **One crawl at a time.** Starting a second while one's running is
  refused (409) — stop the first.
- A page is converted with a simplified HTML→Micron pass (headings,
  paragraphs, links, lists, bold/italic) — tables, forms, and embeds
  degrade to plain text or are dropped. Good enough for an article-shaped
  wiki page, not a general HTML renderer.

## Enable it

```yaml
plugins:
  reticulum:
    enabled: true
    node_enabled: true    # Settings tab -> "Host a NomadNet node"
  reticulum-scraper:
    enabled: true
```

Restart, open the Reticulum page's **Scrape** tab. `node_enabled` only
matters for actually *serving* the result over Reticulum — scraping and
saving works either way, the pages just won't be reachable from another
node until a node is hosting.

## Layout

```
plugin.toml                              manifest ([hook] table, host = "reticulum")
backend/__init__.py                       register(reg) -- a no-op; every route this plugin uses lives in the reticulum plugin itself
frontend/reticulum_scraper_tab.js         the Scrape tab (form, collections list, progress/log)
frontend/reticulum_scraper_tab.css        layout for the above
```
