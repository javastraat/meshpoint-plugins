# LocalMesh Repeaters plugin

The LocalMesh repeaters and what they report about themselves, on a sidebar page in the Radio section: battery, uptime,
noise floor, airtime, packets accepted and refused (with the reasons), neighbours with SNR, firmware and restarts.

- **Listens only.** Every repeater sends a status every hour by itself; nothing is polled and nothing is sent.
- **Needs** the [localmesh](../localmesh) plugin with a **status key** (LocalMesh page, Settings, Repeater status).
- **All repeaters appear by themselves:** those in the server's certificates and any that sent a status.
- **Unverified:** a status key opens a status but does not prove who sent it. Nothing about members is kept.
- **Last heard, signal, route, firmware and recent packets** come from the localmesh plugin, also without a status.
- **Lamp:** green = heard in the last 2.5 hours, amber = late, red = silent for 6 hours, grey = not heard.
- **Restarts** are worked out from the uptime. The history is kept 30 days (Settings: 1-365) in Meshpoint's database
  (`lmr_*` tables), so it survives a restart and an update.

`backend/service.py` takes the statuses (localmesh's `hooks.py`), `store.py` keeps them, `routes.py` serves
`/api/localmesh-repeaters/*`. Tests: `backend/tests/` (standard library only).
