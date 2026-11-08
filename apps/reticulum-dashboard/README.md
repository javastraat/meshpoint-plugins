# Reticulum Dashboard plugin

A live, standalone companion page to the [reticulum](../reticulum) plugin's
own Peers/Messages/Settings page: five stat cards (Status / Known Peers /
People / Infrastructure / Conversations) plus a flashing real-time announce
ticker, newest first.

## Why a separate plugin, not a tab on the reticulum page

The manifest only supports one `[sidebar]` table per plugin, so a second
top-level nav item needs a second plugin — same minimal, backend-free
`provides = ["sidebar"]` shape as [hello-world](../hello-world), just reading
someone else's API instead of saying hello.

## Why it exists

A box with only Reticulum configured (no concentrator/serial/MeshCore) shows
a completely empty core Dashboard — the RF capture pipeline's map/packet
table/stat cards have nothing to show, because Reticulum deliberately
produces zero packets into that pipeline (see
`memory/plugin-reticulum.md`). This page gives Reticulum its own equivalent
of that live, glanceable feel, in its own data shape instead of a faked fit
into RF-only fields (RSSI/SNR/hop-count) Reticulum peers don't have.

## No backend of its own

`provides = ["sidebar"]` only. It reads the reticulum plugin's already-public
`GET /api/reticulum/{status,peers,announces,telemetry/peers,discovered}` and
`GET /api/messages/conversations`, and listens on the shared dashboard
WebSocket (`window.concentratorWS`) for the `reticulum_announce` /
`reticulum_peer` broadcasts the reticulum plugin's own page already
consumes. If the reticulum plugin isn't enabled, those fetches just fail and
the page sits in its empty state — no crash, no dependency mechanism needed.

## Repeaters on the map

With the reticulum plugin's **Show discovered repeaters on the dashboard
map** setting on (`plugins.reticulum.discover_interfaces`, off by default,
needs an rnsd restart), the map gets a second layer: every node whose
interface-discovery announce rnsd heard with a location (ScotMesh repeaters
with their rmap option on, discoverable meshpoints, public TCP/backbone
nodes), from `GET /api/reticulum/discovered`. Antenna icon, violet for
internet-only nodes, grey when not heard for 3+ days; the popup shows
frequency/SF/bandwidth, hops, last heard and software (plus host:port of
TCP/backbone nodes, for admins only). **RNode only** and **Hide stale**
above the map (both on by default, kept in localStorage) narrow it down,
since the backbone brings in these announces from the whole network. The
layer is redrawn only when the visible set changes, so the 20 s refresh
doesn't close an open popup. With the setting off the layer and switches
are hidden.

## Enable it

```yaml
plugins:
  reticulum-dashboard:
    enabled: true
```

Restart, then "Reticulum Dashboard" in the sidebar, right under the
built-in Dashboard (its `[sidebar].category = "top"` — the same tier
Dashboard itself occupies, not filed under Networks). Needs
`plugins.reticulum.enabled: true` too, or there's nothing to show.

## Layout

```
plugin.toml                          manifest ([sidebar] table, [frontend])
backend/__init__.py                  register(reg) -- a no-op; nothing to register
frontend/reticulum_dashboard.js      the page content (registerSidebarPage)
```
