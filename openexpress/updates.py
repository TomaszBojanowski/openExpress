"""Device software updates (firmware and auxiliary files).

Flow as in Garmin Express:
  1. POST /api/device-software/v1/updates with GarminDevice.xml
  2. download `secureDownloadUrl`, check size and MD5
  3. copy to `installation.path/fileName` on the device
  4. disconnect the device: it installs the file itself; the confirmation is that
     the update is no longer returned by the next server query.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from openexpress import applog, cache, download, paths
from openexpress.device.storage import DeviceError, Storage
from openexpress.errors import UserFacingError
from openexpress.i18n import N_, _, human_size
from openexpress.progress import Step

log = logging.getLogger(__name__)

RGN_MAGIC = b"KpGr"
# Room kept free on the device besides an update: at most SPACE_MARGIN, and no more than a
# part of a small drive (the whole drive of a Varia RTL515 is 1.3 MB).
SPACE_MARGIN = 2 * 1024 * 1024
SPACE_MARGIN_PART = 0.05
# The files of a device system update. The server does not always say so: the system
# updates of a fēnix 8 and an Edge 1050 (GUPDATE.GSP, a bundle with the device software)
# come without "isprimaryfirmware" and without "isrestartrequired". The data type does not
# tell either: fonts and databases are "Firmware" too.
SYSTEM_FILES = frozenset({"GUPDATE.GCD", "GUPDATE.GSP"})
# What to do after an update was copied to the device: shown after every install, because
# the device may ask and restart also when the server does not call the update one that
# needs a restart. A watch is on when it is unplugged, while an accessory may stay off (e.g.
# a Varia) and installs the update only once turned on. A restart is only a possibility:
# nothing promises one. Untranslated constants; show them with _(FINISH_STEPS).
FINISH_STEPS = N_("""
To finish the installation:
  1. Disconnect the device from the computer.
  2. If the device does not turn on by itself, turn it on.
  3. The device installs the update by itself. It may ask whether to install it:
     choose to install now. It may also restart. Wait until it has finished.
  4. Reconnect it and run `openexpress updates`: the installed update
     should no longer be listed.
""")
# The same for the window, which has no commands.
FINISH_STEPS_WINDOW = N_("""
To finish the installation:
  1. Disconnect the device from the computer.
  2. If the device does not turn on by itself, turn it on.
  3. The device installs the update by itself. It may ask whether to install it:
     choose to install now. It may also restart. Wait until it has finished.
  4. Reconnect it and check for updates again: the installed update
     should no longer be listed.
