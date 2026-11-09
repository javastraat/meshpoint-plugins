# Reticulum Repeaters plugin

Monitors your own ScotMesh microReticulum repeaters (SenseCAP Solar Node P1,
Heltec V4, ...) over NomadNet, on a sidebar page in the Radio section next to
the built-in MeshCore Repeaters page.

A ScotMesh repeater answers JSON requests on its NomadNet address without an
identity. This plugin polls the ones **you add** -- nothing is scanned or
auto-detected -- and shows per repeater:

- **Status:** battery (V, %, charging), uptime, last reset reason, restart
  count, packets rx/tx, links, routes, neighbours, last poll; a warning for a
  day after the restart count goes up.
- **Radio:** frequency, bandwidth, SF/CR, TX power, noise floor, last
  RSSI/SNR, airtime.
- **Health:** free RAM, link pool and its fragmentation, links closed
  (stuck/idle/memory), TX failures, path table, firmware/board, shared
  location.
- **Sensors** (P1): temperature, humidity, pressure, altitude, MCU
  temperature, power monitors.
- **Neighbours & routes:** the repeater's own lists (up to 45 rows each).
- **Trends:** battery/voltage/temperature/humidity/pressure (core's shared
  chart) and noise floor / RSSI / link-pool fragmentation.

## Polling and airtime

| Requests | When |
|---|---|
| `/api/all`, `/api/radio`, `/api/health`, `/api/env` | every `interval_min` (default 15) |
| `/api/nbrs`, `/api/paths`, `/api/links` | every `lists_interval_min` (default 60) |
| `/api`, `/api/sys`, `/api/pos` | once a day |
| `/api/hist` (the repeater's own 7-day hourly history) | only when the repeater is added |

Each reply fits one LoRa packet (~1 s at SF8/125 kHz); with the defaults it's
well under 1 % airtime. `/api` lists which endpoints the build has, so a board
without sensors or history (V4) is never asked `/api/env` / `/api/hist`
(those would just time out). If the first request fails, the rest of that
poll is skipped and it's retried within 5 minutes.

**Adding** a repeater fetches everything once, including the 7-day history
backfill (only hours before this plugin's own first poll, so a re-add doesn't
double up). **Refresh now** (admins) fetches everything except the history,
at most once a minute per repeater. Both run in the background; the card shows
"Fetching..." until the replies are in.

## Settings

Settings tab on the page (admins only), saved to `local.yaml`, applied on the
poller's next tick without a restart:

```yaml
plugins:
  reticulum-repeaters:
    enabled: true
    polling_enabled: true
    interval_min: 15          # 5..1440
    lists_interval_min: 60    # 15..1440
    repeaters:
      - {hash: <NomadNet address>, name: "NL-AMS-PD2EMC P1"}
```

The name is filled in from the repeater's `/api/sys` after the first poll.

## Data

Two tables in meshpoint's own database, created on start: `rr_latest` (the
last full snapshot per repeater, so the page has data right after a restart)
and `rr_samples` (one row per stats poll plus the backfilled history, kept 60
days). Removing a repeater deletes both.

## API

| Route | Who |
|---|---|
| `GET /api/reticulum-repeaters/repeaters` | logged in |
| `GET /api/reticulum-repeaters/repeaters/{hash}/history?hours=` | logged in |
| `POST /api/reticulum-repeaters/repeaters` `{hash, name}` | admin |
| `DELETE /api/reticulum-repeaters/repeaters/{hash}` | admin |
| `POST /api/reticulum-repeaters/repeaters/{hash}/refresh` (429 within a minute) | admin |
| `PUT /api/reticulum-repeaters/settings` | admin |
| `GET /api/reticulum-repeaters/candidates` (heard NomadNet nodes, pick list only) | admin |

A `reticulum_repeater` WebSocket event follows every poll.

## Requires

The **reticulum** plugin: polling goes through its NomadNet client (same RNS
attach and link cache as the Browse tab).

## Layout

```
plugin.toml
backend/__init__.py     register(): routes + the RepeaterMonitor service
backend/state.py        config (plugins.reticulum-repeaters)
backend/monitor.py      polling tiers, history backfill, restart detection
backend/store.py        rr_latest / rr_samples
backend/routes.py       /api/reticulum-repeaters/*
backend/tests/          test_monitor.py (stdlib), test_routes.py (FastAPI)
frontend/reticulum_repeaters.js / .css
```
