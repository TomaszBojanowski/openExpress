"""Garmin devices are recognised by their USB vendor ID, whatever the MTP address looks like.

All serial numbers and USB trees here are made up.
"""

import types

import pytest

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

NUMERIC = "mtp://091e_51b4_0000abcdef12/"
NAMED = "mtp://Garmin_Edge_1050_0000abcdef34/"
PHONE = "mtp://Google_Pixel_8_3A091EXY0000/"  # the serial happens to contain "091e"
PHONE_NUMERIC = "mtp://18d1_4ee1_AB091E120000/"


class FakeFile:
    def __init__(self, uri):
        self.uri = uri

    def get_uri(self):
        return self.uri


class FakeVolume:
    def __init__(self, uri, unix_device=None, name="volume"):
        self.uri, self.unix_device, self.name = uri, unix_device, name

    def get_activation_root(self):
        return FakeFile(self.uri) if self.uri else None

    def get_identifier(self, kind):
        return self.unix_device if kind == "unix-device" else None

    def get_name(self):
        return self.name


class FakeMount:
    """Like a gvfs MTP mount: it has a root, but no volume and no drive."""

    def __init__(self, uri, name="mtp"):
        self.uri, self.name = uri, name

    def get_root(self):
        return FakeFile(self.uri)

    def get_name(self):
        return self.name


class FakeMonitor:
    def __init__(self, mounts=(), volumes=()):
        self.mounts, self.volumes = list(mounts), list(volumes)

    def get_mounts(self):
        return self.mounts

    def get_volumes(self):
        return self.volumes


def usb_device(root, name, bus, devnum, vendor, product, **extra):
    d = root / name
    d.mkdir(parents=True)
    for attr, value in {"busnum": bus, "devnum": devnum, "idVendor": vendor,
                        "idProduct": product, **extra}.items():
        (d / attr).write_text(f"{value}\n")
    return d


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    """A made-up /sys/bus/usb/devices: a hub, a fenix, an Edge and a phone."""
    root = tmp_path / "sys"
    usb_device(root, "usb1", 1, 1, "1d6b", "0002")
    usb_device(root, "1-8", 1, 31, "091e", "51b4")
    usb_device(root, "1-2", 1, 28, "091E", "5158", serial="0000abcdef34")
    usb_device(root, "2-1", 2, 7, "18d1", "4ee1")
    (root / "1-8:1.0").mkdir()  # an interface: no busnum, must be skipped
    (root / "broken").mkdir()
    (root / "broken" / "busnum").write_text("not a number\n")
    monkeypatch.setattr(gio_storage, "SYSFS_USB", root)
    return root


@pytest.fixture
def no_sysfs(tmp_path, monkeypatch):
    monkeypatch.setattr(gio_storage, "SYSFS_USB", tmp_path / "missing")


def identify(uri, unix_device="unset", **kw):
    volumes = [] if unix_device == "unset" else [FakeVolume(uri, unix_device)]
    return gio_storage.garmin_usb(FakeMount(uri), FakeMonitor(volumes=volumes), **kw)


# --- the USB device decides ------------------------------------------------------------------


def test_numeric_address(sysfs):
    usb = identify(NUMERIC, "/dev/bus/usb/001/031")
    assert usb == gio_storage.GarminUsb("51b4", "usb") and usb.usb_id == "091e:51b4"


def test_address_made_of_usb_strings(sysfs):
    usb = identify(NAMED, "/dev/bus/usb/001/028")
    assert usb == gio_storage.GarminUsb("5158", "usb") and usb.usb_id == "091e:5158"


@pytest.mark.parametrize("uri", [PHONE, PHONE_NUMERIC, "mtp://Garmin_lookalike_000000000000/",
                                 "mtp://091e_51b4_000000000000/"])
def test_device_of_another_vendor_is_not_garmin(sysfs, uri):
    # Whatever the address says: the USB device answers 18d1.
    assert identify(uri, "/dev/bus/usb/002/007") is None


def test_unmounted_volume(sysfs):
    vol = FakeVolume(NAMED, "/dev/bus/usb/001/028")
    assert gio_storage.garmin_usb(vol, FakeMonitor()) == gio_storage.GarminUsb("5158", "usb")
    assert gio_storage.garmin_usb(FakeVolume(None), FakeMonitor()) is None


