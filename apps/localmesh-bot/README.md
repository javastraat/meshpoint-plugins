# LocalMesh Bot plugin

A bot for LocalMesh that answers commands on the channels you choose, through a receiver of the
[localmesh](../localmesh) plugin. It has no page of its own: it adds a **Bot** tab to the LocalMesh page (admin only).

| Command | Answer |
|---|---|
| `.help` | the list of commands |
| `.ping` | `pong, heard directly, -7 dBm / 12 dB`, or through repeaters `pong, 2 hops, last hop NL-AMS-1057 (42978) -36 dBm / 11.75 dB` |
| `.trace` | the repeaters the question came through, in order (`2 hops: NL-AMS-1076 (58159) → NL-AMS-1057 (42978), last hop -36 dBm / 11.75 dB`), or `heard directly, no repeater, -7 dBm / 12 dB` |
| your own | a fixed answer per command (`.weer` → "Kijk even naar buiten"), set in the tab |

Answers start with `@<name>` of whoever asked, when their app put its name in front of the question.

**The signal is the last hop's.** The receiver only measures the last transmitter: the asker when it was heard
directly, otherwise the last repeater -- the same for everyone behind that repeater. The answers say which it is. The
signal of the earlier hops is not in a message; only a route test (TRACE) measures it.

## The Bot tab

- **Bot on/off**, the **receiver** it uses, and its **name**. The receiver's firmware puts its own name in front of
  every channel message it sends, so the bot always answers under the receiver's name; "Change" sets the receiver's
  name (the same `PUT /api/localmesh/name` as the Send tab).
- **Channels** it listens on: tick the channels the receiver has a key for, or add any number 0-254.
- **Prefix** (`.` by default), **cooldown per member** (20 s) and **answers per hour** in all (30).
- **Commands**: your own command → answer pairs (1-16 of `a-z 0-9 _ -`, answers up to 140 characters).
- **Log**: the last 50 questions and what the bot did (answered, or why not: cooldown, hourly limit, unknown command,
  too old, our own name in front).

## Staying friendly to the band

Every answer is a flood through every repeater, and the band allows 10 % airtime. So the bot:

- answers only on the chosen channels, never a direct message (for now);
- never answers its own messages, nor a message with its own name in front (no loops with a second receiver of the
  same name);
- never answers an old message: after a restart the receiver hands over its whole buffer again (older than 120 s = no
  answer);
- gives no answer to an unknown command;
- keeps to one answer per member per cooldown and a maximum per hour;
- cuts an answer to what fits behind the receiver's name (151 bytes in all, firmware 8e12ea5).

## How it hooks into localmesh

- **Messages**: the localmesh plugin keeps a small registry in `sys.modules["meshpoint_localmesh_hooks"]`
  (`backend/hooks.py` there): its receivers, and handlers that get every new message on the event loop. This plugin
  adds its handler there (`backend/service.py`). No import between the plugins and no load order: whichever loads
  first creates the registry.
- **The route** for `.trace` comes from the MESSAGE frame's v2 tail (firmware 15-11), which the localmesh plugin reads
  since 20-11.
- **The tab**: `window.registerPageHook({host: 'localmesh', label: 'Bot', ...})`; the LocalMesh page turns every hook
  for `localmesh` into an extra tab after Settings.

Settings are saved in `plugins.localmesh-bot` in `local.yaml`; a change of the settings goes to the audit log.

## Files

```
plugin.toml                 provides routes + hook, requires localmesh
backend/__init__.py         register(): the service, its routes, saving the settings
backend/bot.py              the decisions (pure Python): channels, loops, age, commands, limits, the answers
backend/service.py          the running bot: the handler, sending, the log, the receivers for the tab
backend/routes.py           GET/PUT /api/localmesh-bot/config, GET /api/localmesh-bot/log (admin)
backend/tests/              test_bot.py, test_service.py (stdlib), test_routes.py (FastAPI + Meshpoint's src)
frontend/localmesh_bot.js   the Bot tab
frontend/localmesh_bot.css
```
