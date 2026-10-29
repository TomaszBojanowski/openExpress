"""What this installation remembers of the Garmin devices it has seen, for devices that are
not connected now: devices.json in the data folder (~/.local/share/openexpress; in the
Flatpak below XDG_DATA_HOME, ~/.var/app/<app ID>/data).

One entry per unit ID: what GarminDevice.xml says, when the device was seen, its kind as
Garmin tells it, and the address of its model's picture as Garmin gives it (the account's
list, or Garmin's update server for a device off the account), to download the picture again
once it is out of the cache: only an address picture_urls allows, of a picture of the model,
with neither the unit ID nor anything of the account in it. No address of the device itself
(an MTP address has the serial number), no GarminDevice.xml (it has it too).

The facts of its tile are one object of the entry, "tile_facts", which tile_facts.py alone
writes and reads: what the window's check of updates last found to install (kept 30 days;
UpdateSummary for its device page) and when that check last answered (UpdateCheck, for its
tile and its device page while it is not connected), what the count in the background last
found to upload (SyncCount), what the account's list said of it (AccountFact) and what is
known of its sync (SyncedFact). An entry without that object is read from the fields at its
top where older files kept them.

Forgetting a device removes its entry and nothing else: its own folder (devices/<unit ID>/:
the sync state, own maps…) stays. The unit ID is never written to the log."""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
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
# page knows them offline. An optional field: an entry without it has no kind (not known).
WATCH, CYCLING_COMPUTER, BIKE_ACCESSORY = "watch", "cycling-computer", "bike-accessory"
CATEGORIES = (WATCH, CYCLING_COMPUTER, BIKE_ACCESSORY)
# A handheld (inReach, GPSMAP, Montana…): told by the model name only, for the icon; Garmin
# tells no such kind that the program knows, so it is never kept here (not in CATEGORIES).
HANDHELD = "handheld"
# An update the window's check found for a device (the time of that check) is shown on its
# tile for so long; an older finding counts as none (the update may be installed since).
UPDATE_FOUND_KEPT_S = 30 * 86400
# A count of the activities to upload is shown on the tile from the first frame for so long
# (and only while the sync state is the one it was counted against).
SYNC_COUNT_KEPT_S = 7 * 86400


@dataclass(frozen=True)
class UpdateSummary:
    """What the window's check of updates found, for the device page to show until its own
    check ends: how many updates, of one its name (as the server gives it), version and
    whether it is of the system, and the software the device had then. Only shown: nothing is
    ever downloaded or installed from it."""

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
    """The last check of updates of the window that answered: when, the software the device
    had then, and whether an update was copied to it already and waited for the device to
    install it once disconnected. What it found to install is in update_found and
    update_summary."""

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


@dataclass(frozen=True)
class SyncCount:
    """What the window's count in the background last found for a connected device of the
    account: how many activities it has to upload, when it was counted, and the version of
    the sync state it was counted against (service.sync_stamp). For its tile from the first
    frame; the count that runs then confirms or changes it."""

    count: int
    at: float
    state: str

    def to_json(self) -> dict:
        return {"count": self.count, "at": self.at, "state": self.state}

    @classmethod
    def from_json(cls, value) -> SyncCount | None:
        """None for anything else than what to_json writes (damaged, edited by hand)."""
        if not isinstance(value, dict):
            return None
        count, at, state = (value.get(key) for key in ("count", "at", "state"))
        if (isinstance(count, bool) or not isinstance(count, int) or count < 0
                or isinstance(at, bool) or not isinstance(at, int | float)
                or not isinstance(state, str)):
            return None
        return cls(count, float(at), state)


@dataclass(frozen=True)
class AccountFact:
    """What the account's list said of a remembered device when it was last read: whether
    the account lists it, and whether it is its primary wearable; when, and the version of
    the login saved it was read with (store.stamp)."""

    on_account: bool
    primary: bool
    at: float
    login: str

    def to_json(self) -> dict:
        return {"on_account": self.on_account, "primary": self.primary, "at": self.at,
                "login": self.login}

    @classmethod
    def from_json(cls, value) -> AccountFact | None:
        """None for anything else than what to_json writes (damaged, edited by hand)."""
        if not isinstance(value, dict):
            return None
        on, primary, at, login = (value.get(k) for k in ("on_account", "primary", "at",
                                                         "login"))
        if (not isinstance(on, bool) or not isinstance(primary, bool)
                or isinstance(at, bool) or not isinstance(at, int | float)
                or not isinstance(login, str)):
            return None
        return cls(on, primary, float(at), login)


