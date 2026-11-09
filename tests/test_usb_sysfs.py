"""What gio_storage reads of the USB devices in /sys/bus/usb/devices, and what it passes over:
a folder it cannot list, an entry without the attribute it asks for (an interface, a hub's
leftover), a number that is not one. Each reader passes over only what it cannot use; the
port of an ejected drive is read on its own. All of /sys is made up."""

import logging

import pytest
from fakes import garmin_on_usb

gio_storage = pytest.importorskip("openexpress.device.gio_storage")
GarminUsb = gio_storage.GarminUsb

NAME = "Garmin USB inReach"
SERIAL = "0000abcdef56"  # made up
UNPLUGGED = "device: the drive ejected by openExpress was unplugged (USB 091e:0f1d)"
REPLUGGED = ("device: the drive ejected by openExpress was unplugged and plugged in again "
             "(USB 091e:0f1d)")
ELSEWHERE = ("device: the drive ejected by openExpress was unplugged and plugged in at another "
             "port (USB 091e:0f1d)")
NOT_AT_ITS_PLACE = ("device: the drive ejected by openExpress is not at its place on USB any "
                    "more (USB 091e:20ee)")


@pytest.fixture
def usb(tmp_path, monkeypatch):
    folder = tmp_path / "usb"
    folder.mkdir()
    monkeypatch.setattr(gio_storage, "SYSFS_USB", folder)
    return folder


def _device(usb, port, **files):
    """A folder of /sys/bus/usb/devices with only `files` in it."""
    base = usb / port
    base.mkdir()
    for name, value in files.items():
        (base / name).write_text(f"{value}\n")
    return base


def _interface_entry(usb, port, cls="08"):
    """An interface as /sys/bus/usb/devices lists it beside the devices: a folder of its own,
    with its class and none of the attributes of a device."""
    return _device(usb, f"{port}:1.0", bInterfaceClass=cls)


@pytest.fixture
def several(usb):
    """A Garmin device with an unreadable bus number (1-3, a drive), an inReach Mini (1-4, HID
    beside a drive), a Varia in mass storage mode (1-5), a Garmin device without its product
    ID (1-6), an interface entry, a phone and the root hub."""
    garmin_on_usb(usb, "1-3", "4c9a", ("08",), devnum=8)
    (usb / "1-3" / "busnum").write_text("not a number\n")
    garmin_on_usb(usb, "1-4", "0b89", ("03", "08"), devnum=9, product=NAME)
    _interface_entry(usb, "1-4")
    garmin_on_usb(usb, "1-5", "20ee", ("08",), devnum=12)
    (usb / "1-6").mkdir()
    for name, value in (("idVendor", "091e"), ("busnum", 1), ("devnum", 13)):
        (usb / "1-6" / name).write_text(f"{value}\n")
    _device(usb, "2-1", idVendor="18d1", idProduct="4ee1", busnum=2, devnum=7)
    _device(usb, "usb1", idVendor="1d6b", idProduct="0002", busnum=1, devnum=1)
    return usb


