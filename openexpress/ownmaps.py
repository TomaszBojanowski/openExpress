"""Maps on the device that are not from Garmin (e.g. made from OpenStreetMap data).

A map is one .img file in the device's map folder. Its name is in the header of the file:
the first 512 bytes carry the signatures "DSKIMG" (0x10) and "GARMIN" (0x41), a description
in two parts (20 bytes at 0x49, up to 30 bytes and a zero at 0x65) and 55 AA at 0x1FE; a
non-zero first byte means the whole file is XORed with it. The format is not published by
Garmin; this is what the public descriptions of it say and what a map from BBBike.org shows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from openexpress.device.storage import DeviceError, Storage
from openexpress.device.xmldevice import GarminDevice

log = logging.getLogger(__name__)

HEADER_SIZE = 512
# Data types that name the folder for maps, in the order they are tried.
MAP_DATATYPES = ("NMaps", "SupplementalMaps")
# Garmin's own map files: a letter code with digits ("D1234567A.img") or "gmap…" (base map,
# time zone map). A map of the user's must never be taken for one of those, nor the reverse.
_GARMIN_NAME = re.compile(r"(?i)^(d[a-z]?\d+[a-z]?|gmap\w*)\.img$")


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


def list_own(device: GarminDevice, storage: Storage, garmin_maps: list) -> list[OwnMap]:
    """The .img files in the map folder that Garmin's server does not list (for any map,
    installed or not) and that are not named like Garmin's files. Reads the folder and the
    first bytes of each such file; nothing is written."""
    folder = map_dir(device)
    if folder is None:
        return []
    listed = set()
    for m in garmin_maps:
        listed.update(f.device_path.casefold() for f in m.files)
        listed.update(p.casefold() for p in m.files_to_remove)
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
