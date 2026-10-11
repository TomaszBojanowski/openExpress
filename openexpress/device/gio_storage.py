"""Access to the MTP device via GIO/gvfs (like the GNOME file manager).

This way the clone does not take exclusive control of the device and does not
conflict with gvfs-mtp, which mounts the watch automatically in GNOME.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from openexpress.device.storage import DeviceError, Entry, Storage  # noqa: E402
from openexpress.i18n import _  # noqa: E402

GARMIN_VID = "091e"
SYSFS_USB = Path("/sys/bus/usb/devices")
_ATTRS = "standard::name,standard::type,standard::size,time::modified"


def _err(e: GLib.Error, what: str) -> DeviceError:
    return DeviceError(f"{what}: {e.message}")


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def _host_visible(path: Path) -> bool:
    """Whether the host sees the file under the same path (Flatpak shares these locations)."""
    p = str(Path(path).resolve())
    home = str(Path.home())
    run_user = f"/run/user/{os.getuid()}/"
    return p.startswith(home + "/") or p.startswith(run_user)


def _staging_dir() -> Path:
    """Directory for temporary files that the host's gvfs can read (in Flatpak: ~/.var/app/…)."""
    from openexpress import paths

    d = paths.cache_dir() / "transfer"
    d.mkdir(parents=True, exist_ok=True)
    return d


