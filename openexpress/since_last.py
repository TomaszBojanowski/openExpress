"""What the Activities page shows of a device since its connection before this one, from its
activity journal (activity_journal.py): the activities started since then, newest first, and
their totals; the newest ones when none is new; how many earlier ones the device still holds;
the units the device shows; what the sync's record of this computer says of each file; and the
trivia of the new ones (how high they climbed, how far they went, against a well-known height or
length). Nothing is read from the device and nothing is written.
"The connection before this one" is the journal's last connection line of another connection:
when the journal read it, not when the cable went in (a connection while the journal was off is
read once it is on again). A journal with no such line takes the device log's (history.jsonl);
with neither, the journal has just started.
Has: nothing of the window."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

NEW_TILES = 6  # the new activities with a tile of their own, newest first
LATEST = 3  # the activities with a tile when none is new, or the journal has just started
STATUTE = 1  # display_measure of the FIT profile: 0 metric, 1 statute, 2 nautical

# The heights and lengths the trivia measures against, in metres, smallest first; their words
# are in gui/words.py (trivia_object). Checked against public sources.
HEIGHTS = (("statue-of-liberty", 93.0), ("great-pyramid", 138.5), ("eiffel-tower", 330.0),
           ("empire-state-building", 443.0), ("burj-khalifa", 828.0),
           ("table-mountain", 1086.0), ("mont-blanc", 4806.0), ("kilimanjaro", 5895.0),
           ("everest", 8849.0), ("edge-of-space", 100_000.0))
LENGTHS = (("marathon", 42_195.0), ("panama-canal", 82_000.0), ("grand-canyon", 446_000.0),
           ("rhine", 1_233_000.0), ("danube", 2_857_000.0), ("equator", 40_075_000.0))


@dataclass(frozen=True)
class Units:
    """The units the device shows (its newest activity's user profile): miles and feet when it
    says statute, else kilometres and metres (also without a profile)."""

    miles: bool = False
    feet: bool = False


@dataclass(frozen=True)
class Totals:
    distance: float  # metres
    time: float  # seconds of the timer (the elapsed time when the file gives no timer)
    ascent: float  # metres
    count: int


@dataclass(frozen=True)
class Trivia:
    """The new activities against a well-known height or length: `kind` "climbed" or
    "distance", how much (metres), the object and how many times it is, one decimal (at
    least 1.0)."""

    kind: str
    metres: float
    thing: str
    times: float


@dataclass
class Since:
    """The page's view of a device since its connection before this one."""

    since: float | None  # when the journal read the connection before this one; None: none
    new: list[dict] = field(default_factory=list)  # started after `since`, newest first
    latest: list[dict] = field(default_factory=list)  # newest first, when none is new
    count: int = 0  # activities in the journal
    oldest: int | None = None  # the start of the oldest
    earlier: int = 0  # activities whose file is on the device and that are not new
    units: Units = Units()
    uploaded: frozenset[str] = frozenset()  # file names (casefold) the sync's record has
    waiting: frozenset[str] = frozenset()  # file names (casefold) kept to upload again

    @property
    def started(self) -> bool:
        """No connection before this one, in the journal or the device log: the journal has
        just started (its first reading)."""
        return self.since is None

    def upload_state(self, activity: dict) -> str | None:
        """What the sync's record says of an activity's file: "uploaded" (this computer's
        record has it), "waiting" (kept to upload again after a failed upload), None (not
        known: it may be uploaded by a phone)."""
        name = activity["file"]["name"].casefold()
        if name in self.waiting:
            return "waiting"
        return "uploaded" if name in self.uploaded else None


def _timestamp(at: str) -> float | None:
    try:
        return datetime.fromisoformat(at).timestamp()
    except (TypeError, ValueError):
        return None


def connection_before(journal, conn: str | None, log_lines: list[dict]) -> float | None:
    """When the journal read the connection before this one (`conn`: this one's key, as the
    device log has it): its last connection line of another connection; the device log's
    last line of another connection when the journal has none; None with neither."""
    for line in reversed(journal.connections):
        if conn is None or line.get("conn") != conn:
            at = _timestamp(line.get("at"))
            if at is not None:
                return at
    for line in reversed(log_lines):
        if conn is None or line.get("conn") != conn:
            at = _timestamp(line.get("time"))
            if at is not None:
                return at
    return None


def units_of(activities: list[dict]) -> Units:
    """The units of the newest activity that says them."""
    for a in sorted(activities, key=lambda a: a["start"], reverse=True):
        units = a.get("units")
        if isinstance(units, dict):
            return Units(miles=units.get("distance") == STATUTE,
                         feet=units.get("elevation") == STATUTE)
    return Units()


def totals(activities: list[dict]) -> Totals:
    def time_of(a: dict) -> float:
        t = a.get("timer_time")
        return t if t is not None else (a.get("elapsed_time") or 0.0)

    return Totals(distance=sum(a.get("distance") or 0.0 for a in activities),
                  time=sum(time_of(a) for a in activities),
                  ascent=sum(a.get("ascent") or 0.0 for a in activities),
                  count=len(activities))


def _measured(metres: float, things) -> tuple[str, float] | None:
    """The largest of `things` not larger than `metres`, and how many times it is (one decimal,
    rounded down: never more than it is); None below the smallest."""
    fitting = [(name, size) for name, size in things if size <= metres]
    if not fitting:
        return None
    name, size = fitting[-1]
    return name, math.floor(metres / size * 10 + 1e-9) / 10


def trivia(activities: list[dict]) -> Trivia | None:
    """How high the activities climbed against a height; when that is less than the smallest,
    how far they went against a length; None when both are below the smallest. The same
    numbers give the same words (no chance)."""
    t = totals(activities)
    found = _measured(t.ascent, HEIGHTS)
    if found is not None:
        return Trivia("climbed", t.ascent, *found)
    found = _measured(t.distance, LENGTHS)
    if found is not None:
        return Trivia("distance", t.distance, *found)
    return None


def since_last(journal, conn: str | None, log_lines: list[dict],
               on_device: set[str] | None, uploaded: set[str] = frozenset(),
               waiting: set[str] = frozenset()) -> Since:
    """The view of a device (`journal`: its activity journal as read) at its connection `conn`
    (its key in the device log, whose lines are `log_lines`); `on_device`: the names
    (casefold) of the activity files on the device now, None when not known (then none is
    counted as earlier); `uploaded`, `waiting`: the names (casefold) the sync's record has as
    sent, and the copies kept to upload again."""
    activities = sorted(journal.activities.values(), key=lambda a: a["start"], reverse=True)
    before = connection_before(journal, conn, log_lines)
    view = Since(since=before, count=len(activities), oldest=journal.oldest(),
                 units=units_of(activities), uploaded=frozenset(uploaded),
                 waiting=frozenset(waiting))
    if before is not None:
        view.new = [a for a in activities if a["start"] > before]
    if not view.new:
        view.latest = activities[:LATEST]
    new_files = {a["file"]["name"].casefold() for a in view.new}
    if on_device is not None:
        view.earlier = len({name for name in on_device if name not in new_files})
    return view


def of_device(device, storage, on_device: set[str] | None) -> Since:
    """The view of the open device from its journal, its device log and the sync's record of
    this computer (all read here, nothing written)."""
    from openexpress import activity_journal, history
    from openexpress.device.storage import is_test_drive

    test_drive = is_test_drive(storage)
    journal = activity_journal.read(activity_journal.path(device.unit_id, test_drive))
    conn = history.connection_key(device, storage)
    log_lines = history.read(device.unit_id, test_drive).lines
    uploaded, waiting = upload_records(device.unit_id, test_drive)
    return since_last(journal, conn, log_lines, on_device, uploaded, waiting)


def upload_records(unit_id: int, test_drive: bool = False) -> tuple[set[str], set[str]]:
    """The activity files (names, casefold) the sync's record of this computer has as sent,
    and those kept to upload again; empty when it cannot be read."""
    from openexpress import sync

    try:
        state = sync.state_of(unit_id, test_drive)
        sent = set(state.completed.get(sync.ACTIVITY_TYPE, set()))
        kept = {p.name.casefold() for p in state.pending_files(sync.ACTIVITY_TYPE)}
    except (OSError, ValueError):
        return set(), set()
    return sent, kept


def local_day(activity: dict) -> datetime:
    """When an activity started, on the clock of the place it was (its time zone, else this
    computer's)."""
    offset = activity.get("utc_offset")
    if isinstance(offset, (int, float)):
        return datetime.fromtimestamp(activity["start"] + offset, UTC).replace(
            tzinfo=None)
    return datetime.fromtimestamp(activity["start"])

