"""The activity journal: what this computer keeps of each activity of a device, so that the
Activities page can show weeks and a year even after the device deleted the files (an Edge 1050
keeps about 200 activities and deletes the oldest).
One file a device, activities.jsonl in its folder of the program's data (0600), JSON Lines:
- a line for each activity: its file's name and size (not read again), when it was written,
  where it came from (the device or a backup), the start, the time zone, the sport, the totals,
  the heart rate and the time in each zone (never the zones' bounds), the units the device
  shows, the shape of its track (scaled into 0-1000, no place on Earth: never a coordinate) and
  its elevation profile;
- a line for each connection it was written at: how many activities the device held, its oldest
  and newest; from them come the journal's range and its gaps.
The same activity read again (a backup, another connection) is one: its key is when its file
was made and the model that made it, never the device's serial number or Unit ID. Nothing here
decides whether the journal is kept (settings.json "activity_journal"): its callers do.
Has: nothing of the window."""
from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openexpress import applog, fit, paths

log = logging.getLogger(__name__)

NAME = "activities.jsonl"
VERSION = 1
SHAPE_POINTS = 200  # at most, after the track is simplified
SHAPE_SCALE = 1000  # the shape fills 0-SHAPE_SCALE along its longer side
PROFILE_POINTS = 120  # at most, along the distance
EARTH_RADIUS = 6_371_000.0  # metres
SOURCES = ("device", "backup")


def path(unit_id: int, test_drive: bool = False) -> Path:
    """The journal of a device; that of a test drive (Storage.test_drive), which carries the
    GarminDevice.xml of a real device, apart from that device's."""
    from openexpress.sync import records_dir

    return records_dir(unit_id, test_drive) / NAME


# --- an activity's line ------------------------------------------------------------------------

def shape(samples) -> dict | None:
    """The shape of a track without its place: the positions of `samples` (fit.Sample) laid
    flat around their middle, scaled so that the longer side spans 0-SHAPE_SCALE (whole
    numbers, x to the east, y to the south: north up), simplified to at most SHAPE_POINTS, and
    the width and height of the whole in metres. The first point is the start. None for fewer
    than two positions or a track within a metre."""
    found = [(s.latitude, s.longitude) for s in samples
             if s.latitude is not None and s.longitude is not None]
    if len(found) < 2:
        return None
    middle = math.radians(sum(lat for lat, _lon in found) / len(found))
    flat = [(math.radians(lon) * EARTH_RADIUS * math.cos(middle), math.radians(lat) * EARTH_RADIUS)
            for lat, lon in found]
    xs, ys = [x for x, _y in flat], [y for _x, y in flat]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if max(width, height) < 1:
        return None
    scale = SHAPE_SCALE / max(width, height)
    left, top = min(xs), max(ys)
    points: list[tuple[int, int]] = []
    for x, y in flat:
        point = (round((x - left) * scale), round((top - y) * scale))
        if not points or points[-1] != point:
            points.append(point)
    return {"width": round(width), "height": round(height),
            "points": [list(p) for p in simplified(points, SHAPE_POINTS)]}


def simplified(points: list[tuple[int, int]], most: int) -> list[tuple[int, int]]:
    """At most `most` of `points`, the first and the last always among them: the
    Douglas-Peucker line, its tolerance grown until few enough are left."""
    if len(points) <= most:
        return points
    tolerance = 0.5
    while True:
        kept = _douglas_peucker(points, tolerance)
        if len(kept) <= most:
            return kept
        tolerance *= 1.5


