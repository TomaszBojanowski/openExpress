"""Folders of a device's top level that ordinary MTP does not show (a car navigator keeps its
software, its time zone map and its maps in .System), read through openExpress's own USB
session: the names and sizes of what the folders asked hold, never the contents of a file. The
session takes the device from the file manager for a moment, so what it read is kept for the
connection (kept) until the device is unplugged or the user asks again (forget: Check Again,
Refresh); updates.mark_present, maps.mark_waiting and history.count take it from there. A file
of an update is written there by write, as Garmin Express writes it, in a session of its own
that lists its folder anew; the files of a map (the command line's trial) by write_map, all in
one session."""

from __future__ import annotations

import hashlib
import io
import logging
import posixpath
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from openexpress import applog, awake
from openexpress.device import ptp
from openexpress.device.storage import (
    DeviceBusy,
    DeviceError,
    DeviceNotResponding,
    Entry,
    RemountFailed,
    Storage,
    connection,
    named_in_any_case,
)
from openexpress.i18n import _, human_size, ngettext
from openexpress.progress import byte_steps, say_to

log = logging.getLogger(__name__)

# What became of a reading (Listing.state), in the words of the log too.
READ = "read"
NO_SESSION = "no USB session of its own here"
NO_USB = "no USB session: a Flatpak without access to USB devices"
FAILED = "the session failed"


@dataclass
class Listing:
    """What one session read: each folder asked, by its path in lower case, with its path in
    the device's letter case and what it holds (the ordinary listing and Garmin's together); the
    folders asked that the device does not have, by their path in lower case, with the path as
    asked; the folders of the top level the session found hidden (ordinary MTP does not show
    them), in lower case; `state`: READ, or why nothing was read (NO_SESSION, NO_USB, FAILED)."""

    folders: dict[str, tuple[str, list[Entry]]] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    state: str = READ
    hidden_tops: set[str] = field(default_factory=set)

    def tops(self) -> list[str]:
        """The folders of the top level read that ordinary MTP does not show (a car navigator's
        .System), in the device's letter case; not one that is seen, as GARMIN, whose folders
        a session may read too."""
        return [top for top in _tops(real for real, _entries in self.folders.values())
                if top.casefold() in self.hidden_tops]

    def entries(self, folder: str) -> list[Entry] | None:
        """What the folder holds; None when it was not read (not asked, not there, or the
        session failed)."""
        found = self.folders.get(_key(folder))
        return found[1] if found is not None else None

    def real(self, folder: str) -> str | None:
        """The folder's path in the device's letter case; None when it was not read."""
        found = self.folders.get(_key(folder))
        return found[0] if found is not None else None

    def file(self, path: str) -> Entry | None:
        """The file at `path` (in any letter case) in a folder read; None when it is not
        there or its folder was not read."""
        entries = self.entries(posixpath.dirname(path.strip("/")))
        name = posixpath.basename(path).casefold()
        return next((e for e in entries or [] if not e.is_dir and e.name.casefold() == name),
                    None)

    def state_of(self, folder: str) -> str | None:
        """What became of reading `folder`: READ when the session told what is there (also
        that it is not there), the session's state when it failed; None: not asked."""
        if self.state != READ:
            return self.state
        return READ if self.knows(folder) else None

    def knows(self, folder: str) -> bool:
        """Whether the session told what is at `folder`: read, or not on the device."""
        return _key(folder) in self.folders or _key(folder) in self.missing


def _key(folder: str) -> str:
    return folder.strip("/").casefold()


def top_folders(storage: Storage) -> set[str] | None:
    """The names of the folders of the device's top level that the ordinary listing shows,
    in lower case; None when they cannot be listed, and for a device seen as a USB drive, which
    hides nothing from its listing (a folder not there is only not there yet): then no folder is
    taken for unseen."""
    if storage.mass_storage:
        return None
    try:
        return {e.name.casefold() for e in storage.listdir("") if e.is_dir}
    except DeviceNotResponding:
        raise
    except DeviceError:
        return None


def top_not_seen(folder: str, tops: set[str] | None) -> str | None:
    """The folder of the device's top level that `folder` (with `/` or `\\`) is in, or is,
    when the ordinary listing does not show it (`tops`: top_folders; a car navigator hides its
    .System from MTP); None when it is seen, when `folder` is the top level itself, or when the
    top level could not be listed. The one rule for the file of an update or of a map (its
    folder: posixpath.dirname of its path) and for the folder of own maps; a folder inside a
    folder that is seen is made for a write like any other."""
    parts = [p for p in folder.replace("\\", "/").split("/") if p]
    if not parts or tops is None:
        return None
    return None if parts[0].casefold() in tops else parts[0]


# What each connection read, by the connection (storage.connection).
_kept: dict[tuple, Listing] = {}
# A reading refused as busy (gvfs, or this process's own operations still under way) is not
# kept: counted per connection instead, so that the window may try again (at most BUSY_TRIES
# times a connection) and the command line does not within one command.
_busy: dict[tuple, int] = {}
BUSY_TRIES = 3


def kept(storage: Storage) -> Listing | None:
    """What a session read of this connection; None when none was opened for it."""
    return _kept.get(connection(storage))


def forget(storage: Storage) -> None:
    """The next look at the device reads its hidden folders anew ("Check Again"), also after
    readings refused as busy."""
    _kept.pop(connection(storage), None)
    _busy.pop(connection(storage), None)


def busy_failures(storage: Storage) -> int:
    """How many readings of this connection were refused as busy since it was opened or
    forgotten (forget)."""
    return _busy.get(connection(storage), 0)


