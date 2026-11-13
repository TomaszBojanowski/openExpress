"""Device software updates (firmware and auxiliary files).

Flow as in Garmin Express:
  1. POST /api/device-software/v1/updates with GarminDevice.xml
  2. download `secureDownloadUrl`, check size and MD5
  3. copy to `installation.path/fileName` on the device
  4. unplug the device: it installs the file itself; the confirmation is that
     the update is no longer returned by the next server query.
"""

from __future__ import annotations

import hashlib
import logging
import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from openexpress import answers, applog, cache, download, hidden_folders, known_devices, paths
from openexpress.device.storage import MAIN_FOLDERS, DeviceError, DeviceNotResponding, Storage
from openexpress.errors import UserFacingError
from openexpress.i18n import N_, _, ngettext
from openexpress.progress import (
    Step,
    byte_steps,
    checking_text,
    copying_text,
    downloading_text,
    say_to,
)

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
  1. Unplug the device from the computer.
  2. If the device does not turn on by itself, turn it on.
  3. The device installs the update by itself. It may ask whether to install it:
     choose to install now. It may also restart. Wait until it has finished.
  4. Plug it in again and run `openexpress updates`: the installed update
     should no longer be listed.
""")
# The same for the window, which has no commands and wraps the words itself.
# TRANSLATORS: in the message after an update was copied to the device: one line for each step,
# with no line break inside a sentence (the window wraps the words itself)
FINISH_STEPS_WINDOW = N_("To finish the installation:\n"
                         "1. Unplug the device from the computer.\n"
                         "2. If the device does not turn on by itself, turn it on.\n"
                         "3. The device installs the update by itself. It may ask whether to "
                         "install it: choose to install now. It may also restart. Wait until "
                         "it has finished.\n"
                         "4. Plug it in again and check for updates again: the installed "
                         "update should no longer be listed.")
# Said before an update the device installs once disconnected (installs_when_disconnected),
# on the command line and in the window.
SYSTEM_UPDATE_WARNING = N_("Warning: this is a device system update. Charge the device and do "
                           "not unplug it while copying.")
# The same for the software of a part of the device (its GPS chip), copied with the same care.
PART_UPDATE_WARNING = N_("Warning: this updates the software of a part of the device. Charge the "
                         "device and do not unplug it while copying.")
# The kinds of device (the "segmenttype" of unit-info) that Garmin marks as losing the data
# they hold when their software is updated. Garmin Express holds such an update back while a
# sync of the device runs; openExpress says so before every update and suggests to sync first.
DATA_LOST_SEGMENTS = frozenset({"wellness", "vivomove"})
# Said before every update of such a device: in the window, on the command line, and for a
# device connected as a USB drive that openExpress cannot sync (service.drive_sync_refusal).
DATA_LOSS_WARNING = N_("Warning: installing an update erases the data stored on this device. "
                       "Sync it with Garmin Connect first, or the data not uploaded yet is lost.")
DATA_LOSS_WARNING_CLI = N_("Warning: installing an update erases the data stored on this "
                           "device. Sync it first (openexpress sync --apply), or the data not "
                           "uploaded yet is lost.")
DATA_LOSS_WARNING_DRIVE = N_("Warning: installing an update erases the data stored on this "
                             "device. openExpress cannot sync this device: sync it with the "
                             "Garmin Connect app first, or the data not uploaded yet is lost.")
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
    # From the device's GarminDevice.xml (parse_updates with the device): the part number of
    # its model, and the version of this part number it lists; empty and None: not known.
    model_part_number: str = ""
    installed_version: str | None = None
    # When openExpress copied this very update to this device, while the server still offers
    # it (mark_copied; and right after the copy): the device has not installed it. None: not
    # copied, or not known.
    copied_at: float | None = None
    # The file at its place has the size offered but other bytes (mark_present): after a copy,
    # the device changed it, or refused it.
    changed_on_device: bool = False
    # The device was seen unplugged after openExpress copied it (mark_copied: the device's
    # unplugged_at after copied_at): it had its chance to install it.
    unplugged_since_copy: bool = False
    # The size and time of its file on the device when Free Up Space listed it
    # (freeing.updates_not_installed); None: not listed.
    listed_file: tuple | None = None
    # The server's word whether the device has this product ("isProductInstalled"); None: not
    # said.
    product_installed: bool | None = None
    # The folder of the device's top level its file goes to when the ordinary listing does not
    # show it (mark_present): openExpress never writes there.
    hidden_folder: str | None = None
    # For one with a hidden_folder: what became of reading its folder through openExpress's own
    # USB session on this connection (hidden_folders.Listing.state); None: not tried.
    hidden_session: str | None = None
    # Its file found in the folder read, by name: True at the size offered, False of another
    # size; None: not there, or not read. Its contents are never read.
    hidden_size_same: bool | None = None

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
    def is_offer(self) -> bool:
        """An update the server offers to install: neither a reinstall nor a -META entry."""
        return not self.is_reinstall and not self.is_metadata

    @property
    def is_beta(self) -> bool:
        return "beta" in self.release_state.casefold()

    @property
    def is_system(self) -> bool:
        """An update of the device's own software, the one its "Software" version tells: the
        server calls it primary, it has the part number of the device's model, or it is a
        system file in the device's top folder. A part of the device (its GPS chip) may have
        its software in a system file of another folder (an Instinct 2S: GUPDATE.GCD in
        GARMIN/REMOTESW), with a version of its own."""
        if self.is_primary:
            return True
        if self.model_part_number and \
                self.part_number.casefold() == self.model_part_number.casefold():
            return True
        folder = posixpath.dirname(self.device_path).casefold()
        return self.filename.upper() in SYSTEM_FILES and folder in MAIN_FOLDERS

    @property
    def hidden_read(self) -> bool:
        """Its folder, which ordinary MTP does not show, was read through openExpress's own
        session: what is there is known by name and size."""
        return self.hidden_session == hidden_folders.READ

    @property
    def is_add_on(self) -> bool:
        """Something the device does not have that Garmin offers to add to it (a voice, a
        vehicle of a car navigator): the server says it is not installed, it is not the device's
        own software, and it is below "Recommended", the importance from which Garmin Express
        installs an item by itself. A new part the server recommends stays an update."""
        return (self.product_installed is False and not self.is_system
                and self.severity.casefold() not in ("critical", "recommended"))

    @property
    def installs_when_disconnected(self) -> bool:
        """An update that the device installs itself once it is disconnected, perhaps with a
        restart: the device's own software or the software of a part of it. Copied last, and
        the device must stay connected and charged while it is copied."""
        return (self.is_system or self.restart_required
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
TOP_FOLDER_NOT_SEEN = "its top folder not seen"
SAME_SIZE = "same size, contents not compared"
MD5_DIFFERS = "MD5 differs"


class Presence(NamedTuple):
    """Whether the file of an offered update is on the device (`why`: MATCHES, or why not, in
    words for the log) and the path of the file found at its place (`real`), if any."""

    why: str
    real: str | None = None


# The MD5 of files on the devices read in this process, by (device, path, size, time): a file
# of an update is read whole only once while it stays the same (the page Device Status and the
# Updates page ask again and again; over MTP a big file takes seconds).
_md5_read: dict[tuple, str] = {}


def _md5_of(storage: Storage, real: str, size: int, mtime: float) -> str:
    where = getattr(storage, "device_uri", None) or str(getattr(storage, "root", ""))
    key = (where, real, size, mtime)
    if key in _md5_read:  # never one of a file without a time (below)
        return _md5_read[key]
    digest = hashlib.md5(storage.read(real)).hexdigest()
    if mtime:
        _md5_read[key] = digest
    return digest


def presence(storage: Storage, u: SoftwareUpdate) -> Presence:
    """Whether the file of this update is on the device already (MATCHES: same size and same
    MD5), and if not, why not. The file is looked for in any letter case. Only reads; a file
    read before and still the same (size and time) is not read again. Without a size and an
    MD5 from the server nothing can be said."""
    md5 = download.normalize_md5(u.md5) if u.md5 else None
    if not u.device_path or not u.size or md5 is None:
        return Presence("not compared (no size or MD5 from the server)")
    real = None
    try:
        real = storage.resolve(u.device_path)
        entry = storage.info(real) if real is not None else None
        size = entry.size if entry is not None and not entry.is_dir else None
        if size is None:
            return Presence(NOT_ON_THE_DEVICE)
        if size != u.size:
            return Presence(f"size differs ({applog.size(size)} on the device, "
                            f"{applog.size(u.size)} offered)", real)
        same = _md5_of(storage, real, size, entry.mtime) == md5
        return Presence(MATCHES if same else MD5_DIFFERS, real)
    except DeviceNotResponding:
        raise
    except DeviceError as e:
        log.info("updates: cannot compare %s with the device: %s", u.part_number, e)
        return Presence("not compared (cannot be read)", real)


def presence_by_size(listing: hidden_folders.Listing, u: SoftwareUpdate) -> Presence:
    """Whether the file of this update is in a folder read through openExpress's own session
    (hidden_folders), by its name in any letter case and its size; its contents are not read.
    Sets u.hidden_size_same."""
    entry = listing.file(u.device_path)
    if entry is None:
        return Presence(NOT_ON_THE_DEVICE)
    real = posixpath.join(listing.real(posixpath.dirname(u.device_path)) or "", entry.name)
    if not u.size:
        return Presence("not compared (no size from the server)", real)
    u.hidden_size_same = entry.size == u.size
    if u.hidden_size_same:
        return Presence(SAME_SIZE, real)
    return Presence(f"size differs ({applog.size(entry.size)} on the device, "
                    f"{applog.size(u.size)} offered)", real)


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
    offered = [u for u in updates if u.is_offer]
    tops = top_folders(storage)
    listing = hidden_folders.kept(storage)  # what this connection read of folders not seen
    found = []
    for u in offered:
        u.hidden_folder = top_folder_not_seen(u, tops)
        u.hidden_session = u.hidden_size_same = None
        if u.hidden_folder is None:
            found.append(presence(storage, u))
        else:
            u.hidden_session = (listing.state_of(posixpath.dirname(u.device_path))
                                if listing is not None else None)
            found.append(presence_by_size(listing, u) if u.hidden_read
                         else Presence(TOP_FOLDER_NOT_SEEN))
        # The device's own software at the size offered is not taken for the same file: its
        # contents were not compared.
        u.on_device = found[-1].why == MATCHES or (found[-1].why == SAME_SIZE
                                                   and not u.is_system)
        u.changed_on_device = found[-1].why == MD5_DIFFERS
    # For each: where its file goes on the device, from the device's folder, and whether it
    # is there; never an address (a download address carries a token).
    named = ", ".join(f"{u.part_number} {u.version} at {_where(u, p)}: {p.why}"
                      for u, p in zip(offered[:LOG_ITEMS], found, strict=False))
    if len(offered) > LOG_ITEMS:
        named += f", … and {len(offered) - LOG_ITEMS} more"
    log.info("updates: %d offered%s, %d of them already on the device%s", len(offered),
             f" ({named})" if named else "", sum(1 for u in offered if u.on_device),
             f"; {components} components listed" if components is not None else "")
    hidden = sorted({u.hidden_folder for u in offered if u.hidden_folder})
    extra = [u for u in offered if u.is_add_on]
    if hidden or extra:
        log.info("updates: %d in a top folder not seen (%s), %d add-ons (not installed: %s)",
                 sum(1 for u in offered if u.hidden_folder), ", ".join(hidden) or "none",
                 len(extra), ", ".join(sorted({u.severity or "no importance" for u in extra}))
                 or "none")
    return updates


def top_folders(storage: Storage) -> set[str] | None:
    """The names of the folders of the device's top level that the ordinary listing shows,
    in lower case; None when they cannot be listed (then no folder is taken for unseen)."""
    try:
        return {e.name.casefold() for e in storage.listdir("") if e.is_dir}
    except DeviceNotResponding:
        raise
    except DeviceError:
        return None


def top_folder_not_seen(u: SoftwareUpdate, tops: set[str] | None) -> str | None:
    """The folder of the device's top level the file of `u` goes to when the listing does not
    show it (a car navigator hides its .System from MTP); None when it is seen, when the file
    goes to the top level itself, or when the top level could not be listed. A folder inside
    a folder that is seen is made for the write like any other."""
    parts = [p for p in u.device_path.split("/") if p]
    if len(parts) < 2 or tops is None:
        return None
    return None if parts[0].casefold() in tops else parts[0]


def hidden_needed(updates: list[SoftwareUpdate]) -> list[str]:
    """The folders the offered updates go to whose folder of the top level ordinary MTP does not
    show, and that this connection has not tried to read yet (hidden_folders.read), each once."""
    out: dict[str, str] = {}
    for u in updates:
        if u.is_offer and u.hidden_folder is not None and u.hidden_session is None:
            folder = posixpath.dirname(u.device_path)
            out.setdefault(folder.casefold(), folder)
    return sorted(out.values(), key=str.casefold)


def not_installed_here_text(u: SoftwareUpdate) -> str:
    if u.hidden_read:
        # TRANSLATORS: `openexpress updates`: an update openExpress does not install yet; its
        # folder ({folder}, e.g. .System) the device hides, and openExpress only reads it
        return _("Not installed yet: {name} {version} goes to {folder}, which openExpress only "
                 "reads.").format(name=u.title, version=u.version, folder=u.hidden_folder)
    # TRANSLATORS: `openexpress updates`: an update openExpress does not install; {folder}: the
    # folder of the device's top level its file goes to, e.g. .System
    return _("Not installed: {name} {version} goes to {folder}, which openExpress cannot see "
             "on the device.").format(name=u.title, version=u.version, folder=u.hidden_folder)


def hidden_software_text(folder: str) -> str:
    # TRANSLATORS: `openexpress updates`: the device keeps its software in a folder openExpress
    # cannot see over MTP; {folder}: e.g. .System
    return _("openExpress cannot see the folder {folder}, where this device keeps its software, "
             "so it installs nothing there. Some devices, such as car navigators, hide it from "
             "the ordinary USB connection.").format(folder=folder)


def read_only_software_text(folder: str) -> str:
    # TRANSLATORS: `openexpress updates`: the device keeps its software in a folder hidden from
    # the ordinary USB connection ({folder}, e.g. .System), which openExpress reads through a
    # USB session of its own
    return _("openExpress reads the folder {folder} through its own USB connection, but does "
             "not install into it yet.").format(folder=folder)


def other_size_text(u: SoftwareUpdate) -> str:
    # TRANSLATORS: `openexpress updates`, under an update: a file of its name is on the device,
    # of another size than the one offered, which is only noted; {file}: e.g. B444900.update
    return _("The device already has a file named {file}, of another size than the new one; no "
             "action is needed.").format(file=u.filename)


def same_size_text(u: SoftwareUpdate) -> str:
    # TRANSLATORS: `openexpress updates`: a file of the update's name in a folder the device
    # hides, found by name and size only; {file}: e.g. gmaptz.img
    return _("The device already has a file named {file} of the same size; openExpress does not "
             "read its contents to compare them.").format(file=u.filename)


def not_read_text(u: SoftwareUpdate) -> str | None:
    """Why the hidden folder of `u` could not be read; None when it was, or was not tried."""
    if u.hidden_folder is None or u.hidden_session in (None, hidden_folders.READ):
        return None
    return hidden_folders.no_session_text(u.hidden_folder, u.hidden_session)


def add_ons_text(count: int) -> str:
    # TRANSLATORS: `openexpress updates`: items Garmin offers to add to the device (voices,
    # vehicles) that it does not have
    return ngettext("{count} add-on not on the device is not offered: openExpress does not "
                    "install add-ons yet.",
                    "{count} add-ons not on the device are not offered: openExpress does not "
                    "install add-ons yet.", count).format(count=count)


def installable(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """Those openExpress may write: none whose top folder it cannot see."""
    return [u for u in updates if u.hidden_folder is None]


def add_ons(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """The add-ons offered (is_add_on): not updates, and not installed by openExpress yet."""
    return [u for u in updates if u.is_offer and u.is_add_on and not u.on_device]


def staged(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """Updates copied already that wait for the device to take them in once disconnected:
    the device's own software or a part's (installs_when_disconnected), and a component that
    openExpress copied (copied_at). Two facts, no guess: the server still offers the update
    (not as a reinstall), so the device has not installed it, and the very same file (size
    and MD5) is on the device. The file of a component there without such a copy is no update
    (mark_present). One the device had its chance to install is not_installed instead."""
    return [u for u in updates if u.on_device and u.is_offer
            and (u.installs_when_disconnected or u.copied_at is not None)
            and not u.unplugged_since_copy]


def pending(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """The real updates: not reinstalls, not metadata, not on the device already, and not
    copied before and not installed by the device (not_installed)."""
    return [u for u in updates if u.is_offer and not u.on_device and not _not_installed(u)
            and not u.is_add_on]


def not_installed(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """Updates openExpress copied that the device has not installed although it was unplugged
    since (seen: unplugged_since_copy): the server still offers them, not as reinstalls, and
    the file at their place is as copied or changed in place by the device. Nothing waits for
    them: copying the same file again changes nothing."""
    return [u for u in updates if _not_installed(u)]


def _not_installed(u: SoftwareUpdate) -> bool:
    return (u.copied_at is not None and u.unplugged_since_copy and u.is_offer
            and (u.on_device or u.changed_on_device))


def state(new: list, staged: list, not_installed: list) -> str:
    """The state of a device's updates from one answer of the server (its pending, staged and
    not_installed): "ready" (copied, waiting for the device to install it once disconnected),
    which wins over updates still to install; "available"; "not-installed" (copied, not
    installed by the device although it was unplugged since); "current". The window and
    `openexpress status` tell this one state, each in its own words."""
    return ("ready" if staged else "available" if new
            else "not-installed" if not_installed else "current")


def offered(storage: Storage, response: dict, device,
            forget_installed: bool = True) -> list[SoftwareUpdate]:
    """What the update server's `response` offers `device` (its GarminDevice.xml), each
    compared with the device's storage (mark_present) and with what openExpress copied to it
    before (mark_copied; `forget_installed`: False for a check that keeps nothing). A test
    drive carries a real device's unit ID: what was copied to that device is not its."""
    unit = None if _test_drive(storage) else getattr(device, "unit_id", None)
    return mark_copied(mark_present(storage, parse_updates(response, device),
                                    component_count(device)), unit, forget_installed)


