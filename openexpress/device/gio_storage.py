"""Access to the MTP device via GIO/gvfs (like the GNOME file manager).

This way the clone does not take exclusive control of the device and does not
conflict with gvfs-mtp, which mounts the watch automatically in GNOME.
"""

from __future__ import annotations

import codecs
import contextlib
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
from openexpress.device.storage import (  # noqa: E402
    CopyNotStarted,
    DeviceError,
    Entry,
    LocalStorage,
    RemountFailed,
    Storage,
    UmsStorage,
)
from openexpress.errors import OrdinaryStop  # noqa: E402
from openexpress.i18n import _  # noqa: E402
from openexpress.paths import in_flatpak  # noqa: E402

log = logging.getLogger(__name__)

GARMIN_VID = "091e"
SYSFS_USB = Path("/sys/bus/usb/devices")
# Each block device (a disk, a partition) with a link to where it is in the device tree.
SYSFS_BLOCK = Path("/sys/class/block")
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


def _staging():
    """In the Flatpak a file is staged in the cache (transfer) on its way to the device: the
    cache is held until it is deleted. Elsewhere nothing is staged there."""
    from openexpress import cache

    return cache.using() if in_flatpak() else contextlib.nullcontext()


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


def host_copy(local: Path, target_uri: str, what: str, progress=None, started=None) -> None:
    """Copies a local file to the watch with the host's `gio copy`.

    gvfs refuses to copy local files for sandboxed (Flatpak) apps, and gvfs-mtp supports no
    other way of writing; so in the Flatpak the copy runs on the host (needs the
    --talk-name=org.freedesktop.Flatpak permission).

    With `progress(done, total)` the copy runs with `-p` and what it prints is read while it
    runs. When that cannot be read, `progress` is simply not called: the result of the copy
    depends on the exit code alone. `started()` is called for each line of progress read: the
    evidence that the copy is under way (and that the device is being written).
    """
    if not shutil.which("flatpak-spawn"):
        raise CopyNotStarted(f"{what}: " + _("flatpak-spawn is not available"))
    command = ["flatpak-spawn", "--host", "gio", "copy"]
    # The whole path, as _host_visible saw it: one given as typed ("-x.mp3", "Name: x.mp3")
    # would be an option or an address to the host's gio.
    source = str(Path(local).resolve())
    if progress is None:
        try:
            res = subprocess.run([*command, source, target_uri],
                                 capture_output=True, text=True, timeout=HOST_COPY_TIMEOUT_S,
                                 check=False)
        except OSError as e:  # the command could not be started
            raise CopyNotStarted(f"{what}: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise DeviceError(f"{what}: {e}") from e
        if res.returncode != 0:
            raise DeviceError(f"{what}: {_detail(res.stderr, res.stdout)}")
        return
    try:
        size = Path(local).stat().st_size
        proc = subprocess.Popen([*command, "-p", source, target_uri],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:  # the command could not be started
        raise CopyNotStarted(f"{what}: {e}") from e
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
                if fraction is not None and started is not None:
                    started()
                if fraction is not None and fraction > shown:
                    shown = fraction
                    progress(int(fraction * size), size)
        code = proc.wait()
    except OSError as e:
        proc.kill()
        try:
            proc.wait(5)  # reaped: not left behind as a zombie
        except subprocess.TimeoutExpired:
            log.warning("host copy: gio copy did not end 5 s after it was killed")
        reader.join(5)
        raise DeviceError(f"{what}: {e}") from e
    finally:
        watchdog.cancel()
    reader.join(5)
    if timed_out.is_set():
        raise DeviceError(f"{what}: timed out after {HOST_COPY_TIMEOUT_S} seconds")
    if code != 0:
        stderr = b"".join(errors).decode("utf-8", "replace")
        raise DeviceError(f"{what}: {_detail(stderr, ''.join(said))}")


# The size of a file that could not be told.
_UNKNOWN = object()


@dataclass
class _Attempt:
    """A write to the device under way: the size of the target before it (None: no file;
    _UNKNOWN), and whether gio copy reported progress."""
    before: object
    progressed: bool = False


class GioStorage(Storage):
    def __init__(self, root_uri: str):
        self.root = Gio.File.new_for_uri(root_uri.rstrip("/") + "/")
        self.label = root_uri
        # "vendor:product" of the USB device (never the serial number), when known.
        self.usb_id: str | None = None
        self.usb: GarminUsb | None = None
        self.device_uri: str | None = None  # the address gvfs mounts, above the storage
        # Each write under way: what the target was before it, and whether gio copy said it
        # was copying (see _discard_partial).
        self._attempts: dict[str, _Attempt] = {}

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

    def info(self, real_path: str) -> Entry | None:
        try:
            info = self._f(real_path).query_info(_ATTRS, Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
                return None
            raise _err(e, _("Cannot read {path}").format(path=real_path)) from e
        mtime = info.get_modification_date_time()
        return Entry(info.get_name(), info.get_file_type() == Gio.FileType.DIRECTORY,
                     info.get_size(), mtime.to_unix() if mtime else 0.0)

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
        """gvfs-mtp cannot write streams, only copy a whole local file: the bytes go to a
        temporary file, copied as any file is (_write_file, so that what a copy cut short
        left is known to _discard_partial). Failing before the copy: CopyNotStarted."""
        what = _("Cannot write {path}").format(path=real_path)
        with _staging():
            try:
                fd, tmp = tempfile.mkstemp(prefix="openexpress-",
                                           dir=_staging_dir() if in_flatpak() else None)
            except OSError as e:
                raise CopyNotStarted(f"{what}: {e}") from e
            try:
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                except OSError as e:
                    raise CopyNotStarted(f"{what}: {e}") from e
                self._write_file(real_path, Path(tmp))
            finally:
                os.unlink(tmp)

    def _write_file(self, real_path: str, local, progress=None) -> None:
        """Everything that can fail before the device is touched (the file to send, its
        staging in the Flatpak, starting the copy) raises CopyNotStarted: the old file stays.
        What fails once the copy runs leaves half a file, which write_file removes."""
        what = _("Cannot write {path}").format(path=real_path)
        local = Path(local)
        try:
            local.stat()
        except OSError as e:
            raise CopyNotStarted(f"{what}: {e}") from e
        # What the target is before the copy: the evidence _discard_partial goes by.
        self._attempts[real_path] = _Attempt(self._size_now(real_path))
        try:
            self._send(local, real_path, progress, what)
        except CopyNotStarted:
            self._attempts.pop(real_path, None)  # nothing touched, nothing to clean up
            raise
        # Any other failure: _discard_partial (called by write_file) reads and drops it.
        self._attempts.pop(real_path, None)

    def _send(self, local: Path, real_path: str, progress, what: str) -> None:
        if in_flatpak() and not _host_visible(local):
            # E.g. a file in the sandbox's private /tmp: stage it where the host can read it.
            with _staging():
                try:
                    fd, tmp = tempfile.mkstemp(prefix="openexpress-", dir=_staging_dir())
                    os.close(fd)
                except OSError as e:
                    raise CopyNotStarted(f"{what}: {e}") from e
                try:
                    try:
                        shutil.copyfile(local, tmp)
                    except OSError as e:
                        raise CopyNotStarted(f"{what}: {e}") from e
                    self._copy_local(Path(tmp), real_path, progress, what)
                finally:
                    os.unlink(tmp)
            return
        self._copy_local(local, real_path, progress, what)

    def _size_now(self, real_path: str):
        """The size of the file under the name: None when there is none, _UNKNOWN when it
        cannot be told."""
        try:
            info = self._f(real_path).query_info(
                "standard::size", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
                return None
            return _UNKNOWN
        return info.get_size()

    def _discard_partial(self, real_path: str) -> None:
        """After a copy that failed once it ran: the file under the name is deleted only on
        evidence that the copy touched it - gio copy reported progress, or a file is there
        now where there was none, or with another size than before. Otherwise it is the old
        file, untouched (e.g. gio copy started and refused at once), and it stays.

        Not told apart, and safe either way: the old and the new file of the same size, gio
        copy reporting no progress and gvfs replacing the old one with the whole new one -
        then the complete new file is kept. Half a new file of exactly the old size is all
        but impossible, and would only be kept, never wrongly deleted."""
        attempt = self._attempts.pop(real_path, None)
        now = self._size_now(real_path)
        if now is None or attempt is None:
            return  # nothing under the name, or nothing known of the copy
        if attempt.progressed:
            touched = True
        elif attempt.before is _UNKNOWN or now is _UNKNOWN:
            touched = False  # no evidence
        else:
            touched = attempt.before is None or now != attempt.before
        if not touched:
            log.info("device: a failed copy left the file under its name as it was; kept")
            return
        try:
            self._delete(real_path)
        except Exception as e:  # noqa: BLE001 - cleanup after a failure: that one is reported
            log.warning("Cannot delete a partly copied file: %s", type(e).__name__)

    def _copy_local(self, local: Path, real_path: str, progress, what: str) -> None:
        attempt = self._attempts.get(real_path)
        target = self._f(real_path)
        if in_flatpak():
            host_copy(local, target.get_uri(), what, progress,
                      started=(lambda: setattr(attempt, "progressed", True)) if attempt
                      else None)
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
    # What tells a drive from another of its model while nothing of it is read (analysis
    # 18): its USB serial (None: it gives none, as a Varia Rearview Radar) and the UUID of its
    # volume. As private as its unit ID (a fēnix 8's serial is its unit ID): never shown, and
    # hidden in the log as soon as they are read (applog.hide_usb).
    serial: str | None = field(default=None, compare=False, repr=False)
    volume_uuid: str | None = field(default=None, compare=False, repr=False)

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
    kind: str = "mtp"  # "mtp", or "ums" for a Garmin device in mass storage mode (a drive)


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


# --- a Garmin device in mass storage mode: a USB drive ------------------------------------
#
# Its volume names the block device (e.g. /dev/sdb1); /sys/class/block leads from it to the
# USB device it belongs to. Neither the label of the volume nor the folders on it decide.

_BLOCK_DEVICE = re.compile(r"/dev/([A-Za-z0-9_][A-Za-z0-9_.-]*)")


def _block_usb(unix_device: str | None) -> tuple[tuple[str, str, int, int], Path] | None:
    """(idVendor, idProduct, busnum, devnum) of the USB device that holds this block device,
    and the folder of that USB device in /sys; None when it is not on USB or not known. Only
    these four attributes are read here."""
    name = _BLOCK_DEVICE.fullmatch(unix_device or "")
    if name is None:
        return None
    try:
        here = (SYSFS_BLOCK / name.group(1)).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    # Up the device tree to the first folder that is a USB device: a disk on USB has its
    # partition, the disk, the SCSI and the USB interface folders below it. A disk that is
    # not on USB (e.g. NVMe) has none above it.
    for folder in (here, *here.parents):
        if (folder / "idVendor").is_file():
            try:
                return (((folder / "idVendor").read_text().strip().lower(),
                         (folder / "idProduct").read_text().strip().lower(),
                         int((folder / "busnum").read_text()),
                         int((folder / "devnum").read_text())), folder)
            except (OSError, ValueError):
                return None
    return None


def _usb_serial(folder: Path) -> str | None:
    """The USB serial of the USB device in this folder of /sys; None when it gives none."""
    try:
        return (folder / "serial").read_text().strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def _drive_usb(ids: tuple[str, str, int, int], folder: Path, volume) -> GarminUsb:
    """The USB device of a Garmin drive, with what tells it from another of its model: its
    USB serial and the UUID of its volume (udisks reads it without mounting), both hidden in
    the log at once."""
    from openexpress import applog

    uuid = volume.get_identifier("uuid") if volume is not None else None
    usb = GarminUsb(ids[1], "usb", ids[2], ids[3], serial=_usb_serial(folder),
                    volume_uuid=uuid or None)
    applog.hide_usb(usb)
    return usb


def _block_size(unix_device: str | None) -> int | None:
    """The size of a block device as the kernel tells it (sectors; 0: no medium, e.g. a
    Garmin drive turned off after it was removed safely); None when not known."""
    name = _BLOCK_DEVICE.fullmatch(unix_device or "")
    if name is None:
        return None
    try:
        return int((SYSFS_BLOCK / name.group(1) / "size").read_text())
    except (OSError, ValueError):
        return None


# The key of a Garmin drive that is not mounted, in the place of the address it has not got
# yet: its block device (never a folder; it has no user name in it).
UNMOUNTED_DRIVE = "ums-volume:"


def drive_volume(volume) -> tuple[str, GarminUsb, bool] | None:
    """For an event of a volume (gvfs: added, changed): (its key as a drive not mounted, its
    USB device, whether it has a medium) when it is a volume of a Garmin device in mass
    storage mode; None for any other volume. Only /sys is read, not its USB serial."""
    unix = volume.get_identifier("unix-device")
    block = _block_usb(unix)
    if block is None or block[0][0] != GARMIN_VID or not _mass_storage_only(block[1]):
        return None
    ids = block[0]
    return (UNMOUNTED_DRIVE + unix, GarminUsb(ids[1], "usb", ids[2], ids[3]),
            bool(_block_size(unix)))


# The USB places ("bus-devnum") of the Garmin devices in mass storage mode that the last list
# found without a drive to list: gvfs had no volume of theirs yet, or none with a medium. A
# drive may come a moment after its USB device (#19, analysis 18): the window looks for them
# again a few times (gui/detection.py, look_again_for_drives).
_without_drive: frozenset[str] = frozenset()


def drives_without_a_volume() -> frozenset[str]:
    """What the last list found of _without_drive."""
    with _places_lock:
        return _without_drive


def drives_on_usb() -> frozenset[str]:
    """The USB places ("bus-devnum") of the Garmin devices in mass storage mode now. Only /sys
    is read."""
    return frozenset(place for place, _product in _mass_storage_on_usb())


def garmin_ums(mount) -> GarminUsb | None:
    """Whether a Gio mount is a Garmin device in mass storage mode: a local folder on a block
    device of a USB device of vendor 091e; None if not. Its files are not read here (that is
    find_ums_devices), only /sys."""
    root = mount.get_root()
    uri = root.get_uri() if root is not None else None
    if not uri or not uri.lower().startswith("file://"):
        return None
    volume = mount.get_volume() if hasattr(mount, "get_volume") else None
    block = _block_usb(volume.get_identifier("unix-device") if volume is not None else None)
    if block is None or block[0][0] != GARMIN_VID:
        return None
    return _drive_usb(block[0], block[1], volume)


def _hidden_in_sandbox(path: str | None) -> bool:
    """A drive that gvfs reports mounted, whose folder the Flatpak cannot see (F1: no access
    to /run/media or /media, e.g. the permission was turned off)."""
    return path is not None and in_flatpak() and not os.path.isdir(path)


def drive_hidden_in_sandbox() -> bool:
    """Whether a mounted Garmin drive is out of the Flatpak's sight: why it is not opened."""
    return any(_hidden_in_sandbox(m.get_root().get_path())
               for m in Gio.VolumeMonitor.get().get_mounts() if garmin_ums(m) is not None)


def find_ums_devices() -> list[FoundDevice]:
    """The mounted Garmin devices in mass storage mode that have GARMIN/GarminDevice.xml, in
    any letter case, then those that are not mounted (_unmounted_drives, `mounted` False).
    Only the folder listings on the way to that file are read."""
    found = []
    usb_seen: set[str] = set()  # the USB places of the drives looked at, "bus-devnum"
    for mount in Gio.VolumeMonitor.get().get_mounts():
        usb = garmin_ums(mount)
        if usb is None:
            continue
        usb_seen.add(f"{usb.bus}-{usb.address}")
        note_drive_place(usb)
        if ejected_by_us(usb):
            log.info("device: a Garmin drive (USB %s) ejected by openExpress, skipped until it "
                     "is unplugged", usb.usb_id)
            continue
        root = mount.get_root()
        path = root.get_path()
        if _hidden_in_sandbox(path):
            # gvfs tells the sandbox about the mount, but its folder is not in it (F1).
            log.info("device: a Garmin drive (USB %s) is not visible in the Flatpak, skipped",
                     usb.usb_id)
            continue
        try:
            has_xml = path is not None and LocalStorage(Path(path)).exists(
                "GARMIN/GarminDevice.xml")
        except (DeviceError, OSError):
            has_xml = False
        if not has_xml:
            # No path in the log: it has the user name.
            log.info("device: a Garmin drive (USB %s) without GARMIN/GarminDevice.xml, skipped",
                     usb.usb_id)
            continue
        found.append(FoundDevice(root.get_uri(), mount.get_name(), True, mount.get_volume(),
                                 usb, kind="ums"))
    found += _unmounted_drives(seen=usb_seen)
    return found


def _unmounted_drives(seen: set[str]) -> list[FoundDevice]:
    """The Garmin drives that the system has not mounted (KDE mounts a drive only once a file
    manager opens it, #19): a volume (gvfs) whose block device belongs to a USB device of
    vendor 091e with a mass storage interface, and that has a medium – listed, to be mounted
    when chosen. Without a medium (removed safely: the device turns its drive off), or with
    no volume at all, only a replug helps: not listed, said in the log. `seen`: the USB
    places of the drives already mounted (and found or skipped), not looked at again.

    One entry for each USB device, judged by all of its volumes: a device may give more
    than one block device, one of them an empty slot (a Forerunner 55: `sdc` of 0 B beside
    `sdd`, its drive, #19). Its drive is its first block device with a medium; an empty slot
    beside it is no drive without a medium."""
    global _without_drive
    found = []
    waiting = set()  # the places of devices in mass storage mode without a drive to list
    places: dict[str, list] = {}  # the unmounted volumes of each Garmin drive, by USB place
    for vol in Gio.VolumeMonitor.get().get_volumes():
        mounted = getattr(vol, "get_mount", lambda: None)()
        unix = vol.get_identifier("unix-device")
        block = _block_usb(unix)
        if mounted is not None or block is None or block[0][0] != GARMIN_VID:
            continue
        ids, folder = block
        place = f"{ids[2]}-{ids[3]}"
        if place in seen or not _mass_storage_only(folder):
            continue
        with _places_lock:
            _drive_places[folder.name] = None
        places.setdefault(place, []).append((vol, unix, ids, folder))
    for place, volumes in places.items():
        seen.add(place)
        ids = volumes[0][2]
        usb = GarminUsb(ids[1], "usb", ids[2], ids[3])
        if ejected_by_us(usb):
            log.info("device: a Garmin drive (USB %s) ejected by openExpress, skipped until it "
                     "is unplugged", usb.usb_id)
            continue
        with_medium = sorted(((vol, unix) for vol, unix, _ids, _f in volumes
                              if _block_size(unix)),
                             key=lambda v: (len(v[1]), v[1]))  # sdc before sdd, sdz before sdaa
        if not with_medium:
            log.info("device: a Garmin drive (USB %s) has no medium (removed safely?), not "
                     "listed: unplug it and plug it in again", usb.usb_id)
            waiting.add(place)  # or not yet (#19)
            continue
        vol, unix = with_medium[0]
        usb = _drive_usb(ids, volumes[0][3], vol)  # the volume of its drive: its UUID
        log.info("device: a Garmin drive (USB %s) is not mounted, listed to be mounted",
                 usb.usb_id)
        found.append(FoundDevice(UNMOUNTED_DRIVE + unix, vol.get_name(), False, vol, usb,
                                 kind="ums"))
    for base, product in _mass_storage_on_usb():
        if base not in seen:
            log.info("device: a Garmin device in mass storage mode (USB %s:%s) without a "
                     "drive: unplug it and plug it in again", GARMIN_VID, product)
            waiting.add(base)  # or not yet (#19)
    with _places_lock:
        _without_drive = frozenset(waiting)
    return found


def _mass_storage_on_usb() -> list[tuple[str, str]]:
    """("bus-devnum", USB product ID) of each Garmin device on USB in mass storage mode."""
    found = []
    try:
        entries = sorted(SYSFS_USB.iterdir())
    except OSError:
        return found
    for base in entries:
        try:
            if ((base / "idVendor").read_text().strip().lower() == GARMIN_VID
                    and _mass_storage_only(base)):
                place = (f"{int((base / 'busnum').read_text())}-"
                         f"{int((base / 'devnum').read_text())}")
                found.append((place, (base / "idProduct").read_text().strip().lower()))
        except (OSError, ValueError):
            continue
    return found


MOUNT_SHOWN_S = 5.0  # how long a drive just mounted may take to be shown by the volume monitor


def _shown_mount(volume, sleep=time.sleep):
    """The mount of a volume just mounted. The volume monitor shows it once it has its
    signal, which the main thread handles (the window's, or ours on the command line), while
    the answer to the mount comes to the thread that waited for it: so it may come a moment
    later. None when it is not shown within MOUNT_SHOWN_S."""
    started = time.monotonic()
    on_main = threading.current_thread() is threading.main_thread()
    while True:
        mounted = volume.get_mount() if volume is not None else None
        if mounted is not None or time.monotonic() - started >= MOUNT_SHOWN_S:
            break
        if on_main:  # nobody else handles the signals of the monitor here
            context = GLib.MainContext.default()
            for _turn in range(100):
                if not context.iteration(False):
                    break
        sleep(0.1)
    waited = time.monotonic() - started
    if mounted is not None and waited >= 0.1:
        log.debug("device: the mount of the drive was shown after %.1f s", waited)
    return mounted


def mount_drive(dev: FoundDevice) -> FoundDevice:
    """A Garmin drive chosen while it is not mounted, mounted as the system mounts it
    (Gio.Volume.mount, as an MTP device is): the drive as find_ums_devices finds a mounted
    one. DeviceError when it cannot be mounted (said as for MTP)."""
    mount(dev)
    mounted = _shown_mount(dev.volume)
    root = mounted.get_root() if mounted is not None else None
    if root is None:
        raise DeviceError(_("Cannot mount the device"))
    log.info("device: mounted the Garmin drive (USB %s)",
             dev.usb.usb_id if dev.usb is not None else "?")
    return FoundDevice(root.get_uri(), mounted.get_name(), True, dev.volume, dev.usb, kind="ums")


def find_all_devices() -> list[FoundDevice]:
    """Every Garmin device found, MTP devices first, then Garmin drives (`kind` tells them
    apart), for a list to choose from. Nothing is opened or mounted and no GarminDevice.xml
    is read: for a drive only the folder listings on the way to that file."""
    return [*find_devices(), *find_ums_devices()]


def open_ums_storage(dev: FoundDevice) -> UmsStorage:
    """The storage of a Garmin device in mass storage mode that find_ums_devices found."""
    path = Gio.File.new_for_uri(dev.uri).get_path()
    try:
        if path is None:
            raise DeviceError("not a local folder")
        st = UmsStorage(Path(path), dev.uri, dev.usb, dev.name)
    except DeviceError:
        # Unplugged since it was found. The error would name the folder, with the user name.
        raise NotThereYet(_("The device is not mounted.")) from None
    log.info("device: opened in mass storage mode (%s:, USB %s)", dev.uri.split(":", 1)[0],
             st.usb_id or "?")
    return st


def mount(dev: FoundDevice, timeout_s: float | None = None) -> None:
    """Mount the volume of a device found not mounted (as the file manager does)."""
    if dev.mounted or dev.volume is None:
        return
    volume = dev.volume
    _wait(lambda done, cancellable: volume.mount(
        Gio.MountMountFlags.NONE, None, cancellable,
        lambda vol, res: done(lambda: vol.mount_finish(res))),
        MOUNT_TIMEOUT_S if timeout_s is None else timeout_s, _("Cannot mount the device"),
        "mount")
    dev.mounted = True


# --- a Garmin drive ejected by openExpress -------------------------------------------------
#
# A Garmin device in mass storage mode may take a file written to it only once the computer
# lets go of its drive (#11: an inReach Mini 2 installed its update only after it was ejected,
# although the file was synced to the disk). So after a write the drive is ejected before the
# cable may go out. Ejected, the drive stays on USB, not mounted (the Varia RTL515 keeps its
# place, ejected from the system and from the Flatpak, 21.10.2026): until it leaves that place
# openExpress does not open it again, also when something mounts it again.

EJECT_TIMEOUT_S = 60.0
_ejected: list[GarminUsb] = []  # where on USB the drives ejected by us are
_ejected_lock = threading.Lock()


class DriveNotMounted(DeviceError):
    """The drive to eject is not mounted any more: ejected or unmounted meanwhile."""


def eject(storage) -> None:
    """Eject the drive of a Garmin device in mass storage mode (`storage`: a UmsStorage): the
    system writes out what it still holds, unmounts the drive and tells the device that it
    may go. The drive is then not opened again until it is unplugged (ejected_by_us).
    Raises DeviceError when the system refuses, e.g. while a program uses the drive."""
    key = _key(storage.device_uri)
    found = next((m for m in Gio.VolumeMonitor.get().get_mounts()
                  if m.get_root() is not None and _key(m.get_root().get_uri()) == key), None)
    if found is None:
        raise DriveNotMounted(_("The drive is not mounted any more. If it was ejected, you can "
                                "unplug the cable."))
    what = _("Cannot eject the device")
    note_drive_place(getattr(storage, "usb", None))  # a watch may go into Garmin USB mode
    if found.can_eject():
        _wait(lambda done, cancellable: found.eject_with_operation(
            Gio.MountUnmountFlags.NONE, None, cancellable,
            lambda m, res: done(lambda: m.eject_with_operation_finish(res))),
            EJECT_TIMEOUT_S, what, "eject")
    else:  # a drive that the system can only unmount
        _wait(lambda done, cancellable: found.unmount_with_operation(
            Gio.MountUnmountFlags.NONE, None, cancellable,
            lambda m, res: done(lambda: m.unmount_with_operation_finish(res))),
            EJECT_TIMEOUT_S, what, "eject (unmount)")
    usb = getattr(storage, "usb", None)
    if usb is not None:
        with _ejected_lock:
            _ejected.append(usb)
    log.info("device: ejected (USB %s)", getattr(storage, "usb_id", None) or "?")


def _forget_unplugged() -> None:
    """The drives ejected by us that left their place on USB are ours no more."""
    with _ejected_lock:
        for usb in [u for u in _ejected if not usb_still_there(u)]:
            _ejected.remove(usb)
            log.info("device: the drive ejected by openExpress was unplugged (USB %s)",
                     usb.usb_id)


def ejected_by_us(usb: GarminUsb | None) -> bool:
    """Whether this drive was ejected by openExpress and is still at its place on USB: then it
    is neither opened nor listed, and its mount is ignored, until it is unplugged."""
    if usb is None:
        return False
    _forget_unplugged()
    with _ejected_lock:
        return any(u == usb and (u.bus, u.address) == (usb.bus, usb.address) for u in _ejected)


def ejected_drive_waits() -> bool:
    """Whether a drive ejected by openExpress is still plugged in: the cable can go out."""
    _forget_unplugged()
    with _ejected_lock:
        return bool(_ejected)


def ejected_text() -> str:
    """A drive ejected by openExpress: the cable can go out now (and only now)."""
    return _("The device is ejected. You can unplug the cable.")


def not_ejected_text(window: bool = True) -> str:
    """After a failed eject: the cable must stay in, and what to do."""
    if window:
        return _("Do not unplug the cable yet: the device may not see the update. Close any "
                 "program that uses the drive and try again, or eject it in the file manager.")
    return _("Do not unplug the cable yet: the device may not see the update. Close any "
             "program that uses the drive and eject it in the file manager.")


# --- a device that is plugged in but not there yet ---------------------------------------
#
# Right after the cable goes in, a Garmin watch is on USB under a transitional product ID
# for a while (about 19 s on the fēnix 8), then comes back as an MTP device, and gvfs needs
# a few more seconds to offer its files. Only /sys is read to tell these states apart: the
# device is not opened and nothing is sent to it.

STARTING_PRODUCT = "0003"
ABSENT, STARTING, PRESENT = "absent", "starting", "present"
# Only Garmin devices in mass storage mode (e.g. the Varia RTL515): the system offers them
# as a USB drive, never over MTP, so there is nothing to wait for.
MASS_STORAGE = "mass-storage"
# A watch that connects as a USB drive, back on USB as 091e:0003 after its drive was
# ejected (a Forerunner 55, #19): it is in its Garmin USB mode (Garmin's own protocol, which
# openExpress does not use), not a drive switched off, whatever the name of this state says;
# only plugging it in again helps, nothing to wait for (_drive_places).
DRIVE_OFF = "drive-off"
MASS_STORAGE_CLASS = "08"  # the USB interface class
WAIT_FOR_DEVICE_S = 45
# Between its transitional mode and MTP the device is off the bus for a moment (about
# 0.3 s on the fēnix 8). Only a longer absence means that it was unplugged.
USB_GAP_S = 5


class NotThereYet(DeviceError):
    """gvfs does not offer a Garmin device, or its files cannot be seen yet."""


class MassStorageNotice(NotThereYet, OrdinaryStop):
    """A Garmin device in mass storage mode is on USB, but its drive is not mounted or is
    out of the Flatpak's sight: a notice, not an error (the command line writes no "Error:"
    before it; the exit code stays 1)."""

    log_reason = "a notice about the USB mode of the device"


class SeveralDevices(DeviceError):
    """More than one device where one was to be opened. `devices`: what was found (kind,
    address, USB id), for a window that lets the user choose; the text is for the command
    line."""

    def __init__(self, message: str, devices: list[FoundDevice] | None = None):
        super().__init__(message)
        self.devices: list[FoundDevice] = list(devices or [])


def _mass_storage_only(base: Path) -> bool:
    """Whether every interface of this USB device is a mass storage interface. False when
    none of their classes can be read."""
    classes = []
    for interface in base.glob(f"{base.name}:*"):
        try:
            classes.append((interface / "bInterfaceClass").read_text().strip().lower())
        except OSError:
            continue
    return bool(classes) and all(c == MASS_STORAGE_CLASS for c in classes)


def _garmin_on_usb() -> list[tuple[str, bool]]:
    """(USB product ID, whether it is in mass storage mode) of each Garmin device plugged in."""
    return [(product, mass_storage) for _place, product, mass_storage in _garmin_at_places()]


def _garmin_at_places() -> list[tuple[str, str, bool]]:
    """_garmin_on_usb, with the place of each device on USB: the name of its folder in
    /sys/bus/usb/devices (e.g. "1-4"), which stays when it comes back under another product
    ID at the same port."""
    found = []
    try:
        entries = sorted(SYSFS_USB.iterdir())
    except OSError:
        return found
    for base in entries:
        try:
            if (base / "idVendor").read_text().strip().lower() == GARMIN_VID:
                found.append((base.name, (base / "idProduct").read_text().strip().lower(),
                              _mass_storage_only(base)))
        except OSError:
            continue
    return found


# The places on USB where a Garmin drive was seen in this run of the program, or went away
# (unmounted, ejected), with since when nothing is there (None: something is). A Garmin device
# at such a place as 091e:0003 is that watch in its Garmin USB mode after its drive was ejected
# (a Forerunner 55, #19), not a device starting. A place is forgotten once a device in its
# normal mode is there, or nothing for USB_GAP_S (unplugged). Only /sys is read.
_drive_places: dict[str, float | None] = {}
# What the log said last of a device 091e:0003 at a place: whether it was in Garmin USB mode.
_said_0003: dict[str, bool] = {}
_places_lock = threading.Lock()


def note_drive_place(usb: GarminUsb | None) -> None:
    """A Garmin drive is (or was a moment ago) at the place of `usb` (its bus and device
    number): seen, unmounted or ejected. Nothing when it is not on USB any more."""
    place = _place_of(usb)
    if place is not None:
        with _places_lock:
            _drive_places[place] = None


def _place_of(usb: GarminUsb | None) -> str | None:
    if usb is None or usb.bus is None or usb.address is None:
        return None
    try:
        entries = sorted(SYSFS_USB.iterdir())
    except OSError:
        return None
    for base in entries:
        try:
            if (int((base / "busnum").read_text()) == usb.bus
                    and int((base / "devnum").read_text()) == usb.address):
                return base.name
        except (OSError, ValueError):
            continue
    return None


def _note_places(devices: list[tuple[str, str, bool]], now: float) -> None:
    """What is on USB now, for _drive_places (and one line in the log for each device
    091e:0003 that comes, saying which it is)."""
    with _places_lock:
        here = set()
        for place, product, mass_storage in devices:
            here.add(place)
            if mass_storage:
                _drive_places[place] = None
            elif product != STARTING_PRODUCT:
                _drive_places.pop(place, None)
            else:
                drive = place in _drive_places
                if drive:
                    _drive_places[place] = None
                if _said_0003.get(place) != drive:
                    _said_0003[place] = drive
                    if drive:
                        log.info("USB: a Garmin device %s:%s where a Garmin drive was: in its "
                                 "Garmin USB mode after its drive was ejected, to be plugged "
                                 "in again", GARMIN_VID, product)
                    else:
                        log.info("USB: a Garmin device %s:%s: in its transitional mode, "
                                 "starting", GARMIN_VID, product)
        for place in [p for p in _said_0003 if p not in here]:
            del _said_0003[place]
        for place, gone in list(_drive_places.items()):
            if place in here:
                continue
            if gone is None:
                _drive_places[place] = now
            elif now - gone >= USB_GAP_S:
                del _drive_places[place]


def garmin_products_on_usb() -> list[str]:
    """USB product IDs of the Garmin devices plugged in, whatever state they are in."""
    return [product for product, _mass_storage in _garmin_on_usb()]


def usb_state(clock=time.monotonic) -> str:
    """ABSENT: no Garmin device on USB; STARTING: one is there in its transitional mode;
    PRESENT: one is there in its normal mode (which says nothing about gvfs yet);
    MASS_STORAGE: the only Garmin devices there are in mass storage mode; DRIVE_OFF: those
    that are not are 091e:0003 where a Garmin drive was (_drive_places): in Garmin USB mode
    after it was ejected."""
    devices = _garmin_at_places()
    _note_places(devices, clock())
    if not devices:
        return ABSENT
    others = [(place, product) for place, product, mass_storage in devices if not mass_storage]
    if not others:
        return MASS_STORAGE
    if any(p != STARTING_PRODUCT for _place, p in others):
        return PRESENT
    with _places_lock:
        drives = all(place in _drive_places for place, _p in others)
    return DRIVE_OFF if drives else STARTING


def mass_storage_text() -> str:
    """What an open Garmin device in mass storage mode offers: for its pages in the window,
    and the refusal of the commands that do not apply to it."""
    return _("The device is connected in mass storage mode (as a USB drive): openExpress "
             "shows its information and software updates; sync, apps, maps and music are "
             "not supported in this mode.")


def drive_off_text() -> str:
    """A watch back on USB as 091e:0003 after its drive was ejected (DRIVE_OFF): the window
    and the command line."""
    return _("After it was ejected, the watch switched to its Garmin USB mode, which "
             "openExpress does not use. Unplug the cable and plug it in again.")


def drive_not_mounted_text() -> str:
    """A Garmin device in mass storage mode on USB whose drive the system has not mounted
    (after it was ejected, or when the desktop does not mount drives by itself)."""
    return _("The Garmin device is connected, but its drive is not mounted. Open it in the "
             "file manager, or unplug the cable and plug it in again.")


def flatpak_drive_text() -> str:
    """A Garmin drive mounted by the system in a folder the Flatpak cannot see (its access to
    /run/media and /media turned off)."""
    return _("The Garmin device is connected as a USB drive, but this version (Flatpak) has no "
             "access to /run/media, where the system mounted it.")


def drive_notice_text() -> str:
    """A Garmin drive on USB that cannot be opened: ejected by openExpress, not mounted, or
    out of the sandbox's sight (the same words in the window and on the command line)."""
    if ejected_drive_waits():
        return ejected_text()
    return flatpak_drive_text() if drive_hidden_in_sandbox() else drive_not_mounted_text()


def _vfs():
    return Gio.Vfs.get_default()


def gvfs_schemes() -> list[str]:
    """The kinds of address GIO can open here: with gvfs, those of its backends (in the
    Flatpak the host's, asked over D-Bus); without gvfs only local files. Nothing is asked
    of a device."""
    return list(_vfs().get_supported_uri_schemes() or [])


_no_mtp_told = False  # the missing backend is written to the log once


def mtp_missing() -> bool:
    """Whether gvfs here has no MTP backend (no gvfs-mtp, #13), or there is no gvfs at all:
    then no MTP device is ever offered, and there is nothing to wait for. Not known (the
    question failed): False, so the wait and its words stay as they were."""
    global _no_mtp_told
    try:
        schemes = gvfs_schemes()
    except Exception as e:  # noqa: BLE001 - only a better message depends on it
        log.info("gvfs: its backends could not be read (%s)", type(e).__name__)
        return False
    missing = "mtp" not in schemes
    if missing and not _no_mtp_told:
        _no_mtp_told = True
        log.info("gvfs: no MTP backend (it offers: %s)", ", ".join(sorted(schemes)) or "-")
    return missing


def held_elsewhere_text() -> str:
    """Said once the wait for a device in its normal mode ran out (the window and the command
    line): another program may hold it over MTP, so that gvfs cannot offer it (#13: KDE's
    kmtpd, from kio-extras, in the kiod6 process). Once Dolphin has started it, kmtpd keeps
    every MTP device until it is unplugged or kiod6 quits, and opens one plugged in again at
    once: plugging the cable in again does not free it, quitting kiod6 does. Not Nautilus: it
    reads the device through gvfs."""
    # TRANSLATORS: added once the wait for a device on USB ran out: KDE's MTP service may hold
    # it; "kquitapp6 kiod6" is a command, not to be translated
    return _("KDE's MTP service may be holding the device: it keeps it after Dolphin is closed "
             "and takes it back when the cable is plugged in again. Close Dolphin and run "
             "kquitapp6 kiod6, then unplug the cable and plug it back in.")


def no_mtp_title() -> str:
    # TRANSLATORS: title while a Garmin device is on USB and gvfs has no MTP support
    return _("No MTP support in the system (gvfs-mtp)")


def no_mtp_text() -> str:
    """gvfs has no MTP backend (#13): what to install, in the window and on the command
    line (the package names of Fedora, Arch and Debian)."""
    return _("openExpress reads the device over MTP through gvfs, and gvfs on this system "
             "has no MTP support. Install it (Fedora: sudo dnf install gvfs-mtp; Arch: sudo "
             "pacman -S gvfs-mtp; Debian and Ubuntu: sudo apt install gvfs-backends), log "
             "out and back in, then plug the device in again.")


def not_ready_text(state: str) -> str:
    """What to tell the user when the wait for a plugged-in device ran out."""
    if state == STARTING:
        return _("The device is still starting. Wait until it shows its usual screen, or "
                 "unplug it and plug it in again.")
    if state == PRESENT:
        return _("The Garmin device is connected, but it does not offer its files. Check its "
                 "screen: if it is locked with a PIN or asks something, unlock it or confirm.")
    if state == MASS_STORAGE:
        return drive_notice_text()
    if state == DRIVE_OFF:
        return drive_off_text()
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
                # ABSENT: no Garmin on USB; STARTING: 091e:0003; PRESENT: on USB, not mounted;
                # MASS_STORAGE: only Garmin devices in mass storage mode; DRIVE_OFF: 091e:0003
                # where a Garmin drive was.
                log.info("device: not available after %.1f s, USB state: %s (%s), waiting: %s",
                         clock() - started, state,
                         ", ".join(f"091e:{p}" for p in garmin_products_on_usb()) or "-", wait)
                seen = state
            if state == DRIVE_OFF and isinstance(e, NotThereYet):
                # In Garmin USB mode after it was ejected: only plugging it in again helps.
                raise MassStorageNotice(not_ready_text(state)) from e
            if state in (ABSENT, MASS_STORAGE):
                if wait and told:
                    # A device we were waiting for left the bus: unplugged, or between modes.
                    if gone_since is None:
                        gone_since = clock()
                    if clock() - gone_since < USB_GAP_S and clock() < deadline:
                        sleep(1)
                        continue
                if state == MASS_STORAGE and isinstance(e, NotThereYet):
                    raise MassStorageNotice(not_ready_text(state)) from e
                raise
            gone_since = None
            if state == PRESENT and mtp_missing():
                # gvfs here has no MTP backend (#13): the device is never offered, so it is
                # not waited for.
                raise NotThereYet(no_mtp_text()) from e
            if not wait:
                if isinstance(e, NotThereYet) and state == STARTING:
                    raise NotThereYet(_("The device is starting. Try again in a moment.")) from e
                raise
            if clock() >= deadline:
                if isinstance(e, NotThereYet):
                    text = not_ready_text(state)
                    if state == PRESENT:
                        text = f"{text} {held_elsewhere_text()}"
                    raise NotThereYet(text) from e
                if state == PRESENT:
                    # On USB in its normal mode, never mounted (gvfs could not open it: another
                    # program may hold it): the error, then the window's sentence about that.
                    said = str(e).rstrip()
                    raise DeviceError(f"{said}{'' if said.endswith('.') else '.'} "
                                      f"{held_elsewhere_text()}") from e
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
                .format(names=names), devices)
        mount(devices[0])
        uri, usb = devices[0].uri, devices[0].usb
    else:
        usb = _identify(uri, None)
    return _mtp_storage(uri, usb)


def _mtp_storage(uri: str, usb: GarminUsb | None) -> GioStorage:
    """The storage of a mounted MTP device that has GARMIN/GarminDevice.xml: the device
    itself or one of its top folders (its internal storage)."""
    top = GioStorage(uri)
    candidates = [e.name for e in top._list("") if e.is_dir]
    for st in [GioStorage(top._f(name).get_uri()) for name in candidates] + [top]:
        if st.exists("GARMIN/GarminDevice.xml"):
            st.usb_id = usb.usb_id if usb is not None else None
            st.usb, st.device_uri = usb, uri
            return st
    raise NotThereYet(_("GARMIN/GarminDevice.xml not found on device {uri}").format(uri=uri))


# --- a short look at a device found, for a list to choose from -----------------------------

# Why a summary has no details.
NOT_MOUNTED = "not-mounted"  # an MTP device whose volume is not mounted: never mounted here
NO_DEVICE_FILE = "no-device-file"  # no GARMIN/GarminDevice.xml on it
UNREADABLE = "unreadable"  # the device or its GarminDevice.xml could not be read


@dataclass
class DeviceSummary:
    """What GarminDevice.xml says about a device found: enough for an entry in a list."""
    found: FoundDevice
    description: str = ""
    part_number: str = ""
    software_version: str = ""
    unit_id: int | None = field(default=None, repr=False)  # in memory only, never written
    reason: str | None = None  # NOT_MOUNTED, NO_DEVICE_FILE or UNREADABLE: no details
    # What probe opened of an MTP device (its GarminDevice and storage), kept in memory for
    # the window's count of its activities in the background; None for a drive.
    device: object | None = field(default=None, repr=False, compare=False)
    storage: object | None = field(default=None, repr=False, compare=False)
    # Its activities not uploaded yet (sync.ActivitiesToSync) as the window counted them,
    # None: not counted (yet) or not known; whether a count was started.
    to_sync: object | None = field(default=None, repr=False, compare=False)
    to_sync_asked: bool = field(default=False, repr=False, compare=False)

    @property
    def ok(self) -> bool:
        return self.reason is None


def probe(found: FoundDevice) -> DeviceSummary:
    """Read only GarminDevice.xml of a device from find_all_devices: no session, no account,
    no server, no PTP, nothing mounted. Never raises: without details, the summary says
    why. One log line, without the address or the unit ID. An MTP device read keeps what
    was opened (`device`, `storage`), for the window to count its activities later."""
    from openexpress import applog
    from openexpress.device.xmldevice import parse

    summary = _probe(found, parse)
    if summary.ok:
        applog.hide_unit_id(summary.unit_id)  # as hide_device: never in the log
    log.info("device: probed %s (USB %s): %s", found.kind,
             found.usb.usb_id if found.usb is not None else "?",
             "details read" if summary.ok else summary.reason)
    return summary


def distinct_devices(summaries: list[DeviceSummary]) -> list[DeviceSummary]:
    """One entry per device. Entries with the same unit ID are one device seen in two ways
    (over MTP and as a drive, while it switches): the MTP entry stays, in the place of the
    first (D1). Entries without details cannot be told apart and are each kept."""
    out: list[DeviceSummary] = []
    for s in summaries:
        seen = next((i for i, o in enumerate(out)
                     if s.unit_id is not None and o.unit_id == s.unit_id), None)
        if seen is None:
            out.append(s)
        elif out[seen].found.kind != "mtp" and s.found.kind == "mtp":
            out[seen] = s
    return out


def _probe(found: FoundDevice, parse) -> DeviceSummary:
    if not found.mounted:
        return DeviceSummary(found, reason=NOT_MOUNTED)
    try:
        if found.kind == "ums":
            path = Gio.File.new_for_uri(found.uri).get_path()
            if path is None:
                return DeviceSummary(found, reason=UNREADABLE)
            storage: Storage = UmsStorage(Path(path), found.uri, found.usb, found.name)
            if not storage.exists("GARMIN/GarminDevice.xml"):
                return DeviceSummary(found, reason=NO_DEVICE_FILE)
        else:
            storage = _mtp_storage(found.uri, found.usb)
    except NotThereYet:
        return DeviceSummary(found, reason=NO_DEVICE_FILE)
    except Exception as e:  # noqa: BLE001 - a summary, never an error; the reason is kept
        from openexpress import applog

        # Its kind and message (an MTP address in it carries the serial number: filtered
        # here, before the log's own filter); e.g. a device unplugged while gvfs still has it.
        log.info("device: probe could not open it (%s: %s)", type(e).__name__,
                 applog.redact(str(e)))
        return DeviceSummary(found, reason=UNREADABLE)
    try:
        device = parse(storage.read("GARMIN/GarminDevice.xml"))
    except Exception as e:  # noqa: BLE001 - the error may quote the file; only its kind
        log.debug("device: probe could not read GarminDevice.xml (%s)", type(e).__name__)
        return DeviceSummary(found, reason=UNREADABLE)
    # Without <Id> the parser gives 0: no unit ID, never one to merge entries by.
    summary = DeviceSummary(found, device.description, device.part_number,
                            device.software_version_str, device.unit_id or None)
    if found.kind == "mtp":
        summary.device, summary.storage = device, storage
    return summary


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


# --- one asynchronous Gio operation, waited for ------------------------------------------

MOUNT_TIMEOUT_S = 30.0
UNMOUNT_TIMEOUT_S = 30.0
CANCEL_WAIT_S = 5.0  # for the answer of an operation cancelled at its time limit


class GioTimedOut(DeviceError):
    """A Gio operation that did not answer within its time limit: it was cancelled, and its
    answer was the cancel, an error, or none at all. gvfs may still be at it."""


def _answer_code(e: GLib.Error) -> str:
    """The code of an answer of Gio, for the log (e.g. "cancelled", "failed")."""
    if e.domain == "g-io-error-quark":
        with suppress(ValueError):
            return Gio.IOErrorEnum(e.code).value_nick
    return f"{e.domain} {e.code}"


def _ms(seconds: float) -> int:
    return max(1, round(seconds * 1000))


def _wait(start, timeout_s: float, what: str, name: str = "operation") -> None:
    """Run one asynchronous Gio operation to its end. `start(done, cancellable)` begins it and
    `done(finish)` is called with a function that raises GLib.Error on failure. `name` says
    the operation in the log.

    Gio gives the answer in the main context that was the default of the thread when the
    operation began. A worker thread waits on a context of its own, pushed for the wait: its
    answer and its time limit need nothing of the main thread (the window holds the global
    default context while it runs), and no callback of the main thread runs here. The main
    thread (the command line) waits on the global default context, where PyGObject wakes it on
    Ctrl+C.

    At its time limit the operation is cancelled and its answer, which Gio always gives, is
    waited for up to CANCEL_WAIT_S. Done all the same is a success: that is what happened. A
    cancel, an error or no answer at all is GioTimedOut."""
    own = threading.current_thread() is not threading.main_thread()
    context = GLib.MainContext.new() if own else GLib.MainContext.default()
    loop = GLib.MainLoop(context)
    cancellable = Gio.Cancellable()
    answers: list = []  # None: done; or the GLib.Error
    cancelled: list[bool] = []
    sources: list = []
    started = time.monotonic()

    def done(finish):
        try:
            finish()
            answers.append(None)
        except GLib.Error as e:
            answers.append(e)
        loop.quit()

    def at_the_limit():
        if not answers:  # an answer in the same turn of the loop comes first
            cancelled.append(True)
            cancellable.cancel()  # Gio answers all the same: cancelled, or done if too late
            grace = GLib.timeout_source_new(_ms(CANCEL_WAIT_S))
            grace.set_callback(lambda *_a: loop.quit() or False)
            sources.append(grace)
            grace.attach(context)
        return False

    timer = GLib.timeout_source_new(_ms(timeout_s))
    timer.set_callback(lambda *_a: at_the_limit())
    sources.append(timer)
    if own:
        context.push_thread_default()
    try:
        timer.attach(context)
        start(done, cancellable)
        if not answers:  # an answer given within start() needs no loop
            loop.run()
    except BaseException:
        cancellable.cancel()  # e.g. Ctrl+C on the command line: no answer waited for
        raise
    finally:
        for source in sources:
            source.destroy()
        if own:
            context.pop_thread_default()
    took = time.monotonic() - started
    if not cancelled:
        log.debug("gio: %s answered in %.1f s (%s)", name, took,
                  "own context" if own else "main loop of this thread")
        if answers[0] is not None:
            raise _err(answers[0], what)
        return
    if not answers:
        log.warning("gio: %s: no answer in %g s, cancelled; no answer %g s after the cancel "
                    "either, given up", name, timeout_s, CANCEL_WAIT_S)
        raise GioTimedOut(_("{action}: the device did not answer in time").format(action=what))
    answer = answers[0]
    if answer is None:
        log.warning("gio: %s: no answer in %g s, cancelled; it ended all the same after "
                    "%.1f s", name, timeout_s, took)
        return
    cancelled_only = answer.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
    # A cancel ends our wait at once; gvfs may go on (it did: an unmount cancelled at its time
    # limit took effect afterwards, the live test of 28.10.2026).
    log.warning("gio: %s: no answer in %g s, cancelled; its answer: %s%s", name, timeout_s,
                _answer_code(answer), " (our wait ended, gvfs may go on)" if cancelled_only else "")
    if cancelled_only:
        raise GioTimedOut(_("{action}: the device did not answer in time").format(action=what))
    raise GioTimedOut(f"{what}: {answer.message}")  # what really happened, as _err says it


def _unmount(uri: str) -> None:
    found = next((m for m in Gio.VolumeMonitor.get().get_mounts()
                  if _key(m.get_root().get_uri()) == _key(uri)), None)
    if found is None:
        raise DeviceError(_("The device is not mounted."))
    _wait(lambda done, cancellable: found.unmount_with_operation(
        Gio.MountUnmountFlags.NONE, None, cancellable,
        lambda m, res: done(lambda: m.unmount_with_operation_finish(res))),
        UNMOUNT_TIMEOUT_S, _("Cannot unmount the device"), "unmount")


def _mounted_again(location, res) -> None:
    """The answer of mounting the device again: mounted already is as good (something else
    mounted it, or an unmount that did not end left it mounted)."""
    try:
        location.mount_enclosing_volume_finish(res)
    except GLib.Error as e:
        if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
            raise
        log.info("PTP: the device is mounted already")


def _remount(uri: str, storage: Storage, sleep=time.sleep) -> None:
    location = Gio.File.new_for_uri(uri)
    try:
        _wait(lambda done, cancellable: location.mount_enclosing_volume(
            Gio.MountMountFlags.NONE, None, cancellable,
            lambda f, res: done(lambda: _mounted_again(f, res))),
            MOUNT_TIMEOUT_S, _("Cannot mount the device"), "mount again")
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
        try:
            _unmount(uri)
        except (GioTimedOut, KeyboardInterrupt) as stopped:
            # Cancelled, gvfs may unmount it all the same: mounted again (or found mounted), so
            # that it is not left without a mount. A refusal leaves it mounted: nothing to do.
            log.info("PTP: the unmount did not end: mounting the device again")
            try:
                _remount(uri, storage)
            except RemountFailed:
                if not isinstance(stopped, KeyboardInterrupt):
                    raise  # the callers say it: unplug the device and plug it in again
                log.warning("PTP: the device was not mounted again after Ctrl+C")
            raise
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
