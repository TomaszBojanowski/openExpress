"""Writing to a device over MTP (GioStorage): what a failed write leaves on the device. A copy
that never started touched nothing (the old file stays); one that started and broke off
leaves half a file, which the storage itself removes (review of 498f8f2). Here GioStorage
works on a local folder (file://) and the Flatpak's host copy is faked: no device."""

import subprocess
from pathlib import Path

import pytest
from test_updates_maps import (  # noqa: F401 - fixtures
    FILES,
    east_with_shared_dem,
    maps_response,
    server,
    unlock_e2,
)
from test_updates_system import the_font

from openexpress import maps, updates
from openexpress.device import gio_storage
from openexpress.device.gio_storage import GioStorage
from openexpress.device.storage import DeviceError, LocalStorage

OLD = b"the old, working file"


@pytest.fixture
def flatpak(monkeypatch):
    """As in the Flatpak: files go to the device with the host's `gio copy`, through
    flatpak-spawn. `fakes.which` is what shutil.which finds; `fakes.copy(src, dst)` does the
    copy or fails (None: copy the file)."""
    import types

    fakes = types.SimpleNamespace(which="/usr/bin/flatpak-spawn", copy=None, started=[])
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    monkeypatch.setattr(gio_storage, "_host_visible", lambda path: True)
    monkeypatch.setattr(gio_storage.shutil, "which", lambda name: fakes.which)

    def target_of(args):
        from gi.repository import Gio

        return Path(args[-2]), Path(Gio.File.new_for_uri(args[-1]).get_path())

    def run(args, **kw):
        src, dst = target_of(args)
        fakes.started.append(dst.name)
        if fakes.copy is not None:
            return fakes.copy(src, dst)
        dst.write_bytes(src.read_bytes())
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gio_storage.subprocess, "run", run)
    return fakes


def gio(root: Path) -> GioStorage:
    return GioStorage(root.as_uri())


def old_font(device_root):
    old = device_root / "GARMIN" / "Font" / "006-D0000-00.fnt"
    old.parent.mkdir(parents=True)
    old.write_bytes(OLD)
    return old


def no_flatpak_spawn(flatpak, monkeypatch):
    flatpak.which = None


def cannot_start(flatpak, monkeypatch):
    def run(args, **kw):
        raise FileNotFoundError(2, "No such file or directory", "flatpak-spawn")

    monkeypatch.setattr(gio_storage.subprocess, "run", run)


@pytest.mark.parametrize("failure", [no_flatpak_spawn, cannot_start])
def test_an_update_whose_copy_never_started_keeps_the_old_file(device_root, tmp_path,
                                                               monkeypatch, flatpak, failure):
    """Over MTP: the copy fails before it starts (no flatpak-spawn; the command cannot be
    run): nothing on the device was touched, and the old file stays as it was."""
    font = the_font(tmp_path, monkeypatch)
    old = old_font(device_root)
    failure(flatpak, monkeypatch)
    with pytest.raises(DeviceError):
        updates.install([font], gio(device_root))
    assert old.read_bytes() == OLD


def test_a_copy_that_cannot_be_started_with_progress_keeps_the_old_file(device_root, tmp_path,
                                                                       monkeypatch, flatpak):
    """The same with the progress bar, which starts `gio copy -p` with Popen."""
    def popen(*a, **kw):
        raise PermissionError(13, "Permission denied", "flatpak-spawn")

    monkeypatch.setattr(gio_storage.subprocess, "Popen", popen)
    font = the_font(tmp_path, monkeypatch)
    old = old_font(device_root)
    with pytest.raises(DeviceError):
        updates.install([font], gio(device_root), transfer_progress=lambda done, total: None)
    assert old.read_bytes() == OLD


