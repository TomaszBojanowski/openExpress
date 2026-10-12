"""Access to device files.

Paths are relative to the root of the device storage and use `/`.
Directory names in GarminDevice.xml have inconsistent case
(`Garmin/...` and `GARMIN/...`), so paths are resolved
case-insensitively.
"""

from __future__ import annotations

import posixpath
import shutil
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from openexpress.i18n import _


class DeviceError(Exception):
    pass


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
        return None

    def usb_blocked(self) -> bool:
        """Whether a sandbox keeps the program away from the USB device itself (Flatpak),
        so that a PTP session is out of reach here whatever the device offers."""
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
        """Write a file, creating missing directories. Returns the real path."""
        real = self._target(path)
        self._write(real, data)
        return real

    def write_file(self, path: str, local: Path, progress=None) -> str:
        """Stream a local file to the device (large map/firmware files)."""
        real = self._target(path)
        self._write_file(real, Path(local), progress)
        return real

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


class LocalStorage(Storage):
    """Directory on disk: a device in mass storage mode, a copy, or tests."""

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

    def _write(self, real_path: str, data: bytes) -> None:
        with _os_errors(_("Cannot write {path}").format(path=real_path)):
            self._p(real_path).write_bytes(data)

    def _write_file(self, real_path: str, local: Path, progress=None) -> None:
        with _os_errors(_("Cannot write {path}").format(path=real_path)):
            shutil.copyfile(local, self._p(real_path))

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
