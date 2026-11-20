"""LocalMesh Bot: answers commands (.help, .ping, .trace and your own) on chosen LocalMesh channels, through a receiver
of the localmesh plugin. Settings and a log in an extra Bot tab on the LocalMesh page.

Imports are deferred into register() so bot.py's tests run without FastAPI or Meshpoint.
"""

from __future__ import annotations


def _current_saved_config() -> dict:
    """plugins.localmesh-bot as it is on disk now (save_section_to_yaml merges one level deep: keep "enabled")."""
    import yaml

    from src.config import _get_local_yaml_path  # noqa: SLF001 -- same as the localmesh and dapnet plugins

    path = _get_local_yaml_path()
    if not path.exists():
        return {}
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    section = (data.get("plugins") or {}).get("localmesh-bot")
    return dict(section) if isinstance(section, dict) else {}


def _save(values: dict) -> None:
    from src.config import save_section_to_yaml

    current = _current_saved_config()
    current.update(values)
    save_section_to_yaml("plugins", {"localmesh-bot": current})


def register(reg) -> None:
    from . import routes
    from .service import BotService

    service = BotService(reg.config, save=_save)
    service.attach()
    routes.init_routes(service)
    reg.add_router(routes.router)
