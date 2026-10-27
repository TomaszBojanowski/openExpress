"""The device's own diagnostic files (analysis 20, §3.1–3.2).

What the device last logged as an error (its `err_log` files, read through the ordinary
connection at each opening, whatever the settings), and a copy of its diagnostic files kept
on this computer: off by default, at most once per connection, and never sent anywhere.

The files are read only. The copy never opens a session of its own with the device: the
hidden crash logs (Garmin's listing only) and the memory dumps are copied only when asked
for (`copy_crash_logs`, `copy_dumps`). Of an error entry only its kind, time, error number,
program counter and screen are kept; never the unit ID or the backtrace in it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openexpress import applog, paths
from openexpress.device.storage import DeviceError, Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.errors import UserFacingError
from openexpress.i18n import _, ngettext

log = logging.getLogger(__name__)

DEBUG = "Garmin/Debug"
ERR_LOGS = (f"{DEBUG}/err_log.txt", f"{DEBUG}/err_log.bak")
ERR_LOG_MAX = 1024 * 1024  # bigger is not an error log (a few kB with 14 entries)
DUMPS = f"{DEBUG}/dump"
# Folders of event logs where some devices show them (Venu 2, Edge 1050); a fēnix 8
# hides its GARMIN/EVNTLOGS from the ordinary connection.
EVENT_LOGS = ("GARMIN/EVNTLOGS", f"{DEBUG}/EventLogs")
# The hidden crash logs of a fēnix 8: its boot log and its event logs.
CRASH_LOGS = (f"{DEBUG}/recovery_mode_log.txt",)
CRASH_LOG_FOLDERS = ("GARMIN/EVNTLOGS",)
# The data types of the device's diagnostics (sync.DIAGNOSTIC_TYPES without the files of
# a FIT type): their folders are copied with Garmin/Debug.
TYPES = ("ErrorShutdownReports", "ULFLogs", "IQErrorReports", "KPI", "RAMDump", "BLELogs")
# Never copied, only listed: memory dumps (encrypted, MB each), health metrics and
# settings kept by the device (personal, nothing to do with errors) and big files.
LISTED_ONLY = ("dump", "metricsbackup", "backup")
BIG = 4 * 1000 * 1000
MAX_FILES = 500

COPIES_KEPT = 30
LIMIT = 50 * 1000 * 1000
STAMP = "%Y-%m-%d %H.%M.%S"
RECENT = timedelta(days=30)
# The only kind shown as "the device reported an error" (analysis 20, §3.2): an Edge 1050
# logs "Assert" at every ordinary restart, "Abnormal Powerdown" is a power loss or a
# restart forced with the button.
CRASH = "RTL Trap"


class DiagnosticsError(UserFacingError):
    pass


@dataclass(frozen=True)
class ErrorEntry:
    """One entry of an error log: what it is and when, no more."""

    kind: str
    at: datetime | None  # the device's local time, as it logs it
    error: str | None = None  # "RTL Error Number", e.g. "0x003"
    program_counter: str | None = None
    screen: str | None = None  # "pageStack"
    source: str = ""  # "err_log.bak"

    @property
    def is_crash(self) -> bool:
        return self.kind.casefold() == CRASH.casefold()

    def words(self) -> str:
        """The entry in one line for the log and the command line (no personal data)."""
        when = self.at.strftime("%Y-%m-%d %H:%M:%S") if self.at else "?"
        parts = [f"{self.kind} at {when}"]
        if self.error:
            parts.append(f"error {self.error}")
        if self.program_counter:
            parts.append(f"PC {self.program_counter}")
        if self.screen:
            parts.append(f"screen {self.screen}")
        return ", ".join(parts)


def _field(item: dict, key: str, limit: int = 40) -> str | None:
    value = item.get(key)
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value).strip()
        return text[:limit] if text else None
    return None


def _moment(item: dict, extra: dict) -> datetime | None:
    """The time of an entry: "Time" of its details ("2026-10-26 22:24:06"), else
    "ms_since_epoch"; both are the device's local time (analysis 20: they agree, and they
    agree with the time of the file)."""
    text = _field(extra, "Time")
    if text:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    ms = _field(item, "ms_since_epoch")
    if ms and ms.isdigit():
        try:
            return datetime.fromtimestamp(int(ms) / 1000, UTC).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def parse_err_log(data: bytes, source: str = "") -> list[ErrorEntry]:
    """The entries of an err_log file (a JSON list of objects; an object alone is one entry).
    Empty, damaged or of another shape: no entries. Only the kind, time and crash fields are
    read; the unit ID and the backtrace never leave the file."""
    try:
        parsed = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        return []
    items = parsed if isinstance(parsed, list) else [parsed]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = _field(item, "error_cause", 60)
        if not kind:
            continue
        extra = item.get("proprietary_info")
        extra = extra if isinstance(extra, dict) else {}
        out.append(ErrorEntry(kind, _moment(item, extra), _field(extra, "RTL Error Number"),
                              _field(extra, "programCounter"), _field(extra, "pageStack"),
                              source))
    return out


def read_errors(storage: Storage) -> list[ErrorEntry]:
    """The entries of the device's error logs, read now through the ordinary connection,
    newest first. Missing files are no entries; a file that cannot be read is said in the
    log."""
    out: list[ErrorEntry] = []
    for path in ERR_LOGS:
        name = posixpath.basename(path)
        try:
            size = storage.size(path)
            if size is None:
                continue
            if size > ERR_LOG_MAX:
                log.info("diagnostics: %s is %s, not read", name, applog.size(size))
                continue
            out += parse_err_log(storage.read(path), name)
        except DeviceError as e:
            log.info("diagnostics: %s not read (%s)", name, type(e).__name__)
    return sorted(out, key=lambda e: e.at or datetime.min, reverse=True)


def last_crash(entries: list[ErrorEntry], now: datetime) -> ErrorEntry | None:
    """The newest "RTL Trap" of the last 30 days (by the device's clock); None: none."""
    for e in entries:
        if e.is_crash and e.at is not None and now - RECENT <= e.at <= now + timedelta(days=1):
            return e
    return None


# --- the copy on this computer ------------------------------------------------------------


def folder(unit_id: int) -> Path:
    """Where the copies of a device are: in its own folder, so that forgetting the device
    removes them too."""
    return paths.device_dir(unit_id) / "diagnostics"


@dataclass
class Copied:
    """What one copy did."""

    new: int = 0  # files new or changed since the copy before (the content not kept yet, for
    # the crash logs and the dumps)
    new_bytes: int = 0  # what was stored now
    unchanged: int = 0
    listed: int = 0  # listed only: dumps, personal and big files
    saved: bool = False  # a new copy was written (nothing changed: none)
    copies: int = 0  # copies kept afterwards
    kept_bytes: int = 0
    not_copied: list[str] = field(default_factory=list)  # dumps over the limit


def _listed_only(path: str, size: int) -> bool:
    parts = [p.casefold() for p in path.split("/")]
    return size > BIG or any(p in LISTED_ONLY for p in parts[:-1])


def _type_folders(device: GarminDevice, storage: Storage) -> list[str]:
    """The folders of the declared diagnostic types that the device shows. A path that does
    not exist as it is declared (an Edge 1050 gives "Garmin/Debug/Uploads/Er" for
    ".../Error") is the one folder next to it whose name begins with it."""
    out: list[str] = []
    for f in device.files:
        if f.datatype not in TYPES or not f.readable:
            continue
        path = f.path.strip("/")
        if path.count("/") < 1:  # never a whole top folder
            continue
        real = storage.resolve(path)
        if real is None:
            parent, start = posixpath.split(path)
            found = [e.name for e in storage.listdir(parent)
                     if e.is_dir and e.name.casefold().startswith(start.casefold())]
            real = posixpath.join(storage.resolve(parent) or parent, found[0]) \
                if len(found) == 1 else None
        if real is not None and real not in out:
            out.append(real)
    return out


def _visible_files(device: GarminDevice, storage: Storage) -> dict[str, tuple[int, float]]:
    """The diagnostic files the device shows: path → (size, time)."""
    roots = [DEBUG, *_type_folders(device, storage), *EVENT_LOGS]
    found: dict[str, tuple[int, float]] = {}
    seen: set[str] = set()
    for root in roots:
        for path, entry in storage.walk_files(root):
            if path.casefold() in seen:
                continue
            seen.add(path.casefold())
            found[path] = (entry.size, entry.mtime)
            if len(found) >= MAX_FILES:
                log.info("diagnostics: more than %d files, the rest not listed", MAX_FILES)
                return found
    return found


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _private(where: Path) -> None:
    """The folder of the copies and its own folders: for their owner alone (0700)."""
    for folder_ in (where, where / "objects", where / "snapshots"):
        paths.make_dir(folder_)
        folder_.chmod(0o700)


def _snapshots(where: Path) -> list[Path]:
    try:
        return sorted((where / "snapshots").glob("*.json"))
    except OSError:
        return []


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _last(where: Path, kind: str) -> dict:
    for path in reversed(_snapshots(where)):
        data = _load(path)
        if data.get("kind") == kind:
            return data
    return {}


def _keep(where: Path, data: bytes) -> tuple[str, bool]:
    """The content kept once by its SHA-256; True when it was not kept yet."""
    sha = _sha(data)
    target = where / "objects" / sha
    if target.exists():
        return sha, False
    _private(where)
    paths.write_private(target, data)
    return sha, True


def _save(where: Path, kind: str, files: dict, listed: dict, now: datetime,
          software: str) -> None:
    _private(where)
    name = now.strftime(STAMP)
    target = where / "snapshots" / f"{name}.json"
    n = 1
    while target.exists():
        n += 1
        target = where / "snapshots" / f"{name} ({n}).json"
    paths.write_private(target, json.dumps({
        "kind": kind, "at": now.isoformat(timespec="seconds"), "software_version": software,
        "files": files, "listed": listed}, indent=1, sort_keys=True))


def _object_bytes(where: Path) -> int:
    try:
        return sum(p.stat().st_size for p in (where / "objects").iterdir() if p.is_file())
    except OSError:
        return 0


def rotate(where: Path, copies: int = COPIES_KEPT, limit: int = LIMIT) -> tuple[int, int]:
    """At most `copies` copies and `limit` bytes of content: the oldest copies go first, the
    newest never; then the content no copy refers to. Returns the copies and the bytes
    kept."""
    snaps = _snapshots(where)
    removed = 0

    def referenced(kept: list[Path]) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in kept:
            for item in _load(p).get("files", {}).values():
                if isinstance(item, dict) and isinstance(item.get("sha256"), str):
                    out[item["sha256"]] = int(item.get("size") or 0)
        return out

    while len(snaps) > max(1, copies):
        snaps.pop(0).unlink(missing_ok=True)
        removed += 1
    while len(snaps) > 1 and sum(referenced(snaps).values()) > limit:
        snaps.pop(0).unlink(missing_ok=True)
        removed += 1
    wanted = referenced(snaps)
    objects = where / "objects"
    if objects.is_dir():
        for p in objects.iterdir():
            if p.is_file() and p.name not in wanted:
                p.unlink(missing_ok=True)
    if removed:
        log.info("diagnostics: %d older copies removed", removed)
    return len(snaps), _object_bytes(where)


def copy_files(device: GarminDevice, storage: Storage, now: datetime | None = None) -> Copied:
    """Copy the diagnostic files the device shows (through the ordinary connection, never a
    session of its own): the content not kept yet, a new copy only when something changed.
    Memory dumps, health metrics, settings and big files are only listed."""
    now = now or datetime.now()
    where = folder(device.unit_id)
    found = _visible_files(device, storage)
    last = _last(where, "files")
    before = last.get("files", {})
    report = Copied()
    files, listed = {}, {}
    for path, (size, mtime) in found.items():
        if _listed_only(path, size):
            listed[path] = {"size": size, "mtime": mtime}
            report.listed += 1
            continue
        old = before.get(path)
        if (isinstance(old, dict) and size > 64 * 1024 and mtime and old.get("size") == size
                and old.get("mtime") == mtime
                and (where / "objects" / str(old.get("sha256"))).exists()):
            files[path] = old  # a big file as it was: not read again
            report.unchanged += 1
            continue
        data = storage.read(path)
        sha, stored = _keep(where, data)
        files[path] = {"sha256": sha, "size": len(data), "mtime": mtime}
        report.new_bytes += len(data) if stored else 0
        # Unchanged: as in the copy before (not merely the content of another file).
        if isinstance(old, dict) and old.get("sha256") == sha:
            report.unchanged += 1
        else:
            report.new += 1
    if files != last.get("files") or listed != last.get("listed"):
        _save(where, "files", files, listed, now, device.software_version_str)
        report.saved = True
    report.copies, report.kept_bytes = rotate(where)
    log.info("diagnostics: %s; %d new (%s), %d unchanged, %d listed only; %d copies kept (%s)",
             "copied" if report.saved else "nothing changed since the last copy", report.new,
             applog.size(report.new_bytes), report.unchanged, report.listed, report.copies,
             applog.size(report.kept_bytes))
    return report


def dumps_on_device(storage: Storage) -> list[tuple[str, int]]:
    """The memory dumps the device shows: (path, size)."""
    return [(path, e.size) for path, e in storage.walk_files(DUMPS)]


def copy_dumps(device: GarminDevice, storage: Storage, now: datetime | None = None) -> Copied:
    """Copy the memory dumps (on request only), the newest first while they fit within the
    limit; those that do not fit even alone are not copied (`not_copied`)."""
    now = now or datetime.now()
    where = folder(device.unit_id)
    found = sorted(storage.walk_files(DUMPS), key=lambda t: t[1].mtime, reverse=True)
    report = Copied()
    files, room = {}, LIMIT
    for path, entry in found:
        if entry.size > room:
            report.not_copied.append(posixpath.basename(path))
            continue
        data = storage.read(path)
        sha, new = _keep(where, data)
        files[path] = {"sha256": sha, "size": len(data), "mtime": entry.mtime}
        room -= len(data)
        report.new += new
        report.new_bytes += len(data) if new else 0
        report.unchanged += not new
    if files:
        _save(where, "dumps", files, {}, now, device.software_version_str)
        report.saved = True
    report.copies, report.kept_bytes = rotate(where)
    log.info("diagnostics: memory dumps: %d copied (%s), %d not (over the limit); %d copies "
             "kept (%s)", len(files), applog.size(report.new_bytes), len(report.not_copied),
             report.copies, applog.size(report.kept_bytes))
    return report


# --- the hidden crash logs, on request ------------------------------------------------------


def _state(where: Path) -> dict:
    return _load(where / "state.json")


def hidden_logs_known_absent(unit_id: int) -> bool:
    """Whether a session asked for found no hidden crash log on this device: the window
    then no longer offers one."""
    return _state(folder(unit_id)).get("crash_logs") == "none"


def may_have_hidden_logs(storage: Storage) -> bool:
    """The rule of "Copy Crash Logs" (analysis 20, §3.2): a device over MTP whose event logs
    the ordinary connection does not show (a fēnix 8 hides them, a Venu 2 and an Edge 1050
    show them, and the copy takes them as they are)."""
    if storage.mass_storage or not getattr(storage, "device_uri", ""):
        return False
    return not any(storage.exists(p) for p in EVENT_LOGS)


def ptp_unavailable_text(storage: Storage) -> str:
    if storage.usb_blocked():
        return _("The Flatpak version has no access to USB devices, so it cannot read the "
                 "hidden crash logs. Give it the permission (see the README) or use the "
                 "version from source.")
    return _("openExpress cannot open its own USB session with this device, so it cannot "
             "read the hidden crash logs.")


def copy_crash_logs(device: GarminDevice, storage: Storage,
                    now: datetime | None = None) -> Copied:
    """Copy the hidden crash logs (the boot log and the event logs of a fēnix 8) in one
    read-only session of our own: the device leaves the file manager for a moment. Nothing
    found: remembered, and not offered again."""
    from openexpress import ciq

    now = now or datetime.now()
    where = folder(device.unit_id)
    opener = storage.exclusive_ptp()
    if opener is None:
        raise DiagnosticsError(ptp_unavailable_text(storage))
    read: dict[str, bytes] = {}
    with opener as session:
        store = ciq._ptp_store(session)
        for path in CRASH_LOGS:
            item = session.lookup(store, path)
            if item is not None and not item.is_dir:
                read[path] = session.read_file(item.handle)
        for path in CRASH_LOG_FOLDERS:
            item = session.lookup(store, path)
            if item is None or not item.is_dir:
                continue
            for child in session.all_children(store, item.handle):
                if not child.is_dir and child.size <= BIG:
                    read[f"{path}/{child.name}"] = session.read_file(child.handle)
    report = Copied()
    files = {}
    for path, data in read.items():
        sha, new = _keep(where, data)
        files[path] = {"sha256": sha, "size": len(data), "mtime": None}
        report.new += new
        report.new_bytes += len(data) if new else 0
        report.unchanged += not new
    if files:
        _save(where, "crash logs", files, {}, now, device.software_version_str)
        report.saved = True
        report.copies, report.kept_bytes = rotate(where)
    else:
        state = _state(where)
        state["crash_logs"] = "none"
        _private(where)
        paths.write_private(where / "state.json", json.dumps(state))
    log.info("diagnostics: crash logs: %d files read in a session of our own (%d new, %s)%s",
             len(files), report.new, applog.size(report.new_bytes),
             "" if files else "; none on this device, not offered again")
    return report


def boot_log_crashes(unit_id: int) -> list[str]:
    """The "RTL Trap" lines of the boot log in the newest copy of the crash logs (their time
    and error number), for the command line; none without such a copy."""
    where = folder(unit_id)
    item = _last(where, "crash logs").get("files", {}).get(CRASH_LOGS[0])
    if not isinstance(item, dict):
        return []
    try:
        text = (where / "objects" / str(item.get("sha256"))).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return []
    from openexpress.i18n import full_time

    out = []
    for line in text.splitlines():
        stamp, _sep, said = line.partition("]:")
        if CRASH.casefold() not in said.casefold():
            continue
        try:  # "[10/26/26 22:24:39 <seconds>]": the device's local time
            when = full_time(datetime.strptime(" ".join(stamp.lstrip("[").split()[:2]),
                                               "%m/%d/%y %H:%M:%S"))
        except ValueError:
            when = "?"
        out.append(f"{when}: {said.strip()[:80]}")
    return out


# --- at each opening ------------------------------------------------------------------------


@dataclass
class AtOpen:
    """What the window learns of the device's errors when it opens it."""

    entries: list[ErrorEntry]
    crash: ErrorEntry | None
    dumps: list[tuple[str, int]]
    hidden_logs: bool  # "Copy Crash Logs" is offered
    copied: Copied | None = None


# Once per connection: the unit ID and where the device is on USB (a new connection gives a
# new address).
_done: dict[tuple, AtOpen] = {}


def _connection(device: GarminDevice, storage: Storage) -> tuple | None:
    usb = getattr(storage, "usb", None)
    if usb is None or usb.bus is None or usb.address is None:
        return None
    return device.unit_id, usb.bus, usb.address


def copy_on(settings=None) -> bool:
    from openexpress.settings import Settings, SetupError

    try:
        return bool((settings or Settings.load()).diagnostics_copy)
    except SetupError:
        return False


def at_open(device: GarminDevice, storage: Storage, now: datetime | None = None) -> AtOpen:
    """At the opening of a device, once per connection: its error logs read (always), and its
    diagnostic files copied when the copy is on. Never a session of our own; a failure is
    a line in the log, never a failed opening."""
    key = _connection(device, storage)
    if key is not None and key in _done:
        return _done[key]
    now = now or datetime.now()
    entries = read_errors(storage)
    try:
        dumps = dumps_on_device(storage)
        hidden = (may_have_hidden_logs(storage)
                  and not hidden_logs_known_absent(device.unit_id))
    except DeviceError as e:
        log.info("diagnostics: the folders not listed (%s)", type(e).__name__)
        dumps, hidden = [], False
    found = AtOpen(entries, last_crash(entries, now), dumps, hidden)
    if found.crash is not None:
        log.info("diagnostics: the device logged %s", found.crash.words())
    if copy_on():
        try:
            found.copied = copy_files(device, storage, now)
        except (DeviceError, OSError) as e:
            log.info("diagnostics: the copy failed (%s: %s)", type(e).__name__, e)
    if key is not None:
        _done[key] = found
    return found


def copies_text(unit_id: int) -> str:
    """How many copies are kept, how big and when the last was made, for the command line."""
    from openexpress.i18n import full_time, human_size

    where = folder(unit_id)
    snaps = _snapshots(where)
    if not snaps:
        return _("No copy yet")
    try:
        when = full_time(datetime.strptime(snaps[-1].stem.split(" (")[0], STAMP))
    except ValueError:
        when = "?"
    return ngettext("{count} copy, {size}, the last on {when}",
                    "{count} copies, {size}, the last on {when}", len(snaps)).format(
        count=len(snaps), size=human_size(_object_bytes(where)), when=when)
