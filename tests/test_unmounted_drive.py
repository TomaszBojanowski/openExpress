"""A Garmin drive that the system has not mounted (analysis 15, stage 0; #19, 25.10.2026): on
KDE a drive is mounted only once a file manager opens it. Seen through Gio.VolumeMonitor's
volumes (gvfs, no new permission): its block device belongs to a USB device of vendor 091e
with a mass storage interface and has a medium (a size): a tile "Not mounted; choose it to
open it" with the chip "USB drive", as for MTP; chosen, it is mounted (Gio.Volume.mount) and
opened as a drive. Without a medium (removed safely: the Garmin device turns its drive off)
it is not listed and the notice stays as it was; a drive ejected by openExpress (D3) is not
listed; without gvfs there are no volumes and nothing changes. All devices are made up."""

import logging
import os

import pytest
from test_detection import (  # noqa: F401 - the fixture
    RTL515_XML,
    FakeDriveMount,
    FakeMonitor,
    FakeVolume,
    drive_folder,
    drives,
    usb_drive,
)

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

FR55 = "0f1d"


class UnmountedVolume(FakeVolume):
    """Like the volume of a drive that is not mounted: mounting it (Gio.Volume.mount) answers
    from the main loop, and then it has a mount, at `folder`."""

    def __init__(self, unix_device, folder, name="GARMIN"):
        super().__init__(None, unix_device, name)
        self.folder, self.mounted_at, self.asked, self.refusal = folder, None, 0, None

    def get_mount(self):
        return self.mounted_at

    def mount(self, flags, operation, cancellable, callback):
        self.asked += 1
        gio_storage.GLib.idle_add(lambda: callback(self, "mounted") and False)

    def mount_finish(self, _result):
        if self.refusal:
            raise gio_storage.GLib.Error.new_literal(gio_storage.Gio.io_error_quark(),
                                                     self.refusal,
                                                     gio_storage.Gio.IOErrorEnum.FAILED)
        self.mounted_at = FakeDriveMount(self.folder, self.unix_device)
        return True


def _sized(drives, unix_device, sectors):
    """The size of the block device as the kernel tells it (512-byte sectors; 0: no medium)."""
    name = unix_device.removeprefix("/dev/")
    (drives.block / name / "size").write_text(f"{sectors}\n")


def _on_usb(drives, monkeypatch, port="1-4"):
    """/sys/bus/usb/devices with the USB device of the drive (as the kernel links it)."""
    root = drives.block.parent.parent / "bus" / "usb" / "devices"
    root.mkdir(parents=True, exist_ok=True)
    (root / port).symlink_to(os.path.relpath(drives.hub / port, root))
    monkeypatch.setattr(gio_storage, "SYSFS_USB", root)


def _monitor(monkeypatch, mounts=(), volumes=()):
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get",
                        staticmethod(lambda: FakeMonitor(mounts=list(mounts),
                                                         volumes=list(volumes))))


def _unmounted(drives, monkeypatch, sectors=7_800_000, vendor="091e"):
    unix = usb_drive(drives, vendor=vendor, product=FR55)
    _sized(drives, unix, sectors)
    folder = drive_folder(drives)
    volume = UnmountedVolume(unix, folder)
    _monitor(monkeypatch, volumes=[volume])
    return volume, folder


# --- the list -----------------------------------------------------------------------------------


def test_an_unmounted_garmin_drive_is_listed_to_be_mounted(drives, monkeypatch, caplog):
    from openexpress.gui.app import choice_subtitle

    volume, _folder = _unmounted(drives, monkeypatch)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        (found,) = gio_storage.find_ums_devices()
    assert (found.kind, found.mounted, found.volume, found.usb.usb_id) == (
        "ums", False, volume, "091e:0f1d")
    assert str(drives.media) not in found.uri and "/dev/" in found.uri  # a key, not a path
    assert ("device: a Garmin drive (USB 091e:0f1d) is not mounted, listed to be mounted"
            in caplog.messages)
    summary = gio_storage.probe(found)
    assert summary.reason == gio_storage.NOT_MOUNTED
    assert choice_subtitle(summary) == "Not mounted; choose it to open it"


