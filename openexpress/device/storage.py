"""Access to device files.

Paths are relative to the root of the device storage and use `/`.
Directory names in GarminDevice.xml have inconsistent case
(`Garmin/...` and `GARMIN/...`), so paths are resolved
case-insensitively.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import posixpath
import shutil
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from openexpress import applog
from openexpress.errors import UserFacingError
from openexpress.i18n import _, human_size

log = logging.getLogger(__name__)


class DeviceError(UserFacingError):
    pass


class CopyNotStarted(DeviceError):
    """A write that failed before anything on the device was touched (e.g. the copy command
    could not be started): the file under the name, if any, is as it was."""


class RemountFailed(DeviceError):
    """The device was not mounted again after a PTP session. `says_replug`: the text says
    itself to unplug the device and plug it in again."""

    says_replug = False


class DeviceNotResponding(DeviceError):
    """A device that stopped answering in the middle of an operation: over MTP, a step of a
    read with no answer within its time limit. gvfs stays blocked on such a device until it is
    unplugged, so this error is never taken for a missing file and nothing more is read; its
    text says what to do (`says_replug`)."""

    says_replug = True
    usb = None  # where the device is on USB, when known (gio_storage.GarminUsb)

    @classmethod
    def during(cls, action: str) -> DeviceNotResponding:
        """The error of `action` (e.g. "Cannot list GARMIN"), with what to do."""
        return cls(_("{action}: the device stopped responding. Unplug it and plug it in "
                     "again.").format(action=action))


def replug_text(error: DeviceError) -> str:
    """A device not mounted again (RemountFailed) or one that stopped responding: its error as
    a sentence, then what to do, unless the error says that itself (`says_replug`)."""
    text = str(error).rstrip()
    sentence = text if text.endswith((".", "!", "?", "…")) else text + "."
    if getattr(error, "says_replug", False):
        return sentence
    return _("{error} Unplug the device and plug it in again.").format(error=sentence)


def refresh_to_read_again(storage: Storage, progress=None) -> bool:
    """Storage.refresh before a file read cut short is read again, as a sync and a backup
    ask it: whether the device was mounted again; a device not mounted again is raised with
    what to do (replug_text)."""
    try:
        return storage.refresh(progress)
    except RemountFailed as e:
        raise RemountFailed(replug_text(e)) from e


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


class Storage(ABC):
    """Common interface: a local directory (UMS, tests) or MTP via gvfs."""

    label = "storage"
    # A Garmin device in mass storage mode that the program found itself (UmsStorage).
    mass_storage = False
    # A USB drive taken for a Garmin drive for testing (UmsStorage.test_drive).
    test_drive = False
    # Whether write_file keeps the file it replaces until the new one is whole (a temporary
    # copy renamed at the end), so both have to fit for a while: what the space checks count.
    # Over MTP the old file is deleted before the new one is sent. (What a failed write
    # leaves behind is cleaned up by the storage itself: _discard_partial.)
    replaces_atomically = False

    @abstractmethod
    def _list(self, real_path: str) -> list[Entry]: ...

    @abstractmethod
    def _read(self, real_path: str) -> bytes: ...

    @abstractmethod
    def _write(self, real_path: str, data: bytes) -> None: ...

    @abstractmethod
    def _delete(self, real_path: str) -> None: ...

    @abstractmethod
    def _mkdir(self, real_path: str) -> None: ...

    def free_space(self) -> int | None:
        return None

    @property
    def usb_id(self) -> str | None:
        """The USB ID of the storage's device, "vendor:product" (never its serial number), as
        its `usb` (gio_storage.GarminUsb) says it; None when that is not known. A storage that
        is not on USB (a folder) has no `usb`, and so no usb_id either (AttributeError)."""
        usb = self.usb
        return usb.usb_id if usb is not None else None

    @property
    def stalled(self) -> bool:
        """Whether the device stopped responding to this storage (over MTP: a step with no
        answer within its time limit); then every operation of it fails at once."""
        return False

    def exclusive_ptp(self):
        """A context manager that gives a PTP session (`openexpress.device.ptp.Session`)
        to the device, for files ordinary MTP does not show; None when that is not
        possible here. The storage itself cannot be used inside the block."""
        return

    def refresh(self, progress=None) -> bool:
        """Has the device's files looked at anew where the storage keeps what it once saw of
        them: over MTP gvfs gives the size a file had when it was first looked at, so a file
        the device writes while it is connected reads cut short until the device is mounted
        again. True once that is done; False where there is nothing to refresh (a folder, a
        USB drive: their sizes are current) or it cannot be done. `progress` hears its step
        (progress.refreshing_text) when a refresh is tried."""
        return False

    def usb_blocked(self) -> bool:
        """Whether a sandbox keeps the program away from the USB device itself (a Flatpak
        without the --device=usb permission), so that a PTP session is out of reach here
        whatever the device offers."""
        return False

    def capacity(self) -> int | None:
        """Total size of the storage in bytes, when known."""
        return None

    def resolve(self, path: str) -> str | None:
        """Return the real path (with the correct case) or None."""
        real = ""
        for part in [p for p in path.split("/") if p]:
            try:
                entries = self._list(real)
            except DeviceNotResponding:
                raise  # not a missing folder
            except DeviceError:
                return None
            match = _entry_named(entries, part)
            if match is None:
                return None
            real = posixpath.join(real, match.name) if real else match.name
        return real

    def exists(self, path: str) -> bool:
        return self.resolve(path) is not None

    def info(self, real_path: str) -> Entry | None:
        """The entry of the file or folder at this real path (in its letter case, as listed
        before), without listing the folders above it where the storage can tell it alone
        (over MTP each listing is a round trip); None: not there."""
        parent, name = posixpath.split(real_path)
        try:
            return next((e for e in self._list(parent) if e.name == name), None)
        except DeviceNotResponding:
            raise
        except DeviceError:
            return None

    def read_at(self, real_path: str) -> bytes:
        """The file at this real path (in its letter case, as listed before), not looked
        for again."""
        return self._read(real_path)

    def listdir(self, path: str) -> list[Entry]:
        real = self.resolve(path)
        if real is None:
            return []
        return self._list(real)

    def list_at(self, real_path: str) -> list[Entry]:
        """The entries of the folder at this real path (in its letter case, as listed before),
        not looked for again."""
        return self._list(real_path)

    def walk_files(self, path: str) -> list[tuple[str, Entry]]:
        """Recursive list of files under `path`: (real path, entry)."""
        real = self.resolve(path)
        if real is None:
            return []
        out: list[tuple[str, Entry]] = []
        stack = [real]
        while stack:
            cur = stack.pop()
            for e in self._list(cur):
                p = posixpath.join(cur, e.name) if cur else e.name
                if e.is_dir:
                    stack.append(p)
                else:
                    out.append((p, e))
        out.sort(key=lambda t: t[0])
        return out

    def read(self, path: str) -> bytes:
        real = self.resolve(path)
        if real is None:
            raise DeviceError(_("File not found on the device: {path}").format(path=path))
        return self._read(real)

    def read_start(self, path: str, count: int) -> bytes:
        """The first `count` bytes of a file (fewer when it is shorter), without reading the
        rest: for the header of a large file."""
        real = self.resolve(path)
        if real is None:
            raise DeviceError(_("File not found on the device: {path}").format(path=path))
        return self._read_start(real, count)

    def _read_start(self, real_path: str, count: int) -> bytes:
        return self._read(real_path)[:count]

    def _target(self, path: str) -> str:
        parent, name = _split_file_path(path)
        real_parent = self._ensure_dir(parent)
        existing = named_in_any_case(self._list(real_parent), name)
        if existing is not None and existing.is_dir:
            raise DeviceError(_("Cannot write {path}: it is a directory").format(path=path))
        target_name = existing.name if existing else name
        return posixpath.join(real_parent, target_name) if real_parent else target_name

    def write(self, path: str, data: bytes) -> str:
        """Write a file, creating missing directories. Returns the real path. After a failure
        the storage cleans up what the write left under the name, as write_file does
        (_discard_partial): never half a file; the old file stays when the write never
        started (CopyNotStarted, DeviceNotResponding)."""
        real = self._target(path)
        try:
            self._write(real, data)
        except (CopyNotStarted, DeviceNotResponding):
            raise
        except BaseException:
            self._discard_partial(real)
            raise
        return real

    def write_file(self, path: str, local: Path, progress=None, keep_partial: bool = False) -> str:
        """Stream a local file to the device (large map/firmware files). After a failure the
        storage itself cleans up what the copy left under the name (_discard_partial), unless
        the copy never started (CopyNotStarted: the old file is untouched; DeviceNotResponding:
        a copy has no time limit, so the device stopped responding before it, and a clean-up
        would wait on it) or `keep_partial` (the caller's to keep or remove: a file other maps
        use, never deleted, or an own map, removed and said by the caller)."""
        real = self._target(path)
        try:
            self._write_file(real, Path(local), progress)
        except (CopyNotStarted, DeviceNotResponding):
            raise
        except BaseException:
            if not keep_partial:
                self._discard_partial(real)
            raise
        return real

    def write_checked(self, path: str, data: bytes, *, error: type[Exception] = DeviceError) -> str:
        """write, then the size of the file on the device checked: another size (or one not
        known) deletes what was written and raises `error` (the error class of the caller,
        e.g. CiqError). Returns the real path."""
        real = self.write(path, data)
        self._check_written(real, len(data), error, keep=False)
        return real

    def write_file_checked(self, path: str, local: Path, progress=None, *,
                           keep_partial: bool = False,
                           error: type[Exception] = DeviceError, md5: str | None = None,
                           checking=None) -> str:
        """write_file, then its size on the device checked against the local file's: another
        size (or one not known) deletes what was written, unless `keep_partial` (as in
        write_file), and raises `error`. Returns the real path. `md5`: what the
        bytes are to hash to (the server's), for a storage that reads them back (a drive);
        `checking` hears that reading as `progress` hears the copy (done, size)."""
        real = self.write_file(path, local, progress, keep_partial=keep_partial)
        self._check_written(real, Path(local).stat().st_size, error, keep=keep_partial)
        return real

    def _check_written(self, real: str, size: int, error: type[Exception], keep: bool) -> None:
        found = self.size(real)
        if found == size:
            return
        # Sizes only: the name may be the user's own (a track, a map).
        log.warning("device: a file written has %s instead of %s%s",
                    "no size" if found is None else applog.size(found), applog.size(size),
                    "" if keep else "; deleted")
        self._not_written(real, error, keep)

    def _not_written(self, real: str, error: type[Exception], keep: bool) -> NoReturn:
        """A file a check found other than it was written (said in the log by the check):
        deleted, unless `keep`, and `error` raised."""
        if not keep:
            self.delete_quietly(real)
        raise error(_("Write verification of {path} failed").format(path=real))

    def delete_quietly(self, path: str) -> bool:
        """Delete `path` after a failure that is said already: a failure of the deletion goes
        to the log only (one WARNING), but a stall (DeviceNotResponding) goes on. Whether it was
        deleted."""
        try:
            self.delete(path)
        except DeviceNotResponding:
            raise  # a stall stops what meets it: nothing more is asked of the device
        except Exception as e:  # noqa: BLE001 - cleanup after a failure: that one is reported
            log.warning("Cannot delete %s: %s", path, e)
            return False
        return True

    def require_space(self, need: int, margin: int, *,
                      error: type[Exception] = DeviceError, free=...,
                      margin_said: bool = False) -> int | None:
        """Room on the device for `need` bytes and `margin` kept free, or `error` saying how
        much is needed and free (`margin_said`: and how much is kept free, a margin that
        depends on the device). The free space, or None when it is not known (then nothing
        is checked); `free`: as the device told it already, not asked again."""
        if free is ...:
            free = self.free_space()
        if free is not None and need + margin > free:
            if margin_said:
                raise error(_("Not enough space on the device: {need} needed and {margin} kept "
                              "free, but only {free} free")
                            .format(need=human_size(need), margin=human_size(margin),
                                    free=human_size(free)))
            raise error(_("Not enough space on the device: {need} needed, {free} free")
                        .format(need=human_size(need), free=human_size(free)))
        return free

    def _discard_partial(self, real_path: str) -> None:  # noqa: B027 - nothing by default
        """What a copy that started and failed left under the name. Nothing here: a drive
        copies to a temporary file, which it removes itself."""

    def _write_file(self, real_path: str, local: Path, progress=None) -> None:
        self._write(real_path, local.read_bytes())

    def make_dir(self, path: str) -> str:
        """Create a directory with its missing parents. Returns the real path."""
        return self._ensure_dir(path)

    def size(self, path: str) -> int | None:
        real = self.resolve(path)
        if real is None:
            return None
        parent, name = posixpath.split(real)
        return next((e.size for e in self._list(parent) if e.name == name and not e.is_dir), None)

    def sizes(self, paths: list[str]) -> dict[str, int | None]:
        """size() of each path, with each folder on the way listed once for all of them (over
        MTP every listing is a round trip through gvfs). The same answers: names in any
        letter case, the exact one first; None for what is not there, or is a folder."""
        listed: dict[str, list[Entry] | None] = {}

        def entries(real: str) -> list[Entry] | None:
            if real not in listed:
                try:
                    listed[real] = self._list(real)
                except DeviceNotResponding:
                    raise  # not a missing folder
                except DeviceError:
                    listed[real] = None
            return listed[real]

        def size_of(path: str) -> int | None:
            real, entry = "", None
            for part in [p for p in path.split("/") if p]:
                found = entries(real)
                if found is None:
                    return None
                entry = _entry_named(found, part)
                if entry is None:
                    return None
                real = posixpath.join(real, entry.name) if real else entry.name
            return entry.size if entry is not None and not entry.is_dir else None

        return {path: size_of(path) for path in dict.fromkeys(paths)}

    def delete(self, path: str) -> None:
        real = self.resolve(path)
        if real == "":
            raise DeviceError(_("Refusing to delete the root of the device storage"))
        if real is not None:
            self._delete(real)

    def _ensure_dir(self, path: str) -> str:
        real = ""
        for part in [p for p in path.split("/") if p]:
            match = named_in_any_case(self._list(real), part)
            name = match.name if match is not None else part
            nxt = posixpath.join(real, name) if real else name
            if match is None:
                self._mkdir(nxt)
            real = nxt
        return real


def named_in_any_case(items, name: str):
    """The first of `items` (each with a `name`, in the order the device lists them) whose name
    is `name` in any letter case; None when none is. What a write and the folders made for it
    take, and what PTP looks a path up by."""
    wanted = name.casefold()
    return next((item for item in items if item.name.casefold() == wanted), None)


def _entry_named(entries: list[Entry], name: str) -> Entry | None:
    """The entry of this name in its exact letter case, else the first in any
    (named_in_any_case): what looking a path up takes."""
    exact = next((e for e in entries if e.name == name), None)
    return exact if exact is not None else named_in_any_case(entries, name)


def _split_file_path(path: str) -> tuple[str, str]:
    parent, name = posixpath.split(path.strip("/"))
    if not name.strip():
        raise DeviceError(_("Invalid file name: “{path}”").format(path=path))
    return parent, name


@contextmanager
def _os_errors(what: str):
    try:
        yield
    except OSError as e:
        raise DeviceError(f"{what}: {e.strerror or e}") from e


# Bytes written at a time when a large file is copied to a folder; one progress step each.
COPY_CHUNK = 1 << 20
# A file read back from a drive in less than about a second is not told as a step: below
# this size its check would only flash by.
CHECK_TOLD_FROM = 16 << 20
# A file being copied is named ".<name><PART_SUFFIX>" until it is complete.
PART_SUFFIX = ".openexpress-part"


def _told(chunks, checking, size: int):
    """The chunks of a file read back, each told to `checking` (done, size) after it, from
    0; told nothing when nobody listens or the file is under CHECK_TOLD_FROM."""
    if checking is None or size < CHECK_TOLD_FROM:
        yield from chunks
        return
    done = 0
    checking(done, size)
    for chunk in chunks:
        done += len(chunk)
        checking(done, size)
        yield chunk


def _md5_of(chunks) -> str:
    """The MD5 of bytes given in chunks, in lower-case hex."""
    digest = hashlib.md5()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def _sync_folder(folder: Path) -> None:
    """Put a folder's entries (a new or renamed file) on the disk. A file system that cannot
    sync a folder says so (EINVAL): then there is nothing more to put there."""
    fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.ENOTSUP):
            raise
    finally:
        os.close(fd)


class LocalStorage(Storage):
    """Directory on disk: a device in mass storage mode, a copy, or tests. A write counts only
    once it is on the disk (fsync of the file and of its folder)."""

    replaces_atomically = True  # a temporary copy, renamed when whole (_replace)

    def __init__(self, root: Path):
        self.root = Path(root)
        self.label = str(self.root)
        if not self.root.is_dir():
            raise DeviceError(_("Not a directory: {path}").format(path=root))

    def _p(self, real: str) -> Path:
        return self.root / real if real else self.root

    def _list(self, real_path: str) -> list[Entry]:
        p = self._p(real_path)
        if not p.is_dir():
            raise DeviceError(_("Not a directory: {path}").format(path=real_path))
        out = []
        with _os_errors(_("Cannot list {path}").format(path=real_path or "/")):
            for child in p.iterdir():
                st = child.stat()
                out.append(Entry(child.name, child.is_dir(), st.st_size, st.st_mtime))
        return out

    def _read(self, real_path: str) -> bytes:
        with _os_errors(_("Cannot read {path}").format(path=real_path)):
            return self._p(real_path).read_bytes()

    def info(self, real_path: str) -> Entry | None:
        p = self._p(real_path)
        try:
            st = p.stat()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise DeviceError(f"{_('Cannot read {path}').format(path=real_path)}: "
                              f"{e.strerror or e}") from e
        return Entry(p.name, p.is_dir(), st.st_size, st.st_mtime)

    def _read_start(self, real_path: str, count: int) -> bytes:
        with _os_errors(_("Cannot read {path}").format(path=real_path)), \
                open(self._p(real_path), "rb") as f:
            return f.read(count)

    def _write(self, real_path: str, data: bytes) -> None:
        """As _write_file: under a temporary name, synced, then renamed."""
        self._replace(real_path, [data])

    def _write_file(self, real_path: str, local: Path, progress=None) -> None:
        """Copied under a temporary name next to the target, synced, then renamed (_replace).
        `progress(done, total)` after each COPY_CHUNK bytes."""
        with _os_errors(_("Cannot write {path}").format(path=real_path)):
            total = Path(local).stat().st_size
            with open(local, "rb") as src:
                self._replace(real_path, iter(lambda: src.read(COPY_CHUNK), b""), progress,
                              total)

    def _replace(self, real_path: str, chunks, progress=None, total: int = 0) -> None:
        """The chunks written under a temporary name next to the target, synced, then renamed
        to it: the real name never holds half a file, also when the write fails or the cable
        goes out, and the old file stays whole until the new one is."""
        target = self._p(real_path)
        part = target.with_name("." + target.name + PART_SUFFIX)
        try:
            with _os_errors(_("Cannot write {path}").format(path=real_path)):
                fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
                try:
                    done = 0
                    for chunk in chunks:
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(fd, view):]
                        done += len(chunk)
                        if progress:
                            progress(done, total)
                    # A device in mass storage mode may lose what is still in the system's
                    # cache when its cable goes out.
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.replace(part, target)
                _sync_folder(target.parent)
        except BaseException:
            try:
                part.unlink(missing_ok=True)
            except OSError as e:
                log.warning("Cannot delete a partly copied file: %s", e.strerror or e)
            raise

    def _delete(self, real_path: str) -> None:
        with _os_errors(_("Cannot delete {path}").format(path=real_path)):
            self._p(real_path).unlink()

    def _mkdir(self, real_path: str) -> None:
        with _os_errors(_("Cannot create directory {path}").format(path=real_path)):
            self._p(real_path).mkdir()

    def write_checked(self, path: str, data: bytes, *, error: type[Exception] = DeviceError) -> str:
        """As Storage.write_checked, and its bytes read back whole from the drive after the
        write (_from_the_medium): other bytes are deleted and raise `error` before anything
        is told of the file (a file of Garmin Connect's queue is acknowledged only after
        this)."""
        real = super().write_checked(path, data, error=error)
        if b"".join(self._from_the_medium(real)) != data:
            self._other_bytes(real, len(data), error)
        return real

    def write_file_checked(self, path: str, local: Path, progress=None, *,
                           keep_partial: bool = False,
                           error: type[Exception] = DeviceError, md5: str | None = None,
                           checking=None) -> str:
        """As Storage.write_file_checked, and the file read back from the drive after the write
        (_from_the_medium, its bytes told to `checking`) and its MD5 compared with `md5` (the
        server's, hex in any letter case) or the local file's: other bytes are deleted, unless
        `keep_partial`, and raise `error` before anything is told of the file."""
        real = super().write_file_checked(path, local, progress, keep_partial=keep_partial,
                                          error=error, md5=md5)
        if md5 is None:
            with _os_errors(_("Cannot read {path}").format(path=str(local))), \
                    open(local, "rb") as f:
                md5 = _md5_of(iter(lambda: f.read(COPY_CHUNK), b""))
        size = Path(local).stat().st_size
        if _md5_of(_told(self._from_the_medium(real), checking, size)) != md5.strip().lower():
            self._other_bytes(real, size, error, keep=keep_partial)
        return real

    def _from_the_medium(self, real_path: str):
        """The bytes of a file written and synced, in chunks, as the drive holds them: the
        system's cache of the file is dropped first, else the bytes would come from it, not
        from the drive. What the drive itself keeps in its own cache only an eject tells."""
        with _os_errors(_("Cannot read {path}").format(path=real_path)), \
                open(self._p(real_path), "rb") as f:
            advise = getattr(os, "posix_fadvise", None)
            if advise is not None:
                advise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            yield from iter(lambda: f.read(COPY_CHUNK), b"")

    def _other_bytes(self, real: str, size: int, error: type[Exception],
                     keep: bool = False) -> None:
        """A file read back from the drive with other bytes than were written: deleted (unless
        `keep`), said in the log without its name, and `error` raised."""
        log.warning("drive: a file of %s read back with other bytes than were written%s",
                    applog.size(size), "" if keep else "; deleted")
        self._not_written(real, error, keep)

    def free_space(self) -> int | None:
        return shutil.disk_usage(self.root).free

    def capacity(self) -> int | None:
        return shutil.disk_usage(self.root).total


class UmsStorage(LocalStorage):
    """A Garmin device in mass storage mode: the folder where the system mounted the drive.
    Reads and writes like LocalStorage; it also knows where it is on USB and its address."""

    # Ejected, it stays on USB with its medium, not mounted (a Varia: no reset of its USB), so
    # an update written to it is followed by an eject.
    mass_storage = True

    def __init__(self, root: Path, device_uri: str, usb=None, volume_name: str | None = None):
        super().__init__(root)
        # The label of the volume as Gio gives it ("GARMIN"): what is shown instead of the
        # mount path, which has the user name.
        self.volume_name = volume_name or None
        # The address of the mount as Gio gives it. The label ends with "/", like the storage
        # of an MTP device below its mount: the window tells by it which mount it is on.
        self.device_uri = device_uri
        self.label = device_uri.rstrip("/") + "/"
        self.usb = usb  # gio_storage.GarminUsb, when known

    @property
    def test_drive(self) -> bool:
        """A USB drive of another vendor than Garmin, taken for a Garmin drive for testing
        (gio_storage.TEST_DRIVE_ENV): it carries the GarminDevice.xml of a real device."""
        return self.usb is not None and self.usb.for_testing
