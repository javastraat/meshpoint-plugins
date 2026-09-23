"""bluetooth-scanner plugin entry point."""

from __future__ import annotations


def register(reg) -> None:
    from .listener import BluetoothScannerListener
    from .routes import init_routes, router

    reg.add_router(router)
    reg.add_listener("bluetooth-scanner", BluetoothScannerListener, init_routes)
