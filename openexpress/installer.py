"""Reads the client key and download-signing data from an official Garmin Express installer.

openExpress does not ship any Garmin data. The user points it at their own copy of
GarminExpress.exe; the needed values are read from Networking.dll and Utilities.dll inside it.
The file is told by its bytes, never by its name, in one of two layouts:

* Velopack (Garmin Express 8 and later): a signed Windows program with a zip archive attached
  before its signature, the two DLLs in it under lib/app/; read with zipfile, nothing unpacked
  to disk;
* WiX (before): a bootstrapper with a cabinet attached, an MSI in it, the DLLs streams of the
  MSI; taken apart with the `7z` tool (p7zip), which only this layout needs.

Networking.dll itself (with Utilities.dll beside it) is taken as well. What is read from the
archive is bounded (the two entries only, each up to MAX_ENTRY), as the user chose the file.
"""

from __future__ import annotations

import io
import logging
import mmap
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path

from openexpress import applog
from openexpress.i18n import _
from openexpress.settings import (
    Consumer,
    SetupError,
    find_consumer,
    find_url_salt,
)

log = logging.getLogger(__name__)

CAB_MAGIC = b"MSCF\0\0\0\0"
# A Windows program starts with these bytes; the installer for Windows is one.
WINDOWS_PROGRAM = b"MZ"
# What an installer for macOS looks like, by its bytes: a disk image (.dmg, UDIF) ends with a
# 512-byte trailer that begins with "koly"; an installer package (.pkg) is a xar archive; a
# disk image taken apart (e.g. by 7z) leaves an HFS+ volume (its header 1024 bytes in) or an
# APFS container (its superblock magic 32 bytes in).
UDIF_TRAILER = (512, b"koly")
MACOS_STARTS = ((0, b"xar!"), (1024, b"H+\0\x04"), (1024, b"HX\0\x05"), (32, b"NXSB"))
MIN_CONTAINER = 10 * 1024 * 1024
COPY_CHUNK = 1024 * 1024
# Stream names of the DLLs inside ExpressInstaller.msi
MSI_STREAMS = {"NetworkingDLL": "Networking.dll", "UtilitiesDLL": "Utilities.dll"}
# The DLLs in the archive of the Velopack layout, and the most read of each.
VELOPACK_ENTRIES = {"Networking.dll": "lib/app/Networking.dll",
                    "Utilities.dll": "lib/app/Utilities.dll"}
MAX_ENTRY = 16 * 1024 * 1024
# Networking.dll chosen itself: no larger than this is read whole.
MAX_DLL = 64 * 1024 * 1024


def _not_an_installer(path: Path, size: int, check: str) -> SetupError:
    """The file is neither layout: said with what the user can do, logged with the check
    that failed and the file's size (not its name)."""
    log.info("setup: installer not read: %s (%s)", check, applog.size(size))
    # TRANSLATORS: setup: the file chosen is not Garmin Express's installer for Windows;
    # {file} is its name
    return SetupError(_("{file} is not the Garmin Express installer for Windows. Download "
                        "GarminExpress.exe from garmin.com/express and choose it.").format(
        file=path.name))


def _key_not_read(path: Path, size: int, check: str) -> SetupError:
    """An installer of Garmin Express in which what the setup needs is not found (Garmin may
    have changed it): said, and logged with the check that failed and the file's size."""
    log.info("setup: installer not read: %s (%s)", check, applog.size(size))
    # TRANSLATORS: setup: the file chosen is Garmin Express's installer, but the application key
    # could not be read from it; {file} is its name
    return SetupError(_("{file} is a Garmin Express installer, but openExpress cannot read the "
                        "application key from it: Garmin may have changed the installer. Please "
                        "report this on GitHub.").format(file=path.name))


def _seven_zip() -> str:
    for name in ("7z", "7za", "7zz"):
        path = shutil.which(name)
        if path:
            return path
    raise SetupError(_("The 7z tool is required (package p7zip or 7zip)."))


def _run_7z(archive: Path, out: Path, *members: str) -> None:
    cmd = [_seven_zip(), "x", "-y", f"-o{out}", str(archive), *members]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode not in (0, 1):  # 1 = warnings, e.g. extra data after the archive
        # TRANSLATORS: setup: unpacking the Garmin Express installer with 7z failed; {error}: the
        # end of 7z's error output
        raise SetupError(_("Extraction failed: {error}").format(error=res.stderr.strip()[-300:]))


def is_macos_installer(path: Path) -> bool:
    """Whether the file is an installer for macOS (a disk image or an installer package),
    told by its bytes, not by its name. A Windows program never is."""
    try:
        with open(path, "rb") as f:
            if f.read(len(WINDOWS_PROGRAM)) == WINDOWS_PROGRAM:
                return False
            for offset, magic in MACOS_STARTS:
                f.seek(offset)
                if f.read(len(magic)) == magic:
                    return True
            size = f.seek(0, 2)
            back, magic = UDIF_TRAILER
            if size < back:
                return False
            f.seek(size - back)
            return f.read(len(magic)) == magic
    except OSError:
        return False


