"""Satellite data (experimental): the files that speed up the first GPS fix of a watch, written
as Garmin sends them, never changed, to a watch that already keeps them in a format known here:

* CPE.BIN in GARMIN/REMOTESW of the format of a Forerunner 45 (63,488 bytes, which the address
  URL gives, not a tar archive), written when it differs from the device's and read back whole;
  a device that renames the file written to CPE_BAD.bin rejected it;
* GPE.bin and GPES.bin in GARMIN/RemoteSW of the rxnetworks kind (gzip as Garmin serves them;
  the watch keeps them unpacked: 01 00, the time of their data, then bytes 6-15 as in RX_HEADER),
  as a fēnix 8 keeps them, hidden from the ordinary MTP listing. Each is written only where it was
  found, in a PTP session of our own (a write through gvfs would land on the hidden file of that
  name and keep its size and the tail of its content): the old file deleted, ours written, its
  first bytes read back. Written only when the device's file is older than its RxFile.fresh and
  Garmin's is newer; the device unpacks the file when it is unplugged.

Only a device that had such a file of its own is offered it; seen once, it is remembered
(devices/<unit ID>/satellite.json in the data folder), also after the file is gone. Files of the
rxnetworks kind that the ordinary listing shows (an Edge 1050 keeps GPES.bin so) are not offered:
that way of writing them was never tried. A device with GARMIN/RemoteSW and none of them shown is
looked at in a PTP session at a sync, at most once in RECHECK (the phone may give it the files
later). The feature is off until it is turned on for the device. Garmin's files are kept in the
cache for 4 hours, as the server says they may be; after its answer 429 nothing is asked of it
until the time it gave (blocked.json beside them). The requests carry no account and nothing of
the device.

Has: nothing of the window."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import zlib
from dataclasses import asdict, dataclass, fields
from email.utils import parsedate_to_datetime

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
CHECKED_NAME = "satellite-checked.json"  # when a device was last looked at for rxnetworks files

SONY, RXNETWORKS = "sony", "rxnetworks"
RX_FOLDER = "GARMIN/RemoteSW"
RX_HEADER = bytes.fromhex("12 00 00 00 00 00 00 00 80 00")  # bytes 6-15 of every file seen
RECHECK = 24 * 3600
BLOCKED_FOR = 3600  # after a 429 that gives no time


@dataclass(frozen=True)
class RxFile:
    """A file of the rxnetworks kind: its name as Garmin Express and the phone write it, its
    address, its size unpacked, and how old the device's may be before it is written again."""

    name: str
    url: str
    unpacked: int
    fresh: int


RX_FILES = (
    # Its data cover 7 days; the phone was seen to leave it for more than 8 hours.
    RxFile("GPE.bin", "https://api.gcs.garmin.com/ephemeris/cpe/rxnetworks/bytes?coverage=DAYS_7",
           213_108, 24 * 3600),
    # Its data cover 12 hours; the phone gives a new one every few hours.
    RxFile("GPES.bin",
           "https://api.gcs.garmin.com/ephemeris/cpe/rxnetworks/bytes?coverage=HOURS_12",
           15_976, 4 * 3600),
)


def known_format(data: bytes) -> bool:
    """Whether `data` is a file of the format known: 63,488 bytes that begin with 2a 12 a0 02,
    the length of their data (bytes 4-7, little-endian) within them, zeros after it."""
    if len(data) != SIZE or not data.startswith(START):
        return False
    length = int.from_bytes(data[4:8], "little")
    return 8 < length <= SIZE and not any(data[length:])


def rx_time(data: bytes) -> int | None:
    """The time in the header of a file of the rxnetworks kind, gzip (as Garmin serves it) or
    unpacked (as a watch keeps it); None when it is not of that kind."""
    raw = data
    if data[:2] == b"\x1f\x8b":
        try:
            raw = zlib.decompressobj(wbits=31).decompress(data, 64)
        except zlib.error:
            return None
    if len(raw) < 16 or raw[:2] != b"\x01\x00" or raw[6:16] != RX_HEADER:
        return None
    return int.from_bytes(raw[2:6], "big")


def rx_served(data: bytes, spec: RxFile) -> bool:
    """Whether Garmin's `data` can be written as `spec`: one whole gzip stream, of its size
    unpacked, of the rxnetworks kind."""
    if data[:2] != b"\x1f\x8b":
        return False
    stream = zlib.decompressobj(wbits=31)
    try:
        raw = stream.decompress(data)
    except zlib.error:
        return False
    return (stream.eof and not stream.unused_data and len(raw) == spec.unpacked
            and rx_time(raw) is not None)


