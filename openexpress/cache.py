"""The cache of openExpress: downloaded maps and software updates, pictures, and files staged
for copying to the device. Everything in it can be downloaded again.

It is paths.cache_dir(): ~/.cache/openexpress for the version from source; in the Flatpak,
where XDG_CACHE_HOME is ~/.var/app/<app ID>/cache, its own folder below that. Each
installation has its own cache."""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from openexpress import paths
from openexpress.i18n import N_, _

# The folders openExpress keeps in its cache, in the order they are shown; nothing else in
# the cache folder is counted (or, later, deleted).
FOLDERS = ("maps", "images", "software", "transfer")
# TRANSLATORS: the parts of the cache, as `openexpress cache` and the preferences list them
FOLDER_NAMES = {"maps": N_("Maps"), "images": N_("Pictures"),
                "software": N_("Software updates"), "transfer": N_("Transfer")}


# The lock of the cache, in its folder; a clearing never deletes it. Shared while files of the
# cache are downloaded, written or read for an install (any process: the window, the command
# line, the background service), exclusive while it is cleared.
LOCK_NAME = "cache.lock"


class CacheError(Exception):
    """The cache could not be cleared; the message says why."""


class CacheBusy(CacheError):
    """The cache is in use (a download or an install is running): it is not cleared now."""


@contextlib.contextmanager
def using():
    """Hold the cache while its files are downloaded, written or read for an install. Many
    may hold it at once; while a clearing runs, this waits for it (a clearing is short)."""
    with _lock_file() as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


@contextlib.contextmanager
def clearing():
    """Hold the cache alone, to clear it. Refused at once (CacheBusy) while anything uses it;
    no download starts until it ends."""
    with _lock_file() as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CacheBusy(_("The cache is in use (a download or an install is running). "
                              "Try again later.")) from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


@contextlib.contextmanager
def _lock_file():
    root = paths.cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    with open(root / LOCK_NAME, "a") as f:
        yield f


@dataclass
class CacheUsage:
    root: Path
    sizes: dict[str, int]  # bytes in each of FOLDERS

    @property
    def total(self) -> int:
        return sum(self.sizes.values())


def usage() -> CacheUsage:
    """How big each folder of the cache is, in bytes. A folder that is not there is 0; nothing
    is made or changed."""
    root = paths.cache_dir()
    return CacheUsage(root, {name: _size(root / name) for name in FOLDERS})


def clear() -> CacheUsage:
    """Delete what is in the four folders of the cache, holding it alone (CacheBusy while it
    is in use). The folders themselves stay, and so do cache.lock and anything else in the
    cache folder; nothing is deleted through a symbolic link, and a folder that is a link is
    left alone. Returns what was there: its total is what was freed."""
    with clearing():
        before = usage()
        for name in FOLDERS:
            folder = before.root / name
            if folder.is_symlink() or not folder.is_dir():
                continue
            for entry in folder.iterdir():
                try:
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry)  # removes links inside as links
                    else:
                        entry.unlink()
                except OSError as e:
                    raise CacheError(_("Could not delete {path}: {error}").format(
                        path=entry, error=e.strerror or e)) from None
    return before


def _size(folder: Path) -> int:
    """The bytes of the regular files below `folder`. Symbolic links are neither followed nor
    counted: a folder that is a link to elsewhere counts as nothing."""
    if folder.is_symlink() or not folder.is_dir():
        return 0
    total = 0
    for here, _dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            try:
                st = os.lstat(os.path.join(here, name))
            except OSError:
                continue  # gone meanwhile (e.g. a staged file just deleted)
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total
