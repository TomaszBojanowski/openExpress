"""The cache of openExpress, paths.cache_dir(): downloaded maps and software updates,
pictures, and files staged for copying to the device, all of which can be downloaded again.
It is counted and cleared in four parts (CATEGORIES), all or some.

Each installation has its own: ~/.cache/openexpress for the version from source, a folder
below ~/.var/app/<app ID>/cache (its XDG_CACHE_HOME) in the Flatpak."""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from openexpress import paths
from openexpress.errors import OrdinaryStop, UserFacingError
from openexpress.i18n import N_, _

log = logging.getLogger(__name__)

# The parts of the cache, in the order they are shown, with the folders of each (the keys are
# those of `cache --clear --only`); nothing else in the cache folder is counted or deleted.
CATEGORIES = {"maps": ("maps",), "updates": ("software",), "pictures": ("images",),
              # Files on their way to the device (the Flatpak), and what older versions left.
              "temp": ("transfer", "tmp")}
# TRANSLATORS: the parts of the cache, as `openexpress cache` and the preferences list them
CATEGORY_NAMES = {"maps": N_("Maps"), "updates": N_("Software updates"),
                  "pictures": N_("Pictures and icons"), "temp": N_("Temporary files")}


# The lock of the cache, in its folder; a clearing never deletes it. Shared while files of the
# cache are downloaded, written or read for an install (any process: the window, the command
# line, the background service), exclusive while it is cleared.
LOCK_NAME = "cache.lock"


class CacheError(UserFacingError):
    """The cache could not be cleared; the message says why."""


class CacheBusy(CacheError, OrdinaryStop):
    """The cache is in use (a download or an install is running): it is not cleared now."""

    log_reason = "the cache is in use"


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
    sizes: dict[str, int]  # bytes in each part counted (CATEGORIES), in their order
    files: dict[str, int]  # how many files in each
    # (bytes, files) of the pictures a clearing of all of the cache keeps (kept_pictures);
    # counted with all of it only, inside "pictures".
    kept: tuple[int, int] = (0, 0)

    @property
    def total(self) -> int:
        return sum(self.sizes.values())


def chosen(only: Iterable[str] | None) -> list[str]:
    """The parts named (each once, in the order of CATEGORIES); all of them for None. A name
    that is no part is refused (ValueError)."""
    if only is None:
        return list(CATEGORIES)
    names = set(only)
    unknown = sorted(names - set(CATEGORIES))
    if unknown:
        raise ValueError(f"not a part of the cache: {', '.join(map(repr, unknown))}")
    return [name for name in CATEGORIES if name in names]


def device_picture_name(part_number: str) -> str:
    """The name of the product picture of a model in the folder of pictures (the window
    keeps it there: gui/pictures.device_picture_path)."""
    safe = "".join(c for c in part_number if c.isalnum() or c in "-_") or "unknown"
    return f"device-{safe}.png"


def kept_pictures() -> set[str]:
    """The pictures a clearing of all of the cache keeps: those of the devices this
    installation remembers (devices.json). A device not on the account may have its picture
    from nowhere else any more (unit-info gives none for some); the trash of the pictures
    (--only pictures) still deletes them."""
    from openexpress import known_devices

    return {device_picture_name(d.part_number) for d in known_devices.load().values()
            if d.part_number}


def _kept_files(folder: Path, names: set[str]) -> list[Path]:
    """Those of `names` that are regular files in `folder` (a link is not kept)."""
    return [folder / name for name in sorted(names)
            if (folder / name).is_file() and not (folder / name).is_symlink()]


def usage(only: Iterable[str] | None = None) -> CacheUsage:
    """How big each part of the cache is (all, or those named), in bytes and files, and for
    all of it what a clearing keeps. A folder that is not there is 0; nothing is made or
    changed."""
    root = paths.cache_dir()
    sizes, files = {}, {}
    for name in chosen(only):
        counted = [_count(root / folder) for folder in CATEGORIES[name]]
        sizes[name] = sum(size for size, _n in counted)
        files[name] = sum(n for _size, n in counted)
    kept = (0, 0)
    if only is None:
        found = _kept_files(root / CATEGORIES["pictures"][0], kept_pictures())
        kept = (sum(p.lstat().st_size for p in found), len(found))
    return CacheUsage(root, sizes, files, kept)


def clear(only: Iterable[str] | None = None) -> CacheUsage:
    """Delete what is in the folders of the parts named (all for None, but the pictures of the
    devices remembered: kept_pictures); returns what was deleted, whose total is what was
    freed. Raises CacheBusy while anything uses any part of the cache. The folders, cache.lock
    and anything else in the cache folder stay; nothing is deleted through a symbolic link."""
    names = chosen(only)
    keep = kept_pictures() if only is None else set()
    with clearing():
        before = usage(only)
        for name in names:
            for folder_name in CATEGORIES[name]:
                _empty(before.root / folder_name, keep if name == "pictures" else set())
    kept_bytes, kept_number = before.kept
    if only is None:
        log.info("cache: pictures: %d of the devices remembered kept", kept_number)
    sizes, files = dict(before.sizes), dict(before.files)
    if "pictures" in sizes:
        sizes["pictures"] -= kept_bytes
        files["pictures"] -= kept_number
    return CacheUsage(before.root, sizes, files)


def _empty(folder: Path, keep: set[str]) -> None:
    """Delete what is in `folder`, but the regular files named in `keep`."""
    if folder.is_symlink() or not folder.is_dir():
        return
    kept = set(_kept_files(folder, keep))
    for entry in folder.iterdir():
        if entry in kept:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)  # removes links inside as links
            else:
                entry.unlink()
        except OSError as e:
            raise CacheError(_("Could not delete {path}: {error}").format(
                path=entry, error=e.strerror or e)) from None


def _count(folder: Path) -> tuple[int, int]:
    """(bytes, number) of the regular files below `folder`. Symbolic links are neither
    followed nor counted: a folder that is a link to elsewhere counts as nothing."""
    if folder.is_symlink() or not folder.is_dir():
        return 0, 0
    total = number = 0
    for here, _dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            try:
                st = os.lstat(os.path.join(here, name))
            except OSError:
                continue  # gone meanwhile (e.g. a staged file just deleted)
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
                number += 1
    return total, number
