"""Courses on the device, and GPX files copied to it for it to make courses of: what the device
holds (its courses with where each came from, the GPX files waiting for it, the number of its
saved locations), the plan and the copy of a file, the removal of a course or of a waiting
file, and the record of what openExpress copied (devices/<unit>/courses.json).

A device that declares GPX files inward to Garmin/NewFiles takes a file there: at the unplug it
makes a FIT course in its courses folder of each track and route, and saved locations of the
waypoints, and the file is gone. course_gpx checks a file before it is copied. Where a course
came from is read from its FIT file and the record: Garmin Connect's courses carry the product
65534 in file_id, the courses the device made of a file the product 20119 and the time it made
them; such a course whose name a copy of either install of this computer recorded before is
openExpress's. Nothing else is guessed.

A copy or a removal takes the sync's lock (service.sync_lock), which holds the other install's
too: a sync and a copy never write to the device at once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openexpress import applog, course_gpx, fit, paths, service
from openexpress.course_gpx import CourseFileError, GpxFile
from openexpress.device.storage import DeviceError, DeviceNotResponding, Storage
from openexpress.device.xmldevice import DeviceFile, GarminDevice
from openexpress.errors import UserFacingError
from openexpress.i18n import _, ngettext
from openexpress.maps import SPACE_MARGIN
from openexpress.progress import copying_text, say_to

log = logging.getLogger(__name__)

GPX_DATATYPE = "GPSData"
COURSE_DATATYPE = "FIT_TYPE_6"
LOCATION_DATATYPE = "FIT_TYPE_8"
# The one folder a GPX file is written to, compared without case as the device's storage does:
# where the devices tried take it. A device that declares another is not offered the courses.
GPX_FOLDER = "garmin/newfiles"
STATE_NAME = "courses.json"
ORIGIN_OPENEXPRESS = "openExpress"
ORIGIN_CONNECT = "Garmin Connect"
GARMIN = 1  # file_id's manufacturer
CONNECT_PRODUCT = 65534  # file_id's product of a file Garmin Connect made
# file_id's product of a course the device made of a file (the FIT profile's "training center")
DEVICE_PRODUCT = 20119
# What is read of a course's file first: its course and lap come before its points.
START = 16 * 1024
# How far apart the device's clock and the computer's may be when a course the device made is
# told to be made of a copy (after it).
CLOCKS_APART_S = 600


class CourseError(UserFacingError):
    """A copy or a removal that is not made: the words say why."""


# --- where the files are ------------------------------------------------------------------------


def gpx_target(device: GarminDevice) -> DeviceFile | None:
    """Where the device takes a GPX file to make courses of it: its GPSData declared inward to
    Garmin/NewFiles. None for a device that declares none, or another folder (the log says
    which): it is not offered the courses."""
    targets = device.targets(GPX_DATATYPE)
    found = next((t for t in targets if t.path.strip("/").casefold() == GPX_FOLDER), None)
    if found is None and targets:
        log.info("courses: GPX files go to %s on this device, not Garmin/NewFiles: not offered",
                 targets[0].path.strip("/") or "/")
    return found


def offered(device: GarminDevice) -> bool:
    return gpx_target(device) is not None


def _extension(target: DeviceFile) -> str:
    """The extension of a GPX file as the device declares it (".GPX" or ".gpx")."""
    return "." + (target.extension.lstrip(".") or "gpx")


def _out_folder(device: GarminDevice, datatype: str) -> str | None:
    """The folder the device keeps the files of `datatype` in (its courses, its locations)."""
    return next((s.path.strip("/") for s in device.sources(datatype) if s.path.strip("/")), None)


# --- what openExpress copied ---------------------------------------------------------------------


def _state_file(unit_id: int) -> Path:
    # The folder is named after the unit ID: this path is never printed or logged.
    return paths.device_dir(unit_id) / STATE_NAME


def _other_state_file(unit_id: int) -> Path | None:
    base = paths.other_install_dir()
    return base / "devices" / str(unit_id) / STATE_NAME if base is not None else None


def _read_copies(path: Path | None) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path is not None else None
    except (OSError, ValueError):
        return []
    copies = data.get("copies") if isinstance(data, dict) else None
    return [c for c in copies if isinstance(c, dict)] if isinstance(copies, list) else []


def recorded(unit_id: int) -> list[dict]:
    """The files this install copied to the device: each one's name on the device ("file"),
    the names of the courses it makes ("courses"), its size and MD5, and when it was copied
    ("copied", a Unix time). No content of the file."""
    return _read_copies(_state_file(unit_id))


def _copies(unit_id: int) -> list[dict]:
    """recorded, with the other install's of this computer (read, never written)."""
    return recorded(unit_id) + _read_copies(_other_state_file(unit_id))


