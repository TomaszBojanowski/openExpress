"""A write that fails leaves nothing behind (updates, maps, a local storage); a map without its
files is not offered."""

import os
from pathlib import Path

import pytest
from test_updates_maps import maps_response, server, sw_response  # noqa: F401

from openexpress import maps, updates
from openexpress.device.storage import DeviceError, LocalStorage

# --- updates / maps write failures ----------------------------------------------------


class TruncatingStorage(LocalStorage):
    def _write_file(self, real_path, local, progress=None):
        self._p(real_path).write_bytes(Path(local).read_bytes()[:10])


class CrashingStorage(LocalStorage):
    # Half a file under the real name, as over MTP: not a drive's temporary copy; and, as
    # GioStorage, it removes that half itself (the caller never deletes anything).
    replaces_atomically = False

    def _write_file(self, real_path, local, progress=None):
        self._p(real_path).write_bytes(b"half")
        raise DeviceError("unplugged")

    def _discard_partial(self, real_path):
        self._p(real_path).unlink(missing_ok=True)


@pytest.mark.parametrize("cls,exc", [(TruncatingStorage, updates.UpdateError),
                                     (CrashingStorage, DeviceError)])
def test_update_write_failure_leaves_no_file(server, device_root, xdg, cls, exc):
    items = updates.parse_updates(sw_response(server))
    st = cls(device_root)
    with pytest.raises(exc):
        updates.install(items, st)
    assert not (device_root / "GARMIN/GUPDATE.GSP").exists()
    assert not (device_root / "GARMIN/RemoteSW/aux.gcd").exists()


@pytest.mark.parametrize("cls,exc", [(TruncatingStorage, maps.MapError),
                                     (CrashingStorage, DeviceError)])
def test_map_write_failure_leaves_no_file(server, device_root, xdg, cls, exc):
    all_maps = maps.parse_maps(maps_response(server))
    japan = all_maps[2]
    with pytest.raises(exc):
        maps.install(japan, all_maps, cls(device_root), lambda ident: {})
    assert not (device_root / "GARMIN/J.img").exists()


# --- maps without deliverables ---------------------------------------------------------


def test_map_with_missing_deliverable_not_installable(server, device_root, xdg):
    resp = maps_response(server)
    east = resp["maps"][1]
    east["installoptions"][0]["files"][1]["deliverableoptions"] = []
    resp["maps"][2]["installoptions"][0]["files"] = []
    all_maps = maps.parse_maps(resp)
    assert all_maps[0].installable
    assert not all_maps[1].installable and not all_maps[2].installable
    (device_root / "GARMIN/E.img").write_bytes(b"old")
    called = []
    with pytest.raises(maps.MapError):
        maps.install(all_maps[1], all_maps, LocalStorage(device_root), called.append)
    assert (device_root / "GARMIN/E.img").exists() and called == []


# --- errors of a local storage --------------------------------------------------------


def test_local_storage_errors(device_root):
    st = LocalStorage(device_root)
    for bad in ["", "/", "GARMIN/", "GARMIN/  "]:
        with pytest.raises(DeviceError):
            st.write(bad, b"x")
        with pytest.raises(DeviceError):
            st.write_file(bad, device_root / "GARMIN/GarminDevice.xml")
    with pytest.raises(DeviceError):
        st.write("GARMIN/Activity", b"x")
    with pytest.raises(DeviceError):
        st.delete("GARMIN/Activity")
    with pytest.raises(DeviceError):
        st.delete("")
    assert (device_root / "GARMIN/Activity").is_dir()
    if os.geteuid() != 0:
        (device_root / "GARMIN/Monitor/M1.FIT").chmod(0)
        with pytest.raises(DeviceError):
            st.read("GARMIN/Monitor/M1.FIT")
