"""Capture source for a LocalMesh receiver (Heltec V4 with companion firmware) on USB serial.

Speaks the binary companion protocol (SLIP frames, see protocol.py): HELLO at connect (the reply is the receiver's
INFO), then it listens. Every packet the receiver hears arrives as an RX_LOG event and goes into Meshpoint's packet
pipeline; STATUS_EVENTs (every 30 s, unsolicited) keep the topbar chip current; MESSAGE and ADVERT events are kept for
the plugin's own page. Read-only: this source never sends a message and never changes the receiver's settings.

Same threading shape as the DAPNET plugin's source: pyserial blocks, so a background thread owns the serial port and
hands results to asyncio with call_soon_threadsafe. Unlike DAPNET it reopens the port by itself when the cable comes
out or the board restarts, and greets the board again.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from dataclasses import asdict
from typing import Any, AsyncIterator, Optional

from src.capture.base import CaptureSource
from src.models.packet import RawCapture
from src.models.signal import SignalMetrics

from . import protocol as p

logger = logging.getLogger(__name__)

RECONNECT_S = 10


class LocalMeshSerialSource(CaptureSource):
    def __init__(self, serial_port: Optional[str] = None, serial_baud: int = 115200, label: str = "") -> None:
        self._port = serial_port
        self._baud = serial_baud
        self._label = label
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._running = False
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._serial = None
        self.info: Optional[p.Info] = None
        self.status: Optional[p.Status] = None
        self.last_heard: Optional[float] = None
        # kept for the plugin's own page (phase 2); newest last
        self.messages: collections.deque = collections.deque(maxlen=200)
        self.adverts: dict[int, dict] = {}

    @property
    def name(self) -> str:
        return f"localmesh_{self._label}" if self._label else "localmesh"

    @property
    def connected(self) -> bool:
        return self._connected

    def snapshot(self) -> dict[str, Any]:
        """For GET /api/localmesh/status and the topbar chip."""
        return {
            "name": self.name, "label": self._label, "serial_port": self._port, "connected": self._connected,
            "info": asdict(self.info) if self.info else None,
            "status": asdict(self.status) if self.status else None,
            "last_heard": self.last_heard,
        }

    def signal(self, rssi: float, snr: float) -> SignalMetrics:
        i = self.info
        return SignalMetrics(rssi=float(rssi), snr=float(snr), frequency_mhz=i.freq_mhz if i else 869.556,
                             spreading_factor=i.sf if i else 7, bandwidth_khz=i.bw_khz if i else 62.5,
                             coding_rate=f"4/{i.cr}" if i else "4/5")

    async def start(self) -> None:
        if not self._port:
            logger.warning("%s: no serial_port configured, source will not run", self.name)
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._run, name=f"{self.name}-reader", daemon=True)
        self._thread.start()
        logger.info("%s: started on %s @ %d baud", self.name, self._port, self._baud)

    async def stop(self) -> None:
        self._running = False
        self._close()

    async def packets(self) -> AsyncIterator[RawCapture]:
        while self._running:
            try:
                yield await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    # --- background thread ---

    def _close(self) -> None:
        self._connected = False
        s, self._serial = self._serial, None
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    def _run(self) -> None:
        """Open, greet, read; on any error close and try again after RECONNECT_S."""
        while self._running:
            try:
                import serial
                self._serial = serial.Serial(self._port, self._baud, timeout=1.0)
                self._connected = True
                self._serial.write(p.hello(seq=1))
                self._serial.write(p.slip_encode(p.frame(p.STATUS, 2)))
                decoder = p.SlipDecoder()
                while self._running:
                    data = self._serial.read(512)
                    if data:
                        frames, _lines = decoder.feed(data)   # text lines are the board's log: not for us
                        for f in frames:
                            self._handle(f)
            except Exception as e:
                if self._running:
                    logger.warning("%s: %s; retrying in %d s", self.name, e, RECONNECT_S)
            self._close()
            for _ in range(RECONNECT_S):
                if not self._running:
                    return
                time.sleep(1)

    def _handle(self, f: bytes) -> None:
        if len(f) < 2:
            return
        t, seq, payload = f[0], f[1], f[2:]
        try:
            if t == p.REPLY and payload and payload[0] == 0:   # error code 0 = ok
                if seq == 1:
                    self.info = p.parse_info(payload[1:])
                elif seq == 2:
                    self.status = p.parse_status(payload[1:])
            elif t == p.STATUS_EVENT:
                self.status = p.parse_status(payload)
            elif t == p.RX_LOG:
                self.last_heard = time.time()
                log = p.parse_rx_log(payload)
                if self._loop is not None:
                    self._loop.call_soon_threadsafe(self._enqueue, payload, log.rssi, log.snr)
            elif t == p.MESSAGE:
                self.messages.append(asdict(p.parse_message(payload)))
            elif t == p.ADVERT:
                a = p.parse_advert(payload)
                self.adverts[a.member] = {**asdict(a), "heard": time.time()}
        except ValueError:
            logger.debug("%s: short or unknown frame 0x%02x ignored", self.name, t)

    def _enqueue(self, payload: bytes, rssi: int, snr: float) -> None:
        try:
            self._queue.put_nowait(RawCapture(payload=payload, signal=self.signal(rssi, snr), capture_source=self.name))
        except asyncio.QueueFull:
            logger.warning("%s: queue full, dropping a packet", self.name)
