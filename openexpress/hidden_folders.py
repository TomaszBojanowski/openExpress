"""Folders of a device's top level that ordinary MTP does not show (a car navigator keeps its
software, its time zone map and its maps in .System), read through openExpress's own USB
session: the names and sizes of what the folders asked hold, never the contents of a file, and
nothing is written there. The session takes the device from the file manager for a moment, so
what it read is kept for the connection (kept) until the device is unplugged or the user asks
again (forget: Check Again, Refresh); updates.mark_present, maps.mark_waiting and history.count
take it from there. Whatever writes there one day lists its folder anew in its own session."""

from __future__ import annotations

import hashlib
import logging
import posixpath
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from openexpress.device import ptp
from openexpress.device.storage import (
    DeviceError,
    DeviceNotResponding,
    Entry,
    RemountFailed,
    Storage,
    named_in_any_case,
)
from openexpress.i18n import _
from openexpress.progress import say_to

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
    folders asked that the device does not have; `state`: READ, or why nothing was read
    (NO_SESSION, NO_USB, FAILED)."""

    folders: dict[str, tuple[str, list[Entry]]] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    state: str = READ

    def tops(self) -> list[str]:
        """The folders of the top level read, in the device's letter case."""
        return _tops(real for real, _entries in self.folders.values())

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


# What each connection read, by the connection (_connection).
_kept: dict[tuple, Listing] = {}


def _connection(storage: Storage) -> tuple:
    """The device's address and its place on USB (new at each plugging in); a folder (a drive,
    the tests) by its path."""
    usb = getattr(storage, "usb", None)
    where = getattr(storage, "device_uri", None) or str(getattr(storage, "root", ""))
    return where, getattr(usb, "bus", None), getattr(usb, "address", None)


def kept(storage: Storage) -> Listing | None:
    """What a session read of this connection; None when none was opened for it."""
    return _kept.get(_connection(storage))


def forget(storage: Storage) -> None:
    """The next look at the device reads its hidden folders anew ("Check Again")."""
    _kept.pop(_connection(storage), None)


def reading_text(folders: Iterable[str]) -> str:
    # TRANSLATORS: a step: openExpress reads a folder the device hides from the ordinary USB
    # connection; {folder}: e.g. .System
    return _("Reading the folder {folder}…").format(folder=", ".join(_tops(folders)))


def no_session_text(folder: str, state: str) -> str:
    """Why the folder could not be read (Listing.state NO_USB, else NO_SESSION or FAILED)."""
    if state == NO_USB:
        # TRANSLATORS: {folder}: a folder the device hides from the ordinary USB connection,
        # e.g. .System
        return _("The Flatpak version has no access to USB devices, so it cannot read the "
                 "folder {folder}. Give it the permission (see the README) or use the version "
                 "from source.").format(folder=folder)
    # TRANSLATORS: {folder}: a folder the device hides from the ordinary USB connection, e.g.
    # .System
    return _("openExpress cannot open its own USB session with this device, so it cannot read "
             "the folder {folder}.").format(folder=folder)


def _tops(folders: Iterable[str]) -> list[str]:
    """The folders of the top level among `folders`, each once, in the order given."""
    out: list[str] = []
    for folder in folders:
        top = folder.strip("/").split("/")[0]
        if top and top.casefold() not in {t.casefold() for t in out}:
            out.append(top)
    return out


def folders_to_read(device, needed: Iterable[str]) -> list[str]:
    """The folders a session reads, the same in the window and the command line: those
    `needed` (by updates and maps), the folders of the top level they are in and those the
    device is known to hide (known_devices.hidden_tops), and what its GarminDevice.xml names
    under them (history.declared_under: what Device Status counts there), each once."""
    from openexpress import history, known_devices

    needed = [f.replace("\\", "/").strip("/") for f in needed]
    tops = {f.split("/")[0] for f in needed if f}
    tops |= set(known_devices.hidden_tops(getattr(device, "unit_id", None)))
    out: dict[str, str] = {}
    for folder in [*needed, *tops, *history.declared_under(device, tops)]:
        if folder:
            out.setdefault(folder.casefold(), folder)
    return sorted(out.values(), key=str.casefold)