@dataclass(frozen=True)
class SyncedFact:
    """What this installation knew of the sync of a device when it was last seen or synced,
    and when (`at`)."""

    once: bool  # it synced it at all (its state.json is there)
    last: float | None  # when its last sync recorded was
    # That last sync could not upload (no consent, device sync off in Garmin Connect).
    not_uploaded: bool
    at: float
    state: str | None  # its sync state's version then (service.sync_stamp); None: never synced

    def to_json(self) -> dict:
        return {"once": self.once, "last": self.last, "not_uploaded": self.not_uploaded,
                "at": self.at, "state": self.state}

    @classmethod
    def from_json(cls, value) -> SyncedFact | None:
        """None for anything else than what to_json writes (damaged, edited by hand)."""
        if not isinstance(value, dict):
            return None
        once, last, not_uploaded, at, state = (
            value.get(k) for k in ("once", "last", "not_uploaded", "at", "state"))
        if (not isinstance(once, bool) or not isinstance(not_uploaded, bool)
                or isinstance(at, bool) or not isinstance(at, int | float)
                or (last is not None and (isinstance(last, bool)
                                          or not isinstance(last, int | float)))
                or (state is not None and not isinstance(state, str))):
            return None
        return cls(once, float(last) if last is not None else None, not_uploaded, float(at),
                   state)


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
    # The last count of its activities to upload (SyncCount); None: not known.
    sync_count: SyncCount | None = None
    # The USB product ID of a drive ("0f1d"), kept when it was opened: the name of its tile
    # while it is not mounted and nothing of it can be read; None: not known.
    usb_product: str | None = None
    # What the account's list said of it (AccountFact) and what is known of its sync
    # (SyncedFact); None: not known. With update_found, update_summary, update_check and
    # sync_count: the facts its tile shows, kept in one object, "tile_facts" (tile_facts.py
    # writes and reads them).
    account: AccountFact | None = None
    synced: SyncedFact | None = None
    # The USB serial of a drive and the UUID of its volume, kept when it was opened: they
    # tell which remembered drive a drive not mounted is, when its serial is not its unit ID
    # in hexadecimal (a Varia Rearview Radar gives no serial). None: not known.
    # As private as the unit ID, as the file is.
    usb_serial: str | None = None
    volume_uuid: str | None = None
    # The name Garmin gives its model (unit-info, garmin_name), kept for a device whose
    # GarminDevice.xml names it in capitals only ("EPIX"); None: not known.
    model_name: str | None = None

    @property
    def shown_name(self) -> str:
        """Its name as the window shows it (shown_name)."""
        return shown_name(self.description, self.model_name)


# The longest name of a model kept from unit-info.
MODEL_NAME_MAX = 80


def garmin_name(model) -> str | None:
    """The name of a model as unit-info gives it (devicemodel, "epix™ (Gen 2) — Sapphire
    Edition | 47 mm"), as the window shows it: without ™ and ®, with " — " and " | " as
    ", " ("epix (Gen 2), Sapphire Edition, 47 mm"); None for none, or a name too long."""
    if not isinstance(model, str):
        return None
    name = model.replace("\u2122", "").replace("\u00ae", "")
    name = name.replace(" \u2014 ", ", ").replace(" | ", ", ")
    name = name.strip()
    while "  " in name:
        name = name.replace("  ", " ")
    return name if name and len(name) <= MODEL_NAME_MAX else None


def shown_name(description: str, model_name: str | None) -> str:
    """The name of a device in the window: the name Garmin gives its model when its
    GarminDevice.xml names it in capitals only ("EPIX"), else that name as it is."""
    return model_name if model_name and description.isupper() else description


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
             kind: str, now: float | None = None, usb_product: str | None = None,
             usb_serial: str | None = None, volume_uuid: str | None = None) -> KnownDevice:
    """Add or update the entry of a device seen now; when it was first seen stays, and its
    USB product, serial and the UUID of its volume unless new ones are given; the facts of
    its tile stay (tile_facts writes them)."""
    with _locked():
        known, writable = _read()
        now = time.time() if now is None else now
        old = known.get(unit_id) or KnownDevice(
            unit_id=unit_id, description=description, part_number=part_number,
            software_version=software_version, kind=kind, first_seen=now, last_seen=now)
        entry = replace(old, description=description, part_number=part_number,
                        software_version=software_version, kind=kind, last_seen=now,
                        usb_product=usb_product or old.usb_product,
                        usb_serial=_identifier(usb_serial) or old.usb_serial,
                        volume_uuid=_identifier(volume_uuid) or old.volume_uuid)
        known[unit_id] = entry
        if writable:
            _write(known)
            log.info("devices: remembered %s", part_number)
    return entry


