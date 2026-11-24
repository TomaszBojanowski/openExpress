"""Large files of a device over MTP read whole: copied by one copy of gvfs into a temporary file
on this computer and read there, instead of read as a stream. gvfs reads a stream in answers of
at most 256 KiB, each a transaction of the device; some watches take tens of milliseconds for
each above its transfer, which one copy does not (on a fēnix 8 a stream reads about 2.6 MB/s,
a copy about 12.7 MB/s). Below COPY_FROM the start of a copy is not worth it; a drive and a
folder of this computer are read as a stream.

The temporary files are in the cache (paths.cache_dir() / FOLDER, open to this user only),
each removed as soon as it is read; those a process left (it ended during a copy) are removed
when the next one starts (remove_leftovers). They carry no name of the device's files."""

from __future__ import annotations

import functools
import itertools
import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from openexpress import applog, cache, paths
from openexpress.device.storage import CopyNotStarted, DeviceError, Storage

log = logging.getLogger(__name__)

COPY_FROM = 4 * 1024 * 1024  # bytes: a file from this size is copied whole, not streamed
ROOM = 100_000_000  # bytes the cache keeps free beyond the copy
FOLDER = "device-reads"
LEFT_AFTER_S = 3600.0  # a temporary file not written to for this long is a leftover
_numbers = itertools.count(1)


def folder() -> Path:
    return paths.cache_dir() / FOLDER


def _made_folder() -> Path:
    where = folder()
    paths.make_private_dir(where)  # whatever a copy makes in it, only this user reads it
    return where


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # there, of another user
    return True


def remove_leftovers() -> None:
    """The temporary files left by runs that ended during a copy, removed: those not written
    to for LEFT_AFTER_S, and, outside the Flatpak, those of processes that are gone (each
    instance of the Flatpak numbers its processes on its own, so there a number says nothing
    of another instance's copy under way). One line in the log when there were any."""
    try:
        entries = list(folder().iterdir())
    except OSError:
        return  # no folder yet: nothing left
    by_process = not paths.in_flatpak()
    removed = 0
    for entry in entries:
        pid = entry.name.split("-", 1)[0]
        try:
            stale = (time.time() - entry.stat().st_mtime > LEFT_AFTER_S
                     or (by_process and (not pid.isdigit() or not _alive(int(pid)))))
            if stale:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("whole reads: %d temporary file(s) left by an earlier run removed", removed)


def whole_file(storage: Storage, real_path: str, size: int | None,
               stream: Callable[[], bytes] | None = None, told=None) -> bytes:
    """The bytes of the file at this real path of `storage`. From COPY_FROM on a device that
    copies (GioStorage.copy_to_local), with room for it in the cache: copied whole into a
    temporary file and read there; otherwise, and when the copy is refused before its first
    byte, `stream()` (the caller's own read; without one, storage.read of the path). A copy
    that fails after it began is not read again as a stream: its DeviceError passes on.
    `told(done, total)`: the bytes copied (a `told` that raises stops the copy). The cache is
    held while a copy is in it."""
    if stream is None:
        stream = functools.partial(storage.read, real_path)
    copy = getattr(storage, "copy_to_local", None)
    if copy is None or size is None or size < COPY_FROM:
        return stream()
    with cache.using():
        where = _made_folder()
        free = shutil.disk_usage(where).free
        if free < size + ROOM:
            log.info("whole reads: %s free in the cache, %s needed for a copy of %s; read as a "
                     "stream", applog.size(free), applog.size(size + ROOM), applog.size(size))
        else:
            target = where / f"{os.getpid()}-{next(_numbers)}.part"
            try:
                copy(real_path, target, size, told)
                return target.read_bytes()
            except CopyNotStarted as e:
                if not getattr(e, "said", False):  # said once a process where it is said
                    # What happens first, why last: the log's filter takes all after a path
                    # for it, and the reason may end in one.
                    log.info("whole reads: read as a stream, the copy was refused before it "
                             "began: %s", e)
            except OSError as e:  # the copy on this computer
                raise DeviceError(f"{real_path}: {applog.system_words(e) or e}") from e
            finally:
                target.unlink(missing_ok=True)
    return stream()
