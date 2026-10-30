"""What Garmin's servers answered, kept for a while, so that the program does not ask them more
often than Garmin Express does: the same question is not asked again while its answer is
fresh, and two asking it at once share one request. One place for the whole process (the
window, the command line, the background service).

Each kind of answer has its lifetime (LIFETIMES); the key holds what the answer depends on:
the environment and the login for the account's data, the environment and the language for
Garmin's update server, and for the answers about a device its GarminDevice.xml (as Garmin
Express keys them). An answer is forgotten when what it says may have changed (forget: Refresh,
an install, a change of the primary device, the login…), and a request that must be fresh
asks anew (fresh=True: the ownership check before a write).

Some kinds are also kept on disk for the next command (DISK_FIELDS: only the fields the
program reads, never a token, never the raw unit-info, never unlock data), in the cache
(cache.CATEGORIES "answers"), each file readable by its owner only.

Has: ANSWERS, the one store of the process.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

ACCOUNT_DEVICES = "account-devices"
PRIMARY_TRAINING = "primary-training"
SOFTWARE_UPDATES = "software-updates"
UNIVERSAL_MAPS = "universal-maps"
UNIT_INFO = "unit-info"
ACCOUNT_COUNTRY = "account-country"
STORE_APPS = "store-apps"
STORE_UPDATES = "store-updates"
# The answers about the account: forgotten when the login changes (auth.store.save, delete).
ACCOUNT_KINDS = (ACCOUNT_DEVICES, PRIMARY_TRAINING, ACCOUNT_COUNTRY)

# How long an answer is fresh, in seconds. Garmin Express: the account's devices and the
# primary device only when signing in; software 1 day and maps 1 hour on disk, by the
# GarminDevice.xml; unit-info 7 days.
LIFETIMES = {
    ACCOUNT_DEVICES: 5 * 60,
    PRIMARY_TRAINING: 5 * 60,
    SOFTWARE_UPDATES: 60 * 60,
    UNIVERSAL_MAPS: 60 * 60,
    UNIT_INFO: 7 * 86400,
    ACCOUNT_COUNTRY: 60 * 60,
    # The Connect IQ store: the Apps page read again a moment later (Restore the order…);
    # an install, a removal from the account and a sync forget it.
    STORE_APPS: 2 * 60,
    STORE_UPDATES: 2 * 60,
}

# What unit-info's answer keeps, in memory and on disk: the fields the program reads (the map
# service, the picture, the kind, the model, whether Garmin Connect serves it); its unit ID and
# serial number never stay.
UNIT_INFO_FIELDS = frozenset({"devicemodel", "graphicurl", "segmenttype", "connectsupport",
                              "mapupdatetype"})


# Fields kept whole wherever they are: what the program sends back to the server as it came
# (a map's identifier, in its activation).
WHOLE = frozenset({"identifier"})


def _fields(value: Any, keep: frozenset[str]) -> Any:
    """`value` with only the keys of `keep` in its dicts, at any depth (those of WHOLE with
    all that is in them)."""
    if isinstance(value, dict):
        return {k: v if k in WHOLE else _fields(v, keep) for k, v in value.items()
                if k in keep}
    if isinstance(value, list):
        return [_fields(v, keep) for v in value]
    return value


# What a kind keeps of an answer, in memory and on disk.
KEEP: dict[str, Callable[[Any], Any]] = {
    UNIT_INFO: lambda answer: _fields(answer, UNIT_INFO_FIELDS),
}
# What goes to disk of the account's list: what the start page, the ownership check and the
# primary devices read; never a name the user gave a device ("displayname").
ACCOUNT_FIELDS = frozenset({
    "unitid", "deviceid", "productdisplayname", "partnumber", "currentfirmwareversion",
    "primaryactivitytrackerindicator", "primaryactivitytrackersettingcapable",
    "primarytrainingcapable", "imageurl", "devicecategories", "hasopticalheartrate"})
PRIMARY_FIELDS = frozenset({"deviceid"})
# What updates.parse_updates and beta_offered read of the software check.
SOFTWARE_FIELDS = frozenset({
    "updates", "accesslevel", "partnumber", "productname", "description", "displayversion",
    "semanticversion", "version", "severity", "isprimaryfirmware", "installation",
    "isreinstall", "isrestartrequired", "path", "filename", "order", "deliverable",
    "securedownloadurl", "downloadurl", "md5", "sizeinbytes", "eulaurl", "releasenotes",
    "changelog", "datatype", "releasestate"})
# What maps.read_maps reads of the map check; never unlock data ("gma": that is the answer of
# the activation, never kept).
MAP_FIELDS = frozenset({
    "maps", "directoriestoremove", "backgroundprimaryhost", "canuninstall", "country",
    "deliverableoptions", "description", "displayname", "downloadhosts", "eulaurls",
    "externalpath", "failoverhosts", "filematch", "filename", "files", "filestoremove",
    "foregroundprimaryhost", "geographicregion", "identifier", "installationstate",
    "installoptions", "ispreferred", "isreinstall", "majorversion", "maptype", "md5",
    "minorversion", "path", "productgroupname", "release", "restriction", "restrictiontype",
    "sizeinbytes", "title", "type", "url"})
# The kinds kept on disk too, with the fields of each that go there (at any depth); a kind
# not here stays in memory only (the account's country).
DISK_FIELDS: dict[str, frozenset[str]] = {
    ACCOUNT_DEVICES: ACCOUNT_FIELDS,
    PRIMARY_TRAINING: PRIMARY_FIELDS,
    SOFTWARE_UPDATES: SOFTWARE_FIELDS,
    UNIVERSAL_MAPS: MAP_FIELDS,
    UNIT_INFO: UNIT_INFO_FIELDS,
}
# The folder of the cache they are kept in (cache.CATEGORIES "answers").
FOLDER = "answers"


def xml_key(device_xml: bytes) -> str:
    """What an answer about a device is keyed by: its GarminDevice.xml, hashed."""
    return hashlib.sha256(device_xml).hexdigest()


def device_tags(device_xml: bytes) -> list:
    """What an answer about a device is about, for forget: its unit ID (the <Id> of its
    GarminDevice.xml) and the XML itself (xml_key)."""
    import re

    found = re.search(rb"<Id>\s*(\d+)\s*</Id>", device_xml)
    return ([int(found.group(1))] if found else []) + [xml_key(device_xml)]


def forget_device(kinds: Iterable[str] | str, unit_id) -> None:
    """Forget what ANSWERS keeps of these kinds about the device `unit_id` (none: nothing)."""
    if unit_id:
        ANSWERS.forget(kinds, about=int(unit_id))


def _tag(value) -> str:
    """A tag of an entry as it is kept (on disk too): hashed, so that no unit ID is in it."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


