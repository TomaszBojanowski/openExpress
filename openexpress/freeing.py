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
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from openexpress import applog, diagnostics, paths
from openexpress.device.storage import MAIN_FOLDERS, DeviceError, DeviceNotResponding, Storage
from openexpress.errors import UserFacingError
from openexpress.i18n import _
from openexpress.progress import Step, say_to
from openexpress.updates import SYSTEM_FILES

log = logging.getLogger(__name__)

REMOVED_LIMIT = 100 * 1000 * 1000
# Times of memory dumps a device with a clock not set (or set wrong) writes: before any device
# that writes dumps, or ahead of this computer by more than a day. Which dump is the newest cannot
# be told then, and nothing is offered for removal.
EARLIEST = datetime(2010, 1, 1).timestamp()
AHEAD = timedelta(days=1)

# A memory dump on the device: its path, size and time (seconds; 0 or None: not known).
Dump = tuple[str, int, float]


class ListChanged(UserFacingError):
    """The files on the device are no longer those the question named: nothing was removed."""


def limit_words() -> str:
    """REMOVED_LIMIT as the texts name it ("100 MB")."""
    if REMOVED_LIMIT % 1_000_000 == 0:
        return f"{REMOVED_LIMIT // 1_000_000}\u00a0MB"
    from openexpress.i18n import human_size

    return human_size(REMOVED_LIMIT)


def removed_folder(unit_id: int, test_drive: bool = False) -> Path:
    """Where the copies of the dumps removed from a device are: in the folder of its diagnostic
    copies (Copies on This Computer counts them, Open Folder shows them, Forget Device removes
    them); those of a test drive (Storage.test_drive), which carries a real device's unit ID,
    in its own folder of copies (diagnostics.folder), with a limit of their own."""
    return diagnostics.folder(unit_id, test_drive) / diagnostics.REMOVED


def by_age(dumps: list[Dump] | None) -> list[Dump] | None:
    """The dumps, the newest first; None when which is the newest cannot be told: no listing, a
    time not known (0), one before EARLIEST or ahead of this computer (AHEAD), or two newest at
    the same time. One answer for the page, the command line and the removal."""
    if dumps is None:
        return None
    latest = datetime.now().timestamp() + AHEAD.total_seconds()
    if any(not when or when < EARLIEST or when > latest for _path, _size, when in dumps):
        return None
    ordered = sorted(dumps, key=lambda d: d[2], reverse=True)
    if len(ordered) > 1 and ordered[0][2] == ordered[1][2]:
        return None
    return ordered


def older_dumps(dumps: list[Dump] | None) -> list[Dump] | None:
    """The dumps but the newest, the newest first; None: not known (by_age). One dump alone
    is the newest whatever its time."""
    if dumps is not None and len(dumps) <= 1:
        return []
    ordered = by_age(dumps)
    return None if ordered is None else ordered[1:]


def copy_split(older: list[Dump]) -> tuple[list[Dump], list[Dump]]:
    """Which of `older` are copied before they are removed (the newest first while they fit
    within REMOVED_LIMIT) and which are not, as the copies would not fit: removed without a
    copy."""
    copied, without_copy, room = [], [], REMOVED_LIMIT
    for dump in sorted(older, key=lambda d: d[2], reverse=True):
        if dump[1] <= room:
            copied.append(dump)
            room -= dump[1]
        else:
            without_copy.append(dump)
    return copied, without_copy


@dataclass(frozen=True)
class DumpPlan:
    """The removal of the older dumps as a question names it: the newest (which stays), the
    older ones, those copied first and those removed without a copy. A removal goes only while
    the device holds exactly these (dump_plan of a new listing equal to it)."""

    newest: Dump
    older: tuple[Dump, ...]
    copied: tuple[Dump, ...]
    without_copy: tuple[Dump, ...]


def dump_plan(dumps: list[Dump] | None) -> DumpPlan | None:
    """The removal of the older dumps (by_age, copy_split); None: not known, or no dump."""
    ordered = by_age(dumps)
    if not ordered:
        return None
    copied, without_copy = copy_split(ordered[1:])
    return DumpPlan(ordered[0], tuple(ordered[1:]), tuple(copied), tuple(without_copy))


# Never removed, though an update's: map support files and satellite data.
NEVER_FOLDERS = ("garmin/sid", "garmin/gps")
NEVER_EXTENSIONS = (".sid",)