def refuse_macos_installer(path: Path) -> None:
    """Says so when the file chosen is an installer for macOS: only the one for Windows has
    the two DLLs."""
    if is_macos_installer(path):
        raise SetupError(_("{file} is an installer for macOS. openExpress needs the Garmin "
                           "Express installer for Windows (GarminExpress.exe); it only reads "
                           "it and installs nothing.").format(file=Path(path).name))


# --- the Velopack layout --------------------------------------------------------------------

def _signature_start(f, size: int) -> int:
    """Where the signature of a signed Windows program begins (the security directory of its
    PE header), which is where an archive attached to it ends; the file's size when it names
    none at the end of the file."""
    f.seek(0)
    head = f.read(0x40)
    if len(head) < 0x40 or not head.startswith(WINDOWS_PROGRAM):
        return size
    pe = struct.unpack_from("<I", head, 0x3C)[0]
    f.seek(pe)
    header = f.read(24 + 112 + 16 * 8)
    if len(header) < 26 or header[:4] != b"PE\0\0":
        return size
    magic = struct.unpack_from("<H", header, 24)[0]
    directories = 24 + {0x20B: 112, 0x10B: 96}.get(magic, 0)
    if directories == 24 or len(header) < directories + 5 * 8:
        return size
    if struct.unpack_from("<I", header, directories - 4)[0] < 5:
        return size
    at, length = struct.unpack_from("<II", header, directories + 4 * 8)
    return at if 0 < at < size and at + length == size else size


class _Front(io.RawIOBase):
    """The first `end` bytes of an open file, read only."""

    def __init__(self, f, end: int):
        super().__init__()
        self.f, self.end, self.pos = f, end, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        start = {0: 0, 1: self.pos, 2: self.end}[whence]
        if start + offset < 0:
            raise OSError("seek before the start")
        self.pos = min(start + offset, self.end)
        return self.pos

    def readinto(self, buffer) -> int:
        count = min(len(buffer), self.end - self.pos)
        if count <= 0:
            return 0
        self.f.seek(self.pos)
        data = self.f.read(count)
        buffer[:len(data)] = data
        self.pos += len(data)
        return len(data)


def _velopack_dlls(path: Path, size: int) -> dict[str, bytes] | None:
    """The two DLLs from the archive a Velopack installer of Garmin Express attaches to its
    program; None when the file is not one (no archive, or one of another program)."""
    with open(path, "rb") as f:
        if f.read(2) != WINDOWS_PROGRAM:
            return None
        try:
            archive = zipfile.ZipFile(_Front(f, _signature_start(f, size)))
        except (zipfile.BadZipFile, OSError, ValueError, EOFError):
            return None
        with archive:
            entries = {i.filename.casefold(): i for i in archive.infolist() if not i.is_dir()}
            # The package of the program at the top of the archive: Garmin's.
            if not any(n.endswith(".nuspec") and "/" not in n and "garmin" in n
                       for n in entries):
                return None
            found = {}
            for name, entry in VELOPACK_ENTRIES.items():
                info = entries.get(entry.casefold())
                if info is None:
                    raise _key_not_read(path, size, f"{entry} missing from the archive")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise _key_not_read(path, size, f"{entry} packed in another way")
                if info.file_size > MAX_ENTRY:
                    raise _key_not_read(path, size, f"{entry} larger than "
                                        f"{applog.size(MAX_ENTRY)}")
                try:
                    with archive.open(info) as member:
                        data = member.read(MAX_ENTRY + 1)
                except (zipfile.BadZipFile, OSError, EOFError, zlib.error) as e:
                    raise _key_not_read(path, size, f"{entry} not read ({type(e).__name__})") \
                        from e
                if len(data) > MAX_ENTRY:
                    raise _key_not_read(path, size, f"{entry} larger than "
                                        f"{applog.size(MAX_ENTRY)}")
                found[name] = data
            return found


# --- the WiX layout ---------------------------------------------------------------------------

def _payload_cabinet(exe: Path) -> tuple[int, int] | None:
    """The largest cabinet attached to the WiX bootstrapper (the package payload): where it
    is and its size; None when there is none."""
    best = None
    with open(exe, "rb") as f:
        try:
            data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            return None
        with data:
            end = len(data)
            pos = data.find(CAB_MAGIC)
            while pos != -1:
                if pos + 12 <= end:
                    size = struct.unpack_from("<I", data, pos + 8)[0]
                    if (size >= MIN_CONTAINER and pos + size <= end
                            and (best is None or size > best[1])):
                        best = (pos, size)
                pos = data.find(CAB_MAGIC, pos + 1)
    return best


