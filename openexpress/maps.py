""""Universal" maps (TopoActive, base maps, CourseView, Ski) for watches.

As in Garmin Express: the server returns the maps assigned to the device, the client
downloads the files, activates the map on the server (the response contains .gma unlock
files) and writes everything to the watch (`write_order`). A file other maps use is never
deleted. A map written or removed waits until the device is unplugged (`mark_waiting`): only
then does the device take it in.

There is no license circumvention here: only what the server offers for this device is
installed. A map Garmin does not offer in the country of the Garmin account (its
"restriction") is not installed or updated; when that country is not known, it is only marked.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import posixpath
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from openexpress import applog, cache, download, paths
from openexpress.connect import ApiError
from openexpress.device.storage import DeviceError, Storage
from openexpress.errors import UserFacingError
from openexpress.i18n import _, country_name, human_size

log = logging.getLogger(__name__)

SPACE_MARGIN = 50 * 1024 * 1024
WAIT_INSTALL, WAIT_REMOVE = "install", "remove"
# Writing an unlock file the device refused is tried again after a pause, as Garmin Express
# does: one try and five more.
UNLOCK_TRIES = 6
UNLOCK_PAUSE_S = 5


class MapError(UserFacingError):
    pass


@dataclass(frozen=True)
class CleanupRule:
    """A rule of the map service for old files ("directoriesToRemove"): a folder of the
    device, one on its memory card, a pattern of file names and what it is for."""

    path: str | None
    external_path: str | None
    file_match: str | None
    description: str | None


@dataclass(frozen=True)
class CleanupFile:
    """A file a cleanup rule would take (cleanup_plan)."""

    path: str
    size: int


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
    # The country Garmin does not offer the map in ("restriction", type "Country"; upper
    # case), and whether that is the account's country (apply_country): True, False (no
    # restriction, or another country), None (restricted, the account's country not known).
    restricted_in: str | None = None
    restricted: bool | None = False
    # The cleanup rules of the answer, for every map as Garmin Express keeps them (only
    # shown: cleanup_plan).
    cleanup_rules: tuple[CleanupRule, ...] = ()

    # The states of the server ("installationState"): 0 not installed, 1 installed, 2 partly
    # on the device, 3 a newer version on the device.

    @property
    def listed(self) -> bool:
        """The server sees the map, or a part of it, on the device."""
        return self.state in (1, 2, 3)

    @property
    def installed(self) -> bool:
        """Installed as far as the server knows: a map partly on the device is not."""
        return self.state in (1, 3)

    @property
    def incomplete(self) -> bool:
        """Partly on the device: shown there, to be installed again (never an update)."""
        return self.state == 2 and self.waiting is None

    @property
    def would_update(self) -> bool:
        """The server has a version of an installed map (not one partly there) that the device
        does not have; never a newer version on the device, which an update would replace
        with the older one."""
        return self.state == 1 and not self.is_reinstall and self.waiting is None

    @property
    def update_available(self) -> bool:
        """An update to offer: not of a map Garmin does not offer in the account's country."""
        return self.would_update and self.restricted is not True

    @property
    def occupies(self) -> bool:
        """Its files are on the device for good: listed and not waiting to be removed, or
        written and waiting to be taken in. Only shown ("Maps use"); which files are kept
        when a map is removed is decided by protected_files."""
        return (self.listed and self.waiting != WAIT_REMOVE) or self.waiting == WAIT_INSTALL

    @property
    def keeps_files(self) -> bool:
        """Its files are kept when another map is removed or rewritten: listed as far as the
        server knows (also partly), or written and waiting to be taken in. A map that looks
        removed still counts: a shared file is kept rather than lost."""
        return self.listed or self.waiting == WAIT_INSTALL

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
        if self.incomplete:
            # TRANSLATORS: map status: a part of the map is on the device; it is to be
            # installed again
            return _("incomplete on the device")
        if self.update_available:
            # TRANSLATORS: map status
            return _("update available")
        if self.would_update:  # held back: not offered in the account's country
            # TRANSLATORS: map status
            return _("update not offered in your account's country")
        if self.installed:
            # TRANSLATORS: map status
            return _("up to date")
        if self.restricted is True:
            # TRANSLATORS: map status (Garmin does not offer it in the account's country)
            return _("not offered in your account's country")
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


# The data type a device declares when its maps come from the universal map service although
# Garmin files it as "UniversalDetect" (the fēnix 8 and the Edge 1050 do).
UNIVERSAL_DETECT_DATATYPE = "PreProgrammedNamedMaps"