def test_a_folder_that_cannot_be_listed_has_no_device(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(gio_storage, "SYSFS_USB", tmp_path / "missing")
    usb = GarminUsb("0f1d", 1, 12, serial=SERIAL)
    assert gio_storage.usb_still_there(usb) is False
    assert gio_storage._identify("mtp://[usb:001,012]/", None) is None
    assert gio_storage.drives_on_usb() == frozenset()
    assert gio_storage.garmin_usb_summary() == []
    assert gio_storage.garmin_products_on_usb() == []
    assert gio_storage.unsupported_devices() == []
    assert gio_storage.usb_state() == gio_storage.ABSENT
    gio_storage.note_drive_place(usb)
    assert gio_storage._drive_places == {}
    gio_storage.note_ejected(usb, "1-4")  # its port known: empty for a while is an unplugging
    gio_storage.note_ejected(GarminUsb("20ee", 1, 13))  # its port not known
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.ejected_drive_waits(clock=lambda: 100.0)
        assert not gio_storage.ejected_drive_waits(clock=lambda: 100.0 + gio_storage.USB_GAP_S)
    assert caplog.messages == [NOT_AT_ITS_PLACE, UNPLUGGED]


def test_each_reader_passes_over_what_it_cannot_read(several):
    assert gio_storage.garmin_usb_summary() == [("091e:4c9a", None, ["08"]),
                                                ("091e:0b89", NAME, ["03", "08"]),
                                                ("091e:20ee", None, ["08"])]
    assert gio_storage.garmin_products_on_usb() == ["4c9a", "0b89", "20ee"]
    assert gio_storage.drives_on_usb() == frozenset({"1-12"})
    assert gio_storage.unsupported_devices() == [("091e:0b89", NAME)]
    assert gio_storage.usb_state() == gio_storage.MASS_STORAGE


def test_a_device_is_found_by_its_bus_and_device_number(several):
    assert gio_storage.usb_still_there(GarminUsb("0b89", 1, 9))
    assert gio_storage.usb_still_there(GarminUsb(None, 1, 9))
    assert gio_storage.usb_still_there(GarminUsb("0B89", 1, 9))
    assert not gio_storage.usb_still_there(GarminUsb("0b89", 1, 10))
    assert not gio_storage.usb_still_there(GarminUsb("20ee", 1, 9))
    assert not gio_storage.usb_still_there(GarminUsb("4ee1", 2, 7))  # not Garmin's
    assert not gio_storage.usb_still_there(GarminUsb("0b89"))
    found = gio_storage._identify("mtp://[usb:001,012]/", None)
    assert (found, found.bus, found.address) == (GarminUsb("20ee"), 1, 12)
    assert gio_storage._identify("mtp://[usb:002,007]/", None) is None
    # Found, but its IDs cannot be read: the others are looked at, none of them is it.
    assert not gio_storage.usb_still_there(GarminUsb(None, 1, 13))
    assert gio_storage._identify("mtp://[usb:001,013]/", None) is None


def test_the_port_of_a_drive_is_the_name_of_its_folder(several):
    gio_storage.note_drive_place(GarminUsb("20ee", 1, 12))
    gio_storage.note_drive_place(GarminUsb("20ee", 1, 99))  # not on USB
    gio_storage.note_drive_place(GarminUsb("20ee"))  # its place not known
    assert gio_storage._drive_places == {"1-5": None}


def test_a_device_that_stopped_responding_is_left_out_but_still_in_the_summary(several):
    gio_storage.note_stalled(GarminUsb("0b89", 1, 9))
    assert gio_storage.garmin_products_on_usb() == ["4c9a", "20ee"]
    assert gio_storage.unsupported_devices() == []
    assert [usb_id for usb_id, _name, _classes in gio_storage.garmin_usb_summary()] == [
        "091e:4c9a", "091e:0b89", "091e:20ee"]


# --- the port of a drive ejected by openExpress -----------------------------------------------


def _ejected(usb, product="0f1d", serial=SERIAL):
    """The drive at port 1-4 (device number 12), ejected by openExpress."""
    garmin_on_usb(usb, "1-4", product or "0f1d", ("08",), devnum=12, serial=serial)
    gio_storage.note_ejected(GarminUsb(product, 1, 12, serial=serial))


def _replace(usb, port, **files):
    import shutil

    shutil.rmtree(usb / port)
    return _device(usb, port, **files)


def test_another_vendors_device_at_its_port_leaves_the_port_empty(usb, caplog):
    _ejected(usb)
    _replace(usb, "1-4", idVendor="18d1", idProduct="4ee1", busnum=1, devnum=14)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.ejected_drive_waits(clock=lambda: 100.0)
        assert not gio_storage.ejected_drive_waits(clock=lambda: 100.0 + gio_storage.USB_GAP_S)
    assert caplog.messages == [UNPLUGGED]


def test_its_device_number_unreadable_is_another_device_number(usb, caplog):
    _ejected(usb)
    assert gio_storage.ejected_drive_waits(clock=lambda: 100.0)
    (usb / "1-4" / "devnum").write_text("not a number\n")
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert not gio_storage.ejected_drive_waits(clock=lambda: 101.0)
    assert caplog.messages == [REPLUGGED]


def test_at_another_port_the_entries_without_ids_are_passed_over(usb, caplog):
    _ejected(usb)
    _replace(usb, "1-4")  # its port empty, but for a leftover interface entry
    _interface_entry(usb, "1-4")
    garmin_on_usb(usb, "2-1", "0f1d", ("08",), devnum=3, serial=SERIAL)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert not gio_storage.ejected_drive_waits(clock=lambda: 100.0)
    assert caplog.messages == [ELSEWHERE]


def test_without_its_product_it_is_never_seen_at_another_port(usb, caplog):
    _ejected(usb, product=None)
    _replace(usb, "1-4")
    garmin_on_usb(usb, "2-1", "0f1d", ("08",), devnum=3, serial=SERIAL)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.ejected_drive_waits(clock=lambda: 100.0)
        assert not gio_storage.ejected_drive_waits(clock=lambda: 100.0 + gio_storage.USB_GAP_S)
    assert caplog.messages == [
        "device: the drive ejected by openExpress was unplugged (USB 091e:unknown)"]