def removable_update(u) -> bool:
    """Whether the file of an update the device did not install may be removed: outside
    the device's main folder, not hidden, not a map support file (Garmin/SID, .sid) nor
    satellite data (Garmin/GPS)."""
    path = u.device_path.strip("/").casefold()
    folder, name = posixpath.split(path)
    if folder in MAIN_FOLDERS or name.startswith("."):
        return False
    if any(folder == f or folder.startswith(f + "/") for f in NEVER_FOLDERS):
        return False
    return not name.endswith(NEVER_EXTENSIONS)


def waits_to_install(u) -> bool:
    """An update file in the device's main folder (GUPDATE.GCD, GUPDATE.GSP): the device
    installs it at a restart; removing it would cancel the update."""
    folder = posixpath.dirname(u.device_path).casefold()
    return u.filename.upper() in SYSTEM_FILES and folder in MAIN_FOLDERS


@dataclass
class Freed:
    """What a removal did, filled while it goes (a caller that passes its own keeps what was
    done when it stops half-way): the files removed (path, size), those the device refused or
    kept (path, why), the dumps copied first and those removed without a copy (path, size), the
    dumps not removed because their copy failed (paths), the files gone from the device before
    their removal (something else removed them: paths; a dump's copy then stays, the only one),
    and why it stopped before the end (`stopped`: words; None: it went to the end)."""

    removed: list[tuple[str, int]] = field(default_factory=list)
    kept: list[tuple[str, str]] = field(default_factory=list)
    copied: list[tuple[str, int]] = field(default_factory=list)
    not_copied: list[tuple[str, int]] = field(default_factory=list)
    copy_failed: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    stopped: str | None = None

    @property
    def nothing(self) -> bool:
        """Nothing removed, refused or left by a failed copy: the files were gone already."""
        return not (self.removed or self.kept or self.copy_failed)

    @property
    def freed(self) -> int:
        return sum(size for _path, size in self.removed)


def _kept_by_device() -> str:
    # TRANSLATORS: why a file is still on the device after its removal: the device said it
    # removed it, but the file is there
    return _("the device kept it")


def _changed() -> ListChanged:
    # TRANSLATORS: why nothing was removed from the device after the confirmation was
    # answered
    return ListChanged(_("The files on the device changed while the confirmation was open; "
                         "nothing was removed. Check the list again."))


def _copy(storage: Storage, dumps: list[Dump], where: Path, freed: Freed, progress,
          copies: dict[str, Path]) -> list[Dump]:
    """Copy `dumps` into `where`, each checked (size and SHA-256 of the copy read back);
    returns those copied, each copy's file in `copies`. A failed or wrong copy is not kept, and
    its dump is not removed."""
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
        copies[path] = target
        freed.copied.append((path, size))
    return done


def _number(removal: Path) -> int:
    found = re.match(r"(\d+) (?=\d{4}-)", removal.name)
    return int(found.group(1)) if found else 0


def removals(unit_id: int, test_drive: bool = False) -> list[Path]:
    """The folders of the removals' copies, oldest first: by their number, one more than the
    highest at each removal, so that a clock of this computer set wrong never changes their
    order."""
    try:
        found = [p for p in removed_folder(unit_id, test_drive).iterdir() if p.is_dir()]
    except OSError:
        return []
    return sorted(found, key=lambda p: (_number(p), p.name))


def _keep_within_limit(unit_id: int, test_drive: bool, newest: Path) -> None:
    """At most REMOVED_LIMIT of copies: the oldest removals go first, never `newest`. Under the
    lock of the device's records (remove_dumps), so that no removal still copying loses its
    copies to another."""
    removals_ = [p for p in removals(unit_id, test_drive) if p != newest]

    def size(where: Path) -> int:
        return sum(p.stat().st_size for p in where.rglob("*") if p.is_file())

    total = size(newest) + sum(size(p) for p in removals_)
    while removals_ and total > REMOVED_LIMIT:
        oldest = removals_.pop(0)
        total -= size(oldest)
        shutil.rmtree(oldest, ignore_errors=True)


def _new_removal_folder(unit_id: int, test_drive: bool) -> Path:
    """The folder of this removal's copies, made now (under the lock: two removals never share
    one), numbered after the removals before it, for its owner alone as the other copies."""
    earlier = removals(unit_id, test_drive)
    number = max((_number(p) for p in earlier), default=0) + 1
    where = removed_folder(unit_id, test_drive) / (
        f"{number:04d} {datetime.now().strftime(diagnostics.STAMP)}")
    for folder in (where.parent.parent, where.parent, where):
        paths.make_dir(folder)
        folder.chmod(0o700)
    return where