# A device opened while it was still starting may have no place on USB yet: a session of its own
# is asked again after each of these waits (seconds) before it is taken for impossible.
STARTING_WAITS = (2, 3, 5, 5, 5, 10)
_sleep = time.sleep


def waiting_text() -> str:
    # TRANSLATORS: a step: the device was connected a moment ago and is not ready yet for the
    # USB session openExpress opens of its own
    return _("Waiting for the device to get ready…")


def session_for(storage: Storage, progress: Callable[[str], None] | None = None):
    """storage.exclusive_ptp(); while the device's place on USB is not known yet (opened while it
    was starting), asked again after each of STARTING_WAITS, each wait told to `progress`. None
    when it stays impossible, at once where the Flatpak has no USB (usb_blocked) or the place is
    known (another reason: libusb missing, a USB drive)."""
    from openexpress.device.gio_storage import usb_place

    opener = storage.exclusive_ptp()
    for wait in STARTING_WAITS:
        if (opener is not None or storage.usb_blocked()
                or not getattr(storage, "device_uri", None)
                or usb_place(getattr(storage, "usb", None)) is not None):
            break
        log.info("hidden folders: the device's place on USB not known yet, asked again in %d s",
                 wait)
        say_to(progress)(waiting_text())
        _sleep(wait)
        opener = storage.exclusive_ptp()
    return opener


def reading_text(folders: Iterable[str]) -> str:
    # TRANSLATORS: a step: openExpress reads folders of the device through a direct USB
    # connection, also those the device hides from the file manager; {folder}: the folders of the
    # top level they are in, e.g. .System or GARMIN
    return _("Reading the folder {folder}…").format(folder=", ".join(_tops(folders)))


def no_session_text(folder: str, state: str) -> str:
    """Why the folder could not be read (Listing.state NO_USB, else NO_SESSION or FAILED)."""
    if state == NO_USB:
        # TRANSLATORS: {folder}: a folder the device hides from the file manager, e.g.
        # .System
        return _("The Flatpak version has no access to USB devices, so it cannot read the "
                 "folder {folder}. Give it the permission (see the README) or use the version "
                 "from source.").format(folder=folder)
    # TRANSLATORS: {folder}: a folder the device hides from the file manager, e.g. .System
    return _("openExpress cannot open a direct USB connection to this device, so it cannot read "
             "the folder {folder}.").format(folder=folder)


def _tops(folders: Iterable[str]) -> list[str]:
    """The folders of the top level among `folders`, each once, in the order given."""
    out: list[str] = []
    for folder in folders:
        top = folder.strip("/").split("/")[0]
        if top and top.casefold() not in {t.casefold() for t in out}:
            out.append(top)
    return out


def folders_to_read(device, needed: Iterable[str], inside: Iterable[str] = ()) -> list[str]:
    """The folders a session reads, the same in the window and the command line: those
    `needed` (by updates and maps, under folders of the top level ordinary MTP does not show),
    the folders of the top level they are in and those the device is known to hide
    (known_devices.hidden_tops), and what its GarminDevice.xml names under them
    (history.declared_under: what Device Status counts there); and the folders `inside` folders
    of the top level that are seen (those of updates whose file the file manager does not
    show), as they are: never every folder of GARMIN. Each once."""
    from openexpress import history, known_devices

    needed = [f.replace("\\", "/").strip("/") for f in needed]
    tops = {f.split("/")[0] for f in needed if f}
    tops |= set(known_devices.hidden_tops(getattr(device, "unit_id", None)))
    out: dict[str, str] = {}
    inside = [f.replace("\\", "/").strip("/") for f in inside]
    for folder in [*needed, *tops, *history.declared_under(device, tops), *inside]:
        if folder:
            out.setdefault(folder.casefold(), folder)
    return sorted(out.values(), key=str.casefold)


def still_to_read(storage: Storage, device, needed: Iterable[str],
                  inside: Iterable[str] = ()) -> list[str]:
    """What a reading of this connection reads now, the same in the window and the command line:
    nothing after a reading that failed, or where no session can be had (said once a
    connection); else those of folders_to_read the connection has not read, as a reading is
    added to what it read before."""
    listing = kept(storage)
    if listing is not None and listing.state != READ:
        return []
    return [f for f in folders_to_read(device, needed, inside=inside)
            if listing is None or not listing.knows(f)]