def test_volume_is_matched_by_address_ignoring_the_trailing_slash(sysfs):
    monitor = FakeMonitor(volumes=[FakeVolume(PHONE, "/dev/bus/usb/002/007"),
                                   FakeVolume(NAMED.rstrip("/"), "/dev/bus/usb/001/028")])
    assert gio_storage.garmin_usb(FakeMount(NAMED), monitor).product == "5158"
    assert gio_storage.garmin_usb(FakeMount(PHONE), monitor) is None


@pytest.mark.parametrize("uri", ["file:///home/user", "gphoto2://091e_51b4_x/", "smb://garmin_nas/",
                                 "", None])
def test_only_mtp_addresses(sysfs, uri):
    assert gio_storage._identify(uri, FakeVolume(uri, "/dev/bus/usb/001/031")) is None


def test_serial_is_never_read(sysfs):
    usb = identify(NAMED, "/dev/bus/usb/001/028")
    assert "0000abcdef34" not in repr(usb) + usb.usb_id


# --- older address form: mtp://[usb:BBB,DDD]/ (not tried on a device) ------------------------


def test_usb_address_form(sysfs):
    assert identify("mtp://[usb:001,028]/") == gio_storage.GarminUsb("5158", "usb")
    assert identify("mtp://[usb:001,031]/").usb_id == "091e:51b4"
    assert identify("mtp://[usb:002,007]/") is None  # the phone
    assert identify("mtp://[usb:009,099]/") is None  # no such device


def test_usb_address_form_without_sysfs(no_sysfs):
    assert identify("mtp://[usb:001,028]/") is None


# --- fallback: the USB properties cannot be read ----------------------------------------------


@pytest.mark.parametrize("uri, expected", [
    (NUMERIC, ("51b4", "uri")),
    ("mtp://091E_4F67_0000ABCDEF56/", ("4f67", "uri")),
    (NAMED, (None, "uri")),
    ("mtp://GARMIN_fenix_0000abcdef12/", (None, "uri")),
    (PHONE, None),
    (PHONE_NUMERIC, None),
    ("mtp://Not_Garmin_0000abcdef12/", None),
    ("mtp://x091e_51b4_0000abcdef12/", None),
])
def test_fallback_uses_the_start_of_the_host_name(no_sysfs, uri, expected):
    usb = identify(uri, "/dev/bus/usb/001/031")
    assert (usb and (usb.product, usb.source)) == expected
    if usb and usb.product is None:
        assert usb.usb_id == "091e:unknown"


@pytest.mark.parametrize("unix_device", ["unset", None, "", "/dev/sdb1", "/dev/bus/usb/001",
                                         "/dev/bus/usb/009/099"])
def test_fallback_when_the_volume_does_not_lead_to_a_usb_device(sysfs, unix_device):
    assert identify(NUMERIC, unix_device) == gio_storage.GarminUsb("51b4", "uri")
    assert identify(NAMED, unix_device) == gio_storage.GarminUsb(None, "uri")
    assert identify(PHONE, unix_device) is None


def test_unreadable_sysfs_entries_are_skipped(sysfs):
    (sysfs / "1-8" / "idProduct").unlink()
    assert identify(NUMERIC, "/dev/bus/usb/001/031") == gio_storage.GarminUsb("51b4", "uri")


# --- callers ---------------------------------------------------------------------------------


def test_find_devices(sysfs, monkeypatch):
    edge = FakeVolume(NAMED, "/dev/bus/usb/001/028", "Edge 1050")
    fenix = FakeVolume(NUMERIC, "/dev/bus/usb/001/031", "091e 51b4")
    phone = FakeVolume(PHONE, "/dev/bus/usb/002/007", "Pixel 8")
    monitor = FakeMonitor(mounts=[FakeMount(NAMED), FakeMount(PHONE)], volumes=[edge, fenix, phone])
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get", staticmethod(lambda: monitor))
    found = {d.uri: d for d in gio_storage.find_devices()}
    assert set(found) == {NAMED, NUMERIC}
    assert found[NAMED].mounted and found[NAMED].usb.usb_id == "091e:5158"
    assert not found[NUMERIC].mounted and found[NUMERIC].volume is fenix
    assert found[NUMERIC].usb.usb_id == "091e:51b4"