def _attached_container(exe: Path, dest: Path) -> Path:
    """Copies the largest cabinet attached to the WiX bootstrapper (the package payload)."""
    best = _payload_cabinet(exe)
    if best is None:
        raise _not_an_installer(exe, exe.stat().st_size, "no payload cabinet of the WiX layout")
    with open(exe, "rb") as f, open(dest, "wb") as out:
        f.seek(best[0])
        remaining = best[1]
        while remaining:
            chunk = f.read(min(COPY_CHUNK, remaining))
            if not chunk:
                break
            out.write(chunk)
            remaining -= len(chunk)
    return dest


def _find_msi(folder: Path) -> Path:
    """The application MSI is the largest file in the payload container."""
    files = [p for p in folder.iterdir() if p.is_file()]
    msi = max(files, key=lambda p: p.stat().st_size, default=None)
    if msi is not None:
        with open(msi, "rb") as f:
            header = f.read(8)
    if msi is None or header != bytes.fromhex("d0cf11e0a1b11ae1"):
        raise SetupError(_("The installer does not contain the expected MSI package."))
    return msi


def extract_dlls(installer: Path, dest: Path) -> dict[str, Path]:
    """Extracts Networking.dll and Utilities.dll of an installer of the WiX layout into
    `dest`; returns {name: path}."""
    installer = Path(installer).expanduser()
    if not installer.is_file():
        raise SetupError(_("File not found: {path}").format(path=installer))
    refuse_macos_installer(installer)
    with tempfile.TemporaryDirectory(prefix="openexpress-") as tmp:
        tmp_path = Path(tmp)
        cab = _attached_container(installer, tmp_path / "payload.cab")
        payload = tmp_path / "payload"
        _run_7z(cab, payload)
        msi = _find_msi(payload)
        files = tmp_path / "msi"
        _run_7z(msi, files, *MSI_STREAMS)
        out = {}
        dest.mkdir(parents=True, exist_ok=True)
        for stream, name in MSI_STREAMS.items():
            src = files / stream
            if not src.exists():
                raise _key_not_read(installer, installer.stat().st_size,
                                    f"{name} missing from the MSI")
            target = dest / name
            shutil.copyfile(src, target)
            out[name] = target
        return out


# --- the file chosen ------------------------------------------------------------------------

def _from_dlls(path: Path, size: int, dlls: dict[str, bytes], layout: str) -> Consumer:
    consumer = find_consumer(dlls["Networking.dll"])
    if consumer is None:
        raise _key_not_read(path, size, "no application key in Networking.dll")
    consumer.url_salt = find_url_salt(dlls["Utilities.dll"])
    if consumer.url_salt is None:
        raise _key_not_read(path, size, "no URL signing data in Utilities.dll")
    log.info("setup: the installer read (%s layout, %s)", layout, applog.size(size))
    return consumer


def _networking_dll(path: Path, size: int) -> Consumer | None:
    """Networking.dll chosen itself: its key, with the salt of Utilities.dll beside it when it
    is there; None when the file is not a DLL with the key."""
    if size > MAX_DLL:
        return None
    data = path.read_bytes()
    if not data.startswith(WINDOWS_PROGRAM):
        return None
    consumer = find_consumer(data)
    if consumer is None:
        return None
    utilities = path.with_name("Utilities.dll")
    if utilities.is_file() and utilities.stat().st_size <= MAX_DLL:
        consumer.url_salt = find_url_salt(utilities.read_bytes())
    log.info("setup: Networking.dll read (Utilities.dll beside it: %s)",
             "read" if consumer.url_salt else "none")
    return consumer


def consumer_from_installer(installer: Path) -> Consumer:
    """The OAuth1 client key and the download-signing salt from the file the user chose,
    told by its bytes: Garmin Express's installer for Windows of either layout, or
    Networking.dll itself (with Utilities.dll beside it)."""
    path = Path(installer).expanduser()
    if not path.is_file():
        raise SetupError(_("File not found: {path}").format(path=path))
    refuse_macos_installer(path)
    size = path.stat().st_size
    dlls = _velopack_dlls(path, size)
    if dlls is not None:
        return _from_dlls(path, size, dlls, "Velopack")
    if _payload_cabinet(path) is not None:
        with tempfile.TemporaryDirectory(prefix="openexpress-") as tmp:
            found = extract_dlls(path, Path(tmp))
            dlls = {name: p.read_bytes() for name, p in found.items()}
        return _from_dlls(path, size, dlls, "WiX")
    consumer = _networking_dll(path, size)
    if consumer is not None:
        return consumer
    raise _not_an_installer(path, size, "neither the archive of the Velopack layout, nor the "
                                        "payload cabinet of the WiX layout, nor the key of "
                                        "Networking.dll")
