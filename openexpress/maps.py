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
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from openexpress import answers, applog, awake, cache, download, hidden_folders, paths, updates
from openexpress.connect import ApiError, unexpected_answer_text
from openexpress.device.storage import DeviceError, DeviceNotResponding, Storage, connection
from openexpress.errors import UserFacingError
from openexpress.i18n import _, country_name, human_size
from openexpress.progress import (
    Step,
    byte_steps,
    checking_text,
    copying_text,
    downloading_text,
    say_to,
)

log = logging.getLogger(__name__)

SPACE_MARGIN = 50 * 1024 * 1024
# How fast a map is written and read back whole into a folder a car navigator hides, at the
# slowest measured (a software update of 64.7 MB on a Drive 53 EX, written and read back in
# 9.4 s): only for the estimate of the trial, which errs on the long side.
TRIAL_PACE = 6_900_000  # bytes a second
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
    # Listed by the server, and no folder of its files is seen on the device (a car navigator
    # hides its .System from MTP), nor read through openExpress's own session: its state cannot
    # be read, and nothing is done with it.
    unseen: bool = False
    # The folder of the device's top level its files go to when ordinary MTP does not show it
    # (mark_waiting): nothing is installed or removed there, except by the command line's
    # trial; its files are known only when that folder was read through openExpress's own
    # session (hidden_folders).
    hidden_folder: str | None = None
    # The folder its files go to when the device hides it inside a folder the listing shows
    # (mark_waiting with the device: the rule of the updates, updates.hidden_inside): maps are
    # not written there, the command line's trial included, so it is refused before anything.
    hidden_inside: str | None = None
    # The country Garmin does not offer the map in ("restriction", type "Country"; upper
    # case), and whether that is the account's country (apply_country): True, False (no
    # restriction, or another country), None (restricted, the account's country not known).
    restricted_in: str | None = None
    restricted: bool | None = False
    # The cleanup rules of the answer, for every map as Garmin Express keeps them (only
    # shown: cleanup_plan).
    cleanup_rules: tuple[CleanupRule, ...] = ()
    # The size of each of its files on the device (also those a newer version removes), by
    # its path in lower case, as mark_waiting found them; None when they are not known (not
    # checked against the device, its listing failed, or a folder of its files is hidden and
    # was not read).
    device_sizes: dict[str, int] | None = None
    # A map in a folder the device hides that was being written and stopped (mark_waiting): some
    # of its old files gone or some of its new ones there, not all of them: incomplete.
    partly_written: bool = False

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
        """Partly on the device (as the server knows it, or written partly into a folder the
        device hides): shown there, to be installed again (never an update)."""
        return ((self.state == 2 or self.partly_written) and self.waiting is None
                and not self.unseen)

    @property
    def would_update(self) -> bool:
        """The server has a version of an installed map (not one partly there) that the device
        does not have; never a newer version on the device, which an update would replace
        with the older one."""
        return (self.state == 1 and not self.is_reinstall and self.waiting is None
                and not self.unseen and not self.partly_written)

    @property
    def update_available(self) -> bool:
        """An update to offer: not of a map Garmin does not offer in the account's country."""
        return self.would_update and self.restricted is not True

    @property
    def newer_on_device(self) -> bool:
        """The device has a newer version of the map than the one the server offers: the
        version, files and sizes of the answer are then those of the server's older version,
        not of the files on the device."""
        return (self.state == 3 and self.waiting is None and not self.unseen
                and not self.partly_written)

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
        if self.unseen:
            # TRANSLATORS: map status (`openexpress maps`): the device hides the folder of the
            # map's files from MTP
            return _("cannot be read: openExpress cannot see the folder of its files")
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
        if self.newer_on_device:
            # TRANSLATORS: map status (the Maps page, `openexpress maps`): the device has a newer
            # version of the map than the one Garmin's server offers for it; {version}: Garmin's
            # version, e.g. 9.0
            return _("newer than version {version} Garmin offers").format(version=self.version)
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
        super().__init__(_("Map updates for this device are not supported yet."))
        self.map_update_type = map_update_type


class DeviceHasNoMaps(MapsNotSupported):
    """Garmin's universal map service does not serve the device, and nothing it declares may
    be maps either (declares_maps): it has none, rather than maps this program does not
    update yet (a Venu 2, a Varia)."""

    def __init__(self) -> None:
        MapError.__init__(self, _("This device has no maps."))
        self.map_update_type = None