class MapsNotSupported(MapError):
    """Garmin serves the maps of the device another way than the universal map service, the
    only one this program asks: no map check. `map_update_type`: the word unit-info gives
    (e.g. "Gemini"), for the Maps page to name it; None when it gives none, or no word."""

    def __init__(self, map_update_type: str | None = None) -> None:
        super().__init__(_("Map updates for this device aren't supported yet."))
        self.map_update_type = map_update_type


def not_supported_text() -> str:
    # TRANSLATORS: subtitle of the row "Maps" and title of the Maps page, without a full stop
    return _("Map updates for this device aren't supported yet")


def universal_serves(map_update_type, device) -> bool:
    """Whether the universal map service serves the device, from what unit-info says of it
    (mapUpdateType): Universal, or UniversalDetect while the device declares the data type
    PreProgrammedNamedMaps. Anything else (Gemini, LegacyRmu, Unknown, none given) is served
    another way."""
    said = map_update_type.casefold() if isinstance(map_update_type, str) else None
    if said == "universal":
        return True
    if said != "universaldetect":
        return False
    wanted = UNIVERSAL_DETECT_DATATYPE.casefold()
    return any(name.casefold() == wanted for name in device.datatypes())


def check(client, device, ask=None, country=None) -> tuple[list[UniversalMap], int]:
    """The maps of the device and the count of listed maps (read_maps), restrictions applied;
    MapsNotSupported when unit-info says the universal map service does not serve it. `ask`
    gives unit-info's answer (the window: service.unit_info); none: it is asked here.
    `country` gives the account's country, asked only when a map is restricted to one; none:
    such a map is only marked."""
    # A failed unit-info fails the check: without it, which service serves the device is not
    # known.
    if ask is not None:
        info = ask()
    else:
        info = client.unit_info(device.unit_id, device.part_number) if device.unit_id else {}
    value = info.get("mapupdatetype") if isinstance(info, dict) else None
    if value is None or value == "":
        said = "none given"
    else:  # a word as Garmin gives it; an answer of another shape by its type, not quoted
        said = value[:40] if isinstance(value, str) else type(value).__name__
    if not universal_serves(value, device):
        log.info("maps: map update type %s for %s: map updates not supported, no map check",
                 said, device.part_number)
        raise MapsNotSupported(value[:40] if isinstance(value, str) and value else None)
    log.info("maps: map update type %s for %s: asking the universal map service", said,
             device.part_number)
    found = read_maps(client.universal_maps(device.raw))
    apply_restrictions(found[0], country)
    return found


def apply_country(maps: list[UniversalMap], country: str | None) -> None:
    """Whether each map with a restriction is not offered in `country` (the account's; None:
    not known, the map is only marked)."""
    for m in maps:
        if m.restricted_in is not None:
            m.restricted = None if not country else country.upper() == m.restricted_in


def apply_restrictions(maps: list[UniversalMap], country=None) -> None:
    """The account's country asked (`country()`, None: it cannot be) only when a map has a
    restriction, and applied; one line in the log, without the country."""
    restricted = [m for m in maps if m.restricted_in is not None]
    if not restricted:
        log.info("maps: 0 maps restricted by country")
        return
    # Garmin Express takes the country from the address of the computer; here it is the
    # account's profile.
    code = country() if country is not None else None
    apply_country(maps, code)
    log.info("maps: %d maps restricted by country; the account's country: %s; %d apply",
             len(restricted), "known" if code else "unknown",
             sum(1 for m in restricted if m.restricted is True))


def restriction_note(m: UniversalMap) -> str | None:
    """The mark of a map restricted in a country when the account's country is not known (it
    is neither held back nor hidden); None otherwise."""
    if m.restricted_in is None or m.restricted is not None:
        return None
    # TRANSLATORS: {country} is the name of a country, e.g. South Korea
    return _("Garmin does not offer this map in this country: {country}").format(
        country=country_name(m.restricted_in))


