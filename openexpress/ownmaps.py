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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openexpress import applog, paths
from openexpress.device.storage import DeviceError, Storage
from openexpress.device.xmldevice import GarminDevice
from openexpress.i18n import _, human_size
from openexpress.maps import SPACE_MARGIN, MapError

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
    folder = map_dir(device)
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
        own.append(OwnMap(entry.name, entry.size, title))
    log.info("own maps: %d", len(own))  # counts only: the file names are the user's own
    return own


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
        raise MapError(_("The name {name} cannot be used: only letters, digits, spaces and "
                         ". _ - ( ) are allowed, and it must end with .img.").format(name=name))
    return name, title, local.stat().st_size


def _exists_text(name: str) -> str:
    return _("A file named {name} is already on the device; nothing is overwritten. Choose "
             "another name with --name.").format(name=name)


def plan_add(device: GarminDevice, storage: Storage, garmin_maps: list, local: Path,
             name: str | None = None) -> AddPlan:
    """Everything that is checked before a map is copied; nothing is written. Raises
    MapError with the reason when the map cannot be added."""
    name, title, size = check_local(local, name)
    folder = map_dir(device)
    if folder is None:
        raise MapError(_("This device has no folder for maps."))
    path = f"{folder}/{name}"
    # A file with one of Garmin's names would be overwritten or deleted by a later install
    # or removal of that map.
    if is_garmin_name(name) or path.casefold() in garmin_paths(garmin_maps):
        raise MapError(_("The name {name} is reserved for Garmin's own maps. Choose another "
                         "one with --name.").format(name=name))
    if storage.resolve(path) is not None:  # whatever it is, and whatever its case
        raise MapError(_exists_text(name))
    free = storage.free_space()
    if free is not None and size + SPACE_MARGIN > free:
        raise MapError(_("Not enough space on the device: {need} needed, {free} free")
                       .format(need=human_size(size), free=human_size(free)))
    return AddPlan(local, name, path, title, size, free)


def add(plan: AddPlan, device: GarminDevice, storage: Storage, transfer_progress=None) -> str:
    """Copy the map, check its size on the device and put it on the list of own maps.
    Returns the path written. When the copy fails, the incomplete file is removed: it can
    only be this run's, because no file of that name was there a moment before."""
    if storage.resolve(plan.path) is not None:
        raise MapError(_exists_text(plan.name))  # it appeared since the plan was made
    try:
        real = storage.write_file(plan.path, plan.local, transfer_progress)
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


def _record(device: GarminDevice, entry: dict) -> None:
    maps = [m for m in recorded(device)
            if str(m.get("filename", "")).casefold() != entry["filename"].casefold()]
    maps.append(entry)
    try:
        f = _state_file(device)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"maps": maps}, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        # The map is on the device; only the note about it is missing.
        log.warning("own map: the list of own maps could not be saved (%s)", type(e).__name__)
