"""The device log: one line for each connection of a device, in its folder on this computer
(devices/<unit ID>/history.jsonl, readable by its owner alone).

A line says when and how the device was connected, its software version, its capacity and free
space, the space each kind of file takes, its memory dumps and every crash its error logs hold:
counts and short fields only, never a file name of the user's, the unit ID or a serial number.
It is written at most once a connection, whoever opened the device (the window, the command
line, the background sync: note_connection), and kept whatever the copy of
the diagnostic files says; Forget Device deletes it with the rest of the device's folder. Read
back line by line, a damaged or foreign line skipped (and kept as it is when the log is written
again); lines more than two years older than the newest and the oldest beyond MAX_LINES are
dropped.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from openexpress import applog, paths
from openexpress.device.storage import (
    MAIN_FOLDERS,
    DeviceError,
    DeviceNotResponding,
    Entry,
    Storage,
    named_in_any_case,
)
from openexpress.device.xmldevice import GarminDevice

log = logging.getLogger(__name__)

NAME = "history.jsonl"
VERSION = 1
KEEP = timedelta(days=2 * 365)
# About 400 bytes a line: some hundreds of kilobytes for a device connected a few times a day.
MAX_LINES = 2000
# The kinds of file, in the order a file is given the first that takes it; "system" is the rest
# of the used space (the device's own files and what its file system keeps).
KINDS = ("logs", "updates", "apps", "music", "activities", "courses", "maps")
CATEGORIES = ("maps", "activities", "music", "apps", "courses", "updates", "logs", "system")
VIA = ("mtp", "drive")
# What Garmin Connect queued for the device waits there until the device takes it in.
QUEUE = "garmin/newfiles"


def path(unit_id: int, test_drive: bool = False) -> Path:
    """The log of a device; that of a test drive (Storage.test_drive), which carries the
    GarminDevice.xml of a real device, apart from that device's (sync.records_dir)."""
    from openexpress.sync import records_dir

    return records_dir(unit_id, test_drive) / NAME


# --- the space of each kind of file ----------------------------------------------------------


def _kind(datatype: str) -> str | None:
    from openexpress.maps import may_be_maps
    from openexpress.sync import DIAGNOSTIC_TYPES

    name = datatype.casefold()
    if datatype in DIAGNOSTIC_TYPES or name == "ulfconfig":
        return "logs"
    if name.startswith(("ephemeris", "update")):
        return "updates"
    if name.startswith("iq"):
        return "apps"
    if name.startswith("media_"):
        return "music"
    if datatype == "FIT_TYPE_4":
        return "activities"
    if datatype in ("FIT_TYPE_6", "FIT_TYPE_8"):
        return "courses"
    if may_be_maps(datatype):
        return "maps"
    return None


class _Folders:
    """The device's folders, each listed once for all the kinds (over MTP each listing is a
    round trip). The folders of its top level ordinary MTP does not show come from what
    openExpress's own session read of them on this connection (hidden_folders.kept); one of
    them it did not read is taken for empty, so that what it holds stays in "system"."""

    def __init__(self, storage: Storage):
        from openexpress import hidden_folders

        self.storage = storage
        self.listed: dict[str, list[Entry]] = {}
        listing = hidden_folders.kept(storage)
        self.hidden = listing if listing is not None and listing.state == hidden_folders.READ \
            else None

    def entries(self, real: str) -> list[Entry]:
        if real not in self.listed:
            self.listed[real] = self._list(real)
        return self.listed[real]

    def _list(self, real: str) -> list[Entry]:
        if self.hidden is None:
            return self.storage.list_at(real)
        if not real:
            found = self.storage.list_at(real)
            seen = {e.name.casefold() for e in found}
            return found + [Entry(top, True) for top in self.hidden.tops()
                            if top.casefold() not in seen]
        if real.split("/")[0].casefold() in {t.casefold() for t in self.hidden.tops()}:
            return self.hidden.entries(real) or []
        return self.storage.list_at(real)

    def resolve(self, folder: str) -> str | None:
        real = ""
        for part in [p for p in folder.split("/") if p]:
            # A folder behind a link is elsewhere on this computer: not the device's.
            entries = [e for e in self.entries(real) if e.is_dir and not e.is_link]
            match = next((e for e in entries if e.name == part), None) or named_in_any_case(
                entries, part)
            if match is None:
                return None
            real = posixpath.join(real, match.name) if real else match.name
        return real

    def files(self, folder: str, deep: bool) -> list[tuple[str, Entry]]:
        real = self.resolve(folder)
        if real is None:
            return []
        out, stack = [], [real]
        while stack:
            current = stack.pop()
            for e in self.entries(current):
                p = posixpath.join(current, e.name) if current else e.name
                if not e.is_dir:
                    out.append((p, e))
                elif deep and not e.is_link:
                    stack.append(p)
        return out


