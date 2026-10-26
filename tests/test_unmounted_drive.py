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
import types

import pytest
from test_detection import (  # noqa: F401 - the fixture
    DRIVE_SERIAL,
    RTL515_XML,
    FakeDriveMount,
    FakeMonitor,
    FakeVolume,
    drive_folder,
    drives,
    monitor_with,
    usb_drive,
)

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

FR55 = "0f1d"


class UnmountedVolume(FakeVolume):
    """Like the volume of a drive that is not mounted: mounting it (Gio.Volume.mount) answers
    from the main loop, and then it has a mount, at `folder`."""

    def __init__(self, unix_device, folder, name="GARMIN", uuid=None):
        super().__init__(None, unix_device, name, uuid)
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


def _unmounted(drives, monkeypatch, sectors=7_800_000, vendor="091e", serial=DRIVE_SERIAL,
               uuid=None):
    unix = usb_drive(drives, vendor=vendor, product=FR55, serial=serial)
    _sized(drives, unix, sectors)
    folder = drive_folder(drives)
    volume = UnmountedVolume(unix, folder, uuid=uuid)
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


# --- state (b): the drive removed safely (#19) -------------------------------------------
#
# After "safely remove" the Forerunner 55 keeps its mass storage interface; its drive (sdd)
# is gone and the empty slot (sdc, 0 B) stays. The window says it is a USB drive that is not
# mounted, to be plugged in again - never that the device is still starting.


def _removed_safely(drives, monkeypatch):
    usb_drive(drives, product=FR55, disk="sdc", partition=None)
    _sized(drives, "/dev/sdc", 0)
    _on_usb(drives, monkeypatch)
    _monitor(monkeypatch)  # no volume: nothing to mount


def test_removed_safely_the_usb_state_is_a_drive(drives, monkeypatch, caplog):
    _removed_safely(drives, monkeypatch)
    assert gio_storage.usb_state() == gio_storage.MASS_STORAGE
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.find_ums_devices() == []
    assert ("device: a Garmin device in mass storage mode (USB 091e:0f1d) without a drive: "
            "unplug it and plug it in again" in caplog.messages)


@pytest.mark.parametrize("waited_s", [0.0, 50.0, 600.0])
def test_removed_safely_the_window_says_drive_not_starting(drives, monkeypatch, waited_s):
    from openexpress.gui.app import MainWindow

    _removed_safely(drives, monkeypatch)
    title, text, busy = MainWindow._usb_notice(gio_storage.usb_state(), waited_s)
    assert (title, busy) == ("USB drive", False)
    assert text == gio_storage.drive_not_mounted_text()
    assert "drive is not mounted" in text and "plug it in again" in text
    assert "starting" not in title + text


def test_removed_safely_the_command_line_says_the_same(drives, monkeypatch):
    from openexpress import service

    _removed_safely(drives, monkeypatch)
    with pytest.raises(gio_storage.NotThereYet) as refused:
        service.open_device_or_drive()
    assert str(refused.value) == gio_storage.drive_not_mounted_text()


# --- state (b) as it is: after it was ejected, the watch is on USB as 091e:0003 (#19) ------
#
# lsusb on the reporter's computer: after "safely remove" the Forerunner 55 is 091e:0003, and
# 091e:0f1d once plugged in again. At the place on USB where a Garmin drive was in this run,
# 091e:0003 is that drive switched off: the window and the command line say so at once. A
# 091e:0003 without that (an MTP watch starting) is still a device starting.

DRIVE_OFF_TEXT = ("The watch has switched its drive off after it was ejected. Unplug the cable "
                  "and plug it in again.")


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _reenumerated(drives, product="0003", port="1-4", devnum=13, cls="ff"):
    """The device at `port` gone and back as `product` (a new device number), as the kernel
    shows it: one interface of class `cls`, no drive."""
    import shutil

    from test_detection import usb_device

    shutil.rmtree(drives.hub / port, ignore_errors=True)
    usb = usb_device(drives.hub, port, 1, devnum, "091e", product)
    (usb / f"{port}:1.0").mkdir()
    (usb / f"{port}:1.0" / "bInterfaceClass").write_text(f"{cls}\n")