def test_its_tile_is_connected_with_the_chip_usb_drive(drives, monkeypatch):
    from openexpress import devicelist
    from openexpress.gui import start

    _unmounted(drives, monkeypatch)
    (found,) = gio_storage.find_ums_devices()
    (tile,) = devicelist.tiles([gio_storage.probe(found)], {}, None, False)
    assert tile.connected and tile.kind == "ums"
    assert [words for words, _s, _t in start.chips(tile)] == ["USB drive"]


def test_a_remembered_drive_gets_its_name_from_the_record(drives, monkeypatch):
    """Not mounted, nothing of it is read: its USB product, kept when it was last opened,
    names it (only when one remembered drive has that product); its entry is not listed
    again as remembered."""
    from openexpress import devicelist, known_devices

    known_devices.remember(3000000055, "Forerunner 55", "006-B3869-00", "11.03", "ums",
                           usb_product=FR55)
    _unmounted(drives, monkeypatch)
    (found,) = gio_storage.find_ums_devices()
    tiles = devicelist.tiles([gio_storage.probe(found)], known_devices.load(), None, False)
    assert [(t.description, t.connected, t.kind) for t in tiles] == [
        ("Forerunner 55", True, "ums")]
    known_devices.remember(3000000056, "Forerunner 55", "006-B3869-00", "11.03", "ums",
                           usb_product=FR55)  # two of them: which one is not known
    tiles = devicelist.tiles([gio_storage.probe(found)], known_devices.load(), None, False)
    assert [t.description for t in tiles if t.connected] == [""]


def test_the_usb_product_of_a_drive_is_kept_when_it_is_seen():
    from openexpress import known_devices

    known_devices.seen(3000000055, "Forerunner 55", "006-B3869-00", "11.03", "ums",
                       usb_product=FR55)
    assert known_devices.load()[3000000055].usb_product == FR55
    known_devices.seen(3000000055, "Forerunner 55", "006-B3869-00", "11.04", "ums")
    assert known_devices.load()[3000000055].usb_product == FR55  # kept


# --- choosing it --------------------------------------------------------------------------------


def test_choosing_it_mounts_it_and_opens_the_drive(drives, monkeypatch):
    from openexpress import service

    volume, folder = _unmounted(drives, monkeypatch)
    (found,) = gio_storage.find_ums_devices()
    storage = service._open_found(found)
    assert volume.asked == 1
    assert storage.mass_storage and storage.root == folder
    assert storage.read("GARMIN/GarminDevice.xml") == RTL515_XML.encode()
    assert storage.usb_id == "091e:0f1d"


def test_a_mount_refused_says_so(drives, monkeypatch):
    from openexpress import service
    from openexpress.device.storage import DeviceError

    volume, _folder = _unmounted(drives, monkeypatch)
    volume.refusal = "Not authorized to perform operation"
    (found,) = gio_storage.find_ums_devices()
    with pytest.raises(DeviceError, match="Cannot mount the device"):
        service._open_found(found)


def test_one_unmounted_drive_alone_gives_its_tile_not_a_mount(drives, monkeypatch):
    """The window's detection: the drive is not mounted behind the user's back (D7 opens the
    one device that is mounted); it is a tile to choose."""
    from openexpress.gui import app

    volume, _folder = _unmounted(drives, monkeypatch)
    _on_usb(drives, monkeypatch)  # still on USB: its tile stays
    result = app.detect_devices()
    assert isinstance(result, app.DeviceChoices) and volume.asked == 0
    (summary,) = result.summaries
    assert summary.reason == gio_storage.NOT_MOUNTED and summary.found.kind == "ums"


