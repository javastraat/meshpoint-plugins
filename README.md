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
meshpoint.json  the browse catalog — generated, do not hand-edit
```

## For contributors

After adding/changing a plugin or bumping a version, regenerate the catalog
from a Meshpoint checkout:

```sh
meshpoint plugin index /path/to/this/repo --write
```

Meshpoint re-validates the real `plugin.toml` / `theme.json` on install, so
`meshpoint.json` is only metadata for the browse view.

## Contents

| id | kind | what |
|----|------|------|
| `hello-world-github` | app | Minimal sidebar-page example (test plugin) |
