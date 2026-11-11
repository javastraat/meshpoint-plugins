# LocalMesh plugin

A [LocalMesh](https://www.localmesh.nl) receiver (Heltec V4 with LocalMesh companion firmware) on the Meshpoint's USB
port. LocalMesh uses 62.5 kHz, which the SX1302 concentrator cannot receive, so a USB receiver is the only way in.

What it does (read-only, it never sends or changes anything on the receiver):

- **Packet log:** every packet the receiver hears, under protocol `localmesh`, with its type (channel, direct, advert,
  status, ...), the sender (member number, `server`, or `repeater <node>`), counter, hops, previous hop, path, signal
  and the receiver's decision (forward, deliver, replay, blocked, ...). The contents stay encrypted.
- **Topbar chip:** LocalMesh, connected or not, node number and battery.
- **LocalMesh page** (sidebar, Networks): a card per receiver at the top, then tabs:
  - **Receiver:** node, member, role, firmware, board, radio settings, transmit power, battery, uptime, packets
    heard/rejected/sent, last signal, noise floor, airtime used.
  - **Settings** (admin): the receiver list, with a picker of the connected USB devices.
  Members, Messages, Activity and Send follow. Messages and adverts are already kept in memory for them.

## Setup

1. Flash the board with the LocalMesh companion firmware and make it a member (web flasher, or `tools/testnet.py add
   companion <port>` on a test network). Over USB the receiver sends its full packet log.
2. Plug it into the Meshpoint and turn the plugin on (Settings → Plugins).
3. On the LocalMesh page, Settings tab: add a receiver, pick the board from the list (an `Espressif` entry with the
   board's MAC address; that `/dev/serial/by-id/...` name stays the same whichever USB port the board is in), give it
   a label (`base`), save, and restart the service (Settings → System). Or put it in `local.yaml` yourself:

   ```yaml
   plugins:
     localmesh:
       enabled: true
       devices:
         - serial_port: /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_F8:5B:1B:A7:1A:8C-if00
           label: base
   ```

4. With MeshCore USB auto-detect on, pin the MeshCore companion to its own port, so it does not
   probe the LocalMesh board.

pyserial is already on every Meshpoint (the core serial sources use it); no setup script needed.

## Layout

- `backend/protocol.py`: companion protocol v1 (SLIP, frames, INFO/STATUS/RX_LOG/MESSAGE/ADVERT), stdlib only.
- `backend/listener.py`: the serial capture source (thread, reconnects by itself).
- `backend/decode.py`: RX_LOG to a Meshpoint Packet.
- `backend/routes.py`: `GET /api/localmesh/status`.
- `backend/settings_routes.py`: `GET/PUT /api/localmesh/settings` (saving is admin-only and audited).
- `backend/state.py`: the receiver list, saved to `plugins.localmesh` in `local.yaml`.
- `frontend/localmesh_panel.js`: the page and its tabs; `localmesh_settings_tab.js`: the Settings tab.
- `frontend/localmesh_topbar_chip.js`: the chip.

The protocol is documented in the LocalMesh firmware repo, `docs/protocol-en.md`.
