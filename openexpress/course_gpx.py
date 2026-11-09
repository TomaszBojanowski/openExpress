"""A GPX file to copy to a device, which makes courses of its tracks and routes and saved
locations of its waypoints: the checks made before the file is read whole or written, what it
holds (for the question before the copy), the course's name and its writing into the copy, and
the file's name on the device. Nothing here touches a device.

The file is read by expat itself, with no document type and no entity allowed (so nothing is
expanded and no other file is read), and the places of the course's elements are kept, so that a
new name changes that one element of the copy and no other byte.
"""

from __future__ import annotations

import codecs
import itertools
import math
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from xml.parsers import expat
from xml.sax.saxutils import escape

from openexpress import applog
from openexpress.errors import UserFacingError
from openexpress.i18n import _, human_size

MAX_SIZE = 25 * 1024 * 1024  # read whole into memory to be checked: a course is far smaller
MAX_NAME = 64  # the file's name on the device, with its extension
NAMESPACES = frozenset({"http://www.topografix.com/GPX/1/1", "http://www.topografix.com/GPX/1/0"})
EARTH_RADIUS_M = 6_371_000
# Names Windows (and some file systems of devices) keep for devices, whatever the extension.
_DOS_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                        *(f"LPT{i}" for i in range(1, 10))})
# Letters that decomposing does not reduce to plain Latin ones.
_LETTERS = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss",
                          "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "Th",
                          "ð": "d", "Ð": "D", "ı": "i"})


class CourseFileError(UserFacingError):
    """A file that is not copied to the device as a course: the words say why."""