def _douglas_peucker(points: list[tuple[int, int]], tolerance: float) -> list[tuple[int, int]]:
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (x1, y1), (x2, y2) = points[first], points[last]
        length = math.hypot(x2 - x1, y2 - y1)
        far, farthest = 0.0, None
        for i in range(first + 1, last):
            x, y = points[i]
            d = (abs((x2 - x1) * (y1 - y) - (x1 - x) * (y2 - y1)) / length if length
                 else math.hypot(x - x1, y - y1))
            if d > far:
                far, farthest = d, i
        if farthest is not None and far > tolerance:
            keep[farthest] = True
            stack += [(first, farthest), (farthest, last)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def profile(samples) -> list[int] | None:
    """The elevation of an activity at up to PROFILE_POINTS places evenly along its distance
    (along its time when the records give no distance), in whole metres; None for fewer than
    two altitudes."""
    along = [(s.distance, s.altitude) for s in samples
             if s.distance is not None and s.altitude is not None]
    if len(along) < 2 or along[-1][0] <= along[0][0]:
        along = [(float(s.time), s.altitude) for s in samples if s.altitude is not None]
    if len(along) < 2 or along[-1][0] <= along[0][0]:
        return None
    start, end = along[0][0], along[-1][0]
    count = min(PROFILE_POINTS, len(along))
    found, j = [], 0
    for i in range(count):
        at = start + (end - start) * i / (count - 1)
        while j + 2 < len(along) and along[j + 1][0] < at:
            j += 1
        (a, high_a), (b, high_b) = along[j], along[j + 1]
        part = 0.0 if b <= a else min(max((at - a) / (b - a), 0.0), 1.0)
        found.append(round(high_a + (high_b - high_a) * part))
    return found


def key_of(data: fit.ActivityData) -> str | None:
    """What tells one activity from another wherever its file is found: when the file was made
    and the model that made it; None without that time."""
    if data.created is None:
        return None
    return f"{data.created}-{data.product if data.product is not None else ''}"


def _number(value, digits: int = 0):
    if value is None:
        return None
    return round(value) if digits == 0 else round(value, digits)


def entry(data: fit.ActivityData, name: str, size: int, source: str,
          seen: datetime) -> dict | None:
    """The line of an activity read from a file named `name` of `size` bytes, found on the
    device or in a backup (`source`) at the connection of `seen`; None for a file that is not
    a whole activity (still being written, damaged, without a session or its time)."""
    key = key_of(data)
    if data.state is not fit.FileState.WHOLE or key is None or data.start is None:
        return None
    heart_rates = [s.heart_rate for s in data.samples if s.heart_rate is not None]
    units = None if data.units is None else {"distance": data.units.distance,
                                             "elevation": data.units.elevation,
                                             "speed": data.units.speed}
    track = shape(data.samples)
    return {
        "v": VERSION, "kind": "activity", "key": key,
        "file": {"name": name, "size": size}, "from": source,
        "seen": seen.astimezone().isoformat(timespec="seconds"),
        "start": data.start, "utc_offset": data.utc_offset,
        "sport": data.sport, "sub_sport": data.sub_sport,
        "timer_time": _number(data.timer_time), "elapsed_time": _number(data.elapsed_time),
        "distance": _number(data.distance), "ascent": data.ascent, "descent": data.descent,
        "min_altitude": _number(data.min_altitude), "max_altitude": _number(data.max_altitude),
        "avg_heart_rate": data.avg_heart_rate, "max_heart_rate": data.max_heart_rate,
        "zones": None if data.zones is None else [round(t) for t in data.zones.times],
        "avg_speed": _number(data.avg_speed, 3), "calories": data.calories,
        "has_track": track is not None,
        "has_heart_rate": data.avg_heart_rate is not None or bool(heart_rates),
        "units": units, "shape": track, "profile": profile(data.samples),
    }


def connection(now: datetime, on_device: int, oldest: int | None,
               newest: int | None) -> dict:
    """The line of a connection: how many activities the device held, the start of its oldest
    and newest the journal knows."""
    return {"v": VERSION, "kind": "connection", "at": now.astimezone().isoformat(
        timespec="seconds"), "on_device": on_device, "oldest": oldest, "newest": newest}


# --- the file ------------------------------------------------------------------------------

def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _time(value) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def valid(raw: str) -> dict | None:
    """A line as the program writes it; None for anything else (not JSON, another version or
    kind, a field of the wrong kind): a reader skips it, a writer keeps it as it is."""
    try:
        item = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(item, dict) or item.get("v") != VERSION:
        return None
    if item.get("kind") == "connection":
        ok = (isinstance(item.get("at"), str) and _count(item.get("on_device"))
              and _time(item.get("oldest")) and _time(item.get("newest")))
    elif item.get("kind") == "activity":
        file = item.get("file")
        ok = (isinstance(item.get("key"), str) and isinstance(file, dict)
              and isinstance(file.get("name"), str) and _count(file.get("size"))
              and item.get("from") in SOURCES and _time(item.get("start"))
              and item.get("start") is not None)
    else:
        ok = False
    return item if ok else None


@dataclass
class Journal:
    """A journal as read: its activities by key (a key written again: the last line), its
    connections in order, and how many lines were skipped."""

    activities: dict[str, dict] = field(default_factory=dict)
    connections: list[dict] = field(default_factory=list)
    skipped: int = 0
    size: int = 0  # bytes

    @property
    def files(self) -> dict[tuple[str, int], dict]:
        """The activities by their file's name (casefold) and size: a file of these is not read
        again."""
        return {(a["file"]["name"].casefold(), a["file"]["size"]): a
                for a in self.activities.values()}

    def oldest(self) -> int | None:
        """The start of the oldest activity: where the journal's range begins."""
        return min((a["start"] for a in self.activities.values()), default=None)

    def gaps(self) -> list[tuple[int, int]]:
        """The spans whose activities may have gone from the device unseen: between the newest
        a connection knew and the oldest the next one found, when that one is newer. A device
        without any activity at a connection has none (the journal stays as it was)."""
        found, newest = [], None
        for c in self.connections:
            if c["oldest"] is None:
                continue
            if newest is not None and c["oldest"] > newest:
                found.append((newest, c["oldest"]))
            newest = c["newest"] if newest is None else max(newest, c["newest"] or newest)
        return found


def _lines(data: bytes) -> Iterator[tuple[bytes, dict | None]]:
    for raw in data.split(b"\n"):
        if raw.strip():
            try:
                yield raw, valid(raw.decode("utf-8"))
            except UnicodeDecodeError:
                yield raw, None


def read(where: Path) -> Journal:
    """The journal in `where`; an empty one when there is none."""
    found = Journal()
    try:
        data = where.read_bytes()
    except FileNotFoundError:
        return found
    found.size = len(data)
    for _raw, item in _lines(data):
        if item is None:
            found.skipped += 1
        elif item["kind"] == "activity":
            found.activities.pop(item["key"], None)  # the last line of a key, in its place
            found.activities[item["key"]] = item
        else:
            found.connections.append(item)
    return found


def _append(where: Path, line: dict) -> None:
    """One line added at the end of the journal (made 0600 when it is new), under the lock of
    the device records (Forget Device takes it too)."""
    from openexpress.history import records_locked

    text = json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n"
    with records_locked():
        try:
            with open(where, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell():
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        text = "\n" + text  # a write cut short: its piece a line of its own
        except FileNotFoundError:
            pass
        paths.make_dir(where.parent)
        fd = os.open(where, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            os.fchmod(fd, 0o600)
            f.write(text)


def journals() -> list[Path]:
    """Every journal on this computer: of each device and of a test drive."""
    devices = paths.data_dir() / "devices"
    try:
        folders = [p for p in devices.iterdir() if p.is_dir() and not p.is_symlink()]
    except OSError:
        return []
    return [p for folder in folders for p in (folder / NAME, folder / "test-drive" / NAME)
            if p.is_file()]


def count(where: Path) -> int:
    """How many activities a journal holds."""
    return len(read(where).activities)


def delete(where: list[Path]) -> int:
    """These journals deleted, under the lock of the device records: how many activities they
    held."""
    from openexpress.history import records_locked

    held = 0
    with records_locked():
        for p in where:
            held += count(p)
            p.unlink(missing_ok=True)
    log.info("activity journal: %d deleted, %d activities", len(where), held)
    return held


# --- at a connection -----------------------------------------------------------------------

@dataclass
class Added:
    """What a reading at a connection did: activities added, files on the device, files not
    whole (still being written, damaged, no FIT file), and how long it took."""

    added: int = 0
    on_device: int = 0
    not_whole: int = 0
    seconds: float = 0.0


def add(device, storage, backups: Path | None, *, now: datetime | None = None,
        test_drive: bool = False, keep_going: Callable[[], bool] = lambda: True,
        read_file: Callable | None = None) -> Added:
    """The activity files of `device` (on `storage`, and of its backups in `backups`) that the
    journal does not have yet read and added, each line written as soon as it is read (a
    device unplugged meanwhile loses nothing written), then the line of this connection. Stops
    between two files when `keep_going()` says no (the journal turned off). An error of the
    device passes on (DeviceError). One line of the program's log: numbers and times only."""
    from openexpress import activities
    from openexpress.device import whole_reads

    started = time.monotonic()
    now = now or datetime.now()
    where = path(device.unit_id, test_drive)
    journal = read(where)
    known = journal.files
    found = activities.DeviceActivities(device, storage, backups)
    found.list_files()
    did = Added(on_device=sum(1 for a in found.listing if a.source is activities.Source.DEVICE))
    size = 0
    for a in found.listing:
        if (a.name.casefold(), a.size) in known:
            continue
        if not keep_going():
            log.info("activity journal: stopped after %d added (%.1f s)", did.added,
                     time.monotonic() - started)
            did.seconds = time.monotonic() - started
            return did
        if read_file is not None:
            data = read_file(a)
        elif a.source is activities.Source.DEVICE:
            data = whole_reads.whole_file(storage, a.path, a.size,
                                          lambda a=a: storage.read_at(a.path))
        else:
            data = found.read_file(a)
        size += len(data or b"")
        try:
            line = entry(fit.activity_data(data), a.name, a.size, a.source.value, now) \
                if data is not None else None
        except fit.FitError:
            line = None
        if line is None:
            did.not_whole += 1
            continue
        _append(where, line)
        known[(a.name.casefold(), a.size)] = line
        journal.activities[line["key"]] = line
        did.added += 1
    starts = [known[(a.name.casefold(), a.size)]["start"] for a in found.listing
              if a.source is activities.Source.DEVICE and (a.name.casefold(), a.size) in known]
    _append(where, connection(now, did.on_device, min(starts, default=None),
                              max(starts, default=None)))
    did.seconds = time.monotonic() - started
    log.info("activity journal: %d added, %d on the device, %d not whole, %.1f s (%s read)",
             did.added, did.on_device, did.not_whole, did.seconds, applog.size(size))
    return did