def _gone(drives, port="1-4"):
    import shutil

    shutil.rmtree(drives.hub / port)


def _fr55_drive(drives, monkeypatch):
    """The Forerunner 55 as a drive (sdd with a medium), listed once as the window lists it."""
    _two_slots(drives, monkeypatch, ("sdd",))
    assert [d.uri for d in gio_storage.find_ums_devices()] == ["ums-volume:/dev/sdd"]


def test_ejected_the_watch_comes_back_as_0003_and_the_window_says_it_switched_its_drive_off(
        drives, monkeypatch, caplog):
    from openexpress.gui.app import MainWindow

    _fr55_drive(drives, monkeypatch)
    assert gio_storage.usb_state() == gio_storage.MASS_STORAGE
    _reenumerated(drives)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.usb_state() == gio_storage.DRIVE_OFF
        assert gio_storage.usb_state() == gio_storage.DRIVE_OFF
    assert [m for m in caplog.messages if "0003" in m] == [
        ("USB: a Garmin device 091e:0003 where a Garmin drive was: its drive is switched off "
         "after it was ejected, to be plugged in again")]
    for waited_s in (0.0, 16.0, 50.0):
        assert MainWindow._usb_notice(gio_storage.usb_state(), waited_s) == (
            "USB drive", DRIVE_OFF_TEXT, False)


def test_ejected_the_command_line_says_it_at_once(drives, monkeypatch):
    from openexpress import service

    _fr55_drive(drives, monkeypatch)
    _reenumerated(drives)

    def sleep(_s):
        pytest.fail("waited for a device that switched its drive off")

    with pytest.raises(gio_storage.MassStorageNotice) as said:
        gio_storage.open_device_storage(wait=True, sleep=sleep)
    assert str(said.value) == DRIVE_OFF_TEXT
    with pytest.raises(gio_storage.MassStorageNotice) as said:
        service.open_device_or_drive(wait=False)
    assert str(said.value) == DRIVE_OFF_TEXT


def test_a_drive_unmounted_while_open_is_known_by_its_place(drives, monkeypatch):
    """The window does not look at USB while a device is open: the drive's own unmount (or
    eject) says where it was, before the device comes back as 091e:0003."""
    unix = usb_drive(drives, product=FR55)
    _sized(drives, unix, FR55_SECTORS)
    _on_usb(drives, monkeypatch)
    _monitor(monkeypatch)
    gio_storage.note_drive_place(gio_storage.GarminUsb(FR55, "usb", 1, 12))
    _reenumerated(drives)
    assert gio_storage.usb_state() == gio_storage.DRIVE_OFF


def test_the_window_notes_the_place_of_the_open_drive_it_sees_unmounted(drives, monkeypatch):
    from openexpress.gui.app import MainWindow

    unix = usb_drive(drives, product=FR55)
    _sized(drives, unix, FR55_SECTORS)
    _on_usb(drives, monkeypatch)
    _monitor(monkeypatch)
    folder = drive_folder(drives)
    mount = FakeDriveMount(folder, unix)
    shown = []
    storage = types.SimpleNamespace(label=folder.as_uri() + "/", device_uri=folder.as_uri(),
                                    usb=gio_storage.GarminUsb(FR55, "usb", 1, 12))
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(storage=storage, mass_storage=True,
                                      device=types.SimpleNamespace(unit_id=3000000055)),
        connected=None, show_no_device=lambda: shown.append(1))
    MainWindow._on_mount_removed(win, None, mount)
    assert shown == [1] and win.session is None
    _reenumerated(drives)
    assert gio_storage.usb_state() == gio_storage.DRIVE_OFF