def read(storage: Storage, folders: Iterable[str], why: str,
         progress: Callable[[str], None] | None = None, unit_id: int | None = None) -> Listing:
    """Read `folders` (paths from the top level) through a session of our own, add what it
    read to what the connection read before (each folder read again replaces only itself,
    _added), and return what the connection knows now: each folder by the ordinary listing and
    Garmin's together, as they were read. `why`: for the log, what needs them. A session that
    cannot be opened or fails leaves a listing that says why (Listing.state), in place of what
    was read before; one refused as busy is not kept: it returns such a listing and leaves
    what was read before (busy_failures). Only a device not mounted again after it raises
    (RemountFailed: unplug it and plug it in). `unit_id`: the device's, which then keeps the
    folders of its top level found hidden (known_devices.hidden_tops)."""
    wanted = sorted({f.strip("/") for f in folders if f.strip("/")}, key=str.casefold)
    listing = Listing()
    opener = session_for(storage, progress) if wanted else None
    if opener is None:
        listing.state = NO_USB if wanted and storage.usb_blocked() else NO_SESSION
        if wanted:
            log.info("hidden folders: %s not read: %s", ", ".join(wanted), listing.state)
        _kept[connection(storage)] = listing
        return listing
    log.info("hidden folders: opening a USB session of its own to read %s: %s",
             ", ".join(wanted), why)
    say_to(progress)(reading_text(wanted))
    try:
        with opener as session:
            _read_in(session, wanted, listing)
    except RemountFailed:
        forget(storage)
        raise
    except DeviceNotResponding:
        raise
    except DeviceBusy as e:
        # What the connection read before stays: a later reading may find the device free.
        tries = _busy[connection(storage)] = busy_failures(storage) + 1
        # The words of gvfs when it refused; the error itself is the sentence for the user.
        log.warning("hidden folders: not read, the device was busy (%s); not kept, %d such "
                    "reading(s) this connection", e.__cause__ or e, tries)
        return Listing(state=FAILED)
    except DeviceError as e:
        log.warning("hidden folders: not read, %s (%s: %s)", FAILED, type(e).__name__, e)
        listing = Listing(state=FAILED)
    else:
        if unit_id and listing.tops():
            from openexpress import known_devices

            with known_devices.optional_change("devices: the hidden folders not kept (%s)"):
                known_devices.note_hidden_tops(unit_id, listing.tops())
        log.info("hidden folders: %s", "; ".join([
            *(f"{real}: {len(entries)} entries" for real, entries in listing.folders.values()),
            *(f"{m}: not on the device"
              for m in sorted(listing.missing.values(), key=str.casefold))]))
        listing = _added(_kept.get(connection(storage)), listing)
    _kept[connection(storage)] = listing
    return listing


def _added(before: Listing | None, now: Listing) -> Listing:
    """What the connection knows after the reading `now`: what was read before, each folder
    `now` read (or found not there) in place of what was known of it; `now` alone when nothing
    was read before, or the reading before told nothing (it failed or had no session)."""
    if before is None or before.state != READ:
        return now
    added = Listing(dict(before.folders), dict(before.missing),
                    hidden_tops=before.hidden_tops | now.hidden_tops)
    for key, found in now.folders.items():
        added.folders[key] = found
        added.missing.pop(key, None)
    for key, asked in now.missing.items():
        added.missing[key] = asked
        added.folders.pop(key, None)
    return added


def _children(session, store: int, parent: int) -> list:
    """What ordinary MTP and Garmin's listing show in a folder, the top level too (where the
    hidden folder of a navigator is)."""
    return session.all_children(store, parent, at_root=True)


def _descend(children, store: int, top, parts: list[str]):
    """The folder `parts` below the folder `top`, each looked up whatever its letter case among
    the folders `children(store, handle)` lists, with its path as the device spells it; (None,
    the path so far) when one of them is not there."""
    current, real = top, [top.name]
    for part in parts:
        current = named_in_any_case(
            [i for i in children(store, current.handle) if i.is_dir], part)
        if current is None:
            return None, real
        real.append(current.name)
    return current, real


def _read_in(session, wanted: list[str], listing: Listing) -> None:
    """Each folder of `wanted`, looked up from the top level of the storage that has its
    folder of the top level (the first one), into `listing`. Names and sizes only."""
    tops = {}  # the top level of each storage, listed once
    listed: dict[tuple[int, int], list] = {}

    def children(store: int, parent: int) -> list:
        if (store, parent) not in listed:
            listed[(store, parent)] = _children(session, store, parent)
        return listed[(store, parent)]

    stores = session.storages()
    for store in stores:
        tops[store] = children(store, ptp.ROOT)
    for folder in wanted:
        parts = folder.split("/")
        place = None
        for store in stores:
            top = named_in_any_case([i for i in tops[store] if i.is_dir], parts[0])
            if top is not None:
                place = (store, top)
                break
        if place is None:
            listing.missing[folder.casefold()] = folder
            continue
        store, top = place
        if top.hidden:
            listing.hidden_tops.add(top.name.casefold())
        current, real = _descend(children, store, top, parts[1:])
        if current is None:
            listing.missing[folder.casefold()] = folder
            continue
        entries = [Entry(i.name, i.is_dir, 0 if i.is_dir else i.size)
                   for i in children(store, current.handle)]
        listing.folders[folder.casefold()] = ("/".join(real), entries)


# --- writing one file there (updates._write_hidden) -------------------------------------------


class WriteRefused(DeviceError):
    """Nothing was changed on the device: why not."""


class NotWritten(DeviceError):
    """The file was not written; the text says what is on the device now."""


class ContentsNotRead(DeviceError):
    """The file was written and has the right size, and the device did not let it be read back."""


def not_written_text(name: str, old_removed: bool) -> str:
    if old_removed:
        # TRANSLATORS: {name} is an update, e.g. "Time Zone Map"; its old file was deleted before
        # the new one was written, as Garmin Express does, and the new one failed
        return _("{name} was not written: its old file was removed and the new one is not on "
                 "the device. Nothing else was changed; install it again.").format(name=name)
    # TRANSLATORS: {name} is an update, e.g. "Time Zone Map"
    return _("{name} was not written; nothing of it is on the device and nothing else was "
             "changed.").format(name=name)


def contents_not_read_text(name: str) -> str:
    # TRANSLATORS: {name} is an update, e.g. "Time Zone Map", written into a folder the device
    # hides; the device refused to give it back, so its bytes were not checked
    return _("{name} was written and has the right size, but the device did not let "
             "openExpress read it back, so its contents were not compared. openExpress stops here "
             "and writes nothing else.").format(name=name)


def no_space_text(name: str, needed: int, free: int) -> str:
    # TRANSLATORS: {needed} and {free} are sizes; the old file of the update counts as free, as
    # it is removed before the new one is written
    return _("Not enough space for {name}: {needed} needed, {free} free with its old file "
             "counted. openExpress removes nothing else to make room.").format(
        name=name, needed=human_size(needed), free=human_size(free))


