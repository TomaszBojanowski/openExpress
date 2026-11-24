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
import json
import logging
import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from openexpress import (
    answers,
    applog,
    awake,
    cache,
    download,
    hidden_folders,
    known_devices,
    paths,
)
from openexpress.device import whole_reads
from openexpress.device.storage import (
    MAIN_FOLDERS,
    DeviceError,
    DeviceNotResponding,
    Storage,
    connection,
    device_place,
    is_test_drive,
)
from openexpress.errors import UserFacingError
from openexpress.i18n import N_, _, ngettext, pgettext
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
# The same after a reinstall: the version on the device stays, so it does not leave the list.
FINISH_STEPS_REINSTALL = N_("""
To finish the installation:
  1. Unplug the device from the computer.
  2. If the device does not turn on by itself, turn it on.
  3. The device installs the update by itself. It may ask whether to install it:
     choose to install now. It may also restart. Wait until it has finished.
  4. Plug it in again if you want to check it: the software version stays
     the same.
""")
# TRANSLATORS: in the message after a component already on the device was copied to it again:
# one line for each step, with no line break inside a sentence (the window wraps the words)
FINISH_STEPS_REINSTALL_WINDOW = N_("To finish the installation:\n"
                                   "1. Unplug the device from the computer.\n"
                                   "2. If the device does not turn on by itself, turn it on.\n"
                                   "3. The device installs the update by itself. It may ask "
                                   "whether to install it: choose to install now. It may also "
                                   "restart. Wait until it has finished.\n"
                                   "4. Plug it in again if you want to check it: the software "
                                   "version stays the same.")


def finish_steps(written: list, window: bool = False) -> str:
    """The steps after updates were copied (an untranslated constant: show it with _()), those
    of a reinstall when all `written` are reinstalls: then the same version stays on the device
    and the server goes on listing it, so the steps do not promise it leaves the list."""
    reinstall = bool(written) and all(u.is_reinstall for u in written)
    if window:
        return FINISH_STEPS_REINSTALL_WINDOW if reinstall else FINISH_STEPS_WINDOW
    return FINISH_STEPS_REINSTALL if reinstall else FINISH_STEPS


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
    # show it (mark_present): openExpress writes there only through its own session, once that
    # session has read the folder.
    hidden_folder: str | None = None
    # For one with a hidden_folder: what became of reading its folder through openExpress's own
    # USB session on this connection (hidden_folders.Listing.state); None: not tried.
    hidden_session: str | None = None
    # Its file found in the folder read, by name: True at the size offered, False of another
    # size; None: not there, or not read. Its contents are never read.
    hidden_size_same: bool | None = None
    # The folder its file goes to, as the device names it, when that folder is inside a folder
    # the listing shows, the device names it for its own components (GarminDevice.xml), and the
    # listing does not show it: the device hides it (mark_present, hidden_inside). Written there
    # through openExpress's own session where one can be had (folder_writable), else refused
    # before anything is downloaded (refused_inside).
    hidden_inside: str | None = None
    folder_writable: bool = False
    # Whether the device hides folders its GarminDevice.xml names inside folders that are seen
    # (mark_present: hidden_inside found some): a device that hides something where updates go.
    device_hides: bool = False
    # An offer whose file the file manager does not show, in a folder this connection has not
    # read through openExpress's own session (mark_present): session_folders reads it where it
    # can. Its file found there (file_hidden): the device hides it; it is then known by name
    # and size only (hidden_size_same), as one in a folder the device hides.
    file_unseen: bool = False
    file_hidden: bool = False
    # A reinstall whose version on the device (GarminDevice.xml) is higher than the one offered
    # (offers_older): whether its file is the offered one (mark_present) - OLDER_SAME_FILE,
    # OLDER_SAME_SIZE, OLDER_DIFFERS or OLDER_NOT_COMPARED; None for every other update.
    older_offer: str | None = None

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
    def part_key(self) -> str:
        """The part number in capitals without "-META": an update and its -META entry share
        it (is_metadata tells them apart, by the part number as the server gives it)."""
        return self.part_number.upper().removesuffix("-META")

    @property
    def refused_inside(self) -> bool:
        """In a folder the device hides inside a folder that is seen, where openExpress's own
        session cannot be had (the Flatpak without USB, a device not over MTP): not installed."""
        return self.hidden_inside is not None and not self.folder_writable

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
    def hidden_unread(self) -> bool:
        """Its file goes to a folder of the top level ordinary MTP does not show that this
        connection has not read through openExpress's own session: it is not installed."""
        return self.hidden_folder is not None and not self.hidden_read

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

# A reinstall of an older version than the device lists for it (older_offer): its file on the
# device is the offered one (the same size and MD5), of its size (a folder the device hides: its
# contents are not read), another file (an older version: never written over it), or could not
# be compared (its file not there, its folder not read, no size or MD5 from the server: not
# written either, as writing an older version without knowing it is the same file risks a
# component the device has newer).
OLDER_SAME_FILE = "the same file"
OLDER_SAME_SIZE = "the same size"
OLDER_DIFFERS = "another file"
OLDER_NOT_COMPARED = "not compared"
MD5_DIFFERS = "MD5 differs"
SIZE_DIFFERS = "size differs"  # followed by both sizes


class Presence(NamedTuple):
    """Whether the file of an offered update is on the device (`why`: MATCHES, or why not, in
    words for the log) and the path of the file found at its place (`real`), if any."""

    why: str
    real: str | None = None


