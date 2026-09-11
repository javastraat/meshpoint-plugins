"""offline-map plugin -- entry point.

Meshpoint's plugin loader imports this module and calls ``register(reg)``
when ``plugins.offline-map.enabled: true`` is set in config. See
``plugin.toml`` and ``docs/CONFIGURATION.md`` (Plugins).

Imports are deferred into ``register()`` so ``backend.state``/``backend.process``
(no FastAPI dependency) can be imported for their own tests without
pulling in FastAPI, same convention as the ACARS plugin.
"""

from __future__ import annotations


def register(reg) -> None:
    from . import state
    from .process import OfflineMapProcess
    from .routes import init_routes, router

    state.init(reg.config)
    reg.add_router(router)
    init_routes(OfflineMapProcess())