def no_folder_text(folder: str) -> str:
    # TRANSLATORS: {folder}: e.g. .System/Map
    return _("The folder {folder} is not on the device, and openExpress does not create "
             "folders there.").format(folder=folder)


@dataclass
class KeptCopy:
    """The file that was on the device before, copied to this computer before it was
    replaced."""
    path: Path
    size: int
    sha256: str
    md5: str


@dataclass
class Written:
    real: str  # where the file is on the device, in its letter case
    size: int
    kept: KeptCopy | None = None  # the file there before, when a copy was asked for


def copy_not_read_text(name: str) -> str:
    # TRANSLATORS: {name} is an update; openExpress copies the file on the device to this
    # computer before it writes the new one, and that copy failed
    return _("{name} was not written: the file there now could not be copied whole to this "
             "computer first. Nothing on the device was changed.").format(name=name)


def probe_step_text(folder: str) -> str:
    # TRANSLATORS: a step of the trial on the command line: a small file written into a folder
    # the device hides, read back and deleted, before anything there is replaced
    return _("Trying a small test file in {folder}…").format(folder=folder)


def keeping_step_text(name: str) -> str:
    # TRANSLATORS: a step of the trial on the command line; {name}: an update, whose file now on
    # the device is copied to this computer before it is replaced
    return _("Copying the old {name} to this computer").format(name=name)


def removing_step_text(name: str) -> str:
    # TRANSLATORS: a step of the trial on the command line; {name}: an update
    return _("Removing the old {name} from the device").format(name=name)


def putting_back_step_text(name: str) -> str:
    # TRANSLATORS: a step of the trial on the command line; {name}: an update whose new file was
    # not written whole, so its old one is written back from the copy on this computer
    return _("Putting the old {name} back on the device").format(name=name)


def probe_refused_text(name: str) -> str:
    # TRANSLATORS: {name} is an update; before it replaces anything, openExpress writes a small
    # test file into the folder the device hides, reads it back and deletes it
    return _("{name} was not written: the device did not take a small test file into that "
             "folder, so the file there now was left as it is.").format(name=name)


PROBE = "openexpress-probe.tmp"
PROBE_DATA = b"openExpress: a test that this folder takes a new file; deleted at once\n"


def put_back_dropped_text() -> str:
    # TRANSLATORS: after an update was not written into a folder the device hides, the file that
    # was there before was written back from its copy on this computer
    return _("The file that was there before was put back and read back the same.")


def session_possible(storage: Storage) -> bool:
    """Whether a session of openExpress's own may be asked of this device: one over MTP whose
    USB the program can reach (not the Flatpak without the permission). A device still starting
    counts (session_for waits for it); whether the device then takes a file is known only as it
    is written."""
    return (bool(getattr(storage, "device_uri", None)) and not storage.usb_blocked()
            and not storage.mass_storage)  # a USB drive: no MTP, no session


def put_back_text(kept: KeptCopy, put_back: bool) -> str:
    """What became of the file that was there before, after the new one was not written."""
    if put_back:
        # TRANSLATORS: after an update was not written into a folder the device hides; {path}:
        # the copy on this computer of the file that was there before; {sha256}: its checksum
        return _("The file that was there before was put back and read back the same as its "
                 "copy ({path}, SHA-256 {sha256}).").format(path=kept.path, sha256=kept.sha256)
    # TRANSLATORS: after an update was not written into a folder the device hides; {path}: the
    # copy on this computer of the file that was there before; {sha256}: its checksum
    return _("The file that was there before could not be put back; its copy is at {path} "
             "(SHA-256 {sha256}).").format(path=kept.path, sha256=kept.sha256)


# How a file written is read back: whole (FULL), or by samples (SAMPLED: the first and the
# last block and CHECK_SAMPLES blocks at random, byte for byte against the file on the
# computer). A file up to FULL_CHECK_UP_TO is always read back whole.
FULL = "full"
SAMPLED = "sampled"
CHECK_BLOCK = 1024 * 1024
CHECK_SAMPLES = 8
FULL_CHECK_UP_TO = 256 * 1024 * 1024
_random = random.Random()


def _pace(size: int, seconds: float) -> str:
    """How long a copy or a check took, and how fast it went (for the log)."""
    if seconds <= 0:
        return f"{seconds:.1f} s"
    return f"{seconds:.1f} s, {size / seconds / 1_000_000:.1f} MB/s"


