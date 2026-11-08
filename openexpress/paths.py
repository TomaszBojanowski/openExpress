"""Local application directories (XDG)."""

from __future__ import annotations

import configparser
import contextlib
import fcntl
import os
import subprocess
import tempfile
from pathlib import Path

APP_NAME = "openexpress"
APP_ID = "io.github.tomaszbojanowski.OpenExpress"
FLATPAK_INFO = Path("/.flatpak-info")


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def _app_dir(var: str, default: str) -> Path:
    """XDG directory for the app."""
    return _xdg(var, default) / APP_NAME


def config_dir() -> Path:
    return _app_dir("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _app_dir("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _app_dir("XDG_CACHE_HOME", ".cache")


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def flatpak_data_dir() -> Path:
    """The data folder of the Flatpak of openExpress, as the rest of this user's files see it
    (outside the sandbox)."""
    return Path.home() / ".var" / "app" / APP_ID / "data" / "openexpress"


def other_install_dir() -> Path | None:
    """The data folder of the other install of openExpress on this computer, which this one
    reads and never writes: the Flatpak's for the version from source; None in the Flatpak,
    whose sandbox hides the folders of the version from source."""
    return None if in_flatpak() else flatpak_data_dir()


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


def host_command(*args: str) -> list[str]:
    """A command for the host, run from the Flatpak (flatpak-spawn --host); every one is made
    here. GIO_USE_VFS=gvfs: the session helper of flatpak before 1.14.5 (1.12 on Ubuntu 22.04)
    passes on its own GIO_USE_VFS=local, and the host's gio then sees mtp:// as no file of gvfs,
    so every copy to the device fails with "Operation not supported". Where gvfs is not
    installed, GIO says so on stderr and keeps the local VFS."""
    return ["flatpak-spawn", "--host", "--env=GIO_USE_VFS=gvfs", *args]


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
        answer = subprocess.run(host_command("flatpak", "info", "--show-origin", ref),
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


def shown_path(path: Path) -> str:
    """A path as it is shown to the user: with ~ for the home folder."""
    text, home = str(path), str(Path.home())
    return "~" + text[len(home):] if len(home) > 1 and text.startswith(home + os.sep) else text


def default_gpx_dir() -> Path:
    """Where `activities export` writes GPX files: next to the backups, in Garmin in the
    Documents folder (which the Flatpak may write)."""
    return default_backup_dir().parent / "GPX"


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
    is its owner's alone; the cache, the backups and the folders above the app's (~/.config,
    …) get the usual mode. Folders that exist keep theirs."""
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


@contextlib.contextmanager
def settings_locked():
    """One writer of settings.json at a time, in any process and any thread (each opens the
    lock file of its own); the system lets go of it when the process ends. Reading alone
    needs no lock: the file is replaced whole (write_private)."""
    folder = config_dir()
    make_dir(folder)
    with open(folder / "settings.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def write_private(path: Path, data: str | bytes) -> None:
    """Atomically write a file readable only by its owner (0600): text in UTF-8, bytes as
    they are."""
    make_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        binary = isinstance(data, bytes)
        with (os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding="utf-8")) as f:
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
