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


# --- an installer for macOS (audit of 26.10.2026) -------------------------------------------
# Only the installer for Windows has the two DLLs. A macOS disk image used to give "does not
# look like a Garmin Express installer" on the command line and "Consumer key not found" in
# the window; now both say what the file is and which one is needed. The file is told by its
# bytes, not by its name.

MACOS_TEXT = ("{file} is an installer for macOS. openExpress needs the Garmin Express installer "
              "for Windows (GarminExpress.exe); it only reads it and installs nothing.")


def _disk_image(tmp_path: Path, name: str = "GarminExpress.dmg") -> Path:
    """A made-up macOS disk image (UDIF): its data, then the 512-byte trailer such an image
    ends with, which begins with "koly"."""
    image = tmp_path / name
    image.write_bytes(b"x\xda" + b"made-up image data " * 1000 + b"koly" + b"\0" * 508)
    return image


def _macos_files(tmp_path: Path) -> list[Path]:
    """A disk image under the usual name, under another and under none; an installer
    package (a xar archive); a raw HFS+ image and an APFS one."""
    files = [_disk_image(tmp_path), _disk_image(tmp_path, "GarminExpress.exe"),
             _disk_image(tmp_path, "download")]
    package = tmp_path / "Install Garmin Express.pkg"
    package.write_bytes(b"xar!\0\x1c\0\x01" + b"made-up package" * 100)
    hfs = tmp_path / "4.hfs"
    hfs.write_bytes(b"\0" * 1024 + b"H+\0\x04" + b"made-up volume" * 100)
    apfs = tmp_path / "GarminExpress.img"
    apfs.write_bytes(b"\0" * 32 + b"NXSB" + b"made-up container" * 100)
    return [*files, package, hfs, apfs]


def test_an_installer_for_macos_is_told_by_its_bytes(tmp_path):
    for path in _macos_files(tmp_path):
        with pytest.raises(SetupError) as refused:
            installer.consumer_from_installer(path)
        assert str(refused.value) == MACOS_TEXT.format(file=path.name), path.name


def test_a_windows_program_is_read_as_before_whatever_it_ends_with(monkeypatch, tmp_path):
    """A program for Windows (it starts with "MZ") is never taken for a disk image, even
    with the bytes of one at its end."""
    _stand_in_7z(monkeypatch, {"NetworkingDLL": b"made-up networking",
                               "UtilitiesDLL": b"made-up utilities"})
    exe = _bootstrapper(tmp_path, _cabinet(installer.MIN_CONTAINER + 10, b"p"),
                        b"koly" + b"\0" * 508)
    assert not installer.is_macos_installer(exe)
    found = installer.extract_dlls(exe, tmp_path / "dlls")
    assert sorted(found) == ["Networking.dll", "Utilities.dll"]


def test_other_files_are_refused_as_before(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_bytes(b"made-up notes " * 100)
    assert not installer.is_macos_installer(other)
    with pytest.raises(SetupError, match="notes.txt does not look like a Garmin Express "
                                         "installer"):
        installer.consumer_from_installer(other)


@pytest.mark.parametrize("option", ["--from-installer", "--from-dll"])
def test_the_command_line_says_it_is_for_macos(tmp_path, capsys, option):
    from openexpress import paths
    from openexpress.cli import main

    image = _disk_image(tmp_path)
    assert main(["setup", option, str(image)]) == 1
    assert MACOS_TEXT.format(file="GarminExpress.dmg") in capsys.readouterr().err
    assert not (paths.config_dir() / "consumer.json").exists()


def test_the_window_says_it_is_for_macos(monkeypatch, tmp_path):
    import types

    from fakes import RunNow

    from openexpress.gui import app

    image = _disk_image(tmp_path)

    class ChosenFile:
        def get_path(self):
            return str(image)

    class FileDialog:
        def __init__(self, title):
            self.title = title

        def open(self, parent, cancellable, callback):
            callback(self, None)

        def open_finish(self, result):
            return ChosenFile()

    monkeypatch.setattr(app, "Gtk", types.SimpleNamespace(FileDialog=FileDialog))
    monkeypatch.setattr(app, "save_consumer", lambda consumer: pytest.fail("saved"))
    run = RunNow(error="record")
    win = types.SimpleNamespace(run_task=run, toast=lambda text: pytest.fail(text))
    app.MainWindow.choose_dll(win)
    assert run.errors == [MACOS_TEXT.format(file="GarminExpress.dmg")]


def test_the_words_in_polish(tmp_path):
    from openexpress import i18n

    image = _disk_image(tmp_path)
    i18n.setup("pl")
    try:
        with pytest.raises(SetupError) as refused:
            installer.consumer_from_installer(image)
    finally:
        i18n.setup("en")
    # Hard spaces after the one-letter words (tools/po-hard-spaces.py).
    assert str(refused.value) == (
        "GarminExpress.dmg to instalator dla macOS. openExpress potrzebuje instalatora Garmin "
        "Express dla Windows (GarminExpress.exe); tylko go odczytuje i\u00a0niczego nie "
        "instaluje.")
