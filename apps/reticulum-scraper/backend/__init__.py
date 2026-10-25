"""Reticulum Scraper plugin -- entry point.

Nothing to register on the backend: this plugin only provides "hook" (a
"Scrape" tab injected into the Reticulum page), and talks entirely to the
*reticulum* plugin's own backend -- POST /api/reticulum/scrape/start,
/stop, GET /status, /collections, DELETE /collections/{name} (all in
plugins/apps/reticulum/backend/scrape_routes.py + scraper.py). Nothing
here needs a route, a service, or even Python at all -- register() still
has to exist and be callable, same as reticulum-call/hello-world-hook.
"""

from __future__ import annotations


def register(reg) -> None:
    pass
