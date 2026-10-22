"""Reading the installer of Garmin Express (setup --from-installer) up to its two DLLs: the
container attached to the bootstrapper, the MSI in it, the streams of the DLLs, and what is
said when one is missing. No installer of Garmin's is needed and no Garmin data is used:
the files are made up, and 7z is stood in for where it would run."""

import struct
import subprocess
from pathlib import Path

import pytest

from openexpress import installer
from openexpress.settings import SetupError

OLE = bytes.fromhex("d0cf11e0a1b11ae1")  # the start of an MSI (an OLE compound file)


def _cabinet(size: int, fill: bytes) -> bytes:
    """A cabinet header as the bootstrapper has it (magic, then its size at offset 8), filled
    up to `size`."""
    head = installer.CAB_MAGIC + struct.pack("<I", size)
    return head + fill * ((size - len(head)) // len(fill)) + b"\0" * ((size - len(head))
                                                                      % len(fill))


def _bootstrapper(tmp_path: Path, *parts: bytes) -> Path:
    exe = tmp_path / "GarminExpress.exe"
    exe.write_bytes(b"MZ made-up bootstrapper\0" + b"".join(parts) + b"trailing data")
    return exe


def test_the_largest_cabinet_of_the_bootstrapper_is_its_payload(tmp_path):
    """A small cabinet (an icon, say) is not the payload; of those large enough the largest
    is, copied byte for byte."""
    small = _cabinet(4096, b"s")
    payload = _cabinet(installer.MIN_CONTAINER + 1000, b"payload")
    other = _cabinet(installer.MIN_CONTAINER + 10, b"o")
    exe = _bootstrapper(tmp_path, small, b"x" * 100, payload, other)
    out = installer._attached_container(exe, tmp_path / "payload.cab")
    assert out.read_bytes() == payload


@pytest.mark.parametrize("content", [b"", b"MZ nothing attached",
                                     installer.CAB_MAGIC + struct.pack("<I", 1 << 30)])
def test_a_file_without_a_payload_is_not_an_installer(tmp_path, content):
    """Empty, no cabinet, or one whose size goes past the end of the file."""
    exe = tmp_path / "Other.exe"
    exe.write_bytes(content)
    with pytest.raises(SetupError, match="Other.exe does not look like a Garmin Express "
                                         "installer"):
        installer._attached_container(exe, tmp_path / "payload.cab")
    assert not (tmp_path / "payload.cab").exists()


def test_the_msi_is_the_largest_file_of_the_payload(tmp_path):
    (tmp_path / "small").write_bytes(OLE + b"s")
    (tmp_path / "ExpressInstaller.msi").write_bytes(OLE + b"m" * 1000)
    (tmp_path / "readme").write_bytes(b"r" * 10)
    assert installer._find_msi(tmp_path).name == "ExpressInstaller.msi"


@pytest.mark.parametrize("files", [{}, {"big": b"not an msi" * 100, "small": OLE}])
def test_a_payload_without_an_msi_is_refused(tmp_path, files):
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    with pytest.raises(SetupError, match="does not contain the expected MSI"):
        installer._find_msi(tmp_path)


def test_without_7z_it_says_what_to_install(monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    with pytest.raises(SetupError, match="The 7z tool is required"):
        installer._seven_zip()


@pytest.mark.parametrize("code, fails", [(0, False), (1, False), (2, True)])
def test_7z_warnings_pass_and_errors_are_told(monkeypatch, tmp_path, code, fails):
    """Exit code 1 is a warning (e.g. data after the archive); 2 and more an error, told with
    the end of what 7z said."""
    ran = []
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(installer.subprocess, "run", lambda cmd, **kw: (
        ran.append(cmd), subprocess.CompletedProcess(cmd, code, "", "x" * 400 + "the end"))[1])
    archive, out = tmp_path / "a.cab", tmp_path / "out"
    if fails:
        with pytest.raises(SetupError, match="Extraction failed: x+the end$") as e:
            installer._run_7z(archive, out, "NetworkingDLL")
        assert len(str(e.value)) < 330  # only the end of what it said
    else:
        installer._run_7z(archive, out, "NetworkingDLL")
    assert ran == [["/usr/bin/7z", "x", "-y", f"-o{out}", str(archive), "NetworkingDLL"]]


def _stand_in_7z(monkeypatch, streams):
    """7z stood in for: the payload holds an MSI, the MSI holds `streams` (made-up bytes)."""
    def run(archive, out, *members):
        out.mkdir(parents=True, exist_ok=True)
        if not members:  # the payload
            (out / "ExpressInstaller.msi").write_bytes(OLE + b"made-up msi")
            return
        for member in members:
            if member in streams:
                (out / member).write_bytes(streams[member])

    monkeypatch.setattr(installer, "_run_7z", run)


def test_the_two_dlls_are_taken_out_of_the_installer(monkeypatch, tmp_path):
    _stand_in_7z(monkeypatch, {"NetworkingDLL": b"made-up networking",
                               "UtilitiesDLL": b"made-up utilities"})
    exe = _bootstrapper(tmp_path, _cabinet(installer.MIN_CONTAINER + 10, b"p"))
    found = installer.extract_dlls(exe, tmp_path / "dlls")
    assert {name: path.read_bytes() for name, path in found.items()} == {
        "Networking.dll": b"made-up networking", "Utilities.dll": b"made-up utilities"}


def test_a_dll_missing_from_the_installer_is_named(monkeypatch, tmp_path):
    _stand_in_7z(monkeypatch, {"NetworkingDLL": b"made-up networking"})
    exe = _bootstrapper(tmp_path, _cabinet(installer.MIN_CONTAINER + 10, b"p"))
    with pytest.raises(SetupError, match="Utilities.dll was not found in the installer"):
        installer.extract_dlls(exe, tmp_path / "dlls")


def test_an_installer_that_is_not_there(tmp_path):
    with pytest.raises(SetupError, match="File not found: .*GarminExpress.exe"):
        installer.extract_dlls(tmp_path / "GarminExpress.exe", tmp_path / "dlls")