# The data types of a device's GarminDevice.xml known to be no maps: by them a device whose
# maps Garmin serves another way is told apart from one without maps. Maps come under many
# names (NMaps, BaseMaps, PedestrianMaps, BirdsEye, PreProgrammedDEMMaps, ManifestGmf…), so
# any other data type may be maps. Points of interest (CustomPOI) are no maps.
NO_MAP_DATATYPES = frozenset(name.casefold() for name in (
    "BLELogs", "CustomPOI", "ErrorShutdownReports", "ExpressRequest", "FITBinary", "GPSData",
    "KPI", "LiveActivity", "PROMPT_FILE", "RAMDump", "TranslatedText", "UHCConfig",
    "ULFConfig", "ULFLogs", "UpdateMetadata", "WiFiConfiguration"))
NO_MAP_DATATYPE_PREFIXES = tuple(prefix.casefold() for prefix in (
    "FIT_TYPE_", "WELLNESS_TYPE_", "ACTIVITY_", "BACKUP_", "MEDIA_", "IQ", "Ephemeris"))


def may_be_maps(datatype: str) -> bool:
    """Whether a data type of GarminDevice.xml may be maps: any but those known to be no maps."""
    name = datatype.casefold()
    return name not in NO_MAP_DATATYPES and not name.startswith(NO_MAP_DATATYPE_PREFIXES)


def declares_maps(device) -> bool:
    """Whether the device's GarminDevice.xml declares a data type that may be maps."""
    return any(may_be_maps(name) for name in device.datatypes())


def no_maps_on_device_text() -> str:
    # TRANSLATORS: subtitle of the row "Maps" of the device page and the words of the Maps
    # page of a device without maps, without a full stop
    return _("This device has no maps")


def not_supported_text() -> str:
    # TRANSLATORS: subtitle of the row "Maps" and title of the Maps page, without a full stop
    return _("Map updates for this device are not supported yet")


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
    MapsNotSupported when unit-info says the universal map service does not serve it
    (DeviceHasNoMaps when nothing the device declares may be maps either). `ask`
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
        if not declares_maps(device):
            log.info("maps: map update type %s for %s: the device declares no maps, no map "
                     "check", said, device.part_number)
            raise DeviceHasNoMaps()
        log.info("maps: map update type %s for %s: map updates not supported, no map check",
                 said, device.part_number)
        raise MapsNotSupported(value[:40] if isinstance(value, str) and value else None)
    with answers.told() as sources:
        answer = client.universal_maps(device.raw)
    log.info("maps: map update type %s for %s: the universal map service's answer, %s", said,
             device.part_number, sources[-1] if sources else "asked now")
    found = read_maps(answer)
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
        raise ApiError(unexpected_answer_text(_("Map check")))
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


def _text(value) -> str | None:
    return value.strip()[:200] if isinstance(value, str) and value.strip() else None


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
    named = ownmaps.garmin_paths(maps)
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
        copied = ownmaps.recorded_names(device)
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
    except DeviceNotResponding:
        raise
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


def can_install(m: UniversalMap, hidden_trial: bool = False) -> bool:
    """Whether the server gave a usable, checked download for every file of `m`, Garmin
    offers it in the account's country, and its files go to folders ordinary MTP shows - or,
    with `hidden_trial` (the command line's trial), to a folder of the top level the device
    hides that openExpress's own session read; never to one it hides inside a seen one."""
    return (m.installable and bool(m.files) and m.restricted is not True
            and m.hidden_inside is None
            and (m.hidden_folder is None or (hidden_trial and m.device_sizes is not None)))


def cannot_install_reason(m: UniversalMap) -> str:
    """The same reason as cannot_install_message, short, for the line under the map's name."""
    if m.hidden_folder is not None:
        return not_installed_into(m.hidden_folder)
    if m.hidden_inside is not None:
        return not_installed_hidden_inside(m.hidden_inside)
    if m.restricted is True:
        return _("Cannot be installed: Garmin does not offer this map in your account's "
                 "country")
    if m.unavailable == BAD_CHECKSUM:
        return _("Cannot be installed: the server gave an invalid checksum for one of its "
                 "files")
    return _("Cannot be installed: the server did not provide all of its files")


