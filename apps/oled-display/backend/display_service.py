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
import signal
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
        self._prev_sigterm = None
        self._prev_sigint = None
        self._shutdown_message_shown = False
        self._shutdown_install_task: asyncio.Task | None = None
        # idle_animation ("matrix"/"pet") state -- see _idle_animation_loop().
        # _rf_activity_count is bumped by _on_packet (core's on_packet hook,
        # registered in start()) and drained each animation tick. RT
        # activity is read from the live LxmfService's own announce_log()
        # instead (see _reticulum_activity_delta() -- compared by
        # timestamp, not by length, since that log is a capped ring
        # buffer). _activity_level is the smoothed "mood" value both draw
        # methods read.
        self._idle_anim_task: asyncio.Task | None = None
        self._rf_activity_count = 0
        self._activity_level = 0
        self._last_seen_announce_ts: str | None = None
        self._anim_frame = 0
        # "pet" (dinosaur) walk state -- persists across frames so it
        # actually paces back and forth rather than teleporting; reset
        # each time the idle animation (re)starts, see _idle_animation_loop().
        self._dino_x = 0
        self._dino_dir = 1

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
        self._shutdown_install_task = asyncio.ensure_future(self._install_shutdown_handlers_delayed())
        # Registered unconditionally (negligible per-packet cost even when
        # idle_animation is "off") -- there's no off_packet()/unregister in
        # core, so this stays registered for the pipeline's lifetime; the
        # callback itself no-ops once self._device is gone (see
        # _on_packet). core's _notify_callbacks() already wraps every
        # callback in its own try/except, so a bug here can't take down
        # the real packet pipeline.
        try:
            self._context.pipeline.on_packet(self._on_packet)
        except Exception:  # noqa: BLE001
            logger.warning("oled-display: could not register on_packet hook", exc_info=True)
        logger.info(
            "oled-display started (%s @ %s, %sx%s)",
            self._cfg["driver"], self._cfg["i2c_address"],
            self._cfg["width"], self._cfg["height"],
        )

    async def stop(self) -> None:
        self._stop_event.set()
        await self._stop_idle_anim()
        if self._shutdown_install_task is not None:
            self._shutdown_install_task.cancel()
            try:
                await self._shutdown_install_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._restore_shutdown_handlers()
        # Skipped when a SIGTERM/SIGINT already put the "restarting..."
        # message up (see _on_shutdown_signal) -- blanking here would
        # just overwrite that message with a plain blank a moment
        # later, right as the process is exiting anyway. Still runs
        # for a stop() reached without a preceding signal (e.g. a test
        # harness calling it directly), same safety net as before.
        if self._device is not None and not self._shutdown_message_shown:
            try:
                self._device.clear()
            except Exception:  # noqa: BLE001
                pass
        logger.info("oled-display stopped")

    # How long to wait, from this plugin's own start(), before actually
    # installing the shutdown handlers -- see
    # _install_shutdown_handlers_delayed()'s docstring for why this
    # can't just happen immediately. Comfortably past the ~8s this
    # box's own full startup (every plugin, concentrator, Reticulum,
    # all of it) took end to end in the boot log used to pick this
    # number -- not a hard guarantee against a slower box or a future
    # plugin that itself defers its own signal registration, just a
    # generous buffer against what's actually running today.
    _SHUTDOWN_HANDLER_DELAY_S = 8.0

    async def _install_shutdown_handlers_delayed(self) -> None:
        """Deferred on purpose -- installing immediately in start()
        was tried first and doesn't work. Confirmed live: Reticulum's
        underlying RNS library installs its OWN real SIGTERM handler
        when IT starts up ("RNS: Received SIGTERM, shutting down
        now!" on every restart), and Python only ever has one handler
        per signal -- RNS doesn't know or care that this plugin got
        there first, so its later `signal.signal()` call silently
        replaces this one's, with no chaining. Since plugins start in
        registration order and this one (alphabetically) starts
        before reticulum, installing immediately meant this handler
        was always dead by the time a real shutdown happened.

        Waiting past every other plugin's own startup flips that:
        THIS plugin's `signal.signal()` call ends up being the last
        one made, so it's the live handler at actual shutdown time --
        and because it saves+chains rather than overwriting, it
        correctly hands off to RNS's real handler afterward instead
        of skipping it."""
        await asyncio.sleep(self._SHUTDOWN_HANDLER_DELAY_S)
        if self._device is not None:
            self._install_shutdown_handlers()

    def _install_shutdown_handlers(self) -> None:
        """Chain onto SIGTERM/SIGINT so the panel shows a
        "restarting..." message the INSTANT the process is asked to
        stop -- before `service_registry.stop_all()`'s async, ordering
        -dependent teardown even starts. Plugins stop in REVERSE
        registration order, and this one registers alphabetically
        *before* the reticulum plugin, whose own teardown does real
        disk I/O (persisting LXMF ratchet state) -- waiting for
        `stop()` to be reached the normal way risks the panel still
        showing stale content if the process runs out of systemd's
        `TimeoutStopSec` budget before this plugin's turn comes up.
        Firing on the signal itself sidesteps that ordering entirely.
        Called only after `_install_shutdown_handlers_delayed()`'s
        wait -- see that method for why the delay itself is required.

        Safe to chain: uvicorn installs its own SIGTERM/SIGINT
        handlers with a plain `signal.signal(sig, self.handle_exit)`
        (confirmed against uvicorn's own `Server.
        install_signal_handlers()` source -- not asyncio's own signal
        machinery, which would make overwriting it here dangerous).
        By the time THIS runs, whatever's currently installed (RNS's
        own handler, or uvicorn's trivial flag-setter if Reticulum
        isn't enabled) is saved and chained to afterward, so nobody's
        own graceful-shutdown logic gets skipped. Only SIGKILL (an
        unblockable, un-catchable signal, by design -- true for every
        process, not something any handler anywhere can work around)
        can still skip this."""
        self._prev_sigterm = signal.getsignal(signal.SIGTERM)
        self._prev_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, self._on_shutdown_signal)
        signal.signal(signal.SIGINT, self._on_shutdown_signal)

    def _restore_shutdown_handlers(self) -> None:
        if self._prev_sigterm is not None:
            signal.signal(signal.SIGTERM, self._prev_sigterm)
        if self._prev_sigint is not None:
            signal.signal(signal.SIGINT, self._prev_sigint)

    def _on_shutdown_signal(self, signum, frame) -> None:
        """The actual handler. Draws the restart message -- unconditionally,
        even if the panel was manually asleep or auto-blanked at the
        time, since a restart is worth seeing regardless -- then chains
        to whatever handler was previously installed (see
        `_install_shutdown_handlers()`) so the app's own shutdown
        sequence still proceeds exactly as it would have. Synchronous
        and fast (a handful of I2C writes): runs in signal context on
        the process's main thread and must never block or raise."""
        self._shutdown_message_shown = True
        try:
            self._draw_wordmark_page("restarting...")
        except Exception:  # noqa: BLE001 -- never let this break the real shutdown
            logger.warning("oled-display: shutdown message draw failed", exc_info=True)

        prev = self._prev_sigterm if signum == signal.SIGTERM else self._prev_sigint
        if callable(prev):
            prev(signum, frame)

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

    def _draw_wordmark_page(self, subtitle: str) -> None:
        """Big centered MESHPOINT title + a small subtitle underneath --
        shared by the boot logo ("starting...") and the shutdown
        handler's message ("restarting..."), so both read as the same
        "the box is doing something, hang on" moment rather than two
        different visual languages. Synchronous on purpose: the
        shutdown handler calls this from signal context, where nothing
        can be awaited."""
        from luma.core.render import canvas

        width, height = self._cfg["width"], self._cfg["height"]
        title = "MESHPOINT"
        sub_font = self._boot_font()

        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            title_font = self._fit_title_font(draw, title, width, height)

            tl, tt, tr, tb = draw.textbbox((0, 0), title, font=title_font)
            tw, th = tr - tl, tb - tt
            sl, st, sr, sb = draw.textbbox((0, 0), subtitle, font=sub_font)
            sw, sh = sr - sl, sb - st

            gap = 3
            top = max(0, (height - (th + gap + sh)) // 2)
            draw.text(((width - tw) // 2 - tl, top - tt), title, font=title_font, fill="white")
            draw.text(((width - sw) // 2 - sl, top + th + gap - st), subtitle, font=sub_font, fill="white")
        self._capture_frame(cv.image, is_status=False)

    async def _show_boot_logo(self) -> None:
        boot_seconds = float(self._cfg.get("boot_logo_seconds", 3.0))
        if boot_seconds <= 0:
            return  # boot_logo_seconds: 0 -- skip it entirely, straight to the status loop
        self._draw_wordmark_page("starting...")
        await asyncio.sleep(boot_seconds)

    async def _loop(self) -> None:
        refresh_s = max(1, int(self._cfg.get("refresh_seconds", 5)))
        blank_after_s = int(self._cfg.get("blank_after_minutes", 0)) * 60

        while not self._stop_event.is_set():
            elapsed = time.monotonic() - self._start_time
            timed_out = bool(blank_after_s) and elapsed >= blank_after_s
            tick_s = refresh_s

            if (self._manual_sleep or timed_out) and self._current_idle_animation() != "off":
                if self._idle_anim_task is None or self._idle_anim_task.done():
                    self._idle_anim_task = asyncio.ensure_future(self._idle_animation_loop())
            elif self._manual_sleep or timed_out:
                await self._stop_idle_anim()
                if not self._blanked:
                    self._blank()
            elif self._cfg.get("rotate_screens", False):
                await self._stop_idle_anim()
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
                await self._stop_idle_anim()
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
        await self._stop_idle_anim()
        self._manual_sleep = False
        self._start_time = time.monotonic()
        await self._render_pages(advance=1)
        return True

    async def prev_page(self) -> bool:
        """The Next button's mirror image -- see `next_page()`."""
        if self._device is None:
            return False
        await self._stop_idle_anim()
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

    # -- idle_animation (matrix/pet) ------------------------------------

    def _current_idle_animation(self) -> str:
        """Reads ``plugins.oled-display.idle_animation`` LIVE from
        ``state`` rather than ``self._cfg`` (the one-time snapshot every
        other setting here reads from, which is why every other change
        needs a restart to apply). Deliberately scoped to just this one
        key: i2c_address/driver/width/height genuinely can't apply live
        -- the physical device object is only ever opened once, in
        start() -- but idle_animation has no such hardware dependency,
        it's pure software re-checked every tick/frame anyway, so
        there's no reason to make someone restart the service just to
        preview a screensaver choice. Falls back to the startup snapshot
        on any import/lookup failure, same as every other best-effort
        read in this file."""
        try:
            from . import state

            return state.to_dict().get("idle_animation", "off")
        except Exception:  # noqa: BLE001
            return self._cfg.get("idle_animation", "off")

    def _on_packet(self, packet) -> None:
        """Registered with core's PipelineCoordinator.on_packet() in
        start() -- fires once per decoded packet, synchronously, off the
        same event loop this plugin's own tasks run on (no locking
        needed for a plain counter increment). There's no
        off_packet()/unregister in core, so this stays registered for
        the pipeline's whole lifetime even after this service's own
        stop() -- the self._device check makes it a harmless no-op once
        this plugin itself has stopped."""
        if self._device is None:
            return
        self._rf_activity_count += 1

    def _rf_activity_count_and_reset(self) -> int:
        count = self._rf_activity_count
        self._rf_activity_count = 0
        return count

    async def _stop_idle_anim(self) -> None:
        """Cancel the idle-animation task if one is running (a no-op
        otherwise) -- called from every path that leaves idle-animation
        mode (waking, manual page nav, a real draw mode, service
        shutdown) so the animation never keeps racing a real draw for
        control of self._device. Cancels THEN awaits, same pattern
        stop() already uses for self._task/self._shutdown_install_task
        -- a bare .cancel() only schedules the CancelledError, it
        doesn't guarantee the task has actually stopped drawing yet."""
        task = self._idle_anim_task
        if task is None:
            return
        self._idle_anim_task = None
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    _IDLE_ANIM_FPS = 3

    async def _idle_animation_loop(self) -> None:
        """Runs only while idle (the blank timeout has elapsed and
        idle_animation isn't "off"), independent of refresh_seconds --
        its own faster ~3fps ticker, deliberately conservative: nothing
        in this plugin (or this repo) has ever measured this board's
        actual I2C throughput at animation rates, and the Pi is already
        busy with SDR capture/decode/the dashboard itself. Each frame is
        wrapped in its own try/except (log + skip) so one bad draw can't
        silently freeze the whole idle-anim task on its last frame with
        nothing in the log to explain why."""
        frame_interval = 1.0 / self._IDLE_ANIM_FPS
        self._last_seen_announce_ts = None  # re-seed fresh each (re)start
        self._dino_x = 2  # walk from the left edge each time "pet" (re)starts
        self._dino_dir = 1
        while not self._stop_event.is_set():
            try:
                rf = self._rf_activity_count_and_reset()
                rt = await self._reticulum_activity_delta()
                self._activity_level = self._activity_level // 2 + rf + rt
                mode = self._current_idle_animation()
                if mode == "blink":
                    self._draw_blink_frame(self._activity_level, self._anim_frame)
                elif mode == "smile":
                    self._draw_smile_frame(self._activity_level, self._anim_frame)
                elif mode == "pet":
                    self._draw_dino_frame(self._activity_level, self._anim_frame)
                elif mode == "matrix":
                    self._draw_matrix_frame(self._anim_frame)
                else:
                    return  # config can only change on restart today, but be safe
                self._anim_frame += 1
            except Exception:  # noqa: BLE001
                logger.warning("oled-display: idle animation frame failed", exc_info=True)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=frame_interval)
            except asyncio.TimeoutError:
                pass

    def _draw_blink_frame(self, level: int, frame: int) -> None:
        """The simplest idle face: a bouncing blob with wide/droopy eyes
        and a wiggling antenna when excited, a flat "bored" mouth when
        quiet -- no sprite assets, just Pillow primitives, same canvas/
        draw pattern every other frame in this file uses. Purely
        decorative: idle_animation has zero practical value by design, a
        fun thing to glance at instead of a dead blank screen."""
        from luma.core.render import canvas

        width, height = self._cfg["width"], self._cfg["height"]
        cx, cy = width // 2, height // 2
        excited = level > 0
        bounce = 2 if (excited and frame % 6 < 3) else 0

        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            body_r = 14
            draw.ellipse(
                (cx - body_r, cy - body_r + bounce, cx + body_r, cy + body_r + bounce),
                outline="white",
            )
            # Wide-open eyes when excited, droopy/half-closed when quiet.
            eye_dy = -4 if excited else 2
            for dx in (-6, 6):
                draw.ellipse(
                    (cx + dx - 2, cy + eye_dy - 2, cx + dx + 2, cy + eye_dy + 2),
                    fill="white",
                )
            if excited:
                tip_dx = 4 if frame % 2 else -4  # antenna wiggle
                draw.line((cx, cy - body_r, cx + tip_dx, cy - body_r - 8), fill="white")
            else:
                draw.line((cx - 4, cy + body_r - 2, cx + 4, cy + body_r - 2), fill="white")
        self._capture_frame(cv.image, is_status=False)

    def _draw_smile_frame(self, level: int, frame: int) -> None:
        """A sadder/happier face than "blink": a real frown when quiet,
        eyes raised and looking up with the antenna held straight up
        (plus a little blip climbing it, like it's picking something up)
        and a proper smile when excited. Same canvas/draw pattern as
        every other frame in this file."""
        from luma.core.render import canvas

        width, height = self._cfg["width"], self._cfg["height"]
        cx, cy = width // 2, height // 2
        excited = level > 0
        bounce = 2 if (excited and frame % 6 < 3) else 0
        body_r = 14
        body_top = cy - body_r + bounce

        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            draw.ellipse(
                (cx - body_r, body_top, cx + body_r, cy + body_r + bounce),
                outline="white",
            )
            if excited:
                eye_dy = -8 + bounce
                for dx in (-6, 6):
                    draw.ellipse(
                        (cx + dx - 2, cy + eye_dy - 2, cx + dx + 2, cy + eye_dy + 2),
                        fill="white",
                    )
                antenna_top = body_top - 9
                draw.line((cx, body_top, cx, antenna_top), fill="white")
                blip_y = body_top - (frame % 4) * 2 - 1
                draw.ellipse((cx - 1, blip_y - 1, cx + 1, blip_y + 1), fill="white")
                my = cy + 4 + bounce
                draw.arc((cx - 6, my - 4, cx + 6, my + 4), start=0, end=180, fill="white")  # smile
            else:
                eye_dy = 2
                for dx in (-6, 6):
                    draw.ellipse(
                        (cx + dx - 2, cy + eye_dy - 1, cx + dx + 2, cy + eye_dy + 1),
                        fill="white",
                    )
                my = cy + body_r - 6
                draw.arc((cx - 6, my - 4, cx + 6, my + 4), start=180, end=360, fill="white")  # frown
        self._capture_frame(cv.image, is_status=False)

    _DINO_W = 20
    _DINO_H = 14
    _DINO_SPEED = 2

    def _draw_dino_frame(self, level: int, frame: int) -> None:
        """A little dinosaur that paces back and forth across the panel
        while idle (self._dino_x/_dino_dir persist across frames so it
        actually walks rather than teleporting, reset each time
        idle-animation restarts), legs alternating each frame for a
        walking gait. A short "!" pops up over its head on any frame
        with recent activity (level > 0) -- "it noticed something" --
        rather than changing the walk cycle itself, so the constant
        back-and-forth pacing reads the same whether the mesh is busy
        or not and the activity cue stays a clean, separate signal."""
        from luma.core.render import canvas

        width, height = self._cfg["width"], self._cfg["height"]
        min_x, max_x = 2, max(2, width - self._DINO_W - 2)
        self._dino_x += self._dino_dir * self._DINO_SPEED
        if self._dino_x >= max_x:
            self._dino_x, self._dino_dir = max_x, -1
        elif self._dino_x <= min_x:
            self._dino_x, self._dino_dir = min_x, 1
        x = self._dino_x
        facing_right = self._dino_dir > 0
        ground_y = height - 6
        body_top = ground_y - self._DINO_H
        bx0, bx1 = x, x + int(self._DINO_W * 0.7)

        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            draw.line((0, ground_y, width, ground_y), fill="white")  # the ground
            draw.ellipse((bx0, body_top, bx1, body_top + self._DINO_H), outline="white")
            # Head: a small blob at whichever end it's facing.
            head_dx = 6 if facing_right else -6
            head_x0, head_x1 = sorted(((bx1 - 3) if facing_right else (bx0 - 3),
                                        (bx1 - 3 + head_dx) if facing_right else (bx0 - 3 + head_dx)))
            draw.ellipse((head_x0, body_top - 2, head_x1, body_top + 7), outline="white")
            # Tail stub at the back.
            tail_x = bx0 if facing_right else bx1
            tail_dx = -6 if facing_right else 6
            draw.line((tail_x, body_top + self._DINO_H - 3, tail_x + tail_dx, body_top + self._DINO_H + 2), fill="white")
            # Alternating legs -- a simple 2-frame walk cycle.
            leg_phase = frame % 2
            for i, lx in enumerate((bx0 + 3, bx1 - 5)):
                lean = 2 if i == leg_phase else -2
                draw.line((lx, body_top + self._DINO_H - 1, lx + lean, ground_y), fill="white")
            if level > 0:
                bang_x = (head_x0 + head_x1) // 2
                draw.line((bang_x, body_top - 10, bang_x, body_top - 5), fill="white")
                draw.point((bang_x, body_top - 3), fill="white")
        self._capture_frame(cv.image, is_status=False)

    _MATRIX_COLS = 16
    _MATRIX_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ!@#$%&*"
    # How many rows of a column are lit at once -- the "falling streak."
    # Monochrome can't fade brightness the way a real terminal-green
    # Matrix effect does, so a short trailing run of characters (instead
    # of filling the whole column solid, which is what the first version
    # of this did and looked like flashing noise, not rain) is the usual
    # substitute for a fade.
    _MATRIX_TRAIL = 5

    def _draw_matrix_frame(self, frame: int) -> None:
        """Classic "digital rain" -- a short streak per column falls
        from the top and wraps back around, each column at its own
        speed and phase (seeded off the column index) so they don't all
        advance in lockstep, which is what made the first version of
        this read as flashing rather than falling."""
        from luma.core.render import canvas
        from PIL import ImageFont

        font = ImageFont.load_default()
        width, height = self._cfg["width"], self._cfg["height"]
        col_w = max(6, width // self._MATRIX_COLS)
        row_h = 10
        rows = height // row_h + 1
        cycle = rows + self._MATRIX_TRAIL

        cv = canvas(self._device)
        with cv as draw:
            draw.rectangle(self._device.bounding_box, outline="white", fill="black")
            for col in range(width // col_w):
                speed = 1 + col % 3  # 1x/2x/3x -- columns drift out of sync
                head = (frame // speed + col * 5) % cycle
                for row in range(rows):
                    dist = head - row
                    if not (0 <= dist < self._MATRIX_TRAIL):
                        continue
                    ch = self._MATRIX_CHARS[(row * 7 + col * 3 + frame) % len(self._MATRIX_CHARS)]
                    draw.text((col * col_w, row * row_h), ch, font=font, fill="white")
        self._capture_frame(cv.image, is_status=False)

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
            short_name = f"SN: {getattr(transmit, 'short_name', '') or '?'}"
            long_name = f"LN: {(getattr(transmit, 'long_name', '') or '?')[:16]}"
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
        brackets and cut to 16 chars, which (plus the "ID: " label)
        is as much as fits this panel's width at the default font
        without overflowing. Same "Label: value" convention every
        other rotate_screens page uses, for a consistent look across
        all of them. `announce_log()` is a plain in-memory ring buffer
        (see its own docstring on `LxmfService`) -- `len()` on it is
        free, no DB/RNS round trip, safe to call every tick."""
        try:
            from src.api.service_registry import live

            service = next((svc for name, svc in live() if name == "reticulum"), None)
            address = getattr(service, "own_address", None) if service is not None else None
            if service is None or not address:
                return None
            peer_count = await service.peer_count()
            announce_count = len(service.announce_log())
            return [
                f"ID: {address.strip('<>')[:16]}",
                f"Peers: {peer_count}",
                f"Announces: {announce_count}",
            ]
        except Exception:  # noqa: BLE001
            return None

    async def _reticulum_activity_delta(self) -> int:
        """Reticulum "activity since last tick" for idle_animation's
        mood signal -- same in-process service_registry.live() lookup
        _reticulum_status()/_reticulum_page_lines() already use, but
        counting entries NEWER than a remembered timestamp rather than
        diffing announce_log()'s length. That log is a capped ring
        buffer (LxmfService's own _ANNOUNCE_LOG_MAX) -- len() rises to
        the cap then freezes there forever, so a length-delta would
        silently go to zero permanently on any mesh busy enough to fill
        it. Each entry carries its own "ts"; comparing those costs the
        same (the list is already built internally either way) and
        stays correct under the cap.

        Seeds self._last_seen_announce_ts on the first tick the service
        is actually FOUND, not on this method's first call -- Reticulum
        can start after this plugin notices it's missing, or its log
        could already be near-full by the time it's first seen, and
        seeding against a stale/None value either way would produce one
        bogus giant spike the moment it's finally visible.

        Best-effort, same as every other Reticulum lookup in this file:
        not installed/enabled, or a shape this plugin doesn't recognise,
        both just mean 0 -- never break the idle-anim loop over it."""
        try:
            from src.api.service_registry import live

            service = next((svc for name, svc in live() if name == "reticulum"), None)
            if service is None:
                return 0
            entries = service.announce_log()
            if self._last_seen_announce_ts is None:
                self._last_seen_announce_ts = entries[0]["ts"] if entries else ""
                return 0
            new_count = 0
            for entry in entries:  # newest-first
                ts = entry.get("ts", "")
                if ts <= self._last_seen_announce_ts:
                    break  # reached an already-seen entry -- nothing newer remains
                new_count += 1
            if entries:
                self._last_seen_announce_ts = entries[0]["ts"]
            return new_count
        except Exception:  # noqa: BLE001
            return 0

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
        await self._stop_idle_anim()
        self._manual_sleep = False
        self._start_time = time.monotonic()
        await self._draw_status()
        return True

    async def sleep(self) -> bool:
        """Force the physical panel into its idle state right now --
        backs the settings page's Sleep button, the manual counterpart
        to Wake. Same idle state an auto-timeout would reach: the
        configured `idle_animation` if one is set, otherwise a plain
        blank. A one-shot `_blank()`/animation-start call wouldn't stick
        on its own: `_loop()` would just re-evaluate its own
        elapsed-time check on the next tick and redraw status again
        since the configured timeout hasn't actually elapsed (and never
        will, when blank_after is "never"). `_manual_sleep` overrides
        that check in `_loop()` directly -- combined with `timed_out`
        there, so Sleep and an auto-timeout land on exactly the same
        idle state -- and stays set until `wake()` clears it. Async: an
        idle animation could already be running when Sleep is clicked
        (switching from rotate/static mode straight into it), and
        starting/stopping either one here mirrors `_loop()`'s own
        handling exactly rather than waiting up to refresh_seconds for
        the next tick to notice. Returns False if the display was never
        opened."""
        if self._device is None:
            return False
        self._manual_sleep = True
        if self._current_idle_animation() != "off":
            if self._idle_anim_task is None or self._idle_anim_task.done():
                self._idle_anim_task = asyncio.ensure_future(self._idle_animation_loop())
        else:
            await self._stop_idle_anim()
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