def _test_drive(storage) -> bool:
    return bool(getattr(storage, "test_drive", False))


def mark_copied(updates: list[SoftwareUpdate], unit_id: int | None,
                forget_installed: bool = True) -> list[SoftwareUpdate]:
    """The updates openExpress copied to this device before that the server still offers
    (not as reinstalls): when each was copied (known_devices), each said in the log, with
    whether the file at its place changed since (the same size, other bytes: the device
    changed or refused it). What the server no longer offers is forgotten (the device
    installed it), unless not `forget_installed`. Optional: a failure is only written to the
    log."""
    if not unit_id:
        return updates
    offered_now = {u.key for u in updates if u.is_offer}
    try:
        entry = known_devices.load().get(unit_id)
        copied = dict(entry.copied_updates) if entry is not None else {}
        unplugged = entry.unplugged_at if entry is not None else None
        if forget_installed and set(copied) - offered_now:
            known_devices.keep_copied(unit_id, offered_now)
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("updates: what was copied before not read (%s)", type(e).__name__)
        return updates
    for u in updates:
        at = copied.get(u.key)
        if at is None or u.key not in offered_now:
            continue
        u.copied_at = at
        u.unplugged_since_copy = unplugged is not None and unplugged > at
        when = datetime.fromtimestamp(at).isoformat(timespec="minutes")
        if _not_installed(u):
            log.info("software update: %s %s copied %s, on the device after an unplugging "
                     "(%s): not installed by the device", u.part_number, u.version, when,
                     MATCHES if u.on_device else "other bytes")
        elif not u.on_device:
            log.info("software update: %s %s copied %s is offered again%s", u.part_number,
                     u.version, when,
                     "; the file at its place has other bytes of the same size: the device "
                     "changed or refused it" if u.changed_on_device else "")
    return updates