def _names(copy: dict) -> list[str]:
    names = copy.get("courses")
    return [n for n in names if isinstance(n, str) and n.strip()] if isinstance(names, list) else []


def _copied_at(copy: dict) -> float | None:
    at = copy.get("copied")
    return float(at) if isinstance(at, int | float) and not isinstance(at, bool) else None


def _save(unit_id: int, copies: list[dict]) -> None:
    """The record written whole or not at all, readable by its owner only."""
    paths.write_private(_state_file(unit_id),
                        json.dumps({"copies": copies}, indent=1, ensure_ascii=False))


def _keep(unit_id: int, copies: list[dict]) -> None:
    try:
        _save(unit_id, copies)
    except OSError as e:
        # What is on the device is right; only the note about it is missing or stale.
        log.warning("courses: the record of copied files could not be saved (%s)",
                    type(e).__name__)


def _same_course(made: str, copied: str) -> bool:
    """Whether a course the device named `made` is the one a copy named `copied`: the same
    name, or its start (a device cuts a long name)."""
    made, copied = made.strip().casefold(), copied.strip().casefold()
    return bool(made) and (made == copied or copied.startswith(made))


# --- what the device holds -----------------------------------------------------------------------


@dataclass(frozen=True)
class Course:
    """A FIT file of the device's courses folder, with what it says of itself (None: not said,
    or the file could not be read)."""
    filename: str
    path: str  # on the device, as it spells it
    size: int
    name: str | None  # the course's
    distance_m: float | None
    ascent_m: int | None
    made: int | None  # when its file was made (a Unix time)
    origin: str | None  # ORIGIN_OPENEXPRESS, ORIGIN_CONNECT, or None: not known

    @property
    def title(self) -> str:
        return self.name or self.filename


@dataclass(frozen=True)
class Waiting:
    """A GPX file in the folder the device takes them in, not yet made a course of."""
    filename: str
    path: str  # on the device, as it spells it
    size: int
    gpx: GpxFile | None = field(repr=False)  # None: too large or not a GPX file to read

    @property
    def course(self) -> str | None:
        """The name of the one course it makes; None when it makes none or several."""
        return course_gpx.course_name(self.gpx) if self.gpx is not None else None

    @property
    def title(self) -> str:
        return self.course or self.filename


@dataclass(frozen=True)
class Overview:
    """What the Courses page shows of the device."""
    folder: str | None  # where GPX files go; None: the device is not offered the courses
    courses: list[Course]
    waiting: list[Waiting]
    locations: int | None  # how many saved locations; None: not known
    unreadable: tuple[str, ...] = ()  # "courses", "waiting", "locations": could not be read


def _origin(described: fit.Described, copies: list[dict]) -> str | None:
    if described.manufacturer == GARMIN and described.product == CONNECT_PRODUCT:
        return ORIGIN_CONNECT
    if (described.manufacturer == GARMIN and described.product == DEVICE_PRODUCT
            and described.name and described.created is not None):
        for copy in copies:
            at = _copied_at(copy)
            if (at is not None and at - CLOCKS_APART_S <= described.created
                    and any(_same_course(described.name, n) for n in _names(copy))):
                return ORIGIN_OPENEXPRESS
    return None


