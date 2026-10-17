"""What this installation remembers of the Garmin devices it has seen, for devices that are
not connected now: devices.json in the data folder (~/.local/share/openexpress; in the
Flatpak below XDG_DATA_HOME, ~/.var/app/<app ID>/data). One entry per unit ID, with only
what GarminDevice.xml says and when the device was seen: no address (an MTP address has the
serial number), no GarminDevice.xml (it has it too), nothing of the account.

Forgetting a device removes its entry and nothing else: its own folder (devices/<unit ID>/:
the sync state, own maps…) stays. The unit ID is never written to the log."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from openexpress import paths

log = logging.getLogger(__name__)

FILE_NAME = "devices.json"
FORMAT = 1


@dataclass
class KnownDevice:
    unit_id: int = field(repr=False)
    description: str
    part_number: str
    software_version: str
    kind: str  # "mtp", or "ums" for a Garmin device in mass storage mode
    first_seen: float
    last_seen: float


def path() -> Path:
    return paths.data_dir() / FILE_NAME


def load() -> dict[int, KnownDevice]:
    """The devices remembered, by unit ID. No file: none. A damaged file: none, said in the
    log; the file is left as it is."""
    return _read()[0]


def remember(unit_id: int, description: str, part_number: str, software_version: str,
             kind: str, now: float | None = None) -> KnownDevice:
    """Add or update the entry of a device seen now; when it was first seen stays."""
    known, writable = _read()
    now = time.time() if now is None else now
    old = known.get(unit_id)
    entry = KnownDevice(unit_id, description, part_number, software_version, kind,
                        old.first_seen if old is not None else now, now)
    known[unit_id] = entry
    if writable:
        _write(known)
        log.info("devices: remembered %s", part_number)
    return entry


def forget(unit_id: int) -> bool:
    """Remove the entry of this device; whether there was one. Nothing else is deleted."""
    known, writable = _read()
    entry = known.pop(unit_id, None)
    if entry is None or not writable:
        return False
    _write(known)
    log.info("devices: forgot %s", entry.part_number)
    return True


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
            entry = KnownDevice(int(key), str(value["description"]), str(value["part_number"]),
                                str(value["software_version"]), str(value["kind"]),
                                float(value["first_seen"]), float(value["last_seen"]))
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
        devices[str(unit_id)] = fields
    paths.write_private(path(), json.dumps({"version": FORMAT, "devices": devices}, indent=1,
                                           ensure_ascii=False))
