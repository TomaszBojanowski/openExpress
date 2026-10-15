""""Universal" maps (TopoActive, base maps, CourseView, Ski) for watches.

Official Garmin Express flow (docs/analiza/04, section 6): the server returns
the maps assigned to the device, the client downloads the files, activates the map
on the server (the response contains .gma unlock files) and writes everything to the watch.
There is no license circumvention here: only what the server offers for this
device is installed.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openexpress import applog, download, paths
from openexpress.connect import ApiError
from openexpress.device.storage import Storage
from openexpress.i18n import N_, _, human_size

log = logging.getLogger(__name__)

# TRANSLATORS: map installation states
STATE_NAMES = {0: N_("not installed"), 1: N_("installed"), 2: N_("partial"),
               3: N_("newer on device")}
SPACE_MARGIN = 50 * 1024 * 1024
WAIT_INSTALL, WAIT_REMOVE = "install", "remove"


class MapError(Exception):
    pass


@dataclass
class MapFile:
    device_path: str
    datatype: str
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
    # False when the server did not provide a usable download for every file.
    installable: bool = True
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
        or written and waiting to be taken in."""
        return (self.installed and self.waiting != WAIT_REMOVE) or self.waiting == WAIT_INSTALL

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


def parse_maps(response: dict) -> list[UniversalMap]:
    out = []
    for m in _listed(response):
        options = m.get("installoptions") or []
        opt = next((o for o in options if o.get("ispreferred")), options[0] if options else None)
        if opt is None:
            continue
        files = []
        listed = opt.get("files") or []
        for f in listed:
            deliv = next(
                (d for d in f.get("deliverableoptions") or [] if d.get("type") in (1, None)),
                None,
            )
            if not deliv or not f.get("filename") or not (deliv.get("url") or ""):
                continue
            files.append(MapFile(
                device_path=f["filename"].replace("\\", "/"),
                datatype=f.get("datatype") or "",
                url=deliv.get("url") or "",
                md5=download.normalize_md5(deliv.get("md5")),
                size=int(deliv.get("sizeinbytes") or 0),
            ))
        rel = m.get("release") or {}
        region = (m.get("geographicregion") or {}).get("title") or ""
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
            files_to_remove=[f.get("filename", "").replace("\\", "/")
                             for f in m.get("filestoremove") or [] if f.get("filename")],
            hosts=_hosts(m.get("downloadhosts")),
            eula_urls=list(m.get("eulaurls") or []),
            installable=bool(files) and len(files) == len(listed),
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
    paths = {p for m in maps for p in [*(f.device_path for f in m.files), *m.files_to_remove]}
    present: dict[str, int] = {}  # casefolded path -> size, one listing per folder
    try:
        for folder in {posixpath.dirname(p) for p in paths}:
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
        own = {p.casefold() for p in [*(f.device_path for f in m.files), *m.files_to_remove]}
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
        # A map that looks removed still counts: a shared file is kept rather than lost.
        if m is target or not (m.installed or m.waiting == WAIT_INSTALL):
            continue
        keep.update(f.device_path.casefold() for f in m.files)
        keep.update(p.casefold() for p in m.files_to_remove)
    return keep


def removal_list(maps: list[UniversalMap], target: UniversalMap, rewriting: bool) -> list[str]:
    keep = protected_files(maps, target)
    new = {f.device_path.casefold() for f in target.files} if rewriting else set()
    return [p for p in target.files_to_remove if p.casefold() not in keep | new]


def space_needed(storage: Storage, m: UniversalMap, removing: list[str]) -> int:
    freed = sum(storage.size(p) or 0 for p in removing)
    replaced = sum(storage.size(f.device_path) or 0 for f in m.files
                   if f.device_path not in removing)
    return max(0, m.size - freed - replaced)


def cache_file(f: MapFile) -> Path:
    return paths.cache_dir() / "maps" / (f.md5 or posixpath.basename(f.device_path))


@dataclass
class MapReport:
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unlocks: list[str] = field(default_factory=list)


def install(
    m: UniversalMap,
    all_maps: list[UniversalMap],
    storage: Storage,
    activate: Callable[[dict], dict],
    session=None,
    progress: Callable[[str], None] = lambda s: None,
    transfer_progress: Callable[[int, int], None] | None = None,
    download_progress: Callable[[int, int | None], None] | None = None,
    url_salt: str | None = None,
) -> MapReport:
    if m.waiting == WAIT_INSTALL:
        raise MapError(waiting_message(m))
    if not m.installable or not m.files:
        raise MapError(_("Map '{name}' cannot be installed: the server did not provide "
                         "all of its files").format(name=m.name))
    removing = removal_list(all_maps, m, rewriting=True)
    need = space_needed(storage, m, removing)
    free = storage.free_space()
    if free is not None and need + SPACE_MARGIN > free:
        raise MapError(_("Not enough space on the device: {need} needed, {free} free")
                       .format(need=human_size(need), free=human_size(free)))

    # Smallest file first, as Garmin Express does: the small type and search files are on
    # the device before the big map and terrain files.
    files = sorted(m.files, key=lambda f: f.size)
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
    unlocks = [(u.get("filename"), u.get("gma")) for u in act.get("unlocks") or []
               if u.get("filename") and u.get("gma")]

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
        real = storage.write(fname.replace("\\", "/"), data)
        report.unlocks.append(real)
        gmas.append(_short_md5(data))
    log.info("map install: %s %s, %d files written (%s), %d old files removed, %d unlock files"
             " (md5 %s)", m.name, m.version, len(report.written), applog.size(m.size),
             len(report.removed), len(report.unlocks), ", ".join(gmas) or "-")
    return report


def _write_checked(storage: Storage, f: MapFile, local: Path, transfer_progress,
                   keep: bool = False) -> str:
    """Writes one file and checks its size. A file that failed is deleted, unless `keep`."""
    try:
        real = storage.write_file(f.device_path, local, transfer_progress)
        ok = storage.size(real) == f.size
    except Exception:
        if not keep:
            _delete_quietly(storage, f.device_path)
        raise
    if not ok:
        if not keep:
            _delete_quietly(storage, real)
        raise MapError(_("Write verification of {path} failed").format(path=real))
    return real


def _write_shared(storage: Storage, f: MapFile, local: Path, transfer_progress,
                  users: list[str]) -> str:
    """A file other installed maps use too: deleting it after a failed write would break
    them, so the write is tried once more and the file is never deleted."""
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


def _users(maps: list[UniversalMap], target: UniversalMap, path: str) -> list[str]:
    """Names of the other installed maps that list `path` among their files."""
    p = path.casefold()
    return [m.name for m in maps if m is not target
            and (m.installed or m.waiting == WAIT_INSTALL)
            and p in {x.casefold() for x in [*(f.device_path for f in m.files),
                                             *m.files_to_remove]}]


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
