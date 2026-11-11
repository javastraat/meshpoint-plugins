# LocalMesh plugin

A [LocalMesh](https://www.localmesh.nl) receiver (Heltec V4 with LocalMesh companion firmware) on the Meshpoint's USB
port. LocalMesh uses 62.5 kHz, which the SX1302 concentrator cannot receive, so a USB receiver is the only way in.

What it does. By itself it only listens and asks the receiver what it knows (status, channels, stored messages, member
list, neighbours). It transmits only from the Send tab, by an admin, within the receiver's own airtime budget.

- **Packet log:** every packet the receiver hears, under protocol `localmesh`, with its type (channel, direct, advert,
  status, ...), the sender (member number, `server`, or `repeater <node>`), counter, hops left, previous hop, path, signal
  and the receiver's decision (forward, deliver, replay, blocked, ...). The contents stay encrypted.
- **Topbar chip:** LocalMesh, connected or not, node number and battery. Click it for the LocalMesh page.
- **LocalMesh page** (sidebar, Networks): a card per receiver at the top, then tabs:
  - **Members:** everyone the receiver knows of (its member list, adverts and the packets it heard): name, member
    number, last heard, packets, hops left, signal, position (OpenStreetMap link), blocked or national; the
    receiver itself is marked "this Meshpoint".
  - **Messages:** channel messages the receiver has the key for, and direct messages to the receiver. At connect the
    plugin fetches the board's last 32 messages, so they survive a Meshpoint restart. The time is the board's when its
    clock is set, otherwise when Meshpoint received the message.
  - **Activity:** every packet heard, newest first: type, sender, counter, hops left, path, signal and the receiver's
    decision (green passed on or delivered, amber dropped as normal mesh traffic, red refused).
  - **Send** (admin): a message to a channel the receiver has the key for, or a direct message to a member (also from
    a member's side panel), with a byte counter (a channel message carries the receiver's name in front, in the same
    155 bytes; a direct message fits 149). The receiver's name on the network (it broadcasts it 5 s after a change,
    and every 24 h) and "Broadcast name now". Recently sent, with status: sent, repeated (a repeater was heard
    passing it on), delivered or not delivered (direct messages). Sent messages also show on the Messages tab.
    Every send is in the audit log (where to and how many bytes, not the text).
  - **Receiver:** node, member, role, firmware, board, radio settings, transmit power, battery, uptime, packets
    heard/rejected/sent, last signal, noise floor, airtime used; its channels and its neighbours (repeaters heard
    directly).
  - **Settings** (admin): the receiver list, with a picker of the connected USB devices.
  Hops in LocalMesh count down: a packet starts with the network's maximum (4) and every repeater takes one off, so
  4 means it came straight from the sender. The "Name: " the sender's board puts in front of a channel message is
  left out of the text (the name is under From).
  Members, Messages and Activity have a search box; click a row for everything about it: a member opens the
  side panel (like a Meshtastic node or a Reticulum peer: status, last heard, signal, position, its recent messages
  and packets); a message or packet opens a popup (the full message, or the decision explained, the path with the
  SNR per hop, routed or flood). A member without an
  advert gets the name in front of their messages ("Einstein: ..."), as the app sends it. The lists live in memory (300 messages and packets per receiver).
  The name set on the Send tab is kept with the receiver in `local.yaml` (the board does not report it).

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
- `backend/send_routes.py`: `POST /api/localmesh/send`, `PUT /api/localmesh/name`, `POST /api/localmesh/advert` (admin,
  audited): the only routes that transmit.
- `backend/settings_routes.py`: `GET/PUT /api/localmesh/settings` (saving is admin-only and audited).
- `backend/state.py`: the receiver list, saved to `plugins.localmesh` in `local.yaml`.
- `frontend/localmesh_panel.js`: the page and its tabs; `localmesh_settings_tab.js`: the Settings tab;
  `localmesh_member_drawer.js`: the member side panel; `localmesh_send_tab.js`: the Send tab.
- `frontend/localmesh_topbar_chip.js`: the chip; `localmesh.css`: scrolling, column widths, clickable rows.

The protocol is documented in the LocalMesh firmware repo, `docs/protocol-en.md`.