def test_the_command_line_does_not_mount_it_either(drives, monkeypatch):
    from openexpress import service

    volume, _folder = _unmounted(drives, monkeypatch)
    monkeypatch.setattr(gio_storage, "usb_state", lambda: gio_storage.MASS_STORAGE)
    with pytest.raises(gio_storage.NotThereYet):
        service.open_device_or_drive()
    assert volume.asked == 0


# --- what is not listed -------------------------------------------------------------------------


def test_a_drive_without_a_medium_is_not_listed_and_said_in_the_log(drives, monkeypatch, caplog):
    """Removed safely: the Garmin device turns its drive off; only a replug helps, as the
    notice says."""
    _unmounted(drives, monkeypatch, sectors=0)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.find_ums_devices() == []
    assert ("device: a Garmin drive (USB 091e:0f1d) has no medium (removed safely?), not "
            "listed: unplug it and plug it in again" in caplog.messages)


def test_a_device_in_mass_storage_mode_without_a_volume_is_said_in_the_log(drives, monkeypatch,
                                                                         caplog):
    usb_drive(drives, product=FR55)
    _on_usb(drives, monkeypatch)
    _monitor(monkeypatch)  # no volume of it
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.find_ums_devices() == []
    assert ("device: a Garmin device in mass storage mode (USB 091e:0f1d) without a drive: "
            "unplug it and plug it in again" in caplog.messages)


def test_a_volume_of_another_vendor_is_not_listed(drives, monkeypatch):
    _unmounted(drives, monkeypatch, vendor="0781")
    assert gio_storage.find_ums_devices() == []


def test_a_drive_ejected_by_openexpress_is_not_listed(drives, monkeypatch):
    _volume, _folder = _unmounted(drives, monkeypatch)
    _on_usb(drives, monkeypatch)
    usb = gio_storage.GarminUsb(FR55, "usb", 1, 12)
    with gio_storage._ejected_lock:
        gio_storage._ejected.append(usb)
    try:
        assert gio_storage.find_ums_devices() == []
    finally:
        with gio_storage._ejected_lock:
            gio_storage._ejected.remove(usb)


def test_the_volume_of_a_mounted_drive_is_listed_once(drives, monkeypatch):
    unix = usb_drive(drives, product=FR55)
    _sized(drives, unix, 7_800_000)
    mount = FakeDriveMount(drive_folder(drives), unix)
    _monitor(monkeypatch, mounts=[mount], volumes=[mount.volume])
    (found,) = gio_storage.find_ums_devices()
    assert found.mounted


def test_without_gvfs_nothing_changes(drives, monkeypatch):
    """No gvfs: Gio knows no volumes, only what is mounted; a mounted drive as before."""
    unix = usb_drive(drives, product=FR55)
    _sized(drives, unix, 7_800_000)
    _monitor(monkeypatch)
    assert gio_storage.find_ums_devices() == []
    mount = FakeDriveMount(drive_folder(drives), unix)
    _monitor(monkeypatch, mounts=[mount])
    assert [d.mounted for d in gio_storage.find_ums_devices()] == [True]


# --- the window looks for it when it comes ------------------------------------------------------


def test_a_device_in_mass_storage_mode_coming_brings_a_detection(drives, monkeypatch):
    """No mount event comes for a drive that the system does not mount: the window's look at
    USB finds it in mass storage mode and detects once."""
    import types

    from test_detection import FakeStartPage

    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    usb_drive(drives, product=FR55)
    _on_usb(drives, monkeypatch)
    _monitor(monkeypatch)
    detected = []
    page = types.SimpleNamespace(
        session=None, busy=False, _usb_state=gio_storage.ABSENT, _usb_since=0.0, _usb_title="",
        _usb_notice=MainWindow._usb_notice, _update_actions=lambda: None,
        refresh_device=lambda **kw: detected.append(kw.get("cause")), start=FakeStartPage(),
        sections={"watch": types.SimpleNamespace(set_empty=lambda *notice: None)})
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: fn())
    MainWindow._poll_usb(page)
    MainWindow._poll_usb(page)
    assert detected == [app.Cause.PLUGGED]  # once


