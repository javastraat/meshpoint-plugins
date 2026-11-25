"""A small seam for other plugins that build on this one (first user: localmesh-bot, 20-11).

Meshpoint loads every plugin's backend as its own top-level module (``meshpoint_plugin_<name>``, src/plugins/loader.py),
in an order this plugin does not control. So the registry lives in ``sys.modules`` under a fixed name: whichever plugin
gets there first creates it, the other finds it. No import between plugins, no load order.

    registry().sources                    the receivers (LocalMeshSerialSource), set by this plugin's wire()
    registry().status_handlers            callables handler(source, status: dict), called for every repeater status that
                                          could be opened (copies through other repeaters too: same node and counter)
    registry().message_handlers           callables handler(source, message: dict), called for every NEW message

A handler is called on the event loop thread (scheduled from the serial reader thread), once per message: the
board's buffer comes back at every connect, but _note_message() only passes on what it had not seen yet since this
service started. A handler must not block; one that raises is logged and skipped, never stops the others.

A plugin that uses this copies registry() as it is here (same name, same attributes), so it works whichever loads
first.
"""

from __future__ import annotations

import logging
import sys
import types

logger = logging.getLogger(__name__)

REGISTRY_MODULE = "meshpoint_localmesh_hooks"


def registry() -> types.ModuleType:
    reg = sys.modules.get(REGISTRY_MODULE)
    if reg is None:
        reg = types.ModuleType(REGISTRY_MODULE)
        reg.sources = ()
        reg.message_handlers = []
        sys.modules[REGISTRY_MODULE] = reg
    if not hasattr(reg, "status_handlers"):   # created by a plugin with an older copy of this function
        reg.status_handlers = []
    return reg


def set_sources(sources) -> None:
    registry().sources = tuple(sources)


def emit_message(source, message: dict) -> None:
    for handler in list(registry().message_handlers):
        try:
            handler(source, dict(message))
        except Exception:
            logger.exception("localmesh: a message handler failed")


def emit_repeater_status(source, status: dict) -> None:
    for handler in list(registry().status_handlers):
        try:
            handler(source, dict(status))
        except Exception:
            logger.exception("localmesh: a repeater status handler failed")