def _remove(storage: Storage, path: str, size: int, freed: Freed) -> None:
    """Remove one file; a file gone already (removed meanwhile by something else) is not freed
    by this removal (`gone`); one whose folder cannot be listed stays, with why (`kept`)."""
    try:
        if not storage.delete(path, strict=True):
            log.info("freeing: a file was gone already, not counted as removed")
            freed.gone.append(path)
            return
    except DeviceNotResponding:
        raise
    except DeviceError as e:
        freed.kept.append((path, str(e)))
        return
    freed.removed.append((path, size))


def _check_gone(still_there: set[str], freed: Freed) -> None:
    """The files the device says it removed but still shows: kept, not removed."""
    kept = [(p, s) for p, s in freed.removed if p in still_there]
    for path, size in kept:
        freed.removed.remove((path, size))
        freed.kept.append((path, _kept_by_device()))


def remove_dumps(storage: Storage, unit_id: int, asked: DumpPlan, progress=None,
                 freed: Freed | None = None) -> Freed:
    """Remove the older dumps as the question named them (`asked`, dump_plan): under the lock
    of the device's records (history.records_locked: another removal, the device log and Forget
    Device wait), the device listed again, and only when it holds exactly the dumps of the
    question (the same newest, the same older ones with their sizes and times) are they
    removed, with the question's split: those to copy copied first (removed_folder), a dump
    whose copy failed staying; else ListChanged and nothing removed. Each removal checked by
    listing again. `freed`: the caller's, filled while it goes."""
    from openexpress import history

    if not unit_id:
        raise ValueError("no removal of memory dumps without a unit ID")
    freed = freed if freed is not None else Freed()
    test_drive = bool(getattr(storage, "test_drive", False))
    copies: dict[str, Path] = {}
    where = None
    try:
        with history.records_locked():
            now = [(path, e.size, e.mtime) for path, e in diagnostics.dump_files(storage)]
            if dump_plan(now) != asked:
                log.info("freeing: the memory dumps changed since the question, nothing "
                         "removed")
                raise _changed()
            copied, without_copy = list(asked.copied), list(asked.without_copy)
            if copied:
                where = _new_removal_folder(unit_id, test_drive)
            try:
                if copied:
                    copied = _copy(storage, copied, where, freed, progress, copies)
                going = copied + without_copy
                # TRANSLATORS: a step of removing the memory dumps ("… · 1 of 3")
                head = _("Removing memory dumps")
                for n, (path, size, _when) in enumerate(going, 1):
                    say_to(progress)(Step(head, n, len(going)))
                    _remove(storage, path, size, freed)
                if going:
                    _check_gone({path for path, _e in diagnostics.dump_files(storage)}, freed)
            finally:
                # Those removed without a copy: only those that went.
                gone = {path for path, _size in freed.removed}
                freed.not_copied = [(path, size) for path, size, _when in without_copy
                                    if path in gone]
                # A copy of a dump still on the device (kept, refused, not reached, also when
                # the copying itself stopped) is not its only one: dropped, so that it never
                # pushes out a copy that is.
                _drop_copies_of_kept(freed, copies)
                if where is not None and any(where.iterdir()):
                    _keep_within_limit(unit_id, test_drive, where)
                elif where is not None:
                    shutil.rmtree(where, ignore_errors=True)
    finally:
        log.info("freeing: memory dumps: %d removed (%s), %d copied first (%s), %d without a "
                 "copy, %d not removed by the device, %d not removed as their copy failed",
                 len(freed.removed), applog.size(freed.freed), len(freed.copied),
                 applog.size(sum(s for _p, s in freed.copied)), len(freed.not_copied),
                 len(freed.kept), len(freed.copy_failed))
    return freed


def _drop_copies_of_kept(freed: Freed, copies: dict[str, Path]) -> None:
    """The copies of the dumps that stay on the device dropped (not their only copy); those
    of dumps removed, or gone from it meanwhile, kept."""
    gone = {path for path, _size in freed.removed} | set(freed.gone)
    for path, target in copies.items():
        if path not in gone:
            target.unlink(missing_ok=True)
    freed.copied = [(path, size) for path, size in freed.copied if path in gone]


def listed_file(storage: Storage, u) -> tuple[int, float] | None:
    """The size and time of an update's file on the device now; None: not there, or behind a
    link (Storage.through_link: never removed)."""
    real = storage.resolve(u.device_path)
    if real is None or storage.through_link(real):
        return None
    entry = storage.info(real)
    return None if entry is None or entry.is_dir else (entry.size, entry.mtime)


