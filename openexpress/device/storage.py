"""Access to device files.

Paths are relative to the root of the device storage and use `/`.
Directory names in GarminDevice.xml have inconsistent case
(`Garmin/...` and `GARMIN/...`), so paths are resolved
case-insensitively.
"""

from __future__ import annotations

import errno
import logging
import os
import posixpath
import shutil
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from openexpress.i18n import _

log = logging.getLogger(__name__)


class DeviceError(Exception):
    pass


class CopyNotStarted(DeviceError):
    """A write that failed before anything on the device was touched (e.g. the copy command
    could not be started): the file under the name, if any, is as it was."""


class RemountFailed(DeviceError):
    """The device was not mounted again after a PTP session."""


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

    def exclusive_ptp(self):
        """A context manager that gives a PTP session (`openexpress.device.ptp.Session`)
        to the device, for files ordinary MTP does not show; None when that is not
        possible here. The storage itself cannot be used inside the block."""
        return

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
            except DeviceError:
                return None
            match = next((e.name for e in entries if e.name == part), None)
            if match is None:
                match = next(
                    (e.name for e in entries if e.name.casefold() == part.casefold()), None
                )
            if match is None:
                return None
            real = posixpath.join(real, match) if real else match
        return real

    def exists(self, path: str) -> bool:
        return self.resolve(path) is not None

    def listdir(self, path: str) -> list[Entry]:
        real = self.resolve(path)
        if real is None:
            return []
        return self._list(real)

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
        existing = next(
            (e for e in self._list(real_parent) if e.name.casefold() == name.casefold()), None
        )
        if existing is not None and existing.is_dir:
            raise DeviceError(_("Cannot write {path}: it is a directory").format(path=path))
        target_name = existing.name if existing else name
        return posixpath.join(real_parent, target_name) if real_parent else target_name

    def write(self, path: str, data: bytes) -> str:
        """Write a file, creating missing directories. Returns the real path. After a failure
        the storage cleans up what the write left under the name, as write_file does
        (_discard_partial): never half a file; the old file stays when the write never
        started (CopyNotStarted)."""
        real = self._target(path)
        try:
            self._write(real, data)
        except CopyNotStarted:
            raise
        except BaseException:
            self._discard_partial(real)
            raise
        return real

    def write_file(self, path: str, local: Path, progress=None, keep_partial: bool = False) -> str:
        """Stream a local file to the device (large map/firmware files). After a failure the
        storage itself cleans up what the copy left under the name (_discard_partial), unless
        the copy never started (CopyNotStarted: the old file is untouched) or `keep_partial`
        (a file other maps use, never deleted)."""
        real = self._target(path)
        try:
            self._write_file(real, Path(local), progress)
        except CopyNotStarted:
            raise
        except BaseException:
            if not keep_partial:
                self._discard_partial(real)
            raise
        return real

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
                except DeviceError:
                    listed[real] = None
            return listed[real]

        def size_of(path: str) -> int | None:
            real, entry = "", None
            for part in [p for p in path.split("/") if p]:
                found = entries(real)
                if found is None:
                    return None
                entry = (next((e for e in found if e.name == part), None)
                         or next((e for e in found if e.name.casefold() == part.casefold()),
                                 None))
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
            entries = self._list(real)
            match = next((e.name for e in entries if e.name.casefold() == part.casefold()), None)
            nxt = posixpath.join(real, match or part) if real else (match or part)
            if match is None:
                self._mkdir(nxt)
            real = nxt
        return real


def _split_file_path(path: str) -> tuple[str, str]:
    parent, name = posixpath.split(path.strip("/"))
    if not name.strip():
        raise DeviceError(_("Invalid file name: '{path}'").format(path=path))
    return parent, name


@contextmanager
def _os_errors(what: str):
    try:
        yield
    except OSError as e:
        raise DeviceError(f"{what}: {e.strerror or e}") from e


# Bytes written at a time when a large file is copied to a folder; one progress step each.
COPY_CHUNK = 1 << 20
# A file being copied is named ".<name><PART_SUFFIX>" until it is complete.
PART_SUFFIX = ".openexpress-part"


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
    """Directory on disk: a device in mass storage mode, a copy, or tests.

    A write counts only once it is on the disk (fsync of the file and of its folder): a
    device in mass storage mode may lose what is still in the system's cache when its cable
    goes out, and some (the Varia) reset their USB instead of being ejected."""

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

    def free_space(self) -> int | None:
        return shutil.disk_usage(self.root).free

    def capacity(self) -> int | None:
        return shutil.disk_usage(self.root).total


class UmsStorage(LocalStorage):
    """A Garmin device in mass storage mode: the folder where the system mounted the drive.
    Reads and writes like LocalStorage; it also knows where it is on USB and its address."""

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
        self.usb_id: str | None = usb.usb_id if usb is not None else None