@dataclass(frozen=True)
class Line:
    """A track or a route of the file: what the device makes a course of."""
    kind: str  # "track" or "route"
    name: str  # its <name>, stripped; "" when it has none
    points: int
    distance_m: float  # along its points, segment by segment
    ascent_m: float | None  # the climbs between its points; None when no point has an elevation
    # Its points' (latitude, longitude), segment by segment (a route: one), for its map.
    segments: tuple[tuple[tuple[float, float], ...], ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class GpxFile:
    """A GPX file checked: its tracks and routes with at least two points each (`lines`), the
    number of its waypoints, and what writing a course's name into a copy of it needs."""
    path: Path
    size: int
    lines: tuple[Line, ...]
    waypoints: int
    encoding: str
    data: bytes = field(repr=False)
    places: tuple[tuple[float, float], ...] = field(default=(), repr=False)  # the waypoints
    # For the one course of a file that makes one: where its element starts and where its
    # <name> starts and ends (None: it has none). See with_course_name.
    name_at: tuple[int, int | None, int | None] | None = field(default=None, repr=False)

    @property
    def courses(self) -> int:
        """How many courses the device makes of it: one per track or route."""
        return len(self.lines)


class _NotGpx(Exception):
    pass


class _DocumentType(Exception):
    pass


class _OffTheEarth(Exception):
    pass


def distance_m(a, b) -> float:
    """The distance between two (latitude, longitude) on the Earth (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _position(attrs: dict) -> tuple[float, float]:
    try:
        lat, lon = float(attrs["lat"]), float(attrs["lon"])
    except (KeyError, ValueError):
        raise _OffTheEarth from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise _OffTheEarth
    return lat, lon


class _Reader:
    """One pass of expat over the file: the lines with their points, the waypoints, and the
    places of each line's element and of its <name>."""

    def __init__(self) -> None:
        self.encoding = "utf-8"
        self.stack: list[tuple[str, str]] = []
        self.ns: str | None = None
        self.lines: list[dict] = []
        self.line: dict | None = None
        self.waypoints: list[tuple[float, float]] = []
        self.text: list[str] | None = None  # the text of a <name> or <ele> being read
        self.parser = p = expat.ParserCreate(namespace_separator=" ")
        p.XmlDeclHandler = self.declaration
        p.StartDoctypeDeclHandler = self.document_type
        p.EntityDeclHandler = self.document_type
        p.StartElementHandler = self.start
        p.EndElementHandler = self.end
        p.CharacterDataHandler = self.characters

    def declaration(self, _version, encoding, _standalone) -> None:
        self.encoding = encoding or "utf-8"

    def document_type(self, *_args) -> None:
        raise _DocumentType

    def characters(self, data: str) -> None:
        if self.text is not None:
            self.text.append(data)

    def start(self, name: str, attrs: dict) -> None:
        uri, _space, local = name.rpartition(" ")
        depth = len(self.stack)
        self.stack.append((uri, local))
        if depth == 0:
            if uri not in NAMESPACES or local != "gpx":
                raise _NotGpx
            self.ns = uri
            return
        if uri != self.ns:
            return  # extensions and the like
        line = self.line
        if depth == 1:
            if local in ("trk", "rte"):
                self.line = {"kind": "track" if local == "trk" else "route", "name": "",
                             "start": self.parser.CurrentByteIndex, "name_start": None,
                             "name_end": None, "segments": [] if local == "trk" else [[]]}
            elif local == "wpt":
                self.waypoints.append(_position(attrs))
        elif line is not None:
            parent = self.stack[-2][1]
            if depth == 2 and local == "name" and line["name_start"] is None:
                line["name_start"] = self.parser.CurrentByteIndex
                self.text = []
            elif depth == 2 and local == "trkseg" and line["kind"] == "track":
                line["segments"].append([])
            elif (local == "trkpt" and parent == "trkseg") or (local == "rtept" and depth == 2):
                line["segments"][-1].append([*_position(attrs), None])
            elif local == "ele" and parent in ("trkpt", "rtept") and line["segments"]:
                self.text = []

    def end(self, name: str) -> None:
        uri, _space, local = name.rpartition(" ")
        self.stack.pop()
        line = self.line
        if line is None or uri != self.ns:
            return
        depth = len(self.stack)
        if depth == 2 and local == "name" and self.text is not None:
            line["name"] = "".join(self.text).strip()
            line["name_end"] = self.parser.CurrentByteIndex
            self.text = None
        elif local == "ele" and self.text is not None:
            try:
                ele = float("".join(self.text))
            except ValueError:
                ele = None
            segment = line["segments"][-1]
            if segment and ele is not None and math.isfinite(ele):
                segment[-1][2] = ele
            self.text = None
        elif depth == 1 and local in ("trk", "rte"):
            self.lines.append(line)
            self.line = None


def _line(found: dict) -> Line:
    distance, ascent, points, any_ele = 0.0, 0.0, 0, False
    for segment in found["segments"]:
        points += len(segment)
        for a, b in itertools.pairwise(segment):
            distance += distance_m(a[:2], b[:2])
        eles = [p[2] for p in segment if p[2] is not None]
        any_ele = any_ele or bool(eles)
        ascent += sum(max(0.0, b - a) for a, b in itertools.pairwise(eles))
    segments = tuple(tuple((p[0], p[1]) for p in segment) for segment in found["segments"]
                     if segment)
    return Line(found["kind"], found["name"], points, distance, ascent if any_ele else None,
                segments)


def read(path: Path) -> GpxFile:
    """The GPX file at `path`, checked: CourseFileError says why one is not copied (not found,
    too large, no GPX, a document type, nothing to navigate, a point off the Earth)."""
    path = Path(path)
    applog.hide(path.name, "file")  # shown to the user in an error; never in the log
    try:
        info = path.stat()
    except OSError:
        info = None
    if info is None or not stat.S_ISREG(info.st_mode):
        raise CourseFileError(_("Not found: {path}").format(path=path))
    if info.st_size > MAX_SIZE:
        raise _too_large(path, info.st_size)
    return parse(path.read_bytes(), path)


def _too_large(path: Path, size: int) -> CourseFileError:
    # TRANSLATORS: a GPX file chosen to be copied to the device as a course is too large;
    # {size} and {limit}: sizes such as "31.2 MB"
    return CourseFileError(_("{file} is too large for a course: {size}, at most {limit}.")
                           .format(file=path.name, size=human_size(size),
                                   limit=human_size(MAX_SIZE)))


def parse(data: bytes, path: Path) -> GpxFile:
    """The GPX file of these bytes, checked as read checks a file (`path`: where it is, named
    in what is said: a file on the device, or the copy of a file with a new course name)."""
    path = Path(path)
    if len(data) > MAX_SIZE:
        raise _too_large(path, len(data))
    reader = _Reader()
    try:
        reader.parser.Parse(data, True)
    except _DocumentType:
        # TRANSLATORS: a GPX file declares a document type (<!DOCTYPE …>) or entities, which
        # could make a parser expand them or read other files: it is not read at all
        raise CourseFileError(_("{file} is not read: it declares a document type, which a GPX "
                                "file has no need of.").format(file=path.name)) from None
    except _OffTheEarth:
        # TRANSLATORS: a point of a GPX file has a latitude or longitude out of range, or none
        raise CourseFileError(_("{file} has a point with an impossible position.").format(
            file=path.name)) from None
    except (_NotGpx, expat.ExpatError):
        # TRANSLATORS: a file chosen to be copied to the device as a course is not a GPX file
        raise CourseFileError(_("{file} is not a GPX file.").format(file=path.name)) from None
    lines = [found for found in reader.lines if sum(map(len, found["segments"])) >= 2]
    if not lines and not reader.waypoints:
        # TRANSLATORS: a GPX file has nothing the device could navigate by
        raise CourseFileError(_("{file} has no track, route or waypoint.").format(
            file=path.name))
    name_at = ((lines[0]["start"], lines[0]["name_start"], lines[0]["name_end"])
               if len(lines) == 1 else None)
    return GpxFile(path, len(data), tuple(_line(found) for found in lines), len(reader.waypoints),
                   reader.encoding, data, name_at=name_at, places=tuple(reader.waypoints))


def course_name(gpx: GpxFile, entered: str | None = None) -> str | None:
    """The name of the file's one course: what was entered, else the <name> of its track or
    route, else the file's name without its extension. None when the file makes no course or
    several (each keeps the name it has)."""
    if gpx.courses != 1:
        return None
    if entered is not None and entered.strip():
        return entered.strip()
    return gpx.lines[0].name or gpx.path.stem.strip() or gpx.path.name


def _tag_end(data: bytes, start: int) -> int:
    """The place of the ">" that ends the tag starting at `start` (one in quotes does not)."""
    quote = None
    for i in range(start, len(data)):
        c = data[i:i + 1]
        if quote:
            if c == quote:
                quote = None
        elif c in (b'"', b"'"):
            quote = c
        elif c == b">":
            return i
    raise ValueError("a tag without its end")


def _tag_name(data: bytes, start: int) -> bytes:
    """The name of the tag at `start` as written, with its prefix ("g:trk")."""
    return re.match(rb"<\s*([^\s/>]+)", data[start:start + 256]).group(1)


def with_course_name(gpx: GpxFile, name: str) -> bytes:
    """The bytes of a copy of the file whose one course is named `name`: the text of its
    track's or route's <name> replaced (an element added first in it when it has none), every
    other byte as it was. ValueError when the file makes no course or several, or the name is
    empty; CourseFileError when the file's text encoding cannot carry the change."""
    name = name.strip()
    if gpx.name_at is None or not name:
        raise ValueError("one course and a name are needed")
    if codecs.lookup(gpx.encoding).name.startswith(("utf-16", "utf-32")):
        # TRANSLATORS: the course's name cannot be written into this GPX file (its text is
        # written in UTF-16 or UTF-32); the file can be copied with the name it has
        raise CourseFileError(_("The name of the course cannot be changed in {file}; copy it "
                                "with the name it has.").format(file=gpx.path.name))
    text = escape(name).encode(gpx.encoding, "xmlcharrefreplace")
    data, (line_start, name_start, name_end) = gpx.data, gpx.name_at
    if name_start is None:
        prefix = _tag_name(data, line_start).rpartition(b":")[0]
        qname = prefix + b":name" if prefix else b"name"
        at = _tag_end(data, line_start) + 1
        return data[:at] + b"<" + qname + b">" + text + b"</" + qname + b">" + data[at:]
    start_end = _tag_end(data, name_start)
    if data[start_end - 1:start_end] == b"/":  # <name/>: an element with no text
        qname = _tag_name(data, name_start)
        opening = data[name_start:start_end - 1].rstrip() + b">"
        return (data[:name_start] + opening + text + b"</" + qname + b">"
                + data[start_end + 1:])
    return data[:start_end + 1] + text + data[name_end:]


def device_name(course: str, taken=(), extension: str = ".gpx") -> str:
    """The file's name on the device, made from the course's name: plain Latin letters (accents
    dropped), `_` for spaces, only letters, digits and `. _ - ( )`, no name of a DOS device, at
    most MAX_NAME characters with the extension; a name in `taken` (casefolded) gets "_2",
    "_3"… The extension is written as the device declares it."""
    ext = extension if extension.startswith(".") else "." + extension
    text = unicodedata.normalize("NFKD", course.translate(_LETTERS))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^A-Za-z0-9._()\-]", "", text)
    stem = re.sub(r"_+", "_", text).strip("._-") or "course"
    taken = {t.casefold() for t in taken}

    def made(suffix: str) -> str:
        cut = stem[:MAX_NAME - len(ext) - len(suffix)].rstrip("._-") or "course"
        if cut.upper() in _DOS_NAMES:
            cut += "_"
        return cut + suffix + ext

    candidate, n = made(""), 2
    while candidate.casefold() in taken:
        candidate, n = made(f"_{n}"), n + 1
    return candidate