def write(storage: Storage, path: str, local: Path, md5: str | None, name: str, margin: int,
          progress: Callable[[str], None] | None = None, copying: str | None = None,
          checking: str | None = None, check: str = FULL,
          keep_copy: Path | None = None, drop_copy: bool = False) -> Written:
    """Write the file `local` (on the computer) as the file at `path` in a folder of the top
    level ordinary MTP does not show, as Garmin Express writes there, in a session of its own:
    the folder listed afresh (the ordinary listing and Garmin's together), the space checked
    with the old file counted (free + old >= new + `margin`, else WriteRefused, nothing
    changed), the old file of that name deleted (hidden too), the new one announced in that
    folder and sent as it is read (ptp.WRITE_CHUNK at a time, never held whole), then checked:
    its size as the device gives it, and the file read back (`check`: FULL, the whole file
    against `local` and `md5`; SAMPLED, blocks of it against `local`, see FULL_CHECK_UP_TO). A
    file not written whole, or read back otherwise, is deleted at once (NotWritten, which says
    what is on the device); one the device does not give back stays (ContentsNotRead). No folder
    is ever made; nothing else is touched. A file written whole is added to what this
    connection read (_note_written); after a file not written or not read back the folders
    are read anew (forget). The time of the copy and of the check are logged
    apart. `name`: of the update, for the words; `copying` and `checking`: the heads of the
    steps told to `progress` (in bytes) while it is sent and read back. `keep_copy`: a write
    that changes nothing the device refuses first: a small file (PROBE) written into the
    folder, read back and deleted (else WriteRefused), then the file there now copied whole
    to `keep_copy` on this computer before it is deleted (else WriteRefused); when the new one
    is then not written, the copy is put back once and read back, and the text says what
    became of it (Written.kept: the copy). `drop_copy`: the copy deleted once the new file is
    read back whole, or the old one put back; kept only when it could not be put back."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    folder, filename = "/".join(parts[:-1]), parts[-1]
    size = local.stat().st_size
    opener = session_for(storage, progress)
    if opener is None:
        state = NO_USB if storage.usb_blocked() else NO_SESSION
        raise WriteRefused(no_session_text(parts[0], state))
    log.info("hidden folders: writing %s (%s) into %s, in a session of its own", filename,
             applog.size(size), folder)
    say_to(progress)(reading_text([folder]))
    with opener as session:
        store, parent, real = _folder_in(session, folder)
        items = _children(session, store, parent.handle)
        old = named_in_any_case([i for i in items if not i.is_dir], filename)
        free = session.free_space(store)
        room = (free or 0) + (old.size if old is not None else 0)
        if free is None or room < size + margin:
            log.info("hidden folders: %s not written: %s free with the old file, %s needed",
                     filename, applog.size(room if free is not None else None),
                     applog.size(size + margin))
            raise WriteRefused(no_space_text(name, size + margin, room))
        kept = None
        if keep_copy is not None:
            say_to(progress)(probe_step_text(real))
            _probe(session, store, parent.handle, name)
        if keep_copy is not None and old is not None:
            kept = _copy_of(session, old, keep_copy, name,
                            byte_steps(progress, keeping_step_text(name)))
            say_to(progress)(removing_step_text(name))
        if old is not None:
            try:
                session.delete(old.handle, size=old.size)
            except ptp.PtpError as e:
                log.warning("hidden folders: the old %s not deleted (%s: %s)", old.name,
                            type(e).__name__, e)
                raise NotWritten(not_written_text(name, old_removed=False)) from e
            log.info("hidden folders: the old %s (%s) deleted", old.name, applog.size(old.size))
        try:
            how = _put(session, store, parent.handle, filename, local, size, md5, check,
                       byte_steps(progress, copying) if copying else None,
                       byte_steps(progress, checking) if checking else None, name)
        except ptp.PtpError as e:
            said = not_written_text(name, old_removed=old is not None)
            if kept is not None:
                say_to(progress)(putting_back_step_text(name))
                back = _put_back(session, store, parent.handle, old.name, kept, name)
                if back and drop_copy:
                    kept.path.unlink(missing_ok=True)
                    said += " " + put_back_dropped_text()
                else:
                    said += " " + put_back_text(kept, back)
            forget(storage)
            raise NotWritten(said) from e
        except ContentsNotRead:
            forget(storage)  # written at its size, not read back: the folders read anew
            raise
    log.info("hidden folders: %s written into %s (%s), read back %s", filename, real,
             applog.size(size), how)
    _note_written(storage, real, filename, size)
    if kept is not None and drop_copy:
        kept.path.unlink(missing_ok=True)
        kept = None
    return Written(f"{real}/{filename}", size, kept)


def _note_written(storage: Storage, folder: str, filename: str, size: int) -> None:
    """The file just written whole into `folder`, at the size the device gave, in what this
    connection read: in place of the file of that name in any letter case (the old one, which
    was deleted), else after the rest. A folder not read stays unread; one found not there is
    no longer taken for missing (it is there, what else it holds not known)."""
    listing = _kept.get(connection(storage))
    if listing is None or listing.state != READ:
        return
    key = _key(folder)
    listing.missing.pop(key, None)
    found = listing.folders.get(key)
    if found is None:
        return
    real, entries = found
    new = Entry(filename, False, size)
    old = next((i for i, e in enumerate(entries)
                if not e.is_dir and e.name.casefold() == filename.casefold()), None)
    entries = [*entries, new] if old is None else [*entries[:old], new, *entries[old + 1:]]
    listing.folders[key] = (real, entries)


def _probe(session, store: int, parent: int, name: str) -> None:
    """A small file written into the folder `parent`, read back and deleted: WriteRefused when
    the device does not take it (what it left of it deleted, when it can be)."""
    try:
        _put(session, store, parent, PROBE, PROBE_DATA, len(PROBE_DATA), None, FULL, None, None,
             name)
    except (ptp.PtpError, ContentsNotRead) as e:
        log.warning("hidden folders: the test file not taken (%s: %s)", type(e).__name__, e)
        _delete_named(session, store, parent, PROBE)
        raise WriteRefused(probe_refused_text(name)) from e
    if not _delete_named(session, store, parent, PROBE):
        raise WriteRefused(probe_refused_text(name))
    log.info("hidden folders: the test file written, read back and deleted")


def _delete_named(session, store: int, parent: int, filename: str) -> bool:
    """The file `filename` in the folder `parent` deleted, when it is there; whether it is
    gone."""
    if session.broken:
        return False
    try:
        for item in _children(session, store, parent):
            if not item.is_dir and item.name == filename:
                session.delete(item.handle, size=item.size)
    except ptp.PtpError as e:
        log.warning("hidden folders: %s not deleted (%s: %s)", filename, type(e).__name__, e)
        return False
    return True


def _copy_of(session, item, dest: Path, name: str,
             told: Callable[[int, int | None], None] | None = None) -> KeptCopy:
    """The file `item` read whole into `dest` on this computer, with its checksums, its bytes
    told to `told` as they come; WriteRefused (nothing on the device changed) when it is not
    read whole."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha, md5 = hashlib.sha256(), hashlib.md5()  # MD5: the checksum Garmin gives, not security

    done = 0

    def piece(data: bytes) -> None:
        nonlocal done
        sha.update(data)
        md5.update(data)
        out.write(data)
        done += len(data)
        if told is not None:
            told(done, item.size)

    try:
        with open(dest, "wb") as out:
            read = session.read_into(item.handle, piece, size=item.size)
    except ptp.PtpError as e:
        log.warning("hidden folders: the copy of %s not read (%s: %s)", item.name,
                    type(e).__name__, e)
        raise WriteRefused(copy_not_read_text(name)) from e
    if read != item.size:
        log.warning("hidden folders: the copy of %s read %s of %s", item.name,
                    applog.size(read), applog.size(item.size))
        raise WriteRefused(copy_not_read_text(name))
    kept = KeptCopy(dest, read, sha.hexdigest(), md5.hexdigest())
    log.info("hidden folders: %s (%s) copied to this computer first, SHA-256 %s", item.name,
             applog.size(read), kept.sha256)
    return kept