def read(storage: Storage, folders: Iterable[str], why: str,
         progress: Callable[[str], None] | None = None, unit_id: int | None = None) -> Listing:
    """Read `folders` (paths from the top level) through a session of our own, keep what it
    read for the connection, and return it: each folder by the ordinary listing and Garmin's
    together, as they are now. `why`: for the log, what needs them. A session that cannot be
    opened or fails leaves a listing that says why (Listing.state); only a device not mounted
    again after it raises (RemountFailed: unplug it and plug it in). `unit_id`: the device's,
    which then keeps the folders of its top level found hidden (known_devices.hidden_tops)."""
    wanted = sorted({f.strip("/") for f in folders if f.strip("/")}, key=str.casefold)
    listing = Listing()
    opener = storage.exclusive_ptp() if wanted else None
    if opener is None:
        listing.state = NO_USB if wanted and storage.usb_blocked() else NO_SESSION
        if wanted:
            log.info("hidden folders: %s not read: %s", ", ".join(wanted), listing.state)
        _kept[_connection(storage)] = listing
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
    except DeviceError as e:
        log.warning("hidden folders: not read, %s (%s: %s)", FAILED, type(e).__name__, e)
        listing = Listing(state=FAILED)
    else:
        if unit_id and listing.tops():
            from openexpress import known_devices

            with known_devices.optional_change("devices: the hidden folders not kept (%s)"):
                known_devices.note_hidden_tops(unit_id, listing.tops())
        log.info("hidden folders: %s", "; ".join(
            f"{real}: {len(entries)} entries" for real, entries in listing.folders.values())
            + "".join(f"; {m}: not on the device" for m in sorted(listing.missing)))
    _kept[_connection(storage)] = listing
    return listing


def _children(session, store: int, parent: int) -> list:
    """What ordinary MTP and Garmin's listing show in a folder, the top level too (where the
    hidden folder of a navigator is); the ordinary ones alone on a device without Garmin's
    listing."""
    found = session.children(store, parent)
    try:
        extra = session.hidden_children(store, parent)
    except ptp.PtpRefused as e:
        if e.code != ptp.OPERATION_NOT_SUPPORTED:
            raise
        return found
    known = {item.handle for item in found}
    return found + [item for item in extra if item.handle not in known]


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
            listing.missing.add(folder.casefold())
            continue
        store, current = place
        real = [current.name]
        for part in parts[1:]:
            current = named_in_any_case(
                [i for i in children(store, current.handle) if i.is_dir], part)
            if current is None:
                break
            real.append(current.name)
        if current is None:
            listing.missing.add(folder.casefold())
            continue
        entries = [Entry(i.name, i.is_dir, 0 if i.is_dir else i.size)
                   for i in children(store, current.handle)]
        listing.folders[folder.casefold()] = ("/".join(real), entries)


# --- writing one file there (a trial: updates._install with hidden_trial) ---------------------


class WriteRefused(DeviceError):
    """Nothing was changed on the device: why not."""


class NotWritten(DeviceError):
    """The file was not written; the text says what is on the device now."""


class ContentsNotRead(DeviceError):
    """The file was written at its size, and the device did not let it be read back."""


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
    return _("{name} was written at its size, but the device did not let openExpress read it "
             "back, so its contents were not compared. The trial stops here: nothing else is "
             "written.").format(name=name)


def no_space_text(name: str, needed: int, free: int) -> str:
    from openexpress.i18n import human_size

    # TRANSLATORS: {needed} and {free} are sizes; the old file of the update counts as free, as
    # it is removed before the new one is written
    return _("Not enough space for {name}: {needed} needed, {free} free with its old file "
             "counted. openExpress removes nothing else to make room.").format(
        name=name, needed=human_size(needed), free=human_size(free))


def no_folder_text(folder: str) -> str:
    # TRANSLATORS: {folder}: e.g. .System/Map
    return _("The folder {folder} is not on the device; openExpress makes no folder "
             "there.").format(folder=folder)


@dataclass
class Written:
    real: str  # where the file is on the device, in its letter case
    size: int