def host_copy(local: Path, target_uri: str, what: str) -> None:
    """Copies a local file to the watch with the host's `gio copy`.

    gvfs refuses to copy local files for sandboxed (Flatpak) apps, and gvfs-mtp supports no
    other way of writing; so in the Flatpak the copy runs on the host (needs the
    --talk-name=org.freedesktop.Flatpak permission).
    """
    if not shutil.which("flatpak-spawn"):
        raise DeviceError(f"{what}: " + _("flatpak-spawn is not available"))
    try:
        res = subprocess.run(["flatpak-spawn", "--host", "gio", "copy", str(local), target_uri],
                             capture_output=True, text=True, timeout=6 * 3600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DeviceError(f"{what}: {e}") from e
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip()
        if "org.freedesktop.Flatpak" in detail or "Portal call failed" in detail:
            detail = _("the Flatpak is missing the permission to copy files to the watch")
        raise DeviceError(f"{what}: {detail}")


class GioStorage(Storage):
    def __init__(self, root_uri: str):
        self.root = Gio.File.new_for_uri(root_uri.rstrip("/") + "/")
        self.label = root_uri
        # "vendor:product" of the USB device (never the serial number), when known.
        self.usb_id: str | None = None

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
        try:
            while True:
                info = enum.next_file(None)
                if info is None:
                    break
                mtime = info.get_modification_date_time()
                out.append(
                    Entry(
                        info.get_name(),
                        info.get_file_type() == Gio.FileType.DIRECTORY,
                        info.get_size(),
                        mtime.to_unix() if mtime else 0.0,
                    )
                )
        except GLib.Error as e:
            raise _err(e, _("Cannot list {path}").format(path=real_path or "/")) from e
        finally:
            try:
                enum.close(None)
            except GLib.Error:
                pass
        return out

    def _read(self, real_path: str) -> bytes:
        try:
            ok, data, _etag = self._f(real_path).load_contents(None)
        except GLib.Error as e:
            raise _err(e, _("Cannot read {path}").format(path=real_path)) from e
        return bytes(data)

    def _write(self, real_path: str, data: bytes) -> None:
        # gvfs-mtp can't write streams; it can only copy a whole local file.
        what = _("Cannot write {path}").format(path=real_path)
        fd, tmp = tempfile.mkstemp(prefix="openexpress-",
                                   dir=_staging_dir() if in_flatpak() else None)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            self._copy_local(Path(tmp), real_path, None, what)
        except OSError as e:
            raise DeviceError(f"{what}: {e}") from e
        finally:
            os.unlink(tmp)

    def _write_file(self, real_path: str, local, progress=None) -> None:
        what = _("Cannot write {path}").format(path=real_path)
        local = Path(local)
        if in_flatpak() and not _host_visible(local):
            # E.g. a file in the sandbox's private /tmp: stage it where the host can read it.
            fd, tmp = tempfile.mkstemp(prefix="openexpress-", dir=_staging_dir())
            os.close(fd)
            try:
                shutil.copyfile(local, tmp)
                self._copy_local(Path(tmp), real_path, progress, what)
            finally:
                os.unlink(tmp)
            return
        self._copy_local(local, real_path, progress, what)

    def _copy_local(self, local: Path, real_path: str, progress, what: str) -> None:
        target = self._f(real_path)
        if in_flatpak():
            host_copy(local, target.get_uri(), what)
            if progress:
                size = local.stat().st_size
                progress(size, size)
            return
        cb = (lambda cur, total, _d=None: progress(cur, total)) if progress else None
        try:
            Gio.File.new_for_path(str(local)).copy(target, Gio.FileCopyFlags.OVERWRITE,
                                                   None, cb, None)
        except GLib.Error as e:
            raise _err(e, what) from e

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

    def _fs_attr(self, attr: str) -> int | None:
        try:
            info = self.root.query_filesystem_info(attr, None)
        except GLib.Error:
            return None
        if not info.has_attribute(attr):
            return None
        return info.get_attribute_uint64(attr)

    def free_space(self) -> int | None:
        return self._fs_attr("filesystem::free")

    def capacity(self) -> int | None:
        # A size of 0 means "unknown" (a real storage is never empty-sized).
        return self._fs_attr("filesystem::size") or None


@dataclass(frozen=True)
class GarminUsb:
    """A Garmin device on USB. `product` is the USB product ID, when it is known."""

    product: str | None
    source: str  # "usb": read from the USB device; "uri": taken from the MTP address

    @property
    def usb_id(self) -> str:
        return f"{GARMIN_VID}:{self.product or 'unknown'}"


@dataclass
class FoundDevice:
    uri: str
    name: str
    mounted: bool
    volume: Gio.Volume | None = None
    usb: GarminUsb | None = None


_UNIX_DEVICE = re.compile(r"/dev/bus/usb/(\d+)/(\d+)")
_URI_USB_ADDRESS = re.compile(r"mtp://\[usb:(\d+),(\d+)\]", re.I)
_URI_GARMIN_IDS = re.compile(rf"mtp://{GARMIN_VID}_([0-9a-f]{{4}})_", re.I)
_URI_GARMIN_NAME = "mtp://garmin_"


def _sysfs_ids(bus: int, devnum: int) -> tuple[str, str] | None:
    """(idVendor, idProduct) of the USB device with this bus and device number.

    Only these four sysfs attributes are read; the device node itself is not needed
    (a Flatpak sandbox has /sys but no /dev/bus/usb).
    """
    try:
        entries = sorted(SYSFS_USB.iterdir())
    except OSError:
        return None
    for base in entries:
        try:
            if (int((base / "busnum").read_text()) == bus
                    and int((base / "devnum").read_text()) == devnum):
                return ((base / "idVendor").read_text().strip().lower(),
                        (base / "idProduct").read_text().strip().lower())
        except (OSError, ValueError):
            continue
    return None


def _volume_for(uri: str, monitor) -> Gio.Volume | None:
    """A mounted MTP device has no volume of its own; the MTP volume shares its address."""
    for vol in (monitor or Gio.VolumeMonitor.get()).get_volumes():
        root = vol.get_activation_root()
        if root is not None and root.get_uri().rstrip("/") == uri.rstrip("/"):
            return vol
    return None


def _identify(uri: str | None, volume, monitor=None) -> GarminUsb | None:
    if not uri or not uri.lower().startswith("mtp://"):
        return None
    # gvfs names the address after the USB strings ("Garmin_Edge_1050_<serial>") or, when
    # the device has none, after the IDs ("091e_51b4_<serial>"); so ask the USB device.
    address = _URI_USB_ADDRESS.match(uri)
    if address is None:
        if volume is None:
            volume = _volume_for(uri, monitor)
        device = volume.get_identifier("unix-device") if volume is not None else None
        address = _UNIX_DEVICE.fullmatch(device or "")
    # An address of the older form mtp://[usb:BBB,DDD]/ carries the numbers itself.
    # That branch is covered by tests only: it was not tried on a device.
    ids = _sysfs_ids(int(address.group(1)), int(address.group(2))) if address else None
    if ids is not None:
        return GarminUsb(ids[1], "usb") if ids[0] == GARMIN_VID else None
    # The USB properties are not available: fall back to the start of the host name.
    by_ids = _URI_GARMIN_IDS.match(uri)
    if by_ids:
        return GarminUsb(by_ids.group(1).lower(), "uri")
    if uri.lower().startswith(_URI_GARMIN_NAME):
        return GarminUsb(None, "uri")
    return None


def garmin_usb(obj, monitor=None) -> GarminUsb | None:
    """Whether a Gio mount or volume is a Garmin device (USB vendor 091e); None if not."""
    if hasattr(obj, "get_activation_root"):
        root, volume = obj.get_activation_root(), obj
    else:
        root, volume = obj.get_root(), None
    return _identify(root.get_uri() if root is not None else None, volume, monitor)


def find_devices() -> list[FoundDevice]:
    monitor = Gio.VolumeMonitor.get()
    found: dict[str, FoundDevice] = {}
    for mount in monitor.get_mounts():
        usb = garmin_usb(mount, monitor)
        if usb is not None:
            uri = mount.get_root().get_uri()
            found[uri.rstrip("/")] = FoundDevice(uri, mount.get_name(), True, usb=usb)
    for vol in monitor.get_volumes():
        usb = garmin_usb(vol, monitor)
        if usb is not None:
            uri = vol.get_activation_root().get_uri()
            if uri.rstrip("/") not in found:
                found[uri.rstrip("/")] = FoundDevice(uri, vol.get_name(), False, vol, usb)
    return list(found.values())


def mount(dev: FoundDevice, timeout_s: int = 30) -> None:
    if dev.mounted or dev.volume is None:
        return
    loop = GLib.MainLoop()
    error: list[GLib.Error] = []
    finished: list[bool] = []
    timed_out: list[bool] = []

    def done(vol, res):
        finished.append(True)
        try:
            vol.mount_finish(res)
        except GLib.Error as e:
            error.append(e)
        loop.quit()

    def on_timeout():
        timed_out.append(True)
        loop.quit()
        return False

    dev.volume.mount(Gio.MountMountFlags.NONE, None, None, done)
    source = GLib.timeout_add_seconds(timeout_s, on_timeout)
    loop.run()
    if not timed_out:
        GLib.source_remove(source)
    if not finished:
        raise DeviceError(_("Mounting the watch timed out"))
    if error:
        raise _err(error[0], _("Cannot mount the device"))
    dev.mounted = True


def open_device_storage(uri: str | None = None) -> GioStorage:
    """Return the device storage that contains GARMIN/GarminDevice.xml."""
    usb = None
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
        uri, usb = devices[0].uri, devices[0].usb
    else:
        usb = _identify(uri, None)
    top = GioStorage(uri)
    candidates = [e.name for e in top._list("") if e.is_dir]
    for st in [GioStorage(top._f(name).get_uri()) for name in candidates] + [top]:
        if st.exists("GARMIN/GarminDevice.xml"):
            st.usb_id = usb.usb_id if usb is not None else None
            return st
    raise DeviceError(_("GARMIN/GarminDevice.xml not found on device {uri}").format(uri=uri))
