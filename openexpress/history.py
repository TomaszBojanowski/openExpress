"""The device log: one line for each connection of a device, in its folder on this computer
(devices/<unit ID>/history.jsonl, readable by its owner alone).

A line says when and how the device was connected, its software version, its capacity and free
space, the space each kind of file takes, its memory dumps and the newest crash its error logs
hold: counts and short fields only, never a file name, the unit ID or a serial number. It is
written at most once a connection, whoever opened the device (the window, the command line, the
background sync: note_connection), and kept whatever the copy of the diagnostic files says; Forget
Device deletes it with the rest of the device's folder. Read back line by line, a damaged or
foreign line skipped; lines older than two years and the oldest beyond MAX_LINES are dropped.
"""

from __future__ import annotations

import fcntl
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
# Declared for the device's whole main folder (FITBinary, FTLImageVersion): only its own files
# are counted, not those of the folders under it.
MAIN_FOLDERS = ("", "garmin")


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
    round trip)."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.listed: dict[str, list[Entry]] = {}

    def entries(self, real: str) -> list[Entry]:
        if real not in self.listed:
            self.listed[real] = self.storage.list_at(real)
        return self.listed[real]

    def resolve(self, folder: str) -> str | None:
        real = ""
        for part in [p for p in folder.split("/") if p]:
            entries = [e for e in self.entries(real) if e.is_dir]
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
                elif deep:
                    stack.append(p)
        return out


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
        for real, entry in found:
            key = real.casefold()
            if key in seen:
                continue
            seen.add(key)
            if any(_within(key, place.casefold()) for place in logs):
                kind = "logs"
            else:
                kind = next((k for _order, k, f in rules if f.matches(real)), None)
            if kind is not None:
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


def _key(device: GarminDevice, storage: Storage, via: str) -> str:
    """The connection, as a short digest of what tells it (the start of the computer, the
    device, where it is on USB): the same for every opening while it stays plugged in."""
    usb = getattr(storage, "usb", None)
    told = "|".join(str(part) for part in (_boot(), device.unit_id, via,
                                           getattr(usb, "bus", None),
                                           getattr(usb, "address", None)))
    return hashlib.sha256(told.encode()).hexdigest()[:12]


def _crash(entries) -> dict | None:
    crash = next((e for e in entries if e.is_crash), None)
    if crash is None:
        return None
    return {"kind": crash.kind,
            "at": crash.at.strftime("%Y-%m-%d %H:%M:%S") if crash.at else None,
            "error": crash.error, "pc": crash.program_counter, "screen": crash.screen}


def _line(device: GarminDevice, storage: Storage, found, now: datetime, key: str,
          via: str) -> dict:
    from openexpress import diagnostics

    kinds, capacity, free = storage_by_kind(device, storage)
    if found is not None:
        entries = found.entries if found.errors_known else None
        dumps = found.dumps if found.dumps_known else None
    else:
        missed: list[str] = []
        entries = diagnostics.read_errors(storage, missed=missed)
        entries = None if missed else entries
        try:
            dumps = diagnostics.dumps_on_device(storage)
        except DeviceNotResponding:
            raise
        except DeviceError:
            dumps = None
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
        line["crash"] = _crash(entries)  # None: the error logs hold no crash
    return line


def _declared(device: GarminDevice) -> set[str]:
    kinds = {kind for f in device.files
             if (kind := _kind(f.datatype)) is not None and f.path.strip("/").casefold() != QUEUE}
    kinds.add("logs")
    if device.update_files:
        kinds.add("updates")
    return kinds


# Connections noted by this process: (the log, key).
_noted: set[tuple[str, str]] = set()


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
    via = "drive" if getattr(storage, "mass_storage", False) else "mtp"
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
        written = _append(where, line, now)
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


@contextmanager
def _locked(folder: Path):
    """The device's folder locked while its log is read and written (the window and the
    background service may note the same connection at once); the folder, not the file, which
    a dropping of lines replaces."""
    paths.make_dir(folder)
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _last_key(where: Path) -> str | None:
    kept = read_file(where)
    return kept.lines[-1]["conn"] if kept.lines else None


def _append(where: Path, line: dict, now: datetime) -> bool:
    text = json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n"
    with _locked(where.parent):
        try:
            data = where.read_bytes()
        except FileNotFoundError:
            data = b""
        kept = _parse(data)
        if kept.lines and kept.lines[-1]["conn"] == line["conn"]:
            return False
        if data and not data.endswith(b"\n"):
            text = "\n" + text  # a write cut short: its piece stays a line of its own
        fd = os.open(where, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            os.fchmod(fd, 0o600)
            f.write(text)
        lines = [*kept.lines, line]
        cut = now.astimezone() - KEEP
        recent = [x for x in lines if _when(x) >= cut][-MAX_LINES:]
        if len(recent) < len(lines):
            paths.write_private(where, "".join(json.dumps(x, ensure_ascii=False, sort_keys=True)
                                               + "\n" for x in recent))
    return True


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
    # Not splitlines(): it also breaks at characters a JSON text may hold (U+2028…).
    for raw in data.decode("utf-8", errors="replace").split("\n"):
        if not raw.strip():
            continue
        line = valid(raw)
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
    known (capacity, free, dumps, crash); "crash": None is known: no crash."""
    try:
        item = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(item, dict) or item.get("v") != VERSION:
        return None
    try:
        time, conn, via = item["time"], item["conn"], item["via"]
        software, categories = item["software"], item["categories"]
        datetime.fromisoformat(time)
    except (KeyError, TypeError, ValueError):
        return None
    if not (isinstance(conn, str) and via in VIA and isinstance(software, str)
            and isinstance(categories, dict)
            and all(_count(v) for k, v in categories.items() if k in CATEGORIES)):
        return None
    line = {"v": VERSION, "time": time, "conn": conn, "via": via, "software": software,
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
    if "crash" in item:
        crash = item["crash"]
        if crash is not None and not (isinstance(crash, dict)
                                      and isinstance(crash.get("kind"), str)
                                      and all(_text(crash.get(k))
                                              for k in ("at", "error", "pc", "screen"))):
            return None
        line["crash"] = None if crash is None else {k: crash.get(k) for k in
                                                    ("kind", "at", "error", "pc", "screen")}
    return line


def _when(line: dict) -> datetime:
    when = datetime.fromisoformat(line["time"])
    return when if when.tzinfo is not None else when.astimezone()


def local_time(line: dict) -> datetime:
    """When the line was written, in this computer's time now (naive)."""
    return _when(line).astimezone().replace(tzinfo=None)


def crash_entry(line: dict):
    """The newest crash of the line as an error entry (diagnostics.ErrorEntry), for its words."""
    from openexpress.diagnostics import ErrorEntry

    crash = line.get("crash")
    if not crash:
        return None
    try:
        at = datetime.strptime(crash["at"], "%Y-%m-%d %H:%M:%S") if crash.get("at") else None
    except ValueError:
        at = None
    return ErrorEntry(crash["kind"], at, crash.get("error"), crash.get("pc"),
                      crash.get("screen"))