def half_and_fail(src, dst):
    dst.write_bytes(src.read_bytes()[: max(1, len(src.read_bytes()) // 2)])
    return subprocess.CompletedProcess(["gio"], 1, "", "Error copying file: unplugged")


def test_a_copy_that_broke_off_leaves_no_half_file(device_root, tmp_path, monkeypatch,
                                                   flatpak):
    """Guards: the copy started (over MTP the old file is gone by then) and broke off: no
    half file stays under the name; the storage removes it."""
    font = the_font(tmp_path, monkeypatch)
    old = old_font(device_root)
    flatpak.copy = half_and_fail
    with pytest.raises(DeviceError):
        updates.install([font], gio(device_root))
    assert flatpak.started == ["006-D0000-00.fnt"]
    assert not old.exists()


def test_a_map_file_whose_copy_never_started_keeps_the_old_file(server, device_root, xdg,
                                                                 monkeypatch, flatpak):
    """Over MTP, a map: the copy of its own file fails before it starts: the old file of
    that name stays."""
    all_maps, east = east_with_shared_dem(server, device_root)
    old = device_root / "GARMIN" / "E2.img"
    old.write_bytes(OLD)

    def run(args, **kw):
        src, dst = Path(args[-2]), Path(args[-1].removeprefix("file://"))
        if dst.name == "E2.img":
            raise FileNotFoundError(2, "No such file or directory", "flatpak-spawn")
        dst.write_bytes(src.read_bytes())
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gio_storage.subprocess, "run", run)
    with pytest.raises(DeviceError):
        maps.install(east, all_maps, gio(device_root), unlock_e2)
    assert old.read_bytes() == OLD


def test_a_map_file_that_broke_off_leaves_no_half_but_a_shared_one_stays(server, device_root,
                                                                         xdg, flatpak):
    """Guards, a map over MTP: its own file broken off is removed by the storage; a file
    other maps use, broken off twice, stays (it is never deleted: decision of 0.5.0)."""
    all_maps, east = east_with_shared_dem(server, device_root)
    own = device_root / "GARMIN" / "E2.img"

    def own_fails(src, dst):
        if dst.name == "E2.img":
            return half_and_fail(src, dst)
        dst.write_bytes(src.read_bytes())
        return subprocess.CompletedProcess(["gio"], 0, "", "")

    flatpak.copy = own_fails
    with pytest.raises(DeviceError):
        maps.install(east, all_maps, gio(device_root), unlock_e2)
    assert not own.exists()

    def shared_fails(src, dst):
        if dst.name == "DEM.img":
            return half_and_fail(src, dst)
        dst.write_bytes(src.read_bytes())
        return subprocess.CompletedProcess(["gio"], 0, "", "")

    flatpak.copy = shared_fails
    with pytest.raises(maps.MapError):
        maps.install(east, all_maps, gio(device_root), unlock_e2)
    assert (device_root / "GARMIN" / "DEM.img").exists()


def test_the_caller_never_deletes_what_a_failed_write_left(device_root, tmp_path, monkeypatch):
    """Cleaning up after a failed write is the storage's own business: updates no longer
    deletes the file under the name (a storage that leaves something there means to)."""
    class LeavesIt(LocalStorage):
        replaces_atomically = False  # as MTP, for the space

        def _write_file(self, real_path, local, progress=None):
            self._p(real_path).write_bytes(b"kept on purpose")
            raise DeviceError("unplugged")

    font = the_font(tmp_path, monkeypatch)
    old = old_font(device_root)
    with pytest.raises(DeviceError):
        updates.install([font], LeavesIt(device_root))
    assert old.read_bytes() == b"kept on purpose"


def test_a_drive_removes_its_temporary_copy_when_the_rename_fails(device_root, tmp_path,
                                                                  monkeypatch):
    """Guards the drive (LocalStorage): the rename into place fails: the temporary copy is
    removed and the old file stays."""
    import os

    from openexpress.device.storage import PART_SUFFIX

    font = the_font(tmp_path, monkeypatch)
    old = old_font(device_root)

    def replace(src, dst):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(DeviceError):
        updates.install([font], LocalStorage(device_root))
    assert old.read_bytes() == OLD
    assert not [p for p in old.parent.iterdir() if p.name.endswith(PART_SUFFIX)]