def remove_update(storage: Storage, u, client, device, freed: Freed | None = None) -> Freed:
    """Remove the file of an update the device did not install, as the question named it
    (`u`, with the size and time of its file when it was listed: u.listed_file), never one
    that waits to be installed (waits_to_install: ValueError). Before it, the file is looked at
    again (the same size and time) and the update server asked anew (it still offers the
    update and the device has not installed it): else ListChanged and nothing removed. What
    was freed is the size of the file removed. The file is looked at and removed under the lock
    of the device's records (history.records_locked), after the server's answer, so that a
    removal by another process (the window and the command line) never counts it twice.
    `freed`: the caller's, filled while it goes."""
    from openexpress import history

    if waits_to_install(u) or not removable_update(u):
        raise ValueError(f"{u.device_path} is not removed (waits to be installed, or not an "
                         "update file freeing removes)")
    freed = freed if freed is not None else Freed()
    found, why = updates_not_installed(client, storage, device, fresh=True)
    if why is not None:
        raise UserFacingError(why)
    still = any(f.device_path.casefold() == u.device_path.casefold() for f in found)
    with history.records_locked():
        now = listed_file(storage, u)
        if now is None or now != u.listed_file or not still:
            log.info("freeing: the update file changed since the question, not removed")
            raise _changed()
        real = storage.resolve(u.device_path)
        try:
            _remove(storage, real, now[0], freed)
            if freed.removed and storage.resolve(u.device_path) is not None:
                _check_gone({real}, freed)
        finally:
            log.info("freeing: an update file %s (%s)",
                     "removed" if freed.removed else "not removed", applog.size(now[0]))
    return freed


def updates_not_installed(client, storage: Storage, device, fresh: bool = False
                          ) -> tuple[list, str | None]:
    """The updates the device did not install whose files it holds (updates.not_installed),
    from the update server's answer (asked as the pages ask it, reinstalls too: one answer for
    all), each with the size and time of its file (listed_file); ([], why) when it cannot be
    asked; ([], None) without a client. `fresh`: asked anew, before a removal."""
    from openexpress import updates as upd

    if client is None:
        return [], None
    try:
        answer = client.software_updates(device.raw, include_reinstalls=True, fresh=fresh)
        # Only read: what was copied to the device is not forgotten here (the Updates page
        # does it), nor ever by a test drive, which carries a real device's unit ID.
        found = upd.not_installed(upd.offered(storage, answer, device, forget_installed=False))
    except DeviceNotResponding:
        raise
    except (UserFacingError, OSError) as e:  # the dumps are freed all the same
        return [], applog.network_error_text(e)
    out = []
    for u in found:
        if not u.on_device:
            continue
        try:
            u.listed_file = listed_file(storage, u)
            if u.listed_file is None:
                continue  # gone meanwhile, or behind a link: nothing of it to remove
        except DeviceNotResponding:
            raise
        except DeviceError:
            u.listed_file = None
        out.append(u)
    return out, None


def dumps_removable(device) -> bool:
    """Whether this device's older dumps may be removed: not without a unit ID, as the copies
    before a removal would have no folder of the device to go to (its page could not show them,
    nor Forget Device remove them). One answer for the page and the command line."""
    return bool(getattr(device, "unit_id", None))


@dataclass
class Plan:
    """What can be freed on a device: its dumps (None: not listed), their removal as a question
    names it (`dumps_plan`, None: not known or none), the update files to remove, those that
    wait to be installed (only shown), and why the updates were not checked."""

    dumps: list[Dump] | None
    dumps_plan: DumpPlan | None
    updates: list
    waiting: list
    why: str | None

    @property
    def older(self) -> list[Dump] | None:
        """The dumps but the newest; None: not known (older_dumps)."""
        return older_dumps(self.dumps)

    @property
    def copied(self) -> list[Dump]:
        return list(self.dumps_plan.copied) if self.dumps_plan is not None else []

    @property
    def without_copy(self) -> list[Dump]:
        return list(self.dumps_plan.without_copy) if self.dumps_plan is not None else []

    @property
    def size(self) -> int:
        """What the removal would free: the older dumps only when they may be removed."""
        dumps = self.dumps_plan.older if self.dumps_plan is not None else ()
        return sum(size for _p, size, _t in dumps) + sum(u.size or 0 for u in self.updates)


def plan(storage: Storage, device, client, fresh: bool = False) -> Plan:
    """What can be freed, for the page Device Status (status.read) and the command line
    alike. Only reads the device (and asks the update server)."""
    try:
        dumps = [(path, e.size, e.mtime) for path, e in diagnostics.dump_files(storage)]
    except DeviceNotResponding:
        raise
    except DeviceError:
        dumps = None
    found, why = updates_not_installed(client, storage, device, fresh=fresh)
    return Plan(dumps=dumps, dumps_plan=dump_plan(dumps) if dumps_removable(device) else None,
                updates=[u for u in found if removable_update(u)],
                waiting=[u for u in found if waits_to_install(u)], why=why)
