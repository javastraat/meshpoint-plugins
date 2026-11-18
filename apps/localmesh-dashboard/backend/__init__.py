"""LocalMesh Dashboard plugin -- entry point.

Nothing to register: this plugin only provides "sidebar" (a top-level
live-activity page), wired declaratively through plugin.toml's [sidebar]
table + its frontend script, not through PluginRegistry (see
plugins/apps/hello-world/backend/__init__.py for the same pattern, and
plugins/apps/reticulum-dashboard/backend/__init__.py for the identical
reasoning applied to a different companion plugin).
register() still has to exist and be callable, but has nothing to do.

Deliberately backend-free: the page reads the localmesh plugin's own
already-public /api/localmesh/{status,members,messages,activity} endpoints
and /api/device (for the home-location marker, same as reticulum-dashboard)
via plain fetch(), polling every 10s -- same cadence localmesh_panel.js's
own page already uses. If the localmesh plugin isn't enabled those fetches
just fail and the page shows its empty state.
"""

from __future__ import annotations


def register(reg) -> None:
    pass
