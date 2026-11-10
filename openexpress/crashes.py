"""The device's crashes: those that restarted it ("RTL Trap"), merged from every source this
computer has.

The sources: the error logs on the device (err_log.txt, err_log.bak), read by the caller; the
copies of them kept on this computer; the boot log in the copies of the crash logs (Copy Crash
Logs), which the device writes at the start after a crash; and the newest crash of each line of
the device log (history), which holds them also when no copy is kept, and while the device is not
connected. The device keeps only its last entries, the copies older ones. A crash written in
several of them (the error log at the crash, the boot log at the next start, the same entry in
each copy and in the device log) counts once: within a minute, with the same error, program
counter and boot log error number where both give one. Other kinds of
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


def merge(found: list[Crash]) -> list[Crash]:
    """One crash for each one written in several sources, newest first."""
    out: list[Crash] = []
    for crash in sorted(found, key=lambda c: c.at):
        same = next((c for c in out if abs(c.at - crash.at) <= SAME and _agree(c, crash)), None)
        if same is None:
            out.append(Crash(crash.at, crash.software, crash.error, crash.program_counter,
                             crash.screen, crash.errnum, set(crash.sources)))
            continue
        for name in ("software", "error", "program_counter", "screen", "errnum"):
            if getattr(same, name) is None:
                setattr(same, name, getattr(crash, name))
        same.sources |= crash.sources
    return sorted(out, key=lambda c: c.at, reverse=True)


def history(unit_id: int | None, entries=None, test_drive: bool = False) -> list[Crash]:
    """The device's crashes, newest first: those of `entries` (its error logs read now,
    diagnostics.read_errors; None or none: not read), those the copies on this computer keep
    and those its device log wrote (a test drive's own log; none for a device without a unit
    ID)."""
    from openexpress import history as device_log

    found = [_from_entry(e, e.source or "err_log")
             for e in entries or () if e.is_crash and e.at is not None]
    if unit_id:
        for line in device_log.read(unit_id, test_drive).lines:
            entry = device_log.crash_entry(line)
            if entry is not None and entry.is_crash and entry.at is not None:
                found.append(_from_entry(entry, DEVICE_LOG))
        for name, data in diagnostics.kept_files(unit_id, (*_ERR_LOG_NAMES, _BOOT_LOG_NAME)):
            if name.casefold() == _BOOT_LOG_NAME.casefold():
                found += _boot_log(data)
            else:
                found += [_from_entry(e, COPY) for e in diagnostics.parse_err_log(data, name)
                          if e.is_crash and e.at is not None]
    return merge(found)
