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
        self._manual_sleep = False
        self._page_index = 0
        self._page_started = time.monotonic()
        self._current_page_title: str | None = None

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

    @staticmethod
    def _boot_font(size: int | None = None):
        """The classic small bitmap `load_default()` has no size knob
        on every Pillow version -- `size=` only works from Pillow
        10.1's scalable variant (setup.sh pins `pillow>=10.0`, so an
        exactly-10.0.x install would still hit the old signature).
        Falls back to the small bitmap font so a big title just quietly
        becomes a same-size one on an old Pillow rather than crashing
        the boot sequence."""
        from PIL import ImageFont

        if size is None:
            return ImageFont.load_default()
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def _fit_title_font(self, draw, text: str, width: int, height: int):
        """Largest boot-logo title size that still fits `text` within
        `width` (4px total margin) -- "MESHPOINT" at the height-driven
        ceiling alone overflowed a 128px-wide panel (162px wide at
        size=28), so width has to shrink it back down, not just height.
        Ceiling is still height-driven (`height // 2`, capped at 28) so
        a short/wide panel doesn't end up with a title taller than the
        panel; floor is 8 so a tiny panel still gets something bigger
        than the "starting..." subtitle rather than giving up."""
        max_size = max(10, min(28, height // 2))
        min_size = 8
        for size in range(max_size, min_size - 1, -1):
            font = self._boot_font(size=size)
            l, t, r, b = draw.textbbox((0, 0), text, font=font)
            if r - l <= width - 4:
                return font
        return self._boot_font(size=min_size)

    async def _show_boot_logo(self) -> None:
        boot_seconds = float(self._cfg.get("boot_logo_seconds", 3.0))
        if boot_seconds <= 0:
            return  # boot_logo_seconds: 0 -- skip it entirely, straight to the status loop

        from luma.core.render import canvas

        width, height = self._cfg["width"], self._cfg["height"]
        title, sub = "MESHPOINT", "starting..."
        sub_font = self._boot_font()

        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            title_font = self._fit_title_font(draw, title, width, height)

            tl, tt, tr, tb = draw.textbbox((0, 0), title, font=title_font)
            tw, th = tr - tl, tb - tt
            sl, st, sr, sb = draw.textbbox((0, 0), sub, font=sub_font)
            sw, sh = sr - sl, sb - st

            gap = 3
            top = max(0, (height - (th + gap + sh)) // 2)
            draw.text(((width - tw) // 2 - tl, top - tt), title, font=title_font, fill="white")
            draw.text(((width - sw) // 2 - sl, top + th + gap - st), sub, font=sub_font, fill="white")
        self._capture_frame(cv.image, is_status=False)
        await asyncio.sleep(boot_seconds)

    async def _loop(self) -> None:
        refresh_s = max(1, int(self._cfg.get("refresh_seconds", 5)))
        blank_after_s = int(self._cfg.get("blank_after_minutes", 0)) * 60

        while not self._stop_event.is_set():
            elapsed = time.monotonic() - self._start_time
            timed_out = bool(blank_after_s) and elapsed >= blank_after_s
            tick_s = refresh_s

            if self._manual_sleep or timed_out:
                if not self._blanked:
                    self._blank()
            elif self._cfg.get("rotate_screens", False):
                # rotate_seconds can legitimately be shorter than
                # refresh_seconds (show each page briefly, but only
                # bother re-querying peer counts every refresh_seconds)
                # -- ticking at whichever is SHORTER keeps the rotation
                # cadence honest instead of only advancing on whatever
                # refresh_seconds happens to allow.
                rotate_s = max(1.0, float(self._cfg.get("rotate_seconds", 4)))
                due = time.monotonic() - self._page_started >= rotate_s
                await self._render_pages(advance=1 if due else 0)
                tick_s = min(refresh_s, rotate_s)
            else:
                await self._draw_status()

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=tick_s)
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

    _PROTOCOL_TITLES = {"LW": "LoRaWAN", "MT": "Meshtastic", "MC": "MeshCore"}

    async def _build_pages(self) -> list[tuple[str, list[str]]]:
        """Pages for rotate_screens mode: an Overview page first, then
        one per protocol actually present -- same LW/MT/MC/RT detection
        as the static view's own `_active_sources()`/`_reticulum_status()`,
        so a page never appears for a protocol that isn't actually
        configured on this box. Always returns at least the Overview
        page, so callers never need to handle an empty list."""
        ip = _lan_ip() or "no network"
        port = getattr(getattr(self._context.config, "dashboard", None), "port", 8080)
        device_name = getattr(getattr(self._context.config, "device", None), "device_name", None)
        uptime = _fmt_uptime(int(time.monotonic() - self._start_time))

        pages: list[tuple[str, list[str]]] = [
            ("Overview", [device_name or "meshpoint", f"{ip}:{port}", f"up {uptime}"]),
        ]

        for label in self._active_protocol_labels():
            pages.append((self._PROTOCOL_TITLES.get(label, label), await self._protocol_page_lines(label)))

        reticulum_lines = await self._reticulum_page_lines()
        if reticulum_lines is not None:
            pages.append(("Reticulum", reticulum_lines))

        return pages

    def _draw_page(self, title: str, lines: list[str]) -> None:
        """Render one rotate_screens page: a title row (same underline
        style as the static view's `IP:port` header) then one detail
        line per row below it."""
        from luma.core.render import canvas
        from PIL import ImageFont

        font = ImageFont.load_default()
        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            draw.text((2, 0), title, font=font, fill="white")
            draw.line((0, 12, self._cfg["width"], 12), fill="white")
            y = 16
            for line in lines:
                draw.text((2, y), line, font=font, fill="white")
                y += 10
        self._capture_frame(cv.image, is_status=True)
        self._blanked = False
        self._current_page_title = title

    async def _render_pages(self, *, advance: int = 0) -> None:
        """Build the current rotate_screens page list and draw whichever
        page that leaves the loop on.

        `advance=0` (the common per-tick case while dwelling on a page)
        just redraws the current index with fresh data (uptime/peer
        counts) -- no page change. A nonzero `advance` steps the index
        by that many pages (wrapping) and restarts this page's own
        dwell timer, so a manual Prev/Next click doesn't get
        immediately overridden by the rotation timer advancing again a
        moment later. One `_build_pages()` call feeds both the index
        math and the draw -- fetched once, not once to size the step
        and again to draw, and the page count it returns is always the
        one actually used, never a value cached from an earlier tick
        (which could go stale the moment a protocol connects/drops)."""
        pages = await self._build_pages()
        if advance:
            self._page_index = (self._page_index + advance) % len(pages)
            self._page_started = time.monotonic()
        else:
            self._page_index = min(self._page_index, len(pages) - 1)
        title, lines = pages[self._page_index]
        self._draw_page(title, lines)

    async def next_page(self) -> bool:
        """Advance to the next rotate_screens page right now, waking
        the panel if it was blanked/asleep -- the settings page's
        manual Next button. Same wake semantics as `wake()`: clears
        `_manual_sleep` and restarts the blank timer, since a viewer
        stepping through pages by hand is clearly looking at it right
        now. Returns False if the display was never opened."""
        if self._device is None:
            return False
        self._manual_sleep = False
        self._start_time = time.monotonic()
        await self._render_pages(advance=1)
        return True

    async def prev_page(self) -> bool:
        """The Next button's mirror image -- see `next_page()`."""
        if self._device is None:
            return False
        self._manual_sleep = False
        self._start_time = time.monotonic()
        await self._render_pages(advance=-1)
        return True

    def _blank(self) -> None:
        from luma.core.render import canvas

        cv = canvas(self._device)
        with cv as draw:
            pass  # leave black -- burn-in protection
        self._capture_frame(cv.image, is_status=False)
        self._blanked = True

    def _active_protocol_labels(self) -> list[str]:
        """LW/MT/MC labels for capture sources actually registered right
        now -- same convention the Messages page's own protocol filter
        chips use, not the raw source names (which don't map 1:1 to
        protocols: "concentrator" is the SX1302 handling LoRaWAN AND
        Meshtastic simultaneously over the same dual-sync-word capture,
        by design -- every "concentrator" source is always both at
        once, never just one). Shared by the static view's
        `_active_sources()` and rotate_screens' `_build_pages()`.

        Best-effort: pipeline shape can vary, a plugin never crashes
        the display loop over it."""
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
        return protocols

    async def _active_sources(self) -> list[str]:
        """Live capture protocols with a peer count, e.g. ['LW (3p)', 'MT
        (12p)'] -- for the static view's cramped one-screen layout.

        Returns a list (not a joined string) so _draw_status() can lay
        entries out multiple-per-row instead of cramming everything onto
        one line -- the display has plenty of unused vertical space.

        Peer counts are all-time unique totals, matching each
        protocol's own dashboard tab exactly (LW's "Unique Devices",
        MT's "Unique Nodes", MC's `total_nodes`) -- see
        `_protocol_peer_count()`. RT's count is likewise Reticulum's
        own all-time known-peer count (`peer_count()`), so all four
        protocols shown here now agree with what their own dashboard
        page says."""
        parts = []
        for p in self._active_protocol_labels():
            count = await self._protocol_peer_count(p)
            parts.append(f"{p} ({count}p)" if count is not None else p)
        return parts

    async def _protocol_peer_count(self, protocol_label: str) -> int | None:
        """All-time unique count for one LW/MT/MC label, matching each
        protocol's own dashboard tab exactly -- "Unique Devices" on the
        LoRaWAN tab (lorawan_routes.py's ``lorawan_stats()``), "Unique
        Nodes" on the Meshtastic tab (meshtastic_routes.py's
        ``meshtastic_stats()``), and MeshCore's ``meshcore_stats()``
        ``total_nodes``. A previous version scoped this to "active in
        the last 24h" on the theory that mattered more for a glance
        display, but that makes the OLED disagree with the dashboard
        the user is looking at side by side over nothing more than an
        unannounced difference in definition -- matching wins.

        LW and MT both come from ``COUNT(DISTINCT source_id) FROM
        packets``: LoRaWAN devices never get a `nodes` table row at all
        (coordinator.py's `_update_node()` only bumps an existing
        packet counter for a LoRaWAN source, it never upserts one), and
        the dashboard's own Meshtastic tile queries `packets` too, not
        `node_repo`, despite MT nodes having real `nodes` rows -- so
        this mirrors `packets` for both rather than only for LW.

        MC has real `nodes` rows and its dashboard tile counts THAT
        table instead (`COUNT(*) FROM nodes WHERE protocol =
        'meshcore'`), so it gets its own query shape rather than
        reusing LW/MT's."""
        try:
            if protocol_label in ("LW", "MT"):
                key = "lorawan" if protocol_label == "LW" else "meshtastic"
                row = await self._context.pipeline.database.fetch_one(
                    "SELECT COUNT(DISTINCT source_id) AS cnt FROM packets "
                    "WHERE protocol = ?",
                    (key,),
                )
                return row["cnt"] if row else 0
            if protocol_label == "MC":
                row = await self._context.pipeline.database.fetch_one(
                    "SELECT COUNT(*) AS cnt FROM nodes WHERE protocol = 'meshcore'",
                )
                return row["cnt"] if row else 0
            return None
        except Exception:  # noqa: BLE001
            return None

    async def _protocol_stats(self, protocol_label: str) -> dict[str, int] | None:
        """Richer LW/MT/MC numbers for a rotate_screens page: all-time
        AND last-24h packet + device counts. A rotate_screens page has
        the room to show both; the static view's one-line-per-protocol
        layout doesn't, which is why this is separate from
        `_protocol_peer_count()` rather than replacing it. Same
        packets-table-for-LW/MT, nodes-table-for-MC split as that
        method (see its own docstring for the reasoning) -- this just
        additionally splits each into all-time and last-24h.

        None on any failure (unrecognised label, DB error, pipeline
        shape not what's expected) -- callers show a "stats
        unavailable" line rather than crashing the draw loop."""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        try:
            db = self._context.pipeline.database
            if protocol_label in ("LW", "MT"):
                key = "lorawan" if protocol_label == "LW" else "meshtastic"
                total_row = await db.fetch_one(
                    "SELECT COUNT(*) AS total, COUNT(DISTINCT source_id) AS devices "
                    "FROM packets WHERE protocol = ?",
                    (key,),
                )
                recent_row = await db.fetch_one(
                    "SELECT COUNT(*) AS total, COUNT(DISTINCT source_id) AS devices "
                    "FROM packets WHERE protocol = ? AND timestamp >= ?",
                    (key, cutoff),
                )
                return {
                    "packets": total_row["total"] if total_row else 0,
                    "devices": total_row["devices"] if total_row else 0,
                    "packets_24h": recent_row["total"] if recent_row else 0,
                    "devices_24h": recent_row["devices"] if recent_row else 0,
                }
            if protocol_label == "MC":
                devices_row = await db.fetch_one(
                    "SELECT COUNT(*) AS cnt FROM nodes WHERE protocol = 'meshcore'",
                )
                packets_row = await db.fetch_one(
                    "SELECT COUNT(*) AS total FROM packets WHERE protocol = 'meshcore'",
                )
                recent_row = await db.fetch_one(
                    "SELECT COUNT(*) AS total FROM packets "
                    "WHERE protocol = 'meshcore' AND timestamp >= ?",
                    (cutoff,),
                )
                devices_24h = await self._context.pipeline.node_repo.get_active_count(
                    hours=24, protocol="meshcore",
                )
                return {
                    "packets": packets_row["total"] if packets_row else 0,
                    "devices": devices_row["cnt"] if devices_row else 0,
                    "packets_24h": recent_row["total"] if recent_row else 0,
                    "devices_24h": devices_24h,
                }
            return None
        except Exception:  # noqa: BLE001
            return None

    async def _protocol_page_lines(self, protocol_label: str) -> list[str]:
        """The rotate_screens detail lines for one LW/MT/MC page.

        MT gets this box's own Meshtastic identity (short/long name --
        `config.transmit.*`, the same fields nodeinfo_broadcaster TXes)
        alongside peer/packet counts, since a person looking at the
        panel to confirm "is this the right box" wants the name, not
        just traffic stats. LW/MC have no identity of their own to
        show (LoRaWAN is a passive sniffer here; a MeshCore companion's
        own name isn't something this plugin reaches for, to stay
        decoupled from that capture source's specific object shape) --
        they get four stats instead: all-time and last-24h for both
        devices and packets, which is genuinely more useful there than
        padding with nothing."""
        stats = await self._protocol_stats(protocol_label)
        if protocol_label == "MT":
            transmit = getattr(self._context.config, "transmit", None)
            short_name = getattr(transmit, "short_name", "") or "?"
            long_name = (getattr(transmit, "long_name", "") or "?")[:20]
            if stats is None:
                return [short_name, long_name, "stats unavailable"]
            return [
                short_name, long_name,
                f"Peers: {stats['devices']}",
                f"Packets: {stats['packets']}",
            ]
        if stats is None:
            return ["stats unavailable"]
        return [
            f"Devices: {stats['devices']}",
            f"Packets: {stats['packets']}",
            f"Active 24h: {stats['devices_24h']}",
            f"Pkts 24h: {stats['packets_24h']}",
        ]

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

    async def _reticulum_page_lines(self) -> list[str] | None:
        """Detail lines for Reticulum's rotate_screens page, or None
        when Reticulum isn't running -- None so `_build_pages()` can
        skip the page entirely rather than showing an empty one.

        Same in-process service_registry lookup as `_reticulum_status()`,
        just with room for the fuller detail a dedicated page has
        (address prefix + peer count + announce count) instead of that
        one line's "RT (Np)" abbreviation. `own_address` comes back as
        `RNS.prettyhexrep()`'s `<32 hex chars>` -- stripped of the
        brackets and cut to 16 chars, which is as much as fits this
        panel's width at the default font without overflowing.
        `announce_log()` is a plain in-memory ring buffer (see its own
        docstring on `LxmfService`) -- `len()` on it is free, no DB/RNS
        round trip, safe to call every tick."""
        try:
            from src.api.service_registry import live

            service = next((svc for name, svc in live() if name == "reticulum"), None)
            address = getattr(service, "own_address", None) if service is not None else None
            if service is None or not address:
                return None
            peer_count = await service.peer_count()
            announce_count = len(service.announce_log())
            return [
                address.strip("<>")[:16],
                f"{peer_count} peers",
                f"{announce_count} announces",
            ]
        except Exception:  # noqa: BLE001
            return None

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
        now again. Also clears `_manual_sleep`, so waking up after a
        manual Sleep behaves the same as waking up after an auto-blank.
        Returns False if the display was never opened, so the route
        can tell the frontend there's nothing to wake."""
        if self._device is None:
            return False
        self._manual_sleep = False
        self._start_time = time.monotonic()
        await self._draw_status()
        return True

    def sleep(self) -> bool:
        """Force the physical panel blank right now -- backs the
        settings page's Sleep button, the manual counterpart to Wake.
        A plain one-shot `_blank()` call wouldn't stick: `_loop()`
        would just re-evaluate its own elapsed-time check on the next
        tick and redraw status again since the configured timeout
        hasn't actually elapsed (and never will, when blank_after is
        set to "never"). `_manual_sleep` overrides that check in
        `_loop()` directly, so the panel stays blank until `wake()`
        clears it. Sync (not async) -- unlike `wake()` it never needs
        to draw a status frame, just blank ones, and `_blank()` itself
        is synchronous. Returns False if the display was never
        opened."""
        if self._device is None:
            return False
        self._manual_sleep = True
        self._blank()
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
    def current_page_title(self) -> str | None:
        """Title of whatever rotate_screens page is currently showing
        (e.g. "Overview", "Meshtastic") -- None in static mode, or
        before the first page has ever been drawn. Lets the settings
        page's Prev/Next controls show what's actually on the panel
        right now instead of a mystery."""
        return self._current_page_title

    @property
    def is_open(self) -> bool:
        return self._device is not None