def _course(storage: Storage, path: str, size: int, copies: list[dict]) -> Course:
    """A course's file read (its start, or whole when the start does not give its course and
    lap); one that does not read is still listed, with nothing said of it."""
    described = fit.Described()
    try:
        data = storage.read_start(path, START)
        described = fit.describe(data)
        if (described.name is None or not described.laps) and size > len(data):
            described = fit.describe(storage.read(path))
    except DeviceNotResponding:
        raise
    except (DeviceError, fit.FitError) as e:
        log.info("courses: a course's file could not be read (%s)", type(e).__name__)
    name = described.name.strip() if described.name else None
    return Course(path.rpartition("/")[2], path, size, name or None, described.distance,
                  described.ascent, described.created, _origin(described, copies))


def _files(storage: Storage, folder: str, extension: str) -> list[tuple[str, int]]:
    """(path, size) of each file directly in `folder` with this extension (no subfolder),
    by name; none when the folder is not there."""
    real = storage.resolve(folder)
    if real is None:
        return []
    found = [(f"{real}/{e.name}", e.size) for e in storage.listdir(real)
             if not e.is_dir and e.name.casefold().endswith(extension.casefold())]
    return sorted(found, key=lambda f: f[0].casefold())


def _waiting(storage: Storage, path: str, size: int) -> Waiting:
    gpx = None
    if size <= course_gpx.MAX_SIZE:
        name = path.rpartition("/")[2]
        try:
            gpx = course_gpx.parse(storage.read(path), Path(name))
        except DeviceNotResponding:
            raise
        except (DeviceError, CourseFileError) as e:
            log.info("courses: a waiting file could not be read (%s)", type(e).__name__)
    return Waiting(path.rpartition("/")[2], path, size, gpx)


def _locations(device: GarminDevice, storage: Storage) -> int | None:
    """How many saved locations the device keeps: the location messages of its locations
    file; None for a device that declares none. FitError when a file of them does not read
    whole."""
    folder = _out_folder(device, LOCATION_DATATYPE)
    if folder is None:
        return None
    declared = device.sources(LOCATION_DATATYPE)
    count = 0
    for path, _size in _files(storage, folder, ".fit"):
        if not any(d.matches(path) for d in declared):
            continue
        found = fit.inspect(storage.read(path))
        if not found.whole:
            raise fit.FitError("a locations file does not read whole")  # the log: its class
        count += found.messages.get(fit.MESG_LOCATION, 0)
    return count


def overview(device: GarminDevice, storage: Storage) -> Overview:
    """The device's courses, the GPX files waiting for it and its saved locations, read from
    it; nothing is written. A part that cannot be read is said (`unreadable`), not raised, so
    that the rest still shows."""
    target = gpx_target(device)
    if target is None:
        return Overview(None, [], [], None)
    copies = _copies(device.unit_id)
    unreadable: list[str] = []
    folder = _out_folder(device, COURSE_DATATYPE)
    parts = (
        ("courses", lambda: [_course(storage, p, s, copies)
                             for p, s in _files(storage, folder, ".fit")] if folder else []),
        ("waiting", lambda: [_waiting(storage, p, s) for p, s in
                             _files(storage, target.path.strip("/"), _extension(target))]),
        ("locations", lambda: _locations(device, storage)),
    )
    read = {}
    for part, how in parts:
        try:
            read[part] = how()
        except DeviceNotResponding:
            raise
        except (DeviceError, fit.FitError) as e:
            log.info("courses: the %s could not be read (%s)", part, type(e).__name__)
            unreadable.append(part)
    courses, waiting, locations = (read.get("courses", []), read.get("waiting", []),
                                   read.get("locations"))
    log.info("courses: %d on the device (%d of openExpress, %d of Garmin Connect), %d files "
             "waiting, saved locations: %s", len(courses),
             sum(c.origin == ORIGIN_OPENEXPRESS for c in courses),
             sum(c.origin == ORIGIN_CONNECT for c in courses), len(waiting),
             "?" if locations is None else locations)  # counts only: the names are the user's
    return Overview(target.path.strip("/"), courses, waiting, locations, tuple(unreadable))


