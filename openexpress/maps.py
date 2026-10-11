""""Universal" maps (TopoActive, base maps, CourseView, Ski) for watches.

Official Garmin Express flow (docs/analiza/04, section 6): the server returns
the maps assigned to the device, the client downloads the files, activates the map
on the server (the response contains .gma unlock files) and writes everything to the watch.
There is no license circumvention here: only what the server offers for this
device is installed.
"""

from __future__ import annotations

import base64
import logging
import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from openexpress import download, paths
from openexpress.device.storage import Storage
from openexpress.i18n import N_, _

log = logging.getLogger(__name__)

# TRANSLATORS: map installation states
STATE_NAMES = {0: N_("not installed"), 1: N_("installed"), 2: N_("partial"),
               3: N_("newer on watch")}
SPACE_MARGIN = 50 * 1024 * 1024


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

    @property
    def installed(self) -> bool:
        return self.state in (1, 2, 3)

    @property
    def update_available(self) -> bool:
        return self.installed and not self.is_reinstall

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def status(self) -> str:
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


def parse_maps(response: dict) -> list[UniversalMap]:
    out = []
    for m in response.get("maps") or []:
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


def protected_files(maps: list[UniversalMap], target: UniversalMap) -> set[str]:
    """Files used by other installed maps (e.g. a shared terrain model)."""
    keep: set[str] = set()
    for m in maps:
        if m is target or not m.installed:
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
    url_salt: str | None = None,
) -> MapReport:
    if not m.installable or not m.files:
        raise MapError(_("Map '{name}' cannot be installed: the server did not provide "
                         "all of its files").format(name=m.name))
    removing = removal_list(all_maps, m, rewriting=True)
    need = space_needed(storage, m, removing)
    free = storage.free_space()
    if free is not None and need + SPACE_MARGIN > free:
        raise MapError(_("Not enough space on the watch: {need} B needed, {free} B free")
                       .format(need=need, free=free))

    local: dict[str, Path] = {}
    for f in m.files:
        progress(_("Downloading {path} ({size} MB)")
                 .format(path=f.device_path, size=f"{f.size / 1e6:.0f}"))
        urls = [h + f.url.lstrip("/") for h in m.hosts] if not f.url.startswith("http") else [f.url]
        if url_salt:
            urls = [download.tokenize(u, url_salt) for u in urls]
        local[f.device_path] = download.fetch(urls, cache_file(f), f.size, f.md5, session=session)

    progress(_("Activating the map on the Garmin server"))
    act = activate(m.identifier)
    unlocks = [(u.get("filename"), u.get("gma")) for u in act.get("unlocks") or []
               if u.get("filename") and u.get("gma")]

    report = MapReport()
    for p in removing:
        progress(_("Removing old file {path}").format(path=p))
        storage.delete(p)
        report.removed.append(p)
    for f in m.files:
        progress(_("Copying to the watch: {path}").format(path=f.device_path))
        try:
            real = storage.write_file(f.device_path, local[f.device_path], transfer_progress)
            ok = storage.size(real) == f.size
        except Exception:
            _delete_quietly(storage, f.device_path)
            raise
        if not ok:
            _delete_quietly(storage, real)
            raise MapError(_("Write verification of {path} failed").format(path=real))
        report.written.append(real)
    for fname, gma in unlocks:
        data = base64.b64decode(gma)
        real = storage.write(fname.replace("\\", "/"), data)
        report.unlocks.append(real)
    return report


def uninstall(m: UniversalMap, all_maps: list[UniversalMap], storage: Storage,
              progress: Callable[[str], None] = lambda s: None) -> list[str]:
    if not m.can_uninstall:
        raise MapError(_("Map '{name}' cannot be removed").format(name=m.name))
    removed = []
    for p in removal_list(all_maps, m, rewriting=False):
        if storage.exists(p):
            progress(_("Removing {path}").format(path=p))
            storage.delete(p)
            removed.append(p)
    return removed


def _delete_quietly(storage: Storage, path: str) -> None:
    try:
        storage.delete(path)
    except Exception as e:  # noqa: BLE001
        log.warning("Cannot delete %s: %s", path, e)
