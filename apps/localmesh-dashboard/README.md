# LocalMesh Dashboard plugin

A live, standalone companion page to the [localmesh](../localmesh) plugin's
own Members/Messages/Activity/Send/Receiver/Settings page: five stat cards
(Status / Members / Neighbours / Messages (24h) / Airtime), a map of located
members, a live member list, and a flashing real-time activity ticker,
newest first.

## Why a separate plugin, not a tab on the localmesh page

The manifest only supports one `[sidebar]` table per plugin, so a second
top-level nav item needs a second plugin — same minimal, backend-free
`provides = ["sidebar"]` shape as [hello-world](../hello-world) and
[reticulum-dashboard](../reticulum-dashboard), just reading someone else's
API instead of saying hello.

## Why it exists

Same gap [reticulum-dashboard](../reticulum-dashboard) already closes for
Reticulum: a box with only LocalMesh configured gets packets into the core
Dashboard's RF capture pipeline (LocalMesh's capture source does feed the
Packets table), but the stat-card summary that pipeline's cards give isn't
shaped for LocalMesh's own data at all — no "Members heard 24h / on the
list", no "Neighbours heard directly", no "Airtime budget this hour". This
page gives LocalMesh that same live, glanceable feel, in its own data
shape, the same way reticulum-dashboard did for Reticulum's.

## No backend of its own

`provides = ["sidebar"]` only. It reads the localmesh plugin's already-public
`GET /api/localmesh/{status,members,messages,activity}` and `GET /api/device`
(for the home-location marker). If the localmesh plugin isn't enabled, those
fetches just fail and the page sits in its empty state — no crash, no
dependency mechanism needed.

## No live push — polls every 10s

Unlike Reticulum, LocalMesh has no WebSocket broadcasts of its own (its
capture source only feeds the generic RF packet pipeline, which this page
doesn't use), so everything here polls on the same 10s cadence
`localmesh_panel.js`'s own page already uses, instead of reacting to a push
event. The Activity ticker still gets the same "flowing" flash feel: each
poll is diffed against what's already rendered, and only the genuinely new
rows get prepended with a flash.

## Smaller in scope than reticulum-dashboard

LocalMesh has no NomadNet-style page browsing. It does have a working
"Send message" action on the member drawer, same as reticulum-dashboard's
own peer drawer — LocalMesh messages now mirror into the same shared core
message store Reticulum/Meshtastic/MeshCore write into (see the localmesh
plugin's own `listener.py`), so clicking a member here can jump straight
to a live thread on the core Messages page.

## Enable it

```yaml
plugins:
  localmesh-dashboard:
    enabled: true
```

Restart, then "LocalMesh Dashboard" in the sidebar, right under the built-in
Dashboard (its `[sidebar].category = "top"` — the same tier Dashboard itself
occupies, not filed under Networks). Needs `plugins.localmesh.enabled: true`
too, or there's nothing to show.

## Layout

```
plugin.toml                          manifest ([sidebar] table, [frontend])
backend/__init__.py                  register(reg) -- a no-op; nothing to register
frontend/localmesh_dashboard.js      the page content (registerSidebarPage)
frontend/localmesh_dashboard.css     map/popup/member-row/ticker-column styling
```