# --- copying a GPX file --------------------------------------------------------------------------


@dataclass(frozen=True)
class AddPlan:
    gpx: GpxFile  # the file as it was chosen
    course: str | None  # the name of its one course; None: it makes none or several
    data: bytes = field(repr=False)  # what is written: the file, with the course's name
    name: str  # the file's name on the device
    path: str  # folder and name on the device
    free: int | None  # on the device, before
    same_waiting: str | None = None  # a waiting file of the same bytes, by its name
    taken: tuple[str, ...] = ()  # the names in the folder, as the plan read it
    extension: str = ".gpx"  # of a GPX file, as the device declares it

    @property
    def size(self) -> int:
        return len(self.data)

    def file_for(self, name: str | None) -> tuple[str, int]:
        """The file's name on the device and its size, were the course named `name` (as the
        question's entry gives it; empty: the file's own): worked out as plan_add does,
        without the device. A name the file cannot carry leaves both as planned."""
        course = course_gpx.course_name(self.gpx, name)
        try:
            data = _data_for(self.gpx, course)
        except CourseFileError:
            return self.name, self.size
        return _file_name(self.gpx, course, self.taken, self.extension), len(data)

    @property
    def md5(self) -> str:
        return _md5(self.data)

    @property
    def courses(self) -> list[str]:
        """The names of the courses the device makes of it, as far as the file names them."""
        if self.course is not None:
            return [self.course]
        return [line.name for line in self.gpx.lines if line.name]


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _no_name_text(gpx: GpxFile) -> str:
    if gpx.courses == 0:
        # TRANSLATORS: `courses add FILE --name NAME` for a GPX file of waypoints only
        return _("{file} has only waypoints: no course is made of it, so it takes no course "
                 "name.").format(file=gpx.path.name)
    # TRANSLATORS: `courses add FILE --name NAME` for a GPX file of several tracks and routes,
    # each of which the device makes a course of, with the name it has
    return ngettext("{file} makes {count} course; only a file with one track or route takes "
                    "a course name.",
                    "{file} makes {count} courses; only a file with one track or route takes "
                    "a course name.", gpx.courses).format(file=gpx.path.name, count=gpx.courses)


def _data_for(gpx: GpxFile, course: str | None) -> bytes:
    """The bytes copied for a file whose one course is named `course` (its own name: the file
    as it is)."""
    if course is None or course == gpx.lines[0].name:
        return gpx.data
    return course_gpx.with_course_name(gpx, course)


def _file_name(gpx: GpxFile, course: str | None, taken, extension: str) -> str:
    return course_gpx.device_name(course or gpx.path.stem, taken, extension)


def _taken_text(name: str) -> str:
    return _("A file named {name} is already on the device; nothing is overwritten.").format(
        name=name)


def what_the_device_does(gpx: GpxFile) -> list[tuple[str, bool]]:
    """What the device makes of a GPX file, said of Garmin's devices in general, each with
    whether it is a warning (a route's points are joined with straight lines)."""
    said = []
    if gpx.courses:
        said.append((ngettext("The device makes the course when you unplug the cable.",
                              "The device makes {count} courses when you unplug the cable.",
                              gpx.courses).format(count=gpx.courses), False))
    if any(line.kind == "route" for line in gpx.lines):
        said.append((_("Garmin devices join the points of a route with straight lines, not "
                       "roads. For a course along roads, export a track instead of a route."),
                     True))
    if gpx.waypoints:
        said.append((_("Garmin devices save waypoints as saved locations."), False))
    return said


def not_offered_text() -> str:
    # TRANSLATORS: a device that does not take GPX files in Garmin/NewFiles
    return _("This device does not make courses of GPX files.")


