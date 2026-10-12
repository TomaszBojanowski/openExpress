"""Local application directories (XDG)."""

from __future__ import annotations

import os
import tempfile
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
            # Another process may have migrated it in the meantime.
            if new.is_dir():
                return new
            return old if old.is_dir() else new
    return new


def config_dir() -> Path:
    return _app_dir("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _app_dir("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _app_dir("XDG_CACHE_HOME", ".cache")


def state_dir() -> Path:
    """Where the log file is kept (in the Flatpak: under ~/.var/app/…/.local/state)."""
    return _app_dir("XDG_STATE_HOME", ".local/state")


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
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if fd != -1:
            os.close(fd)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