# Declared for the device's whole main folder (FITBinary, FTLImageVersion, the maps of a fēnix 8):
# only its own files are counted, not those of the folders under it, and only by a rule that names
# an extension or a file (one without would take GarminDevice.xml and the update files there).
def _within(folder: str, parent: str) -> bool:
    return parent not in MAIN_FOLDERS and (folder == parent or folder.startswith(parent + "/"))


def count(device: GarminDevice, storage: Storage) -> dict[str, int]:
    """The space each kind of file takes, by the data types the device declares (where each
    kind is and which files are of it), its update files and its folders of logs; "system":
    the rest of the used space. Sizes only, nothing read. A kind whose folder could not be
    listed is left out, and "system" with it; a kind the device does not declare too."""
    from openexpress import diagnostics

    rules = []
    for f in device.files:
        kind = _kind(f.datatype)
        if kind is not None and f.path.strip("/").casefold() != QUEUE:
            rules.append((KINDS.index(kind), kind, f))
    rules.sort(key=lambda rule: rule[0])
    logs = [diagnostics.DEBUG, *diagnostics.EVENT_LOGS]
    wanted: dict[str, set[str]] = {}  # folder (in any case) -> the kinds found in it
    for _order, kind, f in rules:
        wanted.setdefault(f.path.strip("/").casefold(), set()).add(kind)
    for folder in logs:
        wanted.setdefault(folder.casefold(), set()).add("logs")
    # A folder under another one listed whole comes with it.
    walked = [f for f in sorted(wanted) if not any(_within(f, other) and f != other
                                                   for other in wanted)]
    folders = _Folders(storage)
    sizes = {kind: 0 for _order, kind, _f in rules}
    sizes["logs"] = 0
    unknown: set[str] = set()
    seen: set[str] = set()
    for folder in walked:
        try:
            found = folders.files(folder, deep=folder not in MAIN_FOLDERS)
        except DeviceNotResponding:
            raise
        except DeviceError as e:
            log.info("history: a folder not listed (%s)", type(e).__name__)
            unknown |= {kind for f, kinds in wanted.items() if _within(f, folder) or f == folder
                        for kind in kinds}
            continue
        main = folder in MAIN_FOLDERS
        for real, entry in found:
            key = real.casefold()
            if key in seen or entry.is_link:
                continue
            if any(_within(key, place.casefold()) for place in logs):
                kind = "logs"
            else:
                kind = next((k for _order, k, f in rules
                             if (not main or f.extension or f.basename) and f.matches(real)),
                            None)
            if kind is None:
                continue  # of no kind: an update file of the main folder is counted below
            seen.add(key)
            sizes[kind] += entry.size
    if device.update_files:
        sizes.setdefault("updates", 0)
        for u in device.update_files:
            try:
                real = folders.resolve(u.path)
                entries = folders.entries(real) if real is not None else []
            except DeviceNotResponding:
                raise
            except DeviceError:
                unknown.add("updates")
                continue
            entry = named_in_any_case([e for e in entries if not e.is_dir], u.filename)
            key = posixpath.join(real or "", entry.name).casefold() if entry else None
            if entry is not None and key not in seen:
                seen.add(key)
                sizes["updates"] += entry.size
    for kind in unknown:
        sizes.pop(kind, None)
    return sizes


def space(storage: Storage) -> tuple[int | None, int | None]:
    """(capacity, free space) of the device; None: not known."""
    out = []
    for ask in (storage.capacity, storage.free_space):
        try:
            value = ask()
        except DeviceNotResponding:
            raise
        except DeviceError:
            value = None
        out.append(value if isinstance(value, int) and value >= 0 else None)
    return out[0], out[1]


# --- the line of a connection ----------------------------------------------------------------