def _put_back(session, store: int, parent: int, filename: str, kept: KeptCopy,
              name: str) -> bool:
    """The copy `kept` written back once as `filename`, read back against it; whether it is
    there whole. Not tried when the session is out of step."""
    if session.broken:
        log.warning("hidden folders: %s not put back: the session is out of step", filename)
        return False
    try:
        how = _put(session, store, parent, filename, kept.path, kept.size, kept.md5,
                   FULL if kept.size <= FULL_CHECK_UP_TO else SAMPLED, None, None, name)
    except (ptp.PtpError, ContentsNotRead) as e:
        log.warning("hidden folders: %s not put back (%s: %s)", filename, type(e).__name__, e)
        return False
    log.info("hidden folders: %s put back, read back %s", filename, how)
    return True


# --- a map: its files written in one session (maps, the trial on the command line) -------------

class MapNotWhole(DeviceError):
    """Some of a map was changed on the device and not all of it written: the text says how
    much, and that installing it again writes only what is missing."""


class UnlockFailed(DeviceError):
    """The files of a map are written, its unlock file `path` is not (`error`: why)."""

    def __init__(self, path: str, error: Exception):
        super().__init__(str(error))
        self.path, self.error = path, error


# A file of a map the device refused is written again, while the session goes on: removed (as it
# is at once) and written anew, after a pause, up to WRITE_TRIES times; an unlock file up to the
# times of the ordinary way of maps (given by maps).
WRITE_TRIES = 3
WRITE_PAUSE_S = 5


def map_not_whole_text(name: str, done: int, count: int) -> str:
    # TRANSLATORS: {name} is a map; it was being written into a folder the device hides and
    # stopped: {done} of its {count} files are on the device (the verb agrees with {done})
    return ngettext("{name} is incomplete on the device: {done} of {count} files was written. "
                    "Nothing else was removed, and the downloaded files stay on this computer: "
                    "install it again and only what is missing is written.",
                    "{name} is incomplete on the device: {done} of {count} files were written. "
                    "Nothing else was removed, and the downloaded files stay on this computer: "
                    "install it again and only what is missing is written.",
                    done).format(name=name, done=done, count=count)


def no_space_map_text(name: str, needed: int, free: int) -> str:
    # TRANSLATORS: {name} is a map; {needed} and {free} are sizes; its old files and those it
    # writes over count as free, as they are removed first
    return _("Not enough space for {name}: {needed} needed, {free} free with its old files "
             "counted. openExpress removes nothing else to make room.").format(
        name=name, needed=human_size(needed), free=human_size(free))


@dataclass
class FileToWrite:
    """A file of a map, downloaded, that write_map writes."""

    path: str  # on the device, as the server gives it
    local: Path  # downloaded, checked against its MD5
    size: int
    md5: str | None


@dataclass
class MapWritten:
    written: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # there already, read back the same
    removed: list[str] = field(default_factory=list)
    unlocks: list[str] = field(default_factory=list)


