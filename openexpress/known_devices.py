"""What this installation remembers of the Garmin devices it has seen, for devices that are
not connected now: devices.json in the data folder (~/.local/share/openexpress; in the
Flatpak below XDG_DATA_HOME, ~/.var/app/<app ID>/data). One entry per unit ID, with what
GarminDevice.xml says and when the device was seen, its kind as Garmin tells it, and the
address of the picture of its model as Garmin gives it (the account's list, or Garmin's
update server for a device off the account), so that the picture can be downloaded again
when it is no longer in the cache: a picture of the model, with neither the unit ID nor
anything of the account in it, and only an address picture_urls allows; and when the
window's check of updates last found one to install, for its tile (kept 30 days), with what it
found (how many; of one, its name, version and whether it is of the system; the software of the
device then), for its device page; and when that check last answered, with the software of the
device then and whether an update was copied to it already (for its tile and the page of the
device when it is not connected). No address of the device itself (an MTP address has the
serial number), no GarminDevice.xml (it has it too).

Forgetting a device removes its entry and nothing else: its own folder (devices/<unit ID>/:
the sync state, own maps…) stays. The unit ID is never written to the log."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import time
from collections.abc import Callable
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


@dataclass(frozen=True)
class UpdateSummary:
    """What the window's check of updates found (24.10.2026), for the device page to show
    until its own check ends: how many updates, of one its name (as the server gives it),
    version and whether it is of the system, and the software the device had then. Only
    shown: nothing is ever downloaded or installed from it."""

    count: int
    software_version: str
    title: str | None = None
    version: str | None = None
    is_system: bool | None = None

    def to_json(self) -> dict:
        kept = {"count": self.count}
        if self.title is not None:
            kept.update(title=self.title, version=self.version, is_system=self.is_system)
        kept["software_version"] = self.software_version
        return kept

    @classmethod
    def from_json(cls, value) -> UpdateSummary | None:
        """None for anything else than what to_json writes (damaged, edited by hand)."""
        if not isinstance(value, dict):
            return None
        count, software = value.get("count"), value.get("software_version")
        if (isinstance(count, bool) or not isinstance(count, int) or count < 1
                or not isinstance(software, str)):
            return None
        one = [value.get(key) for key in ("title", "version", "is_system")]
        if one == [None, None, None]:
            return cls(count, software)
        title, version, is_system = one
        if (count != 1 or not isinstance(title, str) or not isinstance(version, str)
                or not isinstance(is_system, bool)):
            return None
        return cls(count, software, title, version, is_system)


@dataclass(frozen=True)
class UpdateCheck:
    """The last check of updates of the window that answered (25.10.2026): when, the
    software the device had then, and whether an update was copied to it already and waited
    for the device to install it once disconnected. What it found to install is in
    update_found and update_summary."""

    at: float
    software_version: str
    ready: bool = False

    def to_json(self) -> dict:
        return {"at": self.at, "software_version": self.software_version, "ready": self.ready}

    @classmethod
    def from_json(cls, value) -> UpdateCheck | None:
        """None for anything else than what to_json writes (damaged, edited by hand)."""
        if not isinstance(value, dict):
            return None
        at, software, ready = (value.get(key) for key in ("at", "software_version", "ready"))
        if (isinstance(at, bool) or not isinstance(at, int | float)
                or not isinstance(software, str) or not isinstance(ready, bool)):
            return None
        return cls(float(at), software, ready)


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
    # What that check found (UpdateSummary), with update_found; None: not known (an entry
    # of an older version, or a damaged one).
    update_summary: UpdateSummary | None = None
    # The last check that answered, whatever it found (UpdateCheck); None: not known.
    update_check: UpdateCheck | None = None


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
                            old.update_found if old is not None else None,
                            old.update_summary if old is not None else None,
                            old.update_check if old is not None else None)
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


def set_update_found(unit_id: int, found: bool, now: float | None = None,
                     summary: UpdateSummary | None = None, software_version: str | None = None,
                     ready: bool = False) -> bool:
    """What the window's check of updates found for a device remembered: an update (the
    time of this check is kept, with `summary`), or none (what was kept goes); with the
    device's software now (`software_version`), the check itself too (UpdateCheck: `ready`,
    an update copied already waits for the device). Whether the entry changed. A device
    that is not remembered gets no entry for it. The log names the part number."""
    with _locked():
        known, writable = _read()
        entry = known.get(unit_id)
        if entry is None or not writable:
            return False
        at = time.time() if now is None else now
        value = at if found else None
        summary = summary if found else None
        check = (UpdateCheck(at, software_version, ready) if software_version is not None
                 else entry.update_check)
        if (entry.update_found, entry.update_summary, entry.update_check) == (value, summary,
                                                                              check):
            return False
        said = (entry.update_found is not None,
                entry.update_check is not None and entry.update_check.ready)
        entry.update_found, entry.update_summary, entry.update_check = value, summary, check
        _write(known)
    if not found and said == (False, check is not None and check.ready):
        return True  # only the time of the check is new: nothing to say
    if found and ready:
        log.info("devices: an update for %s found, another copied already", entry.part_number)
    elif found:
        log.info("devices: an update for %s found", entry.part_number)
    elif ready:
        log.info("devices: an update for %s copied already, it waits to be installed",
                 entry.part_number)
    else:
        log.info("devices: no update waits for %s", entry.part_number)
    return True


def recorded_updates(entry: KnownDevice | None, software_version: str,
                     now: float | None = None) -> UpdateSummary | None:
    """What the last check found for this device, if it found an update less than
    UPDATE_FOUND_KEPT_S ago and the device has the same software now (else it may be
    installed since); None otherwise, and for an entry without a summary."""
    if entry is None or entry.update_summary is None:
        return None
    if update_found_recently(entry.update_found, now) is None:
        return None
    return entry.update_summary if entry.update_summary.software_version == software_version \
        else None


def recorded_check(entry: KnownDevice | None, software_version: str,
                   now: float | None = None) -> UpdateCheck | None:
    """The last check that answered for this device, if it was less than
    UPDATE_FOUND_KEPT_S ago and the device has the same software now; None otherwise."""
    check = entry.update_check if entry is not None else None
    if check is None or update_found_recently(check.at, now) is None:
        return None
    return check if check.software_version == software_version else None


def update_found_recently(found: float | None, now: float | None = None) -> float | None:
    """When an update was found, if that was less than UPDATE_FOUND_KEPT_S ago; else None
    (an older finding counts as none)."""
    if found is None:
        return None
    return found if (time.time() if now is None else now) - found < UPDATE_FOUND_KEPT_S else None


def forget(unit_id: int, before: Callable[[], str] | None = None) -> bool:
    """Remove the entry of this device; whether there was one. Nothing else is deleted here:
    `before`, when given, runs under the lock once the entry is found, deletes the rest and
    returns words for the log line; when it raises, the entry stays."""
    with _locked():
        known, writable = _read()
        entry = known.pop(unit_id, None)
        if entry is None or not writable:
            return False
        note = before() if before is not None else ""
        _write(known)
    log.info("devices: forgot %s%s", entry.part_number, note)
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
                                and not isinstance(update_found, bool) else None,
                                UpdateSummary.from_json(value.get("update_summary")),
                                UpdateCheck.from_json(value.get("update_check")))
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
        fields["update_summary"] = (entry.update_summary.to_json()
                                    if entry.update_summary is not None else None)
        fields["update_check"] = (entry.update_check.to_json()
                                  if entry.update_check is not None else None)
        for optional in ("category", "image_url", "update_found", "update_summary",
                         "update_check"):
            if fields[optional] is None:
                del fields[optional]  # written only when known
        devices[str(unit_id)] = fields
    paths.write_private(path(), json.dumps({"version": FORMAT, "devices": devices}, indent=1,
                                           ensure_ascii=False))
