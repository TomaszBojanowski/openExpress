"""Garmin devices are recognised by their USB vendor ID, whatever the MTP address looks like.

All serial numbers and USB trees here are made up.
"""

import logging
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


def test_window_logs_mount_events_without_the_serial_number(monkeypatch, caplog):
    from openexpress import applog
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    garmin = {NUMERIC}
    remounted = set()
    timers = []
    monkeypatch.setattr(app, "garmin_usb", lambda m: m.uri in garmin)
    monkeypatch.setattr(app, "own_remount", lambda m: m.uri in remounted)
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: timers.append(ms))
    session = types.SimpleNamespace(storage=types.SimpleNamespace(label=NUMERIC + "Internal"))
    win = types.SimpleNamespace(session=session, busy=False, show_device=lambda: None)
    stick = "file:///run/media/someone/STICK/"

    with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
        MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
        MainWindow._on_mount_added(win, None, FakeMount(stick))
        remounted.add(NUMERIC)
        MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
        MainWindow._on_mount_removed(win, None, FakeMount(NUMERIC))
        remounted.clear()
        MainWindow._on_mount_removed(win, None, FakeMount(PHONE))
        MainWindow._on_mount_removed(win, None, FakeMount(NUMERIC))
    assert timers == [1500]  # only the first mount of a Garmin device leads to a detection
    # As the log file writes them: through the filter.
    lines = [applog.redact(r.getMessage()) for r in caplog.records]
    assert lines == [
        ("mount added (mtp://<device>/): a Garmin device, detecting in 1.5 s "
         "(a device is open: True)"),
        "mount added (file:): not a Garmin device, ignored",
        "mount added (mtp://<device>/): our own remount, ignored",
        "mount removed (mtp://<device>/): our own remount, ignored",
        "mount removed (mtp://<device>/): not the open device, ignored",
        ("mount removed (mtp://<device>/): the open device, showing no device "
         "(a task is running: False)"),
    ]
    # A local path is not written at all: the filter would not hide the user name in it.
    assert "someone" not in caplog.text and "STICK" not in caplog.text
    assert win.session is None


def test_window_logs_what_it_decides_when_it_detects_a_device(caplog):
    from openexpress import applog
    from openexpress.gui.app import MainWindow

    def device(unit_id):
        return types.SimpleNamespace(device=types.SimpleNamespace(unit_id=unit_id))

    tasks, shown = [], []
    open_one = device(3000000001)
    win = types.SimpleNamespace(
        busy=True, _refresh_pending=False, session=open_one,
        sections={"watch": types.SimpleNamespace(show=lambda state: None)},
        run_task=lambda title, fn, done, failed: tasks.append(done),
        show_device=lambda: shown.append(win.session))
    with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
        MainWindow.refresh_device(win)  # a task is running: later
        win.busy = False
        MainWindow.refresh_device(win)
        tasks.pop()(device(3000000001))
        MainWindow.refresh_device(win)
        tasks.pop()(device(3000000002))
        win.session = None
        MainWindow.refresh_device(win)
        tasks.pop()(device(3000000001))
    assert len(shown) == 3 and win._refresh_pending is False
    lines = [applog.redact(r.getMessage()) for r in caplog.records]
    assert lines == [
        "device detection: after the running task",
        "device detection: again, a device is open",
        "device detection: the same device as the open one, all pages are loaded anew",
        "device detection: again, a device is open",
        "device detection: another device than the open one, all pages are loaded anew",
        "device detection: no device is open",
        "device detection: a device found, all pages are loaded anew",
    ]
    assert "3000000001" not in caplog.text  # the unit ID is compared, never written


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


# --- a device that is plugged in but not there yet ----------------------------------------


class Clock:
    """Time that only moves when the code sleeps."""

    def __init__(self):
        self.now, self.slept = 0.0, 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.slept += 1