def write_map(storage: Storage, name: str, removing: list[str], files: list[FileToWrite],
              unlocks: list[tuple[str, bytes]], margin: int,
              progress: Callable[[str], None] | None = None, copying: str | None = None,
              checking: str | None = None, check: str = FULL,
              sleep: Callable[[float], None] = time.sleep, *, unlock_tries: int,
              unlock_pause: float) -> MapWritten:
    """The files of map `name` written into folders of the top level ordinary MTP does not
    show, in one session of its own, in Garmin Express's order: each folder listed afresh
    (the ordinary listing and Garmin's together), the space checked with the old files and
    those written over counted (else WriteRefused, nothing changed), the files of `removing`
    that are there deleted, then each file of `files` in their order: one there already of its
    size and read back the same is kept (installed again, only what is missing is written),
    any other one of that name deleted and the new one written and checked as `write` does;
    the unlock files last, read back whole. A file the device refuses is written again
    (WRITE_TRIES, an unlock file `unlock_tries`) while the session goes on; whatever stops it
    once the device is changed raises MapNotWhole (what was not written whole is deleted at
    once), an unlock file not written UnlockFailed. The computer is kept awake from the first
    removal to the last write (awake). No folder is ever made, nothing else is touched. The
    time of each copy and check go to the log apart."""
    paths = [*removing, *(f.path for f in files), *(path for path, _data in unlocks)]
    split = {p: (posixpath.dirname(_clean(p)), posixpath.basename(_clean(p))) for p in paths}
    folders = sorted({folder for folder, _name in split.values()}, key=str.casefold)
    opener = session_for(storage, progress)
    if opener is None:
        state = NO_USB if storage.usb_blocked() else NO_SESSION
        raise WriteRefused(no_session_text(folders[0].split("/")[0], state))
    new = sum(f.size for f in files) + sum(len(data) for _path, data in unlocks)
    log.info("hidden folders: writing map %s (%d files, %s) into %s, in a session of its own",
             name, len(files), applog.size(new), ", ".join(folders))
    say_to(progress)(reading_text(folders))
    out = MapWritten()
    with opener as session:
        places = {}
        for folder in folders:
            store, parent, _real = _folder_in(session, folder)
            items = {i.name.casefold(): i for i in _children(session, store, parent.handle)
                     if not i.is_dir}
            places[folder.casefold()] = (store, parent, items)

        def there(path: str):
            folder, filename = split[path]
            return places[folder.casefold()][2].get(filename.casefold())

        free = session.free_space(places[folders[0].casefold()][0])
        freed = sum(there(p).size for p in dict.fromkeys(paths) if there(p) is not None)
        if free is None or free + freed < new + margin:
            log.info("hidden folders: map %s not written: %s free with its old files, %s needed",
                     name, applog.size(free + freed if free is not None else None),
                     applog.size(new + margin))
            raise WriteRefused(no_space_map_text(name, new + margin, (free or 0) + freed))
        try:
            with awake.kept_awake(f"map {name} is written", say=say_to(progress)):
                for path in removing:
                    item = there(path)
                    if item is None:
                        continue
                    session.delete(item.handle, size=item.size)
                    del places[split[path][0].casefold()][2][item.name.casefold()]
                    out.removed.append(path)
                    log.info("hidden folders: the old %s (%s) deleted", item.name,
                             applog.size(item.size))
                total, before = sum(f.size for f in files), 0
                for f in files:
                    told = (byte_steps(progress, copying, before=before, total=total)
                            if copying else None)
                    checked = (byte_steps(progress, checking, before=before, total=total)
                               if checking else None)
                    if _put_map_file(session, places, split, f.path, f.local, f.size, f.md5, check,
                                     told, checked, name, keep=True, tries=WRITE_TRIES,
                                     pause=WRITE_PAUSE_S, sleep=sleep):
                        out.kept.append(f.path)
                    else:
                        out.written.append(f.path)
                    before += f.size
                for path, data in unlocks:
                    try:
                        _put_map_file(session, places, split, path, data, len(data), None, FULL,
                                      None, None, name, keep=False, tries=unlock_tries,
                                      pause=unlock_pause, sleep=sleep)
                    except (ptp.PtpError, ContentsNotRead) as e:
                        log.warning("hidden folders: the unlock file %s of map %s not written (%s)",
                                    posixpath.basename(path), name, type(e).__name__)
                        raise UnlockFailed(path, e) from e
                    out.unlocks.append(path)
        except (ptp.PtpError, ContentsNotRead) as e:
            done = len(out.written) + len(out.kept)
            log.warning("hidden folders: map %s stopped after %d of %d files (%s: %s)", name,
                        done, len(files), type(e).__name__, e)
            raise MapNotWhole(map_not_whole_text(name, done, len(files))) from e
        finally:
            forget(storage)  # what was read of the folders is not what is there now
    log.info("hidden folders: map %s written: %d files written, %d there already and read "
             "back the same, %d old files removed, %d unlock files", name, len(out.written),
             len(out.kept), len(out.removed), len(out.unlocks))
    return out


def _put_map_file(session, places, split, path: str, local: Path | bytes, size: int,
                  md5: str | None, check: str, told, checked, name: str, *, keep: bool,
                  tries: int, pause: float, sleep: Callable[[float], None]) -> bool:
    """One file of a map: True when one of its size was there and read back the same (kept,
    when `keep`); else any other one of that name deleted and this one written (_put), again
    after `pause` up to `tries` times while the session goes on."""
    folder, filename = split[path]
    store, parent, items = places[folder.casefold()]
    item = items.get(filename.casefold())
    if item is not None and keep and item.size == size:
        try:
            how = _read_back(session, item.handle, filename, local, size, md5, check, checked,
                             name)
        except (ptp.PtpWriteError, ContentsNotRead):
            log.info("hidden folders: %s there at its size, not the same: written again",
                     filename)
        else:
            log.info("hidden folders: %s there already, read back %s: kept", filename, how)
            return True
    if item is not None:
        session.delete(item.handle, size=item.size)
        log.info("hidden folders: the old %s (%s) deleted", item.name, applog.size(item.size))
    for attempt in range(1, tries + 1):
        try:
            _put(session, store, parent.handle, filename, local, size, md5, check, told,
                 checked, name)
            return False
        except ptp.PtpError:
            if session.broken or attempt == tries:
                raise
            log.warning("hidden folders: %s not written (try %d of %d): again in %g s",
                        filename, attempt, tries, pause)
            sleep(pause)
    return False


def _clean(path: str) -> str:
    return "/".join(p for p in path.replace("\\", "/").split("/") if p)


def _open_local(local: Path | bytes):
    """What is written, to read: a file on the computer, or bytes (an unlock file)."""
    return io.BytesIO(local) if isinstance(local, bytes) else open(local, "rb")


