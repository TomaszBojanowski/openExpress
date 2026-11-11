"""The device's crashes: those that restarted it ("RTL Trap"), merged from every source this
computer has.

The sources: the error logs on the device (err_log.txt, err_log.bak), read by the caller; the
copies of them kept on this computer; the boot log in the copies of the crash logs (Copy Crash
Logs), which the device writes at the start after a crash; and the crashes of each line of the
device log (history), which holds them also when no copy is kept, and while the device is not
connected. The device keeps only its last entries, the copies older ones. A crash written several
times counts once: the same entry seen again (in each copy and in the device log), or the same
crash in two sources (the error log at the crash, the boot log at the next start) within a minute,
with the same error, program counter and boot log error number where both give one; two entries
of one file are two crashes, however close, even of the same time and codes. Other kinds of
entry ("Assert" at a start, "Abnormal Powerdown") are no crashes here. Of an entry only what
diagnostics.parse_err_log keeps is used, never the unit ID or the backtrace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from openexpress import diagnostics

SAME = timedelta(minutes=1)
# The sources besides the error logs on the device (named after their file).
BOOT_LOG = "boot log"
COPY = "copy"
DEVICE_LOG = "device log"
_BOOT_LOG_NAME = diagnostics.CRASH_LOGS[0].rsplit("/", 1)[1]
_ERR_LOG_NAMES = tuple(path.rsplit("/", 1)[1] for path in diagnostics.ERR_LOGS)
_ERRNUM = re.compile(r"errnum:\s*(0x[0-9a-f]+)", re.IGNORECASE)


@dataclass
class Crash:
    """One crash, with what its sources tell of it."""

    at: datetime  # the device's local time; the error log's when it has the crash
    software: str | None = None  # the device's software version then
    error: str | None = None  # the error log's "RTL Error Number"
    program_counter: str | None = None
    screen: str | None = None
    errnum: str | None = None  # the boot log's error number: another number than `error`
    sources: set[str] = field(default_factory=set)  # file names, BOOT_LOG, COPY
    # The same time and codes written before in the same file: 0 for the first. Two entries of
    # one file are two crashes; the n-th of a file is the n-th in each other.
    repeat: int = field(default=0, compare=False, repr=False)

    def words(self) -> str:
        """The crash in one line, as the device names its parts (the command line)."""
        parts = [diagnostics.CRASH]
        for label, value in (("software", self.software), ("error", self.error),
                             ("PC", self.program_counter), ("screen", self.screen),
                             ("boot log error", self.errnum)):
            if value:
                parts.append(f"{label} {value}")
        return ", ".join(parts)


def _from_entry(entry: diagnostics.ErrorEntry, source: str) -> Crash:
    return Crash(entry.at, entry.software, entry.error, entry.program_counter, entry.screen,
                 sources={source})


def _boot_log(data: bytes) -> list[Crash]:
    """The crashes of a boot log: lines "[10/26/26 22:24:39 <seconds>]: RTL Trap detected,
    errnum: 0x…", the time the device's local time."""
    out = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        stamp, _sep, said = line.partition("]:")
        if diagnostics.CRASH.casefold() not in said.casefold():
            continue
        try:
            at = datetime.strptime(" ".join(stamp.lstrip("[").split()[:2]), "%m/%d/%y %H:%M:%S")
        except ValueError:
            continue
        number = _ERRNUM.search(said)
        out.append(Crash(at, errnum=number.group(1) if number else None, sources={BOOT_LOG}))
    return out


def _agree(a: Crash, b: Crash) -> bool:
    return all(x is None or y is None or x.casefold() == y.casefold()
               for x, y in ((a.error, b.error), (a.program_counter, b.program_counter),
                            (a.errnum, b.errnum)))


def same_error(a: Crash, b: Crash) -> bool:
    """Whether two crashes had the same error ("the same error as the crashes before"): by the
    boot log's error number where both have one, else by the error log's error and program
    counter. Codes of different kinds (the two numbers are not alike) tell nothing: not the
    same."""
    if a.errnum and b.errnum:
        return a.errnum.casefold() == b.errnum.casefold()
    if a.error and b.error and a.program_counter and b.program_counter:
        return (a.error.casefold() == b.error.casefold()
                and a.program_counter.casefold() == b.program_counter.casefold())
    return False


