"""P2000 plugin -- entry point.

Meshpoint's plugin loader imports this module and calls ``register(reg)``
when ``plugins.p2000.enabled: true`` is set in config. See
``plugins/apps/p2000/plugin.toml`` and ``docs/CONFIGURATION.md`` (Plugins).

Imports are deferred into ``register()`` so ``backend.listener`` (stdlib
only) can be imported for its own tests without pulling in FastAPI.
"""

from __future__ import annotations


def register(reg) -> None:
    from .listener import P2000Listener
    from .routes import init_routes, router

    reg.add_router(router)
    keep_running = bool(reg.config.get("keep_running", False))
    reg.add_listener("p2000", lambda: P2000Listener(keep_running=keep_running), init_routes)
