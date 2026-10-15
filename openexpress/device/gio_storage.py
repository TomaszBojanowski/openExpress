"""Access to the MTP device via GIO/gvfs (like the GNOME file manager).

This way the clone does not take exclusive control of the device and does not
conflict with gvfs-mtp, which mounts the watch automatically in GNOME.
"""

from __future__ import annotations

import codecs
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402 - imports must follow gi.require_version

from openexpress.device import ptp  # noqa: E402
from openexpress.device.storage import DeviceError, Entry, RemountFailed, Storage  # noqa: E402
from openexpress.i18n import _  # noqa: E402
from openexpress.paths import in_flatpak  # noqa: E402

log = logging.getLogger(__name__)

GARMIN_VID = "091e"
SYSFS_USB = Path("/sys/bus/usb/devices")
# The device nodes libusb opens. A Flatpak has them only with the --device=usb permission.
USB_NODES = Path("/dev/bus/usb")
_ATTRS = "standard::name,standard::type,standard::size,time::modified"


def _err(e: GLib.Error, what: str) -> DeviceError:
    return DeviceError(f"{what}: {e.message}")


def _host_visible(path: Path) -> bool:
    """Whether the host sees the file under the same path (Flatpak shares these locations)."""
    p = str(Path(path).resolve())
    home = str(Path.home())
    run_user = f"/run/user/{os.getuid()}/"
    return p.startswith((home + "/", run_user))


def _staging_dir() -> Path:
    """Directory for temporary files that the host's gvfs can read (in Flatpak: ~/.var/app/…)."""
    from openexpress import paths

    d = paths.cache_dir() / "transfer"
    d.mkdir(parents=True, exist_ok=True)
    return d