""")
# Said before a system update is installed, on the command line and in the window.
SYSTEM_UPDATE_WARNING = N_("Warning: this is a device system update. Charge the device and do "
                           "not disconnect it while copying.")
# The kinds of device (the "segmenttype" of unit-info) that Garmin marks as losing the data
# they hold when their software is updated. Garmin Express holds such an update back while a
# sync of the device runs; openExpress says so before every update and suggests to sync first.
DATA_LOST_SEGMENTS = frozenset({"wellness", "vivomove"})
# Said before every update of such a device: in the window, on the command line, and for a
# device connected as a USB drive, which openExpress does not sync.
DATA_LOSS_WARNING = N_("Warning: installing an update erases the data stored on this device. "
                       "Sync it with Garmin Connect first, or the data not uploaded yet is lost.")
DATA_LOSS_WARNING_CLI = N_("Warning: installing an update erases the data stored on this "
                           "device. Sync it first (openexpress sync --apply), or the data not "
                           "uploaded yet is lost.")
DATA_LOSS_WARNING_DRIVE = N_("Warning: installing an update erases the data stored on this "
                             "device. openExpress does not sync a device connected as a USB "
                             "drive: sync it with the Garmin Connect app first, or the data not "
                             "uploaded yet is lost.")
# The offered updates named one by one in the log, at most.
LOG_ITEMS = 20
# Said when the server offers beta versions to the device ("accesslevel" of the answer, e.g.
# "public_beta"), on the command line and in the window.
BETA_OFFERED = N_("Garmin offers beta software for this device.")


class UpdateError(UserFacingError):
    pass


@dataclass
class SoftwareUpdate:
    part_number: str
    name: str
    version: str
    severity: str
    is_primary: bool
    is_reinstall: bool
    restart_required: bool
    device_path: str
    url: str
    md5: str | None
    size: int | None
    order: int
    eula_url: str = ""
    changes: list[str] = field(default_factory=list)
    datatype: str = ""
    # The file is on the device already, with the same size and MD5 (`mark_present`).
    on_device: bool = False
    # "live", "archived", or for a beta version e.g. "public_beta_phased_rollout".
    release_state: str = ""

    @property
    def key(self) -> str:
        return f"{self.part_number}.{self.version}"

    @property
    def title(self) -> str:
        """The name to show: the product name, or a readable name of the type when the
        server only repeats the type (e.g. "Etyp") or gives no name."""
        if self.name and self.name != self.datatype:
            return self.name
        known = TYPE_NAMES.get(self.datatype.casefold())
        return _(known) if known else self.name or self.datatype

    @property
    def filename(self) -> str:
        return posixpath.basename(self.device_path)

    @property
    def severity_text(self) -> str:
        known = SEVERITY_NAMES.get(self.severity.casefold())
        return _(known) if known else self.severity

    @property
    def is_metadata(self) -> bool:
        return self.part_number.endswith("-META")

    @property
    def is_beta(self) -> bool:
        return "beta" in self.release_state.casefold()

    @property
    def is_system(self) -> bool:
        """An update of the device system: copied last, and the device must stay connected
        and charged while it is copied."""
        return (self.is_primary or self.restart_required
                or self.filename.upper() in SYSTEM_FILES)


# Types of updates as the server names them ("datatype"), seen for a fēnix 8 and in the
# answers Garmin Express logs. An unknown type is shown as the server sent it.
TYPE_NAMES = {
    # TRANSLATORS: kind of update: a file that belongs to a map (Garmin/SID/*.sid)
    "etyp": N_("Map support file"),
    # TRANSLATORS: kind of update
    "firmware": N_("Software"),
    # TRANSLATORS: kind of update
    "config": N_("Configuration"),
    # TRANSLATORS: kind of update: a file that describes other updates
    "updatemetadata": N_("Update information"),
}
SEVERITY_NAMES = {
    # TRANSLATORS: importance of an update
    "critical": N_("Critical"),
    # TRANSLATORS: importance of an update
    "recommended": N_("Recommended"),
    # TRANSLATORS: importance of an update
    "optional": N_("Optional"),
    # TRANSLATORS: importance of an update, when the server does not say
    "unspecified": N_("Unspecified"),
}


# Whether the file of an offered update is on the device, as the log says it.
MATCHES = "matches"
NOT_ON_THE_DEVICE = "not on the device"
MD5_DIFFERS = "MD5 differs"


class Presence(NamedTuple):
    """Whether the file of an offered update is on the device (`why`: MATCHES, or why not, in
    words for the log) and the path of the file found at its place (`real`), if any."""

    why: str
    real: str | None = None


def presence(storage: Storage, u: SoftwareUpdate) -> Presence:
    """Whether the file of this update is on the device already (MATCHES: same size and same
    MD5), and if not, why not. The file is looked for in any letter case. Only reads. Without
    a size and an MD5 from the server nothing can be said."""
    md5 = download.normalize_md5(u.md5) if u.md5 else None
    if not u.device_path or not u.size or md5 is None:
        return Presence("not compared (no size or MD5 from the server)")
    real = None
    try:
        real = storage.resolve(u.device_path)
        size = storage.size(real) if real is not None else None
        if size is None:
            return Presence(NOT_ON_THE_DEVICE)
        if size != u.size:
            return Presence(f"size differs ({applog.size(size)} on the device, "
                            f"{applog.size(u.size)} offered)", real)
        same = hashlib.md5(storage.read(real)).hexdigest() == md5
        return Presence(MATCHES if same else MD5_DIFFERS, real)
    except DeviceError as e:
        log.info("updates: cannot compare %s with the device: %s", u.part_number, e)
        return Presence("not compared (cannot be read)", real)


def _where(u: SoftwareUpdate, found: Presence) -> str:
    """The place of an update's file for the log: the server's path, and the file on the
    device too when its name has another letter case (its folder's case is the device's
    own and is written into as it is)."""
    if found.real and posixpath.basename(found.real) != posixpath.basename(u.device_path):
        return f"{u.device_path} (on the device: {found.real})"
    return u.device_path


def component_count(device) -> int | None:
    """How many components the device's GarminDevice.xml lists; None when that is not known.
    Only for the log, so it never fails."""
    files = getattr(device, "update_files", None)
    return len(files) if isinstance(files, list) else None


def mark_present(storage: Storage, updates: list[SoftwareUpdate],
                 components: int | None = None) -> list[SoftwareUpdate]:
    """Find the offered updates whose file the device already has (e.g. map support files
    it briefly does not list right after it is plugged in). They are not updates.

    `components`: how many components the device's GarminDevice.xml lists, for the log."""
    offered = [u for u in updates if not u.is_reinstall and not u.is_metadata]
    found = []
    for u in offered:
        found.append(presence(storage, u))
        u.on_device = found[-1].why == MATCHES
    # For each: where its file goes on the device, from the device's folder, and whether it
    # is there; never an address (a download address carries a token).
    named = ", ".join(f"{u.part_number} {u.version} at {_where(u, p)}: {p.why}"
                      for u, p in zip(offered[:LOG_ITEMS], found, strict=False))
    if len(offered) > LOG_ITEMS:
        named += f", … and {len(offered) - LOG_ITEMS} more"
    log.info("updates: %d offered%s, %d of them already on the device%s", len(offered),
             f" ({named})" if named else "", sum(1 for u in offered if u.on_device),
             f"; {components} components listed" if components is not None else "")
    return updates