def test_0003_without_a_drive_before_is_a_device_starting(drives, monkeypatch, caplog):
    """An MTP watch starting (091e:0003 before its normal mode): as before; after 25 s (past
    the about 19 s of a fēnix 8 starting) the page adds that it may be plugged in again,
    after the wait it is not ready."""
    from openexpress.gui.app import REPLUG_HINT, MainWindow

    _on_usb(drives, monkeypatch, port="1-8")
    _reenumerated(drives, port="1-8")
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.usb_state() == gio_storage.STARTING
    assert [m for m in caplog.messages if "0003" in m] == [
        "USB: a Garmin device 091e:0003: in its transitional mode, starting"]
    starting = "This takes a moment after the cable is plugged in"
    for waited_s in (5.0, 19.0, 24.9):
        assert MainWindow._usb_notice(gio_storage.STARTING, waited_s) == (
            "The device is starting…", starting, True)
    assert MainWindow._usb_notice(gio_storage.STARTING, 25.0) == (
        "The device is starting…", f"{starting}. {REPLUG_HINT}", True)
    assert MainWindow._usb_notice(gio_storage.STARTING, 50.0) == (
        "The device is not ready", gio_storage.not_ready_text(gio_storage.STARTING), False)


def test_a_device_in_its_normal_mode_at_that_place_forgets_the_drive(drives, monkeypatch):
    _fr55_drive(drives, monkeypatch)
    gio_storage.usb_state()
    _reenumerated(drives, product="4fdd")  # another watch there, over MTP
    assert gio_storage.usb_state() == gio_storage.PRESENT
    _reenumerated(drives, devnum=14)
    assert gio_storage.usb_state() == gio_storage.STARTING


def test_unplugged_for_longer_than_a_moment_forgets_the_drive(drives, monkeypatch):
    clock = Clock()
    _fr55_drive(drives, monkeypatch)
    gio_storage.usb_state(clock=clock)
    _gone(drives)
    assert gio_storage.usb_state(clock=clock) == gio_storage.ABSENT
    clock.now += 2  # off the bus a moment between its two modes: still the drive's place
    _reenumerated(drives)
    assert gio_storage.usb_state(clock=clock) == gio_storage.DRIVE_OFF
    _gone(drives)
    gio_storage.usb_state(clock=clock)
    clock.now += gio_storage.USB_GAP_S + 1  # unplugged
    gio_storage.usb_state(clock=clock)
    _reenumerated(drives, devnum=15)
    assert gio_storage.usb_state(clock=clock) == gio_storage.STARTING


def test_the_words_in_polish():
    from openexpress import i18n

    i18n.setup("pl")
    try:
        said = gio_storage.drive_off_text()
    finally:
        i18n.setup("en")
    assert said == ("Zegarek wyłączył swój dysk po wysunięciu. Odłącz kabel i\u00a0podłącz go "
                    "ponownie.")


# --- chosen from the start page: the others stay connected (0.7.1, GNOME) -------------------
#
# Seen on GNOME: a Varia ejected in Files, its tile "Not mounted; choose it to open it" chosen,
# mounted and opened; back on the start page only the Varia was connected, the fēnix and the
# Edge (still on USB) under "Remembered". Its entry had no details, nor the address it has once
# mounted, so the open device was none of the entries and the list was dropped (keep_if_open).

FENIX, EDGE, VARIA = 3000000201, 3000000202, 3000000203


def _three_listed():
    from openexpress.device.gio_storage import DeviceSummary, FoundDevice, GarminUsb

    return [DeviceSummary(FoundDevice("mtp://fenix/", "x", True), "fēnix 8", "006-B4532-00",
                          "23.39", FENIX),
            DeviceSummary(FoundDevice("mtp://edge/", "x", True), "Edge 1050", "006-B4440-00",
                          "33.13", EDGE),
            DeviceSummary(FoundDevice(gio_storage.UNMOUNTED_DRIVE + "/dev/sdb", "GARMIN", False,
                                      kind="ums", usb=GarminUsb("20ee", "usb", 1, 12)),
                          reason=gio_storage.NOT_MOUNTED)]


