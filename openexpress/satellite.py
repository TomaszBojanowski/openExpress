"""Satellite data (experimental): the file that speeds up the first GPS fix of watches that keep
it as GARMIN/REMOTESW/CPE.BIN in the one format known here (that of a Forerunner 45: 63,488
bytes, which Garmin's address below gives, not a tar archive).

Only a device that had a file of this format of its own is offered it; seen once, it is
remembered (devices/<unit ID>/satellite.json in the data folder), also after the file is gone.
The feature is off until it is turned on for the device. With it on, a sync downloads Garmin's
current file (kept in the cache for 4 hours, as the server says it may be) and writes it when it
differs from the device's, as Garmin sends it, never changed, then reads it back whole. A device
that renamed the file written to CPE_BAD.bin rejected it: the feature turns itself off for it,
until it is turned on again. The request to Garmin carries no account and nothing of the device.

Has: nothing of the window."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, fields

from openexpress import applog, cache, paths
from openexpress.device.storage import DeviceError, DeviceNotResponding

log = logging.getLogger(__name__)

URL = "https://api.gcs.garmin.com/ephemeris/cpe/sony?coverage=WEEKS_1"
FOLDER = "GARMIN/REMOTESW"
NAME = "CPE.BIN"
REJECTED = "CPE_BAD.bin"  # what a watch renames a file it does not take to
SIZE = 63_488
START = bytes.fromhex("2a12a002")
KEPT = 4 * 3600  # the server's max-age
TIMEOUT = 30
FILE_NAME = "satellite.json"


def known_format(data: bytes) -> bool:
    """Whether `data` is a file of the format known: 63,488 bytes that begin with 2a 12 a0 02,
    the length of their data (bytes 4-7, little-endian) within them, zeros after it."""
    if len(data) != SIZE or not data.startswith(START):
        return False
    length = int.from_bytes(data[4:8], "little")
    return 8 < length <= SIZE and not any(data[length:])


@dataclass
class State:
    """What is kept of a device that had a file of the format known."""

    on: bool = False  # turned on for it (off until then)
    written_at: float | None = None  # when this computer last wrote the file to it
    written_sha: str | None = None  # the SHA-256 of that file, until the device rejects it
    stopped: bool = False  # turned off because the device rejected the file written
    # Why the last sync with it on did not update it (Result.reason); None: it did.
    error: str | None = None


@dataclass(frozen=True)
class Result:
    """What a sync did of the satellite data: "updated" (written, `size` bytes), "current" (the
    device has Garmin's current file), "would" (the dry run: it would be written), "failed"
    (not updated: `reason`) or "stopped" (the device rejected the file written: turned off).
    A reason is put into words only when shown (gui/words.satellite_reason): FORMAT, WRITE,
    or NETWORK followed by the words of the network error."""

    kind: str
    size: int = 0
    reason: str | None = None


FORMAT = "format"  # Garmin's file is not of the format known
WRITE = "write"  # the file could not be written to the device
NETWORK = "network:"  # Garmin's file could not be had: then the words of the error


class NotUpdated(Exception):
    """Garmin's file could not be had, or is not of the format known; `reason` as Result's."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _path(unit_id: int):
    return paths.device_dir(unit_id) / FILE_NAME


def load(unit_id: int) -> State | None:
    """The state kept of a device; None: it never had a file of the format known here. A
    record that cannot be read is the state of a device just seen (off)."""
    try:
        data = json.loads(_path(unit_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("satellite data: %s cannot be read; taken as off", FILE_NAME)
        return State()
    known = {f.name: f.type for f in fields(State)}
    values = {k: v for k, v in data.items() if k in known} if isinstance(data, dict) else {}
    try:
        state = State(**values)
    except TypeError:
        return State()
    if not isinstance(state.on, bool) or not isinstance(state.stopped, bool):
        return State()
    return state


def save(unit_id: int, state: State) -> None:
    paths.make_dir(paths.device_dir(unit_id))
    paths.write_private(_path(unit_id), json.dumps(asdict(state)))


def set_on(unit_id: int, on: bool) -> State:
    """Turn it on or off for a device that had the format; turned on again after the device
    rejected a file, the next sync tries once more."""
    state = load(unit_id) or State()
    state.on = on
    if on:
        state.stopped = False
        state.error = None
    save(unit_id, state)
    log.info("satellite data: turned %s", "on" if on else "off")
    return state


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(storage, name: str) -> bytes | None:
    """The file `name` of the folder, when it has the size of the format; None otherwise
    (absent, or another file of another size, which is never read)."""
    path = f"{FOLDER}/{name}"
    if storage.size(path) != SIZE:
        return None
    return storage.read(path)


def _rejected(unit_id: int, state: State, storage, current: bytes | None,
              keep: bool = True) -> bool:
    """Whether the device rejected the file written last: CPE.BIN is gone and CPE_BAD.bin holds
    it. Then the state is turned off (stopped), and kept so unless `keep` is False."""
    if not state.written_sha or current is not None:
        return False
    bad = _read(storage, REJECTED)
    if bad is None or _sha(bad) != state.written_sha:
        return False
    state.on, state.stopped, state.written_sha = False, True, None
    if keep:
        save(unit_id, state)
        log.info("satellite data: the device renamed the file written to %s: turned off",
                 REJECTED)
    return True


def seen(device, storage) -> State | None:
    """At an opening: the state of a device that has, or had, a file of the format known (a
    device seen with one now is remembered); None for any other device, and for a copy of a
    device's files (a test drive). Reads CPE.BIN of the format's size only, and CPE_BAD.bin after
    a file was written."""
    unit = getattr(device, "unit_id", None)
    if not unit or getattr(storage, "test_drive", False):
        return None
    state = load(unit)
    current = _read(storage, NAME)
    if state is None:
        if current is None or not known_format(current):
            return None
        state = State()
        save(unit, state)
        log.info("satellite data: %s has %s in the format known: offered (off)",
                 getattr(device, "part_number", "?"), NAME)
    _rejected(unit, state, storage, current)
    return state


def _cache_files():
    folder = paths.cache_dir() / "software" / "satellite"
    return folder, folder / NAME, folder / "fetched.json"


def _cached() -> bytes | None:
    _folder, data_path, when_path = _cache_files()
    try:
        at = json.loads(when_path.read_text(encoding="utf-8")).get("at")
        data = data_path.read_bytes()
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(at, (int, float)) or not 0 <= time.time() - at < KEPT:
        return None
    return data if known_format(data) else None


def download(http=None) -> bytes:
    """Garmin's current file: from the cache while it is fresh (KEPT), else asked; raises
    NotUpdated when it cannot be had or is not of the format known."""
    import requests

    from openexpress import useragent

    with cache.using():
        data = _cached()
        if data is not None:
            log.info("satellite data: Garmin's file from the cache")
            return data
        http = http or useragent.session()
        started = time.monotonic()
        try:
            r = http.get(URL, headers={"Accept": "application/octet-stream"}, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            log.info("satellite data: not downloaded: %s", applog.network_error_text(e))
            raise NotUpdated(NETWORK + applog.network_error_text(e)) from e
        log.info("HTTP GET %s -> %s in %.1f s", URL, r.status_code, time.monotonic() - started)
        data = r.content
        kind = r.headers.get("content-type", "")
        if not kind.startswith("application/octet-stream") or not known_format(data):
            log.info("satellite data: Garmin's file is not of the format known (%s, %s)",
                     kind or "no type", applog.size(len(data)))
            raise NotUpdated(FORMAT)
        folder, data_path, when_path = _cache_files()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            data_path.write_bytes(data)
            when_path.write_text(json.dumps({"at": time.time()}), encoding="utf-8")
        except OSError as e:  # asked again next time: the file itself is good
            log.info("satellite data: not kept in the cache (%s)", type(e).__name__)
        return data


def _failed(unit_id: int, state: State, reason: str, apply: bool) -> Result:
    if apply:
        state.error = reason
        save(unit_id, state)
    return Result("failed", reason=reason)


def update(device, storage, apply: bool = True, http=None) -> Result | None:
    """The satellite data of a sync: None when it is not for this device or is off; else
    what was done (Result). `apply` False: the dry run, nothing written or kept."""
    unit = getattr(device, "unit_id", None)
    if not unit or getattr(storage, "test_drive", False):
        return None
    state = load(unit)
    if state is None:
        return None
    current = _read(storage, NAME)
    if _rejected(unit, state, storage, current, keep=apply):
        return Result("stopped")
    if not state.on:
        return None
    try:
        data = download(http)
    except NotUpdated as e:
        return _failed(unit, state, e.reason, apply)
    if current is not None and _sha(current) == _sha(data):
        log.info("satellite data: %s on the device is the same file, nothing written", NAME)
        if apply and state.error is not None:
            state.error = None
            save(unit, state)
        return Result("current")
    if not apply:
        return Result("would", size=len(data))
    try:
        real = storage.write_checked(f"{FOLDER}/{NAME}", data)
        if storage.read_at(real) != data:
            storage.delete_quietly(real)
            raise DeviceError("the file read back differs from the one written")
    except DeviceNotResponding:
        raise
    except DeviceError as e:
        log.info("satellite data: %s not written (%s)", NAME, type(e).__name__)
        return _failed(unit, state, WRITE, apply)
    state.written_at, state.written_sha, state.error = time.time(), _sha(data), None
    save(unit, state)
    log.info("satellite data: %s written (%s), read back whole", NAME, applog.size(len(data)))
    return Result("updated", size=len(data))