def seen(unit_id: int, description: str, part_number: str, software_version: str,
         kind: str, usb_product: str | None = None, usb_serial: str | None = None,
         volume_uuid: str | None = None) -> None:
    """remember() for the detection, then what is known of its sync now, for its tile
    (tile_facts.note_synced): a device without a unit ID (no <Id>) is not remembered, and a
    failure (e.g. a full disk) is only written to the log: remembering a device never stops
    it from opening."""
    if not unit_id:
        return
    from openexpress import tile_facts  # it reads devices.json through this module

    try:
        remember(unit_id, description, part_number, software_version, kind,
                 usb_product=usb_product, usb_serial=usb_serial, volume_uuid=volume_uuid)
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: not remembered (%s)", type(e).__name__)
        return
    tile_facts.note_synced(unit_id)


def set_model_name(unit_id: int, name: str) -> bool:
    """Keep the name Garmin gives the model of a device remembered (garmin_name); whether it
    changed. A device that is not remembered gets no entry for it."""
    name = garmin_name(name)
    changed = _update(unit_id, lambda entry: replace(entry, model_name=name)) if name else None
    if changed is None:
        return False
    log.info("devices: the name of %s from unit-info kept", changed[1].part_number)
    return True


def set_category(unit_id: int, category: str) -> bool:
    """Keep the kind of a device remembered (as Garmin told it); whether it changed. A
    device that is not remembered gets no entry for it."""
    if category not in CATEGORIES:
        return False
    changed = _update(unit_id, lambda entry: replace(entry, category=category))
    if changed is None:
        return False
    log.info("devices: %s is a %s", changed[1].part_number, category)
    return True


def set_image_url(unit_id: int, url) -> bool:
    """Keep the address of the picture of a device remembered, as Garmin gives it, in place
    of the one before; whether it changed. Only an address picture_urls allows, kept as it is
    asked (plain HTTP over HTTPS); a device not remembered gets no entry. The log names the
    host and whether the address has a query, only."""
    url = picture_urls.upgraded(url)
    if not picture_urls.allowed(url):
        return False
    changed = _update(unit_id, lambda entry: replace(entry, image_url=url))
    if changed is None:
        return False
    log.info("devices: the picture of %s is at %s; %s", changed[1].part_number,
             urlsplit(url).hostname, picture_urls.query_said(url))
    return True


def set_update_found(unit_id: int, found: bool, now: float | None = None,
                     summary: UpdateSummary | None = None, software_version: str | None = None,
                     ready: bool = False) -> bool:
    """Keep what the window's check of updates found for a device remembered; whether the
    entry changed. `found`: the time of this check is kept, with `summary`; else both go.
    With `software_version` (the device's now), the check itself is kept too (UpdateCheck;
    `ready`: an update copied already waits for the device). A device not remembered gets no
    entry."""
    def change(entry: KnownDevice) -> KnownDevice:
        at = time.time() if now is None else now
        check = (UpdateCheck(at, software_version, ready) if software_version is not None
                 else entry.update_check)
        return replace(entry, update_found=at if found else None,
                       update_summary=summary if found else None, update_check=check)

    changed = _update(unit_id, change)
    if changed is None:
        return False
    before, entry = changed
    said = (before.update_found is not None,
            before.update_check is not None and before.update_check.ready)
    check = entry.update_check
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


def set_sync_count(unit_id: int, count: SyncCount) -> bool:
    """Keep what the window's count of the activities to upload found for a device
    remembered; whether the entry changed. A device that is not remembered gets no entry for
    it. Nothing goes to the log (the count has its own line)."""
    return _update(unit_id, lambda entry: replace(entry, sync_count=count)) is not None


def set_account_facts(facts: dict[int, AccountFact]) -> int:
    """Keep what the account's list said of the devices remembered (AccountFact by unit ID);
    how many entries changed. Devices not remembered get no entry for it."""
    with _locked():
        known, writable = _read()
        changed = 0
        for unit_id, fact in facts.items():
            entry = known.get(unit_id)
            if entry is not None and entry.account != fact:
                entry.account = fact
                changed += 1
        if not writable or not changed:
            return 0
        _write(known)
    return changed


def set_synced(unit_id: int, fact: SyncedFact) -> bool:
    """Keep what is known of the sync of a device remembered (SyncedFact); whether the entry
    changed."""
    return _update(unit_id, lambda entry: replace(entry, synced=fact)) is not None