def cannot_install_message(m: UniversalMap, hidden_trial: bool = False) -> str:
    """Why `m` cannot be installed, the same words in the command line and the window;
    `hidden_trial`: the reasons of the server and the account only (the trial installs into a
    folder the device hides)."""
    if not hidden_trial and m.hidden_folder is not None:
        # TRANSLATORS: {folder}: a folder the device hides from the file manager, e.g.
        # .System
        return _("Map “{name}” cannot be installed: openExpress does not install maps into "
                 "the folder {folder} yet.").format(name=m.name, folder=m.hidden_folder)
    if m.hidden_inside is not None:
        # TRANSLATORS: {folder}: a folder the device hides inside a folder the file manager
        # shows, e.g. GARMIN/RegionMaps
        return _("Map “{name}” cannot be installed: the device hides the folder {folder} from "
                 "the file manager, and openExpress does not install maps there yet.").format(
            name=m.name, folder=m.hidden_inside)
    if m.restricted is True:
        return _("Map “{name}” cannot be installed: Garmin does not offer it in your account's "
                 "country ({country}).").format(name=m.name,
                                                country=country_name(m.restricted_in))
    if m.unavailable == BAD_CHECKSUM:
        return _("Map “{name}” cannot be installed: the server gave an invalid checksum for "
                 "one of its files.").format(name=m.name)
    return _("Map “{name}” cannot be installed: the server did not provide "
             "all of its files.").format(name=m.name)


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
        raise MapError(_("No map named “{name}”").format(name=name))
    raise MapError(_("Ambiguous name, matches: {names}")
                   .format(names="; ".join(m.name for m in partial)))


def mark_waiting(storage: Storage, maps: list[UniversalMap], device=None) -> None:
    """Sets `waiting` on the maps written to or removed from the device that the device has
    not taken in yet: it does that when it is unplugged, and only then do GarminDevice.xml and
    so Garmin's server know. Read from the files on the device against the server's list,
    nothing remembered; none is marked when the files cannot be listed. With the `device`
    (its GarminDevice.xml): the maps whose files go to a folder it hides inside a seen one
    (hidden_inside)."""
    for m in maps:
        m.hidden_inside = None
        m.waiting = None
        m.unseen = False
        m.device_sizes = None
        m.partly_written = False
    wanted = {p for m in maps for p in m.all_paths}
    present: dict[str, int] = {}  # casefolded path -> size, one listing per folder
    unseen: set[str] = set()  # casefolded folders the listing does not show
    unread: set[str] = set()  # of those, the hidden ones not read: what they hold is not known
    listing = hidden_folders.kept(storage)  # what this connection read of folders not seen
    try:
        tops = hidden_folders.top_folders(storage)
        for m in maps:
            m.hidden_folder = _top_not_seen(m.all_paths, tops)
        if device is not None:
            _mark_hidden_inside(storage, maps, device)
        for folder in {posixpath.dirname(p) for p in wanted}:
            if hidden_folders.top_not_seen(folder, tops) is not None:
                # Read through openExpress's own session, or not known.
                read = (listing.entries(folder) if listing is not None
                        and listing.state_of(folder) == hidden_folders.READ else None)
                if read is None:
                    unseen.add(folder.casefold())
                    unread.add(folder.casefold())
                for e in read or []:
                    if not e.is_dir:
                        present[posixpath.join(folder, e.name).casefold()] = e.size
                continue
            if storage.resolve(folder, strict=True) is None:
                unseen.add(folder.casefold())
                continue
            for e in storage.listdir(folder):
                if not e.is_dir:
                    present[posixpath.join(folder, e.name).casefold()] = e.size
    except DeviceNotResponding:
        raise  # the device stopped answering: nothing more is asked of it
    except Exception as e:  # noqa: BLE001 - only shown: the map check goes on without it
        log.warning("maps: cannot read the map files on the device, none is marked as "
                    "waiting for unplugging: %s", e)
        log_states(storage, maps)
        return
    for m in maps:
        paths = {p.casefold() for p in m.all_paths}
        if not any(posixpath.dirname(p) in unread for p in paths):
            m.device_sizes = {p: present[p] for p in paths if p in present}
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
    for m in maps:
        m.partly_written = _partly_written(m, maps, present)
    removed = []  # decided on the same state for every map, then marked
    for m in maps:
        if not m.listed or m.waiting or m.partly_written:
            continue
        # Removed: the server has the map, yet none of its own files (not used by other maps)
        # is on the device.
        own = {p.casefold() for p in m.all_paths}
        own -= protected_files(maps, m)
        if own and all(posixpath.dirname(p) in unseen for p in own):
            # No folder of its files is seen: that says nothing of the map.
            m.unseen = True
            continue
        if own and not any(p in present for p in own):
            removed.append(m)
    for m in removed:
        m.waiting = WAIT_REMOVE
    log_states(storage, maps)


