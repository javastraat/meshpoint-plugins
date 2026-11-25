"""LocalMesh plugin: a LocalMesh receiver (companion firmware) on USB serial as a Meshpoint capture source.

Every packet the receiver hears goes into Meshpoint's packet log under its own protocol "localmesh"; a topbar chip
shows the receiver's state; the LocalMesh page (sidebar) lists members, messages and packets, sends messages
(Send tab, admin) and has a Settings tab for the receiver list.
LocalMesh uses 62.5 kHz, which Meshpoint's SX1302 concentrator cannot demodulate, so a USB receiver is the only way in.

Imports are deferred into register() so protocol.py/decode-free tests run without FastAPI.
"""

from __future__ import annotations


def register(reg) -> None:
    from . import decode, hooks, routes, send_routes, settings_routes, state
    from .listener import LocalMeshSerialSource

    state.init(reg.config)
    reg.add_router(routes.router)
    reg.add_router(settings_routes.router)
    reg.add_router(send_routes.router)

    def build() -> tuple[LocalMeshSerialSource, ...]:
        return tuple(
            LocalMeshSerialSource(serial_port=d.get("serial_port"), serial_baud=d.get("serial_baud", 115200),
                                  label=d.get("label", ""), name=d.get("name"))
            for d in state.devices()
        )

    def wire(sources, pipeline) -> None:
        # Same shared core message store Reticulum's own plugin writes into
        # (see ~/Software/meshpoint-plugins/apps/reticulum/backend/__init__.py's
        # own MessageRepository(context.pipeline.database) construction) --
        # lets LocalMesh conversations show up in the core Messages page
        # alongside Reticulum/Meshtastic/MeshCore, for free, via the
        # already-generic GET /api/messages/conversations. build() runs
        # before pipeline exists, so this can't be constructed there --
        # every source gets the one shared instance here instead.
        from src.storage.message_repository import MessageRepository

        # The capture-source registration (add_capture_source, below) only
        # hands wire() the pipeline, not a ServiceContext with its own
        # ws_manager the way add_service() gives reticulum's plugin -- but
        # ws_manager is a plain module-level singleton in src.api.server
        # (`ws_manager = WebSocketManager()`), so it's just as reachable
        # here via a direct import. Needed so a new/sent message can push
        # a live "message_received"/"message_sent" WS event the same way
        # core's own meshtastic/meshcore handler does (server.py's
        # on_text_packet) -- without it, LocalMesh messages only showed up
        # in the sidebar badge/notifier/open-thread after the next full
        # page load, since nothing was pushing live.
        from src.api.server import ws_manager

        message_repo = MessageRepository(pipeline.database)
        for n, s in enumerate(sources):
            s.message_repo = message_repo
            s.ws_manager = ws_manager
            # the lists from before the restart (restore.py); the messages table does not say which receiver
            s.restore_from(pipeline.database, messages=n == 0)
        routes.init_routes(sources)
        send_routes.init_routes(sources)
        hooks.set_sources(sources)   # for plugins that build on this one (localmesh-bot), see hooks.py

    reg.add_capture_source("localmesh", build, wire)
    reg.add_protocol("localmesh", capture_prefix="localmesh",
                     adapt=lambda raw: decode.adapt_rx_log(raw.payload, signal=raw.signal, extra=raw.pre_decoded))