def parse_updates(response: dict, device=None) -> list[SoftwareUpdate]:
    """The updates of the server's answer. With `device` (its GarminDevice.xml): which of them
    is its own software (its model's part number) and the version of each part it lists; for
    its own software without one, the version of the device's software."""
    model = getattr(device, "part_number", "") or ""
    listed = {f.part_number.casefold(): f.version
              for f in getattr(device, "update_files", None) or []}
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
                model_part_number=model,
                product_installed=(inst.get("isproductinstalled")
                                   if isinstance(inst.get("isproductinstalled"), bool) else None),
            )
        )
        new = out[-1]
        new.installed_version = (listed.get(new.part_number.casefold())
                                 or the_device_version(new, device))
    return out


def the_device_version(u: SoftwareUpdate, device) -> str | None:
    """The version of `device`'s software, when it is the version of update `u` too: `u` is
    the device's software by the server's word, the model's part number (`u`'s, or else the
    device's), or as the bundle of its software (GUPDATE.GSP). A system file of another part
    number, even in the top folder, may be a part's, with a version of its own: None."""
    software = getattr(device, "software_version_str", None)
    if not isinstance(software, str) or not u.is_system:
        return None
    model = u.model_part_number or getattr(device, "part_number", "") or ""
    if (u.is_primary or u.filename.upper() == "GUPDATE.GSP"
            or (model and u.part_number.casefold() == model.casefold())):
        return software
    return None


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
    """What the device installs once disconnected goes last; otherwise in server order."""
    return sorted(updates, key=lambda u: (u.installs_when_disconnected, u.order, u.part_number))


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
                            "computer: “{name}”").format(name=bad if bad is not None else
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
    # The updates written to the device, their -META entries too.
    written: list[SoftwareUpdate] = field(default_factory=list)

    @property
    def copied(self) -> list[str]:
        """The keys (SoftwareUpdate.key) of the updates written, without -META entries."""
        return [u.key for u in self.written if not u.is_metadata]


def _copied_first(u: SoftwareUpdate) -> tuple:
    """Which of the updates for one file is copied, as Garmin Express orders them: a new one
    before a reinstall, the device's software first, then what the device installs once
    disconnected, then in the server's order."""
    return (u.is_reinstall, not u.is_primary, not u.is_system, not u.installs_when_disconnected,
            u.order, u.part_number)


def copy_warnings(updates: list[SoftwareUpdate]) -> list[str]:
    """The warning before these updates are copied, if any (an untranslated constant: show it
    with _()): of the device's system when one is its software, else of a part of the device
    when the device installs one once disconnected."""
    if any(u.is_system for u in updates):
        return [SYSTEM_UPDATE_WARNING]
    if any(u.installs_when_disconnected for u in updates):
        return [PART_UPDATE_WARNING]
    return []


def one_per_file(updates: list[SoftwareUpdate], waiting: list[SoftwareUpdate] = (),
                 say: bool = True
                 ) -> tuple[list[SoftwareUpdate], list[tuple[SoftwareUpdate, SoftwareUpdate]]]:
    """At most one update for each file of the device in one copy: copied together, the next
    one would replace the first before the device installed it. `waiting`: updates copied
    already that wait in their file for the device (staged): their files are taken. Returns
    the updates to copy and the others, each with the one at its file instead, each said in
    the log (unless not `say`: nothing is copied yet); the -META entries of a part put off
    go with it. A file is told by its path in any letter case."""
    first: dict[str, SoftwareUpdate] = {w.device_path.casefold(): w for w in waiting}
    put_off = []
    for u in sorted(updates, key=_copied_first):
        if u.is_metadata:
            continue
        place = u.device_path.casefold()
        if place in first and first[place] is not u:
            put_off.append((u, first[place]))
        else:
            first[place] = u
    copied = {u.part_number.upper() for u in updates
              if not u.is_metadata and not any(u is p for p, _other in put_off)}
    kept = [u for u in updates if not any(u is p for p, _other in put_off)
            and (not u.is_metadata or u.part_number.upper().removesuffix("-META") in copied)]
    for u, other in put_off if say else ():
        log.info("software update: %s %s put off, it goes to the file of %s %s (%s): one at a "
                 "time", u.part_number, u.version, other.part_number, other.version,
                 u.device_path)
    return kept, put_off


def _install(
    updates: list[SoftwareUpdate],
    storage: Storage,
    session=None,
    progress: Callable[[str], None] | None = None,
    *,
    report: InstallReport,
) -> InstallReport:
    """`progress`: each update by its name, in bytes ("Downloading … · 1.2 MB of 3.4 MB",
    "Copying … to the device · …"); the files are the server's and the device's own. Two
    updates for one file: one of them is copied (one_per_file). `report` is filled as it goes:
    what was written before a failure is in it."""
    updates = one_per_file(updates)[0]  # the others are told in the log
    # Never a folder of the device's top level: one the listing does not show may be hidden
    # (a car navigator's .System), and nothing is downloaded for an update refused.
    tops = top_folders(storage)
    for u in updates:
        u.hidden_folder = top_folder_not_seen(u, tops)
        if u.hidden_folder is not None:
            log.info("software update: %s %s not installed: its top folder %s is not seen",
                     u.part_number, u.version, u.hidden_folder)
            raise UpdateError(not_installed_here_text(u))
    need, free = check_space(storage, updates)
    storage.require_space(need, space_margin(storage), error=UpdateError, free=free,
                          margin_said=True)
    # Download everything first so the watch is never left with only some of the files.
    local_files: dict[str, Path] = {}
    say = say_to(progress)
    for u in updates:
        if not u.url:
            raise UpdateError(_("{part}: the server did not provide a download URL")
                              .format(part=u.part_number))
        log.debug("software update: downloading %s %s (%s)", u.part_number, u.device_path,
                  applog.size(u.size))
        downloading = downloading_text(u.title)
        local = download.fetch([u.url], cache_path(u), u.size, u.md5, session=session,
                               progress=byte_steps(progress, downloading))
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
        copying = copying_text(u.title)
        _clear_the_place(storage, u)  # deletes a file in another letter case, GUPDATE.TMP
        # A failed write is cleaned up by the storage itself (half a file over MTP; nothing
        # on a drive, nor when the copy never started): nothing more is deleted for it here,
        # and what _clear_the_place deleted does not come back.
        # A truncated firmware file must not stay where the watch would install it: one of
        # another size is deleted.
        # On a drive, read back from it (a step of its own) and compared with the server's
        # MD5 before it counts.
        real = storage.write_file_checked(
            u.device_path, local, byte_steps(progress, copying), error=UpdateError,
            md5=download.normalize_md5(u.md5),
            checking=byte_steps(progress, checking_text(u.title)))
        report.written.append(u)
        # The whole path from the device's folder, as mark_present names it.
        log.info("software update: %s %s (%s) copied to %s", u.part_number, u.version,
                 applog.size(size), real)
    return report


# Left by a device that takes an update in parts. openExpress deletes it from the folder of
# an update the device installs once disconnected before writing it, on every device.
LEFTOVER_FILE = "GUPDATE.TMP"


def _clear_the_place(storage: Storage, u: SoftwareUpdate) -> None:
    """Before the file of an update is written, deletes a file at its place whose name differs
    only in letter case, so that the new file has exactly the name the server gives, and for an
    update the device installs once disconnected a leftover GUPDATE.TMP in its folder: on
    every device, over MTP and on a drive."""
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
        leftover = (u.installs_when_disconnected
                    and e.name.casefold() == LEFTOVER_FILE.casefold())
        # A file of exactly that name is replaced by the copy itself (on a drive only once
        # the new one is whole).
        if not (other_case or leftover):
            continue
        real = posixpath.join(real_parent, e.name) if real_parent else e.name
        storage.delete(real)
        log.info("software update: %s %s: deleted %s (%s), %s", u.part_number, u.version, real,
                 applog.size(e.size), "its file in another letter case" if other_case
                 else "a leftover of an earlier update")


def choice_text(offered: list[SoftwareUpdate], chosen: list[SoftwareUpdate]) -> str:
    """For the log before updates are copied: how many of the `offered` were chosen and which,
    the others offered, and those the program left out, with why: a reinstall, on the device
    already, or put off because another one chosen (or one copied that waits) takes its file
    (one_per_file). The -META entries go unsaid; at most LOG_ITEMS named in each list."""
    real = [u for u in offered if not u.is_metadata]
    picked = [u for u in real if any(u is c for c in chosen)]
    takers = {u.device_path.casefold(): u for u in real
              if any(u is p for p in picked) or (u.on_device and u.installs_when_disconnected
                                                and not u.is_reinstall)}
    others, left = [], []
    for u in real:
        if any(u is p for p in picked):
            continue
        taker = takers.get(u.device_path.casefold())
        if u.is_reinstall:
            left.append((u, "a reinstall"))
        elif u.on_device:
            left.append((u, "on the device already"))
        elif taker is not None and taker is not u:
            left.append((u, f"put off: the same file as {taker.part_number}"))
        else:
            others.append(u)

    def named(items: list[str]) -> str:
        more = [f"… and {len(items) - LOG_ITEMS} more"] if len(items) > LOG_ITEMS else []
        return ", ".join(items[:LOG_ITEMS] + more)

    text = f"{len(picked)} of {len(real)} offered chosen ("
    text += named([f"{u.part_number} {u.version}" for u in picked]) + ")"
    if others:
        text += "; not chosen: " + named([f"{u.part_number} {u.version}" for u in others])
    if left:
        text += "; left out: " + named([f"{u.part_number} {u.version} ({why})" for u, why in left])
    return text


def install(*args, unit_id: int | None = None, offered: list[SoftwareUpdate] | None = None,
            **kwargs) -> InstallReport:
    """Install software updates (see _install), holding the cache from the download to the
    copy to the device: it is not cleared meanwhile. What Garmin's server offers the device
    `unit_id` is asked anew next time (answers), also after a copy that failed (a part may
    be there); what was copied is kept with the device (known_devices), until the server
    offers it no more (mark_copied). `offered`: all the server offered, for the line of the
    log that says what was chosen of it (choice_text)."""
    if offered is not None and args:
        log.info("software update: %s", choice_text(offered, args[0]))
    report = InstallReport()
    try:
        with cache.using():
            return _install(*args, report=report, **kwargs)
    finally:
        answers.forget_device(answers.SOFTWARE_UPDATES, unit_id)
        # Also what was written before a failure; never a test drive's, which carries a real
        # device's unit ID.
        storage = next((a for a in (*args, *kwargs.values()) if isinstance(a, Storage)), None)
        if unit_id and report.copied and not _test_drive(storage):
            try:
                known_devices.note_copied(unit_id, report.copied)
            except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
                log.warning("updates: what was copied not kept (%s)", type(e).__name__)


