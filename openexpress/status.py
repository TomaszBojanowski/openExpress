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


def has_diagnostics(errors_known: bool, error_logs: int | None, crashes_found: list,
                    dumps: int | None, copies: int, hidden: bool) -> bool:
    """Whether the page shows the device's diagnostics, else "No Diagnostic Data": its error
    logs not read (the page says so), an error log, also one without entries (`error_logs`;
    None: not known), a crash from any source, a memory dump (`dumps`; None: not known, so not
    "none"), a copy kept here, or crash logs to copy. One rule for the device connected and
    for its records."""
    return (not errors_known or error_logs is None or error_logs > 0 or bool(crashes_found)
            or dumps is None or dumps > 0 or copies > 0 or hidden)


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
                 same_before=_same_in_a_row(c, found[n + 1:]))
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


def _same_in_a_row(crash: crashes.Crash, older: list[crashes.Crash]) -> int:
    """How many crashes just before this one, one after another, had its error: "the same error
    as the one before" is the crash just before."""
    count = 0
    for before in older:
        if not crashes.same_error(crash, before):
            break
        count += 1
    return count


@dataclass
class Facts:
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
    recounted: bool = False  # categories and space counted again after a write (recount)
    updates_why: str | None = None  # why the update files were not checked (freeing.Plan.why)
    connected: bool = True  # False: a device not connected, from its records (read_kept)
    dumps_removable: bool = True  # its older dumps may be removed (freeing.dumps_removable)
    dumps_at_last: tuple[int, int] | None = None  # not connected: (count, bytes) at the last
    # connection; None: not known
    device_busy: bool = False  # read while another task ran: what needs the device left out

    def older_dumps(self) -> list[tuple[str, int, float]] | None:
        """The memory dumps but the newest (which always stays); None: not known
        (freeing.older_dumps)."""
        from openexpress import freeing

        return freeing.older_dumps(self.dumps)


@dataclass
class Recount:
    """The kinds of file, the space and the memory dumps of the device counted again (after a
    write to it, a removal too): what the page, the device page and the report show instead of
    the count of the connection (the device log's line, the window's reading at the opening)."""

    categories: dict[str, int]
    free: int | None
    capacity: int | None
    at: datetime
    dumps: list[tuple[str, int]] | None = None  # None: not listed


def recount(device, storage, removed: dict | None = None) -> Recount:
    """Count the kinds of file, the space and the dumps again, as at the connection, and put
    them in the device log's line of this connection (history.note_counted; after a removal,
    `removed` by kind: history.after_removal, which puts the line right by what was removed
    when the count fails). `at`: when the count began."""
    now = datetime.now()
    if removed:
        categories, capacity, free, dumps = history.after_removal(device, storage, removed, now)
    else:
        categories, capacity, free, dumps = history.note_counted(device, storage, now)
    return Recount(categories, free, capacity, now, dumps)


def known_capacity(session) -> int | None:
    """The capacity of the open device as read before (it does not change); None: not yet."""
    space = getattr(session, "space", None)
    return space[1] if space is not None else None


def counted_again(session, counted: Recount) -> bool:
    """After a removal or another write: what was counted again kept in the session, for the
    page, the row of the device page and the report alike (the space, and the memory dumps the
    window read at the opening, which Refresh keeps: diagnostics.at_open). A count begun before
    the one kept already is not kept (it ended later, but tells an older state): False."""
    kept = getattr(session, "recount", None)
    if kept is not None and kept.at > counted.at:
        return False
    session.recount = counted
    # A free space not told by this count is not known now; a capacity not told is still the
    # one known: it does not change.
    session.space = (counted.free, counted.capacity if counted.capacity is not None
                     else known_capacity(session))
    found = getattr(session, "diagnostics", None)
    if found is not None:
        # Not listed by this count: not known now (not those of the opening).
        found.dumps = counted.dumps
        found.dumps_known = counted.dumps is not None
    return True


def session_crashes(session) -> list[crashes.Crash]:
    """The crashes of the open device from every source (crashes.history): those of its error
    logs as the window read them at the opening (none when they could not all be read), the
    copies on this computer and its device log. Counted anew each time (after Copy Crash Logs
    the copies hold its boot log): the page, its row and the report alike."""
    found = getattr(session, "diagnostics", None)
    errors_known = found is not None and found.errors_known
    unit = getattr(getattr(session, "device", None), "unit_id", None)
    return crashes.history(unit, found.entries if errors_known else None,
                           test_drive=bool(getattr(getattr(session, "storage", None),
                                                   "test_drive", False)))


@dataclass
class Told:
    """What the summary of the page Device Status and the row of the device page that leads to
    it are told from: one answer for both (told)."""

    crashes: list[crashes.Crash]
    errors_known: bool
    free: int | None
    capacity: int | None
    categories: dict[str, int]
    counted_at: datetime | None
    recounted: bool
    hidden: bool
    copies: tuple[int, int, datetime | None]
    has_diagnostics: bool


