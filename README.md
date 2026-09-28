# meshpoint-plugins

A repository of extra apps and themes for [Meshpoint](https://github.com/KMX415/meshpoint).

## Using it

In your Meshpoint dashboard: **Settings → Plugins → Add source**, paste this
repo's URL, confirm the trust prompt, then Browse to see what's available.

> ⚠️ A plugin source can install code that runs in-process with the
> Meshpoint service's privileges, and a plugin's `setup.sh` runs as root.
> Only add sources whose author you trust.

## Layout

```
apps/<id>/      one folder per plugin  (folder name == plugin.toml `name`)
themes/<id>/    one folder per theme
repo.json       the browse catalog — generated, do not hand-edit
```

## For contributors

Drop a plugin under `apps/<id>/` (with its `plugin.toml`) or a theme under
`themes/<id>/` (with its `theme.json`), then regenerate the catalog:

```sh
python3 make-repo-json.py --write     # needs Python 3.11+, nothing else
```

It reads every `plugin.toml` / `theme.json`, warns about problems (a
folder that doesn't match its `name`, an unknown `provides`, a duplicate
id), and writes `repo.json`. Re-run it whenever you bump a version.

Meshpoint re-validates the real `plugin.toml` / `theme.json` when a plugin
is actually installed, so `repo.json` is only metadata for the browse
view — a stale or edited catalog can't smuggle anything in.

(If you have a Meshpoint checkout handy, `meshpoint plugin index <repo>
--write` does the same thing with its own validator.)

## Contents

| id | kind | what |
|----|------|------|
| `hello-world-github` | app | Minimal sidebar-page example (test plugin) |
| `hello-world-github-hook` | app | Minimal hook example (test plugin) |
| `offline-map` | app | Downloads OSM tiles for offline/emergency dashboard maps |
| `oled-display` | app | Drives a small I2C status OLED -- boot logo, live IP/sources/uptime, auto-blank |
| `bluetooth-scanner` | app | Nearby-BLE-device radar -- live sortable table of advertising devices |
| `rtlsdr` | app | RTL-SDR host page -- every RTL-SDR plugin below hooks a tab into it |
| `radio` | app | FM/AM/SSB broadcast & utility radio listener, with RDS on FM |
| `acars` | app | Aircraft VHF datalink (ACARS) decoding |
| `pocsag` | app | POCSAG pager decoding, 439.9875 MHz |
| `pagers` | app | POCSAG pager decoding, 172.45 MHz |
| `p2000` | app | Dutch emergency dispatch (P2000/FLEX) decoding |
| `rtl433` | app | Generic OOK/FSK decoder -- weather stations, TPMS, sensors, and more |
| `adsb` | app | Live ADS-B air traffic tracking with a map |
| `dab` | app | DAB/DAB+ digital radio |
| `dapnet` | app | DAPNET/POCSAG amateur-radio paging via a companion board over USB serial |
| `reticulum` | app | Native Reticulum/LXMF messaging -- peer roster, Peers/Messages/Send/Settings page |
| `reticulum-call` | app | Voice calls over Reticulum -- adds a Call tab to the Reticulum page |
| `reticulum-browser` | app | Full multi-tab NomadNet browser for Reticulum |
| `reticulum-dashboard` | app | Standalone live Reticulum activity dashboard (stats, map, peer ticker) |
| `raspberry-network` | app | Scan and connect to WiFi networks from the dashboard, no shell needed |
| `github-dark-theme` | theme | GitHub's dark default (Primer) |
| `github-light-theme` | theme | GitHub's light default (Primer) |