@dataclass
class State:
    """What is kept of a device that had a file of a format known."""

    on: bool = False  # turned on for it (off until then)
    written_at: float | None = None  # when this computer last wrote a file to it
    written_sha: str | None = None  # CPE.BIN: the SHA-256 of the file written, until rejected
    stopped: bool = False  # turned off because the device did not take a file written
    # Why the last sync with it on did not update it (Result.reason); None: it did.
    error: str | None = None
    kind: str = SONY  # SONY (CPE.BIN) or RXNETWORKS (GPE.bin, GPES.bin)
    # RXNETWORKS: of each file found on the device (by its name), what is known: "time" (in the
    # header of the device's file as last read, or of ours as written), "pending" (ours written
    # and not seen taken yet), "sha" and "raw" (of ours, as written and unpacked), "connection"
    # (history.connection_key at the write), "gone" (times in a row ours was not there).
    files: dict | None = None
    why: str | None = None  # why it turned itself off: NOT_TAKEN, GONE_TWICE; None: CPE_BAD.bin
    current: bool = False  # the last sync found the device's data current, nothing written


@dataclass(frozen=True)
class Result:
    """What a sync did of the satellite data: "updated" (written, `size` bytes), "current" (the
    device has Garmin's current file, or one young enough), "would" (the dry run: it would be
    written), "failed" (not updated: `reason`) or "stopped" (the device did not take a file
    written: turned off; `reason` NOT_TAKEN or GONE_TWICE, None for CPE_BAD.bin). A reason is
    put into words only when shown (gui/words.satellite_reason): FORMAT, WRITE, HIDDEN, WAIT
    followed by the time, or NETWORK followed by the words of the network error."""

    kind: str
    size: int = 0
    reason: str | None = None


FORMAT = "format"  # Garmin's file is not of the format known
WRITE = "write"  # the file could not be written to the device
HIDDEN = "hidden"  # the device's hidden files cannot be reached (no PTP session here)
WAIT = "wait:"  # Garmin's server asked to wait: then the time (a Unix time) until when
NETWORK = "network:"  # Garmin's file could not be had: then the words of the error
NOT_TAKEN = "not taken"  # the device left our file as written, or renamed it
GONE_TWICE = "gone twice"  # our file was not on the device twice in a row


