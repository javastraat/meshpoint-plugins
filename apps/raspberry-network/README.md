# WiFi & Network plugin

Scan for WiFi networks and switch the Pi's WiFi connection straight from
the dashboard — no SSH, no shell, no editing `wpa_supplicant.conf` by
hand. Shows the current connection/state at the top, a **Scan for
networks** button, a sortable-by-signal table of what's nearby, and a
small connect form (password field, for secured networks) per row.
Leave the password blank to reconnect to an already-known network using
its saved credentials, or to join a genuinely open one.

Uses [NetworkManager](https://networkmanager.dev/) (`nmcli`) — the real
network stack on Bookworm-era Raspberry Pi OS, already installed by
default. Nothing to install for this plugin itself.

Adds "WiFi & Network" under **Settings** in the sidebar.

## A real risk, worth reading before using this remotely

Connecting to a network here can disconnect the dashboard itself if
you're currently reached over WiFi and the new network doesn't work —
wrong password, out of range, no real internet behind it, whatever.
Unless this device also has Ethernet plugged in as a fallback, that
means physical access to recover. The page shows this warning
permanently, not just once — read it, and have a fallback path before
trying a network you're not certain about.

What the backend does to keep this as safe as it can be: a connect
attempt never tears down a working connection to try a failing one —
`nmcli device wifi connect` itself blocks until it knows the real
outcome, and a failure just means nothing changed. If it fails, you'll
see `nmcli`'s own real error text on the page (bad password, timeout,
whatever it actually was), not a generic "something went wrong."

## Enable it

```yaml
plugins:
  raspberry-network:
    enabled: true
```

No setup step — `check.sh` verifies `nmcli` is present and
NetworkManager is actually running (not every supported board is
Bookworm+NetworkManager — the Bobcat Miner 300 runs community Armbian)
and tells you plainly if it isn't, rather than letting the page fail
confusingly later.

Restart, then **Settings → WiFi & Network** in the sidebar.

## API

| Method | Path | Role | Description |
|---|---|---|---|
| `GET` | `/api/raspberry-network/status` | any session | Current wifi device state (`null` if no wifi device at all) |
| `POST` | `/api/raspberry-network/scan` | admin | Rescan and list nearby networks |
| `POST` | `/api/raspberry-network/connect` | admin | `{ssid, password}` — connect; the password is never logged (audit trail records the SSID only) |

## Layout

```
plugin.toml                          manifest ([sidebar] + [deps] + [frontend])
check.sh                             verifies nmcli + NetworkManager are actually usable
backend/__init__.py                  register(reg) -- add_router
backend/routes.py                    /api/raspberry-network/{status,scan,connect}
frontend/raspberry_network.js        the sidebar page (registerSidebarPage), scan/connect flow
frontend/raspberry_network.css       only what core CSS doesn't already cover
```

The actual `nmcli` calls live in core (`src/api/nmcli.py`), not here —
same pattern as the Reticulum plugin's `restart-rnsd` button reusing
core's `src/api/systemctl.py`: a small, narrowly-scoped, sudoers-granted
wrapper stays in core (`config/sudoers-meshpoint`), while the actual
feature (this page, its API, its UI) lives in the plugin.

Full plugin-architecture write-up: [docs/PLUGINS.md](https://github.com/javastraat/meshpoint/blob/main/docs/PLUGINS.md)
(in the main Meshpoint repo — this satellite repo has no `docs/` of its own).
