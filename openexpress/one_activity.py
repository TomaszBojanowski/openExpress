"""What the page of one activity shows on top of it, from its file (fit.ActivityData) or, its file
gone from the device and its backups, from its line of the activity journal
(activity_journal.py): its kind, when it started and on which clock, its numbers (distance,
time, speed, ascent, calories, the rest time of a strength workout), the lowest and highest
point with the elevation profile, the heart rate (average, highest, the time in each zone), the
shape of its track (only the journal's: the page of a file draws its map) and the units its
device shows. Its words are in gui/words.py. Nothing is read from a device and nothing is
written.
Has: nothing of the window."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from openexpress import activity_journal, since_last


@dataclass(frozen=True)
class Overview:
    """One activity as its page shows it on top. None: not known."""

    sport: int | None
    sub_sport: int | None
    start: int | None  # a Unix time
    utc_offset: int | None  # seconds the device's clock was ahead of UTC
    distance: float | None  # metres
    time: float | None  # seconds of the timer (the elapsed time when the file gives no timer)
    speed: float | None  # metres a second, worked out from the distance and time if not given
    ascent: int | None  # metres
    calories: int | None
    rest_time: float | None  # seconds
    lowest: float | None  # metres
    highest: float | None
    profile: tuple[int, ...] | None  # metres along the distance
    avg_heart_rate: int | None
    max_heart_rate: int | None
    zones: tuple[float, ...] | None  # seconds below zone 1, then in zones 1 to 5
    shape: tuple[tuple[int, int], ...] | None  # the journal's (0-1000, north up)
    units: since_last.Units


def _speed(speed, distance, time) -> float | None:
    if isinstance(speed, (int, float)) and speed > 0:
        return float(speed)
    if isinstance(distance, (int, float)) and distance > 0 and time:
        return distance / time
    return None


def _time(timer, elapsed) -> float | None:
    return timer if timer is not None else elapsed


def of_file(data) -> Overview:
    """An activity as its file holds it (fit.ActivityData): the lowest and highest point from
    its records when its sessions do not say them, its elevation profile from its records."""
    altitudes = [s.altitude for s in data.samples if s.altitude is not None]
    lowest = data.min_altitude if data.min_altitude is not None else (
        min(altitudes) if altitudes else None)
    highest = data.max_altitude if data.max_altitude is not None else (
        max(altitudes) if altitudes else None)
    profile = activity_journal.profile(data.samples)
    units = data.units
    time = _time(data.timer_time, data.elapsed_time)
    return Overview(
        sport=data.sport, sub_sport=data.sub_sport, start=data.start, utc_offset=data.utc_offset,
        distance=data.distance, time=time, speed=_speed(data.avg_speed, data.distance, time),
        ascent=data.ascent, calories=data.calories, rest_time=data.rest_time, lowest=lowest,
        highest=highest, profile=tuple(profile) if profile else None,
        avg_heart_rate=data.avg_heart_rate, max_heart_rate=data.max_heart_rate,
        zones=tuple(data.zones.times) if data.zones is not None else None, shape=None,
        units=since_last.Units(
            miles=units is not None and units.distance == since_last.STATUTE,
            feet=units is not None and units.elevation == since_last.STATUTE))


def _points(shape) -> tuple[tuple[int, int], ...] | None:
    points = shape.get("points") if isinstance(shape, dict) else None
    if not isinstance(points, list) or len(points) < 2:
        return None
    try:
        return tuple((int(x), int(y)) for x, y in points)
    except (TypeError, ValueError):
        return None


def _numbers(values) -> tuple | None:
    if not isinstance(values, list) or not values:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return None
    return tuple(values)


def of_journal(entry: dict) -> Overview:
    """An activity as its line of the journal kept it (activity_journal.entry)."""
    time = _time(entry.get("timer_time"), entry.get("elapsed_time"))
    profile = _numbers(entry.get("profile"))
    return Overview(
        sport=entry.get("sport"), sub_sport=entry.get("sub_sport"), start=entry.get("start"),
        utc_offset=entry.get("utc_offset"), distance=entry.get("distance"), time=time,
        speed=_speed(entry.get("avg_speed"), entry.get("distance"), time),
        ascent=entry.get("ascent"), calories=entry.get("calories"), rest_time=None,
        lowest=entry.get("min_altitude"), highest=entry.get("max_altitude"),
        profile=profile if profile and len(profile) >= 2 else None,
        avg_heart_rate=entry.get("avg_heart_rate"), max_heart_rate=entry.get("max_heart_rate"),
        zones=_numbers(entry.get("zones")),
        shape=_points(entry.get("shape")) if entry.get("has_track") else None,
        units=since_last.units_of([entry]))


def started(shown: Overview) -> datetime | None:
    """When it started, on the clock the device had where it was (its time zone), else on
    this computer's; None when not known."""
    if not isinstance(shown.start, (int, float)):
        return None
    if isinstance(shown.utc_offset, (int, float)):
        return datetime.fromtimestamp(shown.start + shown.utc_offset, UTC).replace(tzinfo=None)
    return datetime.fromtimestamp(shown.start)