def _mark_hidden_inside(storage: Storage, maps: list[UniversalMap], device) -> None:
    """Sets `hidden_inside` on the maps a file of which goes to a folder the device hides
    inside a seen one, by the rule of the updates; only the maps' own folders are asked."""
    def folder(f: MapFile) -> str:
        return posixpath.dirname(f.device_path).strip("/").casefold()

    inside = updates.hidden_inside(storage, device, among={folder(f) for m in maps
                                                           for f in m.files})
    for m in maps:
        m.hidden_inside = next((inside[folder(f)] for f in m.files if folder(f) in inside), None)
    refused = [m for m in maps if m.hidden_inside is not None]
    if refused:
        log.info("maps: %d go to a folder the device hides inside a seen one (%s): not "
                 "installed there", len(refused),
                 ", ".join(sorted({m.hidden_inside for m in refused}, key=str.casefold)))


def _partly_written(m: UniversalMap, maps: list[UniversalMap], present: dict[str, int]) -> bool:
    """Whether a map in a folder the device hides, read through openExpress's own session, was
    being written and stopped: not all of its files there at their size, while some of its own
    old files are gone, or (not installed) some of its new files are there. Files other maps use
    say nothing."""
    if m.hidden_folder is None or m.device_sizes is None or m.waiting or not m.files:
        return False
    here = [present.get(f.device_path.casefold()) == f.size for f in m.files]
    if all(here):
        return False
    new = {f.device_path.casefold() for f in m.files}
    shared = protected_files(maps, m)
    own_old = [p.casefold() for p in m.files_to_remove
               if p.casefold() not in new and p.casefold() not in shared]
    old_gone = any(p not in present for p in own_old)
    return old_gone or (any(here) and not m.installed)


def on_the_device(m: UniversalMap, paths: list[str]) -> list[str]:
    """Those of `paths` that are on the device as mark_waiting found its files (device_sizes):
    a file Garmin lists among the old ones that the device does not have is not removed. All of
    them when its files are not known."""
    if m.device_sizes is None:
        return list(paths)
    return [p for p in paths if p.casefold() in m.device_sizes]


def hidden_space(storage: Storage, m: UniversalMap, removing: list[str]) -> tuple[int, int | None]:
    """(needed, free): the space a map going into a folder the device hides needs there, its old
    files and those it writes over counted as they are removed first, from what openExpress's
    own session read (device_sizes); the device's free space."""
    sizes = m.device_sizes or {}
    gone = {p.casefold() for p in removing} | {f.device_path.casefold() for f in m.files}
    freed = sum(sizes.get(p, 0) for p in gone)
    return max(0, m.size - freed), storage.free_space()


def free_on_computer() -> int:
    """The free space where the map files are downloaded (the cache)."""
    where = paths.cache_dir()
    while not where.exists():
        where = where.parent
    return shutil.disk_usage(where).free


def download_space(m: UniversalMap) -> tuple[int, int]:
    """(needed, free) on this computer: the files of `m` not in the cache yet, and the space."""
    needed = sum(f.size or 0 for f in m.files if not (
        f.md5 and cache_file(f).exists() and cache_file(f).stat().st_size == f.size))
    return needed, free_on_computer()