def _restriction(m: dict) -> str | None:
    """The country of the restriction of a map in the server's answer (type "Country" with a
    country; upper case); anything else is not applied, and said in the log by its kind."""
    value = m.get("restriction")
    if value is None:
        return None
    if isinstance(value, dict) and str(value.get("restrictiontype") or "").casefold() == "country":
        country = value.get("country")
        if isinstance(country, str) and country.strip():
            return country.strip().upper()[:8]
    kind = value.get("restrictiontype") if isinstance(value, dict) else type(value).__name__
    log.info("maps: a restriction of another kind (%s), not applied", str(kind)[:40])
    return None


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
    device. The cleanup rules of the answer go with every map."""
    maps = parse_maps(response)
    rules = _cleanup_rules(response)
    for m in maps:
        m.cleanup_rules = rules
    return maps, len(_listed(response))


def _text(value, limit: int = 200) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _cleanup_rules(response) -> tuple[CleanupRule, ...]:
    listed = response.get("directoriestoremove") if isinstance(response, dict) else None
    rules = tuple(CleanupRule(_text(r.get("path")), _text(r.get("externalpath")),
                              _text(r.get("filematch")), _text(r.get("description")))
                  for r in listed or [] if isinstance(r, dict)) if isinstance(listed,
                                                                               list) else ()
    if not rules:
        log.info("maps: 0 cleanup rules")
    for n, r in enumerate(rules, 1):
        # Garmin's own words of what to remove; the log filter masks what it masks anywhere.
        log.info("maps: cleanup rule %d: folder %r, memory card %r, files %r, description %r",
                 n, *(v[:80] if v else v for v in (r.path, r.external_path, r.file_match,
                                                   r.description)))
    return rules


# A pattern of a cleanup rule read as whole file names: these characters only, "*" any run
# of them (Garmin Express reads the rest as a regular expression, anywhere in the name).
_PATTERN = re.compile(r"[A-Za-z0-9._\- *]+")


def _rule_matcher(pattern: str | None):
    if not pattern or not _PATTERN.fullmatch(pattern) or not pattern.strip("* "):
        return None
    regex = "".join(".*" if c == "*" else re.escape(c) for c in pattern)
    return re.compile(regex, re.IGNORECASE)


def _rule_folder(path: str | None) -> str | None:
    if not path:
        return None
    parts = path.replace("\\", "/").strip("/").split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        return None
    return "/".join(parts)


def cleanup_plan(storage: Storage, device, maps: list[UniversalMap]) -> list[CleanupFile]:
    """The files the cleanup rules of the answer would take, read safely (the limits below).
    Only shown: nothing is deleted. A folder that cannot be read gives no plan."""
    from openexpress import ownmaps

    rules = maps[0].cleanup_rules if maps else ()
    if not rules:
        return []
    named = {p.casefold() for m in maps for p in m.all_paths}
    # Only in a folder of the files of the answer's maps (or the device's map folder), only
    # with the extension of such a file.
    folders = {posixpath.dirname(p) for p in named}
    own_folder = ownmaps.map_dir(device)
    if own_folder:
        folders.add(own_folder.casefold())
    extensions = {posixpath.splitext(p)[1] for p in named} - {"", ".xml"}
    found: dict[str, CleanupFile] = {}
    skipped = 0
    try:
        own = {o.path.casefold() for o in ownmaps.list_own(device, storage, maps)}
        copied = {str(r.get("filename", "")).casefold() for r in ownmaps.recorded(device)}
        for rule in rules:
            match, folder = _rule_matcher(rule.file_match), _rule_folder(rule.path)
            why = ("pattern" if match is None else "memory card" if folder is None
                   and rule.external_path and not rule.path else "folder"
                   if folder is None or folder.casefold() not in folders else None)
            if why is not None:
                skipped += 1
                log.info("maps: a cleanup rule was skipped (%s)", why)
                continue
            real = storage.resolve(folder)
            if real is None:
                continue
            for entry in storage.listdir(real):
                path = f"{real}/{entry.name}"
                low = entry.name.casefold()
                # Whole names only, and never a file the server names for a map, a map of your
                # own, a map file the server does not know (it has the header of one),
                # GarminDevice.xml or an update.
                if (entry.is_dir or not match.fullmatch(entry.name)
                        or posixpath.splitext(low)[1] not in extensions
                        or path.casefold() in named or path.casefold() in own
                        or low in copied or low == "garmindevice.xml"
                        or low.startswith("gupdate.")
                        or (low.endswith(".img") and ownmaps.read_title(
                            storage.read_start(path, ownmaps.HEADER_SIZE)) is not None)):
                    continue
                found.setdefault(path.casefold(), CleanupFile(path, entry.size))
    except DeviceError as e:
        log.info("maps: the cleanup rules could not be checked (%s)", type(e).__name__)
        return []
    log.info("maps: %d cleanup rules, %d files match (%d rules skipped)", len(rules),
             len(found), skipped)
    return sorted(found.values(), key=lambda f: f.path.casefold())


def cleanup_lines(files: list[CleanupFile]) -> list[str]:
    """What the questions and the command line say of `files`: a heading, the files and that
    openExpress leaves them; nothing without files."""
    if not files:
        return []
    return [_("Old files Garmin asks to remove:"), *(f.path for f in files),
            _("openExpress does not remove these files yet.")]


# Why a map cannot be installed: a file without a usable download, or one whose MD5 the
# program cannot read (never downloaded without its check).
MISSING_FILE, BAD_CHECKSUM = "missing-file", "bad-checksum"


def can_install(m: UniversalMap) -> bool:
    """Whether the server gave a usable, checked download for every file of `m`, and Garmin
    offers it in the account's country."""
    return m.installable and bool(m.files) and m.restricted is not True