def staged(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """System updates that are copied already and wait for the device to install them, which
    it does when it is disconnected. Two facts, no guess: the server still offers the update
    (not as a reinstall), so the device has not installed it, and the very same file (size
    and MD5) is on the device."""
    return [u for u in updates
            if u.is_system and u.on_device and not u.is_reinstall and not u.is_metadata]


def pending(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """The real updates: not reinstalls, not metadata, not on the device already."""
    return [u for u in updates if not u.is_reinstall and not u.is_metadata and not u.on_device]


def parse_updates(response: dict) -> list[SoftwareUpdate]:
    out = []
    for u in response.get("updates") or []:
        inst = u.get("installation") or {}
        deliv = u.get("deliverable") or {}
        path = (inst.get("path") or "").replace("\\", "/").strip("/")
        fname = inst.get("filename") or ""
        if not fname:
            continue
        changes = [c for rn in u.get("releasenotes") or [] for c in rn.get("changelog") or []]
        out.append(
            SoftwareUpdate(
                part_number=u.get("partnumber") or "",
                name=u.get("productname") or u.get("description") or "",
                version=(u.get("displayversion") or u.get("semanticversion")
                         or u.get("version") or ""),
                severity=u.get("severity") or "Unspecified",
                is_primary=bool(u.get("isprimaryfirmware")),
                is_reinstall=bool(inst.get("isreinstall")),
                restart_required=bool(inst.get("isrestartrequired")),
                device_path=posixpath.join(path, fname) if path else fname,
                url=deliv.get("securedownloadurl") or deliv.get("downloadurl") or "",
                md5=deliv.get("md5"),
                size=deliv.get("sizeinbytes"),
                order=int(inst.get("order") or 0),
                eula_url=u.get("eulaurl") or "",
                changes=changes,
                datatype=u.get("datatype") or "",
                release_state=str(u.get("releasestate") or ""),
            )
        )
    return out


def loses_data_on_update(info) -> bool:
    """Whether Garmin's update server files the device (the answer of unit-info) as a kind that
    loses the data it holds when its software is updated. Only that one field is read."""
    segment = info.get("segmenttype") if isinstance(info, dict) else None
    return isinstance(segment, str) and segment.casefold() in DATA_LOST_SEGMENTS


def beta_offered(response: dict) -> bool:
    """Whether the server offers beta versions to this device: said for the device in the
    answer ("accesslevel"), not for each update (a reinstall of a plain file stays "live")."""
    return "beta" in str(response.get("accesslevel") or "").casefold()


def sort_for_install(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """System updates go last; otherwise in server order."""
    return sorted(updates, key=lambda u: (u.is_system, u.order, u.part_number))


def present_with_metadata(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """The offered files the device already has, each with its -META entry: what 'all' skips."""
    parts = {u.part_number.upper() for u in updates if u.on_device}
    return [u for u in updates if u.part_number.upper().removesuffix("-META") in parts]


def select(updates: list[SoftwareUpdate], wanted: list[str],
           keep_present: bool = False) -> list[SoftwareUpdate]:
    """Select by part number (together with its -META entry) or 'all'.

    'all' leaves out the files the device already has (`present_with_metadata`), unless
    `keep_present`: writing the same file again is a reinstall, and that is asked for by
    its part number or together with the reinstalls."""
    if wanted == ["all"]:
        skipped = [] if keep_present else present_with_metadata(updates)
        return [u for u in updates if not any(u is s for s in skipped)]
    keys = {w.upper() for w in wanted}
    chosen = [u for u in updates if u.part_number.upper() in keys
              or u.part_number.upper().removesuffix("-META") in keys]
    missing = keys - {u.part_number.upper().removesuffix("-META") for u in chosen}
    if missing:
        raise UpdateError(_("Not in the list: {items}").format(items=", ".join(sorted(missing))))
    return chosen


def cache_path(u: SoftwareUpdate) -> Path:
    """cache/software/<part number>/<version>/<file name>, each as the server gives it: a
    part that is no plain name (empty, "..", with a separator) is refused, and so is a path
    that would leave the cache."""
    root = paths.cache_dir() / "software"
    parts = (u.part_number, u.version, u.filename)
    bad = next((part for part in parts if not paths.is_safe_component(part)), None)
    path = root.joinpath(*parts) if bad is None else None
    if path is None or not paths.is_inside(path, root):
        raise UpdateError(_("The server gave a name that cannot be used as a path on this "
                            "computer: '{name}'").format(name=bad if bad is not None else
                                                         "/".join(parts)))
    return path


def check_space(storage: Storage, updates: list[SoftwareUpdate]) -> tuple[int, int | None]:
    """Return (bytes needed, bytes free). A file being replaced counts as freed only where
    it is deleted before the new one is written (MTP); on a drive it stays until the new
    one is whole, so all of the new file must fit beside it."""
    need = 0
    for u in updates:
        existing = 0 if storage.replaces_atomically else storage.size(u.device_path) or 0
        need += max(0, (u.size or 0) - existing)
    return need, storage.free_space()


def space_margin(storage: Storage) -> int:
    """The room kept free on the device besides an update: SPACE_MARGIN, or SPACE_MARGIN_PART
    of the storage when that is less (a small drive); SPACE_MARGIN when its size is not
    known."""
    capacity = storage.capacity()
    if not capacity:
        return SPACE_MARGIN
    return min(SPACE_MARGIN, int(capacity * SPACE_MARGIN_PART))


@dataclass
class InstallReport:
    installed: list[str] = field(default_factory=list)
    restart_required: bool = False


def _install(
    updates: list[SoftwareUpdate],
    storage: Storage,
    session=None,
    progress: Callable[[str], None] | None = None,
) -> InstallReport:
    """`progress`: each update by its name, in bytes ("Downloading … · 1.2 MB of 3.4 MB",
    "Copying … to the device · …"); the files are the server's and the device's own."""
    need, free = check_space(storage, updates)
    margin = space_margin(storage)
    if free is not None and need + margin > free:
        raise UpdateError(_("Not enough space on the device: {need} needed and {margin} kept "
                            "free, but only {free} free")
                          .format(need=human_size(need), margin=human_size(margin),
                                  free=human_size(free)))
    report = InstallReport()
    # Download everything first so the watch is never left with only some of the files.
    local_files: dict[str, Path] = {}
    say = progress if progress is not None else (lambda _message: None)
    for u in updates:
        if not u.url:
            raise UpdateError(_("{part}: the server did not provide a download URL")
                              .format(part=u.part_number))
        log.debug("software update: downloading %s %s (%s)", u.part_number, u.device_path,
                  applog.size(u.size))
        # TRANSLATORS: a step of an update install; {name}: the update ("… · 1.2 MB of 3.4 MB")
        downloading = _("Downloading {name}").format(name=u.title)

        def downloaded(done: int, total: int | None, _head=downloading) -> None:
            say(Step(_head, done, total, in_bytes=True))

        local = download.fetch([u.url], cache_path(u), u.size, u.md5, session=session,
                               progress=downloaded if progress is not None else None)
        got = local.stat().st_size
        say(Step(downloading, got, got, in_bytes=True))  # also when it came from the cache
        with open(local, "rb") as f:
            if f.read(4) == RGN_MAGIC:
                raise UpdateError(
                    _("{part}: the RGN format is not supported by openExpress")
                    .format(part=u.part_number)
                )
        local_files[u.key] = local
    for u in sort_for_install(updates):
        local = local_files[u.key]
        size = local.stat().st_size
        log.debug("software update: copying %s", u.device_path)
        # TRANSLATORS: a step of an update install; {name}: the update ("… · 1.2 MB of 3.4 MB")
        copying = _("Copying {name} to the device").format(name=u.title)

        def written(done: int, total: int | None, _head=copying) -> None:
            say(Step(_head, done, total, in_bytes=True))

        # Only when someone listens: a copy over MTP told nothing goes another way (gio copy
        # without -p in the Flatpak).
        told = written if progress is not None else None
        _clear_the_place(storage, u)  # deletes a file in another letter case, GUPDATE.TMP
        # A failed write is cleaned up by the storage itself (half a file over MTP; nothing
        # on a drive, nor when the copy never started): nothing more is deleted for it here,
        # and what _clear_the_place deleted does not come back.
        # A truncated firmware file must not stay where the watch would install it: one of
        # another size is deleted.
        real = storage.write_file_checked(u.device_path, local, told, error=UpdateError)
        report.installed.append(f"{u.title} {u.version} -> {real}")
        report.restart_required |= u.is_system
        # The whole path from the device's folder, as mark_present names it.
        log.info("software update: %s %s (%s) copied to %s", u.part_number, u.version,
                 applog.size(size), real)
    return report


# Left by a device that takes an update in parts. openExpress deletes it from the folder of
# a system update before writing it, on every device.
LEFTOVER_FILE = "GUPDATE.TMP"


def _clear_the_place(storage: Storage, u: SoftwareUpdate) -> None:
    """Before the file of an update is written, deletes a file at its place whose name differs
    only in letter case, so that the new file has exactly the name the server gives, and for a
    system update a leftover GUPDATE.TMP in its folder: on every device, over MTP and on a
    drive."""
    parent, name = posixpath.split(u.device_path)
    real_parent = storage.resolve(parent) if parent else ""
    if real_parent is None:
        return  # no folder yet: nothing there
    for e in storage.listdir(real_parent):
        if e.is_dir:
            continue
        # An Edge 1050 did not install GUPDATE.GSP written over its own GUPDATE.gsp at once,
        # but later installed that file by itself: the letter case is not known to matter,
        # the exact name is the safer choice.
        other_case = e.name != name and e.name.casefold() == name.casefold()
        leftover = u.is_system and e.name.casefold() == LEFTOVER_FILE.casefold()
        # A file of exactly that name is replaced by the copy itself (on a drive only once
        # the new one is whole).
        if not (other_case or leftover):
            continue
        real = posixpath.join(real_parent, e.name) if real_parent else e.name
        storage.delete(real)
        log.info("software update: %s %s: deleted %s (%s), %s", u.part_number, u.version, real,
                 applog.size(e.size), "its file in another letter case" if other_case
                 else "a leftover of an earlier update")


def install(*args, **kwargs) -> InstallReport:
    """Install software updates (see _install), holding the cache from the download to the
    copy to the device: it is not cleared meanwhile."""
    with cache.using():
        return _install(*args, **kwargs)