def _varia_session():
    usb = gio_storage.GarminUsb("20ee", "usb", 1, 12)
    storage = types.SimpleNamespace(device_uri="file:///run/media/u/GARMIN/",
                                    label="file:///run/media/u/GARMIN/", usb=usb)
    device = types.SimpleNamespace(unit_id=VARIA, description="Varia RTL515",
                                   part_number="006-B3592-03", software_version_str="3.36")
    return types.SimpleNamespace(storage=storage, device=device, mass_storage=True)


def _start_window(monkeypatch, listed):
    from fakes import RunNow

    from openexpress.gui import app
    from openexpress.gui.connected import Connected

    session = _varia_session()
    monkeypatch.setattr(app.service, "open_session", lambda found: session)
    monkeypatch.setattr(app.service, "check_session", lambda s: s)
    monkeypatch.setattr(app, "with_space", lambda s: s)
    monkeypatch.setattr(app, "resolve_account", lambda win: None)
    reread = []
    win = types.SimpleNamespace(
        busy=False, session=None, run_task=RunNow(), toast=lambda text: None,
        connected=Connected(devices=list(listed), choosing=True),
        pages=types.SimpleNamespace(get_visible_child_name=lambda: "overview"),
        _clear_device_pages=lambda: None, show_overview=lambda: None,
        load_section=lambda page: None, _show_device_view=lambda: None,
        _reread_connected=lambda: reread.append(getattr(win, "_reread_cause", None)))
    for name in ("open_choice", "show_device", "_is_open", "_start_summaries"):
        setattr(win, name, types.MethodType(getattr(app.MainWindow, name), win))
    return win, reread


def test_a_drive_mounted_from_its_tile_leaves_the_others_connected(monkeypatch):
    from openexpress import devicelist, known_devices
    from openexpress.gui.navigation import Cause

    for unit, name, part, kind in ((FENIX, "fēnix 8", "006-B4532-00", "mtp"),
                                   (EDGE, "Edge 1050", "006-B4440-00", "mtp"),
                                   (VARIA, "Varia RTL515", "006-B3592-03", "ums")):
        known_devices.remember(unit, name, part, "1.00", kind, now=1000.0,
                               usb_product="20ee" if kind == "ums" else None)
    listed = _three_listed()
    win, reread = _start_window(monkeypatch, listed)
    win.open_choice(listed[2].found)  # the tile of the Varia: mounted and opened
    assert win.session is not None
    summaries, _notice = win._start_summaries()  # back on the start page
    tiles = devicelist.tiles(summaries, known_devices.load(), None, False)
    assert sorted(t.description for t in tiles if t.connected) == [
        "Edge 1050", "Varia RTL515", "fēnix 8"]
    assert not [t for t in tiles if not t.connected]  # none of them "remembered"
    # Then the list is read again whole, as Refresh does on the start page.
    assert reread == [Cause.REFRESH_ON_START]
    # And nothing was written as if the others had gone.
    assert {u: k.last_seen for u, k in known_devices.load().items()} == {
        FENIX: 1000.0, EDGE: 1000.0, VARIA: 1000.0}


def test_a_device_chosen_with_its_details_is_opened_as_before(monkeypatch):
    """An MTP device chosen from the list: its entry is the open one (by its unit ID), the list
    stays, and it is not read again."""
    listed = _three_listed()
    win, reread = _start_window(monkeypatch, listed)
    fenix = _varia_session()
    fenix.device.unit_id, fenix.mass_storage = FENIX, False
    from openexpress.gui import app

    monkeypatch.setattr(app.service, "open_session", lambda found: fenix)
    win.open_choice(listed[0].found)
    summaries, _notice = win._start_summaries()
    assert summaries == listed and reread == []


# --- what tells one drive from another while nothing of it is read (analysis 18) ----------------
#
# Its USB serial (a fēnix 8's is its unit ID in hexadecimal; a Varia Rearview Radar has none,
# 26.10.2026) and the UUID of its volume (udisks reads it without mounting: a FAT volume ID).
# Both are as private as the unit ID: never in the log, nor in the report.