@pytest.fixture
def plugged(tmp_path, monkeypatch):
    """A made-up USB tree and a list of what gvfs offers, both changeable over time."""
    root = tmp_path / "usb"
    root.mkdir()
    monkeypatch.setattr(gio_storage, "SYSFS_USB", root)
    usb_device(root, "usb1", 1, 1, "1d6b", "0002")  # a hub, not a Garmin device
    world = types.SimpleNamespace(root=root, clock=Clock(), offered=[], steps={}, notices=[],
                                  opened=[])

    def find():
        for at, change in sorted(world.steps.items()):
            if world.clock.now >= at:
                change()
                del world.steps[at]
        return list(world.offered)

    monkeypatch.setattr(gio_storage, "find_devices", find)
    monkeypatch.setattr(gio_storage, "mount", lambda dev, timeout_s=30: None)

    class Storage:
        def __init__(self, uri):
            self.uri, self.usb_id, self.usb, self.device_uri = uri, None, None, None
            world.opened.append(uri)

        def _list(self, real):
            return []

        def _f(self, name):
            raise AssertionError("no sub-folders here")

        def exists(self, path):
            return world.files

    world.files = True
    monkeypatch.setattr(gio_storage, "GioStorage", Storage)

    def plug(product):
        for d in root.glob("garmin*"):
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        if product:
            usb_device(root, "garmin" + product, 1, 9, "091e", product)

    def offer():
        world.offered[:] = [gio_storage.FoundDevice(NUMERIC, "mtp", True,
                                                   usb=gio_storage.GarminUsb("51b4", "usb", 1, 9))]

    world.plug, world.offer = plug, offer
    world.open = lambda wait=True: gio_storage.open_device_storage(
        wait=wait, notify=world.notices.append, sleep=world.clock.sleep, clock=world.clock)
    return world


def test_usb_states(plugged):
    assert gio_storage.usb_state() == gio_storage.ABSENT
    plugged.plug("0003")
    assert gio_storage.usb_state() == gio_storage.STARTING
    assert gio_storage.garmin_products_on_usb() == ["0003"]
    plugged.plug("51b4")
    assert gio_storage.usb_state() == gio_storage.PRESENT


def test_starting_device_is_waited_for(plugged):
    plugged.plug("0003")
    plugged.steps[19] = lambda: plugged.plug("51b4")   # it comes back as an MTP device
    plugged.steps[22] = plugged.offer                 # and gvfs offers it a little later
    st = plugged.open()
    assert st.uri == NUMERIC and 22 <= plugged.clock.now < 24
    assert plugged.notices == ["The device is starting…"]  # said once


def test_device_gvfs_has_not_offered_yet_is_waited_for(plugged):
    plugged.plug("51b4")
    plugged.steps[3] = plugged.offer
    assert plugged.open().uri == NUMERIC
    assert plugged.notices == ["The device is starting…"] and plugged.clock.now < 5


def test_files_that_appear_a_moment_after_mounting_are_waited_for(plugged):
    plugged.plug("51b4")
    plugged.offer()
    plugged.files = False
    plugged.steps[4] = lambda: setattr(plugged, "files", True)
    assert plugged.open().uri == NUMERIC and 4 <= plugged.clock.now < 6


def test_nothing_on_usb_is_an_error_at_once(plugged):
    with pytest.raises(gio_storage.DeviceError, match="No Garmin device"):
        plugged.open()
    assert plugged.clock.slept == 0 and plugged.notices == []


def test_another_usb_device_changes_nothing(plugged):
    usb_device(plugged.root, "phone", 1, 5, "18d1", "0003")  # same product ID, other vendor
    with pytest.raises(gio_storage.DeviceError, match="No Garmin device"):
        plugged.open()
    assert plugged.clock.slept == 0


def test_no_wait_fails_at_once_and_says_why(plugged):
    plugged.plug("0003")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open(wait=False)
    assert str(error.value) == "The device is starting. Try again in a moment."
    assert plugged.clock.slept == 0 and plugged.notices == []
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError, match="No Garmin device"):
        plugged.open(wait=False)
    assert plugged.clock.slept == 0


def test_device_that_stays_in_its_transitional_mode(plugged):
    plugged.plug("0003")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value) == ("The device is still starting. Wait until it shows its usual "
                                "screen, or unplug it and plug it in again.")
    assert gio_storage.WAIT_FOR_DEVICE_S == 45 and 45 <= plugged.clock.now <= 46