def _fields(c: Crash) -> tuple:
    return (c.at, c.error, c.program_counter, c.screen, c.errnum)


def _numbered(of_one_file: list[Crash]) -> list[Crash]:
    """The crashes of one file, each the same time and codes as one before it in that file
    numbered (repeat), so that they stay apart."""
    seen: dict[tuple, int] = {}
    for crash in of_one_file:
        crash.repeat = seen.get(_fields(crash), 0)
        seen[_fields(crash)] = crash.repeat + 1
    return of_one_file


def _join(into: Crash, crash: Crash) -> None:
    for name in ("software", "error", "program_counter", "screen", "errnum"):
        if getattr(into, name) is None:
            setattr(into, name, getattr(crash, name))
    into.sources |= crash.sources


def _boot_line_and_crash(a: Crash, b: Crash) -> bool:
    """Whether one of the two is a boot log's line alone and the other a crash no boot log's
    line has joined yet: a line joins one crash, and a crash one line, never two crashes
    through one line."""
    return any(x.sources == {BOOT_LOG} and BOOT_LOG not in y.sources for x, y in ((a, b), (b, a)))


def merge(found: list[Crash]) -> list[Crash]:
    """One crash for each one written several times, newest first: the same entry seen again
    (the same time and codes, and the same place among the like ones of its file: an error log,
    each copy of it, the device log) is one; then a crash of another source within a minute
    whose codes agree joins it, only a boot log's line and a crash of another source (the boot
    log's line of the error log's crash). Two crashes of the same file are never joined: a
    device that restarts again and again writes each."""
    seen: dict[tuple, Crash] = {}
    for crash in found:
        key = (_fields(crash), crash.repeat)
        same = seen.get(key)
        if same is None:
            seen[key] = Crash(crash.at, crash.software, crash.error, crash.program_counter,
                              crash.screen, crash.errnum, set(crash.sources), crash.repeat)
        else:
            _join(same, crash)
    out: list[Crash] = []
    for crash in sorted(seen.values(), key=lambda c: c.at):
        same = min((c for c in out if abs(c.at - crash.at) <= SAME and _agree(c, crash)
                    and not c.sources & crash.sources and _boot_line_and_crash(c, crash)),
                   key=lambda c: abs(c.at - crash.at), default=None)
        if same is None:
            out.append(crash)
        else:
            _join(same, crash)
    return sorted(out, key=lambda c: c.at, reverse=True)


def history(unit_id: int | None, entries=None, test_drive: bool = False) -> list[Crash]:
    """The device's crashes, newest first: those of `entries` (its error logs read now,
    diagnostics.read_errors; None or none: not read), those the copies on this computer keep
    and those its device log wrote (a test drive's own log; none for a device without a unit
    ID)."""
    from openexpress import history as device_log

    found: list[Crash] = []
    by_file: dict[str, list[Crash]] = {}
    for e in entries or ():
        if e.is_crash and e.at is not None:
            source = e.source or "err_log"
            by_file.setdefault(source, []).append(_from_entry(e, source))
    for of_one_file in by_file.values():
        found += _numbered(of_one_file)
    if unit_id:
        for line in device_log.read(unit_id, test_drive).lines:
            of_line: dict[str, list[Crash]] = {}  # by the error log each was in
            for entry in device_log.crash_entries(line):
                if entry.is_crash and entry.at is not None:
                    of_line.setdefault(entry.source, []).append(_from_entry(entry, DEVICE_LOG))
            for of_one_file in of_line.values():
                found += _numbered(of_one_file)
        for name, data in diagnostics.kept_files(unit_id, (*_ERR_LOG_NAMES, _BOOT_LOG_NAME),
                                                  test_drive):
            if name.casefold() == _BOOT_LOG_NAME.casefold():
                found += _numbered(_boot_log(data))
            else:
                found += _numbered([_from_entry(e, COPY)
                                    for e in diagnostics.parse_err_log(data, name)
                                    if e.is_crash and e.at is not None])
    return merge(found)
