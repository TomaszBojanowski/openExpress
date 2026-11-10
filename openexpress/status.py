"""The facts of the page Device Status (gui/status_page.py), read off the window's thread.

What the window read of the device at its opening (diagnostics.AtOpen, in the session), the
device log of this computer (history: the storage by kind of file, counted at the connection,
or again after a removal, and the software versions over time), the crashes from every source
(crashes.history), what can be freed (freeing.plan: the memory dumps with their times, to tell
the newest, and the updates the device took in but did not install, from the update server's
answer) and the copies of the diagnostic files kept here. What could not be read is None, never
none or 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise

from openexpress import crashes, diagnostics, history

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
    recounted: bool = False  # categories and space counted again after a removal (recount)
    updates_why: str | None = None  # why the update files were not checked (freeing.Plan.why)
    connected: bool = True  # False: a device not connected, from its records (read_kept)
    dumps_at_last: tuple[int, int] | None = None  # not connected: (count, bytes) at the last
    # connection; None: not known

    def older_dumps(self) -> list[tuple[str, int, float]] | None:
        """The memory dumps but the newest (which always stays); None: not known
        (freeing.older_dumps)."""
        from openexpress import freeing

        return freeing.older_dumps(self.dumps)


@dataclass
class Recount:
    """The kinds of file and the space of the device counted again (after a removal): what
    the page shows instead of the count of the connection (the device log's line)."""

    categories: dict[str, int]
    free: int | None
    capacity: int | None
    at: datetime


def recount(device, storage) -> Recount:
    """Count the kinds of file, the space and the dumps again, as at the connection, and put
    them in the device log's line of this connection (history.note_counted)."""
    now = datetime.now()
    categories, capacity, free = history.note_counted(device, storage, now)
    return Recount(categories, free, capacity, now)


def read(session, wait: float = READING_WAIT_S) -> Facts:
    """The facts of the open device, once the window's reading of its error logs is done (or
    after `wait` seconds). In the background: what can be freed (freeing.plan: the memory
    dumps listed again for their times, the update server asked for the updates the device
    did not install), nothing else asked of the device. After a removal, the kinds of file and
    the space counted then (session.recount)."""
    from openexpress import freeing, known_devices

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
    counted = getattr(session, "recount", None)
    if counted is not None:
        free = counted.free if counted.free is not None else free
        capacity = counted.capacity if counted.capacity is not None else capacity
        categories, counted_at = dict(counted.categories), counted.at
    elif newest is not None:
        categories, counted_at = dict(newest["categories"]), history.counted_time(newest)
    else:
        categories, counted_at = {}, None
    errors_known = found is not None and found.errors_known
    found_crashes = getattr(session, "crash_history", None)
    if found_crashes is None:
        found_crashes = crashes.history(unit, found.entries if errors_known else None,
                                        test_drive=test_drive)
    plan = (freeing.plan(storage, device, getattr(session, "client", None))
            if storage is not None else None)
    dumps = plan.dumps if plan is not None else None
    copies = diagnostics.copies_facts(unit) if unit else (0, 0, None)
    hidden = bool(found is not None and found.hidden_logs)
    has = (not errors_known or bool(found.entries) or bool(found_crashes) or bool(dumps)
           or copies[0] > 0 or hidden)
    return Facts(
        via="drive" if getattr(session, "mass_storage", False) else "mtp",
        free=free, capacity=capacity,
        categories=categories, counted_at=counted_at,
        crashes=found_crashes, errors_known=errors_known, dumps=dumps,
        events=events(found_crashes, lines, known.first_seen if known else None),
        hidden_logs=hidden, copies=copies,
        not_installed=[*plan.updates, *plan.waiting] if plan is not None else [],
        has_diagnostics=has,
        recounted=counted is not None or (newest is not None and "counted" in newest),
        updates_why=plan.why if plan is not None else None)


def read_kept(unit_id: int) -> Facts | None:
    """The facts of a remembered device that is not connected, from the records of this
    computer only: the last line of its device log (its storage, its memory dumps, whether its
    error logs were read: "crash" left out of the line when not), its crashes (the copies and
    the device log), its software updates and first connection, its copies. None when there is
    no record of it. What the records do not tell is None, never none or 0."""
    from openexpress import known_devices

    lines = history.read(unit_id).lines
    copies = diagnostics.copies_facts(unit_id)
    if not lines and not copies[0]:
        return None
    newest = lines[-1] if lines else None
    found_crashes = crashes.history(unit_id)
    known = known_devices.load().get(unit_id)
    errors_known = newest is not None and "crash" in newest
    dumps = newest.get("dumps") if newest is not None else None
    dumps_at_last = (dumps["count"], dumps["bytes"]) if dumps is not None else None
    return Facts(
        via=newest["via"] if newest is not None else "mtp",
        free=newest.get("free") if newest is not None else None,
        capacity=newest.get("capacity") if newest is not None else None,
        categories=dict(newest["categories"]) if newest is not None else {},
        counted_at=history.counted_time(newest) if newest is not None else None,
        crashes=found_crashes, errors_known=errors_known, dumps=None,
        events=events(found_crashes, lines, known.first_seen if known else None),
        hidden_logs=False, copies=copies, not_installed=[],
        has_diagnostics=bool(errors_known or found_crashes or copies[0] or dumps_at_last),
        connected=False, dumps_at_last=dumps_at_last,
        recounted=newest is not None and "counted" in newest)