class NotUpdated(Exception):
    """Garmin's file could not be had, or is not of the format known; `reason` as Result's."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _path(unit_id: int):
    return paths.device_dir(unit_id) / FILE_NAME


def load(unit_id: int) -> State | None:
    """The state kept of a device; None: it never had a file of a format known here. A record
    that cannot be read is the state of a device just seen (off)."""
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
    if (not isinstance(state.on, bool) or not isinstance(state.stopped, bool)
            or not isinstance(state.current, bool) or state.kind not in (SONY, RXNETWORKS)
            or state.why not in (None, NOT_TAKEN, GONE_TWICE)):
        return State()
    if state.kind == RXNETWORKS and not (
            isinstance(state.files, dict) and state.files
            and all(isinstance(v, dict) for v in state.files.values())):
        log.warning("satellite data: %s holds no files; looked for again", FILE_NAME)
        return None  # the next sync looks for them as on a device not known
    return state


def save(unit_id: int, state: State) -> None:
    paths.make_dir(paths.device_dir(unit_id))
    paths.write_private(_path(unit_id), json.dumps(asdict(state)))


def set_on(unit_id: int, on: bool) -> State:
    """Turn it on or off for a device that had the format; turned on again after the device did
    not take a file, the next sync tries once more."""
    state = load(unit_id) or State()
    state.on = on
    if on:
        state.stopped = False
        state.error = None
        state.why = None
        for known in (state.files or {}).values():
            known["pending"], known["gone"] = False, 0
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
    """At an opening: the state of a device that has, or had, a file of a format known (a device
    seen with CPE.BIN of the format now is remembered); None for any other device, and for a copy
    of a device's files (a test drive). Reads CPE.BIN of the format's size only, and CPE_BAD.bin
    after a file was written. Files of the rxnetworks kind are looked for at a sync (update), as
    a watch hides them: the opening never opens a PTP session."""
    unit = getattr(device, "unit_id", None)
    if not unit or getattr(storage, "test_drive", False):
        return None
    state = load(unit)
    if state is not None and state.kind == RXNETWORKS:
        return state
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


# --- Garmin's files -----------------------------------------------------------------------------

def _cache_folder():
    return paths.cache_dir() / "software" / "satellite"


def _cache_files(name: str = NAME):
    folder = _cache_folder()
    return folder, folder / name, folder / ("fetched.json" if name == NAME
                                            else f"{name}.fetched.json")


def _cached(name: str, valid) -> bytes | None:
    _folder, data_path, when_path = _cache_files(name)
    try:
        at = json.loads(when_path.read_text(encoding="utf-8")).get("at")
        data = data_path.read_bytes()
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(at, (int, float)) or not 0 <= time.time() - at < KEPT:
        return None
    return data if valid(data) else None


def blocked_until() -> float | None:
    """Until when Garmin's server asked not to be asked again (its answer 429); None: it may be
    asked."""
    try:
        until = json.loads((_cache_folder() / "blocked.json").read_text(encoding="utf-8"))["until"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return until if isinstance(until, (int, float)) and until > time.time() else None


def _note_blocked(retry_after: str | None) -> float:
    """The time the server's answer 429 gives (Retry-After: seconds or a date; BLOCKED_FOR
    without one), kept for every file of the server."""
    until = time.time() + BLOCKED_FOR
    if retry_after:
        try:
            until = time.time() + max(0, int(retry_after))
        except ValueError:
            try:
                until = parsedate_to_datetime(retry_after).timestamp()
            except (TypeError, ValueError):
                pass
    try:
        _cache_folder().mkdir(parents=True, exist_ok=True)
        (_cache_folder() / "blocked.json").write_text(json.dumps({"until": until}),
                                                     encoding="utf-8")
    except OSError as e:
        log.info("satellite data: the server's wait not kept (%s)", type(e).__name__)
    return until


def _fetch(url: str, name: str, valid, http=None) -> bytes:
    """Garmin's file at `url`: from the cache while it is fresh (KEPT), else asked unless the
    server asked to wait; raises NotUpdated when it cannot be had or is not `valid`."""
    import requests

    from openexpress import useragent

    with cache.using():
        data = _cached(name, valid)
        if data is not None:
            log.info("satellite data: Garmin's file from the cache")
            return data
        until = blocked_until()
        if until is not None:
            log.info("satellite data: not asked; the server asked to wait %d min more",
                     (until - time.time()) // 60 + 1)
            raise NotUpdated(f"{WAIT}{int(until)}")
        http = http or useragent.session()
        started = time.monotonic()
        try:
            r = http.get(url, headers={"Accept": "application/octet-stream"}, timeout=TIMEOUT)
            if r.status_code == 429:
                until = _note_blocked(r.headers.get("retry-after"))
                log.info("satellite data: the server asks to wait %d min (429)",
                         (until - time.time()) // 60 + 1)
                raise NotUpdated(f"{WAIT}{int(until)}")
            r.raise_for_status()
        except requests.RequestException as e:
            log.info("satellite data: not downloaded: %s", applog.network_error_text(e))
            raise NotUpdated(NETWORK + applog.network_error_text(e)) from e
        log.info("HTTP GET %s -> %s in %.1f s", url, r.status_code, time.monotonic() - started)
        data = r.content
        kind = r.headers.get("content-type", "")
        if not kind.startswith("application/octet-stream") or not valid(data):
            log.info("satellite data: Garmin's file is not of the format known (%s, %s)",
                     kind or "no type", applog.size(len(data)))
            raise NotUpdated(FORMAT)
        folder, data_path, when_path = _cache_files(name)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            data_path.write_bytes(data)
            when_path.write_text(json.dumps({"at": time.time()}), encoding="utf-8")
        except OSError as e:  # asked again next time: the file itself is good
            log.info("satellite data: not kept in the cache (%s)", type(e).__name__)
        return data


def download(http=None) -> bytes:
    """Garmin's current CPE.BIN: from the cache while it is fresh (KEPT), else asked; raises
    NotUpdated when it cannot be had or is not of the format known."""
    return _fetch(URL, NAME, known_format, http)


def _failed(unit_id: int, state: State, reason: str, apply: bool) -> Result:
    if apply:
        state.error = reason
        save(unit_id, state)
    return Result("failed", reason=reason)


def update(device, storage, apply: bool = True, http=None) -> Result | None:
    """The satellite data of a sync: None when it is not for this device or is off; else
    what was done (Result). `apply` False: the dry run, nothing written or kept, and the device
    never left to a PTP session."""
    unit = getattr(device, "unit_id", None)
    if not unit or getattr(storage, "test_drive", False):
        return None
    state = load(unit)
    if state is None:
        if apply:
            try:
                _look_for_rx(unit, device, storage)  # offered when found, off: nothing written
            except DeviceNotResponding:
                raise
            except DeviceError as e:  # the sync is done; the next one looks again
                log.info("satellite data: not looked for (%s)", type(e).__name__)
        return None
    if state.kind == RXNETWORKS:
        return _rx_update(unit, device, storage, state, apply, http)
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


# --- files of the rxnetworks kind -----------------------------------------------------------

def _checked_path(unit_id: int):
    return paths.device_dir(unit_id) / CHECKED_NAME


def _checked_lately(unit_id: int) -> bool:
    try:
        at = json.loads(_checked_path(unit_id).read_text(encoding="utf-8"))["at"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return isinstance(at, (int, float)) and 0 <= time.time() - at < RECHECK


def _note_checked(unit_id: int) -> None:
    paths.make_dir(paths.device_dir(unit_id))
    paths.write_private(_checked_path(unit_id), json.dumps({"at": time.time()}))


def _rx_items(session):
    """The storage, GARMIN/RemoteSW and what Garmin's listing gives in it (hidden objects too);
    (None, None, []) when there is no such folder."""
    for store in session.storages():
        folder = session.lookup(store, RX_FOLDER)
        if folder is not None and folder.is_dir:
            return store, folder, session.all_children(store, folder.handle)
    return None, None, []


def _named(items, name: str) -> list:
    return [i for i in items if not i.is_dir and i.name.casefold() == name.casefold()]


def _look_for_rx(unit_id: int, device, storage) -> State | None:
    """At a sync of a device not known: GPE.bin and GPES.bin of the rxnetworks kind in
    GARMIN/RemoteSW. Shown by the ordinary listing: not offered (never tried). Else, at most once
    in RECHECK, a PTP session reads them hidden; found, the device is offered them (off)."""
    if storage.resolve(RX_FOLDER) is None:
        return None
    shown = [e.name for e in storage.listdir(RX_FOLDER)
             if not e.is_dir and e.name.casefold() in {f.name.casefold() for f in RX_FILES}]
    if shown:
        log.info("satellite data: GARMIN/RemoteSW shows %s: a device that keeps them shown is "
                 "not offered them yet", ", ".join(sorted(shown)))
        return None
    if getattr(storage, "mass_storage", False) or _checked_lately(unit_id):
        return None
    opener = storage.exclusive_ptp()
    if opener is None:
        return None
    found: dict[str, dict] = {}
    with opener as session:
        _store, _folder, items = _rx_items(session)
        for spec in RX_FILES:
            for item in _named(items, spec.name)[:1]:
                stamp = rx_time(session.read_file(item.handle))
                if stamp is not None:
                    found[spec.name] = {"time": stamp}
    _note_checked(unit_id)
    if not found:
        log.info("satellite data: no file of the rxnetworks kind on the device; looked at "
                 "again in %d h", RECHECK // 3600)
        return None
    state = State(kind=RXNETWORKS, files=found)
    save(unit_id, state)
    log.info("satellite data: %s has %s of the rxnetworks kind: offered (off)",
             getattr(device, "part_number", "?"), ", ".join(found))
    return state


def _old(known: dict, spec: RxFile, now: float) -> bool:
    """Whether, by what is known, the device's file may be old enough to write anew."""
    stamp = known.get("time")
    return not isinstance(stamp, (int, float)) or now - stamp >= spec.fresh