# --- two block devices of one device (#19: a Forerunner 55 just plugged in) ---------------
#
# lsblk on the reporter's computer: `sdc 0B` (an empty slot) and `sdd GARMIN 25.3M`, both of
# the one USB device. Its drive is sdd: one tile, the empty slot gives neither a tile, nor a
# line that its drive has no medium, nor that it has no drive.

FR55_SECTORS = 51_814  # 25.3 MiB in 512-byte sectors


def _two_slots(drives, monkeypatch, volumes):
    """The Forerunner 55 as the kernel shows it: one USB device, one mass storage interface,
    two SCSI disks below it (no partitions: the label is on the disk); sdc without a medium,
    sdd with the drive. `volumes`: which of them gvfs gives a volume, in this order."""
    from test_detection import _block

    usb_drive(drives, product=FR55, disk="sdc", partition=None)
    _sized(drives, "/dev/sdc", 0)
    interface = drives.hub / "1-4" / "1-4:1.0"
    _block(drives, interface / "host6" / "target6:0:0" / "6:0:0:1", "sdd", None)
    _sized(drives, "/dev/sdd", FR55_SECTORS)
    _on_usb(drives, monkeypatch)
    folder = drive_folder(drives)
    made = {name: UnmountedVolume(f"/dev/{name}", folder) for name in volumes}
    _monitor(monkeypatch, volumes=list(made.values()))
    return made


@pytest.mark.parametrize("volumes", [("sdd",), ("sdc", "sdd"), ("sdd", "sdc")])
def test_an_empty_slot_beside_the_drive_gives_no_tile_and_no_warning(drives, monkeypatch, caplog,
                                                                      volumes):
    from openexpress import devicelist
    from openexpress.gui import start

    _two_slots(drives, monkeypatch, volumes)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        (found,) = gio_storage.find_ums_devices()
    assert (found.uri, found.mounted, found.kind) == ("ums-volume:/dev/sdd", False, "ums")
    said = [m for m in caplog.messages if "Garmin" in m]
    assert said == [("device: a Garmin drive (USB 091e:0f1d) is not mounted, listed to be "
                     "mounted")]
    (tile,) = devicelist.tiles([gio_storage.probe(found)], {}, None, False)
    assert [words for words, _s, _t in start.chips(tile)] == ["USB drive"]


def test_chosen_it_mounts_the_slot_with_the_drive(drives, monkeypatch):
    from openexpress import service

    made = _two_slots(drives, monkeypatch, ("sdc", "sdd"))
    (found,) = gio_storage.find_ums_devices()
    storage = service._open_found(found)
    assert (made["sdc"].asked, made["sdd"].asked) == (0, 1)
    assert storage.read("GARMIN/GarminDevice.xml") == RTL515_XML.encode()


def test_the_window_lists_it_once(drives, monkeypatch):
    from openexpress.gui import app

    _two_slots(drives, monkeypatch, ("sdc", "sdd"))
    result = app.detect_devices()
    assert isinstance(result, app.DeviceChoices)
    (summary,) = result.summaries
    assert summary.found.uri == "ums-volume:/dev/sdd"


def test_both_slots_without_a_medium_say_it_once(drives, monkeypatch, caplog):
    """Removed safely: the device turns its drive off, both slots are empty: one line, as
    for one slot, and no line that it has no drive."""
    _two_slots(drives, monkeypatch, ("sdc", "sdd"))
    _sized(drives, "/dev/sdd", 0)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.find_ums_devices() == []
    assert [m for m in caplog.messages if "Garmin" in m] == [
        ("device: a Garmin drive (USB 091e:0f1d) has no medium (removed safely?), not listed: "
         "unplug it and plug it in again")]