def write(storage: Storage, path: str, data: bytes, md5: str | None, name: str, margin: int,
          progress: Callable[[str], None] | None = None) -> Written:
    """Write `data` as the file at `path` in a folder of the top level ordinary MTP does not
    show, as Garmin Express writes there, in a session of its own: the folder listed afresh
    (the ordinary listing and Garmin's together), the space checked with the old file counted
    (free + old >= new + `margin`, else WriteRefused, nothing changed), the old file of that
    name deleted (hidden too), the new one announced in that folder and sent, then checked:
    its size as the device gives it, and the whole file read back against `md5`. A file not
    written whole, or read back otherwise, is deleted at once (NotWritten, which says what is on
    the device); one the device does not give back stays (ContentsNotRead). No folder is ever
    made; nothing else is touched. `name`: of the update, for the words."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    folder, filename = "/".join(parts[:-1]), parts[-1]
    opener = storage.exclusive_ptp()
    if opener is None:
        state = NO_USB if storage.usb_blocked() else NO_SESSION
        raise WriteRefused(no_session_text(parts[0], state))
    log.info("hidden folders: writing %s (%d B) into %s, in a session of its own", filename,
             len(data), folder)
    say_to(progress)(reading_text([folder]))
    with opener as session:
        store, parent, real = _folder_in(session, folder)
        items = _children(session, store, parent.handle)
        old = named_in_any_case([i for i in items if not i.is_dir], filename)
        free = session.free_space(store)
        room = (free or 0) + (old.size if old is not None else 0)
        if free is None or room < len(data) + margin:
            log.info("hidden folders: %s not written: %s B free with the old file, %s B needed",
                     filename, room if free is not None else "?", len(data) + margin)
            raise WriteRefused(no_space_text(name, len(data) + margin, room))
        if old is not None:
            try:
                session.delete(old.handle)
            except ptp.PtpError as e:
                log.warning("hidden folders: the old %s not deleted (%s: %s)", old.name,
                            type(e).__name__, e)
                raise NotWritten(not_written_text(name, old_removed=False)) from e
            log.info("hidden folders: the old %s (%d B) deleted", old.name, old.size)
        handle = None
        try:
            handle = session.send_object(store, parent.handle, filename, data)
            size = session.info(handle).size
            if size != len(data):
                raise ptp.PtpWriteError(f"{size} B on the device, {len(data)} B written")
            try:
                back = session.read_file(handle)
            except ptp.PtpRefused as e:
                log.info("hidden folders: %s written at its size; reading it back refused "
                         "(0x%04x): kept, contents not compared", filename, e.code)
                raise ContentsNotRead(contents_not_read_text(name)) from e
            if md5 is not None and hashlib.md5(back).hexdigest() != md5.casefold():
                raise ptp.PtpWriteError("the file read back differs from the one written")
        except ContentsNotRead:
            raise
        except ptp.PtpError as e:
            handle = getattr(e, "handle", None) or handle
            log.warning("hidden folders: %s not written (%s: %s)", filename, type(e).__name__, e)
            if handle is not None:
                try:
                    session.delete(handle)
                except ptp.PtpError as gone:
                    log.warning("hidden folders: what was written of %s not deleted (%s)",
                                filename, type(gone).__name__)
            raise NotWritten(not_written_text(name, old_removed=old is not None)) from e
    log.info("hidden folders: %s written into %s (%d B), read back whole", filename, real,
             len(data))
    forget(storage)  # what was read of the folder is not what is there now
    return Written(f"{real}/{filename}", len(data))


def _folder_in(session, folder: str):
    """(store, the folder's object, its path in the device's letter case), looked up afresh
    from the top level through both listings; WriteRefused when it is not there."""
    parts = folder.split("/")
    for store in session.storages():
        current = named_in_any_case(
            [i for i in _children(session, store, ptp.ROOT) if i.is_dir], parts[0])
        real = [current.name] if current is not None else []
        for part in parts[1:]:
            if current is None:
                break
            current = named_in_any_case(
                [i for i in _children(session, store, current.handle) if i.is_dir], part)
            if current is not None:
                real.append(current.name)
        if current is not None:
            return store, current, "/".join(real)
    raise WriteRefused(no_folder_text(folder))