DRIVE_ID_SERIAL = "7ZQ1W1FE0001"  # made up; not hexadecimal, so not a unit ID
DRIVE_ID_UUID = "1A2B-3C4D"       # made up, in the form udisks gives a FAT volume ID
VARIA_UNIT = 3000000003       # RTL515_XML


def _mounted(drives, monkeypatch, serial=DRIVE_ID_SERIAL, uuid=DRIVE_ID_UUID):
    mount = FakeDriveMount(drive_folder(drives), usb_drive(drives, serial=serial), uuid=uuid)
    monitor_with(monkeypatch, mount)
    return mount


def test_an_unmounted_drive_has_its_usb_serial_and_the_uuid_of_its_volume(drives, monkeypatch):
    _unmounted(drives, monkeypatch, serial=DRIVE_ID_SERIAL, uuid=DRIVE_ID_UUID)
    (found,) = gio_storage.find_ums_devices()
    assert (found.usb.serial, found.usb.volume_uuid) == (DRIVE_ID_SERIAL, DRIVE_ID_UUID)
    assert DRIVE_ID_SERIAL not in repr(found) and DRIVE_ID_UUID not in repr(found)


def test_a_mounted_drive_has_them_too(drives, monkeypatch):
    mount = _mounted(drives, monkeypatch)
    usb = gio_storage.garmin_ums(mount)
    assert (usb.serial, usb.volume_uuid) == (DRIVE_ID_SERIAL, DRIVE_ID_UUID)
    (found,) = gio_storage.find_ums_devices()
    assert (found.usb.serial, found.usb.volume_uuid) == (DRIVE_ID_SERIAL, DRIVE_ID_UUID)


@pytest.mark.parametrize("uuid", [DRIVE_ID_UUID, None])
def test_a_drive_without_a_usb_serial_has_none(drives, monkeypatch, uuid):
    """A Varia Rearview Radar (26.10.2026): no serial in /sys; its volume may have a UUID."""
    _unmounted(drives, monkeypatch, serial=None, uuid=uuid)
    (found,) = gio_storage.find_ums_devices()
    assert (found.usb.serial, found.usb.volume_uuid) == (None, uuid)


def test_they_are_kept_with_the_drive_when_it_is_seen():
    from openexpress import known_devices

    known_devices.seen(VARIA_UNIT, "Varia RTL515", "006-B3592-03", "3.36", "ums",
                       usb_product="20ee", usb_serial=DRIVE_ID_SERIAL, volume_uuid=DRIVE_ID_UUID)
    known = known_devices.load()[VARIA_UNIT]
    assert (known.usb_serial, known.volume_uuid) == (DRIVE_ID_SERIAL, DRIVE_ID_UUID)
    known_devices.seen(VARIA_UNIT, "Varia RTL515", "006-B3592-03", "3.37", "ums")
    known = known_devices.load()[VARIA_UNIT]
    assert (known.usb_serial, known.volume_uuid) == (DRIVE_ID_SERIAL, DRIVE_ID_UUID)  # kept


@pytest.mark.parametrize("value", [123, "", "a b", "x" * 200, ["1A2B-3C4D"]])
def test_a_damaged_serial_or_uuid_in_the_file_is_not_kept(value):
    import json

    from openexpress import known_devices

    known_devices.seen(VARIA_UNIT, "Varia RTL515", "006-B3592-03", "3.36", "ums",
                       usb_product="20ee")
    data = json.loads(known_devices.path().read_text())
    data["devices"][str(VARIA_UNIT)].update(usb_serial=value, volume_uuid=value)
    known_devices.path().write_text(json.dumps(data))
    known = known_devices.load()[VARIA_UNIT]  # the entry stays
    assert (known.usb_serial, known.volume_uuid, known.usb_product) == (None, None, "20ee")


def test_opening_a_drive_keeps_them_and_logs_only_what_they_are(drives, monkeypatch, caplog):
    from openexpress import known_devices, service

    _mounted(drives, monkeypatch)
    (found,) = gio_storage.find_ums_devices()
    with caplog.at_level(logging.INFO):
        service.open_session(found=found)
    known = known_devices.load()[VARIA_UNIT]
    assert (known.usb_product, known.usb_serial, known.volume_uuid) == (
        "20ee", DRIVE_ID_SERIAL, DRIVE_ID_UUID)
    assert ("device: this drive's USB serial is not its unit ID; its volume has a UUID"
            in caplog.messages)


