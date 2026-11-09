"""Reticulum Repeaters plugin -- entry point.

A lifespan service (``RepeaterMonitor``) polls the ScotMesh repeaters you
added over NomadNet and keeps their latest replies plus a sample history in
meshpoint's database (plugin-owned ``rr_*`` tables); ``routes`` serves the
sidebar page. Polling goes through the reticulum plugin's NomadNet client,
hence ``requires = "reticulum"`` in plugin.toml. Imports are deferred into
``register()`` so ``state`` / ``store`` / ``monitor`` import without FastAPI
for their tests.
"""

from __future__ import annotations


def register(reg) -> None:
    from . import routes, state
    from .monitor import RepeaterMonitor
    from .store import RepeaterStore

    state.init(reg.config)
    reg.add_router(routes.router)

    def build(context):
        return RepeaterMonitor(RepeaterStore(context.pipeline.database), ws_manager=context.ws_manager)

    def wire(monitor, _context):
        routes.init_routes(monitor)

    reg.add_service("reticulum-repeaters", build, wire)
