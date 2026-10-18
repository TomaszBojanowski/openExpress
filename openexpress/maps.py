""""Universal" maps (TopoActive, base maps, CourseView, Ski) for watches.

As in Garmin Express: the server returns
the maps assigned to the device, the client downloads the files, activates the map
on the server (the response contains .gma unlock files) and writes everything to the watch.

As Express does, the files are written smallest first; a file other maps share is tried once
more after a failed write and never deleted; an unlock file is tried up to six times, with a
pause between. A map written or removed waits until the device is unplugged (mark_waiting):
only then does the device take it in.

There is no license circumvention here: only what the server offers for this
device is installed.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import posixpath
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openexpress import applog, cache, download, paths
from openexpress.connect import ApiError
from openexpress.device.storage import DeviceError, Storage
from openexpress.i18n import _, human_size

log = logging.getLogger(__name__)

SPACE_MARGIN = 50 * 1024 * 1024
WAIT_INSTALL, WAIT_REMOVE = "install", "remove"
# Writing an unlock file the device refused is tried again after a pause, as Garmin Express
# does: one try and five more.
UNLOCK_TRIES = 6
UNLOCK_PAUSE_S = 5


class MapError(Exception):
    pass


@dataclass
class MapFile:
    device_path: str
    url: str
    md5: str | None
    size: int


@dataclass
class UniversalMap:
    name: str
    map_type: str
    state: int
    is_reinstall: bool
    can_uninstall: bool
    version: str
    region: str
    identifier: dict
    files: list[MapFile]
    files_to_remove: list[str]
    hosts: list[str]
    eula_urls: list[str] = field(default_factory=list)
    # False when the server did not provide a usable download for every file;
    # `unavailable` then says why: MISSING_FILE or BAD_CHECKSUM.
    installable: bool = True
    unavailable: str | None = None
    # WAIT_INSTALL or WAIT_REMOVE: written to or removed from the device, which takes it in
    # only when it is unplugged (see mark_waiting); None otherwise.
    waiting: str | None = None

    @property
    def installed(self) -> bool:
        return self.state in (1, 2, 3)

    @property
    def update_available(self) -> bool:
        return self.installed and not self.is_reinstall and self.waiting is None

    @property
    def occupies(self) -> bool:
        """Its files are on the device for good: installed and not waiting to be removed,
        or written and waiting to be taken in. Only shown ("Maps use"); which files are
        kept when a map is removed is decided by protected_files."""
        return (self.installed and self.waiting != WAIT_REMOVE) or self.waiting == WAIT_INSTALL

    @property
    def keeps_files(self) -> bool:
        """Its files are kept when another map is removed or rewritten: installed as far as
        the server knows, or written and waiting to be taken in. A map that looks removed
        still counts: a shared file is kept rather than lost."""
        return self.installed or self.waiting == WAIT_INSTALL

    @property
    def all_paths(self) -> list[str]:
        """Every file the server names for the map, as it spells them: those of the map and
        those a newer version of it removes."""
        return [*(f.device_path for f in self.files), *self.files_to_remove]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def status(self) -> str:
        if self.waiting == WAIT_INSTALL:
            # TRANSLATORS: map status
            return _("will be loaded after the device is unplugged")
        if self.waiting == WAIT_REMOVE:
            return _("will be removed after the device is unplugged")
        if self.update_available:
            # TRANSLATORS: map status
            return _("update available")
        if self.installed:
            # TRANSLATORS: map status
            return _("up to date")
        # TRANSLATORS: map status (can be installed)
        return _("available")


def _device_path(name: str) -> str:
    """Turn a path from the server into a device path ("\\" becomes "/")."""
    return name.replace("\\", "/")


def _hosts(dh: dict | None) -> list[str]:
    dh = dh or {}
    hosts = [dh.get("foregroundprimaryhost"), *(dh.get("failoverhosts") or []),
             dh.get("backgroundprimaryhost")]
    out: list[str] = []
    for h in hosts:
        if h and h not in out:
            out.append(h if h.endswith("/") else h + "/")
    # HTTPS first (MD5 checks integrity anyway, but there is no reason to use HTTP).
    return sorted(out, key=lambda h: not h.startswith("https://"))


def no_maps_message() -> str:
    return _("No maps are available for this device.")


def _listed(response) -> list:
    """The maps as the server lists them. An answer without a list of maps (an empty body,
    another object) is an error, so that a failure is never taken for "no maps"."""
    listed = response.get("maps") if isinstance(response, dict) else None
    if not isinstance(listed, list):
        raise ApiError(_("{what}: unexpected response from the server").format(
            what=_("Map check")))
    return listed


def read_maps(response: dict) -> tuple[list[UniversalMap], int]:
    """The maps in the server's answer, and how many maps the answer listed: a map without
    anything to install is listed, but not read. Zero means the server has no maps for the
    device."""
    return parse_maps(response), len(_listed(response))


# Why a map cannot be installed: a file without a usable download, or one whose MD5 the
# program cannot read (never downloaded without its check).
MISSING_FILE, BAD_CHECKSUM = "missing-file", "bad-checksum"


def cannot_install_message(m: UniversalMap) -> str:
    """Why `m` cannot be installed, the same words in the command line and the window."""
    if m.unavailable == BAD_CHECKSUM:
        return _("Map '{name}' cannot be installed: the server gave an invalid checksum for "
                 "one of its files.").format(name=m.name)
    return _("Map '{name}' cannot be installed: the server did not provide "
             "all of its files").format(name=m.name)


def parse_maps(response: dict) -> list[UniversalMap]:
    out = []
    for m in _listed(response):
        options = m.get("installoptions") or []
        opt = next((o for o in options if o.get("ispreferred")), options[0] if options else None)
        if opt is None:
            continue
        files = []
        unchecked = 0  # files without an MD5 the program can read
        listed = opt.get("files") or []
        for f in listed:
            deliv = next(
                (d for d in f.get("deliverableoptions") or [] if d.get("type") in (1, None)),
                None,
            )
            if not deliv or not f.get("filename") or not (deliv.get("url") or ""):
                continue
            md5 = download.normalize_md5(deliv.get("md5"))
            if md5 is None:
                # Never downloaded without its check: the map cannot be installed. The file
                # stays listed: its name is still Garmin's (own maps, removals).
                unchecked += 1
            files.append(MapFile(
                device_path=_device_path(f["filename"]),
                url=deliv.get("url") or "",
                md5=md5,
                size=int(deliv.get("sizeinbytes") or 0),
            ))
        if unchecked:
            log.info("maps: the server gave no valid MD5 for %d file(s) of %s; it cannot be "
                     "installed", unchecked, m.get("displayname") or "a map")
        rel = m.get("release") or {}
        region = (m.get("geographicregion") or {}).get("title") or ""
        missing = not files or len(files) != len(listed)
        out.append(UniversalMap(
            name=m.get("displayname") or rel.get("productgroupname") or "?",
            map_type=m.get("maptype") or "",
            state=int(m.get("installationstate") or 0),
            is_reinstall=bool(m.get("isreinstall")),
            can_uninstall=bool(m.get("canuninstall")),
            version=f"{rel.get('majorversion', '?')}.{rel.get('minorversion', '?')}",
            region=region,
            identifier=opt.get("identifier") or {},
            files=files,
            files_to_remove=[_device_path(f.get("filename", ""))
                             for f in m.get("filestoremove") or [] if f.get("filename")],
            hosts=_hosts(m.get("downloadhosts")),
            eula_urls=list(m.get("eulaurls") or []),
            installable=not missing and not unchecked,
            unavailable=MISSING_FILE if missing else BAD_CHECKSUM if unchecked else None,
        ))
    return out


def find(maps: list[UniversalMap], name: str) -> UniversalMap:
    exact = [m for m in maps if m.name.casefold() == name.casefold()]
    if exact:
        return exact[0]
    partial = [m for m in maps if name.casefold() in m.name.casefold()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise MapError(_("No map named '{name}'").format(name=name))
    raise MapError(_("Ambiguous name, matches: {names}")
                   .format(names="; ".join(m.name for m in partial)))


def mark_waiting(storage: Storage, maps: list[UniversalMap]) -> None:
    """Marks the maps written to or removed from the device that the device has not taken in
    yet: it does that when it is unplugged, and only then do GarminDevice.xml and so Garmin's
    server know. Facts, no memory: the files on the device against the server's list.

    Written: the server does not have the map (or has an older version), yet every file of
    the map is on the device, at the size the server gives. Removed: the server has the map,
    yet none of its own files (not used by other maps) is on the device."""
    for m in maps:
        m.waiting = None
    wanted = {p for m in maps for p in m.all_paths}
    present: dict[str, int] = {}  # casefolded path -> size, one listing per folder
    try:
        for folder in {posixpath.dirname(p) for p in wanted}:
            for e in storage.listdir(folder):
                if not e.is_dir:
                    present[posixpath.join(folder, e.name).casefold()] = e.size
    except Exception as e:  # noqa: BLE001 - only shown: the map check goes on without it
        log.warning("maps: cannot read the map files on the device, none is marked as "
                    "waiting for unplugging: %s", e)
        return
    for m in maps:
        on_device = [present.get(f.device_path.casefold()) == f.size for f in m.files]
        # An update with the very names of the files installed now cannot be told apart.
        new_names = {f.device_path.casefold() for f in m.files} - {
            p.casefold() for p in m.files_to_remove}
        if (m.files and all(on_device)
                and (not m.installed or (not m.is_reinstall and new_names))):
            m.waiting = WAIT_INSTALL
    removed = []  # decided on the same state for every map, then marked
    for m in maps:
        if not m.installed or m.waiting:
            continue
        own = {p.casefold() for p in m.all_paths}
        own -= protected_files(maps, m)
        if own and not any(p in present for p in own):
            removed.append(m)
    for m in removed:
        m.waiting = WAIT_REMOVE


def waiting_message(m: UniversalMap) -> str:
    return _("Map '{name}' is on the device already and will be loaded after the device is "
             "unplugged. Unplug the device first.").format(name=m.name)


def protected_files(maps: list[UniversalMap], target: UniversalMap) -> set[str]:
    """Files used by other installed maps (e.g. a shared terrain model), or by maps written
    and waiting for the device to take them in."""
    keep: set[str] = set()
    for m in maps:
        if m is target or not m.keeps_files:
            continue
        keep.update(p.casefold() for p in m.all_paths)
    return keep


def removal_list(maps: list[UniversalMap], target: UniversalMap, rewriting: bool) -> list[str]:
    keep = protected_files(maps, target)
    new = {f.device_path.casefold() for f in target.files} if rewriting else set()
    return [p for p in target.files_to_remove if p.casefold() not in keep | new]


def space_needed(storage: Storage, m: UniversalMap, removing: list[str]) -> int:
    freed = sum(storage.size(p) or 0 for p in removing)
    gone = {p.casefold() for p in removing}
    # A file being replaced frees its space before the new one is written over MTP only; on
    # a drive it stays until the new one is whole.
    replaced = 0 if storage.replaces_atomically else sum(
        storage.size(f.device_path) or 0 for f in m.files
        if f.device_path.casefold() not in gone)
    return max(0, m.size - freed - replaced)


def write_order(m: UniversalMap) -> list[MapFile]:
    """The files of a map in the order install downloads and writes them, and the plan lists
    them: smallest first, as Garmin Express does, so the small type and search files are on
    the device before the big map and terrain files. Files of the same size keep the order
    of the server (the sort is stable)."""
    return sorted(m.files, key=lambda f: f.size)


def cache_file(f: MapFile) -> Path:
    return paths.cache_dir() / "maps" / (f.md5 or posixpath.basename(f.device_path))


@dataclass
class MapReport:
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unlocks: list[str] = field(default_factory=list)


def _install(
    m: UniversalMap,
    all_maps: list[UniversalMap],
    storage: Storage,
    activate: Callable[[dict], dict],
    session=None,
    progress: Callable[[str], None] = lambda s: None,
    transfer_progress: Callable[[int, int], None] | None = None,
    download_progress: Callable[[int, int | None], None] | None = None,
    url_salt: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> MapReport:
    if m.waiting == WAIT_INSTALL:
        raise MapError(waiting_message(m))
    if not m.installable or not m.files:
        raise MapError(cannot_install_message(m))
    removing = removal_list(all_maps, m, rewriting=True)
    need = space_needed(storage, m, removing)
    free = storage.free_space()
    if free is not None and need + SPACE_MARGIN > free:
        raise MapError(_("Not enough space on the device: {need} needed, {free} free")
                       .format(need=human_size(need), free=human_size(free)))

    files = write_order(m)
    local: dict[str, Path] = {}
    # One bar for all the files of the map.
    overall = download.Overall(m.size, download_progress) if download_progress else None
    for f in files:
        progress(_("Downloading {path} ({size})")
                 .format(path=f.device_path, size=human_size(f.size)))
        urls = [h + f.url.lstrip("/") for h in m.hosts] if not f.url.startswith("http") else [f.url]
        if url_salt:
            urls = [download.tokenize(u, url_salt) for u in urls]
        local[f.device_path] = download.fetch(urls, cache_file(f), f.size, f.md5, session=session,
                                              progress=overall.file() if overall else None)
        if overall:
            overall.finished(f.size)

    progress(_("Activating the map on the Garmin server"))
    act = activate(m.identifier)
    entries = act.get("unlocks") or []
    unlocks = [(u.get("filename"), u.get("gma")) for u in entries
               if u.get("filename") and u.get("gma")]
    # Only logged, before anything is written: Garmin Express writes whatever the list holds
    # and says nothing, and some maps (the base maps, as far as the server's file lists show)
    # have no unlock file at all, so a missing one is no sure sign of trouble.
    if not entries:
        log.warning("map install: the activation of %s %s returned no unlock file (%s)",
                    m.name, m.version,
                    "an empty list" if act.get("unlocks") == [] else "no unlocks in the answer")
    elif len(unlocks) < len(entries):
        log.warning("map install: %d of %d unlock entries from the activation of %s %s had no "
                    "file name or data", len(entries) - len(unlocks), len(entries), m.name,
                    m.version)

    report = MapReport()
    for p in removing:
        progress(_("Removing old file {path}").format(path=p))
        storage.delete(p)
        report.removed.append(p)
    shared = protected_files(all_maps, m)
    for f in files:
        progress(_("Copying to the device: {path}").format(path=f.device_path))
        if f.device_path.casefold() not in shared:
            real = _write_checked(storage, f, local[f.device_path], transfer_progress)
        else:
            real = _write_shared(storage, f, local[f.device_path], transfer_progress,
                                 _users(all_maps, m, f.device_path))
        report.written.append(real)
    gmas = []
    for fname, gma in unlocks:
        data = base64.b64decode(gma)
        real = _write_unlock(storage, _device_path(fname), data, progress, sleep)
        report.unlocks.append(real)
        gmas.append(_short_md5(data))
    log.info("map install: %s %s, %d files written (%s), %d old files removed, %d unlock files"
             " (md5 %s)", m.name, m.version, len(report.written), applog.size(m.size),
             len(report.removed), len(report.unlocks), ", ".join(gmas) or "-")
    return report


def install(*args, **kwargs) -> MapReport:
    """Install a map (see _install), holding the cache from the download to the copy to the
    device: it is not cleared meanwhile."""
    with cache.using():
        return _install(*args, **kwargs)


def _write_checked(storage: Storage, f: MapFile, local: Path, transfer_progress,
                   keep: bool = False) -> str:
    """Writes one file and checks its size. What a failed write leaves is cleaned up by the
    storage itself, unless `keep` (a file other maps use: never deleted); a file of the
    wrong size is deleted here, unless `keep`."""
    real = storage.write_file(f.device_path, local, transfer_progress, keep_partial=keep)
    ok = storage.size(real) == f.size
    if not ok:
        if not keep:
            _delete_quietly(storage, real)
        raise MapError(_("Write verification of {path} failed").format(path=real))
    return real


def _write_shared(storage: Storage, f: MapFile, local: Path, transfer_progress,
                  users: list[str]) -> str:
    """A file other maps use too, installed or written and waiting to be taken in: deleting
    it after a failed write would break them, so the write is tried once more and the file
    is never deleted."""
    try:
        return _write_checked(storage, f, local, transfer_progress, keep=True)
    except Exception as e:  # noqa: BLE001 - any failure gets the one retry
        log.warning("map install: writing a file other maps use failed, trying once more: %s",
                    e)
    try:
        return _write_checked(storage, f, local, transfer_progress, keep=True)
    except Exception as e:
        raise MapError(_("Cannot write {path}, which other maps on the device also use "
                         "({maps}). Install the map again.")
                       .format(path=f.device_path, maps=", ".join(users))) from e


def _write_unlock(storage: Storage, path: str, data: bytes, progress, sleep) -> str:
    """Writes an unlock file, tried again after a pause while the device refuses it. The map
    files are on the device by then and the map may not work without it: the error says so
    and what to do."""
    attempt = 1
    while True:
        try:
            return storage.write(path, data)
        except DeviceError as e:
            log.warning("map install: writing an unlock file failed (try %d of %d): %s",
                        attempt, UNLOCK_TRIES, e)
            if attempt == UNLOCK_TRIES:
                raise MapError(_(
                    "The map files were copied, but its unlock file {path} could not be "
                    "written ({error}). The map may not work on the device. If it does not, "
                    "unplug the device and install the map again; if it is listed as "
                    "installed, remove it first.").format(path=path, error=e)) from e
        attempt += 1
        progress(_("Writing the unlock file again ({attempt} of {tries})")
                 .format(attempt=attempt, tries=UNLOCK_TRIES))
        sleep(UNLOCK_PAUSE_S)


def _users(maps: list[UniversalMap], target: UniversalMap, path: str) -> list[str]:
    """Names of the other maps, installed or written and waiting to be taken in, that list
    `path` among their files."""
    p = path.casefold()
    return [m.name for m in maps if m is not target and m.keeps_files
            and p in {x.casefold() for x in m.all_paths}]


def _short_md5(data: bytes) -> str:
    """Start of the MD5 of an unlock file, enough to tell two apart, as 1234_abcd: the log
    filter keeps it (no long hex or digit runs), and it does not identify the device."""
    h = hashlib.md5(data).hexdigest()[:8]
    return f"{h[:4]}_{h[4:]}"


def cannot_remove_message(m: UniversalMap) -> str:
    return _("Map '{name}' cannot be removed").format(name=m.name)


def uninstall(m: UniversalMap, all_maps: list[UniversalMap], storage: Storage,
              progress: Callable[[str], None] = lambda s: None) -> list[str]:
    if not m.can_uninstall:
        raise MapError(cannot_remove_message(m))
    removed = []
    for p in removal_list(all_maps, m, rewriting=False):
        if storage.exists(p):
            progress(_("Removing {path}").format(path=p))
            storage.delete(p)
            removed.append(p)
    log.info("map removal: %s, %d files removed", m.name, len(removed))
    return removed


def _delete_quietly(storage: Storage, path: str) -> None:
    try:
        storage.delete(path)
    except Exception as e:  # noqa: BLE001
        log.warning("Cannot delete %s: %s", path, e)