def test_open_device_storage_records_the_usb_id(sysfs, monkeypatch):
    edge = FakeVolume(NAMED, "/dev/bus/usb/001/028", "Edge 1050")
    monitor = FakeMonitor(mounts=[FakeMount(NAMED)], volumes=[edge])
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get", staticmethod(lambda: monitor))

    class Storage:
        def __init__(self, uri):
            self.label, self.usb_id = uri, None

        def _list(self, path):
            return [types.SimpleNamespace(name="Internal Storage", is_dir=True)]

        def _f(self, name):
            return FakeFile(self.label + name)

        def exists(self, path):
            return self.label.endswith("Internal Storage")

    monkeypatch.setattr(gio_storage, "GioStorage", Storage)
    for uri in (None, NAMED):  # found automatically, or given with --device-uri
        st = gio_storage.open_device_storage(uri)
        assert st.label == NAMED + "Internal Storage" and st.usb_id == "091e:5158"
    st = gio_storage.open_device_storage("mtp://[usb:001,031]/")
    assert st.usb_id == "091e:51b4"
    assert gio_storage.open_device_storage(PHONE).usb_id is None


def test_no_device_found_when_only_a_phone_is_connected(sysfs, monkeypatch):
    monitor = FakeMonitor(mounts=[FakeMount(PHONE)],
                          volumes=[FakeVolume(PHONE, "/dev/bus/usb/002/007")])
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get", staticmethod(lambda: monitor))
    assert gio_storage.find_devices() == []
    with pytest.raises(gio_storage.DeviceError, match="No Garmin device"):
        gio_storage.open_device_storage()


def test_window_notices_the_removal_of_the_open_device(no_sysfs):
    from openexpress.gui.app import MainWindow

    shown = []

    def window(label):
        session = types.SimpleNamespace(storage=types.SimpleNamespace(label=label))
        return types.SimpleNamespace(session=session, show_device=lambda: shown.append(1))

    # A made-up address that neither sysfs (the device is gone) nor the fallback recognises.
    win = window("mtp://[usb:001,028]/Internal%20Storage")
    MainWindow._on_mount_removed(win, None, FakeMount("mtp://[usb:001,028]/"))
    assert win.session is None and shown == [1]

    win = window(NAMED + "Internal%20Storage")
    MainWindow._on_mount_removed(win, None, FakeMount(PHONE))
    assert win.session is not None and shown == [1]  # someone else's device: nothing happens
    MainWindow._on_mount_removed(win, None, FakeMount(NAMED))
    assert win.session is None and shown == [1, 1]


def test_watcher_reacts_to_garmin_devices_only(sysfs, monkeypatch):
    from openexpress import watch

    started = []
    monkeypatch.setattr(watch.GLib, "timeout_add_seconds", lambda delay, fn: started.append(fn))
    monitor = FakeMonitor(volumes=[FakeVolume(NAMED, "/dev/bus/usb/001/028"),
                                   FakeVolume(PHONE, "/dev/bus/usb/002/007")])
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get", staticmethod(lambda: monitor))
    w = watch.Watcher()
    w.on_mount(None, FakeMount(PHONE))
    assert started == []
    w.on_mount(None, FakeMount(NAMED))
    assert started == [w.start_sync]


# --- the device to ourselves for a PTP session ----------------------------------------------


@pytest.fixture
def exclusive(monkeypatch):
    """A storage of a mounted Garmin device; unmounting, mounting and USB are recorded."""
    from ptp_fake import FakeDevice

    from openexpress.device import ptp

    events = []
    fake = FakeDevice()
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: False)
    monkeypatch.setattr(ptp, "load_libusb", lambda: object())
    monkeypatch.setattr(gio_storage, "_unmount", lambda uri: events.append(("unmount", uri)))
    monkeypatch.setattr(gio_storage, "_remount",
                        lambda uri, storage: events.append(("mount", uri, fake.session_open)))
    monkeypatch.setattr(ptp.LibusbTransport, "open",
                        classmethod(lambda cls, bus, address: events.append(("usb", bus, address))
                                    or fake))
    gio_storage._quiet_until.clear()
    st = gio_storage.GioStorage(NUMERIC + "Internal")
    st.usb, st.device_uri = gio_storage.GarminUsb("51b4", "usb", 3, 14), NUMERIC
    yield st, fake, events
    gio_storage._quiet_until.clear()


