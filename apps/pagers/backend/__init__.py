"""Pagers plugin -- entry point.

Meshpoint's plugin loader imports this module and calls ``register(reg)``
when ``plugins.pagers.enabled: true`` is set in config. See
``plugins/apps/pagers/plugin.toml`` and ``docs/CONFIGURATION.md`` (Plugins).

Imports are deferred into ``register()`` so ``backend.listener`` (stdlib
only) can be imported for its own tests without pulling in FastAPI.
"""

from __future__ import annotations


def register(reg) -> None:
    from .listener import PagersListener
    from .routes import init_routes, router

    reg.add_router(router)
    keep_running = bool(reg.config.get("keep_running", False))
    reg.add_listener("pagers", lambda: PagersListener(keep_running=keep_running), init_routes)
