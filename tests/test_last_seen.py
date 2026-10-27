"""When a device was last seen (devices.json, "Last seen …" on its tile once it is gone) is
noted whenever the window reads what is connected and finds its GarminDevice.xml: in a
detection (detection._choose) and also when the list of the devices connected is read
again (detection.connected_devices: a device plugged in while others are connected, the
start page shown or a device open). Before, only the detection and the opening noted it, so
a Varia plugged in for a minute next to a watch, then unplugged, said "Last seen 8 h ago"
(27.10.2026). Every device here is made up."""

from openexpress import known_devices
from openexpress.device import gio_storage

VARIA = 3000000005


def _drive(in_the_window):
    found = gio_storage.FoundDevice("file:///run/media/user/GARMIN/", "GARMIN", True,
                                    usb=gio_storage.GarminUsb("20ee", "usb", 1, 12),
                                    kind="ums")
    summary = gio_storage.DeviceSummary(found, "Varia RTL515", "006-B3592-03", "3.36", VARIA)
    in_the_window("find_all_devices", lambda: [found])
    in_the_window("probe", lambda f: summary)
    return summary


def test_the_list_read_again_notes_when_a_device_was_seen(in_the_window, clock):
    from openexpress.gui import detection

    known_devices.remember(VARIA, "Varia RTL515", "006-B3592-03", "3.36", "ums", now=1000.0)
    summary = _drive(in_the_window)
    assert detection.connected_devices() == [summary]
    entry = known_devices.load()[VARIA]
    assert entry.last_seen == clock.current.timestamp()
    assert entry.first_seen == 1000.0  # when it was first seen stays


def test_a_device_without_details_is_not_noted(in_the_window, clock):
    """An entry whose GarminDevice.xml was not read (no unit ID) notes nothing."""
    from openexpress.gui import detection

    found = gio_storage.FoundDevice("mtp://a/", "a", True, kind="mtp")
    gone = gio_storage.DeviceSummary(found, "", "", "", None, "not-mounted")
    in_the_window("find_all_devices", lambda: [found])
    in_the_window("probe", lambda f: gone)
    in_the_window("usb_still_there", lambda usb: True)
    detection.connected_devices()
    assert known_devices.load() == {}
