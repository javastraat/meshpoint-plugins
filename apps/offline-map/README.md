# Offline Maps plugin

Builds and manages [Cyclenerd/offline-map-tile-downloader](https://github.com/Cyclenerd/offline-map-tile-downloader)
(vendored at `offline-map-src/`) as a background process, so you can
download OpenStreetMap tiles for offline use straight from the device --
no separate SSH session needed to run it.

Adds an **Offline Maps** page under the sidebar's **Ops** section: a
settings form (mirrors the downloader's own CLI flags) and a Start/Stop
control. Once running, the downloader's *own* web UI (a separate app, not
embedded in Meshpoint's dashboard) is where you actually pick an area on a
map and download tiles -- this plugin only builds it and manages the
process + its config.

## ⚠️ Known gap -- read before starting it on a shared network

The upstream binary has a `-port` flag but **no `-host`/`-bind` flag** --
its own `main.go` does `http.ListenAndServe(fmt.Sprintf(":%d", port), nil)`,
which binds every network interface, not just this device. Its web UI has
no login of its own. So starting it today makes an unauthenticated tile
downloader reachable by anyone on the same network, not just this
device's admin -- the plugin's own "Start" confirm dialog says so, but
doesn't stop it.

The real fix is patching a `-host` flag into `offline-map-src/main.go`
(default `127.0.0.1`) so it's only reachable via SSH tunnel or a future
reverse-proxy -- **not done yet**. Until then, treat "Start" the same as
opening a firewall hole: fine on a trusted LAN for a supervised download
session, stop it when you're done, don't leave it running unattended.

## Enable it

```yaml
plugins:
  offline-map:
    enabled: true
```

Restart, then **Settings → Plugins** to run setup (installs `golang-go` if
missing, builds the binary into this plugin's own `bin/` -- nothing
system-wide, no `install.sh`, no root needed after setup). Then **Ops →
Offline Maps** in the sidebar.

## Where tiles/logs go

Settings default to `data/offline-map/{maps,presets}` and
`data/offline-map/offline-map.log` -- Meshpoint's own `data/` convention
(same as `data/reticulum/`, `data/tls/`), not inside this plugin's own
folder, so a plugin update/reinstall never touches downloaded tiles.

## Layout

```
plugin.toml                          manifest ([sidebar], [deps], [frontend])
setup.sh / check.sh                  installs golang-go + builds offline-map-src/
                                      into bin/ (no root needed for the build itself)
offline-map-src/                     vendored upstream Go source (unmodified except
                                      for the .gitignore copied alongside it)
backend/__init__.py                  register(reg) -- reg.add_router()
backend/state.py                     settings (plugins.offline-map.*), same
                                      seed/persist pattern as the Reticulum plugin
backend/process.py                   subprocess start/stop/status -- same shape as
                                      the RTL-SDR listeners' own process management,
                                      minus the shared-hardware arbitration they need
backend/routes.py                    /api/offline-map/{status,settings,start,stop}
frontend/offline_map_panel.js        the sidebar page (registerSidebarPage)
frontend/offline_map_panel.css       layout only -- reuses the app's own
                                      .auth-card / .auth-status / .cfg-field tokens
```

Full plugin-authoring write-up: [docs/PLUGINS.md](https://github.com/KMX415/meshpoint/blob/main/docs/PLUGINS.md)
(in the main Meshpoint repo -- this satellite repo has no `docs/` of its own).
