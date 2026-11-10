"""Freeing space on the device (the page Device Status, `openexpress diagnostics --free-space`):
its older memory dumps, copied to this computer first where they fit, and the files of updates
it did not install. Nothing else is ever removed: the newest dump stays (the only one of the
last crash), and an update file in the device's main folder waits to be installed (removing it
would cancel the update), so it is only shown.

The copies of the removed dumps are kept beside the diagnostic copies, apart from their 50 MB
(which hold the copies of the error logs the crash history reads), at most REMOVED_LIMIT a
device; a dump larger than what fits is removed without a copy, and the question says so.

What can be freed is told by one function, plan(), for the page and the command line alike.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openexpress import applog, diagnostics, paths
from openexpress.device.storage import DeviceError, DeviceNotResponding, Storage
from openexpress.errors import UserFacingError
from openexpress.i18n import _
from openexpress.progress import Step, say_to
from openexpress.updates import SYSTEM_FILES

log = logging.getLogger(__name__)

REMOVED_LIMIT = 100 * 1000 * 1000

# A memory dump on the device: its path, size and time (seconds; 0 or None: not known).
Dump = tuple[str, int, float]


def removed_folder(unit_id: int) -> Path:
    """Where the copies of the dumps removed from a device are: in the folder of its diagnostic
    copies (Copies on This Computer counts them, Open Folder shows them, Forget Device removes
    them), also for a test drive, whose diagnostic copies are there too."""
    return diagnostics.folder(unit_id) / diagnostics.REMOVED


def older_dumps(dumps: list[Dump] | None) -> list[Dump] | None:
    """The dumps but the newest, the newest first; None: not known (no listing, or a dump
    whose time is not known, so that the newest cannot be told)."""
    if dumps is None or any(not when for _path, _size, when in dumps):
        return None
    return sorted(dumps, key=lambda d: d[2], reverse=True)[1:]


def copy_split(older: list[Dump]) -> tuple[list[Dump], list[Dump]]:
    """Which of `older` are copied before they are removed (the newest first while they fit
    within REMOVED_LIMIT) and which are too large: removed without a copy."""
    copied, too_large, room = [], [], REMOVED_LIMIT
    for dump in sorted(older, key=lambda d: d[2], reverse=True):
        if dump[1] <= room:
            copied.append(dump)
            room -= dump[1]
        else:
            too_large.append(dump)
    return copied, too_large


def waits_to_install(u) -> bool:
    """An update file in the device's main folder (GUPDATE.GCD, GUPDATE.GSP): the device
    installs it at a restart; removing it would cancel the update."""
    folder = posixpath.dirname(u.device_path).casefold()
    return u.filename.upper() in SYSTEM_FILES and folder in ("", "garmin")


@dataclass
class Freed:
    """What a removal did: the files removed (path, size), those the device refused or kept
    (path, why), the dumps copied first and those removed without a copy (path, size), and
    the dumps not removed because their copy failed (paths)."""

    removed: list[tuple[str, int]] = field(default_factory=list)
    kept: list[tuple[str, str]] = field(default_factory=list)
    copied: list[tuple[str, int]] = field(default_factory=list)
    not_copied: list[tuple[str, int]] = field(default_factory=list)
    copy_failed: list[str] = field(default_factory=list)

    @property
    def freed(self) -> int:
        return sum(size for _path, size in self.removed)


def _kept_by_device() -> str:
    # TRANSLATORS: why a file is still on the device after its removal: the device said it
    # removed it, but the file is there
    return _("the device kept it")


def _copy(storage: Storage, dumps: list[Dump], where: Path, freed: Freed, progress) -> list[Dump]:
    """Copy `dumps` into `where`, each checked (size and SHA-256 of the copy read back);
    returns those copied. A failed or wrong copy is not kept, and its dump is not removed."""
    # TRANSLATORS: a step of copying the memory dumps ("… · 1 of 3")
    head = _("Copying memory dumps")
    done, names = [], set()
    for n, dump in enumerate(dumps, 1):
        path, size, _when = dump
        say_to(progress)(Step(head, n, len(dumps)))
        name = posixpath.basename(path)
        while name.casefold() in names:  # two folders may hold the same name
            name = "_" + name
        names.add(name.casefold())
        target = where / name
        try:
            data = storage.read(path)
            paths.write_private(target, data)
            whole = target.read_bytes()
        except DeviceNotResponding:
            raise
        except (DeviceError, OSError) as e:
            log.warning("freeing: a memory dump not copied: %s", applog.redact(str(e)))
            target.unlink(missing_ok=True)
            freed.copy_failed.append(path)
            continue
        if len(data) != size or hashlib.sha256(whole).digest() != hashlib.sha256(data).digest():
            log.warning("freeing: the copy of a memory dump does not match it")
            target.unlink(missing_ok=True)
            freed.copy_failed.append(path)
            continue
        done.append(dump)
        freed.copied.append((path, size))
    return done


def _keep_within_limit(folder: Path, newest: Path) -> None:
    """At most REMOVED_LIMIT of copies: the oldest removals go first, never `newest`."""
    try:
        removals = sorted(p for p in folder.iterdir() if p.is_dir() and p != newest)
    except OSError:
        return

    def size(where: Path) -> int:
        return sum(p.stat().st_size for p in where.rglob("*") if p.is_file())

    total = size(newest) + sum(size(p) for p in removals)
    while removals and total > REMOVED_LIMIT:
        oldest = removals.pop(0)
        total -= size(oldest)
        shutil.rmtree(oldest, ignore_errors=True)


def _remove(storage: Storage, path: str, size: int, freed: Freed) -> None:
    try:
        storage.delete(path)
    except DeviceNotResponding:
        raise
    except DeviceError as e:
        freed.kept.append((path, str(e)))
        return
    freed.removed.append((path, size))


def _check_gone(storage: Storage, still_there: set[str], freed: Freed) -> None:
    """The files the device says it removed but still shows: kept, not removed."""
    kept = [(p, s) for p, s in freed.removed if p in still_there]
    for path, size in kept:
        freed.removed.remove((path, size))
        freed.kept.append((path, _kept_by_device()))


def remove_dumps(storage: Storage, unit_id: int, asked: list[Dump], progress=None) -> Freed:
    """Remove the older dumps `asked` (as the question named them): the device is listed
    again first, and only those still there with the same size and not the newest now are
    removed; those that fit are copied to this computer first (removed_folder), a dump whose
    copy failed stays. Each removal is checked by listing again."""
    freed = Freed()
    now = [(path, e.size, e.mtime) for path, e in diagnostics.dump_files(storage)]
    older = older_dumps(now)
    if older is None:
        return freed
    wanted = {(path, size) for path, size, _when in asked}
    targets = [d for d in older if (d[0], d[1]) in wanted]
    copied, too_large = copy_split(targets)
    where = None
    if copied:
        stamp = datetime.now().strftime(diagnostics.STAMP)
        where = removed_folder(unit_id) / stamp
        n = 1
        while where.exists():
            n += 1
            where = where.with_name(f"{stamp} ({n})")
        copied = _copy(storage, copied, where, freed, progress)
        if copied:
            _keep_within_limit(where.parent, where)
        elif where.is_dir():
            shutil.rmtree(where, ignore_errors=True)  # every copy failed: nothing to keep
    freed.not_copied = [(path, size) for path, size, _when in too_large]
    going = copied + too_large
    # TRANSLATORS: a step of removing the memory dumps ("… · 1 of 3")
    head = _("Removing memory dumps")
    for n, (path, size, _when) in enumerate(going, 1):
        say_to(progress)(Step(head, n, len(going)))
        _remove(storage, path, size, freed)
    if going:
        _check_gone(storage, {path for path, _e in diagnostics.dump_files(storage)}, freed)
    log.info("freeing: memory dumps: %d removed (%s), %d copied first (%s), %d without a copy, "
             "%d not removed by the device, %d not removed as their copy failed",
             len(freed.removed), applog.size(freed.freed), len(freed.copied),
             applog.size(sum(s for _p, s in freed.copied)), len(freed.not_copied),
             len(freed.kept), len(freed.copy_failed))
    return freed


def remove_update(storage: Storage, u) -> Freed:
    """Remove the file of an update the device did not install, never one that waits to be
    installed (waits_to_install: ValueError). The Updates page offers the update again."""
    if waits_to_install(u):
        raise ValueError(f"{u.device_path} waits to be installed")
    freed = Freed()
    if storage.resolve(u.device_path) is None:
        return freed
    _remove(storage, u.device_path, u.size or 0, freed)
    if freed.removed and storage.resolve(u.device_path) is not None:
        _check_gone(storage, {u.device_path}, freed)
    log.info("freeing: an update file %s (%s)", "removed" if freed.removed else "not removed",
             applog.size(u.size or 0))
    return freed


def updates_not_installed(client, storage: Storage, device, fresh: bool = False
                          ) -> tuple[list, str | None]:
    """The updates the device did not install whose files it holds (updates.not_installed),
    from the update server's answer (asked as the pages ask it, reinstalls too: one answer for
    all); ([], why) when it cannot be asked; ([], None) without a client. `fresh`: asked anew,
    before a removal."""
    from openexpress import updates as upd

    if client is None:
        return [], None
    try:
        answer = client.software_updates(device.raw, include_reinstalls=True, fresh=fresh)
        found = upd.not_installed(upd.offered(storage, answer, device))
    except DeviceNotResponding:
        raise
    except (UserFacingError, OSError) as e:  # the dumps are freed all the same
        return [], applog.network_error_text(e)
    return [u for u in found if u.on_device], None


@dataclass
class Plan:
    """What can be freed on a device: its dumps (None: not listed), the older ones (None: not
    known), which of them are copied first and which are too large, the update files to remove,
    those that wait to be installed (only shown), and why the updates were not checked."""

    dumps: list[Dump] | None
    older: list[Dump] | None
    copied: list[Dump]
    too_large: list[Dump]
    updates: list
    waiting: list
    why: str | None

    @property
    def size(self) -> int:
        return (sum(size for _p, size, _t in self.older or ())
                + sum(u.size or 0 for u in self.updates))


def plan(storage: Storage, device, client, fresh: bool = False) -> Plan:
    """What can be freed, for the page Device Status (status.read) and the command line
    alike. Only reads the device (and asks the update server)."""
    try:
        dumps = [(path, e.size, e.mtime) for path, e in diagnostics.dump_files(storage)]
    except DeviceNotResponding:
        raise
    except DeviceError:
        dumps = None
    older = older_dumps(dumps)
    copied, too_large = copy_split(older or [])
    found, why = updates_not_installed(client, storage, device, fresh=fresh)
    return Plan(dumps=dumps, older=older, copied=copied, too_large=too_large,
                updates=[u for u in found if not waits_to_install(u)],
                waiting=[u for u in found if waits_to_install(u)], why=why)
