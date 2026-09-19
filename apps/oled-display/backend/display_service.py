"""oled-display plugin -- the lifespan-managed background service.

Drives a small I2C status OLED: a boot logo for a few seconds, then a
periodically-refreshed status screen (device name, LAN IP, which capture
sources are actually running, uptime), auto-blanking after a configurable
timeout to avoid burn-in.

Kept free of heavy imports at module load time (luma/PIL only imported
inside start(), same reasoning as Reticulum's rns/lxmf) so the plugin
loads even before setup.sh has installed its deps -- the loader's
``[deps] check`` probe is what surfaces "setup needed", not an import
crash at boot.
"""

from __future__ import annotations

import asyncio
import io
import logging
import socket
import time
from datetime import datetime, timezone

logger = logging.getLogger("oled_display")

_BOOT_LOGO_SECONDS = 3.0


def _lan_ip() -> str | None:
    """Best-effort local IP -- opens a UDP socket to a public address
    (no packet actually sent, UDP connect() just picks a route) purely
    to ask the OS which local interface/IP it would use. Self-contained
    on purpose: no dependency on core's own _local_ip_addresses() (an
    underscore-prefixed internal in src/tls_cert.py), since a plugin
    should stay decoupled from core internals it isn't handed via
    context."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def _fmt_uptime(seconds: int) -> str:
    d, rem = divmod(max(0, seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


class DisplayService:
    """``context`` is a ``src.api.service_registry.ServiceContext``:
    ``.pipeline`` (``.capture_coordinator.sources`` for live source
    names), ``.ws_manager``, ``.config`` (the full AppConfig, for
    ``device.device_name``/``dashboard.port``)."""

    def __init__(self, cfg: dict, context) -> None:
        self._cfg = cfg
        self._context = context
        self._device = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._start_time = time.monotonic()
        self._last_frame_png: bytes | None = None
        self._last_rendered_at: datetime | None = None
        self._blanked = False

    # -- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        if not self._cfg.get("enabled", True):
            logger.info("oled-display disabled (plugins.oled-display.enabled: false)")
            return

        try:
            self._device = self._open_device()
        except Exception:  # noqa: BLE001 -- a missing/dead panel must not crash the service
            logger.warning("oled-display: could not open display, staying dark", exc_info=True)
            return

        await self._show_boot_logo()
        self._stop_event.clear()
        self._task = asyncio.ensure_future(self._loop())
        logger.info(
            "oled-display started (%s @ %s, %sx%s)",
            self._cfg["driver"], self._cfg["i2c_address"],
            self._cfg["width"], self._cfg["height"],
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._device is not None:
            try:
                self._device.clear()
            except Exception:  # noqa: BLE001
                pass
        logger.info("oled-display stopped")

    # -- rendering -----------------------------------------------------

    def _open_device(self):
        from luma.core.interface.serial import i2c
        from luma.oled.device import sh1106, ssd1306, ssd1309

        drivers = {"ssd1306": ssd1306, "sh1106": sh1106, "ssd1309": ssd1309}
        device_cls = drivers[self._cfg["driver"]]
        serial = i2c(port=1, address=int(self._cfg["i2c_address"], 16))
        return device_cls(serial, width=self._cfg["width"], height=self._cfg["height"])

    async def _show_boot_logo(self) -> None:
        from luma.core.render import canvas
        from PIL import ImageFont

        font = ImageFont.load_default()
        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            draw.text((8, 12), "MESHPOINT", font=font, fill="white")
            draw.text((8, 30), "starting...", font=font, fill="white")
        self._capture_frame(cv.image)
        await asyncio.sleep(_BOOT_LOGO_SECONDS)

    async def _loop(self) -> None:
        refresh_s = max(1, int(self._cfg.get("refresh_seconds", 5)))
        blank_after_s = int(self._cfg.get("blank_after_minutes", 0)) * 60

        while not self._stop_event.is_set():
            elapsed = time.monotonic() - self._start_time
            if blank_after_s and elapsed >= blank_after_s:
                if not self._blanked:
                    self._blank()
            else:
                self._draw_status()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=refresh_s)
            except asyncio.TimeoutError:
                pass

    def _draw_status(self) -> None:
        from luma.core.render import canvas
        from PIL import ImageFont

        font = ImageFont.load_default()
        ip = _lan_ip() or "no network"
        port = getattr(getattr(self._context.config, "dashboard", None), "port", 8080)
        sources = self._active_sources()
        uptime = _fmt_uptime(int(time.monotonic() - self._start_time))

        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            draw.text((2, 0), f"{ip}:{port}", font=font, fill="white")
            draw.line((0, 12, self._cfg["width"], 12), fill="white")
            draw.text((2, 16), sources or "no sources", font=font, fill="white")
            draw.text((2, 28), f"up {uptime}", font=font, fill="white")
        self._capture_frame(cv.image)
        self._blanked = False

    def _blank(self) -> None:
        from luma.core.render import canvas

        cv = canvas(self._device)
        with cv as draw:
            pass  # leave black -- burn-in protection
        self._capture_frame(cv.image)
        self._blanked = True

    def _active_sources(self) -> str:
        """Live capture source names, e.g. 'concentrator, meshcore-1' --
        best-effort: pipeline shape can vary, a plugin never crashes the
        display loop over it."""
        try:
            sources = self._context.pipeline.capture_coordinator.sources
            names = [getattr(s, "name", "?") for s in sources]
            return ", ".join(names) if names else "no sources"
        except Exception:  # noqa: BLE001
            return "status unknown"

    def _capture_frame(self, image) -> None:
        """Mirror whatever was just drawn into a PNG the settings page's
        live-preview <img> can fetch. The rendered image lives on the
        `canvas` instance itself (`cv.image`, set in its __init__ and
        pushed to hardware via device.display() on __exit__) -- NOT on
        the device object, which has no public `.image` attribute at
        all (confirmed against luma.core.device.device's real API,
        checked in a local venv here rather than guessed a second time
        after the first version silently failed via the broad except
        below -- caller must pass the canvas's own image in)."""
        try:
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="PNG")
            self._last_frame_png = buf.getvalue()
            self._last_rendered_at = datetime.now(timezone.utc)
        except Exception:  # noqa: BLE001 -- preview is a nice-to-have, never fatal
            logger.warning("oled-display: frame capture failed", exc_info=True)

    # -- exposed to routes.py -----------------------------------------

    @property
    def last_frame_png(self) -> bytes | None:
        return self._last_frame_png

    @property
    def last_rendered_at(self) -> datetime | None:
        return self._last_rendered_at

    @property
    def is_open(self) -> bool:
        return self._device is not None
