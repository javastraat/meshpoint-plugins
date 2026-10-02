from __future__ import annotations


def register(reg) -> None:
    from .listener import DabListener
    from .routes import init_routes, router

    reg.add_router(router)
    keep_running = bool(reg.config.get("keep_running", False))
    reg.add_listener("dab", lambda: DabListener(keep_running=keep_running), init_routes)
