#!/usr/bin/env python3
"""Generate repo.json for this plugin repo.

Drop your plugins under apps/<id>/ (each with a plugin.toml) and your
themes under themes/<id>/ (each with a theme.json), then run:

    python3 make-repo-json.py            # print to stdout
    python3 make-repo-json.py --write    # write repo.json

Meshpoint re-reads and re-validates the real plugin.toml / theme.json when
a plugin is installed, so repo.json is only the browse catalog --
this script just keeps it in sync.

Needs Python 3.11+ (uses the stdlib `tomllib`). Nothing else -- no
Meshpoint checkout required.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")
_KNOWN_PROVIDES = {
    "listener", "routes", "panel", "sidebar", "hook", "capture", "protocol",
    "topbar", "service",
}


def _warn(msg: str) -> None:
    print(f"  warning: {msg}", file=sys.stderr)


def _plugin_entry(folder: Path) -> dict | None:
    try:
        data = tomllib.loads((folder / "plugin.toml").read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _warn(f"apps/{folder.name}: can't read plugin.toml ({exc})")
        return None

    name = data.get("name")
    if not isinstance(name, str) or not _SLUG.match(name):
        _warn(f"apps/{folder.name}: 'name' must be a slug [a-z0-9-]")
        return None
    if name != folder.name:
        _warn(f"apps/{folder.name}: 'name' is {name!r} -- rename the folder to match")
        return None

    version = str(data.get("version") or "").strip()
    if not version:
        _warn(f"apps/{name}: missing 'version'")
        return None

    api = data.get("meshpoint_api")
    if not isinstance(api, int) or isinstance(api, bool) or api < 1:
        _warn(f"apps/{name}: 'meshpoint_api' must be an integer >= 1")
        return None

    provides = data.get("provides") or []
    if not isinstance(provides, list) or not provides:
        _warn(f"apps/{name}: 'provides' must be a non-empty list")
        return None
    for p in provides:
        if p not in _KNOWN_PROVIDES:
            _warn(f"apps/{name}: unknown provides {p!r} (known: {sorted(_KNOWN_PROVIDES)})")

    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    deps = data.get("deps") if isinstance(data.get("deps"), dict) else {}

    return {
        "id": name,
        "kind": "app",
        "path": f"apps/{name}",
        "version": version,
        "meshpoint_api": api,
        "provides": [str(p) for p in provides],
        "description": str(meta.get("description") or ""),
        "author": str(meta.get("author") or ""),
        "homepage": str(meta.get("homepage") or ""),
        "has_setup": bool(deps.get("setup")),
    }


def _theme_entry(folder: Path) -> dict | None:
    try:
        raw = json.loads((folder / "theme.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        _warn(f"themes/{folder.name}: can't read theme.json ({exc})")
        return None
    if not isinstance(raw, dict):
        _warn(f"themes/{folder.name}: theme.json is not an object")
        return None

    tid = str(raw.get("id") or folder.name).strip()
    if not _SLUG.match(tid):
        _warn(f"themes/{folder.name}: id {tid!r} must be a slug [a-z0-9-]")
        return None
    return {
        "id": tid,
        "kind": "theme",
        "path": f"themes/{tid}",
        "version": str(raw.get("version") or "1.0.0"),
        "description": str(raw.get("description") or ""),
        "author": str(raw.get("author") or ""),
        "homepage": str(raw.get("homepage") or ""),
    }


def build() -> tuple[dict, int]:
    plugins, themes, skipped = [], [], 0

    for folder in sorted((ROOT / "apps").glob("*/")):
        if not (folder / "plugin.toml").is_file():
            continue
        entry = _plugin_entry(folder)
        if entry is None:
            skipped += 1
        else:
            plugins.append(entry)

    for folder in sorted((ROOT / "themes").glob("*/")):
        if not (folder / "theme.json").is_file():
            continue
        entry = _theme_entry(folder)
        if entry is None:
            skipped += 1
        else:
            themes.append(entry)

    # id uniqueness across both lists
    seen: set[str] = set()
    for e in plugins + themes:
        if e["id"] in seen:
            _warn(f"duplicate id {e['id']!r} -- Meshpoint will reject this catalog")
        seen.add(e["id"])

    doc: dict = {"meshpoint_repo": 1, "name": ROOT.name, "plugins": plugins}
    if themes:
        doc["themes"] = themes
    return doc, skipped


def main() -> int:
    write = "--write" in sys.argv or "-w" in sys.argv
    doc, skipped = build()
    rendered = json.dumps(doc, indent=2) + "\n"

    if write:
        (ROOT / "repo.json").write_text(rendered, "utf-8")
        print(
            f"wrote repo.json: {len(doc['plugins'])} plugin(s), "
            f"{len(doc.get('themes', []))} theme(s)"
            + (f", {skipped} skipped" if skipped else ""),
            file=sys.stderr,
        )
    else:
        sys.stdout.write(rendered)
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
