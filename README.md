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