def check_local(local: Path, name: str | None = None) -> GpxFile:
    """What is checked of a GPX file without the device: the file itself (course_gpx.read), and
    that a course name (`name`) is given only to a file of one track or route."""
    gpx = course_gpx.read(local)
    applog.hide(name, "course")
    if name is not None and name.strip() and gpx.courses != 1:
        raise CourseError(_no_name_text(gpx))
    return gpx


def plan_add(device: GarminDevice, storage: Storage, local: Path,
             name: str | None = None) -> AddPlan:
    """Everything checked before a GPX file is copied; nothing is written. `name`: the course's
    name, written into the copy in place of its track's or route's (only for a file of one).
    CourseFileError or CourseError says why a file is not copied."""
    target = gpx_target(device)
    if target is None:
        raise CourseError(not_offered_text())
    gpx = check_local(local, name)
    course = course_gpx.course_name(gpx, name)
    for named in (course, *(line.name for line in gpx.lines)):
        applog.hide(named, "course")
    data = _data_for(gpx, course)
    if data is not gpx.data:
        course_gpx.parse(data, gpx.path)  # the copy checked as any GPX file
    folder = storage.resolve(target.path.strip("/"))
    if folder is None:
        # TRANSLATORS: {folder}: a folder the device declares, such as Garmin/NewFiles
        raise CourseError(_("The device has no folder {folder}.").format(
            folder=target.path.strip("/")))
    listed = storage.listdir(folder)
    entries = [e for e in listed if not e.is_dir]
    extension = _extension(target)
    taken = tuple(e.name for e in listed)  # a folder's name too
    filename = _file_name(gpx, course, taken, extension)
    applog.hide(filename, "file")
    md5 = _md5(data)
    same = next((e.name for e in entries
                 if e.size == len(data) and e.name.casefold().endswith(extension.casefold())
                 and _md5(storage.read(f"{folder}/{e.name}")) == md5), None)
    free = storage.require_space(len(data), SPACE_MARGIN, error=CourseError)
    return AddPlan(gpx, course, data, filename, f"{folder}/{filename}", free, same, taken,
                   extension)


def add(plan: AddPlan, device: GarminDevice, storage: Storage,
        progress: Callable[[str], None] | None = None) -> str:
    """Copy the file as planned under the sync's lock, check its size on the device and record
    it. The path written. A file of its name that appeared since the plan is never overwritten;
    what a failed copy left is removed (Storage.write)."""
    with service.sync_lock():
        if storage.resolve(plan.path) is not None:
            raise CourseError(_taken_text(plan.name))
        say_to(progress)(copying_text(plan.course or plan.name))
        try:
            real = storage.write_checked(plan.path, plan.data, error=CourseError)
        except (DeviceNotResponding, CourseError):
            raise
        except (DeviceError, OSError) as e:
            log.warning("courses: the copy failed (%s)", type(e).__name__)
            failed = _("Writing {path} failed.").format(path=plan.path)
            raise CourseError(f"{failed} ({e})") from e
        copies = [c for c in recorded(device.unit_id)
                  if str(c.get("file", "")).casefold() != plan.name.casefold()]
        _keep(device.unit_id, [*copies, {"file": plan.name, "courses": plan.courses,
                                         "size": plan.size, "md5": plan.md5,
                                         "copied": time.time()}])
    log.info("courses: a GPX file copied: %s, %d course(s), %d waypoint(s)",
             applog.size(plan.size), plan.gpx.courses, plan.gpx.waypoints)  # no names
    return real


# --- removing a course or a waiting file ---------------------------------------------------------


@dataclass(frozen=True)
class RemovePlan:
    filename: str
    path: str  # on the device, as it spells it
    size: int
    title: str  # what the question names
    waiting: bool  # a GPX file the device has not made a course of yet
    origin: str | None = None  # of a course
    made: int | None = None  # of a course


