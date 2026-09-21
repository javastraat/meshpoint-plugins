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
from datetime import datetime, timedelta, timezone

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
        self._last_status_png: bytes | None = None
        self._last_status_rendered_at: datetime | None = None
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
        self._capture_frame(cv.image, is_status=False)
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
                await self._draw_status()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=refresh_s)
            except asyncio.TimeoutError:
                pass

    async def _draw_status(self) -> None:
        from luma.core.render import canvas
        from PIL import ImageFont

        font = ImageFont.load_default()
        ip = _lan_ip() or "no network"
        port = getattr(getattr(self._context.config, "dashboard", None), "port", 8080)
        capture_sources = await self._active_sources()
        reticulum = await self._reticulum_status()
        parts = [*capture_sources, reticulum] if reticulum else list(capture_sources)
        uptime = _fmt_uptime(int(time.monotonic() - self._start_time))

        # Two entries per row, not one cramped comma-joined line -- the
        # display has plenty of unused vertical space below the old
        # single sources line, and four protocols (LW/MT/MC/RT) with a
        # peer count each easily overflows 128px on one row.
        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            draw.text((2, 0), f"{ip}:{port}", font=font, fill="white")
            draw.line((0, 12, self._cfg["width"], 12), fill="white")

            row_h = 10
            col_x = (2, self._cfg["width"] // 2 + 2)
            y = 16
            if not parts:
                draw.text((2, y), "no sources", font=font, fill="white")
                y += row_h
            else:
                for i in range(0, len(parts), 2):
                    for col, item in enumerate(parts[i:i + 2]):
                        draw.text((col_x[col], y), item, font=font, fill="white")
                    y += row_h
            draw.text((2, y), f"up {uptime}", font=font, fill="white")
        self._capture_frame(cv.image, is_status=True)
        self._blanked = False

    def _blank(self) -> None:
        from luma.core.render import canvas

        cv = canvas(self._device)
        with cv as draw:
            pass  # leave black -- burn-in protection
        self._capture_frame(cv.image, is_status=False)
        self._blanked = True

    async def _active_sources(self) -> list[str]:
        """Live capture protocols with a peer count, e.g. ['LW (3p)', 'MT
        (12p)'] -- same LW/MT/MC/RT convention the Messages page's own
        protocol filter chips already use, not the raw source names
        (which don't map 1:1 to protocols: "concentrator" is the SX1302
        handling LoRaWAN AND Meshtastic simultaneously over the same
        dual-sync-word capture, by design -- every "concentrator" source
        is always both at once, never just one).

        Returns a list (not a joined string) so _draw_status() can lay
        entries out multiple-per-row instead of cramming everything onto
        one line -- the display has plenty of unused vertical space.

        Peer counts are "active in the last 24h", not an all-time total
        -- an ever-growing lifetime count is a lot less useful at a
        glance on a status panel than "how many are actually around
        right now". (This is a deliberate difference from Reticulum's
        own peer_count(), which IS an all-time known-peer count --
        matching RT's own semantics exactly wasn't as important as this
        number actually being useful here.)

        Best-effort throughout: pipeline shape can vary, a plugin never
        crashes the display loop over it."""
        try:
            sources = self._context.pipeline.capture_coordinator.sources
            names = [getattr(s, "name", "") for s in sources]
        except Exception:  # noqa: BLE001
            return []

        protocols: list[str] = []
        for name in names:
            if name.startswith("concentrator"):
                for p in ("LW", "MT"):
                    if p not in protocols:
                        protocols.append(p)
            elif name.startswith("sx1262_spi") or name.startswith("serial"):
                if "MT" not in protocols:
                    protocols.append("MT")
            elif name.startswith("meshcore_usb"):
                if "MC" not in protocols:
                    protocols.append("MC")
            elif name and name not in protocols:
                protocols.append(name)  # unrecognised source type -- show as-is, don't hide it

        parts = []
        for p in protocols:
            count = await self._protocol_peer_count(p)
            parts.append(f"{p} ({count}p)" if count is not None else p)
        return parts

    async def _protocol_peer_count(self, protocol_label: str) -> int | None:
        """Devices active in the last 24h for one LW/MT/MC label.

        LW is special-cased: LoRaWAN devices never get a `nodes` table
        row at all -- coordinator.py's _update_node() only bumps an
        existing packet counter for a LoRaWAN source, it never upserts
        one (LoRaWAN devices "have no Meshtastic node profile"; their
        own device panel, lorawan_routes.py's lorawan_devices(), builds
        its roster by aggregating the packets table directly instead).
        node_repo.get_active_count(protocol="lorawan") would therefore
        always report 0 -- wrong table entirely, not actually a "no
        devices" signal. This runs the same shape of query
        lorawan_devices() does, just scoped to the last 24h and counting
        distinct sources rather than listing them.

        MT/MC go through node_repo normally, which IS correct for them
        (both protocols self-announce and get a real nodes-table row)."""
        try:
            if protocol_label == "LW":
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                row = await self._context.pipeline.database.fetch_one(
                    "SELECT COUNT(DISTINCT source_id) AS cnt FROM packets "
                    "WHERE protocol = 'lorawan' AND source_id != '' AND timestamp >= ?",
                    (cutoff,),
                )
                return row["cnt"] if row else 0
            key = {"MT": "meshtastic", "MC": "meshcore"}.get(protocol_label)
            if key is None:
                return None
            return await self._context.pipeline.node_repo.get_active_count(
                hours=24, protocol=key,
            )
        except Exception:  # noqa: BLE001
            return None

    async def _reticulum_status(self) -> str:
        """Reticulum isn't a CaptureSource -- it's a `service` plugin
        (LxmfService), so it never appears in capture_coordinator.sources
        no matter what.

        First attempt at this went over a local HTTP call to
        /api/reticulum/status -- wrong, because that router (like every
        plugin router) is mounted `public=False`, so it 401s with no
        session cookie attached, silently swallowed and indistinguishable
        from "Reticulum isn't running." src.api.service_registry already
        tracks every started plugin service in-process (`live()`, used
        internally by stop_all() but public) -- going straight through
        that instead skips HTTP and auth entirely, so this can reach the
        real LxmfService object directly.

        Best-effort throughout: Reticulum not installed/enabled, or a
        shape this plugin doesn't recognise, both just mean nothing to
        show -- a plugin this plugin doesn't depend on must never be
        able to break its draw loop."""
        try:
            from src.api.service_registry import live

            service = next((svc for name, svc in live() if name == "reticulum"), None)
            if service is None or not getattr(service, "own_address", None):
                return ""
            peer_count = await service.peer_count()
            return f"RT ({peer_count}p)"
        except Exception:  # noqa: BLE001
            return ""

    def _capture_frame(self, image, *, is_status: bool) -> None:
        """Mirror whatever was just drawn into a PNG. The rendered image
        lives on the `canvas` instance itself (`cv.image`, set in its
        __init__ and pushed to hardware via device.display() on
        __exit__) -- NOT on the device object, which has no public
        `.image` attribute at all (confirmed against
        luma.core.device.device's real API, checked in a local venv
        here rather than guessed a second time after the first version
        silently failed via the broad except below -- caller must pass
        the canvas's own image in).

        Always updates `last_frame_png` (an exact mirror of the
        hardware, blank frames included -- kept for anything that
        wants "what's on it right now"). `is_status=True` additionally
        updates `last_status_png`, which only ever holds the last real
        status draw: the settings page's live preview reads THAT one,
        so it keeps showing the last screen contents instead of going
        black in lockstep with the physical panel's burn-in blank --
        matching the physical device only makes the preview useless
        for "what did it last say" the moment auto-blank kicks in."""
        try:
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="PNG")
            png = buf.getvalue()
            self._last_frame_png = png
            self._last_rendered_at = datetime.now(timezone.utc)
            if is_status:
                self._last_status_png = png
                self._last_status_rendered_at = self._last_rendered_at
        except Exception:  # noqa: BLE001 -- preview is a nice-to-have, never fatal
            logger.warning("oled-display: frame capture failed", exc_info=True)

    async def wake(self) -> bool:
        """Force an immediate status redraw and restart the blank
        timer -- backs the settings page's Wake button. Resetting
        `_start_time` is enough: `_loop()` re-derives "should I be
        blanked" from `elapsed = now - _start_time` on every tick, so
        this both un-blanks the physical panel right away (via the
        `_draw_status()` call below, no need to wait for the next loop
        tick) and makes the existing blank_after timeout count from
        now again. Returns False if the display was never opened, so
        the route can tell the frontend there's nothing to wake."""
        if self._device is None:
            return False
        self._start_time = time.monotonic()
        await self._draw_status()
        return True

    # -- exposed to routes.py -----------------------------------------

    @property
    def last_frame_png(self) -> bytes | None:
        return self._last_frame_png

    @property
    def last_rendered_at(self) -> datetime | None:
        return self._last_rendered_at

    @property
    def last_status_png(self) -> bytes | None:
        return self._last_status_png

    @property
    def last_status_rendered_at(self) -> datetime | None:
        return self._last_status_rendered_at

    @property
    def is_blanked(self) -> bool:
        return self._blanked

    @property
    def is_open(self) -> bool:
        return self._device is not None