def _connection(device, storage) -> str | None:
    from openexpress import history

    try:
        return history.connection_key(device, storage)
    except DeviceNotResponding:
        raise
    except Exception as e:  # noqa: BLE001 - not known: a write then counts as not seen taken
        log.info("satellite data: the connection not told (%s)", type(e).__name__)
        return None


def _stop(unit_id: int, state: State, why: str, name: str) -> Result:
    state.on, state.stopped, state.why = False, True, why
    save(unit_id, state)
    log.info("satellite data: %s %s: turned off", name,
             "not taken by the device" if why == NOT_TAKEN else "gone twice after a write")
    return Result("stopped", reason=why)


def _rx_write(session, store, folder, items, spec: RxFile, data: bytes) -> None:
    """Our file written as `spec`: every file of its name deleted first (hidden too), ours
    written, its first bytes read back and compared."""
    for item in _named(items, spec.name):
        session.delete(item.handle)
    try:
        new = session.send_file(store, folder.handle, spec.name, data)
    except DeviceNotResponding:
        raise
    except DeviceError as e:
        handle = getattr(e, "handle", None)
        if handle is not None:
            session.delete(handle)
        raise
    if session.read_file(new.handle)[:len(data)] != data:
        session.delete(new.handle)
        raise DeviceError("the file read back differs from the one written")


