"""Manages the offline-map-tile-downloader subprocess -- start/stop/status.

Same shape as the RTL-SDR listeners' own process management (e.g.
plugins/apps/rtl433/backend/listener.py): asyncio subprocess, its own
process group so stop() can signal the whole tree, SIGTERM with a SIGKILL
escalation if it doesn't exit. Simpler than those, though: this isn't
claiming shared RTL-SDR hardware, so there's no sdr_registry arbitration --
at most one instance of this specific binary needs to run at a time, and
that's just this class's own ``running`` check.

SECURITY NOTE -- read before wiring "Start" into anything user-facing:
the upstream binary's ``-port`` flag picks which port it listens on, but
there is no ``-host``/``-bind`` flag at all -- its own main.go does
``http.ListenAndServe(fmt.Sprintf(":%d", port), nil)``, which binds every
interface. Starting this today makes its (unauthenticated) web UI reachable
by anyone on the LAN, not just this device's admin. Patching a ``-host``
flag into offline-map-src/main.go (default 127.0.0.1) is the real fix and
hasn't been done yet -- this class doesn't work around it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections import deque
from pathlib import Path
from typing import Optional

from . import state

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parents[1]
_BINARY = _PLUGIN_DIR / "bin" / "offline-map-tile-downloader"

_MAX_LOG_LINES = 200
_STOP_GRACE_SECS = 5.0


class OfflineMapProcess:
    """Owns at most one offline-map-tile-downloader process."""

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._last_error: str = ""
        self.log_lines: "deque[str]" = deque(maxlen=_MAX_LOG_LINES)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def binary_present(self) -> bool:
        return _BINARY.is_file() and os.access(_BINARY, os.X_OK)

    def status(self) -> dict:
        cfg = state.to_dict()
        return {
            "running": self.running,
            "binary_present": self.binary_present,
            "port": cfg["port"],
            "maps_directory": cfg["maps_directory"],
            "last_error": self._last_error,
            "log_lines": list(self.log_lines),
        }

    async def start(self) -> None:
        """Raises RuntimeError if the binary hasn't been built yet (run
        setup.sh first) or a process is already running."""
        if not self.binary_present:
            raise RuntimeError(
                "offline-map-tile-downloader is not built yet -- run setup first",
            )
        async with self._lock:
            if self.running:
                return  # already running, idempotent
            await self._start_locked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    # -- internals (call with self._lock held) -----------------------------

    async def _start_locked(self) -> None:
        cfg = state.to_dict()
        for directory in (cfg["maps_directory"], cfg["presets_directory"]):
            Path(directory).mkdir(parents=True, exist_ok=True)
        log_path = Path(cfg["log_file"])
        log_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(_BINARY),
            "-port", str(int(cfg["port"])),
            "-maps-directory", str(cfg["maps_directory"]),
            "-presets-directory", str(cfg["presets_directory"]),
            "-log-file", str(cfg["log_file"]),
            "-max-workers", str(int(cfg["max_workers"])),
            "-rate-limit", str(int(cfg["rate_limit"])),
            "-max-retries", str(int(cfg["max_retries"])),
        ]
        if cfg.get("quiet"):
            cmd.append("-quiet")

        logger.info("offline-map starting: %s", " ".join(cmd))
        self._last_error = ""
        self.log_lines.clear()
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_PLUGIN_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # own process group -> killpg gets all
        )
        loop = asyncio.get_running_loop()
        self._reader_task = loop.create_task(self._read_loop(self._proc))

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                self.log_lines.append(line.decode("utf-8", errors="replace").rstrip())
        except Exception as exc:  # noqa: BLE001 -- reader loop must never crash the process
            logger.warning("offline-map log reader stopped: %s", exc)
        returncode = await proc.wait()
        if returncode not in (0, -signal.SIGTERM, -signal.SIGKILL):
            self._last_error = f"exited with code {returncode}"
            logger.warning("offline-map exited unexpectedly: %s", self._last_error)

    async def _stop_locked(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            self._proc = None
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            self._proc = None
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_STOP_GRACE_SECS)
        except asyncio.TimeoutError:
            logger.warning("offline-map didn't exit on SIGTERM, sending SIGKILL")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
        self._proc = None