def test_device_that_never_offers_its_files(plugged):
    plugged.plug("51b4")
    with pytest.raises(gio_storage.DeviceError) as error:
        plugged.open()
    assert str(error.value) == (
        "The Garmin device is connected, but it does not offer its files. Check its screen: "
        "if it is locked with a PIN or asks something, unlock it or confirm.")
    assert 45 <= plugged.clock.now <= 46 and plugged.opened == []


def test_device_unplugged_while_waiting(plugged):
    """Really unplugged: off the bus for longer than the short gap between its modes."""
    plugged.plug("0003")
    plugged.steps[5] = lambda: plugged.plug(None)
    with pytest.raises(gio_storage.DeviceError, match="No Garmin device"):
        plugged.open()
    assert gio_storage.USB_GAP_S == 5
    assert 5 + gio_storage.USB_GAP_S <= plugged.clock.now <= 5 + gio_storage.USB_GAP_S + 2


@pytest.mark.parametrize("gap", [1, 2, 4])
def test_short_absence_between_the_two_modes_is_not_an_unplug(plugged, gap):
    """0003 -> off the bus for a moment -> MTP: what the fēnix 8 does."""
    plugged.plug("0003")
    plugged.steps[17] = lambda: plugged.plug(None)
    plugged.steps[17 + gap] = lambda: plugged.plug("51b4")
    plugged.steps[17 + gap + 2] = plugged.offer
    st = plugged.open()
    assert st.uri == NUMERIC and plugged.notices == ["The device is starting…"]
    assert 17 + gap + 2 <= plugged.clock.now < 17 + gap + 4


def test_gap_measured_on_the_fenix_8(plugged):
    """Measured: transitional mode for about 18.5 s, off the bus for about 1.5 s, then MTP."""
    plugged.plug("0003")
    plugged.steps[18.5] = lambda: plugged.plug(None)
    plugged.steps[20.0] = lambda: plugged.plug("51b4")
    plugged.steps[22.0] = plugged.offer
    st = plugged.open()
    assert st.uri == NUMERIC and plugged.notices == ["The device is starting…"]
    assert 22 <= plugged.clock.now < 24


def test_absence_does_not_extend_the_overall_limit(plugged):
    plugged.plug("0003")
    plugged.steps[43] = lambda: plugged.plug(None)
    with pytest.raises(gio_storage.DeviceError, match="No Garmin device"):
        plugged.open()
    assert 45 <= plugged.clock.now <= 46


def test_second_short_absence_is_tolerated_too(plugged):
    plugged.plug("0003")
    plugged.steps[5] = lambda: plugged.plug(None)
    plugged.steps[7] = lambda: plugged.plug("0003")
    plugged.steps[12] = lambda: plugged.plug(None)
    plugged.steps[14] = lambda: plugged.plug("51b4")
    plugged.steps[15] = plugged.offer
    assert plugged.open().uri == NUMERIC


def test_several_devices_are_not_waited_for(plugged):
    plugged.plug("51b4")
    plugged.offer()
    plugged.offered.append(gio_storage.FoundDevice(NAMED, "mtp", True))
    with pytest.raises(gio_storage.DeviceError, match="Several devices"):
        plugged.open()
    assert plugged.clock.slept == 0


def test_cli_waits_by_default_and_has_no_wait(plugged, monkeypatch, capsys):
    from openexpress import cli

    calls = []
    monkeypatch.setattr(gio_storage, "open_device_storage",
                        lambda uri=None, wait=False, notify=None: calls.append((uri, wait))
                        or notify("The device is starting…") or "storage")
    parser_args = types.SimpleNamespace(path=None, device_uri=None, no_wait=False)
    assert cli.open_storage(parser_args) == "storage"
    parser_args.no_wait = True
    cli.open_storage(parser_args)
    assert calls == [(None, True), (None, False)]
    captured = capsys.readouterr()
    assert captured.out == "" and "The device is starting…" in captured.err  # not in the data


