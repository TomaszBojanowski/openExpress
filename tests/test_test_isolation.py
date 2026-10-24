"""The tests see no device of the computer running them (review of 24.10.2026, area 6): a
watch plugged in while they run is not looked for on its USB bus, not listed by the session's
volume monitor, and the windows the tests make stop listening when their test ends."""

from pathlib import Path

REAL_SYSFS_USB = Path("/sys/bus/usb/devices")


def test_the_usb_devices_of_this_computer_are_not_seen():
    from openexpress.device import gio_storage

    assert gio_storage.SYSFS_USB != REAL_SYSFS_USB
    assert gio_storage.SYSFS_USB.is_dir() and list(gio_storage.SYSFS_USB.iterdir()) == []
    assert gio_storage._garmin_on_usb() == []
    assert gio_storage.usb_state() == gio_storage.ABSENT
