"""Reads the client key and download-signing data from an official Garmin Express installer.

openExpress does not ship any Garmin data. The user points it at their own copy of
GarminExpress.exe; the needed values are read from Networking.dll and Utilities.dll
inside it. Requires the `7z` tool (p7zip) for the cabinet and MSI containers.
"""

from __future__ import annotations

import mmap
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from openexpress.i18n import _
from openexpress.settings import (
    Consumer,
    SetupError,
    extract_consumer_from_dll,
    extract_url_salt_from_dll,
)

CAB_MAGIC = b"MSCF\0\0\0\0"
MIN_CONTAINER = 10 * 1024 * 1024
COPY_CHUNK = 1024 * 1024
# Stream names of the DLLs inside ExpressInstaller.msi
MSI_STREAMS = {"NetworkingDLL": "Networking.dll", "UtilitiesDLL": "Utilities.dll"}


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
        raise SetupError(_("Extraction failed: {error}").format(error=res.stderr.strip()[-300:]))


def _attached_container(exe: Path, dest: Path) -> Path:
    """Copies the largest cabinet attached to the WiX Burn bootstrapper (the package payload)."""
    best = None
    with open(exe, "rb") as f:
        try:
            data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            data = None
        if data is not None:
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
        if best is None:
            raise SetupError(_("{file} does not look like a Garmin Express installer.").format(
                file=exe.name))
        f.seek(best[0])
        remaining = best[1]
        with open(dest, "wb") as out:
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
    """Extracts Networking.dll and Utilities.dll into `dest`; returns {name: path}."""
    installer = Path(installer).expanduser()
    if not installer.is_file():
        raise SetupError(_("File not found: {path}").format(path=installer))
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
                raise SetupError(_("{name} was not found in the installer.").format(name=name))
            target = dest / name
            shutil.copyfile(src, target)
            out[name] = target
        return out


def consumer_from_installer(installer: Path) -> Consumer:
    """Reads the OAuth1 client key and the download-signing salt from the installer."""
    with tempfile.TemporaryDirectory(prefix="openexpress-") as tmp:
        dlls = extract_dlls(installer, Path(tmp))
        consumer = extract_consumer_from_dll(dlls["Networking.dll"])
        consumer.url_salt = extract_url_salt_from_dll(dlls["Utilities.dll"])
        return consumer
