"""Writing to the watch from the Flatpak goes through the host's `gio copy`."""
import subprocess
from pathlib import Path

import pytest

from openexpress.device import gio_storage
from openexpress.device.storage import DeviceError


class FakeTarget:
    def __init__(self, uri):
        self.uri = uri

    def get_uri(self):
        return self.uri


@pytest.fixture
def flatpak(monkeypatch, xdg):
    calls = []

    def run(cmd, **kw):
        src = Path(cmd[-2])
        calls.append((cmd, src.read_bytes() if src.exists() else None))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    monkeypatch.setattr(gio_storage.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(gio_storage.subprocess, "run", run)
    st = gio_storage.GioStorage.__new__(gio_storage.GioStorage)
    st._f = lambda real: FakeTarget("mtp://watch/" + real)
    return st, calls


def test_write_bytes_uses_host_copy(flatpak):
    st, calls = flatpak
    st._write("GARMIN/NewFiles/x.FIT", b"DATA")
    cmd, content = calls[0]
    assert cmd[:4] == ["flatpak-spawn", "--host", "gio", "copy"]
    assert cmd[-1] == "mtp://watch/GARMIN/NewFiles/x.FIT"
    assert content == b"DATA"
    # The staging file is in a host-visible place and is removed afterwards.
    assert gio_storage._host_visible(Path(cmd[-2]))
    assert not Path(cmd[-2]).exists()


def test_write_file_stages_sandbox_private_files(flatpak, tmp_path, monkeypatch):
    st, calls = flatpak
    private = Path("/tmp") / f"oe-private-{tmp_path.name}.bin"
    private.write_bytes(b"MAP")
    try:
        monkeypatch.setattr(gio_storage, "_host_visible",
                            lambda p: not str(p).startswith("/tmp/"))
        st._write_file("GARMIN/map.img", private)
        cmd, content = calls[0]
        assert cmd[-2] != str(private) and content == b"MAP"
    finally:
        private.unlink()


def test_host_copy_error_is_reported(flatpak, monkeypatch):
    st, _calls = flatpak
    monkeypatch.setattr(gio_storage.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    with pytest.raises(DeviceError, match="boom"):
        st._write("x.FIT", b"1")