def _check_as_shown(storage: Storage, path: str, name: str, size: int) -> None:
    """A file is only removed while it is the one the list showed: still there, the same
    size."""
    if storage.resolve(path) is None:
        raise CourseError(_("{name} is no longer on the device.").format(name=name))
    if storage.size(path) != size:
        # TRANSLATORS: a course's file or a waiting GPX file chosen to be removed
        raise CourseError(_("{name} has changed since the list was shown, so it is not "
                            "removed.").format(name=name))


def plan_remove(device: GarminDevice, storage: Storage, shown: Course | Waiting) -> RemovePlan:
    """What is checked before a course or a waiting GPX file is removed, as the list showed it;
    nothing is deleted. Only one file directly in the device's courses folder (a .fit) or in the
    folder of its GPX files (a GPX file) is removed, never a folder or the locations file, and
    only while it has not changed since it was shown."""
    waiting = isinstance(shown, Waiting)
    applog.hide(shown.filename, "file")
    applog.hide(shown.title, "course")
    if waiting:
        target = gpx_target(device)
        folder = target.path.strip("/") if target is not None else None
        extension = _extension(target) if target is not None else ".gpx"
    else:
        folder, extension = _out_folder(device, COURSE_DATATYPE), ".fit"
    name = shown.filename
    if (folder is None or "/" in name or "\\" in name
            or not name.casefold().endswith(extension.casefold())
            or not name[:-len(extension)].strip(".")):
        # TRANSLATORS: what was chosen to be removed is not a course's file or a waiting GPX
        # file of the device's folders
        raise CourseError(_("{name} is not the file of a course, so it is not removed.").format(
            name=name))
    path = f"{folder}/{name}"
    _check_as_shown(storage, path, name, shown.size)
    real = storage.resolve(path) or path
    if waiting:
        return RemovePlan(name, real, shown.size, shown.title, True)
    return RemovePlan(name, real, shown.size, shown.title, False, shown.origin, shown.made)


def removal_note(plan: RemovePlan) -> str:
    """What removing a course or a waiting file means, as the question before it says: what is
    lost, by where the course came from."""
    if plan.waiting:
        return _("The device has not made a course of it yet.")
    if plan.origin == ORIGIN_OPENEXPRESS:
        return _("The device keeps no other copy of it.")
    if plan.origin == ORIGIN_CONNECT:
        return _("The device keeps no other copy of it; Garmin Connect can send it again.")
    return _("openExpress did not copy this course and it did not come from Garmin Connect. "
             "The device keeps no other copy of it.")


def _forget(unit_id: int, plan: RemovePlan) -> None:
    """The record of a removed waiting file, or the name of a removed course of openExpress's
    (in the copies made before it), taken off; the other install's record is never written."""
    copies, changed = [], False
    for copy in recorded(unit_id):
        if plan.waiting and str(copy.get("file", "")).casefold() == plan.filename.casefold():
            changed = True
            continue
        at = _copied_at(copy)
        if (not plan.waiting and plan.made is not None and at is not None
                and at - CLOCKS_APART_S <= plan.made):
            names = [n for n in _names(copy) if not _same_course(plan.title, n)]
            if names != _names(copy):
                changed = True
                if not names:
                    continue
                copy = {**copy, "courses": names}
        copies.append(copy)
    if changed:
        _keep(unit_id, copies)


def remove(plan: RemovePlan, device: GarminDevice, storage: Storage) -> None:
    """Delete the file under the sync's lock, checked once more against what the list showed,
    make sure it is gone, then take it off the record."""
    with service.sync_lock():
        _check_as_shown(storage, plan.path, plan.filename, plan.size)
        storage.delete(plan.path)
        if storage.resolve(plan.path) is not None:
            raise CourseError(_("{path} is still on the device.").format(path=plan.path))
    log.info("courses: %s removed: %s", "a waiting GPX file" if plan.waiting else "a course",
             applog.size(plan.size))  # no names: they are the user's
    if plan.waiting or plan.origin == ORIGIN_OPENEXPRESS:
        _forget(device.unit_id, plan)