def trial_minutes(m: UniversalMap) -> int:
    """About how many minutes writing and reading back the files of `m` take, at TRIAL_PACE."""
    return max(1, -(-m.size // (TRIAL_PACE * 60)))


def no_complete_map_text() -> str:
    # TRANSLATORS: `openexpress maps install … --hidden-folder-trial`: the old files of the map are
    # removed before the new ones are written, as Garmin Express does
    return _("From the removal of the first old file until the last one is written, the device "
             "has no complete map: do not unplug it until openExpress has finished. Once "
             "unplugged, keep it powered (its car cable or a USB charger) until it has loaded "
             "the new map.")


def folder_not_read_text(folder: str) -> str:
    # TRANSLATORS: {folder}: a folder the device hides, e.g. .System/Map
    return _("The folder {folder} was not read over a direct USB connection.").format(
        folder=folder)


def too_large_text(path: str, size: int) -> str:
    # TRANSLATORS: {path}: a file of a map on the device; {size}: its size
    return _("{path} is {size}: a file over 4 GiB cannot be sent to the device.").format(
        path=path, size=human_size(size))


def no_unlock_place_text(name: str) -> str:
    # TRANSLATORS: {name}: a map; Garmin's server activated it and gave an unlock file without
    # saying where it goes on the device, so the map is refused before the device changes
    return _("{name} was not installed: Garmin's activation did not say where on the device its "
             "unlock file goes. Nothing on the device was changed.").format(name=name)


def trial_refusals(storage: Storage, m: UniversalMap, removing: list[str]) -> list[str]:
    """Why the trial would refuse `m` before it downloads anything or changes the device, each
    once: what the server gave (no usable download, not in the account's country), a folder
    of its files in the folder the device hides not read or not on the device (no folder is
    ever made), a file over what one object can hold."""
    from openexpress.device import ptp

    reasons = []
    if not (m.installable and m.files) or m.restricted is True:
        reasons.append(cannot_install_message(m, hidden_trial=True))
    listing = hidden_folders.kept(storage)
    hidden = (m.hidden_folder or "").casefold()
    targets = {posixpath.dirname(f.device_path.strip("/")) for f in m.files}
    olds = {posixpath.dirname(p.strip("/")) for p in removing}
    for folder in sorted(targets | olds, key=str.casefold):
        if folder.split("/")[0].casefold() != hidden:
            continue  # a folder ordinary MTP shows
        if listing is None or listing.state != hidden_folders.READ or not listing.knows(folder):
            reasons.append(folder_not_read_text(folder))
        elif listing.entries(folder) is None and folder in targets:
            reasons.append(hidden_folders.no_folder_text(folder))
    reasons += [too_large_text(f.device_path, f.size) for f in m.files
                if f.size and f.size > ptp.MAX_OBJECT_SIZE]
    return list(dict.fromkeys(reasons))


def no_room_on_computer_text(name: str, needed: int, free: int) -> str:
    # TRANSLATORS: {name} is a map; {needed} and {free} are sizes
    return _("Not enough space on this computer for the download of {name}: {needed} needed, "
             "{free} free.").format(name=name, needed=human_size(needed), free=human_size(free))


def used_on_device(maps: list[UniversalMap]) -> int | None:
    """What the files of `maps` take on the device: the size of each as mark_waiting found it,
    a file several maps use counted once; None when the size of a file of one of them is not
    known (a smaller sum would be shown as the whole)."""
    sizes: dict[str, int] = {}
    for m in maps:
        if m.device_sizes is None:
            return None
        sizes.update(m.device_sizes)
    return sum(sizes.values())


def _top_not_seen(paths, tops: set[str] | None) -> str | None:
    """The first folder of the top level among those of `paths` that ordinary MTP does not show
    (`tops`: those it shows, in lower case); None when all are seen, or the top level could not
    be listed."""
    return next((top for p in paths
                 if (top := hidden_folders.top_not_seen(
                     posixpath.dirname(p.replace("\\", "/")), tops)) is not None), None)


def hidden_needed(maps: list[UniversalMap]) -> list[str]:
    """The folders of the files of the maps on the device that cannot be read because their
    folder of the top level is not seen (unseen, hidden_folder), each once: what openExpress's
    own session would read (hidden_folders.read)."""
    out: dict[str, str] = {}
    for m in maps:
        if not m.unseen or m.hidden_folder is None:
            continue
        for p in m.all_paths:
            folder = posixpath.dirname(p.strip("/"))
            if folder.split("/")[0].casefold() == m.hidden_folder.casefold():
                out.setdefault(folder.casefold(), folder)
    return sorted(out.values(), key=str.casefold)


def not_installed_into(folder: str) -> str:
    # TRANSLATORS: on the Maps page, under a map whose files go to a folder the device hides
    # ({folder}, e.g. .System), and under its own maps' folder there, without a full stop;
    # openExpress installs software updates there, not maps
    return _("openExpress does not install maps into the folder {folder} yet").format(
        folder=folder)


def not_installed_hidden_inside(folder: str) -> str:
    # TRANSLATORS: on the Maps page, under a map whose files go to a folder ({folder}, e.g.
    # GARMIN/RegionMaps) that the device hides inside a folder the file manager shows; without a
    # full stop
    return _("The device hides the folder {folder} from the file manager; openExpress does not "
             "install maps there yet").format(folder=folder)


def read_only_text(folder: str) -> str:
    # TRANSLATORS: `openexpress maps`, under a map whose files are in a folder the device hides
    # ({folder}, e.g. .System); openExpress installs software updates there, not maps
    return _("openExpress does not install maps into the folder {folder} yet.").format(
        folder=folder)


def _state_words(m: UniversalMap) -> str:
    """The state of a map in the words of the log (status says it in the window's)."""
    if m.unseen:
        return "cannot be read, no folder of its files seen"
    if m.waiting == WAIT_INSTALL:
        return "written, loaded when the device is unplugged"
    if m.waiting == WAIT_REMOVE:
        return "removed, gone when the device is unplugged"
    if m.incomplete:
        return "incomplete"
    if m.update_available:
        return "update available"
    if m.would_update:
        return "update not offered in the account's country"
    if m.state == 3:
        return "newer on the device"
    return "current"


def log_states(storage: Storage, maps: list[UniversalMap]) -> None:
    """One line of the log after the list was matched with the device: each map the device
    has or takes in when unplugged, with its state (maps only offered are left out); a short
    line when this connection logged the same before."""
    shown = [m for m in maps if m.listed or m.waiting]
    states = "; ".join(f"{m.name} {_state_words(m)}" for m in shown) or "no map on the device"
    if applog.unchanged("maps", connection(storage), states):
        log.info("maps: the same states as before")
    else:
        log.info("maps: states: %s", states)


def waiting_message(m: UniversalMap) -> str:
    return _("Map “{name}” is on the device already and will be loaded after the device is "
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


class SpacePlan(NamedTuple):
    """An install's space on the device, and the old files it removes."""
    needed: int  # free while it writes
    change: int  # how much less is free once it is done (negative: more)
    # Those of the old files Garmin lists that the device has: the only ones removed, and
    # counted in the question and the log.
    old: list[str]


def space_plan(storage: Storage, m: UniversalMap, removing: list[str]) -> SpacePlan:
    """The space the install needs free while it writes, how much less space is free once it
    is done, the old files removed and those written over giving theirs back, and which of the
    old files `removing` names the device has."""
    gone = {p.casefold() for p in removing}
    replacing = [f.device_path for f in m.files if f.device_path.casefold() not in gone]
    # Every folder of these files listed once (over MTP each listing is a round trip).
    sizes = storage.sizes([*removing, *replacing])
    freed = sum(sizes[p] or 0 for p in removing)
    replaced = sum(sizes[p] or 0 for p in replacing)
    change = m.size - freed - replaced
    # A file being replaced frees its space before the new one is written over MTP only; on
    # a drive it stays until the new one is whole.
    needed = m.size - freed - (0 if storage.replaces_atomically else replaced)
    return SpacePlan(max(0, needed), change, [p for p in removing if sizes[p] is not None])


def write_order(m: UniversalMap) -> list[MapFile]:
    """The files of a map in the order install downloads and writes them, and the plan lists
    them: smallest first, as Garmin Express does, so the small type and search files are on
    the device before the big map and terrain files. Files of the same size keep the order
    of the server (the sort is stable)."""
    return sorted(m.files, key=lambda f: f.size)


def cache_file(f: MapFile) -> Path:
    """Named after its MD5: a map with a file without one is not installed (can_install)."""
    return paths.cache_dir() / "maps" / f.md5


@dataclass
class MapReport:
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unlocks: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # there already, read back the same
    gmas: list[str] = field(default_factory=list)  # a short MD5 of each unlock file, for the log


def _install(
    m: UniversalMap,
    all_maps: list[UniversalMap],
    storage: Storage,
    activate: Callable[[dict], dict],
    session=None,
    progress: Callable[[str], None] | None = None,
    url_salt: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    hidden_trial: bool = False,
) -> MapReport:
    if m.waiting == WAIT_INSTALL:
        raise MapError(waiting_message(m))
    if not can_install(m, hidden_trial=hidden_trial):
        raise MapError(cannot_install_message(m))
    removing = removal_list(all_maps, m, rewriting=True)
    if m.hidden_folder is not None:
        return _install_hidden(m, removing, storage, activate, session, progress, url_salt,
                               sleep)
    plan = space_plan(storage, m, removing)
    storage.require_space(plan.needed, SPACE_MARGIN, error=MapError)
    removing = plan.old  # Garmin's list may name old files the device does not have

    local = _download(m, session, progress, url_salt)
    unlocks = _activation(m, activate, say_to(progress))
    files = write_order(m)
    say = say_to(progress)
    report = MapReport()
    # From the first removal to the last write the device has no complete map: kept awake.
    with awake.kept_awake(f"map {m.name} is written", say=say):
        _write_all(m, all_maps, storage, removing, files, local, unlocks, progress, say, sleep,
                   report)
    log.info("map install: %s %s, %d files written (%s), %d old files removed, %d unlock files"
             " (md5 %s)", m.name, m.version, len(report.written), applog.size(m.size),
             len(report.removed), len(report.unlocks), ", ".join(report.gmas) or "-")
    return report


def _write_all(m, all_maps, storage, removing, files, local, unlocks, progress, say, sleep,
               report: MapReport) -> None:
    """The old files of `m` removed, its files written and checked, then its unlock files."""
    for n, p in enumerate(removing, 1):
        # TRANSLATORS: a step of a map install: files of an older version or of maps it
        # replaces ("Removing old map files · 2 of 5")
        say(Step(_("Removing old map files"), n, len(removing)))
        log.debug("map install: removing %s", p)
        if storage.delete(p):  # one gone since the space was read is not counted
            report.removed.append(p)
    shared = protected_files(all_maps, m)
    copying, checking = copying_text(m.name), checking_text(m.name)
    copy_total = sum(f.size or 0 for f in files)
    copied = 0  # the bytes of the files written before the current one
    for f in files:
        told = byte_steps(progress, copying, before=copied, total=copy_total)
        # Where the storage reads each file back after its copy: told on the same scale.
        checked = byte_steps(progress, checking, before=copied, total=copy_total)
        log.debug("map install: copying %s", f.device_path)
        if f.device_path.casefold() not in shared:
            real = _write_checked(storage, f, local[f.device_path], told, checked)
        else:
            real = _write_shared(storage, f, local[f.device_path], told,
                                 _users(all_maps, m, f.device_path), checked)
        copied += f.size or 0
        report.written.append(real)
    for fname, gma in unlocks:
        data = base64.b64decode(gma)
        real = _write_unlock(storage, _device_path(fname), data, say, sleep)
        report.unlocks.append(real)
        report.gmas.append(_short_md5(data))


def _download(m: UniversalMap, session, progress, url_salt: str | None) -> dict[str, Path]:
    """Every file of `m` downloaded and checked against its MD5 (download.fetch; one in the
    cache already is not downloaded again), by its path on the device."""
    local: dict[str, Path] = {}
    # The map as a whole, by its name: the files are the server's and the device's own.
    downloaded = byte_steps(progress, downloading_text(m.name))
    # One bar for all the files of the map.
    overall = download.Overall(m.size, downloaded) if downloaded is not None else None
    for f in write_order(m):
        log.debug("map install: downloading %s (%s)", f.device_path, applog.size(f.size))
        urls = [h + f.url.lstrip("/") for h in m.hosts] if not f.url.startswith("http") else [f.url]
        if url_salt:
            urls = [download.tokenize(u, url_salt) for u in urls]
        local[f.device_path] = download.fetch(urls, cache_file(f), f.size, f.md5, session=session,
                                              progress=overall.file() if overall else None)
        if overall:
            overall.finished(f.size)
    return local


def _activation(m: UniversalMap, activate, say, strict: bool = False) -> list[tuple[str, str]]:
    """The map activated on Garmin's server: its unlock files, (file name, data in base64).
    `strict` (the trial): an unlock file without its place on the device refuses the map,
    before the device changes."""
    say(_("Activating the map on the Garmin server…"))
    act = activate(m.identifier)
    entries = act.get("unlocks") or []
    if strict and any(u.get("gma") and not u.get("filename") for u in entries):
        log.warning("map install: the activation of %s %s gave an unlock file without its "
                    "place: nothing written", m.name, m.version)
        raise MapError(no_unlock_place_text(m.name))
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
    return unlocks


def _install_hidden(m: UniversalMap, removing: list[str], storage: Storage, activate,
                    session, progress, url_salt: str | None,
                    sleep: Callable[[float], None]) -> MapReport:
    """The trial: a map into a folder the device hides, read through openExpress's own session.
    Nothing on the device changes until every file is downloaded and checked and the map is
    activated; then one session (hidden_folders.write_map) removes its old files (none that
    other maps use) and writes its files, each read back whole, then its unlock files."""
    refused = trial_refusals(storage, m, removing)
    if refused:
        log.info("map install: %s %s refused before anything (%d reasons)", m.name, m.version,
                 len(refused))
        raise MapError(refused[0])
    need, free = hidden_space(storage, m, removing)
    storage.require_space(need, SPACE_MARGIN, error=MapError, free=free)
    need_here, free_here = download_space(m)
    if need_here + SPACE_MARGIN > free_here:
        raise MapError(no_room_on_computer_text(m.name, need_here + SPACE_MARGIN, free_here))
    log.info("map install: %s %s into %s (the trial): %d files (%s), %d old files to remove",
             m.name, m.version, m.hidden_folder, len(m.files), applog.size(m.size),
             len(on_the_device(m, removing)))
    local = _download(m, session, progress, url_salt)
    unlocks = _activation(m, activate, say_to(progress), strict=True)
    files = [hidden_folders.FileToWrite(f.device_path, local[f.device_path], f.size, f.md5)
             for f in write_order(m)]
    try:
        done = hidden_folders.write_map(
            storage, m.name, removing, files,
            [(_device_path(name), base64.b64decode(gma)) for name, gma in unlocks],
            SPACE_MARGIN, progress=progress, copying=copying_text(m.name),
            checking=checking_text(m.name), sleep=sleep, unlock_tries=UNLOCK_TRIES,
            unlock_pause=UNLOCK_PAUSE_S)
    except DeviceNotResponding:
        raise
    except hidden_folders.UnlockFailed as e:  # the map's files are there: said as for any map
        raise UnlockNotWritten(e.path, e.error) from e
    except DeviceError as e:  # refused before anything changed, or stopped partway (the text)
        raise MapError(str(e)) from e
    return MapReport(written=done.written, removed=done.removed, unlocks=done.unlocks,
                     kept=done.kept)


def install(*args, **kwargs) -> MapReport:
    """Install a map (see _install), holding the cache from the download to the copy to the
    device: it is not cleared meanwhile."""
    with cache.using():
        return _install(*args, **kwargs)


def _write_checked(storage: Storage, f: MapFile, local: Path, told, checked=None,
                   keep: bool = False) -> str:
    """Writes one file and checks its size (on a drive its bytes, read back and told to
    `checked`). What a failed write leaves is cleaned up by the storage itself, unless `keep`
    (a file other maps use: never deleted); a file of the wrong size is deleted here, unless
    `keep`."""
    return storage.write_file_checked(f.device_path, local, told, keep_partial=keep,
                                      error=MapError, checking=checked)


def _write_shared(storage: Storage, f: MapFile, local: Path, told,
                  users: list[str], checked=None) -> str:
    """A file other maps use too, installed or written and waiting to be taken in: deleting
    it after a failed write would break them, so the write is tried once more and the file
    is never deleted."""
    try:
        return _write_checked(storage, f, local, told, checked, keep=True)
    except DeviceNotResponding:
        raise  # not tried again
    except Exception as e:  # noqa: BLE001 - any failure gets the one retry
        log.warning("map install: writing a file other maps use failed, trying once more: %s",
                    e)
    try:
        return _write_checked(storage, f, local, told, checked, keep=True)
    except DeviceNotResponding:
        raise
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
            # Of another size (or other bytes on a drive) it is deleted: tried again.
            return storage.write_checked(path, data)
        except DeviceNotResponding:
            raise  # not tried again
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
    return _("Map “{name}” cannot be removed").format(name=m.name)


def removal_refused(m: UniversalMap) -> str | None:
    """Why `m` is never removed, whatever the server allows: its files are in a folder the
    device hides; None when nothing keeps it."""
    if m.unseen:
        # TRANSLATORS: error of a map removal: the device hides the folder of the map's files
        return _("Map “{name}” cannot be read: openExpress cannot see the folder of its files "
                 "on the device.").format(name=m.name)
    if m.hidden_folder is not None:
        # TRANSLATORS: error of a map removal; {folder}: a folder the device hides from the
        # file manager, e.g. .System
        return _("Map “{name}” cannot be removed: openExpress does not remove maps from "
                 "the folder {folder} yet.").format(name=m.name, folder=m.hidden_folder)
    return None


def uninstall(m: UniversalMap, all_maps: list[UniversalMap], storage: Storage,
              progress: Callable[[str], None] | None = None) -> list[str]:
    progress = say_to(progress)
    if (why := removal_refused(m)) is not None:
        raise MapError(why)
    if not m.can_uninstall:
        raise MapError(cannot_remove_message(m))
    removed = []
    there = [p for p in removal_list(all_maps, m, rewriting=False) if storage.exists(p)]
    for n, p in enumerate(there, 1):
        # TRANSLATORS: a step of a map removal; {name}: the map ("… · 2 of 5")
        progress(Step(_("Removing {name}").format(name=m.name), n, len(there)))
        log.debug("map removal: %s", p)
        storage.delete(p)
        removed.append(p)
    log.info("map removal: %s, %d files removed", m.name, len(removed))
    return removed

