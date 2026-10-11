"""Access to the MTP device via GIO/gvfs (like the GNOME file manager).

This way the clone does not take exclusive control of the device and does not
conflict with gvfs-mtp, which mounts the watch automatically in GNOME.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from openexpress.device.storage import DeviceError, Entry, Storage  # noqa: E402
from openexpress.i18n import _  # noqa: E402

GARMIN_VID = "091e"
_ATTRS = "standard::name,standard::type,standard::size,time::modified"


def _err(e: GLib.Error, what: str) -> DeviceError:
    return DeviceError(f"{what}: {e.message}")


class GioStorage(Storage):
    def __init__(self, root_uri: str):
        self.root = Gio.File.new_for_uri(root_uri.rstrip("/") + "/")
        self.label = root_uri

    def _f(self, real: str) -> Gio.File:
        return self.root.resolve_relative_path(real) if real else self.root

    def _list(self, real_path: str) -> list[Entry]:
        try:
            enum = self._f(real_path).enumerate_children(
                _ATTRS, Gio.FileQueryInfoFlags.NONE, None
            )
        except GLib.Error as e:
            raise _err(e, _("Cannot list {path}").format(path=real_path or "/")) from e
        out = []
        for info in enum:
            mtime = info.get_modification_date_time()
            out.append(
                Entry(
                    info.get_name(),
                    info.get_file_type() == Gio.FileType.DIRECTORY,
                    info.get_size(),
                    mtime.to_unix() if mtime else 0.0,
                )
            )
        enum.close(None)
        return out

    def _read(self, real_path: str) -> bytes:
        try:
            ok, data, _etag = self._f(real_path).load_contents(None)
        except GLib.Error as e:
            raise _err(e, _("Cannot read {path}").format(path=real_path)) from e
        return bytes(data)

    def _write(self, real_path: str, data: bytes) -> None:
        # Not all gvfs-mtp versions support overwriting via streamed writes,
        # whereas copying a local file always works.
        fd, tmp = tempfile.mkstemp(prefix="openexpress-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            Gio.File.new_for_path(tmp).copy(
                self._f(real_path), Gio.FileCopyFlags.OVERWRITE, None, None, None
            )
        except GLib.Error as e:
            raise _err(e, _("Cannot write {path}").format(path=real_path)) from e
        finally:
            os.unlink(tmp)

    def _write_file(self, real_path: str, local, progress=None) -> None:
        cb = (lambda cur, total, _d=None: progress(cur, total)) if progress else None
        try:
            Gio.File.new_for_path(str(local)).copy(
                self._f(real_path), Gio.FileCopyFlags.OVERWRITE, None, cb, None
            )
        except GLib.Error as e:
            raise _err(e, _("Cannot write {path}").format(path=real_path)) from e

    def _delete(self, real_path: str) -> None:
        try:
            self._f(real_path).delete(None)
        except GLib.Error as e:
            raise _err(e, _("Cannot delete {path}").format(path=real_path)) from e

    def _mkdir(self, real_path: str) -> None:
        try:
            self._f(real_path).make_directory(None)
        except GLib.Error as e:
            raise _err(e, _("Cannot create directory {path}").format(path=real_path)) from e

    def free_space(self) -> int | None:
        try:
            info = self.root.query_filesystem_info("filesystem::free", None)
        except GLib.Error:
            return None
        free = info.get_attribute_uint64("filesystem::free")
        return free or None

    def capacity(self) -> int | None:
        try:
            info = self.root.query_filesystem_info("filesystem::size", None)
        except GLib.Error:
            return None
        return info.get_attribute_uint64("filesystem::size") or None


@dataclass
class FoundDevice:
    uri: str
    name: str
    mounted: bool
    volume: Gio.Volume | None = None


def _is_garmin_uri(uri: str | None) -> bool:
    return bool(uri) and uri.startswith("mtp://") and GARMIN_VID in uri.lower()


def find_devices() -> list[FoundDevice]:
    monitor = Gio.VolumeMonitor.get()
    found: dict[str, FoundDevice] = {}
    for mount in monitor.get_mounts():
        uri = mount.get_root().get_uri()
        if _is_garmin_uri(uri):
            found[uri.rstrip("/")] = FoundDevice(uri, mount.get_name(), True)
    for vol in monitor.get_volumes():
        root = vol.get_activation_root()
        uri = root.get_uri() if root else None
        if _is_garmin_uri(uri) and uri.rstrip("/") not in found:
            found[uri.rstrip("/")] = FoundDevice(uri, vol.get_name(), False, vol)
    return list(found.values())


def mount(dev: FoundDevice, timeout_s: int = 30) -> None:
    if dev.mounted or dev.volume is None:
        return
    loop = GLib.MainLoop()
    error: list[GLib.Error] = []

    def done(vol, res):
        try:
            vol.mount_finish(res)
        except GLib.Error as e:
            error.append(e)
        loop.quit()

    dev.volume.mount(Gio.MountMountFlags.NONE, None, None, done)
    GLib.timeout_add_seconds(timeout_s, loop.quit)
    loop.run()
    if error:
        raise _err(error[0], _("Cannot mount the device"))
    dev.mounted = True


def open_device_storage(uri: str | None = None) -> GioStorage:
    """Return the device storage that contains GARMIN/GarminDevice.xml."""
    if uri is None:
        devices = find_devices()
        if not devices:
            raise DeviceError(_(
                "No Garmin device (MTP) found. Connect the watch with a USB cable "
                "and unlock it if it asks to allow the connection."
            ))
        if len(devices) > 1:
            names = ", ".join(d.uri for d in devices)
            raise DeviceError(_("Several devices connected, choose one with --device-uri: {names}")
                              .format(names=names))
        mount(devices[0])
        uri = devices[0].uri
    top = GioStorage(uri)
    candidates = [e.name for e in top._list("") if e.is_dir]
    for name in candidates:
        st = GioStorage(top._f(name).get_uri())
        if st.exists("GARMIN/GarminDevice.xml"):
            return st
    if top.exists("GARMIN/GarminDevice.xml"):
        return top
    raise DeviceError(_("GARMIN/GarminDevice.xml not found on device {uri}").format(uri=uri))