def cannot_install_reason(m: UniversalMap) -> str:
    """The same reason as cannot_install_message, short, for the line under the map's name."""
    if m.restricted is True:
        return _("Cannot be installed: Garmin does not offer this map in your account's "
                 "country")
    if m.unavailable == BAD_CHECKSUM:
        return _("Cannot be installed: the server gave an invalid checksum for one of its "
                 "files")
    return _("Cannot be installed: the server did not provide all of its files")


def cannot_install_message(m: UniversalMap) -> str:
    """Why `m` cannot be installed, the same words in the command line and the window."""
    if m.restricted is True:
        return _("Map '{name}' cannot be installed: Garmin does not offer it in your account's "
                 "country ({country}).").format(name=m.name,
                                                country=country_name(m.restricted_in))
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
            restricted_in=(restricted_in := _restriction(m)),
            restricted=None if restricted_in else False,
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
    """Sets `waiting` on the maps written to or removed from the device that the device has
    not taken in yet: it does that when it is unplugged, and only then do GarminDevice.xml and
    so Garmin's server know. Read from the files on the device against the server's list,
    nothing remembered; none is marked when the files cannot be listed."""
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
        # Written: the server does not have the map (or has an older version), yet every file
        # of the map is on the device, at the size the server gives.
        on_device = [present.get(f.device_path.casefold()) == f.size for f in m.files]
        # An update with the very names of the files installed now cannot be told apart.
        new_names = {f.device_path.casefold() for f in m.files} - {
            p.casefold() for p in m.files_to_remove}
        if (m.files and all(on_device)
                and (not m.installed or (not m.is_reinstall and new_names))):
            m.waiting = WAIT_INSTALL
    removed = []  # decided on the same state for every map, then marked
    for m in maps:
        if not m.listed or m.waiting:
            continue
        # Removed: the server has the map, yet none of its own files (not used by other maps)
        # is on the device.
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
    gone = {p.casefold() for p in removing}
    # A file being replaced frees its space before the new one is written over MTP only; on
    # a drive it stays until the new one is whole.
    replacing = [] if storage.replaces_atomically else [
        f.device_path for f in m.files if f.device_path.casefold() not in gone]
    # Every folder of these files listed once (over MTP each listing is a round trip).
    sizes = storage.sizes([*removing, *replacing])
    freed = sum(sizes[p] or 0 for p in removing)
    replaced = sum(sizes[p] or 0 for p in replacing)
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
    if not can_install(m):
        raise MapError(cannot_install_message(m))
    removing = removal_list(all_maps, m, rewriting=True)
    need = space_needed(storage, m, removing)
    storage.require_space(need, SPACE_MARGIN, error=MapError)

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
    return storage.write_file_checked(f.device_path, local, transfer_progress,
                                      keep_partial=keep, error=MapError)


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


class UnlockNotWritten(MapError):
    """The map files are on the device, its unlock file could not be written. Not "install
    the map again": that is refused while its files are there. str() is for the command
    line, window_text for the window."""

    def __init__(self, path: str, error):
        self.path, self.error = path, error
        super().__init__(_(
            "The map files are on the device, but its unlock file {path} could not be written "
            "({error}). The device may still load the map after it is unplugged; if it does "
            "not, run openexpress maps again.").format(path=path, error=error))

    @property
    def window_text(self) -> str:
        return _("The map files are on the device, but its unlock file {path} could not be "
                 "written ({error}). The device may still load the map after it is unplugged; "
                 "if it does not, come back to this page.").format(path=self.path,
                                                                  error=self.error)


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
                raise UnlockNotWritten(path, e) from e
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

