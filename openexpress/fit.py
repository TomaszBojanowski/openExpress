"""Minimal FIT reader, written from the public FIT protocol: the data messages of a file with
their values by base type, whether a file is whole, and the messages the program needs (the
creation time in file_id, from which Garmin Connect's activity identifier for duplicate checks
comes, and TrueUp's callback).
"""

from __future__ import annotations

import math
import struct
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from enum import Enum, auto

from openexpress.i18n import _

FIT_EPOCH_OFFSET = 631065600  # 1989-12-31T00:00:00Z as Unix time

_MESG_FILE_ID = 0
_FIELD_TIME_CREATED = 4
# FIT profile: session (one part of an activity; a multisport activity has one per sport)
_MESG_SESSION = 18
_SESSION_START_TIME = 2
_SESSION_START_LAT, _SESSION_START_LONG = 3, 4  # semicircles
_SESSION_SPORT = 5
_SESSION_TIMER_TIME = 8  # milliseconds, pauses left out
_SESSION_DISTANCE = 9  # centimetres
_SESSION_ASCENT = 22  # metres
# FIT profile: record (one point of an activity)
_MESG_RECORD = 20
_RECORD_LAT, _RECORD_LONG = 0, 1  # semicircles
_RECORD_ALTITUDE, _RECORD_ENHANCED_ALTITUDE = 2, 78  # metres: value / 5 - 500
# FIT profile: the sport "multisport"
SPORT_MULTISPORT = 18
# The field number of a message's time (date_time) in every message of the FIT profile.
FIELD_TIMESTAMP = 253

# The base types of the FIT protocol by their number (the lowest five bits of the base type
# of a field definition): the struct format of one value and the value that means "invalid"
# (None for a string, which is invalid when empty, and for the floats, invalid as NaN).
_BASE_TYPES = {
    0: ("B", 0xFF),                 # enum
    1: ("b", 0x7F),                 # sint8
    2: ("B", 0xFF),                 # uint8
    3: ("h", 0x7FFF),               # sint16
    4: ("H", 0xFFFF),               # uint16
    5: ("i", 0x7FFFFFFF),           # sint32
    6: ("I", 0xFFFFFFFF),           # uint32
    7: ("s", None),                 # string: UTF-8, ends at its first zero byte
    8: ("f", None),                 # float32
    9: ("d", None),                 # float64
    10: ("B", 0),                   # uint8z
    11: ("H", 0),                   # uint16z
    12: ("I", 0),                   # uint32z
    13: ("B", 0xFF),                # byte
    14: ("q", 0x7FFFFFFFFFFFFFFF),  # sint64
    15: ("Q", 0xFFFFFFFFFFFFFFFF),  # uint64
    16: ("Q", 0),                   # uint64z
}
_BASE_TYPE_NUMBER = 0x1F

Value = int | float | str | tuple | None  # a tuple: an array, when asked


class FitError(Exception):
    pass


@dataclass
class FileId:
    time_created: int | None = None


@dataclass
class _Def:
    big_endian: bool
    global_num: int
    fields: list[tuple[int, int, int]]  # (field number, size, base type)
    size: int  # of its data messages, developer fields included
    # Its timestamp field, if it has one: (where it starts in a data message, size, base type).
    time_field: tuple[int, int, int] | None


def _read_uint(raw: bytes, big_endian: bool) -> int | None:
    if len(raw) not in (1, 2, 4):
        return None
    fmt = {1: "B", 2: "H", 4: "I"}[len(raw)]
    value = struct.unpack((">" if big_endian else "<") + fmt, raw)[0]
    invalid = (1 << (8 * len(raw))) - 1
    return None if value == invalid else value


