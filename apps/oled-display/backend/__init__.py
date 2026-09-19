"""oled-display plugin -- entry point.

A lifespan-managed background service (same seam as Reticulum's
LxmfService / hello-service's minimal example), registered through
``reg.add_service()``. ``build()`` constructs the DisplayService against
the live ServiceContext (``.pipeline.capture_coordinator.sources`` for
real-time capture-source names, ``.config`` for the dashboard port);
``wire()`` binds routes.py to the running instance once it exists.

Imports are deferred into register()/build() so backend.state can be
imported standalone for tests without pulling in FastAPI or luma.
"""

from __future__ import annotations


def register(reg) -> None:
    from . import routes, state
    from .display_service import DisplayService

    state.init(reg.config)
    reg.add_router(routes.router)

    def build(context):
        return DisplayService(state.to_dict(), context)

    def wire(service, context):
        routes.init_routes(service)

    reg.add_service("oled-display", build, wire)
