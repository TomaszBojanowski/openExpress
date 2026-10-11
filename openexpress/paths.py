"""Local application directories (XDG)."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "openexpress"
LEGACY_NAME = "garmin-linux"  # name used before the first public release


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def _app_dir(var: str, default: str) -> Path:
    """XDG directory for the app; moves data from the pre-release name if needed."""
    base = _xdg(var, default)
    new, old = base / APP_NAME, base / LEGACY_NAME
    if not new.exists() and old.is_dir():
        try:
            old.rename(new)
        except OSError:
            return old
    return new


def config_dir() -> Path:
    return _app_dir("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _app_dir("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _app_dir("XDG_CACHE_HOME", ".cache")


def device_dir(unit_id: int) -> Path:
    return data_dir() / "devices" / str(unit_id)


def default_backup_dir() -> Path:
    try:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        docs = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
        if docs:
            return Path(docs) / "Garmin" / "Backups"
    except (ImportError, ValueError):
        pass
    return Path.home() / "Documents" / "Garmin" / "Backups"


def write_private(path: Path, data: str) -> None:
    """Atomically write a file readable only by its owner (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)