def _value(raw: bytes, size: int, base_type: int, big_endian: bool,
           array: bool = False) -> Value:
    """The value of a field by its base type; None when it is the type's invalid value, of a
    size that does not fit its type, or cut off by the end of the file; an array is None too,
    or with `array` a tuple of its values (each invalid one None). A base type the protocol
    does not define is read as an unsigned integer of its size."""
    if len(raw) < size:
        return None
    kind = _BASE_TYPES.get(base_type & _BASE_TYPE_NUMBER)
    if kind is None:
        return _read_uint(raw, big_endian)
    fmt, invalid = kind
    if fmt == "s":
        return raw.split(b"\0", 1)[0].decode("utf-8", "replace") or None
    one = struct.calcsize(fmt)
    if array and size > one and size % one == 0:
        values = struct.unpack((">" if big_endian else "<") + fmt * (size // one), raw[:size])
        return tuple(None if v == invalid or (fmt in ("f", "d") and math.isnan(v)) else v
                     for v in values)
    if one != size:
        return None
    value = struct.unpack((">" if big_endian else "<") + fmt, raw)[0]
    if fmt in ("f", "d"):
        return None if math.isnan(value) else value
    return None if value == invalid else value


def iter_messages(data: bytes, wanted: Collection[int] | None = None,
                  arrays: Collection[int] = ()):
    """Yields (global message number, {field number: value}) for each data message, each value
    by its base type (_value; the arrays of the messages in `arrays` as tuples). A message after
    a compressed-timestamp header has the time that header gives it as its timestamp field
    (FIELD_TIMESTAMP), once a time came before it.
    `wanted`: only the messages of these global numbers; the others are stepped over without
    reading them, but for their time, which a compressed-timestamp header counts from.

    Malformed or truncated input raises FitError (never IndexError/struct.error).
    """
    try:
        yield from _iter_messages(data, wanted, arrays)
    except (IndexError, struct.error) as e:
        raise FitError(_("Damaged FIT file ({error})").format(error=e)) from e


def _check_header(data: bytes) -> int:
    """The size of the header of a FIT file; FitError when `data` is no FIT file."""
    if len(data) < 12:
        # TRANSLATORS: error reading a FIT activity file: too few bytes to be one
        raise FitError(_("File too short"))
    header_size = data[0]
    if header_size not in (12, 14) or data[8:12] != b".FIT":
        raise FitError(_("Not a FIT file"))
    return header_size


def _iter_messages(data: bytes, wanted: Collection[int] | None, arrays: Collection[int] = ()):
    header_size = _check_header(data)
    data_size = struct.unpack_from("<I", data, 4)[0]
    end = min(len(data), header_size + data_size)
    pos = header_size
    defs: dict[int, _Def] = {}
    last_time: int | None = None  # the time of the last message that had one
    while pos < end:
        hdr = data[pos]
        pos += 1
        offset = None
        if hdr & 0x80:  # compressed timestamp: a regular data message
            local = (hdr >> 5) & 0x03
            offset = hdr & 0x1F
            is_def = False
            has_dev = False
        else:
            local = hdr & 0x0F
            is_def = bool(hdr & 0x40)
            has_dev = bool(hdr & 0x20)
        if is_def:
            if pos + 5 > end:
                break
            big_endian = data[pos + 1] == 1
            global_num = struct.unpack_from(">H" if big_endian else "<H", data, pos + 2)[0]
            nfields = data[pos + 4]
            pos += 5
            if pos + 3 * nfields > end:
                raise FitError(_("Truncated FIT definition message"))
            fields = []
            time_field = None
            size = 0
            for _field in range(nfields):
                num, field_size, base_type = data[pos], data[pos + 1], data[pos + 2]
                if num == FIELD_TIMESTAMP and time_field is None:
                    time_field = (size, field_size, base_type)
                fields.append((num, field_size, base_type))
                size += field_size
                pos += 3
            if has_dev:
                if pos + 1 > end:
                    raise FitError(_("Truncated FIT definition message"))
                ndev = data[pos]
                pos += 1
                if pos + 3 * ndev > end:
                    raise FitError(_("Truncated FIT definition message"))
                for _dev in range(ndev):
                    size += data[pos + 1]
                    pos += 3
            defs[local] = _Def(big_endian, global_num, fields, size, time_field)
            continue
        d = defs.get(local)
        if d is None:
            raise FitError(_("Data message without a definition (local type {local})")
                           .format(local=local))
        if offset is not None:
            # The offset takes the place of the lowest five bits of the time before it; one
            # smaller than them is in the next 32 seconds.
            if last_time is not None:
                last_time += (offset - last_time) & 0x1F
        elif d.time_field is not None:
            start, field_size, base_type = d.time_field
            time = _value(data[pos + start : pos + start + field_size], field_size, base_type,
                          d.big_endian)
            if isinstance(time, int):
                last_time = time
        if wanted is not None and d.global_num not in wanted:
            pos += d.size
            continue
        values: dict[int, Value] = {}
        p = pos
        array = d.global_num in arrays
        for num, field_size, base_type in d.fields:
            values[num] = _value(data[p : p + field_size], field_size, base_type, d.big_endian,
                                 array)
            p += field_size
        if offset is not None and last_time is not None:
            values[FIELD_TIMESTAMP] = last_time
        yield d.global_num, values
        pos += d.size


# CRC-16 of the FIT protocol, four bits at a time.
_CRC_TABLE = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


class Problem(Enum):
    """Why data are not a whole FIT file (check); its words: NotWhole.words()."""
    NO_HEADER = auto()
    AFTER_THE_END = auto()  # data after the end that are no FIT file
    CUT_SHORT = auto()
    CRC = auto()  # the CRC does not match


@dataclass(frozen=True)
class NotWhole:
    """What check found: the problem, the bytes read, and the bytes the header declares (cut
    short: header, data and CRC) or those after the whole FIT data (after the end)."""

    problem: Problem
    read: int
    declared: int = 0
    after: int = 0

    def words(self) -> str:
        """For the log: sizes, never the content."""
        if self.problem is Problem.NO_HEADER:
            return f"no FIT header ({self.read} B read)"
        if self.problem is Problem.AFTER_THE_END:
            return f"{self.after} B after the whole FIT data are no FIT file"
        if self.problem is Problem.CUT_SHORT:
            return f"{self.read} B read, {self.declared} B declared"
        return f"the CRC does not match ({self.read} B read)"


def check(data: bytes) -> NotWhole | None:
    """Why `data` is not a whole FIT file, or None when it is: every part (a file may chain
    several) has a header of 12 or 14 bytes with ".FIT", the size its header declares (header,
    data and the CRC of 2 bytes) and that CRC; zeros after the last part are padding. Cut
    short: a header declares more bytes than were read, as when the device was still writing
    the file; else no header, data after the end that are no FIT file, or a CRC that does not
    match."""
    pos = 0
    while True:
        if pos and not any(data[pos:]):
            return None  # zeros after whole FIT data: padding, nothing cut short
        if len(data) - pos < 12 or data[pos] not in (12, 14) or data[pos + 8:pos + 12] != b".FIT":
            if pos == 0:
                return NotWhole(Problem.NO_HEADER, len(data))
            return NotWhole(Problem.AFTER_THE_END, len(data), after=len(data) - pos)
        header = data[pos]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        end = pos + header + size + 2
        if end > len(data):
            return NotWhole(Problem.CUT_SHORT, len(data), declared=end)
        (crc,) = struct.unpack_from("<H", data, end - 2)
        if crc16(data[pos:end - 2]) != crc:
            return NotWhole(Problem.CRC, len(data))
        if end == len(data):
            return None
        pos = end


def not_whole(data: bytes) -> tuple[bool, str] | None:
    """Why `data` is not a whole FIT file (check), as (cut short, the words for the log), or
    None when it is."""
    found = check(data)
    return None if found is None else (found.problem is Problem.CUT_SHORT, found.words())


def incompleteness(data: bytes) -> str | None:
    """Why `data` is not a whole FIT file (not_whole), for the log; None when it is."""
    found = not_whole(data)
    return found[1] if found else None


def find_message(data: bytes, global_num: int) -> dict[int, Value] | None:
    return next((v for g, v in iter_messages(data) if g == global_num), None)


def _whole_number(value: Value) -> int | None:
    """A time or a count of a field: only an integer (a field of another base type: None)."""
    return value if isinstance(value, int) else None


def parse_file_id(data: bytes) -> FileId:
    v = find_message(data, _MESG_FILE_ID)
    if v is None:
        # TRANSLATORS: error reading a FIT file; file_id is the name of a FIT message, keep it
        raise FitError(_("No file_id message"))
    return FileId(time_created=_whole_number(v.get(_FIELD_TIME_CREATED)))


MESG_CALLBACK_DELAY = 197  # Garmin-internal message used by TrueUp


def true_up_time(data: bytes) -> int | None:
    """Unix time at which Connect asks the client to check back (TrueUp)."""
    v = find_message(data, MESG_CALLBACK_DELAY)
    t = _whole_number(v.get(0)) if v else None
    return None if t is None else t + FIT_EPOCH_OFFSET


class FileState(Enum):
    """How a FIT file is, as not_whole finds it (a file that is no FIT file: FitError)."""
    WHOLE = "whole"
    STILL_BEING_WRITTEN = "still being written"  # cut short: the device was writing it
    DAMAGED = "damaged"  # a CRC that does not match, or data that cannot be read


@dataclass(frozen=True)
class ActivitySummary:
    """What an activity file says of itself (summary). Times are Unix times; None: not said."""

    state: FileState
    created: int | None = None  # file_id's time_created
    start: int | None = None  # the start of its first session
    sport: int | None = None  # the FIT sport of its sessions; several: SPORT_MULTISPORT
    timer_time: float | None = None  # seconds, pauses left out
    distance: float | None = None  # metres
    ascent: int | None = None  # metres
    has_positions: bool = False  # a GPX of it can be made


def _unix_time(value: Value) -> int | None:
    time = _whole_number(value)
    return None if time is None else time + FIT_EPOCH_OFFSET


def _created(data: bytes) -> int | None:
    """The creation time in the first file_id, from as much of the file as reads."""
    try:
        for _num, values in iter_messages(data, wanted=(_MESG_FILE_ID,)):
            return _unix_time(values.get(_FIELD_TIME_CREATED))
    except FitError:
        pass
    return None


def _total(sessions: list[dict[int, Value]], number: int, scale: int = 1) -> float | int | None:
    """The sum of a field over the sessions that give it (a whole number unscaled); None when
    none does."""
    found = _added(sessions, lambda s: _whole_number(s.get(number)))
    return found if found is None or scale == 1 else found / scale


def _has_position(values: dict[int, Value], lat: int, long: int) -> bool:
    return all(_whole_number(values.get(number)) is not None for number in (lat, long))


def _a_record_has_a_position(data: bytes) -> bool:
    """Whether a record has a position; the search stops at the first that has one."""
    try:
        return any(_has_position(values, _RECORD_LAT, _RECORD_LONG)
                   for _num, values in iter_messages(data, wanted=(_MESG_RECORD,)))
    except FitError:
        return False


def summary(data: bytes) -> ActivitySummary:
    """What the activity file `data` says of itself. Of a file the device was still writing
    (not_whole: cut short) only when it was created; of a damaged one what can be read up to
    where it breaks off; of a file without a session only when it was created. Positions: a
    session's start position, else the first record that has one. FitError for what is no FIT
    file, as parse_file_id."""
    _check_header(data)
    found = not_whole(data)
    if found is not None and found[0]:
        return ActivitySummary(FileState.STILL_BEING_WRITTEN, created=_created(data))
    state = FileState.WHOLE if found is None else FileState.DAMAGED
    created = None
    sessions: list[dict[int, Value]] = []
    try:
        for num, values in iter_messages(data, wanted=(_MESG_FILE_ID, _MESG_SESSION)):
            if num == _MESG_SESSION:
                sessions.append(values)
            elif created is None:
                created = _unix_time(values.get(_FIELD_TIME_CREATED))
    except FitError:
        state = FileState.DAMAGED
    if not sessions:
        return ActivitySummary(state, created=created)
    sports = {_whole_number(s.get(_SESSION_SPORT)) for s in sessions} - {None}
    sport = None
    if len(sports) == 1:
        sport = sports.pop()
    elif sports:
        sport = SPORT_MULTISPORT
    has_positions = (any(_has_position(s, _SESSION_START_LAT, _SESSION_START_LONG)
                         for s in sessions)
                     or _a_record_has_a_position(data))
    return ActivitySummary(
        state, created=created, start=_unix_time(sessions[0].get(_SESSION_START_TIME)),
        sport=sport, timer_time=_total(sessions, _SESSION_TIMER_TIME, 1000),
        distance=_total(sessions, _SESSION_DISTANCE, 100),
        ascent=_total(sessions, _SESSION_ASCENT), has_positions=has_positions)


# FIT profile: the names of the messages `fit inspect` names (others: by number).
MESSAGE_NAMES = {
    0: "file_id", 12: "sport", 15: "goal", 18: "session", 19: "lap", 20: "record", 21: "event",
    23: "device_info", 26: "workout", 27: "workout_step", 28: "schedule", 31: "course",
    32: "course_point", 34: "activity", 49: "file_creator", 72: "training_file", 78: "hrv",
    206: "field_description", 207: "developer_data_id",
}
# FIT profile: the messages that describe developer data (field_description, developer_data_id)
DEVELOPER_MESSAGES = (206, 207)
# FIT profile: file_id's fields type, manufacturer and product, and the names of manufacturers
_FILE_ID_TYPE, _FILE_ID_MANUFACTURER, _FILE_ID_PRODUCT = 0, 1, 2
MANUFACTURER_NAMES = {1: "Garmin", 255: "development"}


@dataclass(frozen=True)
class Inspection:
    """What a FIT file holds (inspect). Its first part: the sizes its header declares, the
    type, manufacturer, product and creation time of its first file_id (never its serial
    number), and how many messages of each global number there are, as far as they read."""

    size: int
    not_whole: NotWhole | None
    header_size: int | None = None  # None: no FIT file
    data_size: int | None = None
    file_type: int | None = None
    manufacturer: int | None = None
    product: int | None = None
    created: int | None = None  # Unix time
    messages: dict[int, int] = field(default_factory=dict)
    broke_off: str | None = None  # why the messages could not be read to the end

    @property
    def whole(self) -> bool:
        return self.not_whole is None and self.broke_off is None


def inspect(data: bytes) -> Inspection:
    """What the FIT file `data` holds (Inspection); of what is no FIT file only its check."""
    found = check(data)
    try:
        header_size = _check_header(data)
    except FitError:
        return Inspection(len(data), found)
    (data_size,) = struct.unpack_from("<I", data, 4)
    counts: dict[int, int] = {}
    file_id: dict[int, Value] = {}
    broke_off = None
    try:
        for num, values in iter_messages(data):
            counts[num] = counts.get(num, 0) + 1
            if num == _MESG_FILE_ID and counts[num] == 1:
                file_id = values
    except FitError as e:
        broke_off = str(e)
    return Inspection(
        len(data), found, header_size, data_size,
        file_type=_whole_number(file_id.get(_FILE_ID_TYPE)),
        manufacturer=_whole_number(file_id.get(_FILE_ID_MANUFACTURER)),
        product=_whole_number(file_id.get(_FILE_ID_PRODUCT)),
        created=_unix_time(file_id.get(_FIELD_TIME_CREATED)), messages=counts,
        broke_off=broke_off)


@dataclass(frozen=True)
class Point:
    """A record of an activity that has a position: degrees, metres and a Unix time."""

    latitude: float
    longitude: float
    altitude: float | None = None
    time: int | None = None


def _degrees(semicircles: int) -> float:
    return semicircles * 180 / 2**31


def _metres(value: Value) -> float | None:
    raw = _whole_number(value)
    return None if raw is None else raw / 5 - 500


def points(data: bytes) -> Iterator[Point]:
    """The records of the activity file `data` that have a position, in their order: the
    enhanced altitude when it is given, else the altitude. FitError where the file breaks
    off, after the points before it."""
    for _num, values in iter_messages(data, wanted=(_MESG_RECORD,)):
        lat, long = (_whole_number(values.get(n)) for n in (_RECORD_LAT, _RECORD_LONG))
        if lat is None or long is None:
            continue
        altitude = _metres(values.get(_RECORD_ENHANCED_ALTITUDE))
        if altitude is None:
            altitude = _metres(values.get(_RECORD_ALTITUDE))
        yield Point(_degrees(lat), _degrees(long), altitude,
                    _unix_time(values.get(FIELD_TIMESTAMP)))


# FIT profile: more of a record, the lap, and more of a session.
_RECORD_HEART_RATE = 3  # beats a minute
_RECORD_DISTANCE = 5  # centimetres from the start
_RECORD_SPEED, _RECORD_ENHANCED_SPEED = 6, 73  # millimetres a second
_RECORD_TEMPERATURE = 13  # °C, whole degrees
_MESG_LAP = 19
_LAP_START_TIME = 2
_LAP_ELAPSED_TIME, _LAP_TIMER_TIME = 7, 8  # milliseconds, with and without the pauses
_LAP_DISTANCE = 9  # centimetres
_LAP_CALORIES = 11
_LAP_AVG_SPEED, _LAP_ENHANCED_AVG_SPEED = 13, 110  # millimetres a second
_LAP_AVG_HEART_RATE, _LAP_MAX_HEART_RATE = 15, 16
_LAP_ASCENT = 21  # metres
_SESSION_ELAPSED_TIME = 7  # milliseconds, pauses included
_SESSION_CALORIES = 11
_SESSION_AVG_SPEED, _SESSION_ENHANCED_AVG_SPEED = 14, 124  # millimetres a second
_SESSION_AVG_HEART_RATE, _SESSION_MAX_HEART_RATE = 16, 17
# total_training_effect, total_anaerobic_training_effect (tenths, 0.0-5.0), training_load_peak
# (scale 65536)
_SESSION_AEROBIC_EFFECT, _SESSION_ANAEROBIC_EFFECT, _SESSION_TRAINING_LOAD = 24, 137, 168
# The details of an activity's page. total_cycles (strides on foot) and total_fractional_cycles
# (scale 128); max_speed and its enhanced field (millimetres a second); avg_ and max_cadence (a
# minute) and their fractional fields (scale 128); total_descent (metres); avg_, max_ and
# min_temperature (°C); min_heart_rate; active_time (milliseconds); min_ and max_altitude and
# their enhanced fields (scale 5, offset 500); enhanced_avg_, _max_ and _min_respiration_rate
# (breaths a minute, scale 100).
_SESSION_CYCLES, _SESSION_FRACTIONAL_CYCLES = 10, 94
_SESSION_MAX_SPEED, _SESSION_ENHANCED_MAX_SPEED = 15, 125
_SESSION_AVG_CADENCE, _SESSION_MAX_CADENCE = 18, 19
_SESSION_AVG_FRACTIONAL_CADENCE, _SESSION_MAX_FRACTIONAL_CADENCE = 92, 93
_SESSION_DESCENT = 23
_SESSION_AVG_TEMPERATURE, _SESSION_MAX_TEMPERATURE, _SESSION_MIN_TEMPERATURE = 57, 58, 150
_SESSION_MIN_HEART_RATE = 64
_SESSION_ACTIVE_TIME = 78
_SESSION_MIN_ALTITUDE, _SESSION_MAX_ALTITUDE = 71, 50
_SESSION_ENHANCED_MIN_ALTITUDE, _SESSION_ENHANCED_MAX_ALTITUDE = 127, 128
_SESSION_AVG_RESPIRATION, _SESSION_MAX_RESPIRATION, _SESSION_MIN_RESPIRATION = 169, 170, 180
# FIT profile: set, a set of a strength workout: its duration (milliseconds) and set_type (rest
# 0, active 1).
_MESG_SET, _SET_DURATION, _SET_TYPE, _SET_REST = 225, 0, 5, 0
# FIT profile: time_in_zone, of the session (its reference_mesg 18): the time in each zone
# (seconds, scale 1000; an array: below zone 1, zones 1-5, above the top bound), the zones'
# bounds (hr_zone_high_boundary, an array), how they are worked out, the max and threshold
# heart rate. A device keeps as the bound of a zone the first beat of the zone above it, as
# Garmin Connect shows the zones: made-up bounds 118, 141, … are below zone 1 under 118, zone 1
# 118–140; the last is the top of zone 5, which Garmin Connect does not show.
_MESG_TIME_IN_ZONE = 216
_ZONE_REFERENCE, _ZONE_TIMES, _ZONE_BOUNDS, _ZONE_CALC = 0, 2, 6, 10
_ZONE_MAX, _ZONE_THRESHOLD = 11, 13
_ZONE_METHODS = {0: "custom", 1: "max", 2: "reserve", 3: "threshold"}  # hr_zone_calc
ZONES = 6  # below zone 1 and zones 1-5


@dataclass(frozen=True)
class Sample:
    """A record of an activity: its time (a Unix time) and what it gives; None: not given (a
    gap, never a zero)."""

    time: int
    distance: float | None = None  # metres from the start
    speed: float | None = None  # metres a second
    heart_rate: int | None = None  # beats a minute
    altitude: float | None = None  # metres
    latitude: float | None = None  # degrees
    longitude: float | None = None
    temperature: int | None = None  # °C


@dataclass(frozen=True)
class Lap:
    """A lap as the device recorded it; None: not given."""

    start: int | None = None  # a Unix time
    elapsed_time: float | None = None  # seconds, pauses included
    timer_time: float | None = None  # seconds, pauses left out
    distance: float | None = None  # metres
    avg_speed: float | None = None  # metres a second
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    ascent: int | None = None  # metres
    calories: int | None = None


@dataclass(frozen=True)
class HeartRateZones:
    """The heart rate zones of an activity as its device worked them out: the time in each
    (seconds: below zone 1, zones 1-5, the time above the top bound in zone 5), their bounds
    as the device keeps them (beats a minute, the same six: each the first beat of the zone
    above it, the last the top of zone 5), how they were worked out ("max", "reserve",
    "threshold", "custom"; None: not said) and from what."""

    times: tuple[float, ...]
    bounds: tuple[int, ...]
    method: str | None = None
    max_heart_rate: int | None = None
    threshold_heart_rate: int | None = None


@dataclass(frozen=True)
class ActivityData:
    """What an activity file holds for its page (activity_data): how the file is, what its
    sessions say together, its records and its laps. None: not said."""

    state: FileState
    start: int | None = None  # the start of its first session (a Unix time)
    sport: int | None = None  # several sports: SPORT_MULTISPORT
    elapsed_time: float | None = None  # seconds, pauses included
    timer_time: float | None = None  # seconds, pauses left out
    distance: float | None = None  # metres
    ascent: int | None = None  # metres
    avg_speed: float | None = None  # metres a second
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    calories: int | None = None
    samples: tuple[Sample, ...] = field(default=(), repr=False)  # positions and times
    laps: tuple[Lap, ...] = ()
    zones: HeartRateZones | None = None
    aerobic_effect: float | None = None  # the training effect, 0.0-5.0
    anaerobic_effect: float | None = None
    training_load: float | None = None
    descent: int | None = None  # metres
    min_altitude: float | None = None  # metres
    max_altitude: float | None = None
    max_speed: float | None = None  # metres a second
    avg_cadence: float | None = None  # a minute: strides on foot, revolutions on a bike
    max_cadence: float | None = None
    cycles: float | None = None  # strides on foot
    min_heart_rate: int | None = None
    avg_temperature: float | None = None  # °C
    min_temperature: float | None = None
    max_temperature: float | None = None
    avg_respiration: float | None = None  # breaths a minute
    min_respiration: float | None = None
    max_respiration: float | None = None
    active_time: float | None = None  # seconds
    rest_time: float | None = None  # seconds: the rest sets of a strength workout


def _scaled(value: Value, scale: int) -> float | None:
    number = _whole_number(value)
    return None if number is None else number / scale


def _enhanced(values: dict[int, Value], enhanced: int, plain: int, read) -> float | None:
    """A value as `read` gives it: of the enhanced field first, the plain one standing in."""
    found = read(values.get(enhanced))
    return read(values.get(plain)) if found is None else found


def _speed(values: dict[int, Value], enhanced: int, plain: int) -> float | None:
    """A speed in metres a second."""
    return _enhanced(values, enhanced, plain, lambda value: _scaled(value, 1000))


def _sample(values: dict[int, Value]) -> Sample | None:
    """The sample of a record; None for one without a time, which cannot be placed."""
    time = _unix_time(values.get(FIELD_TIMESTAMP))
    if time is None:
        return None
    lat, long = (_whole_number(values.get(n)) for n in (_RECORD_LAT, _RECORD_LONG))
    altitude = _metres(values.get(_RECORD_ENHANCED_ALTITUDE))
    if altitude is None:
        altitude = _metres(values.get(_RECORD_ALTITUDE))
    placed = lat is not None and long is not None
    return Sample(time, _scaled(values.get(_RECORD_DISTANCE), 100),
                  _speed(values, _RECORD_ENHANCED_SPEED, _RECORD_SPEED),
                  _whole_number(values.get(_RECORD_HEART_RATE)), altitude,
                  _degrees(lat) if placed else None, _degrees(long) if placed else None,
                  _whole_number(values.get(_RECORD_TEMPERATURE)))


def _lap(values: dict[int, Value]) -> Lap:
    return Lap(_unix_time(values.get(_LAP_START_TIME)),
               _scaled(values.get(_LAP_ELAPSED_TIME), 1000),
               _scaled(values.get(_LAP_TIMER_TIME), 1000),
               _scaled(values.get(_LAP_DISTANCE), 100),
               _speed(values, _LAP_ENHANCED_AVG_SPEED, _LAP_AVG_SPEED),
               _whole_number(values.get(_LAP_AVG_HEART_RATE)),
               _whole_number(values.get(_LAP_MAX_HEART_RATE)),
               _whole_number(values.get(_LAP_ASCENT)),
               _whole_number(values.get(_LAP_CALORIES)))


def _with_fraction(values: dict[int, Value], whole: int, fraction: int) -> float | None:
    """A field with its fractional field (scale 128) added, when it gives one."""
    number = _whole_number(values.get(whole))
    if number is None:
        return None
    part = _whole_number(values.get(fraction))
    return number + (part or 0) / 128


def _altitude(values: dict[int, Value], enhanced: int, plain: int) -> float | None:
    """An altitude in metres."""
    return _enhanced(values, enhanced, plain, _metres)


def _each(sessions: list[dict[int, Value]], read) -> list[float]:
    """The values `read` gives of the sessions, but those not given."""
    return [v for v in map(read, sessions) if v is not None]


def _lowest(sessions: list[dict[int, Value]], read) -> float | None:
    return min(_each(sessions, read), default=None)


def _greatest(sessions: list[dict[int, Value]], read) -> float | None:
    return max(_each(sessions, read), default=None)


def _added(sessions: list[dict[int, Value]], read) -> float | None:
    found = _each(sessions, read)
    return sum(found) if found else None


def _field(number: int, scale: int = 1):
    """What reads a field of a session, scaled."""
    return lambda values: _scaled(values.get(number), scale)


def _weighted(sessions: list[dict[int, Value]], read) -> float | None:
    """An average of the sessions: of one session as it gives it; of several, weighted by each
    one's timer time."""
    if len(sessions) == 1:
        return read(sessions[0])
    found = [(v, t) for v, t in ((read(s), _whole_number(s.get(_SESSION_TIMER_TIME)))
                                 for s in sessions) if v is not None and t]
    return sum(v * t for v, t in found) / sum(t for _v, t in found) if found else None


def _details_together(sessions: list[dict[int, Value]]) -> dict:
    """The details of an activity's page from its sessions: the descent, strides and active
    time added up, the lowest of the minimums and the highest of the maximums, the averages
    weighted by the timer time."""
    return {
        "descent": _total(sessions, _SESSION_DESCENT),
        "min_altitude": _lowest(sessions, lambda s: _altitude(
            s, _SESSION_ENHANCED_MIN_ALTITUDE, _SESSION_MIN_ALTITUDE)),
        "max_altitude": _greatest(sessions, lambda s: _altitude(
            s, _SESSION_ENHANCED_MAX_ALTITUDE, _SESSION_MAX_ALTITUDE)),
        "max_speed": _greatest(sessions, lambda s: _speed(s, _SESSION_ENHANCED_MAX_SPEED,
                                                          _SESSION_MAX_SPEED)),
        "avg_cadence": _weighted(sessions, lambda s: _with_fraction(
            s, _SESSION_AVG_CADENCE, _SESSION_AVG_FRACTIONAL_CADENCE)),
        "max_cadence": _greatest(sessions, lambda s: _with_fraction(
            s, _SESSION_MAX_CADENCE, _SESSION_MAX_FRACTIONAL_CADENCE)),
        "cycles": _added(sessions, lambda s: _with_fraction(s, _SESSION_CYCLES,
                                                            _SESSION_FRACTIONAL_CYCLES)),
        "min_heart_rate": _lowest(sessions, lambda s: _whole_number(
            s.get(_SESSION_MIN_HEART_RATE))),
        "avg_temperature": _weighted(sessions, _field(_SESSION_AVG_TEMPERATURE)),
        "min_temperature": _lowest(sessions, _field(_SESSION_MIN_TEMPERATURE)),
        "max_temperature": _greatest(sessions, _field(_SESSION_MAX_TEMPERATURE)),
        "avg_respiration": _weighted(sessions, _field(_SESSION_AVG_RESPIRATION, 100)),
        "min_respiration": _lowest(sessions, _field(_SESSION_MIN_RESPIRATION, 100)),
        "max_respiration": _greatest(sessions, _field(_SESSION_MAX_RESPIRATION, 100)),
        "active_time": _total(sessions, _SESSION_ACTIVE_TIME, 1000),
    }


def _temperatures(samples: list[Sample]) -> dict:
    """The average temperature of the records and their lowest and highest, as Garmin Connect
    gives them (the session keeps its average in whole degrees); none without a record that
    gives one, the session's standing."""
    found = [s.temperature for s in samples if s.temperature is not None]
    if not found:
        return {}
    return {"avg_temperature": sum(found) / len(found), "min_temperature": min(found),
            "max_temperature": max(found)}


def _rest_time(sets: list[dict[int, Value]]) -> float | None:
    """The time of the rest sets added up (seconds); None without one that gives its
    duration."""
    rests = [d for d in (_scaled(s.get(_SET_DURATION), 1000) for s in sets
                         if _whole_number(s.get(_SET_TYPE)) == _SET_REST) if d is not None]
    return sum(rests) if rests else None


def _sessions_together(sessions: list[dict[int, Value]]) -> dict:
    """What the sessions say together: the times, distance, ascent and calories added up, the
    average speed over all of them (from one session as it gives it), the average heart rate
    weighted by each one's timer time, the maximum the highest; the training effects and load
    the highest too (several sessions, a multisport activity: an assumption, no such file
    looked at); the details (_details_together)."""
    if not sessions:
        return {}
    sports = {_whole_number(s.get(_SESSION_SPORT)) for s in sessions} - {None}
    sport = sports.pop() if len(sports) == 1 else SPORT_MULTISPORT if sports else None
    timer_time = _total(sessions, _SESSION_TIMER_TIME, 1000)
    distance = _total(sessions, _SESSION_DISTANCE, 100)
    if len(sessions) == 1:
        avg_speed = _speed(sessions[0], _SESSION_ENHANCED_AVG_SPEED, _SESSION_AVG_SPEED)
    else:
        avg_speed = distance / timer_time if distance is not None and timer_time else None
    avg_heart_rate = _weighted(sessions, lambda s: _whole_number(s.get(_SESSION_AVG_HEART_RATE)))
    return {
        "start": _unix_time(sessions[0].get(_SESSION_START_TIME)),
        "sport": sport,
        "elapsed_time": _total(sessions, _SESSION_ELAPSED_TIME, 1000),
        "timer_time": timer_time,
        "distance": distance,
        "ascent": _total(sessions, _SESSION_ASCENT),
        "avg_speed": avg_speed,
        "avg_heart_rate": round(avg_heart_rate) if avg_heart_rate is not None else None,
        "max_heart_rate": _greatest(sessions, lambda s: _whole_number(
            s.get(_SESSION_MAX_HEART_RATE))),
        "calories": _total(sessions, _SESSION_CALORIES),
        "aerobic_effect": _greatest(sessions, _field(_SESSION_AEROBIC_EFFECT, 10)),
        "anaerobic_effect": _greatest(sessions, _field(_SESSION_ANAEROBIC_EFFECT, 10)),
        "training_load": _greatest(sessions, _field(_SESSION_TRAINING_LOAD, 65536)),
        **_details_together(sessions),
    }


def _zones(found: list[dict[int, Value]]) -> HeartRateZones | None:
    """The zones of the time_in_zone messages of the sessions: their times added up (several
    sessions), the bounds and the rest of the first; None without six bounds and times."""
    whole = []
    for values in found:
        times, bounds = values.get(_ZONE_TIMES), values.get(_ZONE_BOUNDS)
        if not isinstance(times, tuple) or not isinstance(bounds, tuple):
            continue
        if len(times) < ZONES or len(bounds) < ZONES or None in bounds[:ZONES] \
                or all(t is None for t in times):
            continue
        seconds = [(t or 0) / 1000 for t in times]
        whole.append((tuple(seconds[:ZONES - 1]) + (sum(seconds[ZONES - 1:]),),
                      tuple(bounds[:ZONES]), values))
    if not whole:
        return None
    times = tuple(sum(z[0][i] for z in whole) for i in range(ZONES))
    first = whole[0][2]
    method = _ZONE_METHODS.get(_whole_number(first.get(_ZONE_CALC)))
    return HeartRateZones(times, whole[0][1], method, _whole_number(first.get(_ZONE_MAX)),
                          _whole_number(first.get(_ZONE_THRESHOLD)))


def activity_data(data: bytes) -> ActivityData:
    """What the activity file `data` holds for its page: its records with a time, its laps,
    what its sessions say together (its temperature that of the records), its heart rate
    zones and the time of its rest sets. Of a file cut short (the device was still writing it)
    or damaged, what reads up to where it breaks off, with its state. FitError for what is no
    FIT file, as summary."""
    _check_header(data)
    found = not_whole(data)
    state = (FileState.WHOLE if found is None else FileState.STILL_BEING_WRITTEN if found[0]
             else FileState.DAMAGED)
    samples: list[Sample] = []
    laps: list[Lap] = []
    sessions: list[dict[int, Value]] = []
    zones: list[dict[int, Value]] = []
    sets: list[dict[int, Value]] = []
    try:
        for num, values in iter_messages(data, wanted=(_MESG_RECORD, _MESG_LAP, _MESG_SESSION,
                                                       _MESG_TIME_IN_ZONE, _MESG_SET),
                                         arrays=(_MESG_TIME_IN_ZONE,)):
            if num == _MESG_RECORD:
                sample = _sample(values)
                if sample is not None:
                    samples.append(sample)
            elif num == _MESG_LAP:
                laps.append(_lap(values))
            elif num == _MESG_SESSION:
                sessions.append(values)
            elif num == _MESG_SET:
                sets.append(values)
            elif values.get(_ZONE_REFERENCE) == _MESG_SESSION:
                zones.append(values)
    except FitError:
        if state is FileState.WHOLE:
            state = FileState.DAMAGED
    together = {**_sessions_together(sessions), **_temperatures(samples)}
    return ActivityData(state, samples=tuple(samples), laps=tuple(laps), zones=_zones(zones),
                        rest_time=_rest_time(sets), **together)


# FIT profile: workout (sport, num_valid_steps, wkt_name), course (sport, name) and schedule
# (scheduled_time, a local_date_time), the messages a file of Garmin Connect's queue may hold;
# location, a saved location (a file of type 8, which holds them all).
_MESG_WORKOUT, _WORKOUT_SPORT, _WORKOUT_STEPS, _WORKOUT_NAME = 26, 4, 6, 8
_MESG_COURSE, _COURSE_SPORT, _COURSE_NAME = 31, 4, 5
_MESG_SCHEDULE, _SCHEDULE_TIME = 28, 6
MESG_LOCATION = 29


@dataclass(frozen=True)
class Described:
    """What a file says of itself (describe): its type, when it was made (a Unix time), who
    made it (its manufacturer and product numbers), and for a workout or a course its sport and
    name, a workout's steps, a course's laps (their distance and ascent), a schedule's days (the
    local clock, as seconds since 1970)."""

    file_type: int | None = None
    created: int | None = None
    manufacturer: int | None = None
    product: int | None = None
    sport: int | None = None
    name: str | None = None
    steps: int | None = None
    days: tuple[int, ...] = ()
    laps: tuple[Lap, ...] = ()

    @property
    def distance(self) -> float | None:
        """The distance of its laps together, in metres; None when none gives one."""
        found = [lap.distance for lap in self.laps if lap.distance is not None]
        return sum(found) if found else None

    @property
    def ascent(self) -> int | None:
        """The ascent of its laps together, in metres; None when none gives one (a course
        made of a route without elevations)."""
        found = [lap.ascent for lap in self.laps if lap.ascent is not None]
        return sum(found) if found else None


def describe(data: bytes) -> Described:
    """What the FIT file `data` says of itself, as far as it reads: the start of a course's
    file is enough when its course and lap come before its points, as in the files the devices
    and Garmin Connect make. FitError for what is no FIT file."""
    _check_header(data)
    file_id: dict[int, Value] = {}
    sport = name = steps = None
    days: list[int] = []
    laps: list[Lap] = []
    try:
        for num, values in iter_messages(
                data, wanted=(_MESG_FILE_ID, _MESG_WORKOUT, _MESG_COURSE, _MESG_SCHEDULE,
                              _MESG_LAP)):
            if num == _MESG_FILE_ID:
                file_id = file_id or values
            elif num == _MESG_WORKOUT:
                sport, steps = values.get(_WORKOUT_SPORT), values.get(_WORKOUT_STEPS)
                name = values.get(_WORKOUT_NAME)
            elif num == _MESG_COURSE:
                sport, name = values.get(_COURSE_SPORT), values.get(_COURSE_NAME)
            elif num == _MESG_LAP:
                laps.append(_lap(values))
            else:
                day = _whole_number(values.get(_SCHEDULE_TIME))
                if day is not None:
                    days.append(day + FIT_EPOCH_OFFSET)
    except FitError:
        pass
    return Described(
        file_type=_whole_number(file_id.get(_FILE_ID_TYPE)),
        created=_unix_time(file_id.get(_FIELD_TIME_CREATED)),
        manufacturer=_whole_number(file_id.get(_FILE_ID_MANUFACTURER)),
        product=_whole_number(file_id.get(_FILE_ID_PRODUCT)), sport=_whole_number(sport),
        name=name if isinstance(name, str) else None, steps=_whole_number(steps),
        days=tuple(sorted(days)), laps=tuple(laps))
