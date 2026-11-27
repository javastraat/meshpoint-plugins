from __future__ import annotations


def register(reg) -> None:
    from .listener import RtlListener
    from .routes import init_routes, router

    reg.add_router(router)
    # FM de-emphasis from the Meshpoint's region (75 us US/KR, else 50 us);
    # plugins.radio.deemphasis_us (50/75) overrides. Older cores without
    # reg.radio_region give "UNKNOWN", i.e. 50 us.
    region = getattr(reg, "radio_region", "UNKNOWN")
    override = reg.config.get("deemphasis_us")
    reg.add_listener("radio", lambda: RtlListener(region=region, deemphasis_us=override), init_routes)
