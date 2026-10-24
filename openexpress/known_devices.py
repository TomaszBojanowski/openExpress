"""What this installation remembers of the Garmin devices it has seen, for devices that are
not connected now: devices.json in the data folder (~/.local/share/openexpress; in the
Flatpak below XDG_DATA_HOME, ~/.var/app/<app ID>/data). One entry per unit ID, with what
GarminDevice.xml says and when the device was seen, its kind as Garmin tells it, and the
address of the picture of its model as Garmin gives it (the account's list, or Garmin's
update server for a device off the account), so that the picture can be downloaded again
when it is no longer in the cache: a picture of the model, with neither the unit ID nor
anything of the account in it, and only an address picture_urls allows; and when the
window's check of updates last found one to install, for its tile (kept 30 days). No address of the
device itself (an MTP address has the serial number), no GarminDevice.xml (it has it too).

Forgetting a device removes its entry and nothing else: its own folder (devices/<unit ID>/:
the sync state, own maps…) stays. The unit ID is never written to the log."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from openexpress import paths, picture_urls

log = logging.getLogger(__name__)

FILE_NAME = "devices.json"
# Held while devices.json is read, changed and written, so that the window, the command
# line and the background service never write over each other's entries.
LOCK_NAME = "devices.lock"
FORMAT = 1
# The kinds of device, as Garmin tells them (never guessed from a name): kept so that the start
# page knows them offline. An optional field: files without it are read as before.
WATCH, CYCLING_COMPUTER, BIKE_ACCESSORY = "watch", "cycling-computer", "bike-accessory"
CATEGORIES = (WATCH, CYCLING_COMPUTER, BIKE_ACCESSORY)
# A handheld (inReach, GPSMAP, Montana…): told by the model name only, for the icon; Garmin
# tells no such kind that the program knows, so it is never kept here (not in CATEGORIES).
HANDHELD = "handheld"
# An update the window's check found for a device (the time of that check) is shown on its
# tile for so long; an older finding counts as none (the update may be installed since).
UPDATE_FOUND_KEPT_S = 30 * 86400


@dataclass
class KnownDevice:
    unit_id: int = field(repr=False)
    description: str
    part_number: str
    software_version: str
    kind: str  # "mtp", or "ums" for a Garmin device in mass storage mode
    first_seen: float
    last_seen: float
    category: str | None = None  # one of CATEGORIES; None: not known
    image_url: str | None = None  # the address of its model's picture; None: not known
    # When the window's check last found an update for it (the time of the check); None:
    # none found, or none since a check found it up to date or an update was installed.
    update_found: float | None = None


def path() -> Path:
    return paths.data_dir() / FILE_NAME


def load() -> dict[int, KnownDevice]:
    """The devices remembered, by unit ID. No file: none. A damaged file: none, said in the
    log; the file is left as it is."""
    return _read()[0]


def in_order(known: dict[int, KnownDevice]) -> list[KnownDevice]:
    """The devices as they are listed and numbered: the one seen last first."""
    return sorted(known.values(), key=lambda d: (-d.last_seen, d.part_number, d.description))


def remember(unit_id: int, description: str, part_number: str, software_version: str,
             kind: str, now: float | None = None) -> KnownDevice:
    """Add or update the entry of a device seen now; when it was first seen stays."""
    with _locked():
        known, writable = _read()
        now = time.time() if now is None else now
        old = known.get(unit_id)
        entry = KnownDevice(unit_id, description, part_number, software_version, kind,
                            old.first_seen if old is not None else now, now,
                            old.category if old is not None else None,
                            old.image_url if old is not None else None,
                            old.update_found if old is not None else None)
        known[unit_id] = entry
        if writable:
            _write(known)
            log.info("devices: remembered %s", part_number)
    return entry


def seen(unit_id: int, description: str, part_number: str, software_version: str,
         kind: str) -> None:
    """remember() for the detection: a device without a unit ID (no <Id>) is not remembered,
    and a failure (e.g. a full disk) is only written to the log: remembering a device never
    stops it from opening."""
    if not unit_id:
        return
    try:
        remember(unit_id, description, part_number, software_version, kind)
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: not remembered (%s)", type(e).__name__)


def set_category(unit_id: int, category: str) -> bool:
    """Keep the kind of a device remembered (as Garmin told it); whether it changed. A
    device that is not remembered gets no entry for it."""
    if category not in CATEGORIES:
        return False
    with _locked():
        known, writable = _read()
        entry = known.get(unit_id)
        if entry is None or not writable or entry.category == category:
            return False
        entry.category = category
        _write(known)
    log.info("devices: %s is a %s", entry.part_number, category)
    return True


def set_image_url(unit_id: int, url) -> bool:
    """Keep the address of the picture of a device remembered, as Garmin gives it (a newer one
    replaces the one before); whether it changed. Only an address picture_urls allows, kept
    as it is asked (plain HTTP over HTTPS); a device that is not remembered gets no entry for
    it. The log names the host, and whether the address has a query, only."""
    url = picture_urls.upgraded(url)
    if not picture_urls.allowed(url):
        return False
    with _locked():
        known, writable = _read()
        entry = known.get(unit_id)
        if entry is None or not writable or entry.image_url == url:
            return False
        entry.image_url = url
        _write(known)
    log.info("devices: the picture of %s is at %s; %s", entry.part_number,
             urlsplit(url).hostname, picture_urls.query_said(url))
    return True


def set_update_found(unit_id: int, found: bool, now: float | None = None) -> bool:
    """What the window's check of updates found for a device remembered: an update (the
    time of this check is kept), or none (what was kept goes); whether the entry changed. A
    device that is not remembered gets no entry for it. The log names the part number."""
    with _locked():
        known, writable = _read()
        entry = known.get(unit_id)
        if entry is None or not writable:
            return False
        value = (time.time() if now is None else now) if found else None
        if entry.update_found == value:
            return False
        entry.update_found = value
        _write(known)
    if found:
        log.info("devices: an update for %s found", entry.part_number)
    else:
        log.info("devices: no update waits for %s", entry.part_number)
    return True


def update_found_recently(found: float | None, now: float | None = None) -> float | None:
    """When an update was found, if that was less than UPDATE_FOUND_KEPT_S ago; else None
    (an older finding counts as none)."""
    if found is None:
        return None
    return found if (time.time() if now is None else now) - found < UPDATE_FOUND_KEPT_S else None


def forget(unit_id: int) -> bool:
    """Remove the entry of this device; whether there was one. Nothing else is deleted."""
    with _locked():
        known, writable = _read()
        entry = known.pop(unit_id, None)
        if entry is None or not writable:
            return False
        _write(known)
    log.info("devices: forgot %s", entry.part_number)
    return True


@contextlib.contextmanager
def _locked():
    """One reader-changer-writer of devices.json at a time, in any process; the system lets go
    of it when the process ends. Reading alone needs no lock: the file is replaced whole."""
    folder = paths.data_dir()
    paths.make_dir(folder)
    with open(folder / LOCK_NAME, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read() -> tuple[dict[int, KnownDevice], bool]:
    """(the devices remembered, whether the file may be written): a damaged file is never
    written over, so that nothing in it is lost without a word."""
    try:
        text = path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, True
    except OSError as e:
        log.warning("devices: %s cannot be read (%s); nothing is remembered from it",
                    FILE_NAME, type(e).__name__)
        return {}, False
    try:
        data = json.loads(text)
        known = {}
        for key, value in data["devices"].items():
            category = value.get("category")
            image_url = value.get("image_url")
            update_found = value.get("update_found")
            entry = KnownDevice(int(key), str(value["description"]), str(value["part_number"]),
                                str(value["software_version"]), str(value["kind"]),
                                float(value["first_seen"]), float(value["last_seen"]),
                                category if category in CATEGORIES else None,
                                image_url if picture_urls.allowed(image_url) else None,
                                float(update_found) if isinstance(update_found, int | float)
                                and not isinstance(update_found, bool) else None)
            known[entry.unit_id] = entry
    except (ValueError, KeyError, TypeError, AttributeError):
        log.warning("devices: %s is damaged; it is left as it is and nothing is remembered "
                    "from it", FILE_NAME)
        return {}, False
    return known, True


def _write(known: dict[int, KnownDevice]) -> None:
    devices = {}
    for unit_id, entry in sorted(known.items()):
        fields = asdict(entry)
        del fields["unit_id"]  # it is the key
        for optional in ("category", "image_url", "update_found"):
            if fields[optional] is None:
                del fields[optional]  # written only when known
        devices[str(unit_id)] = fields
    paths.write_private(path(), json.dumps({"version": FORMAT, "devices": devices}, indent=1,
                                           ensure_ascii=False))