def test_window_shows_a_starting_device_instead_of_an_empty_page(plugged, monkeypatch):
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    shown, refreshed, updates = [], [], []
    section = types.SimpleNamespace(set_empty=lambda *notice: shown.append(notice))
    win = types.SimpleNamespace(session=None, busy=False, sections={"watch": section},
                                _usb_state=gio_storage.ABSENT, _usb_since=0.0, _usb_title="",
                                _usb_notice=MainWindow._usb_notice,
                                refresh_device=lambda: refreshed.append(1))
    # The Sync button is told whenever the words on the pages change, and only then.
    win._update_actions = lambda: updates.append(win._usb_title)
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: fn())
    clock = plugged.clock
    poll = lambda: MainWindow._poll_usb(win, clock=clock)  # noqa: E731

    assert poll() is True and shown[-1][0] == "Connect your device" and shown[-1][2] is False
    assert updates == ["Connect your device"]
    poll()
    assert updates == ["Connect your device"]  # nothing changed: the button is left alone
    plugged.plug("0003")
    clock.now = 10
    poll()
    assert shown[-1] == ("The device is starting…",
                         "This takes a moment after the cable is plugged in.", True)
    assert refreshed == []
    assert updates[-1] == win._usb_title == "The device is starting…" and len(updates) == 2
    clock.now = 29
    plugged.plug("51b4")
    poll()
    assert refreshed == [1] and shown[-1][2] is True  # looked for once it left that mode
    poll()
    assert refreshed == [1] and len(updates) == 2  # still starting: the same words
    clock.now = 10 + 46
    poll()
    assert shown[-1] == ("The device is not ready", gio_storage.not_ready_text("present"), False)
    assert updates[-1] == "The device is not ready" and len(updates) == 3
    # Off the bus for a moment: the page stays; only a longer absence is an unplug.
    plugged.plug(None)
    poll()
    assert shown[-1][0] == "The device is not ready"
    clock.now += 2
    poll()
    assert shown[-1][0] == "The device is not ready"
    plugged.plug("51b4")
    poll()
    assert shown[-1][0] == "The device is not ready" and refreshed == [1]
    plugged.plug(None)
    poll()
    clock.now += gio_storage.USB_GAP_S + 1
    poll()
    assert shown[-1][0] == "Connect your device"
    assert updates == ["Connect your device", "The device is starting…",
                       "The device is not ready", "Connect your device"]
    # With a device open, or while a task runs, the page is left alone.
    count = len(shown)
    win.session = object()
    plugged.plug("0003")
    poll()
    assert len(shown) == count and len(updates) == 4


class _SyncButton:
    """Stands in for a Sync button: its style classes and its tooltip."""

    def __init__(self):
        self.classes, self.tooltip = {"suggested-action"}, "unset"

    def add_css_class(self, name):
        self.classes.add(name)

    def remove_css_class(self, name):
        self.classes.discard(name)

    def set_tooltip_text(self, text):
        self.tooltip = text


def _sync_window(session=None, busy=False):
    from openexpress.gui.app import MainWindow

    enabled = {}
    action = lambda name: types.SimpleNamespace(  # noqa: E731
        set_enabled=lambda value: enabled.__setitem__(name, value))
    win = types.SimpleNamespace(session=session, busy=busy, lookup_action=action,
                                _usb_title="", enabled=enabled, sync_button=_SyncButton(),
                                sync_icon_button=_SyncButton(),
                                _ownership_title=MainWindow._ownership_title)
    win._sync_blocked = types.MethodType(MainWindow._sync_blocked, win)
    win.update = types.MethodType(MainWindow._update_actions, win)
    return win


def _looks(win):
    """(sync action enabled, blue, tooltip of the button with a label, tooltip of the icon)."""
    blue = ["suggested-action" in b.classes for b in (win.sync_button, win.sync_icon_button)]
    assert blue[0] == blue[1]
    return win.enabled["sync"], blue[0], win.sync_button.tooltip, win.sync_icon_button.tooltip


