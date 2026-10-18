"""Local application directories (XDG)."""

from __future__ import annotations

import configparser
import os
import subprocess
import tempfile
from pathlib import Path

APP_NAME = "openexpress"
APP_ID = "io.github.tomaszbojanowski.OpenExpress"
FLATPAK_INFO = Path("/.flatpak-info")
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


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def flatpak_build() -> tuple[str | None, str | None] | None:
    """(branch, the first 12 characters of the app's commit) of this Flatpak, from
    /.flatpak-info; None outside the Flatpak or when the file cannot be read."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        if not parser.read(FLATPAK_INFO, encoding="utf-8") or not parser.has_section("Instance"):
            return None
        instance = parser["Instance"]
        commit = instance.get("app-commit") or None
        return instance.get("branch") or None, commit[:12] if commit else None
    except (configparser.Error, OSError, UnicodeError):
        return None


def flatpak_origin() -> str | None:
    """The remote this Flatpak was installed from ("openexpress"; a bundle gets
    "…-origin"), asked of the host; None outside the Flatpak and on any failure."""
    if not in_flatpak():
        return None
    # With two branches installed (e.g. master and test), a question without the branch is
    # ambiguous and fails: this one's branch goes with the app ID when it can be read.
    build = flatpak_build()
    ref = f"{APP_ID}//{build[0]}" if build and build[0] else APP_ID
    # Only reads the metadata of the installation; besides copying files to the device
    # (gio copy), the one use of flatpak-spawn.
    try:
        answer = subprocess.run(
            ["flatpak-spawn", "--host", "flatpak", "info", "--show-origin", ref],
            capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if answer.returncode != 0:
        return None
    return answer.stdout.strip() or None


def flatpak_source() -> tuple[str | None, str | None]:
    """(origin, short commit) of this Flatpak, for the report and the log."""
    build = flatpak_build()
    return flatpak_origin(), build[1] if build else None


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


PRIVATE_DIR = 0o700


def is_safe_component(name: str) -> bool:
    """Whether a name from a device or a server can be one component of a path on this
    computer: not empty, not "." or "..", no separator (so not absolute either), no NUL."""
    return (bool(name) and name not in (".", "..")
            and not any(c in name for c in ("/", "\\", "\0")))


def is_inside(path: Path, folder: Path) -> bool:
    """Whether `path`, links and ".." resolved, lies within `folder` (also resolved)."""
    return path.resolve().is_relative_to(folder.resolve())


def make_dir(path: Path) -> None:
    """Makes `path` and the folders above it. The app's own folder for the settings, the data
    or the log (the login, what came from the device, what was done), when it is made here,
    is its owner's alone. Folders that exist keep their mode; those above the app's
    (~/.config, …) get the usual one. The cache and the backups are made as before."""
    for root in (config_dir(), data_dir(), state_dir()):
        if path == root or root in path.parents:
            _make_private(root)
            break
    path.mkdir(parents=True, exist_ok=True)


def _make_private(root: Path) -> None:
    if root.is_dir():
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    try:
        root.mkdir(mode=PRIVATE_DIR)
    except FileExistsError:
        return  # made by another process in the meantime: left as it is
    os.chmod(root, PRIVATE_DIR)  # the umask cuts mkdir's mode; this folder was just made


def write_private(path: Path, data: str) -> None:
    """Atomically write a file readable only by its owner (0600)."""
    make_dir(path.parent)
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