# The MD5 of files on the devices read in this process, by (device, path, size, time): a file
# of an update is read whole only once while it stays the same (the page Device Status and the
# Updates page ask again and again; over MTP a big file takes seconds).
_md5_read: dict[tuple, str] = {}
# The same, kept for the next window or command (paths.cache_dir()): a file read once, or
# written by openExpress itself (remember_written), is not read again while its size and its
# time on the device stay; the device by a hash of its address (which carries its serial).
MD5_KEPT = "md5-on-device.json"
MD5_KEPT_AT_MOST = 300
# How much earlier the time the device gives a file openExpress wrote may be than the time it
# gave right after the write, the file still taken for the one written (its size the same, its
# MD5 the server's): a device keeps times in steps of 2 s, rounded down, as a fēnix 8 does (a file
# written at 12:00:01.6 has the time 12:00:00 once plugged in again). A later time is another write
# at that place; a file read whole is known at its very time only.
WRITTEN_TIME_SLACK_S = 2


def _md5_key(storage: Storage, real: str, size: int, mtime: float) -> str:
    where = device_place(storage)
    return "|".join((hashlib.sha256(where.encode()).hexdigest()[:32], real, str(size),
                     repr(float(mtime))))


def _written_key(storage: Storage, real: str) -> str:
    """The key of what openExpress last wrote at `real` on this device: one a file."""
    where = device_place(storage)
    return "|".join(("written", hashlib.sha256(where.encode()).hexdigest()[:32], real))