HOST_COPY_TIMEOUT_S = 6 * 3600
# A size as `gio copy -p` prints it: a number, then a unit ("1,0 MB", "56 bytes"). The words
# around it and the decimal separator follow the language of the system, so only these are read.
_QUANTITY = re.compile(r"(\d+(?:[.,]\d+)?)[ \u00a0\u202f]?([^\W\d_]+)(/?)")
_PREFIXES = {"k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}


def _bytes_of(number: str, unit: str) -> float | None:
    value = float(number.replace(",", "."))
    if unit[0] in _PREFIXES:
        return value * _PREFIXES[unit[0]]
    # Bytes are printed as a whole number ("56 bytes"); anything else is a unit that is not
    # known here (e.g. in another alphabet).
    return value if number.isdigit() else None


def copy_fraction(update: str) -> float | None:
    """How far a copy is, from one update of `gio copy -p` ("Copied 1.0 MB out of 2.0 GB
    (…)" in any language): the first size over the second. None when the update does not have
    two sizes (the last one names only the total) or they cannot be read."""
    sizes = [(number, unit) for number, unit, rate in _QUANTITY.findall(update.split("(")[0])
             if not rate]  # "…/s" is a speed
    if len(sizes) < 2:
        return None
    (done, done_unit), (total, total_unit) = sizes[:2]
    if done_unit == total_unit:
        a, b = float(done.replace(",", ".")), float(total.replace(",", "."))
    else:
        a, b = _bytes_of(done, done_unit), _bytes_of(total, total_unit)
    if a is None or b is None or b <= 0 or a < 0 or a > b * 1.1:
        return None
    return min(1.0, a / b)


def _detail(stderr: str, stdout: str) -> str:
    detail = stderr.strip() or stdout.strip()
    if "org.freedesktop.Flatpak" in detail or "Portal call failed" in detail:
        detail = _("the Flatpak is missing the permission to copy files to the device")
    return detail


def host_copy(local: Path, target_uri: str, what: str, progress=None) -> None:
    """Copies a local file to the watch with the host's `gio copy`.

    gvfs refuses to copy local files for sandboxed (Flatpak) apps, and gvfs-mtp supports no
    other way of writing; so in the Flatpak the copy runs on the host (needs the
    --talk-name=org.freedesktop.Flatpak permission).

    With `progress(done, total)` the copy runs with `-p` and what it prints is read while it
    runs. When that cannot be read, `progress` is simply not called: the result of the copy
    depends on the exit code alone.
    """
    if not shutil.which("flatpak-spawn"):
        raise DeviceError(f"{what}: " + _("flatpak-spawn is not available"))
    command = ["flatpak-spawn", "--host", "gio", "copy"]
    if progress is None:
        try:
            res = subprocess.run([*command, str(local), target_uri],
                                 capture_output=True, text=True, timeout=HOST_COPY_TIMEOUT_S,
                                 check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DeviceError(f"{what}: {e}") from e
        if res.returncode != 0:
            raise DeviceError(f"{what}: {_detail(res.stderr, res.stdout)}")
        return
    try:
        size = Path(local).stat().st_size
        proc = subprocess.Popen([*command, "-p", str(local), target_uri],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise DeviceError(f"{what}: {e}") from e
    errors: list[bytes] = []
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    reader.start()  # so that a lot of error output cannot block the copy
    timed_out = threading.Event()
    watchdog = threading.Timer(HOST_COPY_TIMEOUT_S, lambda: (timed_out.set(), proc.kill()))
    watchdog.daemon = True
    watchdog.start()
    shown, said, pending = 0.0, [], ""
    decode = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while True:
            data = os.read(proc.stdout.fileno(), 65536)
            if not data:
                break
            text = decode.decode(data)
            said.append(text)
            # Updates begin with a carriage return and are cut anywhere by the pipe: the
            # newest one is read as far as it has come, and again when the rest arrives.
            *complete, pending = re.split(r"[\r\n]", pending + text)
            for update in (*complete, pending):
                fraction = copy_fraction(update)
                if fraction is not None and fraction > shown:
                    shown = fraction
                    progress(int(fraction * size), size)
        code = proc.wait()
    except OSError as e:
        proc.kill()
        raise DeviceError(f"{what}: {e}") from e
    finally:
        watchdog.cancel()
    reader.join(5)
    if timed_out.is_set():
        raise DeviceError(f"{what}: timed out after {HOST_COPY_TIMEOUT_S} seconds")
    if code != 0:
        stderr = b"".join(errors).decode("utf-8", "replace")
        raise DeviceError(f"{what}: {_detail(stderr, ''.join(said))}")


class GioStorage(Storage):
    def __init__(self, root_uri: str):
        self.root = Gio.File.new_for_uri(root_uri.rstrip("/") + "/")
        self.label = root_uri
        # "vendor:product" of the USB device (never the serial number), when known.
        self.usb_id: str | None = None
        self.usb: GarminUsb | None = None
        self.device_uri: str | None = None  # the address gvfs mounts, above the storage

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
            _ok, data, _etag = self._f(real_path).load_contents(None)
        except GLib.Error as e:
            raise _err(e, _("Cannot read {path}").format(path=real_path)) from e
        return bytes(data)

    def _read_start(self, real_path: str, count: int) -> bytes:
        # A stream opened for reading; gvfs fetches only what is asked for.
        what = _("Cannot read {path}").format(path=real_path)
        try:
            stream = self._f(real_path).read(None)
        except GLib.Error as e:
            raise _err(e, what) from e
        try:
            data = b""
            while len(data) < count:
                chunk = stream.read_bytes(count - len(data), None).get_data()
                if not chunk:
                    break  # the file is shorter
                data += chunk
            return data
        except GLib.Error as e:
            raise _err(e, what) from e
        finally:
            with suppress(GLib.Error):
                stream.close(None)

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
            host_copy(local, target.get_uri(), what, progress)
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

    def usb_blocked(self) -> bool:
        return in_flatpak() and usb_node(self.usb) is None

    def exclusive_ptp(self):
        usb = self.usb
        if usb is None or usb.bus is None or usb.address is None or not self.device_uri:
            return None
        # A Flatpak reaches the device only through its node, which it has with the
        # --device=usb permission; without it the app is only queued.
        node = usb_node(usb) if in_flatpak() else None
        if in_flatpak() and node is None:
            return None
        try:
            ptp.load_libusb()
        except ptp.UsbUnavailable:
            return None
        return _exclusive(self.device_uri, usb.bus, usb.address, self, node)


@dataclass(frozen=True)
class GarminUsb:
    """A Garmin device on USB. `product` is the USB product ID, when it is known."""

    product: str | None
    source: str  # "usb": read from the USB device; "uri": taken from the MTP address
    # Where the device is on USB, when known; not part of what the device is.
    bus: int | None = field(default=None, compare=False)
    address: int | None = field(default=None, compare=False)

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
_URI_USB_ADDRESS = re.compile(r"mtp://\[usb:(\d+),(\d+)\]", re.IGNORECASE)
_URI_GARMIN_IDS = re.compile(rf"mtp://{GARMIN_VID}_([0-9a-f]{{4}})_", re.IGNORECASE)
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


def usb_still_there(usb: GarminUsb | None) -> bool:
    """Whether this Garmin device is still plugged in at the same place on USB: the same bus
    and device number, and the same product when it is known. A device that was unplugged
    and plugged in again (or restarted) gets a new device number, so it is not. False when
    its place is not known. Only /sys is read."""
    if usb is None or usb.bus is None or usb.address is None:
        return False
    ids = _sysfs_ids(usb.bus, usb.address)
    return (ids is not None and ids[0] == GARMIN_VID
            and (usb.product is None or ids[1] == usb.product.lower()))


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
        if ids[0] != GARMIN_VID:
            return None
        return GarminUsb(ids[1], "usb", int(address.group(1)), int(address.group(2)))
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
        raise DeviceError(_("Mounting the device timed out"))
    if error:
        raise _err(error[0], _("Cannot mount the device"))
    dev.mounted = True


# --- a device that is plugged in but not there yet ---------------------------------------
#
# Right after the cable goes in, a Garmin watch is on USB under a transitional product ID
# for a while (about 19 s on the fēnix 8), then comes back as an MTP device, and gvfs needs
# a few more seconds to offer its files. Only /sys is read to tell these states apart: the
# device is not opened and nothing is sent to it.

STARTING_PRODUCT = "0003"
ABSENT, STARTING, PRESENT = "absent", "starting", "present"
WAIT_FOR_DEVICE_S = 45
# Between its transitional mode and MTP the device is off the bus for a moment (about
# 0.3 s on the fēnix 8). Only a longer absence means that it was unplugged.
USB_GAP_S = 5


class NotThereYet(DeviceError):
    """gvfs does not offer a Garmin device, or its files cannot be seen yet."""


class SeveralDevices(DeviceError):
    pass


def garmin_products_on_usb() -> list[str]:
    """USB product IDs of the Garmin devices plugged in, whatever state they are in."""
    found = []
    try:
        entries = sorted(SYSFS_USB.iterdir())
    except OSError:
        return found
    for base in entries:
        try:
            if (base / "idVendor").read_text().strip().lower() == GARMIN_VID:
                found.append((base / "idProduct").read_text().strip().lower())
        except OSError:
            continue
    return found


def usb_state() -> str:
    """ABSENT: no Garmin device on USB; STARTING: one is there in its transitional mode;
    PRESENT: one is there in its normal mode (which says nothing about gvfs yet)."""
    products = garmin_products_on_usb()
    if not products:
        return ABSENT
    return PRESENT if any(p != STARTING_PRODUCT for p in products) else STARTING


def not_ready_text(state: str) -> str:
    """What to tell the user when the wait for a plugged-in device ran out."""
    if state == STARTING:
        return _("The device is still starting. Wait until it shows its usual screen, or "
                 "unplug it and plug it in again.")
    if state == PRESENT:
        return _("The Garmin device is connected, but it does not offer its files. Check its "
                 "screen: if it is locked with a PIN or asks something, unlock it or confirm.")
    return _("No Garmin device (MTP) found. Connect the device with a USB cable "
             "and unlock it if it asks to allow the connection.")


def open_device_storage(uri: str | None = None, wait: bool = False, notify=None,
                        sleep=time.sleep, clock=time.monotonic) -> GioStorage:
    """Return the device storage that contains GARMIN/GarminDevice.xml.

    With `wait`, a Garmin device that is on USB but not offered by gvfs yet is waited for,
    up to WAIT_FOR_DEVICE_S; `notify` is called once with a line saying so. Without a
    Garmin device on USB there is nothing to wait for. A device that was seen and then
    leaves the bus is given USB_GAP_S to come back before it counts as unplugged.
    """
    started = clock()
    deadline = started + WAIT_FOR_DEVICE_S
    told = False
    gone_since = None  # when a device we were waiting for left the bus
    seen = None  # the last USB state written to the log
    while True:
        try:
            st = _open_device_storage(uri)
            log.info("device: opened over MTP after %.1f s (USB %s)", clock() - started,
                     st.usb_id or "?")
            return st
        except SeveralDevices:
            raise
        except DeviceError as e:
            state = usb_state()
            if state != seen:
                # ABSENT: no Garmin on USB; STARTING: 091e:0003; PRESENT: on USB, not mounted.
                log.info("device: not available after %.1f s, USB state: %s (%s), waiting: %s",
                         clock() - started, state,
                         ", ".join(f"091e:{p}" for p in garmin_products_on_usb()) or "-", wait)
                seen = state
            if state == ABSENT:
                if not (wait and told):
                    raise
                if gone_since is None:
                    gone_since = clock()
                if clock() - gone_since >= USB_GAP_S or clock() >= deadline:
                    raise
                sleep(1)
                continue
            gone_since = None
            if not wait:
                if isinstance(e, NotThereYet) and state == STARTING:
                    raise NotThereYet(_("The device is starting. Try again in a moment.")) from e
                raise
            if clock() >= deadline:
                if isinstance(e, NotThereYet):
                    raise NotThereYet(not_ready_text(state)) from e
                raise
            if not told and notify is not None:
                notify(_("The device is starting…"))
            told = True
            sleep(1)


def _open_device_storage(uri: str | None) -> GioStorage:
    usb = None
    if uri is None:
        devices = find_devices()
        if not devices:
            raise NotThereYet(not_ready_text(ABSENT))
        if len(devices) > 1:
            names = ", ".join(d.uri for d in devices)
            raise SeveralDevices(
                _("Several devices connected, choose one with --device-uri: {names}")
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
            st.usb, st.device_uri = usb, uri
            return st
    raise NotThereYet(_("GARMIN/GarminDevice.xml not found on device {uri}").format(uri=uri))


# --- the device to ourselves, for PTP ----------------------------------------------------
#
# gvfs holds the USB interface while the device is mounted, so for a PTP session it is
# unmounted and mounted again afterwards, also after an error. Our own window and
# background service see both events; `own_remount` lets them ignore these, as long as the
# device stays where it was on USB: unplugged right after the session, it is unplugged.

REMOUNT_QUIET_S = 15
REMOUNT_READY_S = 20
_quiet_until: dict[str, float] = {}
_quiet_usb: dict[str, GarminUsb] = {}  # where the device of the session is on USB


def _key(uri: str) -> str:
    return uri.rstrip("/").lower()


def usb_node(usb: GarminUsb | None) -> Path | None:
    """The node of this USB device in /dev/bus/usb, when this process may read and write
    it; None otherwise (in a Flatpak without the --device=usb permission there is none)."""
    if usb is None or usb.bus is None or usb.address is None:
        return None
    node = USB_NODES / f"{usb.bus:03d}" / f"{usb.address:03d}"
    return node if os.access(node, os.R_OK | os.W_OK) else None


def own_remount(mount) -> bool:
    """Whether this mount appearing or disappearing is our own doing: during our PTP session
    and a moment after it, while the device is still at its place on USB."""
    root = mount.get_root()
    key = _key(root.get_uri()) if root is not None else None
    until = _quiet_until.get(key) if key is not None else None
    if until is None or time.monotonic() >= until:
        return False
    usb = _quiet_usb.get(key)
    if usb is None or usb_still_there(usb):
        return True
    # Gone from its place: the device was unplugged (or restarted), e.g. right after an
    # install, as the user is told to. Its next events are its own again.
    log.info("device: unplugged after our own PTP session")
    _quiet_until.pop(key, None)
    _quiet_usb.pop(key, None)
    return False


def _wait(start, timeout_s: int, what: str) -> None:
    """Run one asynchronous Gio operation to its end. `start(done)` begins it and
    `done(finish)` is called with a function that raises GLib.Error on failure."""
    loop = GLib.MainLoop()
    result: list = []

    def done(finish):
        try:
            finish()
            result.append(None)
        except GLib.Error as e:
            result.append(e)
        loop.quit()

    def on_timeout():
        result.append(TimeoutError())
        loop.quit()
        return False

    start(done)
    source = GLib.timeout_add_seconds(timeout_s, on_timeout)
    loop.run()
    if not isinstance(result[0], TimeoutError):
        GLib.source_remove(source)
    if isinstance(result[0], TimeoutError):
        raise DeviceError(_("{action}: the device did not answer in time").format(action=what))
    if result[0] is not None:
        raise _err(result[0], what)


def _unmount(uri: str) -> None:
    found = next((m for m in Gio.VolumeMonitor.get().get_mounts()
                  if _key(m.get_root().get_uri()) == _key(uri)), None)
    if found is None:
        raise DeviceError(_("The device is not mounted."))
    _wait(lambda done: found.unmount_with_operation(
        Gio.MountUnmountFlags.NONE, None, None,
        lambda m, res: done(lambda: m.unmount_with_operation_finish(res))),
        30, _("Cannot unmount the device"))


def _remount(uri: str, storage: Storage, sleep=time.sleep) -> None:
    location = Gio.File.new_for_uri(uri)
    try:
        _wait(lambda done: location.mount_enclosing_volume(
            Gio.MountMountFlags.NONE, None, None,
            lambda f, res: done(lambda: f.mount_enclosing_volume_finish(res))),
            30, _("Cannot mount the device"))
    except DeviceError as e:
        raise RemountFailed(str(e)) from e
    # Right after mounting gvfs may not list the files yet.
    for _attempt in range(REMOUNT_READY_S):
        try:
            if storage.exists("GARMIN/GarminDevice.xml"):
                return
        except DeviceError:
            pass
        sleep(1)
    raise RemountFailed(_("The device was mounted again but its files are not visible."))


@contextmanager
def _exclusive(uri: str, bus: int, address: int, storage: Storage, node: Path | None = None):
    _quiet_until[_key(uri)] = float("inf")
    known = getattr(storage, "usb", None)
    _quiet_usb[_key(uri)] = GarminUsb(known.product if known else None, "usb", bus, address)
    started = time.monotonic()
    try:
        log.info("PTP: unmounting the device from gvfs")
        _unmount(uri)
        try:
            transport = (ptp.LibusbTransport.open_node(node) if node is not None
                         else ptp.LibusbTransport.open(bus, address))
            with ptp.Session(transport) as session:
                log.info("PTP: session open after %.1f s%s", time.monotonic() - started,
                         " (through the device node)" if node is not None else "")
                yield session
        except Exception as e:
            log.warning("PTP: session ended with %s: %s", type(e).__name__, e)
            raise
        finally:
            _remount(uri, storage)
            log.info("PTP: session closed, device mounted again after %.1f s",
                     time.monotonic() - started)
    finally:
        # The signals of the last mount arrive a moment later.
        _quiet_until[_key(uri)] = time.monotonic() + REMOUNT_QUIET_S