def test_sync_is_only_enabled_with_an_open_device():
    """No device, a starting device, a device of another account, a running task: no Sync,
    and the button is plain grey and says why."""
    from openexpress.connect import NotAccountDevice, OwnershipError

    session = types.SimpleNamespace(logged_in=True, ownership_error=None)
    win = _sync_window()

    win.update()                             # "connect your device" and "the device is starting"
    assert win.enabled == {"sync": False, "refresh": True}
    assert _looks(win) == (False, False, "Connect your device", "Connect your device")

    win.session = session
    win.update()
    assert _looks(win) == (True, True, None, "Sync")  # ready: blue again, the usual tooltip

    win.busy = True                          # a task is running
    win.update()
    assert win.enabled == {"sync": False, "refresh": False}
    assert _looks(win)[1:] == (False, "Another operation is in progress",
                               "Another operation is in progress")
    win.busy = False
    win.update()
    assert _looks(win) == (True, True, None, "Sync")

    for change, why in (
            ({"logged_in": False}, "Not logged in to Garmin Connect"),
            ({"ownership_error": NotAccountDevice("x")},
             "This device is not registered to your Garmin account"),
            ({"ownership_error": OwnershipError("x")},
             "Could not check whether this device belongs to your Garmin account")):
        win.session = types.SimpleNamespace(**{**vars(session), **change})
        win.update()
        assert _looks(win) == (False, False, why, why)

    win.busy = True                          # the device comes first: that is what to fix
    win.update()
    assert _looks(win)[2].startswith("Could not check")
    win.busy = False

    win.session = session                    # another device, on the account
    win.update()
    assert _looks(win) == (True, True, None, "Sync")
    win.session = None                       # unplugged again
    win.update()
    assert _looks(win) == (False, False, "Connect your device", "Connect your device")


def test_sync_tooltip_uses_the_words_of_the_banner():
    """The reason on the button and the banner above the page cannot drift apart."""
    from openexpress.connect import NotAccountDevice, OwnershipError
    from openexpress.gui.app import MainWindow

    def banner_title(s):
        titles = []
        banner = types.SimpleNamespace(set_title=titles.append, set_button_label=lambda b: None,
                                       set_revealed=lambda r: None)
        MainWindow._update_banner(types.SimpleNamespace(
            session=s, banner=banner, _ownership_title=MainWindow._ownership_title))
        return titles

    client = types.SimpleNamespace(consumer=object())
    for logged_in, error in ((True, NotAccountDevice("x")), (True, OwnershipError("x")),
                             (False, None)):
        s = types.SimpleNamespace(logged_in=logged_in, ownership_error=error, client=client)
        win = _sync_window(session=s)
        win.update()
        assert banner_title(s) == [win.sync_button.tooltip]


def test_sync_tooltip_says_what_the_pages_say_while_no_device_is_open():
    """No device, a device that is starting, a device that is not ready: the button has the
    title the pages show, not always "Connect your device"."""
    from openexpress.gui.app import MainWindow

    win = _sync_window()
    win.update()
    assert _looks(win) == (False, False, "Connect your device", "Connect your device")

    for state, waited in ((gio_storage.STARTING, 5), (gio_storage.PRESENT, 5),
                          (gio_storage.PRESENT, gio_storage.WAIT_FOR_DEVICE_S + 1),
                          (gio_storage.ABSENT, 0)):
        title = MainWindow._usb_notice(state, waited)[0]  # what the middle of the window says
        win._usb_title = title
        win.update()
        assert _looks(win) == (False, False, title, title)
    assert {MainWindow._usb_notice(s, w)[0] for s, w in (
        (gio_storage.STARTING, 5), (gio_storage.PRESENT, 99), (gio_storage.ABSENT, 0))} == {
        "The device is starting…", "The device is not ready", "Connect your device"}

    # With a device open the words kept from before do not matter.
    win._usb_title = "The device is starting…"
    win.session = types.SimpleNamespace(logged_in=True, ownership_error=None)
    win.update()
    assert _looks(win) == (True, True, None, "Sync")
    win.session = types.SimpleNamespace(logged_in=False, ownership_error=None)
    win.update()
    assert _looks(win)[2] == "Not logged in to Garmin Connect"
