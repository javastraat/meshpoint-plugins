"""LocalMesh Repeaters: keeps every status the localmesh plugin could open and shows the repeaters on a page of their own.

Listens only. Imports are deferred into register() so store.py and service.py are tested without FastAPI or Meshpoint.
"""

from __future__ import annotations


def register(reg) -> None:
    from . import routes
    from .service import RepeaterService
    from .store import StatusStore

    reg.add_router(routes.router)

    def build(context):
        return RepeaterService(StatusStore(context.pipeline.database), ws_manager=context.ws_manager)

    def wire(service, _context):
        routes.init_routes(service)

    reg.add_service("localmesh-repeaters", build, wire)