def _put(session, store: int, parent: int, filename: str, local: Path | bytes, size: int,
         md5: str | None, check: str, sent: Callable[[int, int | None], None] | None,
         checked: Callable[[int, int | None], None] | None, name: str) -> str:
    """One file announced in the folder `parent` and sent as it is read (the file on the
    computer, or bytes), its size checked and the file read back as `check` says; how it was
    read back. A file not written whole, or read back otherwise, is deleted at once and the
    error raised (ptp.PtpError); one the device does not give back stays (ContentsNotRead).
    The time and pace of the copy and of the check go to the log apart."""
    handle = None
    try:
        if sent is not None:
            sent(0, size)
        started = time.monotonic()
        with _open_local(local) as source:
            handle = session.send_object(store, parent, filename, ptp.Outgoing(
                source, size, None if sent is None else lambda done: sent(done, size)))
        log.info("hidden folders: %s (%s) sent in %s", filename, applog.size(size),
                 _pace(size, time.monotonic() - started))
        on_device = session.info(handle).size
        if on_device != size:
            raise ptp.PtpWriteError(f"{on_device} B on the device, {size} B written")
        return _read_back(session, handle, filename, local, size, md5, check, checked, name)
    except ptp.PtpError as e:
        handle = getattr(e, "handle", None) or handle
        log.warning("hidden folders: %s not written (%s: %s)", filename, type(e).__name__, e)
        if handle is not None:
            try:
                session.delete(handle, size=size)
            except ptp.PtpError as gone:
                log.warning("hidden folders: what was written of %s not deleted (%s)",
                            filename, type(gone).__name__)
        raise


def _read_back(session, handle: int, filename: str, local: Path | bytes, size: int,
               md5: str | None, check: str, told: Callable[[int, int | None], None] | None,
               name: str) -> str:
    """Read back the file just written as `check` says; how it was read ("whole", "by
    samples"). Raises ptp.PtpWriteError when it differs, ContentsNotRead when the device does
    not give it back."""
    if check == SAMPLED and size > FULL_CHECK_UP_TO and size > CHECK_BLOCK * (CHECK_SAMPLES + 2):
        started = time.monotonic()
        try:
            read = _compare_samples(session, handle, local, size, told)
        except ptp.PtpRefused as e:
            log.info("hidden folders: the device does not read parts (0x%04x): read back whole "
                     "instead", e.code)
        else:
            log.info("hidden folders: %s read back by samples (%s) in %s", filename, read,
                     _pace(size, time.monotonic() - started))
            return "by samples"
    started = time.monotonic()
    try:
        _compare_whole(session, handle, local, size, md5, told)
    except ptp.PtpRefused as e:
        log.info("hidden folders: %s written at its size; reading it back refused "
                 "(0x%04x): kept, contents not compared", filename, e.code)
        raise ContentsNotRead(contents_not_read_text(name)) from e
    log.info("hidden folders: %s read back whole (%s) in %s", filename, applog.size(size),
             _pace(size, time.monotonic() - started))
    return "whole"


def _compare_whole(session, handle: int, local: Path | bytes, size: int, md5: str | None,
                   told: Callable[[int, int | None], None] | None) -> None:
    """The whole file read back as it comes, compared with `local` byte for byte and with
    `md5` (when given)."""
    digest = hashlib.md5()
    done, same = 0, True
    if told is not None:
        told(0, size)
    with _open_local(local) as mine:
        def compare(piece: bytes) -> None:
            nonlocal done, same
            digest.update(piece)
            same = same and mine.read(len(piece)) == piece
            done += len(piece)
            if told is not None:
                told(done, size)

        session.read_into(handle, compare, size=size)
    if (not same or done != size
            or (md5 is not None and digest.hexdigest() != md5.casefold())):
        raise ptp.PtpWriteError("the file read back differs from the one written")


def _compare_samples(session, handle: int, local: Path | bytes, size: int,
                     told: Callable[[int, int | None], None] | None) -> str:
    """The first and the last CHECK_BLOCK of the file and CHECK_SAMPLES blocks at random in
    between, each read back and compared with `local`; what was read, for the log."""
    blocks = (size - CHECK_BLOCK) // CHECK_BLOCK  # whole blocks before the last one
    middle = sorted(_random.sample(range(1, blocks), CHECK_SAMPLES))
    offsets = [0, *(k * CHECK_BLOCK for k in middle), size - CHECK_BLOCK]
    total, done = CHECK_BLOCK * len(offsets), 0
    if told is not None:
        told(0, total)
    with _open_local(local) as mine:
        for offset in offsets:
            back = session.read_part(handle, offset, CHECK_BLOCK)
            mine.seek(offset)
            if back != mine.read(CHECK_BLOCK):
                raise ptp.PtpWriteError("a block read back differs from the one written")
            done += len(back)
            if told is not None:
                told(done, total)
    return f"{len(offsets)} blocks, {applog.size(total)} of {applog.size(size)}"


def _folder_in(session, folder: str):
    """(store, the folder's object, its path in the device's letter case), looked up afresh
    from the top level through both listings; WriteRefused when it is not there."""
    parts = folder.split("/")
    for store in session.storages():
        top = named_in_any_case(
            [i for i in _children(session, store, ptp.ROOT) if i.is_dir], parts[0])
        if top is None:
            continue
        current, real = _descend(lambda s, p: _children(session, s, p), store, top, parts[1:])
        if current is not None:
            return store, current, "/".join(real)
    raise WriteRefused(no_folder_text(folder))

