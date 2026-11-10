"""The facts of the page Device Status (gui/status_page.py), read off the window's thread.

What the window read of the device at its opening (diagnostics.AtOpen, in the session), the
device log of this computer (history: the storage by kind of file, counted at the connection,
and the software versions over time), the crashes from every source (crashes.history), the
memory dumps with their times (to tell the newest), the copies of the diagnostic files kept here
and the updates the device took in but did not install (from the window's last check). What
could not be read is None, never none or 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise

from openexpress import crashes, diagnostics, history
from openexpress.device.storage import DeviceError, DeviceNotResponding

# Little free space: under 10 % of the capacity or 500 MB, whichever is smaller (devices hold
# from some megabytes to tens of gigabytes).
LITTLE_SHARE = 0.10
LITTLE_BYTES = 500_000_000
RECENT = timedelta(days=30)
SHOWN_EVENTS = 5
# A device log whose first line came this soon after the device was first seen began with that
# connection: its version is the one of then.
FIRST_LINE_WITHIN = timedelta(hours=1)
# How long the page waits for the window's reading of the error logs at the opening.
READING_WAIT_S = 120


def little_space(free: int | None, capacity: int | None) -> bool | None:
    """Whether the device has little free space left; None: not known."""
    if free is None or not capacity:
        return None
    return free < min(capacity * LITTLE_SHARE, LITTLE_BYTES)


def recent(found: list[crashes.Crash], now: datetime) -> list[crashes.Crash]:
    """The crashes of the last 30 days by the device's clock, newest first."""
    return [c for c in found if now - RECENT <= c.at <= now + timedelta(days=1)]


@dataclass
class Event:
    """One line of "Crashes and Updates"."""

    kind: str  # "crash", "update" (a new software version), "first" (the first connection)
    at: datetime  # local time: the device's for a crash, this computer's for the others
    crash: crashes.Crash | None = None
    same_before: int = 0  # crashes before it with the same error (crashes.same_error)
    software: str | None = None  # crash: the version then; update: the new one; first: then
    previous: str | None = None  # update: the version before


def events(found: list[crashes.Crash], lines: list[dict],
           first_seen: float | None) -> list[Event]:
    """The crashes (newest first, as crashes.history gives them), each new software version of
    the device log and the first connection: newest first. The first connection is when this
    computer first saw the device (`first_seen`, devices.json), not the log's first line, which
    may come long after; its version only when the log began with it; none without that time."""
    out = [Event("crash", c.at, crash=c, software=c.software,
                 same_before=sum(crashes.same_error(c, older) for older in found[n + 1:]))
           for n, c in enumerate(found)]
    for before, line in pairwise(lines):
        if line["software"] != before["software"]:
            out.append(Event("update", history.local_time(line), software=line["software"],
                             previous=before["software"]))
    if first_seen is not None:
        first = datetime.fromtimestamp(first_seen)
        began = bool(lines) and timedelta(0) <= history.local_time(lines[0]) - first <= \
            FIRST_LINE_WITHIN
        out.append(Event("first", first, software=lines[0]["software"] if began else None))
    return sorted(out, key=lambda e: e.at, reverse=True)


@dataclass
class Facts:
    via: str  # "mtp", "drive"
    free: int | None
    capacity: int | None
    categories: dict[str, int]  # history.CATEGORIES; {}: not counted
    counted_at: datetime | None  # when they were counted (local)
    crashes: list[crashes.Crash]
    errors_known: bool  # the error logs on the device were read
    dumps: list[tuple[str, int, float]] | None  # (path, size, time); None: not known
    events: list[Event]
    hidden_logs: bool  # Copy Crash Logs applies
    copies: tuple[int, int, datetime | None]  # copies kept here: how many, bytes, the last
    not_installed: list = field(default_factory=list)  # updates.SoftwareUpdate, file on it
    has_diagnostics: bool = True  # error logs, dumps, crashes or copies: else the empty page

    def older_dumps(self) -> list[tuple[str, int, float]] | None:
        """The memory dumps but the newest (which always stays); None: not known."""
        if self.dumps is None:
            return None
        return sorted(self.dumps, key=lambda d: d[2], reverse=True)[1:]


def read(session, updates=None, wait: float = READING_WAIT_S) -> Facts:
    """The facts of the open device, once the window's reading of its error logs is done (or
    after `wait` seconds). In the background: the memory dumps are listed again for their
    times; nothing else is asked of the device. `updates`: the window's last check
    (updates_cache)."""
    from openexpress import known_devices
    from openexpress.updates import not_installed

    reading = getattr(session, "diagnostics_reading", None)
    if reading is not None:
        reading.wait(wait)
    found = getattr(session, "diagnostics", None)
    device, storage = session.device, session.storage
    unit = device.unit_id
    test_drive = bool(getattr(storage, "test_drive", False))
    lines = history.read(unit, test_drive).lines if unit else []
    # A test drive carries a real device's unit ID: when that device was first seen is not its.
    known = known_devices.load().get(unit) if unit and not test_drive else None
    newest = lines[-1] if lines else None
    free, capacity = getattr(session, "space", None) or (None, None)
    if newest is not None:
        free = newest.get("free") if free is None else free
        capacity = newest.get("capacity") if capacity is None else capacity
    errors_known = found is not None and found.errors_known
    found_crashes = getattr(session, "crash_history", None)
    if found_crashes is None:
        found_crashes = crashes.history(unit, found.entries if errors_known else None)
    dumps = None
    if found is not None and found.dumps_known and storage is not None:
        try:
            dumps = [(path, e.size, e.mtime) for path, e in diagnostics.dump_files(storage)]
        except DeviceNotResponding:
            raise  # the page says it, as every page does
        except DeviceError:
            dumps = None
    copies = diagnostics.copies_facts(unit) if unit else (0, 0, None)
    hidden = bool(found is not None and found.hidden_logs)
    has = (not errors_known or bool(found.entries) or bool(found_crashes) or bool(dumps)
           or copies[0] > 0 or hidden)
    return Facts(
        via="drive" if getattr(session, "mass_storage", False) else "mtp",
        free=free, capacity=capacity,
        categories=dict(newest["categories"]) if newest is not None else {},
        counted_at=history.local_time(newest) if newest is not None else None,
        crashes=found_crashes, errors_known=errors_known, dumps=dumps,
        events=events(found_crashes, lines, known.first_seen if known else None),
        hidden_logs=hidden, copies=copies,
        not_installed=[u for u in not_installed(list(updates or ())) if u.on_device],
        has_diagnostics=has)