def _kept_md5() -> dict:
    """What md5-on-device.json keeps: a file read whole by its _md5_key, its MD5; a file
    openExpress wrote by its _written_key, its size, its time right after the write and the
    server's MD5."""
    try:
        kept = json.loads((paths.cache_dir() / MD5_KEPT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return kept if isinstance(kept, dict) else {}


def _keep_md5(key: str, value) -> None:
    kept = _kept_md5()
    kept.pop(key, None)
    kept[key] = value
    path = paths.cache_dir() / MD5_KEPT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(list(kept.items())[-MD5_KEPT_AT_MOST:])),
                       encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        log.info("updates: the checksum of a file on the device not kept (%s)",
                 type(e).__name__)


def remember_written(storage: Storage, real: str, md5: str | None) -> None:
    """The file openExpress has just written at `real` is the one with this MD5 (the server's,
    checked when it was downloaded): kept with its size and its time on the device now, in the
    place of what was kept of an earlier write there, so that the next check does not read it
    back (_md5_of), also when the device gives its time a little apart later."""
    if not md5:
        return
    try:
        entry = storage.info(real)
    except DeviceNotResponding:
        raise
    except DeviceError:
        return
    if entry is None or entry.is_dir or not entry.mtime:
        return
    digest = md5.casefold()
    where = device_place(storage)
    _md5_read[(where, real, entry.size, entry.mtime)] = digest
    _keep_md5(_written_key(storage, real),
              {"size": entry.size, "mtime": float(entry.mtime), "md5": digest})


def checking_file_text() -> str:
    # TRANSLATORS: a step of the Updates page: a file on the device of the size Garmin offers is
    # read whole to compare it with the server's ("… · 12.3 MB of 61.8 MB"); short, so that the
    # numbers fit the bar of the narrowest window
    return _("Checking the update file")


def _md5_of(storage: Storage, real: str, size: int, mtime: float, told=None) -> str:
    where = device_place(storage)
    key = (where, real, size, mtime)
    if key in _md5_read:  # never one of a file without a time (below)
        return _md5_read[key]
    if mtime:
        kept_all = _kept_md5()
        kept = kept_all.get(_md5_key(storage, real, size, mtime))
        if isinstance(kept, str) and kept:
            log.info("updates: %s known from before (its size and time the same), not read",
                     posixpath.basename(real))
            _md5_read[key] = kept
            return kept
        known = _known_from_our_write(kept_all.get(_written_key(storage, real)), real, size,
                                      mtime)
        if known:
            _md5_read[key] = known
            return known
    digest = hashlib.md5(whole_reads.whole_file(
        storage, real, size, lambda: storage.read_at(real, told=told), told)).hexdigest()
    if mtime:
        _md5_read[key] = digest
        _keep_md5(_md5_key(storage, real, size, mtime), digest)
    return digest


def _known_from_our_write(written, real: str, size: int, mtime: float) -> str | None:
    """The server's MD5 of the file openExpress wrote at `real`, when it is still that file: the
    same size, its time the time right after the write or at most WRITTEN_TIME_SLACK_S earlier.
    Said in the log when the time is another; None when it is not that file (read whole then)."""
    if not (isinstance(written, dict) and written.get("size") == size
            and isinstance(written.get("md5"), str) and written.get("mtime")):
        return None
    name = posixpath.basename(real)
    then = float(written["mtime"])
    if mtime == then:
        log.info("updates: %s known from before (its size and time the same), not read", name)
        return written["md5"]
    times = (f"updates: {name} written by openExpress with the time "
             f"{datetime.fromtimestamp(then):%H:%M:%S}, the device gives "
             f"{datetime.fromtimestamp(mtime):%H:%M:%S} now "
             f"({abs(mtime - then):.3g} s {'earlier' if mtime < then else 'later'}")
    if then - WRITTEN_TIME_SLACK_S <= mtime < then:
        log.info("%s, within %g s): known from that write, not read", times,
                 WRITTEN_TIME_SLACK_S)
        return written["md5"]
    log.info("%s): read again", times)
    return None


def presence(storage: Storage, u: SoftwareUpdate, progress=None) -> Presence:
    """Whether the file of this update is on the device already (MATCHES: same size and same
    MD5), and if not, why not. The file is looked for in any letter case. Only reads; a file
    read before, or written by openExpress, and still the same (size and time) is not read
    again; one that is read tells how far to `progress`. Without a size and an MD5 from the
    server nothing can be said."""
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
            return _size_differs(size, u, real)
        same = _md5_of(storage, real, size, entry.mtime,
                       byte_steps(progress, checking_file_text(), total=size)) == md5
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
    return _size_differs(entry.size, u, real)


def _size_differs(on_device: int, u: SoftwareUpdate, real) -> Presence:
    return Presence(f"{SIZE_DIFFERS} ({applog.size(on_device)} on the device, "
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


def version_number(text: str | None) -> tuple[int, int] | None:
    """A version as numbers (major, minor), however Garmin writes it: GarminDevice.xml and the
    server's displayVersion "2.04", its semanticVersion "2.4.0" (the minor part without its zero,
    a third part that does not count); None when it is no such version."""
    parts = (text or "").strip().split(".")
    if len(parts) < 2 or not (parts[0].isdecimal() and parts[1].isdecimal()):
        return None
    return int(parts[0]), int(parts[1])


def plain_version(text: str) -> str:
    """A version as a device writes it ("2.20" for the server's "2.20.0"); as it is when it is
    no such version."""
    number = version_number(text)
    return f"{number[0]}.{number[1]:02d}" if number is not None else text


def offers_older(u: SoftwareUpdate) -> bool:
    """Whether `u` is a reinstall of a version lower than the one the device lists for it."""
    on_device, offered = version_number(u.installed_version), version_number(u.version)
    return (u.is_reinstall and on_device is not None and offered is not None
            and on_device > offered)


def reinstall_refused(u: SoftwareUpdate) -> bool:
    """An older version than the device's that is not the same file as the device's: it is
    never written (the window shows no button, the command line and install refuse it)."""
    return u.older_offer in (OLDER_DIFFERS, OLDER_NOT_COMPARED)


def _older_offer(storage: Storage, listing, u: SoftwareUpdate) -> str:
    """Whether the file of `u`, an older version than the device's, is the device's file: by
    its size and MD5 in a folder ordinary MTP shows (read once while it stays the same), by its
    size in a folder read through openExpress's own session."""
    if u.hidden_folder is not None:
        if not u.hidden_read:
            return OLDER_NOT_COMPARED
        found = presence_by_size(listing, u)
        if found.why == SAME_SIZE:
            return OLDER_SAME_SIZE
        return OLDER_DIFFERS if found.why.startswith(SIZE_DIFFERS) else OLDER_NOT_COMPARED
    found = presence(storage, u)
    if found.why == MATCHES:
        return OLDER_SAME_FILE
    if found.why == MD5_DIFFERS or found.why.startswith(SIZE_DIFFERS):
        return OLDER_DIFFERS
    return OLDER_NOT_COMPARED


def older_offer_text(u) -> str | None:
    """What the command line says of a reinstall of an older version than the device's (an
    error too: install refuses it); None for any other update."""
    words = {
        # TRANSLATORS: `openexpress updates --reinstalls`, under a component whose version on
        # the device ({on_device}) is higher than the one Garmin offers ({offered}), while the
        # file on the device is the very file offered
        OLDER_SAME_FILE: _("On the device {on_device}, Garmin offers {offered}: the same file."),
        # TRANSLATORS: `openexpress updates --reinstalls`, under a component whose version on
        # the device ({on_device}) is higher than the one Garmin offers ({offered}); its file is
        # in a folder the device hides, so only its size was compared
        OLDER_SAME_SIZE: _("On the device {on_device}, Garmin offers {offered}: the same size, "
                           "its contents not compared."),
        # TRANSLATORS: `openexpress updates --reinstalls`, under a component whose version on
        # the device ({on_device}) is higher than the one Garmin offers ({offered}), while the
        # file on the device is another: an older version, which openExpress never writes over
        # the device's
        OLDER_DIFFERS: _("On the device {on_device}, Garmin offers {offered}, an older version: "
                         "not reinstalled."),
        # TRANSLATORS: `openexpress updates --reinstalls`, under a component whose version on
        # the device ({on_device}) is higher than the one Garmin offers ({offered}), while its
        # file could not be compared with the one offered (not on the device, its folder not read)
        OLDER_NOT_COMPARED: _("On the device {on_device}, Garmin offers {offered}, an older "
                              "version; the file on the device could not be compared: not "
                              "reinstalled."),
    }.get(u.older_offer)
    if words is None:
        return None
    return words.format(on_device=u.installed_version, offered=plain_version(u.version))


def older_offer_caption(u) -> str | None:
    """The same in the row of the component in the window, a caption without a full stop;
    None for any other update."""
    words = {
        # TRANSLATORS: Updates page, in the row of a component whose version on the device
        # ({on_device}) is higher than the one Garmin offers ({offered}), while the file on the
        # device is the very file offered; no full stop
        OLDER_SAME_FILE: _("On the device {on_device} · Garmin offers {offered}, the same file"),
        # TRANSLATORS: Updates page, in the row of a component whose version on the device
        # ({on_device}) is higher than the one Garmin offers ({offered}); its file is in a folder
        # the device hides, so only its size was compared; no full stop
        OLDER_SAME_SIZE: _("On the device {on_device} · Garmin offers {offered}, the same "
                           "size"),
        # TRANSLATORS: Updates page, in the row of a component whose version on the device
        # ({on_device}) is higher than the one Garmin offers ({offered}), while the file on the
        # device is another: an older version, which openExpress does not reinstall (the row has
        # no button); no full stop
        OLDER_DIFFERS: _("On the device {on_device} · Garmin offers {offered}, an older "
                         "version"),
        # TRANSLATORS: Updates page, in the row of a component whose version on the device
        # ({on_device}) is higher than the one Garmin offers ({offered}), while its file could
        # not be compared with the one offered; no full stop
        OLDER_NOT_COMPARED: _("On the device {on_device} · Garmin offers {offered}, an older "
                              "version; the file on the device could not be compared"),
    }.get(u.older_offer)
    if words is None:
        return None
    return words.format(on_device=u.installed_version, offered=plain_version(u.version))


def mark_present(storage: Storage, updates: list[SoftwareUpdate],
                 components: int | None = None,
                 inside: dict[str, str] | None = None,
                 writable: bool = False, progress=None) -> list[SoftwareUpdate]:
    """Find the offered updates whose file the device already has (e.g. map support files
    it briefly does not list right after it is plugged in). They are not updates.

    `components`: how many components the device's GarminDevice.xml lists, for the log;
    `inside`: the folders the device hides inside a folder that is seen (hidden_inside), written
    through openExpress's own session when it is `writable` (session_possible); `progress`:
    how far a file read whole to be compared is."""
    offered = [u for u in updates if u.is_offer]
    tops = hidden_folders.top_folders(storage)
    listing = hidden_folders.kept(storage)  # what this connection read of folders not seen
    for u in updates:
        place(u, tops, listing)
        u.hidden_inside = (inside or {}).get(
            posixpath.dirname(u.device_path.replace("\\", "/")).strip("/").casefold())
        u.folder_writable = u.hidden_inside is not None and writable
        u.device_hides = bool(inside)
        u.file_unseen = u.file_hidden = False  # compared anew below
        u.hidden_size_same = None  # compared anew below, from what this connection read
        if u.hidden_read and not u.is_offer:
            # A reinstall's file there, by name and size: the question names what it replaces.
            presence_by_size(listing, u)
        # An older version than the device's: by its file, never by the numbers alone.
        u.older_offer = _older_offer(storage, listing, u) if offers_older(u) else None
    found = []
    for u in offered:
        if u.hidden_folder is None:
            seen = presence(storage, u, progress)
            found.append(_in_session(listing, u, seen) if seen.why == NOT_ON_THE_DEVICE
                         else seen)
        else:
            found.append(presence_by_size(listing, u) if u.hidden_read
                         else Presence(TOP_FOLDER_NOT_SEEN))
        # The device's own software at the size offered is not taken for the same file: its
        # contents were not compared.
        u.on_device = found[-1].why == MATCHES or (found[-1].why == SAME_SIZE
                                                   and not u.is_system)
        u.changed_on_device = found[-1].why == MD5_DIFFERS
    _log_offered(storage, updates, offered, found, components, writable)
    return updates


def _in_session(listing: hidden_folders.Listing | None, u: SoftwareUpdate,
                gvfs: Presence) -> Presence:
    """An offer whose file the file manager does not show (`gvfs`): what openExpress's own
    session read of its folder on this connection tells, by the file's name and size
    (file_hidden: the device hides the file); `gvfs` when its folder was read and the file is
    not there, or when it was not read (file_unseen: session_folders asks for it)."""
    path = u.device_path.replace("\\", "/")
    if listing is None or listing.state_of(posixpath.dirname(path)) != hidden_folders.READ:
        u.file_unseen = True
        return gvfs
    if listing.file(path) is None:
        return gvfs
    u.file_hidden = True
    return presence_by_size(listing, u)


def known_by_size(u: SoftwareUpdate) -> bool:
    """Whether the file of `u` is known by name and size only (hidden_size_same), its contents
    never read: in a folder of the top level the listing does not show (hidden_folder), or found
    by openExpress's own session where the file manager does not show it (file_hidden)."""
    return u.hidden_folder is not None or u.file_hidden


def _hides_where_updates_go(updates: list[SoftwareUpdate]) -> bool:
    """Whether the device hides something where updates go (mark_present): a folder its
    GarminDevice.xml names inside a folder that is seen, or a folder of its top level."""
    return any(u.device_hides or u.hidden_folder is not None for u in updates)


def _log_offered(storage: Storage, updates: list[SoftwareUpdate], offered: list[SoftwareUpdate],
                 found: list[Presence], components: int | None, writable: bool) -> None:
    """What mark_present found, in full the first time for this connection and when it
    changes, else one short line."""
    said = []
    older = [u for u in updates if u.older_offer is not None]
    if older:
        said.append(("updates: %d reinstalls of an older version than the device's (%s)",
                     len(older), ", ".join(f"{u.part_number} {u.installed_version} on the "
                                           f"device, {u.version} offered: {u.older_offer}"
                                           for u in older)))
    # For each: where its file goes on the device, from the device's folder, and whether it
    # is there; never an address (a download address carries a token).
    named = ", ".join(f"{u.part_number} {u.version} at {_where(u, p)}: {p.why}"
                      for u, p in zip(offered[:LOG_ITEMS], found, strict=False))
    if len(offered) > LOG_ITEMS:
        named += f", … and {len(offered) - LOG_ITEMS} more"
    present = sum(1 for u in offered if u.on_device)
    said.append(("updates: %d offered%s, %d of them already on the device%s", len(offered),
                 f" ({named})" if named else "", present,
                 f"; {components} components listed" if components is not None else ""))
    hidden = sorted({u.hidden_folder for u in offered if u.hidden_folder})
    extra = [u for u in offered if u.is_add_on]
    if hidden or extra:
        said.append(("updates: %d in a top folder not seen (%s), %d add-ons (not installed: %s)",
                     sum(1 for u in offered if u.hidden_folder), ", ".join(hidden) or "none",
                     len(extra), ", ".join(sorted({u.severity or "no importance"
                                                   for u in extra})) or "none"))
    unseen = sum(1 for u in offered if u.file_unseen)
    in_session = sum(1 for u in offered if u.file_hidden)
    # Said where the session matters: a device that hides something where updates go, or one
    # where no session can be had (a drive, the Flatpak without USB); an offer not on a device
    # that hides nothing is only an update.
    hides = _hides_where_updates_go(updates)
    if in_session or (unseen and (hides or storage.mass_storage or storage.usb_blocked())):
        said.append((("updates: %d offered files the file manager does not show, %d of them "
                      "found by openExpress's own session by name and size; the others: %s"),
                     unseen + in_session, in_session,
                     _session_word(storage, writable) if unseen else "none"))
    if applog.unchanged("updates", connection(storage), said):
        log.info("updates: %d offered as before, %d of them already on the device",
                 len(offered), present)
        return
    for line in said:
        log.info(*line)


def place(u: SoftwareUpdate, tops: set[str] | None,
          listing: hidden_folders.Listing | None) -> None:
    """Where the file of `u` goes, the same for an update, a reinstall and a -META entry: the
    folder of the device's top level the listing does not show (hidden_folder; `tops`:
    hidden_folders.top_folders), and what became of reading its folder through openExpress's
    own session in this connection (hidden_session; `listing`: hidden_folders.kept). Decided
    here alone, for the page, the command line and the write; what is in that folder is
    mark_present's."""
    u.hidden_folder = hidden_folders.top_not_seen(
        posixpath.dirname(u.device_path.replace("\\", "/")), tops)
    u.hidden_session = (listing.state_of(posixpath.dirname(u.device_path))
                        if u.hidden_folder is not None and listing is not None else None)


def hidden_inside(storage: Storage, device, among: set[str] | None = None) -> dict[str, str]:
    """The folders the device hides inside a folder the listing shows: those its GarminDevice.xml
    names for its own components (UpdateFile paths, so they hold files), whose top folder the
    listing shows, and which the listing does not show itself (a fēnix keeps its voice prompts
    so). By the folder in lower case, as the device names it. One that is not named so may only
    not be there yet: it is made for a write like any other. A device that cannot be listed:
    none. `among`: only these folders (in lower case) are asked of the device, e.g. those the
    maps write to (over MTP each listing is a round trip)."""
    if among is not None and not among:
        return {}
    tops = hidden_folders.top_folders(storage)
    if tops is None:
        return {}
    named: dict[str, str] = {}
    for f in getattr(device, "update_files", None) or []:
        folder = "/".join(p for p in (f.path or "").replace("\\", "/").split("/") if p)
        if ("/" in folder and hidden_folders.top_not_seen(folder, tops) is None
                and (among is None or folder.casefold() in among)):
            named.setdefault(folder.casefold(), folder)
    hidden = {}
    for key, folder in named.items():
        try:
            seen = storage.resolve(folder) is not None
        except DeviceNotResponding:
            raise
        except DeviceError:
            continue  # not known: made for a write like any other
        if not seen:
            hidden[key] = folder
    if hidden:
        log.info("updates: %d folders the device names for its components are not listed: %s",
                 len(hidden), ", ".join(sorted(hidden.values(), key=str.casefold)))
    return hidden


def hidden_needed(updates: list[SoftwareUpdate]) -> list[str]:
    """The folders the updates and the reinstalls go to whose folder of the top level ordinary
    MTP does not show, and that this connection has not tried to read yet
    (hidden_folders.read), each once: what may be written there is read first."""
    out: dict[str, str] = {}
    for u in updates:
        if u.hidden_folder is not None and u.hidden_session is None:
            folder = posixpath.dirname(u.device_path)
            out.setdefault(folder.casefold(), folder)
    return sorted(out.values(), key=str.casefold)


def session_folders(storage: Storage,
                    updates: list[SoftwareUpdate]) -> tuple[list[str], list[str]]:
    """What openExpress's own session reads for the updates: the one place the window and the
    command line ask (gui.hidden_reading.note_needed, cli._read_hidden). First the folders of
    the top level ordinary MTP does not show (hidden_needed; read even where no session can be
    had, which then says why). Then the folders, inside folders that are seen, of the offers
    whose file the file manager does not show (file_unseen), each once: only where a session
    can be had (session_possible) and the device hides something where updates go (a folder its
    GarminDevice.xml names, or one of its top level), and only those this connection has not
    read. Nothing is asked of the device here."""
    hidden = hidden_needed(updates)
    if not hidden_folders.session_possible(storage) or not _hides_where_updates_go(updates):
        return hidden, []
    inside: dict[str, str] = {}
    for u in updates:
        if u.is_offer and u.file_unseen and u.hidden_folder is None:
            folder = posixpath.dirname(u.device_path.replace("\\", "/")).strip("/")
            inside.setdefault(folder.casefold(), folder)
    return hidden, sorted(inside.values(), key=str.casefold)


def _session_word(storage: Storage, writable: bool) -> str:
    """For the log: what becomes of the files the file manager does not show."""
    if storage.mass_storage:
        return "no session (a USB drive hides nothing)"
    if not writable:
        why = hidden_folders.NO_USB if storage.usb_blocked() else hidden_folders.NO_SESSION
        return f"no session ({why})"
    return "openExpress's own session reads their folders"


def not_installed_here_text(u: SoftwareUpdate) -> str:
    # TRANSLATORS: `openexpress updates`: an update openExpress does not install; {folder}: the
    # folder of the device's top level its file goes to, e.g. .System
    return _("Not installed: {name} {version} goes to the folder {folder}, which openExpress "
             "cannot see on the device.").format(name=u.title, version=u.version,
                                                 folder=u.hidden_folder)


def not_installed_inside_text(u: SoftwareUpdate) -> str:
    # TRANSLATORS: `openexpress updates`: an update not installed because openExpress cannot
    # reach the folder its file goes to; {folder}: that folder, which the device hides from the
    # file manager, e.g. GARMIN/Prompts
    return _("Not installed: {name} {version} goes to the folder {folder}, which the device hides "
             "from the file manager, and openExpress cannot open a direct USB connection to this "
             "device to write there (the Flatpak version needs access to USB devices for it, see "
             "the README). The other updates install as usual."
             ).format(name=u.title, version=u.version, folder=u.hidden_inside)


def hidden_inside_reason(u: SoftwareUpdate) -> str:
    # TRANSLATORS: why an update was not installed, after its name and version in the list of
    # those not installed (lower case, no full stop); {folder}: the folder the device hides from
    # the file manager, e.g. GARMIN/Prompts
    return _("the device keeps it in the folder {folder}, hidden from the file manager, and "
             "openExpress cannot open a direct USB connection to this device to write there (the "
             "Flatpak version needs access to USB devices for it)").format(folder=u.hidden_inside)


def failed_text(report: InstallReport) -> str:
    """The updates not installed and why, the others having been (InstallReport.failed): in the
    window's message and on the command line."""
    # TRANSLATORS: after updates were copied: the head of the list of those that were not
    head = ngettext("{count} update was not installed:", "{count} updates were not installed:",
                    len(report.failed)).format(count=len(report.failed))
    # TRANSLATORS: an update not installed and why ({why}: the error's words)
    lines = [pgettext("update not installed", "{name} {version}: {why}").format(
        name=u.title, version=u.version, why=why) for u, why in report.failed]
    return "\n".join([head, *lines])


def hidden_software_text(folder: str) -> str:
    # TRANSLATORS: `openexpress updates`: the device keeps its software in a folder openExpress
    # cannot see over MTP; {folder}: e.g. .System
    return _("openExpress cannot see the folder {folder}, where this device keeps its software, "
             "so it installs nothing there. Some devices, such as car navigators, hide it from "
             "the file manager.").format(folder=folder)


def power_needed(written: list[SoftwareUpdate]) -> bool:
    """Whether the device's own software was written into a folder it hides (a car navigator,
    which installs it once unplugged): then power_text is said."""
    return any(u.is_system and u.hidden_folder is not None for u in written)


def power_text() -> str:
    # TRANSLATORS: after the device's own software was written into the folder a car navigator
    # hides: it installs it once unplugged, and must not lose power meanwhile
    return _("Keep the device powered until it has installed its software: connect it to its car "
             "cable or a USB charger, or make sure its battery is well charged.")


def not_read_text(u: SoftwareUpdate) -> str | None:
    """Why the hidden folder of `u` could not be read; None when it was, or was not tried."""
    if u.hidden_folder is None or u.hidden_session in (None, hidden_folders.READ):
        return None
    return hidden_folders.no_session_text(u.hidden_folder, u.hidden_session)


def add_ons_text(count: int) -> str:
    # TRANSLATORS: `openexpress updates`: items Garmin offers to add to the device (voices,
    # vehicles) that it does not have
    return ngettext("{count} add-on that is not on the device is not listed: openExpress does "
                    "not install add-ons yet.",
                    "{count} add-ons that are not on the device are not listed: openExpress does "
                    "not install add-ons yet.", count).format(count=count)


def installable(updates: list[SoftwareUpdate]) -> list[SoftwareUpdate]:
    """Those that may be written: none whose top folder ordinary MTP does not show, unless that
    folder was read through openExpress's own session (hidden_folders), and none whose folder
    the device hides inside a folder that is seen (hidden_inside)."""
    return [u for u in updates if not u.hidden_unread and not u.refused_inside]


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
            forget_installed: bool = True, progress=None) -> list[SoftwareUpdate]:
    """What the update server's `response` offers `device` (its GarminDevice.xml), each
    compared with the device's storage (mark_present) and with what openExpress copied to it
    before (mark_copied; `forget_installed`: False for a check that keeps nothing). A test
    drive carries a real device's unit ID: what was copied to that device is not its."""
    unit = None if is_test_drive(storage) else getattr(device, "unit_id", None)
    items = mark_present(storage, parse_updates(response, device), component_count(device),
                         inside=hidden_inside(storage, device),
                         writable=hidden_folders.session_possible(storage), progress=progress)

    started = (_connection_started(device, storage)
               if unit and any(known_by_size(u) for u in items) else None)
    return mark_copied(items, unit, forget_installed, connected_at=started)


def _connection_started(device, storage) -> float | None:
    """When the device's present connection began (history.connection_started); None when
    that is not known. Optional: a failure is only written to the log."""
    from openexpress import history

    try:
        return history.connection_started(device, storage)
    except DeviceNotResponding:
        raise
    except Exception as e:  # noqa: BLE001 - only to tell an unplugging since a copy
        log.info("updates: the start of this connection not read (%s)", type(e).__name__)
        return None


def mark_copied(updates: list[SoftwareUpdate], unit_id: int | None,
                forget_installed: bool = True,
                connected_at: float | None = None) -> list[SoftwareUpdate]:
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
        if known_by_size(u):
            # In a folder the device hides, known by name and size: openExpress's copy is there
            # at the size offered (its bytes were checked as it was written), or the device
            # changed it; a connection begun after the copy (`connected_at`) is an unplugging.
            u.on_device = u.on_device or u.hidden_size_same is True
            u.changed_on_device = u.hidden_size_same is False
            if connected_at is not None and connected_at > at:
                u.unplugged_since_copy = True
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


# What Garmin writes in the notes of some components instead of a change: left out.
NO_CHANGE_NOTE = {"", "na", "n/a", "-"}


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
        changes = [c for rn in u.get("releasenotes") or [] for c in rn.get("changelog") or []
                   if isinstance(c, str) and c.strip().casefold() not in NO_CHANGE_NOTE]
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
    return [u for u in updates if u.part_key in parts]


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
              or u.part_key in keys]
    missing = keys - {u.part_key for u in chosen}
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
    # Those not written, each with why (the words of its error): the others were written.
    failed: list[tuple[SoftwareUpdate, str]] = field(default_factory=list)
    # The trial (hidden_trial): the file each replaced, copied to this computer first.
    kept: list[tuple[SoftwareUpdate, hidden_folders.KeptCopy]] = field(default_factory=list)

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
            and (not u.is_metadata or u.part_key in copied)]
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
    hidden_trial: Path | None = None,
) -> InstallReport:
    """`progress`: each update by its name, in bytes ("Downloading … · 1.2 MB of 3.4 MB",
    "Copying … to the device · …"); the files are the server's and the device's own. Into a
    folder of the top level the device hides only once openExpress's own session has read it
    (_write_hidden). Two updates for one file: one of them is copied (one_per_file). `report`
    is filled as it goes: what was written before a failure is in it. `hidden_trial`: a folder
    on this computer; then an update whose folder the device hides inside a folder that is seen
    (hidden_inside) is written there through openExpress's own session, as into a folder of the
    top level, the file there before copied into this folder first (report.kept), and the first
    one not written stops the rest (a trial, from the command line only)."""
    updates = one_per_file(updates)[0]  # the others are told in the log
    # Never a folder of the device's top level the listing does not show (a car navigator's
    # .System), unless that folder was read through openExpress's own session; nothing is
    # downloaded for an update refused.
    tops = hidden_folders.top_folders(storage)
    listing = hidden_folders.kept(storage)
    for u in updates:
        if reinstall_refused(u):  # never an older version over the device's own
            log.info("software update: %s %s not installed: %s %s on the device, %s",
                     u.part_number, u.version, u.part_number, u.installed_version, u.older_offer)
            raise UpdateError(older_offer_text(u))
    for u in updates:
        place(u, tops, listing)  # as the page and the command line placed it
        if u.hidden_unread:
            log.info("software update: %s %s not installed: its top folder %s is not seen",
                     u.part_number, u.version, u.hidden_folder)
            raise UpdateError(not_installed_here_text(u))
    # A folder the device hides inside a folder that is seen (mark_present): refused before
    # anything is downloaded, unless tried; the others go on.
    for u in [u for u in updates if u.refused_inside and hidden_trial is None]:
        log.info("software update: %s %s not installed: its folder %s is hidden",
                 u.part_number, u.version, u.hidden_inside)
        report.failed.append((u, hidden_inside_reason(u)))
    updates = [u for u in updates if not u.refused_inside or hidden_trial is not None]
    if not updates:
        raise UpdateError(failed_text(report))
    # A file in a hidden folder is checked against the space in its own session.
    need, free = check_space(storage, [u for u in updates if u.hidden_folder is None
                                       and u.hidden_inside is None])
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
        downloading = downloading_step(u)
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
    # From the first copy to the last the device must not be cut off: kept awake.
    refused: list[Exception] = []
    with awake.kept_awake("software updates are copied", say=say):
        # Into a folder the device hides inside one that is seen first, so that the -META entry
        # of one not written is left out; the device's own software stays last.
        ordered = sort_for_install(updates)
        ordered.sort(key=lambda u: u.hidden_inside is None)
        not_written = set()
        for u in ordered:
            if u.hidden_folder is not None:
                # Through openExpress's own session, as Garmin Express writes there: the
                # device's own software after the rest, and the first failure stops all that
                # would follow (the session may be left unusable).
                _write_hidden(storage, u, local_files[u.key], progress, report)
                continue
            name = posixpath.basename(u.device_path.replace("\\", "/"))
            if u.hidden_inside is not None and hidden_trial is not None:  # the trial: stops
                _write_hidden(storage, u, local_files[u.key], progress, report,
                              keep_copy=hidden_trial / f"{u.part_number}-{u.version}-{name}")
                continue
            if u.is_metadata and u.part_key in not_written:
                log.info("software update: %s left out, its update was not installed",
                         u.part_number)
                continue
            try:
                if u.hidden_inside is not None:
                    # Through openExpress's own session; the file it replaces copied to this
                    # computer first, kept only when it could not be put back.
                    _write_hidden(storage, u, local_files[u.key], progress, report,
                                  keep_copy=replaced_path(u, name), drop_copy=True)
                else:
                    _write_one(storage, u, local_files[u.key], progress, report)
            except DeviceNotResponding:
                raise
            except (UpdateError, DeviceError) as e:
                # One file refused: the others are written all the same (the device's own
                # software does not depend on an add-on), unless the device is gone.
                if _gone(storage):
                    raise
                log.info("software update: %s %s not installed (%s), the others go on",
                         u.part_number, u.version, type(e).__name__)
                report.failed.append((u, applog.error_text(e)))
                refused.append(e)
                not_written.add(u.part_number.upper())
    if report.failed and not report.written:
        if refused:
            raise refused[0]  # nothing written: the copy failed with this, its own advice too
        raise UpdateError(failed_text(report))
    return report