def told(session) -> Told:
    """The crashes of every source (session_crashes), whether the error logs were read, the
    space the window read last (session.space: the one source of it), the kinds of file
    counted last (kinds_counted), and whether the device has diagnostic data (has_diagnostics,
    by what the window read at the opening, the copies and the crash logs to copy). Nothing is
    asked of the device: the page, the row of the device page and the report alike."""
    found = getattr(session, "diagnostics", None)
    device, storage = getattr(session, "device", None), getattr(session, "storage", None)
    unit = getattr(device, "unit_id", None)
    test_drive = bool(getattr(storage, "test_drive", False))
    free, capacity = getattr(session, "space", None) or (None, None)
    categories, counted_at, recounted = kinds_counted(session)
    errors_known = found is not None and found.errors_known
    found_crashes = session_crashes(session)
    dumps = found.dumps if found is not None and found.dumps_known else None
    copies = diagnostics.copies_facts(unit, test_drive) if unit else (0, 0, None)
    hidden = diagnostics.hidden_logs_to_copy(found is not None and found.hidden_logs, unit,
                                             test_drive)
    has = has_diagnostics(errors_known, found.error_logs if errors_known else None,
                          found_crashes, len(dumps) if dumps is not None else None,
                          copies[0], hidden)
    return Told(found_crashes, errors_known, free, capacity, categories, counted_at, recounted,
                hidden, copies, has)


def kinds_counted(session) -> tuple[dict[str, int], datetime | None, bool]:
    """The kinds of file of the open device, when they were counted, and whether that was
    after the connection: from this connection's line in the device log (history.
    connection_line; every count, of the window or the command line, puts it right), else
    from the window's own count (session.recount: a device the log does not keep). Nothing is
    asked of the device."""
    device, storage = getattr(session, "device", None), getattr(session, "storage", None)
    line = (history.connection_line(device, storage)
            if getattr(device, "unit_id", None) and storage is not None else None)
    counted = getattr(session, "recount", None)
    if line is not None:
        return (dict(line["categories"]), history.counted_time(line),
                "counted" in line or counted is not None)
    if counted is not None:
        return dict(counted.categories), counted.at, True
    return {}, None, False


def read(session, ask_device: bool = True) -> Facts:
    """The facts of the open device, once the window's reading of its error logs is done (the
    window reads the page only then: its results are in the session before it says it is
    done). In the background: what can be freed (freeing.plan: the memory dumps listed again
    for their times, the update server asked for the updates the device did not install),
    nothing else asked of the device: the space is the session's (the window reads it with
    the page). `ask_device` False while another task runs: nothing asked, what can be freed
    left out (device_busy)."""
    from openexpress import freeing, known_devices

    device, storage = session.device, session.storage
    unit = device.unit_id
    test_drive = bool(getattr(storage, "test_drive", False))
    lines = history.read(unit, test_drive).lines if unit else []
    # A test drive carries a real device's unit ID: when that device was first seen is not its.
    known = known_devices.load().get(unit) if unit and not test_drive else None
    t = told(session)
    plan = (freeing.plan(storage, device, getattr(session, "client", None))
            if storage is not None and ask_device else None)
    return Facts(
        free=t.free, capacity=t.capacity,
        categories=t.categories, counted_at=t.counted_at,
        crashes=t.crashes, errors_known=t.errors_known,
        dumps=plan.dumps if plan is not None else None,
        events=events(t.crashes, lines, known.first_seen if known else None),
        hidden_logs=t.hidden, copies=t.copies,
        not_installed=[*plan.updates, *plan.waiting] if plan is not None else [],
        has_diagnostics=t.has_diagnostics, recounted=t.recounted,
        updates_why=plan.why if plan is not None else None,
        dumps_removable=freeing.dumps_removable(device), device_busy=not ask_device)


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
    # A line written before it told its error logs: the device had one if it had entries.
    error_logs = (newest.get("error_logs", newest.get("errors")) if newest is not None
                  else None)
    has = has_diagnostics(errors_known, error_logs,
                          found_crashes, dumps_at_last[0] if dumps_at_last else None,
                          copies[0], diagnostics.hidden_logs_to_copy(
                              newest.get("hidden") if newest is not None else None, unit_id))
    return Facts(
        free=newest.get("free") if newest is not None else None,
        capacity=newest.get("capacity") if newest is not None else None,
        categories=dict(newest["categories"]) if newest is not None else {},
        counted_at=history.counted_time(newest) if newest is not None else None,
        crashes=found_crashes, errors_known=errors_known, dumps=None,
        events=events(found_crashes, lines, known.first_seen if known else None),
        hidden_logs=False, copies=copies, not_installed=[],
        has_diagnostics=has,
        connected=False, dumps_at_last=dumps_at_last,
        recounted=newest is not None and "counted" in newest)