def _rx_update(unit_id: int, device, storage, state: State, apply: bool, http) -> Result | None:
    if not state.on:
        return None
    now = time.time()
    specs = [spec for spec in RX_FILES if spec.name in state.files]
    if not apply:  # by what is known, without the device
        old = [spec for spec in specs if _old(state.files[spec.name], spec, now)]
        if not old:
            return Result("current")
        try:
            size = sum(len(_fetch(s.url, s.name, lambda d, s=s: rx_served(d, s), http))
                       for s in old)
        except NotUpdated as e:
            return Result("failed", reason=e.reason)
        return Result("would", size=size)
    if not any(_old(state.files[s.name], s, now) or state.files[s.name].get("pending")
               for s in specs):
        state.current, state.error = True, None
        save(unit_id, state)
        return Result("current")
    opener = storage.exclusive_ptp()
    if opener is None:
        return _failed(unit_id, state, HIDDEN, apply)
    key = _connection(device, storage)
    written, reason = 0, None
    try:
        with opener as session:
            store, folder, items = _rx_items(session)
            if folder is None:
                raise DeviceError("no GARMIN/RemoteSW")
            for spec in specs:
                known = state.files[spec.name]
                found = _named(items, spec.name)
                content = session.read_file(found[0].handle) if found else None
                write = content is None
                if known.get("pending"):
                    rejected = any(_sha(session.read_file(i.handle)) in (known.get("sha"),
                                                                          known.get("raw"))
                                   for i in items if not i.is_dir and "bad" in i.name.casefold())
                    if rejected:
                        return _stop(unit_id, state, NOT_TAKEN, spec.name)
                    if content is None:
                        known["gone"] = known.get("gone", 0) + 1
                        if known["gone"] >= 2:
                            return _stop(unit_id, state, GONE_TWICE, spec.name)
                    elif _sha(content) == known.get("sha"):  # ours, not unpacked yet
                        if key is None or known.get("connection") != key:
                            return _stop(unit_id, state, NOT_TAKEN, spec.name)
                        continue  # it is unpacked once the device is unplugged
                    else:  # taken (ours unpacked), or a newer one from the phone
                        known["pending"], known["gone"] = False, 0
                if content is not None:
                    known["time"] = rx_time(content)
                    write = _old(known, spec, now)
                if not write:
                    continue
                try:
                    data = _fetch(spec.url, spec.name, lambda d, s=spec: rx_served(d, s), http)
                except NotUpdated as e:
                    reason = reason or e.reason
                    continue
                theirs, ours = known.get("time"), rx_time(data)
                if content is not None and isinstance(theirs, int) and ours <= theirs:
                    continue  # Garmin has nothing newer than the device's
                _rx_write(session, store, folder, items, spec, data)
                known.update(time=ours, pending=True, sha=_sha(data),
                             raw=_sha(zlib.decompress(data, wbits=31)), connection=key)
                written += len(data)
                log.info("satellite data: %s written (%s), its first bytes read back",
                         spec.name, applog.size(len(data)))
    except DeviceNotResponding:
        raise
    except DeviceError as e:
        log.info("satellite data: not written (%s)", type(e).__name__)
        return _failed(unit_id, state, WRITE, apply)
    if written:
        state.written_at, state.error, state.current = time.time(), None, False
        save(unit_id, state)
        return Result("updated", size=written)
    if reason is not None:
        return _failed(unit_id, state, reason, apply)
    state.current, state.error = True, None
    save(unit_id, state)
    return Result("current")