def storage_by_kind(device: GarminDevice, storage: Storage
                    ) -> tuple[dict[str, int], int | None, int | None]:
    """The space each kind of file takes (count) with "system", the rest of the used space,
    and the capacity and the free space: at the connection and after a removal alike."""
    capacity, free = space(storage)
    kinds = count(device, storage)
    # The rest of the used space only when every kind was counted, else it would hold the kind
    # left out.
    if capacity is not None and free is not None and _declared(device) <= kinds.keys():
        rest = capacity - free - sum(kinds.values())
        if rest >= 0:
            kinds["system"] = rest
    return kinds, capacity, free


def _boot() -> str:
    """This start of the computer: a device at the same place on USB after a restart is another
    connection."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _mount_id(root) -> str | None:
    """The system's ID of the mount of a drive at `root` (/proc/self/mountinfo), made anew at
    each mount; None: not a mount point, or not known."""
    if root is None:
        return None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                fields = line.split()
                if len(fields) > 4 and fields[4].replace("\\040", " ") == str(root):
                    return fields[0]
    except OSError:
        return None
    return None


def _place(device: GarminDevice, storage: Storage) -> str:
    """What tells one connection of the device from the next one: where it is on USB (a new
    address at each plugging in); a drive whose place is not known, its mount (made anew at
    each one); else its software version (an update or a restart after it is a new connection;
    a plugging in again with nothing changed is not told)."""
    usb = getattr(storage, "usb", None)
    bus, address = getattr(usb, "bus", None), getattr(usb, "address", None)
    if bus is None or address is None:
        bus, address = getattr(usb, "told_place", None) or (None, None)
    if bus is not None and address is not None:
        return f"usb|{bus}|{address}"
    mount = (_mount_id(getattr(storage, "root", None))
             if getattr(storage, "mass_storage", False) else None)
    if mount is not None:
        return f"mount|{mount}"
    return f"software|{getattr(device, 'software_version_str', None)}"


def _key(device: GarminDevice, storage: Storage, via: str) -> str:
    """The connection, as a short digest of what tells it (the start of the computer, the
    device, its place: _place): the same for every opening while it stays plugged in."""
    told = "|".join(str(part) for part in (_boot(), device.unit_id, via,
                                           _place(device, storage)))
    return hashlib.sha256(told.encode()).hexdigest()[:12]


def _crash_fields(crash) -> dict:
    fields = {"kind": crash.kind,
              "at": crash.at.strftime("%Y-%m-%d %H:%M:%S") if crash.at else None,
              "error": crash.error, "pc": crash.program_counter, "screen": crash.screen}
    if getattr(crash, "software", None):
        fields["software"] = crash.software  # the version then, as the page shows it
    return fields


def _line(device: GarminDevice, storage: Storage, found, now: datetime, key: str,
          via: str) -> dict:
    from openexpress import diagnostics

    kinds, capacity, free = storage_by_kind(device, storage)
    if found is not None:
        entries = found.entries if found.errors_known else None
        error_logs = found.error_logs
        dumps = found.dumps if found.dumps_known else None
    else:
        present: list[str] = []
        entries = diagnostics.errors_or_none(storage, present=present)
        error_logs = len(present)
        dumps = diagnostics.dumps_or_none(storage)
    line = {"v": VERSION, "time": now.astimezone().isoformat(timespec="seconds"), "conn": key,
            "via": via, "software": device.software_version_str, "categories": kinds}
    # What could not be read is left out, never written as none or 0.
    if capacity is not None:
        line["capacity"] = capacity
    if free is not None:
        line["free"] = free
    if dumps is not None:
        line["dumps"] = {"count": len(dumps), "bytes": sum(size for _p, size in dumps)}
    if entries is not None:
        # Every crash they hold, so that the device's page from its records tells as many as
        # the page of the device connected did.
        line["crashes"] = [_crash_fields(e) for e in entries if e.is_crash]
        line["errors"] = len(entries)
        # Error logs without entries are not "no diagnostic data" (status.has_diagnostics).
        line["error_logs"] = error_logs
    hidden = _hidden_logs(device, storage, found)
    if hidden is not None:
        line["hidden"] = hidden
    return line


def _hidden_logs(device: GarminDevice, storage: Storage, found) -> bool | None:
    """Whether the device has crash logs that only Copy Crash Logs reads (as at the opening);
    None: not known."""
    from openexpress import diagnostics

    if found is not None:
        return bool(found.hidden_logs)
    try:
        return diagnostics.hidden_logs_to_copy(diagnostics.may_have_hidden_logs(storage),
                                               device.unit_id,
                                               bool(getattr(storage, "test_drive", False)))
    except DeviceNotResponding:
        raise
    except DeviceError:
        return None


def _declared(device: GarminDevice) -> set[str]:
    kinds = {kind for f in device.files
             if (kind := _kind(f.datatype)) is not None and f.path.strip("/").casefold() != QUEUE}
    kinds.add("logs")
    if device.update_files:
        kinds.add("updates")
    return kinds


# Connections noted by this process: (the log, key).
_noted: set[tuple[str, str]] = set()
LOCK = "records.lock"


@contextmanager
def records_locked():
    """The lock of the records of the devices on this computer, in any process: the device log
    (note_connection, note_counted), a removal from the device with the copies it keeps
    (freeing.remove_dumps) and Forget Device (forget._delete) take it, one at a time. A lock
    file of its own, outside the device's folder, so that taking it never makes that folder
    again."""
    folder = paths.data_dir()
    paths.make_dir(folder)
    with paths.locked(folder / LOCK):
        yield


def forgotten(unit_id: int) -> None:
    """Forget Device deleted what this computer kept of the device (under records_locked): its
    next connection is noted anew, also in this window."""
    folder = str(paths.device_dir(unit_id))
    _noted.difference_update({n for n in _noted if n[0].startswith(folder)})


def note_connection(device: GarminDevice, storage: Storage | None, found=None,
                    now: datetime | None = None) -> bool:
    """The line of this connection added to the device's log, unless it has one already (an
    opening before, by this process or another). `found`: what the window read of the error
    logs (diagnostics.AtOpen), not read again. A device that stopped responding raises
    (DeviceNotResponding); any other failure, also one not foreseen, is a line in the program's
    log, never a failed opening (nor the window's reading of the error logs lost with it). True
    when a line was written."""
    if storage is None or not getattr(device, "unit_id", None):
        return False
    via = _via(storage)
    key = _key(device, storage, via)
    where = path(device.unit_id, bool(getattr(storage, "test_drive", False)))
    if (str(where), key) in _noted:
        return False
    try:
        if _last_key(where) == key:
            _noted.add((str(where), key))
            return False
        now = now or datetime.now()
        line = _line(device, storage, found, now, key, via)
        written = _append(where, line)
    except DeviceNotResponding:
        raise
    except Exception as e:  # noqa: BLE001 - the log is a side line: never a failed opening
        log.info("history: no line for this connection (%s)", type(e).__name__)
        return False
    _noted.add((str(where), key))
    if written:
        log.info("history: a line for this connection (%d kinds of file, %s free)",
                 len(line["categories"]), applog.size(line.get("free")))
    return written


def _via(storage: Storage) -> str:
    """How the device is connected, as the log keeps it (VIA): a drive or MTP."""
    return "drive" if getattr(storage, "mass_storage", False) else "mtp"


def _last_key(where: Path) -> str | None:
    kept = read_file(where)
    return kept.lines[-1]["conn"] if kept.lines else None


def _append(where: Path, line: dict) -> bool:
    text = json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n"
    with records_locked():
        try:
            data = where.read_bytes()
        except FileNotFoundError:
            data = b""
        kept = _parse(data)
        if kept.lines and kept.lines[-1]["conn"] == line["conn"]:
            return False
        if data and not data.endswith(b"\n"):
            text = "\n" + text  # a write cut short: its piece stays a line of its own
        paths.make_dir(where.parent)
        fd = os.open(where, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            os.fchmod(fd, 0o600)
            f.write(text)
        lines = [*kept.lines, line]
        recent = _kept(lines)
        if len(recent) < len(lines):
            dropped = {id(x) for x in lines} - {id(x) for x in recent}
            _rewrite(where, data + text.encode(),
                     lambda n, _parsed: None if id(lines[n]) in dropped else ...)
    return True


def _kept(lines: list[dict]) -> list[dict]:
    """The lines kept after `lines[-1]` was added: those less than KEEP older than it, at most
    MAX_LINES."""
    cut = _when(lines[-1]) - KEEP
    return [x for x in lines if _when(x) >= cut][-MAX_LINES:]


def _raw_lines(data: bytes) -> list[tuple[bytes, dict | None]]:
    """Each line of the log as written, with what the program reads of it (valid); None: a
    damaged or foreign line (not UTF-8, not one of the program's), which a reader skips and a
    writer keeps as it is. Not splitlines(): it also breaks at characters a JSON text may hold
    (U+2028…)."""
    out = []
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            out.append((raw, None))
            continue
        out.append((raw, valid(text)))
    return out


def _rewrite(where: Path, data: bytes, change) -> None:
    """The log written again from `data` line by line, each line as it was unless
    `change(n, parsed)` (n: its place among the valid lines) gives another: None drops it, a
    dict replaces it, ... keeps it. A damaged or foreign line stays as it was (a reader skips
    it; a writer never drops what it cannot read)."""
    out, n = [], 0
    for raw, parsed in _raw_lines(data):
        if parsed is None:
            out.append(raw)
            continue
        got = change(n, parsed)
        n += 1
        if got is None:
            continue
        out.append(raw if got is ... else
                   json.dumps(got, ensure_ascii=False, sort_keys=True).encode())
    paths.write_private(where, b"".join(line + b"\n" for line in out))


def _put_right(where: Path, key: str, fix) -> bool:
    """The last valid line of the log, when it is the line of the connection `key`, put right in
    place by `fix(item)` (the line as written, with the fields this version does not know);
    every other line as it was. False when the last line is of another connection."""
    try:
        data = where.read_bytes()
    except FileNotFoundError:
        return False
    kept = _parse(data)
    if not kept.lines or kept.lines[-1]["conn"] != key:
        return False
    last = len(kept.lines) - 1
    raws = [raw for raw, parsed in _raw_lines(data) if parsed is not None]
    item = json.loads(raws[last])
    fix(item)
    _rewrite(where, data, lambda n, _parsed: item if n == last else ...)
    return True


class NotCounted(DeviceError):
    """The count after a removal told neither the free space nor the memory dumps (the device
    unplugged meanwhile, each question answered with an error): its line is put right by what
    was removed instead (after_removal)."""


def note_counted(device: GarminDevice, storage: Storage,
                 now: datetime | None = None, after_removal_of: dict | None = None
                 ) -> tuple[dict[str, int], int | None, int | None, list | None]:
    """After the device was written to (files removed too): its storage by kind, its space and
    its memory dumps counted again and put in the line of this connection, in place (the number
    of connections stays), with when ("counted"), so that what the records tell is true without
    connecting it again. A log whose last line is of another connection (its line not written)
    is left as it is, and so is a line counted after this count began (`now`: when it began; a
    count begun before a removal and ended after it never undoes the count of that removal).
    Returns the kinds, the capacity, the free space and the memory dumps (path, size; None: not
    listed) counted. `after_removal_of`: the files just removed, by kind (after_removal): a count
    that tells neither the free space nor the dumps then writes nothing and raises NotCounted."""
    from openexpress import diagnostics

    now = now or datetime.now()
    kinds, capacity, free = storage_by_kind(device, storage)
    dumps = diagnostics.dumps_or_none(storage)
    if after_removal_of and free is None and dumps is None:
        log.info("history: the device could not be counted after the removal")
        raise NotCounted  # what the user is told is the caller's (command line or window)
    if not getattr(device, "unit_id", None):
        return kinds, capacity, free, dumps
    via = _via(storage)
    key = _key(device, storage, via)
    where = path(device.unit_id, bool(getattr(storage, "test_drive", False)))
    later = []
    with records_locked():
        def fix(line: dict) -> None:
            if counted_after(line, now):
                later.append(line)
                return
            line["categories"] = kinds
            line["counted"] = _counted_text(now)
            if capacity is not None:  # not told now: the one known (it does not change)
                line["capacity"] = capacity
            if free is None:
                line.pop("free", None)  # not known now: not what it was before
            else:
                line["free"] = free
            if dumps is None:
                line.pop("dumps", None)
            else:
                line["dumps"] = {"count": len(dumps), "bytes": sum(size for _p, size in dumps)}

        if not _put_right(where, key, fix):
            log.info("history: the line of this connection is not the last one, not put right")
            return kinds, capacity, free, dumps
    if later:
        log.info("history: the line of this connection was counted after this count began, "
                 "left as it is")
        return kinds, capacity, free, dumps
    log.info("history: the line of this connection put right by a count (%d kinds of file, "
             "%s free)", len(kinds), applog.size(free))
    return kinds, capacity, free, dumps


def _counted_text(when: datetime) -> str:
    return when.astimezone().isoformat(timespec="milliseconds")


def counted_after(line: dict, when: datetime) -> bool:
    """Whether the line was counted again ("counted") after `when`."""
    if "counted" not in line:
        return False
    counted = datetime.fromisoformat(line["counted"])
    counted = counted if counted.tzinfo is not None else counted.astimezone()
    return counted > when.astimezone()


def after_removal(device: GarminDevice, storage: Storage,
                  removed: dict[str, list[tuple[str, int]]], now: datetime | None = None
                  ) -> tuple[dict[str, int], int | None, int | None, list | None]:
    """After files were removed from the device (`removed`: by kind, "logs" for memory dumps,
    "updates" for update files, each path and size): the device counted again and its line put
    right (note_counted); when that count fails (the device stopped responding, was unplugged,
    Ctrl+C), the line put right by what was removed (note_removed) and the failure raised as it
    is. The window and the command line alike."""
    now = now or datetime.now()
    try:
        return note_counted(device, storage, now, after_removal_of=removed)
    except BaseException:
        for kind, files in removed.items():
            note_removed(device, storage, files, kind, now)
        raise


def connection_line(device: GarminDevice, storage: Storage) -> dict | None:
    """The line of this connection in the device's log (as it is now: put right after a
    removal by the window or the command line); None: it has none, or another is the last."""
    if not getattr(device, "unit_id", None):
        return None
    via = _via(storage)
    lines = read(device.unit_id, bool(getattr(storage, "test_drive", False))).lines
    return lines[-1] if lines and lines[-1]["conn"] == _key(device, storage, via) else None


def note_removed(device: GarminDevice, storage: Storage, removed: list[tuple[str, int]],
                 kind: str, now: datetime | None = None) -> bool:
    """After a removal that could not be counted again (the device stopped responding, or was
    unplugged): the line of this connection put right by what was removed (`removed`: path,
    size; `kind`: "logs" for memory dumps, "updates" for update files): its memory dumps fewer,
    its free space larger and that kind smaller by their sizes, with when ("counted"). Nothing
    is asked of the device. True when the line was put right."""
    if not removed or not getattr(device, "unit_id", None):
        return False
    now = now or datetime.now()
    via = _via(storage)
    try:
        key = _key(device, storage, via)
    except DeviceNotResponding:
        raise
    except Exception as e:  # noqa: BLE001 - as below
        log.info("history: not put right by what was removed (%s)", type(e).__name__)
        return False
    where = path(device.unit_id, bool(getattr(storage, "test_drive", False)))
    size = sum(s for _p, s in removed)

    def fix(line: dict) -> None:
        if counted_after(line, now):
            return  # counted after the removal (another process): it tells it already
        line["counted"] = _counted_text(now)
        if "free" in line:
            line["free"] += size
        kinds = line.get("categories", {})
        if kind in kinds:
            kinds[kind] = max(0, kinds[kind] - size)
        if kind == "logs" and isinstance(line.get("dumps"), dict):
            line["dumps"] = {"count": max(0, line["dumps"]["count"] - len(removed)),
                             "bytes": max(0, line["dumps"]["bytes"] - size)}

    try:
        with records_locked():
            done = _put_right(where, key, fix)
    except Exception as e:  # noqa: BLE001 - the log is a side line: never the removal's result lost
        log.info("history: not put right by what was removed (%s)", type(e).__name__)
        return False
    log.info("history: the line of this connection %s by what was removed (%d files, %s)",
             "put right" if done else "not put right", len(removed), applog.size(size))
    return done


# --- reading it back -------------------------------------------------------------------------


@dataclass
class Log:
    lines: list[dict] = field(default_factory=list)  # oldest first
    skipped: int = 0  # damaged or foreign lines


def read(unit_id: int, test_drive: bool = False) -> Log:
    return read_file(path(unit_id, test_drive))


def read_file(where: Path) -> Log:
    try:
        data = where.read_bytes()
    except FileNotFoundError:
        return Log()
    except OSError as e:
        log.info("history: not read (%s)", type(e).__name__)
        return Log()
    return _parse(data)


def _parse(data: bytes) -> Log:
    out = Log()
    for _raw, line in _raw_lines(data):
        if line is None:
            out.skipped += 1
        else:
            out.lines.append(line)
    return out


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _text(value, empty_ok: bool = True) -> bool:
    return isinstance(value, str) or (empty_ok and value is None)


def valid(raw: str) -> dict | None:
    """The line as the program wrote it, with only what it knows of it; None for anything
    else (not JSON, another version, a field of the wrong kind). A field left out is not
    known (capacity, free, dumps; "errors" and "crashes": the error logs not read). A field
    "crash" (the newest crash alone), which a line may hold, is left out."""
    try:
        item = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(item, dict) or item.get("v") != VERSION:
        return None
    try:
        when, conn, via = item["time"], item["conn"], item["via"]
        software, categories = item["software"], item["categories"]
        datetime.fromisoformat(when)
    except (KeyError, TypeError, ValueError):
        return None
    if not (isinstance(conn, str) and via in VIA and isinstance(software, str)
            and isinstance(categories, dict)
            and all(_count(v) for k, v in categories.items() if k in CATEGORIES)):
        return None
    line = {"v": VERSION, "time": when, "conn": conn, "via": via, "software": software,
            "categories": {k: v for k, v in categories.items() if k in CATEGORIES}}
    for key in ("capacity", "free"):
        if key in item:
            if not _count(item[key]):
                return None
            line[key] = item[key]
    if "dumps" in item:
        dumps = item["dumps"]
        if not (isinstance(dumps, dict) and _count(dumps.get("count"))
                and _count(dumps.get("bytes"))):
            return None
        line["dumps"] = {"count": dumps["count"], "bytes": dumps["bytes"]}
    if "crashes" in item:
        found = item["crashes"]
        if not (isinstance(found, list) and all(_crash_ok(c) for c in found)):
            return None
        line["crashes"] = [_crash_kept(c) for c in found]
    for key in ("errors", "error_logs"):
        if key in item:
            if not _count(item[key]):
                return None
            line[key] = item[key]
    if "hidden" in item:
        if not isinstance(item["hidden"], bool):
            return None
        line["hidden"] = item["hidden"]
    if "counted" in item:
        try:
            datetime.fromisoformat(item["counted"])
        except (TypeError, ValueError):
            return None
        line["counted"] = item["counted"]
    return line


def _crash_ok(crash) -> bool:
    return (isinstance(crash, dict) and isinstance(crash.get("kind"), str)
            and all(_text(crash.get(k)) for k in ("at", "error", "pc", "screen", "software")))


def _crash_kept(crash: dict) -> dict:
    """What the program reads of a crash of a line (valid): the software only when written; a
    field "log" (the place of its error log), which a line may hold, is left out."""
    return {**{k: crash.get(k) for k in ("kind", "at", "error", "pc", "screen")},
            **({"software": crash["software"]} if crash.get("software") is not None else {})}


def _when(line: dict) -> datetime:
    when = datetime.fromisoformat(line["time"])
    return when if when.tzinfo is not None else when.astimezone()


def local_time(line: dict) -> datetime:
    """When the line was written, in this computer's time now (naive)."""
    return _when(line).astimezone().replace(tzinfo=None)


def counted_time(line: dict) -> datetime:
    """When the storage and the dumps of the line were counted, in this computer's time now
    (naive): again after a removal ("counted", note_counted), else at the connection."""
    if "counted" not in line:
        return local_time(line)
    when = datetime.fromisoformat(line["counted"])
    return (when if when.tzinfo is not None else when.astimezone()).astimezone().replace(
        tzinfo=None)


def _entry(crash: dict):
    from openexpress.diagnostics import ErrorEntry

    try:
        at = datetime.strptime(crash["at"], "%Y-%m-%d %H:%M:%S") if crash.get("at") else None
    except ValueError:
        at = None
    return ErrorEntry(crash["kind"], at, crash.get("error"), crash.get("pc"),
                      crash.get("screen"), software=crash.get("software"))


def crash_entries(line: dict) -> list:
    """Every crash the error logs held at the line's connection, the newest first, as error
    entries (diagnostics.ErrorEntry); none when they were not read ("errors" left out)."""
    return [_entry(c) for c in line.get("crashes", ())]