def test_usb_position_does_not_change_what_the_device_is():
    assert gio_storage.GarminUsb("51b4", "usb", 3, 14) == gio_storage.GarminUsb("51b4", "usb")


def test_ptp_session_unmounts_and_mounts_again(exclusive):
    st, fake, events = exclusive
    with st.exclusive_ptp() as session:
        assert events == [("unmount", NUMERIC), ("usb", 3, 14)] and fake.session_open
        assert session.storages()
        assert gio_storage.own_remount(FakeMount(NUMERIC))
    # The session is closed and the USB device released before the device is mounted again.
    assert events[-1] == ("mount", NUMERIC, False) and fake.closed
    assert gio_storage.own_remount(FakeMount(NUMERIC))  # its signals arrive a moment later
    assert not gio_storage.own_remount(FakeMount(NAMED))


def test_device_is_mounted_again_after_an_error(exclusive):
    st, fake, events = exclusive
    with pytest.raises(RuntimeError), st.exclusive_ptp():
        raise RuntimeError("boom")
    assert [e[0] for e in events] == ["unmount", "usb", "mount"] and not fake.session_open


def test_device_is_mounted_again_when_usb_cannot_be_opened(exclusive, monkeypatch):
    from openexpress.device import ptp

    st, _fake, events = exclusive

    def no_access(cls, bus, address):
        raise ptp.UsbUnavailable("no access")

    monkeypatch.setattr(ptp.LibusbTransport, "open", classmethod(no_access))
    with pytest.raises(ptp.UsbUnavailable), st.exclusive_ptp():
        pytest.fail("the block must not run")
    assert [e[0] for e in events] == ["unmount", "mount"]


def test_failed_unmount_opens_nothing(exclusive, monkeypatch):
    from openexpress.device.storage import DeviceError

    st, _fake, events = exclusive

    def busy(uri):
        raise DeviceError("busy")

    monkeypatch.setattr(gio_storage, "_unmount", busy)
    with pytest.raises(DeviceError, match="busy"), st.exclusive_ptp():
        pytest.fail("the block must not run")
    assert events == []  # still mounted: no USB access and no second mount


def test_quiet_time_ends(exclusive, monkeypatch):
    st, _fake, _events = exclusive
    with st.exclusive_ptp():
        pass
    now = gio_storage.time.monotonic()
    monkeypatch.setattr(gio_storage.time, "monotonic",
                        lambda: now + gio_storage.REMOUNT_QUIET_S + 1)
    assert not gio_storage.own_remount(FakeMount(NUMERIC))  # a real replug is seen again


@pytest.mark.parametrize("change", ["flatpak", "no-address", "no-libusb"])
def test_no_ptp_session_where_it_cannot_work(exclusive, monkeypatch, change):
    from openexpress.device import ptp

    st, _fake, _events = exclusive
    if change == "flatpak":
        monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    elif change == "no-address":
        st.usb = gio_storage.GarminUsb("51b4", "uri")

    def missing():
        raise ptp.UsbUnavailable("no libusb")

    if change == "no-libusb":
        monkeypatch.setattr(ptp, "load_libusb", missing)
    assert st.exclusive_ptp() is None


def test_window_and_watcher_ignore_our_own_remount(exclusive, monkeypatch):
    from openexpress import watch
    from openexpress.gui.app import MainWindow

    st, _fake, _events = exclusive
    shown, started = [], []
    session = types.SimpleNamespace(storage=types.SimpleNamespace(label=NUMERIC + "Internal"))
    win = types.SimpleNamespace(session=session, show_device=lambda: shown.append(1),
                                refresh_device=lambda: shown.append(2))
    monkeypatch.setattr(watch.GLib, "timeout_add_seconds", lambda delay, fn: started.append(fn))
    w = watch.Watcher()
    with st.exclusive_ptp():
        MainWindow._on_mount_removed(win, None, FakeMount(NUMERIC))
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    w.on_mount(None, FakeMount(NUMERIC))
    assert win.session is session and shown == [] and started == []  # the device stays open