def _gone(storage: Storage) -> bool:
    """Whether the device's top level can no longer be listed: it is gone (DeviceNotResponding
    goes on as it is)."""
    try:
        storage.listdir("")
    except DeviceNotResponding:
        raise
    except DeviceError:
        return True
    return False


def downloading_step(u: SoftwareUpdate) -> str:
    """The step of downloading `u`; its -META entry, which the server names as the update
    itself, said apart."""
    if u.is_metadata:
        # TRANSLATORS: a step: the small file of information about an update ({name}) is
        # downloaded ("… · 1.2 MB of 3.4 MB")
        return _("Downloading the update information of {name}").format(name=u.title)
    return downloading_text(u.title)


def copying_step(u: SoftwareUpdate) -> str:
    """The step of copying `u` to the device; its -META entry said apart."""
    if u.is_metadata:
        # TRANSLATORS: a step: the small file of information about an update ({name}) is
        # copied to the device
        return _("Copying the update information of {name} to the device").format(name=u.title)
    return copying_text(u.title)


def checking_step(u: SoftwareUpdate) -> str:
    """The step of reading `u` back from the device; its -META entry said apart."""
    if u.is_metadata:
        # TRANSLATORS: a step: the small file of information about an update ({name}) is read
        # back from the device and compared
        return _("Checking the update information of {name} on the device").format(name=u.title)
    return checking_text(u.title)


