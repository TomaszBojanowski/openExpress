"""Maps on the device that are not from Garmin (e.g. made from OpenStreetMap data).

A map is one .img file in the device's map folder. Its name is in the header of the file:
the first 512 bytes carry the signatures "DSKIMG" (0x10) and "GARMIN" (0x41), a description
in two parts (20 bytes at 0x49, up to 30 bytes and a zero at 0x65) and 55 AA at 0x1FE; a
non-zero first byte means the whole file is XORed with it. The format is not published by
Garmin; this is what the public descriptions of it say and what a map from BBBike.org shows.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openexpress import applog, paths
from openexpress.device.storage import DeviceError, Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.i18n import _
from openexpress.maps import SPACE_MARGIN, MapError
from openexpress.progress import Step

log = logging.getLogger(__name__)

HEADER_SIZE = 512
# Data types that name the folder for maps, in the order they are tried.
MAP_DATATYPES = ("NMaps", "SupplementalMaps")
# Garmin's own map files: a letter code with digits ("D1234567A.img") or "gmap…" (base map,
# time zone map). A map of the user's must never be taken for one of those, nor the reverse.
_GARMIN_NAME = re.compile(r"(?i)^(d[a-z]?\d+[a-z]?|gmap\w*)\.img$")
# What a map of the user's may be called on the device: plain ASCII, nothing a file system
# or the device could trip over.
_OWN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]*\.img$")
MAX_NAME = 64
STATE_NAME = "ownmaps.json"


@dataclass
class OwnMap:
    filename: str
    size: int
    title: str | None  # from the header; None when it could not be read
    path: str = ""  # folder and name on the device


class OwnMapError(MapError):
    """A refusal the user can do something about. What to do depends on where they are: the
    message is the same for the command line and the window, the hint that follows is not."""

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind  # "taken", "reserved" or "name"

    def text(self, window: bool) -> str:
        if window:
            hint = _("Rename the file on your computer and add it again.")
        else:
            hint = {"taken": _("Choose another name with --name."),
                    "reserved": _("Choose another one with --name.")}.get(self.kind, "")
        return f"{self} {hint}".strip()


@dataclass
class Overview:
    """What the Maps page shows about the maps that are not from Garmin."""
    folder: str | None  # None: the device has no folder for maps
    maps: list[OwnMap]
    removable: set[str]  # file names this installation copied: they can be removed
    unreadable: bool = False  # the folder could not be listed
    # Maps by their header that this installation did not copy (by hand, or by another
    # installation): they can be removed after a stronger question. A file without the
    # header of a map is in neither set: nobody knows what it is.
    foreign: set[str] = field(default_factory=set)


def read_title(data: bytes) -> str | None:
    """The name of the map in the start of an .img file; None when it is not such a file."""
    if len(data) < HEADER_SIZE:
        return None
    key = data[0]
    head = bytes(b ^ key for b in data[:HEADER_SIZE]) if key else data[:HEADER_SIZE]
    if head[0x10:0x16] != b"DSKIMG" or head[0x41:0x47] != b"GARMIN" or head[0x1FE:] != b"\x55\xaa":
        return None
    raw = (head[0x49:0x5D] + head[0x65:0x83]).split(b"\0")[0]
    # Whatever the bytes are: unknown ones are replaced, control characters become spaces.
    text = raw.decode("cp1252", "replace")
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())


def is_garmin_name(filename: str) -> bool:
    return _GARMIN_NAME.match(filename) is not None


def map_dir(device: GarminDevice) -> str | None:
    """The folder the device keeps its maps in; None for a device without maps."""
    for datatype in MAP_DATATYPES:
        targets = device.targets(datatype)
        if targets and targets[0].path:
            return targets[0].path.strip("/")
    return None


def device_map_dir(device: GarminDevice, storage: Storage) -> str | None:
    """The map folder as the device itself spells it ("GARMIN" where its description says
    "Garmin"), so that every message names it the same way; None for a device without maps."""
    folder = map_dir(device)
    if folder is None:
        return None
    return storage.resolve(folder) or folder


def garmin_paths(garmin_maps: list) -> set[str]:
    """Every file Garmin's server names for the device's maps, installed or not (casefolded):
    the files of a map and those a newer version of it removes."""
    listed: set[str] = set()
    for m in garmin_maps:
        listed.update(f.device_path.casefold() for f in m.files)
        listed.update(p.casefold() for p in m.files_to_remove)
    return listed


def list_own(device: GarminDevice, storage: Storage, garmin_maps: list) -> list[OwnMap]:
    """The .img files in the map folder that Garmin's server does not list (for any map,
    installed or not) and that are not named like Garmin's files. Reads the folder and the
    first bytes of each such file; nothing is written."""
    folder = device_map_dir(device, storage)
    if folder is None:
        return []
    listed = garmin_paths(garmin_maps)
    own = []
    for entry in sorted(storage.listdir(folder), key=lambda e: e.name.casefold()):
        path = f"{folder}/{entry.name}"
        if (entry.is_dir or not entry.name.lower().endswith(".img")
                or is_garmin_name(entry.name) or path.casefold() in listed):
            continue
        try:
            title = read_title(storage.read_start(path, HEADER_SIZE))
        except DeviceError as e:
            log.info("own maps: the start of a file could not be read (%s)", type(e).__name__)
            title = None
        own.append(OwnMap(entry.name, entry.size, title, path))
    log.info("own maps: %d", len(own))  # counts only: the file names are the user's own
    return own


def overview(device: GarminDevice, storage: Storage, garmin_maps: list) -> Overview:
    """The own maps on the device and which of them this installation may remove. Reads
    the map folder and the start of each own map; a folder that cannot be read is reported,
    not raised, so that the rest of the page still shows."""
    folder = map_dir(device)
    if folder is None:
        return Overview(None, [], set())
    try:
        maps = list_own(device, storage, garmin_maps)
    except DeviceError as e:
        log.info("own maps: the map folder could not be read (%s)", type(e).__name__)
        return Overview(folder, [], set(), unreadable=True)
    ours = {str(m.get("filename", "")).casefold() for m in recorded(device)}
    removable = {m.filename for m in maps if m.filename.casefold() in ours}
    foreign = {m.filename for m in maps if m.filename not in removable and m.title is not None}
    return Overview(folder, maps, removable, foreign=foreign)


# --- copying a map of the user's to the device ------------------------------------------------


@dataclass
class AddPlan:
    local: Path
    name: str  # the file name on the device
    path: str  # folder and name on the device
    title: str  # from the header of the file
    size: int
    free: int | None  # on the device, before


def valid_name(name: str) -> bool:
    return len(name) <= MAX_NAME and _OWN_NAME.match(name) is not None


def header_of(local: Path) -> str | None:
    """The name in the header of a local .img file; None when it is not a map file."""
    try:
        with open(local, "rb") as f:
            return read_title(f.read(HEADER_SIZE))
    except OSError:
        return None


def check_local(local: Path, name: str | None) -> tuple[str, str, int]:
    """What can be said without the device: the name the file gets, the name of the map and
    its size. Raises MapError when the file is not a map or the name cannot be used."""
    # The names are the user's own: an error message that carries them is shown, but in the
    # log they are replaced.
    applog.hide(local.name, "file")
    applog.hide(name, "file")
    if not local.is_file():
        raise MapError(_("Not found: {path}").format(path=local))
    title = header_of(local)
    if title is None:
        raise MapError(_("{file} is not a Garmin map file (.img).").format(file=local.name))
    applog.hide(title, "map")
    name = name if name is not None else local.name
    if not valid_name(name):
        raise OwnMapError(_("The name {name} cannot be used: only letters, digits, spaces and "
                            ". _ - ( ) are allowed, and it must end with .img.").format(
            name=name), "name")
    return name, title, local.stat().st_size


def _taken(name: str) -> OwnMapError:
    return OwnMapError(_("A file named {name} is already on the device; nothing is "
                         "overwritten.").format(name=name), "taken")


def plan_add(device: GarminDevice, storage: Storage, garmin_maps: list, local: Path,
             name: str | None = None) -> AddPlan:
    """Everything that is checked before a map is copied; nothing is written. Raises
    MapError with the reason when the map cannot be added."""
    name, title, size = check_local(local, name)
    folder = device_map_dir(device, storage)
    if folder is None:
        raise MapError(_("This device has no folder for maps."))
    path = f"{folder}/{name}"
    # A file with one of Garmin's names would be overwritten or deleted by a later install
    # or removal of that map.
    if is_garmin_name(name) or path.casefold() in garmin_paths(garmin_maps):
        raise OwnMapError(_("The name {name} is reserved for Garmin's own maps.").format(
            name=name), "reserved")
    if storage.resolve(path) is not None:  # whatever it is, and whatever its case
        raise _taken(name)
    free = storage.require_space(size, SPACE_MARGIN, error=MapError)
    return AddPlan(local, name, path, title, size, free)


def add(plan: AddPlan, device: GarminDevice, storage: Storage,
        progress: Callable[[str], None] | None = None) -> str:
    """Copy the map, check its size on the device and put it on the list of own maps.
    Returns the path written. When the copy fails, the incomplete file is removed: it can
    only be this run's, because no file of that name was there a moment before. `progress`:
    the copy as steps, by the map's title ("Copying … to the device · 1.2 MB of 3.4 MB")."""
    if storage.resolve(plan.path) is not None:
        raise _taken(plan.name)  # it appeared since the plan was made
    told = None
    if progress is not None:
        head = _("Copying {name} to the device").format(name=plan.title or plan.name)

        def told(done: int, total: int | None) -> None:
            progress(Step(head, done, total, in_bytes=True))
    try:
        real = storage.write_file(plan.path, plan.local, told)
        written = storage.size(plan.path)
        if written != plan.size:
            raise DeviceError(_("the file on the device has {actual} instead of {expected}")
                              .format(actual="?" if written is None else f"{written} B",
                                      expected=f"{plan.size} B"))
    except (DeviceError, OSError) as e:
        log.warning("own map: the copy failed (%s)", type(e).__name__)
        raise MapError(f"{_failed_text(storage, plan.path)} ({e})") from e
    _record(device, {"filename": plan.name, "size": plan.size, "title": plan.title,
                     "added": datetime.now().isoformat(timespec="seconds")})
    log.info("own map added: %s", applog.size(plan.size))  # no names: they are the user's
    return real