class Told:
    """Where the answer asked inside told() came from, for the log."""

    def __init__(self):
        self.source = "asked now"


_listening = threading.local()


@contextmanager
def told() -> Iterator[Told]:
    """Where the answer asked inside comes from (this thread's last): "asked now", "from
    memory, 85 s old", "from disk, …", "shared with a request under way"; for a line of the
    log that would else say the server was asked."""
    outer = getattr(_listening, "told", None)
    _listening.told = mine = Told()
    try:
        yield mine
    finally:
        _listening.told = outer


def _tell(source: str) -> None:
    listening = getattr(_listening, "told", None)
    if listening is not None:
        listening.source = source


_MISSING = object()  # nothing kept: an answer itself may be None (no country, an empty body)


class _Flight:
    """A request under way: the others asking the same wait for its answer."""

    def __init__(self):
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class Answers:
    """The answers of the process, by key; thread-safe. `clock`: the time, in seconds (the
    wall clock: an answer on disk is compared with another process's)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._kept: dict[tuple, tuple[float, Any, frozenset[str]]] = {}
        self._flying: dict[tuple, _Flight] = {}
        self.disk = None  # a Disk, for the kinds in it (set up by the program, not by tests)

    def ask(self, kind: str, key: Iterable, fetch: Callable[[], Any], *,
            about: Iterable = (), fresh: bool = False) -> Any:
        """The answer of `kind` for `key`: a fresh one kept, else `fetch()` (once, also when
        several ask at once). `about`: what it is about (a unit ID, xml_key), for forget.
        `fresh`: never from what is kept (still one request with those asking at once).
        Each caller gets a copy of its own."""
        full = (kind, *key)
        tags = frozenset(_tag(a) for a in about)
        while True:
            with self._lock:
                if not fresh:
                    hit = self._fresh(kind, full)
                    if hit is not _MISSING:
                        return copy.deepcopy(hit)
                flight = self._flying.get(full)
                mine = flight is None
                if mine:
                    flight = self._flying[full] = _Flight()
            if mine:
                break
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            _tell("shared with a request under way")
            return copy.deepcopy(flight.value)
        try:
            _tell("asked now")
            value = fetch()
            keep = KEEP.get(kind)
            value = keep(value) if keep is not None else value
        except BaseException as e:
            flight.error = e
            raise
        else:
            flight.value = value
            with self._lock:
                self._kept[full] = (self.clock(), value, tags)
            if self.disk is not None:
                self.disk.put(kind, full, value, tags, self.clock())
        finally:
            with self._lock:
                self._flying.pop(full, None)
            flight.done.set()
        return copy.deepcopy(value)

    def _fresh(self, kind: str, full: tuple):
        """A kept answer still fresh (in memory, else on disk), or _MISSING. Under the lock."""
        now = self.clock()
        kept = self._kept.get(full)
        if kept is not None and now - kept[0] < LIFETIMES[kind]:
            log.debug("answers: %s from memory, %.0f s old", kind, now - kept[0])
            _tell(f"from memory, {now - kept[0]:.0f} s old")
            return kept[1]
        if self.disk is not None:
            found = self.disk.get(kind, full, now)
            if found is not None:
                at, value, tags = found
                self._kept[full] = (at, value, tags)
                log.debug("answers: %s from disk, %.0f s old", kind, now - at)
                _tell(f"from disk, {now - at:.0f} s old")
                return value
        return _MISSING

    def forget(self, kinds: Iterable[str] | str | None = None, about=None) -> None:
        """Forget the answers of these kinds (all: None), only those about `about` (a unit ID,
        xml_key) when given; in memory and on disk."""
        if isinstance(kinds, str):
            kinds = (kinds,)
        kinds = set(kinds) if kinds is not None else None
        tag = _tag(about) if about is not None else None
        with self._lock:
            gone = [k for k, (_at, _v, tags) in self._kept.items()
                    if (kinds is None or k[0] in kinds) and (tag is None or tag in tags)]
            for k in gone:
                del self._kept[k]
        if self.disk is not None:
            self.disk.forget(kinds, tag)
        if gone:
            log.debug("answers: %d forgotten (%s)", len(gone),
                      ", ".join(sorted({k[0] for k in gone})))

    def clear(self) -> None:
        """Nothing kept in memory (the disk stays): a new process, as it were."""
        with self._lock:
            self._kept.clear()


class Disk:
    """The answers of DISK_FIELDS kept in a folder for the next process: a file each, readable
    by its owner only, named by a hash of the key; in it the kind, when it was answered, the
    hashed tags and the fields of the answer DISK_FIELDS names."""

    def __init__(self, folder, fields: dict[str, frozenset[str]] | None = None):
        from pathlib import Path

        self.folder = Path(folder)
        self.fields = DISK_FIELDS if fields is None else fields

    def _path(self, full: tuple):
        name = hashlib.sha256(json.dumps(full, default=str).encode()).hexdigest()[:32]
        return self.folder / f"{name}.json"

    def _own_folder(self) -> None:
        """The folder its owner's alone (0700), also one made before with the usual mode: the
        account's answers are among them."""
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.folder.stat().st_mode & 0o777 != 0o700:
            self.folder.chmod(0o700)

    def put(self, kind: str, full: tuple, value, tags: frozenset[str], at: float) -> None:
        if kind not in self.fields:
            return
        from openexpress import paths

        try:
            self._own_folder()
            paths.write_private(self._path(full), json.dumps(
                {"kind": kind, "at": at, "tags": sorted(tags),
                 "value": _fields(value, self.fields[kind])}))
        except (OSError, TypeError, ValueError) as e:
            log.info("answers: %s not kept on disk (%s)", kind, type(e).__name__)

    def get(self, kind: str, full: tuple, now: float):
        if kind not in self.fields:
            return None
        try:
            data = json.loads(self._path(full).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):  # a file changed by hand: none
            return None
        at = data.get("at")
        if data.get("kind") != kind or not isinstance(at, (int, float)) \
                or not 0 <= now - at < LIFETIMES[kind]:
            return None
        return at, data.get("value"), frozenset(data.get("tags") or ())

    def forget(self, kinds: set[str] | None, tag: str | None) -> None:
        if not self.folder.is_dir():
            return
        for p in self.folder.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            if (kinds is None or data.get("kind") in kinds) \
                    and (tag is None or tag in (data.get("tags") or ())):
                p.unlink(missing_ok=True)


ANSWERS = Answers()


def use_disk() -> None:
    """The kept answers on disk too, from now on (the command line, the window, the background
    service), in the cache: for the next command and the next start of the window."""
    from openexpress import paths

    ANSWERS.disk = Disk(paths.cache_dir() / FOLDER)
