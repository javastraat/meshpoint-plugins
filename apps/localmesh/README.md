# LocalMesh plugin

A [LocalMesh](https://www.localmesh.nl) receiver (Heltec V4 with LocalMesh companion firmware) on the Meshpoint's USB
port. LocalMesh uses 62.5 kHz, which the SX1302 concentrator cannot receive, so a USB receiver is the only way in.

What it does. Read-only: it never transmits on the radio and never changes the receiver's settings; it only asks the
receiver what it knows (status, channels, stored messages, member list, neighbours).

- **Packet log:** every packet the receiver hears, under protocol `localmesh`, with its type (channel, direct, advert,
  status, ...), the sender (member number, `server`, or `repeater <node>`), counter, hops, previous hop, path, signal
  and the receiver's decision (forward, deliver, replay, blocked, ...). The contents stay encrypted.
- **Topbar chip:** LocalMesh, connected or not, node number and battery. Click it for the LocalMesh page.
- **LocalMesh page** (sidebar, Networks): a card per receiver at the top, then tabs:
  - **Members:** everyone the receiver knows of (its member list, adverts and the packets it heard): name, member
    number, last heard, packets, hops, signal, position (OpenStreetMap link), blocked or national.
  - **Messages:** channel messages the receiver has the key for, and direct messages to the receiver. At connect the
    plugin fetches the board's last 32 messages, so they survive a Meshpoint restart. The time is the board's when its
    clock is set, otherwise when Meshpoint received the message.
  - **Activity:** every packet heard, newest first: type, sender, counter, hops, path, signal and the receiver's
    decision (green passed on or delivered, amber dropped as normal mesh traffic, red refused).
  - **Receiver:** node, member, role, firmware, board, radio settings, transmit power, battery, uptime, packets
    heard/rejected/sent, last signal, noise floor, airtime used; its channels and its neighbours (repeaters heard
    directly).
  - **Settings** (admin): the receiver list, with a picker of the connected USB devices.
  Members, Messages and Activity have a search box. The lists live in memory (300 messages and packets per receiver).
  Send follows.

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

- `backend/protocol.py`: companion protocol v1 (SLIP, the read commands, INFO/STATUS/RX_LOG/MESSAGE/ADVERT and the
  list replies), stdlib only.
- `backend/listener.py`: the serial capture source (thread, reconnects by itself, one command at a time).
- `backend/decode.py`: RX_LOG to a Meshpoint Packet.
- `backend/routes.py`: `GET /api/localmesh/status`, `/members`, `/messages`, `/activity`.
- `backend/views.py`: the Members, Messages and Activity lists, merged over all receivers.
- `backend/settings_routes.py`: `GET/PUT /api/localmesh/settings` (saving is admin-only and audited).
- `backend/state.py`: the receiver list, saved to `plugins.localmesh` in `local.yaml`.
- `frontend/localmesh_panel.js`: the page and its tabs; `localmesh_settings_tab.js`: the Settings tab.
- `frontend/localmesh_topbar_chip.js`: the chip.

The protocol is documented in the LocalMesh firmware repo, `docs/protocol-en.md`.
