# OLED Display plugin

Drives a small I2C status OLED (SSD1306-family) attached to the Pi: a
boot logo for a few seconds, then a periodically-refreshed status screen
-- IP address, which capture sources are actually running right now
(not just configured), and uptime. Auto-blanks after a configurable
timeout to avoid burn-in.

Built and tested against the OLED on a COTX-X3's carrier board (I2C
address `0x3D`), but nothing here is COTX-specific -- any Pi HAT/carrier
with a compatible I2C OLED at a known address should work.

## What's here

- **Boot logo** -- "MESHPOINT / starting..." for ~3 seconds once the
  service starts.
- **Live status** -- LAN IP + dashboard port, active capture source
  names (from the same live pipeline the topbar itself reflects), plus
  Reticulum's own peer count if that plugin is enabled -- Reticulum is a
  `service`, not a `CaptureSource`, so it never shows up in the
  pipeline's own source list no matter what. Reached in-process via
  `src.api.service_registry.live()` (every started plugin service,
  looked up by name), not a local HTTP call -- every plugin router is
  mounted `public=False`, so an unauthenticated local request to
  `/api/reticulum/status` just 401s. Uptime too. Refreshes on a
  configurable interval.
- **Burn-in protection** -- auto-blank after N minutes (0 = never).
- **Settings page** (Configuration → OLED Display) -- on/off, I2C
  address, controller variant (SSD1306 / SH1106 / SSD1309 -- cheap
  boards are sometimes mislabeled, same address range but an
  incompatible init sequence), blank timeout, refresh interval, and a
  **live preview** of exactly what's currently on the physical screen
  (`GET /api/oled-display/preview.png`, the same PNG the service last
  pushed to the panel).

## What's not here (yet)

- **No wake mechanism.** This is a passive status panel -- once it's
  auto-blanked after the timeout, there's currently no way to bring it
  back except a service restart or a settings change. A future version
  could wire up a physical button (if the board has one broken out) or
  wake-on-state-change (e.g. light back up when a capture source drops).
- **No anti-burn-in pixel shifting.** Full-blank-after-timeout is the
  only mitigation right now; periodically nudging drawn content by a
  pixel or two would be gentler on the panel for boards left running
  with the display always on.
- **Single fixed status layout.** No rotation between multiple screens
  (e.g. a dedicated "packet stats" or "RF signal" screen) -- one status
  view at a time.

## Setup

```
sudo meshpoint plugin setup oled-display
```

Installs `luma.oled` + `pillow` into Meshpoint's own venv (no system apt
packages needed -- `luma.oled` pulls in its own pure-Python `smbus2` for
I2C). Then enable it:

```yaml
plugins:
  oled-display:
    enabled: true
```

## Finding your display's I2C address

Before enabling, confirm what's actually on your I2C bus:

```bash
i2cdetect -y 1
```

`0x3C` and `0x3D` are the two common SSD1306-family addresses (often a
solder-jumper choice on the display module itself). Whatever address
shows up, set it under `plugins.oled-display.i2c_address` in
`config/local.yaml`.

## Config keys (`plugins.oled-display.*`)

| Key | Default | Notes |
|---|---|---|
| `enabled` | `true` | Master on/off. |
| `i2c_address` | `"0x3D"` | Hex string, e.g. `"0x3C"`. |
| `driver` | `ssd1306` | `ssd1306` \| `sh1106` \| `ssd1309`. |
| `width` / `height` | `128` / `64` | Most small OLEDs are 128×64 or 128×32. |
| `blank_after_minutes` | `30` | `0` disables auto-blank entirely. |
| `refresh_seconds` | `5` | How often the status screen redraws. |

Settings changes apply on the next service restart -- this is a
lifespan-managed background service (same seam as the Reticulum plugin's
`LxmfService`), not a start/stop-able subprocess.