def _failed_text(storage: Storage, path: str) -> str:
    """Remove what a failed copy left behind and say what happened to it."""
    if storage.resolve(path) is None:
        return _("Writing {path} failed.").format(path=path)
    try:
        storage.delete(path)
    except (DeviceError, OSError):
        return _("Writing {path} failed and the incomplete file could not be removed; "
                 "delete {path} from the device yourself.").format(path=path)
    return _("Writing {path} failed; the incomplete file was removed from the device."
             ).format(path=path)


def _state_file(device: GarminDevice) -> Path:
    # The folder is named after the unit ID: this path is never printed or logged.
    return paths.device_dir(device.unit_id) / STATE_NAME


def recorded(device: GarminDevice) -> list[dict]:
    """The maps this program copied to the device: file name, size, map name, date."""
    try:
        data = json.loads(_state_file(device).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    maps = data.get("maps") if isinstance(data, dict) else None
    return [m for m in maps if isinstance(m, dict)] if isinstance(maps, list) else []


def _save(device: GarminDevice, maps: list[dict]) -> None:
    """The list written whole or not at all (a list cut short would read as empty, and the
    next write would drop every map in it), readable by its owner only."""
    paths.write_private(_state_file(device),
                        json.dumps({"maps": maps}, indent=1, ensure_ascii=False))


def _record(device: GarminDevice, entry: dict) -> None:
    maps = [m for m in recorded(device)
            if str(m.get("filename", "")).casefold() != entry["filename"].casefold()]
    maps.append(entry)
    try:
        _save(device, maps)
    except OSError as e:
        # The map is on the device; only the note about it is missing.
        log.warning("own map: the list of own maps could not be saved (%s)", type(e).__name__)


def _forget(device: GarminDevice, name: str) -> None:
    maps = [m for m in recorded(device)
            if str(m.get("filename", "")).casefold() != name.casefold()]
    try:
        _save(device, maps)
    except OSError as e:
        # The file is gone from the device; the next removal finds only the stale note.
        log.warning("own map: the list of own maps could not be saved (%s)", type(e).__name__)


# --- removing a map this installation copied -----------------------------------------------------


@dataclass
class RemovePlan:
    name: str  # the file name, as it is on the list
    path: str  # on the device, as the device spells it
    title: str
    size: int
    free: int | None  # on the device, before
    on_device: bool  # False: only the note on the list is left
    # False for a map this installation did not copy: nothing is on the list for it, and
    # instead of the size that was written, what the page showed is checked (`title`, `size`).
    listed: bool = True


def _size_differs_text(name: str, actual: int | None, expected: int) -> str:
    return _("{name} on the device has {actual}, but the map that was copied had {expected}: "
             "it is not the same file, so it is not removed.").format(
        name=name, actual="?" if actual is None else f"{actual} B", expected=f"{expected} B")


def plan_remove(device: GarminDevice, storage: Storage, garmin_maps: list,
                name: str) -> RemovePlan:
    """What is checked before a map of the user's is removed; nothing is deleted. Only a file
    that this installation copied (it is on the list in the device's state) can be removed,
    and only while its size on the device is the one that was written."""
    applog.hide(name, "file")
    folder = device_map_dir(device, storage)
    if folder is None:
        raise MapError(_("This device has no folder for maps."))
    entry = next((m for m in recorded(device)
                  if str(m.get("filename", "")).casefold() == name.casefold()), None)
    if entry is None:
        raise MapError(_(
            "{name} was not copied to the device by this installation of openExpress (it may "
            "have been copied by hand or by another installation), so it is not removed. "
            "Delete it in the file manager if you want it gone.").format(name=name))
    filename, title = str(entry["filename"]), str(entry.get("title") or "")
    size = int(entry.get("size") or 0)
    applog.hide(filename, "file")
    applog.hide(title, "map")
    path = f"{folder}/{filename}"
    # Never one of Garmin's files, even if such a name got onto the list.
    if is_garmin_name(filename) or path.casefold() in garmin_paths(garmin_maps):
        raise MapError(_("{name} is named like one of Garmin's own maps, so it is not removed.")
                       .format(name=filename))
    real = storage.resolve(path)
    if real is None:
        return RemovePlan(filename, path, title, size, storage.free_space(), on_device=False)
    actual = storage.size(path)
    if actual != size:
        raise MapError(_size_differs_text(filename, actual, size))
    return RemovePlan(filename, real, title, size, storage.free_space(), on_device=True)


def _still_there_text(path: str) -> str:
    return _("{path} is still on the device.").format(path=path)


def _check_as_shown(storage: Storage, path: str, name: str, size: int, title: str) -> None:
    """A map that is not on the list is only removed while it is the file the page showed:
    the same size, and still a map with the same name inside."""
    if storage.resolve(path) is None:
        raise MapError(_("{name} is no longer on the device.").format(name=name))
    if (storage.size(path) != size
            or read_title(storage.read_start(path, HEADER_SIZE)) != title):
        raise MapError(_("{name} has changed since the list was shown (another size or "
                         "another map name inside), so it is not removed.").format(name=name))


def plan_remove_foreign(device: GarminDevice, storage: Storage, garmin_maps: list,
                        shown: OwnMap) -> RemovePlan:
    """The plan for removing a map this installation did not copy, as the page showed it
    (`shown`). Nothing is deleted. Never one of Garmin's files, and only a file that is a map
    by its header and has not changed since it was shown."""
    applog.hide(shown.filename, "file")
    applog.hide(shown.title, "map")
    folder = device_map_dir(device, storage)
    if folder is None:
        raise MapError(_("This device has no folder for maps."))
    path = f"{folder}/{shown.filename}"
    if is_garmin_name(shown.filename) or path.casefold() in garmin_paths(garmin_maps):
        raise MapError(_("{name} is named like one of Garmin's own maps, so it is not removed.")
                       .format(name=shown.filename))
    if shown.title is None:  # not a map by its header: nobody knows what it is
        raise MapError(_("{file} is not a Garmin map file (.img).").format(file=shown.filename))
    _check_as_shown(storage, path, shown.filename, shown.size, shown.title)
    return RemovePlan(shown.filename, storage.resolve(path) or path, shown.title, shown.size,
                      storage.free_space(), on_device=True, listed=False)


def remove(plan: RemovePlan, device: GarminDevice, storage: Storage) -> None:
    """Delete the file, make sure it is gone, and only then take it off the list. A plan for
    a file that is no longer on the device only takes the note off the list. A map that is
    not on the list is checked once more against what the page showed, right before."""
    if not plan.listed:
        _check_as_shown(storage, plan.path, plan.name, plan.size, plan.title)
        storage.delete(plan.path)
        if storage.resolve(plan.path) is not None:
            raise MapError(_still_there_text(plan.path))
        log.info("own map removed (not on the list): %s", applog.size(plan.size))  # no names
        return  # nothing on the list to take off
    if plan.on_device:
        if storage.resolve(plan.path) is not None:
            actual = storage.size(plan.path)
            if actual != plan.size:  # it changed since the plan was made
                raise MapError(_size_differs_text(plan.name, actual, plan.size))
            storage.delete(plan.path)
            if storage.resolve(plan.path) is not None:
                raise MapError(_("{path} is still on the device; nothing was taken off the "
                                 "list.").format(path=plan.path))
        log.info("own map removed: %s", applog.size(plan.size))  # no names
    _forget(device, plan.name)