def _write_one(storage: Storage, u: SoftwareUpdate, local: Path, progress,
               report: InstallReport) -> None:
    """The file of `u` to the device as any file, checked."""
    size = local.stat().st_size
    log.debug("software update: copying %s", u.device_path)
    copying = copying_step(u)
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
        checking=byte_steps(progress, checking_step(u)))
    report.written.append(u)
    remember_written(storage, real, download.normalize_md5(u.md5))  # not read again to be known
    # The whole path from the device's folder, as mark_present names it.
    log.info("software update: %s %s (%s) copied to %s", u.part_number, u.version,
             applog.size(size), real)


def replaced_path(u: SoftwareUpdate, name: str) -> Path:
    """Where the file an update replaces in a folder the device hides is copied first: kept on
    this computer only when it could not be put back (hidden_folders.write)."""
    return paths.data_dir() / "replaced" / f"{u.part_number}-{u.version}-{name}"


def _write_hidden(storage: Storage, u: SoftwareUpdate, local: Path, progress,
                  report: InstallReport, keep_copy: Path | None = None,
                  drop_copy: bool = False) -> None:
    """The file of `u` into the folder of the top level the device hides (hidden_folders.write);
    an UpdateError that says what is on the device when it was not written whole, or could not
    be read back (it then stays, counted as written)."""
    try:
        written = hidden_folders.write(storage, u.device_path, local,
                                       download.normalize_md5(u.md5), u.title,
                                       space_margin(storage), progress=progress,
                                       copying=copying_step(u),
                                       checking=checking_step(u), keep_copy=keep_copy,
                                       drop_copy=drop_copy)
    except hidden_folders.ContentsNotRead as e:
        report.written.append(u)
        raise UpdateError(str(e)) from e
    except DeviceNotResponding:
        raise
    except DeviceError as e:
        raise UpdateError(str(e)) from e
    report.written.append(u)
    if written.kept is not None:
        report.kept.append((u, written.kept))
    log.info("software update: %s %s (%s) written to %s", u.part_number, u.version,
             applog.size(written.size), written.real)


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
        if unit_id and report.copied and not is_test_drive(storage):
            try:
                known_devices.note_copied(unit_id, report.copied)
            except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
                log.warning("updates: what was copied not kept (%s)", type(e).__name__)


