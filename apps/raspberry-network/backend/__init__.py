from __future__ import annotations


def register(reg) -> None:
    from .routes import router
    reg.add_router(router)