@pytest.mark.parametrize(("serial", "uuid", "line"), [
    (f"{VARIA_UNIT:012x}", None,
     "device: this drive's USB serial is its unit ID in hexadecimal; its volume has no UUID"),
    (None, DRIVE_ID_UUID, "device: this drive has no USB serial; its volume has a UUID"),
])
def test_the_line_of_the_log_says_which_keys_a_drive_gives(drives, monkeypatch, caplog, serial,
                                                            uuid, line):
    from openexpress import service

    _mounted(drives, monkeypatch, serial=serial, uuid=uuid)
    (found,) = gio_storage.find_ums_devices()
    with caplog.at_level(logging.INFO):
        service.open_session(found=found)
    assert line in caplog.messages


def test_the_window_keeps_them_for_a_drive_it_lists(drives, monkeypatch):
    """The list (app._choose) remembers each device read before anything is chosen."""
    from openexpress import known_devices
    from openexpress.gui import app

    _mounted(drives, monkeypatch)
    (found,) = gio_storage.find_ums_devices()
    monkeypatch.setattr(app.service, "open_session", lambda found: "opened")
    monkeypatch.setattr(app.service, "check_session", lambda session: session)
    assert app._choose([found]) == "opened"
    known = known_devices.load()[VARIA_UNIT]
    assert (known.usb_serial, known.volume_uuid) == (DRIVE_ID_SERIAL, DRIVE_ID_UUID)


def test_the_command_line_keeps_them_too(drives, monkeypatch, capsys):
    from openexpress import cli, known_devices, service

    _mounted(drives, monkeypatch)
    (found,) = gio_storage.find_ums_devices()
    monkeypatch.setattr(service, "open_device_or_drive",
                        lambda **kw: gio_storage.open_ums_storage(found))
    assert cli.main(["info"]) == 0
    known = known_devices.load()[VARIA_UNIT]
    assert (known.usb_product, known.usb_serial, known.volume_uuid) == (
        "20ee", DRIVE_ID_SERIAL, DRIVE_ID_UUID)


def test_they_never_reach_the_log_nor_the_report(drives, monkeypatch, log_file):
    from openexpress import applog, report, service

    _mounted(drives, monkeypatch)
    applog.setup("cli")
    log = logging.getLogger("openexpress.test")
    (found,) = gio_storage.find_ums_devices()  # hidden as soon as they are read
    log.info("listed: %s %s %s", DRIVE_ID_SERIAL, DRIVE_ID_UUID, DRIVE_ID_UUID.lower())
    session = service.open_session(found=found)
    log.info("opened: %s %s", DRIVE_ID_SERIAL.lower(), DRIVE_ID_UUID)
    text = log_file.read_text()
    assert "listed: <serial> <volume> <volume>" in text and "opened: <serial> <volume>" in text
    for value in (DRIVE_ID_SERIAL, DRIVE_ID_UUID):
        assert value.casefold() not in text.casefold()
    words = report.build(session.device, session.storage)
    assert DRIVE_ID_SERIAL not in words and DRIVE_ID_UUID not in words


def test_a_session_hides_them_also_when_they_were_not_read_for_a_list():
    """applog.hide_device with the USB of the drive (service.open_session, the command line)."""
    from conftest import DEVICE_XML

    from openexpress import applog
    from openexpress.device.xmldevice import parse

    usb = gio_storage.GarminUsb("20ee", "usb", 1, 12, serial=DRIVE_ID_SERIAL, volume_uuid=DRIVE_ID_UUID)
    applog.hide_device(parse(DEVICE_XML.encode()), usb)
    assert applog.redact(f"{DRIVE_ID_SERIAL} {DRIVE_ID_UUID.lower()}") == "<serial> <volume>"