def recorded_sync_count(entry: KnownDevice | None, state: str | None,
                        now: float | None = None) -> int | None:
    """How many activities the last count found to upload, if it was less than
    SYNC_COUNT_KEPT_S ago and of the sync state the device has now (`state`,
    service.sync_stamp); None otherwise."""
    kept = entry.sync_count if entry is not None else None
    if kept is None or state is None or kept.state != state:
        return None
    if (time.time() if now is None else now) - kept.at >= SYNC_COUNT_KEPT_S:
        return None
    return kept.count


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


def _update(unit_id: int, change: Callable[[KnownDevice], KnownDevice]
            ) -> tuple[KnownDevice, KnownDevice] | None:
    """Change the entry of a device remembered and write the file, under the lock: `change`
    gets the entry and returns the new one. Returns (the entry before, after); None, writing
    nothing, for a device not remembered, a file that may not be written over, or no change."""
    with _locked():
        known, writable = _read()
        entry = known.get(unit_id)
        if entry is None or not writable:
            return None
        new = change(entry)
        if new == entry:
            return None
        known[unit_id] = new
        _write(known)
    return entry, new


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
            facts = _tile_facts(value)
            update = facts.get("update") if isinstance(facts.get("update"), dict) else {}
            update_found = update.get("found")
            entry = KnownDevice(
                unit_id=int(key), description=str(value["description"]),
                part_number=str(value["part_number"]),
                software_version=str(value["software_version"]), kind=str(value["kind"]),
                first_seen=float(value["first_seen"]), last_seen=float(value["last_seen"]),
                category=category if category in CATEGORIES else None,
                image_url=image_url if picture_urls.allowed(image_url) else None,
                update_found=float(update_found) if isinstance(update_found, int | float)
                and not isinstance(update_found, bool) else None,
                update_summary=UpdateSummary.from_json(update.get("summary")),
                update_check=UpdateCheck.from_json(update.get("check")),
                sync_count=SyncCount.from_json(facts.get("sync")),
                usb_product=_product(value.get("usb_product")),
                account=AccountFact.from_json(facts.get("account")),
                synced=SyncedFact.from_json(facts.get("synced")),
                usb_serial=_identifier(value.get("usb_serial")),
                volume_uuid=_identifier(value.get("volume_uuid")),
                model_name=garmin_name(value.get("model_name")))
            known[entry.unit_id] = entry
    except (ValueError, KeyError, TypeError, AttributeError):
        log.warning("devices: %s is damaged; it is left as it is and nothing is remembered "
                    "from it", FILE_NAME)
        return {}, False
    return known, True


def _tile_facts(value: dict) -> dict:
    """The object "tile_facts" of an entry; for an entry an older version wrote, the same
    facts from where they were then (update_found, update_summary, update_check, sync_count);
    an entry with neither has none (not known)."""
    facts = value.get("tile_facts")
    if isinstance(facts, dict):
        return facts
    return {"update": {"found": value.get("update_found"),
                       "summary": value.get("update_summary"),
                       "check": value.get("update_check")},
            "sync": value.get("sync_count")}


def _product(value) -> str | None:
    """A USB product ID as written: four hexadecimal digits; anything else is not kept."""
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{4}", value) else None


def _identifier(value) -> str | None:
    """A USB serial or the UUID of a volume as written: 1 to 64 printable characters without
    a space; anything else is not kept."""
    return value if isinstance(value, str) and re.fullmatch(r"[!-~]{1,64}", value) else None


# The fields of an entry kept in its object "tile_facts", not at its top.
_FACT_FIELDS = ("update_found", "update_summary", "update_check", "sync_count", "account",
                "synced")


def _write(known: dict[int, KnownDevice]) -> None:
    devices = {}
    for unit_id, entry in sorted(known.items()):
        fields = asdict(entry)
        del fields["unit_id"]  # it is the key
        for name in _FACT_FIELDS:
            del fields[name]
        for optional in ("category", "image_url", "usb_product", "usb_serial", "volume_uuid",
                         "model_name"):
            if fields[optional] is None:
                del fields[optional]  # written only when known
        update = {"found": entry.update_found,
                  "summary": entry.update_summary.to_json() if entry.update_summary else None,
                  "check": entry.update_check.to_json() if entry.update_check else None}
        facts = {"update": {k: v for k, v in update.items() if v is not None},
                 "sync": entry.sync_count.to_json() if entry.sync_count else None,
                 "synced": entry.synced.to_json() if entry.synced else None,
                 "account": entry.account.to_json() if entry.account else None}
        fields["tile_facts"] = {k: v for k, v in facts.items() if v not in (None, {})}
        devices[str(unit_id)] = fields
    paths.write_private(path(), json.dumps({"version": FORMAT, "devices": devices}, indent=1,
                                           ensure_ascii=False))
