"""Garmin devices are recognised by their USB vendor ID, whatever the MTP address looks like.

All serial numbers and USB trees here are made up.
"""

import hashlib
import itertools
import logging
import os
import shutil
import types
from pathlib import Path

import pytest

gio_storage = pytest.importorskip("openexpress.device.gio_storage")
# The real one, before any test puts a stand-in in its place.
REAL_FIND_DEVICES = gio_storage.find_devices

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


def test_a_device_still_at_its_place_on_usb(sysfs):
    still = gio_storage.usb_still_there
    assert still(gio_storage.GarminUsb("51b4", "usb", 1, 31))
    assert still(gio_storage.GarminUsb(None, "usb", 1, 31))  # product unknown: the place decides
    assert not still(gio_storage.GarminUsb("51b4", "usb", 1, 32))  # plugged in again: new number
    assert not still(gio_storage.GarminUsb("5158", "usb", 1, 31))  # another product there now
    assert not still(gio_storage.GarminUsb("4ee1", "usb", 2, 7))  # not a Garmin device
    assert not still(gio_storage.GarminUsb("51b4", "uri"))  # place not known
    assert not still(None)
    for name in ("busnum", "devnum", "idVendor", "idProduct"):  # unplugged: gone from /sys
        (sysfs / "1-8" / name).unlink()
    assert not still(gio_storage.GarminUsb("51b4", "usb", 1, 31))


def test_no_device_is_still_there_without_sysfs(no_sysfs):
    assert not gio_storage.usb_still_there(gio_storage.GarminUsb("51b4", "usb", 1, 31))


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


class Page:
    """A page of the window: what it shows, and the words of its empty placeholder."""

    def __init__(self):
        self.loaded, self.state, self.notices = True, "content", []

    def clear(self):
        pass

    def show(self, state):
        self.state = state

    def set_empty(self, title, description, busy):
        self.notices.append(title)


def unplug_window(monkeypatch, usb):
    """A window with the real code for an unplug, the pages and the notice from USB.
    `usb` is what /sys says when it is asked; nothing else of the window is real."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(app, "usb_state", lambda: usb[0])
    monkeypatch.setattr(app, "own_remount", lambda mount: False)
    monkeypatch.setattr(app, "garmin_usb", lambda mount: True)
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: pytest.fail("no timer here"))
    pages = {"watch": Page(), "apps": Page()}
    session = types.SimpleNamespace(storage=types.SimpleNamespace(label=NUMERIC + "Internal"))
    win = types.SimpleNamespace(
        session=session, busy=False, sections=pages, built=[], _updated_apps=set(),
        _usb_state=gio_storage.ABSENT, _usb_since=0.0, _usb_title="", _usb_gone=None,
        _usb_notice=MainWindow._usb_notice, _update_banner=lambda: None,
        _update_actions=lambda: None, start=FakeStartPage(), _show_start=lambda: None)
    win._poll_usb = types.MethodType(MainWindow._poll_usb, win)
    real_show = types.MethodType(MainWindow.show_device, win)
    win.show_device = lambda: (win.built.append(win.session), real_show())
    # Opened while the device was starting: the last words the pages were given.
    for page in pages.values():
        page.set_empty("The device is starting…", "", True)
    return win


def test_after_an_unplug_the_pages_tell_the_usb_state_at_once(monkeypatch):
    from openexpress.gui.app import MainWindow

    win = unplug_window(monkeypatch, [gio_storage.ABSENT])
    connect = MainWindow._usb_notice(gio_storage.ABSENT, 0)[0]  # what no device on USB says
    assert connect == "Connect your device"
    MainWindow._on_mount_removed(win, None, FakeMount(NUMERIC))
    assert win.session is None
    for page in win.sections.values():  # before any later poll of USB
        assert page.state == "empty"
        assert page.notices[-1] == connect, "the words from before the device was opened"


def test_a_device_still_starting_is_told_at_once_too(monkeypatch):
    from openexpress.gui.app import MainWindow

    win = unplug_window(monkeypatch, [gio_storage.STARTING])
    MainWindow._on_mount_removed(win, None, FakeMount(NUMERIC))
    assert all(page.notices[-1] == "The device is starting…"
               for page in win.sections.values())


def test_the_second_mount_of_an_unplugged_device_changes_nothing(monkeypatch, caplog):
    from openexpress import applog
    from openexpress.gui.app import MainWindow

    win = unplug_window(monkeypatch, [gio_storage.ABSENT])
    with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
        MainWindow._on_mount_removed(win, None, FakeMount(NUMERIC))
        MainWindow._on_mount_removed(win, None, FakeMount(NUMERIC))  # gvfs: the other mount
    assert len(win.built) == 1 and win.session is None
    lines = [applog.redact(r.getMessage()) for r in caplog.records]
    assert lines == [
        ("mount removed (mtp://<device>/): the open device, showing no device "
         "(a task is running: False)"),
        "mount removed (mtp://<device>/): a Garmin device, no device is open, nothing to do",
    ]


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



# --- one detection for the mount events of the device that is open -----------------------


class Timers:
    """GLib timers that fire only when the test says so."""

    def __init__(self):
        self.live, self.removed, self.last = {}, [], 0

    def add(self, ms, fn):
        self.last += 1
        self.live[self.last] = fn
        return self.last

    def remove(self, sid):
        self.removed.append(sid)
        del self.live[sid]

    def fire(self):
        for sid, fn in list(self.live.items()):
            del self.live[sid]
            fn()


@pytest.fixture
def detecting(tmp_path, monkeypatch):
    """A window with the real detection code. Its tasks wait until the test finishes them;
    each started task is one `open_session`, and each `show_device` builds every page anew.
    The open device is on USB at bus 1, device 40 (a made-up /sys)."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    usb_device(tmp_path / "sys", "1-4", 1, 40, "091e", "51b4")
    monkeypatch.setattr(gio_storage, "SYSFS_USB", tmp_path / "sys")
    timers = Timers()
    monkeypatch.setattr(app.GLib, "timeout_add", timers.add)
    monkeypatch.setattr(app.GLib, "source_remove", timers.remove)
    monkeypatch.setattr(app, "garmin_usb", lambda m: True)
    monkeypatch.setattr(app, "own_remount", lambda m: False)
    win = types.SimpleNamespace(busy=False, session=None, _refresh_pending=False, tasks=[],
                                built=[], reads=[],
                                sections={"watch": types.SimpleNamespace(show=lambda s: None)})

    def run_task(title, fn, done, failed):
        win.busy = True
        win.tasks.append(done)

    def in_background(fn, done, failed):
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - as the real one: the error goes to `failed`
            failed(e)
        else:
            done(result)

    def finish(session):
        win.busy = False
        win.tasks[-1](session)
        MainWindow._run_pending_refresh(win)

    win.run_task, win.in_background, win.finish = run_task, in_background, finish
    win.show_device = lambda: win.built.append(win.session)
    win.refresh_device = types.MethodType(MainWindow.refresh_device, win)
    win.timers = timers
    return win


def opened(win, mount_uri, unit_id=3000000001, answers=True):
    """A session on the storage of this mount, as `open_session` makes it."""

    def read(path):
        win.reads.append(path)
        if not answers:
            raise gio_storage.DeviceError("gone")
        return b"<Device/>"

    storage = types.SimpleNamespace(label=mount_uri + "Internal%20Storage", read=read,
                                    usb=gio_storage.GarminUsb("51b4", "usb", 1, 40))
    return types.SimpleNamespace(storage=storage,
                                 device=types.SimpleNamespace(unit_id=unit_id))


def test_a_mount_event_during_the_first_detection_does_not_load_the_pages_again(
        detecting, caplog):
    from openexpress.gui.app import MainWindow

    win = detecting
    with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
        win.refresh_device()  # at start
        # gvfs reports the mount that is being opened; the timer fires before the end.
        MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
        win.timers.fire()
        assert len(win.tasks) == 1 and win.built == []  # decided after the running task
        win.finish(opened(win, NUMERIC))
        # The timer fires after the end of the first detection: the same.
        MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
        win.timers.fire()
    assert len(win.tasks) == 1 and len(win.built) == 1  # one open_session, pages built once
    assert win.reads == ["GARMIN/GarminDevice.xml"] * 2
    assert caplog.text.count("the open device is still there, nothing to do") == 2


def test_a_mount_with_another_address_is_detected_as_before(detecting):
    from openexpress.gui.app import MainWindow

    win = detecting
    win.session = opened(win, NUMERIC)
    MainWindow._on_mount_added(win, None, FakeMount(NAMED))
    win.timers.fire()
    assert len(win.tasks) == 1 and win.reads == []
    win.finish(opened(win, NAMED, unit_id=3000000002))
    assert len(win.built) == 1


def test_no_open_device_is_detected_as_before(detecting):
    from openexpress.gui.app import MainWindow

    win = detecting
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    win.timers.fire()
    assert len(win.tasks) == 1 and win.reads == []


def test_an_open_device_that_does_not_answer_is_detected_as_before(detecting):
    from openexpress.gui.app import MainWindow

    win = detecting
    win.session = opened(win, NUMERIC, answers=False)
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    win.timers.fire()
    assert win.reads == ["GARMIN/GarminDevice.xml"] and len(win.tasks) == 1


def test_the_same_address_at_a_new_place_on_usb_is_detected_again(detecting, tmp_path, caplog):
    import shutil

    from openexpress.gui.app import MainWindow

    win = detecting
    win.session = opened(win, NUMERIC)
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    win.timers.fire()
    assert win.reads == ["GARMIN/GarminDevice.xml"] and win.tasks == []  # the same place
    # Unplugged and plugged in again: the address carries the serial number, so it is the
    # same, but the device number on USB is new.
    shutil.rmtree(tmp_path / "sys" / "1-4")
    usb_device(tmp_path / "sys", "1-4", 1, 41, "091e", "51b4")
    with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
        MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
        win.timers.fire()
    assert len(win.tasks) == 1 and len(win.reads) == 1  # detected anew, nothing read
    assert "the same address, but not at the same place on USB" in caplog.text


def test_an_open_device_whose_place_on_usb_is_not_known_is_kept_by_its_address(detecting):
    from openexpress.gui.app import MainWindow

    win = detecting
    win.session = opened(win, NUMERIC)
    win.session.storage.usb = gio_storage.GarminUsb("51b4", "uri")  # told by its address only
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    win.timers.fire()
    assert win.reads == ["GARMIN/GarminDevice.xml"] and win.tasks == []


def test_several_triggers_lead_to_one_detection(detecting):
    from openexpress.gui.app import MainWindow, schedule_detection

    win = detecting
    win.session = opened(win, NUMERIC)
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    assert win.timers.removed == [1] and list(win.timers.live) == [2]  # the same one, moved
    win.timers.fire()
    assert win.reads == ["GARMIN/GarminDevice.xml"] and win.tasks == []
    # A mount of the open device and the device seen on USB: one detection covers both.
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    schedule_detection(win, 2500)
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    assert len(win.timers.live) == 1
    win.timers.fire()
    assert len(win.tasks) == 1 and len(win.reads) == 1


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


# --- a Garmin device in mass storage mode: a USB drive -------------------------------------

# What the window says about an open Garmin drive: on its device page and on the pages that
# do not apply to it.
DRIVE_NOT_MOUNTED_TEXT = ("The Garmin device is connected, but its drive is not mounted. Open "
                          "it in the file manager, or unplug the cable and plug it in again.")
FLATPAK_DRIVE_TEXT = ("The Garmin device is connected as a USB drive, but this version "
                      "(Flatpak) has no access to /run/media, where the system mounted it.")
MASS_STORAGE_TEXT = ("The device is connected in mass storage mode (as a USB drive): "
                     "openExpress shows its information and software updates; sync, apps, "
                     "maps and music do not apply to it.")
# Made by hand after the Varia RTL515 (17.10.2026); the unit ID is made up.
RTL515_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
 <Model><PartNumber>006-B3592-03</PartNumber><SoftwareVersion>336</SoftwareVersion>
  <Description>Varia RTL515</Description></Model>
 <Id>3000000003</Id>
 <MassStorageMode>
  <UpdateFile><PartNumber>006-B3592-03</PartNumber>
   <Version><Major>3</Major><Minor>36</Minor></Version>
   <Path>GARMIN/</Path><FileName>GUPDATE.GCD</FileName></UpdateFile>
  <UpdateFile><PartNumber>006-B3593-06</PartNumber>
   <Version><Major>3</Major><Minor>27</Minor></Version>
   <Path>GARMIN/REMOTESW/</Path><FileName>6B359306.GCD</FileName></UpdateFile>
 </MassStorageMode>
</Device>
"""
DRIVE_SERIAL = "0000abcdef56"
MEDIA_USER = "made-up-user"  # the user folder in /run/media/<user>
TESTDATA_XML = (Path(__file__).resolve().parent.parent / "testdata" / "varia-rtl515"
                / "GarminDevice.masked.xml")  # private, not in the repository


class FakeLocalFile(FakeFile):
    """The root of a mounted USB drive: a local folder."""

    def __init__(self, path):
        super().__init__(path.as_uri())
        self.path = str(path)

    def get_path(self):
        return self.path


class FakeDriveMount:
    """Like the mount of a USB drive: a local folder, and a volume that names the block device."""

    def __init__(self, folder, unix_device, name="GARMIN"):
        self.root, self.name = FakeLocalFile(folder), name
        self.volume = FakeVolume(None, unix_device, name) if unix_device != "no volume" else None

    def get_root(self):
        return self.root

    def get_volume(self):
        return self.volume

    def get_name(self):
        return self.name


@pytest.fixture
def drives(tmp_path, monkeypatch):
    """A made-up /sys with /sys/class/block, and a folder for what the system mounts: like
    /run/media/<user>, with a made-up user name."""
    sys_root = tmp_path / "sys-with-drives"
    world = types.SimpleNamespace(block=sys_root / "class" / "block",
                                  media=tmp_path / "run" / "media" / MEDIA_USER,
                                  pci=sys_root / "devices" / "pci0000:00" / "0000:00:14.0")
    world.block.mkdir(parents=True)
    world.media.mkdir(parents=True)
    world.hub = usb_device(world.pci, "usb1", 1, 1, "1d6b", "0002")
    monkeypatch.setattr(gio_storage, "SYSFS_BLOCK", world.block)
    return world


def _block(drives, below, disk, partition):
    """The block device (and its partition) below a device folder, linked from
    /sys/class/block as the kernel does it; returns what the volume gives as unix-device."""
    folder = below / "block" / disk
    folder.mkdir(parents=True)
    (drives.block / disk).symlink_to(os.path.relpath(folder, drives.block))
    if partition is None:
        return f"/dev/{disk}"
    (folder / partition).mkdir()
    (drives.block / partition).symlink_to(os.path.relpath(folder / partition, drives.block))
    return f"/dev/{partition}"


def usb_drive(drives, vendor="091e", product="20ee", port="1-4", devnum=12, disk="sdb",
              partition="sdb1"):
    """A USB drive as the kernel shows it: the USB device, its mass storage interface and the
    SCSI disk below it. Its serial number is there but must never be read."""
    usb = usb_device(drives.hub, port, 1, devnum, vendor, product, serial=DRIVE_SERIAL)
    interface = usb / f"{port}:1.0"
    interface.mkdir()
    (interface / "bInterfaceClass").write_text("08\n")
    return _block(drives, interface / "host6" / "target6:0:0" / "6:0:0:0", disk, partition)


def drive_folder(drives, label="GARMIN", xml_path="GARMIN/GarminDevice.xml"):
    """The folder where the system mounted a drive, with the device file when `xml_path`."""
    folder = drives.media / label
    # One GARMIN folder in whatever letter case: a FAT drive cannot have two.
    (folder / (xml_path or "GARMIN/-")).parent.mkdir(parents=True)
    if xml_path:
        (folder / xml_path).write_text(RTL515_XML)
    return folder


def monitor_with(monkeypatch, *mounts):
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get",
                        staticmethod(lambda: FakeMonitor(mounts=mounts)))


@pytest.mark.parametrize("xml_path, partition", [
    ("GARMIN/GarminDevice.xml", "sdb1"),
    ("Garmin/garmindevice.xml", "sdb1"),   # any letter case, as on the device
    ("GARMIN/GarminDevice.xml", None),     # a drive without partitions
])
def test_a_garmin_drive_is_recognised(drives, monkeypatch, xml_path, partition):
    """A volume whose block device belongs to the USB device 091e:20ee and that has
    GARMIN/GarminDevice.xml: a Garmin device in mass storage mode."""
    mount = FakeDriveMount(drive_folder(drives, xml_path=xml_path),
                           usb_drive(drives, partition=partition))
    usb = gio_storage.garmin_ums(mount)
    assert usb == gio_storage.GarminUsb("20ee", "usb") and usb.usb_id == "091e:20ee"
    assert (usb.bus, usb.address) == (1, 12)
    monitor_with(monkeypatch, mount)
    found = gio_storage.find_ums_devices()
    assert [(d.uri, d.name, d.mounted, d.usb) for d in found] == [
        (mount.root.get_uri(), "GARMIN", True, usb)]
    assert found[0].volume is mount.volume
    assert DRIVE_SERIAL not in repr(found)  # the serial number is never read


def test_a_pendrive_with_a_garmin_folder_is_ignored(drives, monkeypatch):
    """The same folders and file on a drive of another vendor (here a SanDisk pendrive)."""
    mount = FakeDriveMount(drive_folder(drives), usb_drive(drives, vendor="0781",
                                                             product="5581"))
    assert gio_storage.garmin_ums(mount) is None
    monitor_with(monkeypatch, mount)
    assert gio_storage.find_ums_devices() == []


def test_a_garmin_drive_without_the_device_file_is_ignored(drives, monkeypatch, caplog):
    """E.g. the memory card of a Garmin device: vendor 091e, a GARMIN folder, no device file."""
    folder = drive_folder(drives, label="CARD", xml_path=None)
    mount = FakeDriveMount(folder, usb_drive(drives))
    assert gio_storage.garmin_ums(mount) is not None  # a Garmin drive…
    monitor_with(monkeypatch, mount)
    with caplog.at_level(logging.INFO, logger=gio_storage.log.name):
        assert gio_storage.find_ums_devices() == []  # …but not a device we can use
    assert "without GARMIN/GarminDevice.xml" in caplog.text and "091e:20ee" in caplog.text
    assert str(drives.media) not in caplog.text  # no local path (it has the user name)


@pytest.mark.parametrize("case", ["no volume", "no unix-device", "not in /sys", "nvme",
                                  "not a block device", "outside /sys", "mtp mount"])
def test_a_drive_of_unknown_usb_origin_is_ignored(drives, monkeypatch, case):
    """Only the USB device the block device belongs to decides: no guess by the label
    "GARMIN" or by the folders on the drive."""
    folder = drive_folder(drives)
    usb_drive(drives)  # a Garmin drive is plugged in: none of these leads to it
    nvme = _block(drives, drives.pci / "0000:04:00.0" / "nvme" / "nvme0", "nvme0n1", "nvme0n1p1")
    mount = {
        "no volume": FakeDriveMount(folder, "no volume"),
        "no unix-device": FakeDriveMount(folder, None),
        "not in /sys": FakeDriveMount(folder, "/dev/sdz1"),
        "nvme": FakeDriveMount(folder, nvme),
        "not a block device": FakeDriveMount(folder, "/dev/bus/usb/001/012"),
        "outside /sys": FakeDriveMount(folder, "/dev/../../sys/class/block/sdb1"),
        "mtp mount": FakeMount(NUMERIC),
    }[case]
    assert gio_storage.garmin_ums(mount) is None
    monitor_with(monkeypatch, mount)
    assert gio_storage.find_ums_devices() == []


def test_mtp_detection_does_not_see_a_garmin_drive(sysfs, drives, monkeypatch):
    """_identify, garmin_usb and find_devices stay for MTP alone."""
    mount = FakeDriveMount(drive_folder(drives), usb_drive(drives))
    assert gio_storage.garmin_usb(mount) is None
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get", staticmethod(
        lambda: FakeMonitor(mounts=[mount, FakeMount(NAMED)],
                            volumes=[mount.volume, FakeVolume(NAMED, "/dev/bus/usb/001/028")])))
    assert [d.uri for d in gio_storage.find_devices()] == [NAMED]


def test_no_disk_of_the_computer_running_the_tests_is_a_garmin_drive():
    """conftest.py points /sys/class/block to an empty folder for every test."""
    assert list(gio_storage.SYSFS_BLOCK.iterdir()) == []
    assert gio_storage.garmin_ums(FakeDriveMount(Path("/"), "/dev/sda1")) is None


def _rtl515(dev):
    """What a GarminDevice.xml of the RTL515 says, without its unit ID."""
    return (dev.description, dev.part_number, dev.software_version_str, dev.datatypes(),
            dev.files, dev.update_files, dev.iq_max_apps)


def test_the_hand_made_rtl515_xml():
    from openexpress.device.xmldevice import UpdateFile, parse

    dev = parse(RTL515_XML.encode())
    assert dev.unit_id == 3000000003
    assert _rtl515(dev) == ("Varia RTL515", "006-B3592-03", "3.36", [], [], [
        UpdateFile("006-B3592-03", "3.36", "GARMIN/", "GUPDATE.GCD"),
        UpdateFile("006-B3593-06", "3.27", "GARMIN/REMOTESW/", "6B359306.GCD"),
    ], None)  # no data types: nothing to sync, no Connect IQ


@pytest.mark.skipif(not TESTDATA_XML.exists(), reason="private test data")
def test_the_hand_made_rtl515_xml_matches_the_device_file():
    from openexpress.device.xmldevice import parse

    # The unit ID is masked there ("MASKED"), which is no number.
    data = TESTDATA_XML.read_bytes().replace(b"<Id>MASKED</Id>", b"<Id>3000000003</Id>")
    assert _rtl515(parse(data)) == _rtl515(parse(RTL515_XML.encode()))


def opened_drive(drives, monkeypatch, label="GARMIN"):
    """(the mount, the storage) of a Garmin drive found and opened like the window will."""
    mount = FakeDriveMount(drive_folder(drives, label=label), usb_drive(drives), name=label)
    monitor_with(monkeypatch, mount)
    (found,) = gio_storage.find_ums_devices()
    return mount, gio_storage.open_ums_storage(found)


@pytest.mark.parametrize("label", ["GARMIN", "GARMIN 2"])
def test_a_garmin_drive_opens_as_mass_storage(drives, monkeypatch, label):
    from openexpress.device import storage
    from openexpress.device.xmldevice import parse
    from openexpress.gui.app import _on_mount

    mount, st = opened_drive(drives, monkeypatch, label)
    assert isinstance(st, storage.UmsStorage) and isinstance(st, storage.LocalStorage)
    assert st.mass_storage is True
    assert st.usb == gio_storage.GarminUsb("20ee", "usb") and st.usb_id == "091e:20ee"
    assert (st.usb.bus, st.usb.address) == (1, 12)
    # The address as Gio gives it (" " stays "%20"); the folder is the one it names.
    uri = mount.root.get_uri()
    assert st.device_uri == uri and st.label == uri + "/"
    assert st.root == drives.media / label
    assert parse(st.read("GARMIN/GarminDevice.xml")).description == "Varia RTL515"
    # The window tells by the label which mount the open device is on.
    assert _on_mount(st, uri) and _on_mount(st, uri + "/")
    assert not _on_mount(st, uri + "1") and not _on_mount(st, (drives.media / "OTHER").as_uri())


def test_only_a_garmin_drive_is_mass_storage(tmp_path):
    """MTP storages and the folder given with --path are not: --path stays as it was."""
    from openexpress import cli
    from openexpress.device import storage

    st = cli.open_storage(types.SimpleNamespace(path=str(tmp_path), device_uri=None))
    assert type(st) is storage.LocalStorage
    assert st.mass_storage is False and st.label == str(tmp_path)
    assert not any(hasattr(st, name) for name in ("usb", "usb_id", "device_uri"))
    assert storage.Storage.mass_storage is False
    assert gio_storage.GioStorage(NUMERIC).mass_storage is False


def _offered(path, filename, data):
    """One file offered by the update server, with its keys as the server sends them."""
    return {"partNumber": filename.split(".")[0], "productName": "Varia RTL515",
            "dataType": "Firmware", "displayVersion": "3.40", "severity": "Recommended",
            "releaseNotes": [],
            "deliverable": {"downloadUrl": "https://example.org/" + filename,
                            "md5": hashlib.md5(data).hexdigest().upper(), "sizeInBytes": len(data)},
            "installation": {"path": path, "fileName": filename, "isReinstall": False,
                             "isRestartRequired": False, "order": 0}}


class _UpdateServer:
    """Stands in for the HTTP session of GarminClient: records each request, answers with JSON."""

    def __init__(self, answer):
        self.answer, self.sent = answer, []

    def request(self, method, url, **kw):
        import json

        import requests

        self.sent.append((method, url, kw.get("params"), kw.get("json")))
        resp = requests.Response()
        resp.status_code, resp.url = 200, url
        resp._content = json.dumps(self.answer).encode()
        resp.headers["Content-Type"] = "application/json"
        return resp


def test_updates_of_a_garmin_drive_take_the_path_of_cli_path(drives, monkeypatch):
    """The same request and the same comparison with the files on the device, whether the
    folder is opened as a Garmin drive or given with --path."""
    from openexpress import updates
    from openexpress.connect import GarminClient
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.settings import Settings

    mount, ums = opened_drive(drives, monkeypatch)
    system = b"the RTL515 software that is on it"
    (ums.root / "GARMIN" / "GUPDATE.GCD").write_bytes(system)
    answer = {"updates": [_offered("GARMIN", "GUPDATE.GCD", system),
                          _offered("GARMIN\\REMOTESW", "6B359306.GCD", b"a newer light")]}

    def check(storage):
        server = _UpdateServer(answer)
        client = GarminClient(Settings(), None, None, server)
        device = parse(storage.read("GARMIN/GarminDevice.xml"))
        items = updates.mark_present(
            storage, updates.parse_updates(client.software_updates(device.raw, True)),
            updates.component_count(device))
        found = [(u.part_number, u.version, u.device_path, u.size, u.on_device) for u in items]
        return server.sent, found, updates.check_space(storage, items)[0]

    on_drive, with_path = check(ums), check(LocalStorage(ums.root))
    assert on_drive == with_path
    sent, found, _need = on_drive
    assert [(method, params, body["garminDeviceXml"]) for method, _url, params, body in sent] \
        == [("POST", {"filter": "all"}, RTL515_XML)]
    assert [(device_path, on_device) for _p, _v, device_path, _s, on_device in found] == [
        ("GARMIN/GUPDATE.GCD", True), ("GARMIN/REMOTESW/6B359306.GCD", False)]


def test_the_log_of_a_garmin_drive_has_no_user_name(drives, monkeypatch, caplog):
    """Only the scheme of its address goes to the log, never /run/media/<user>, also when
    it is gone between finding it and opening it."""
    no_xml = FakeDriveMount(drive_folder(drives, label="CARD", xml_path=None),
                            usb_drive(drives, port="1-5", devnum=13, disk="sdc", partition="sdc1"))
    with caplog.at_level(logging.DEBUG):
        mount, st = opened_drive(drives, monkeypatch)
        monitor_with(monkeypatch, mount, no_xml)
        (found,) = gio_storage.find_ums_devices()
        shutil.rmtree(st.root)  # unplugged
        with pytest.raises(gio_storage.NotThereYet) as error:
            gio_storage.open_ums_storage(found)
    assert "opened in mass storage mode (file:, USB 091e:20ee)" in caplog.text
    assert "without GARMIN/GarminDevice.xml" in caplog.text
    assert str(error.value) == "The device is not mounted." and error.value.__cause__ is None
    for text in (caplog.text, str(error.value)):
        assert MEDIA_USER not in text and "file://" not in text and str(drives.media) not in text


@pytest.fixture
def watch_and_drives(plugged, drives, device_root, monkeypatch):
    """The made-up USB tree and gvfs of `plugged`, the drives of `drives`; an MTP device
    offered by gvfs reads like conftest's test watch (device_root)."""
    from openexpress.device.storage import LocalStorage

    class MtpStorage(LocalStorage):
        def __init__(self, uri):
            super().__init__(device_root)
            self.label, self.usb_id, self.usb, self.device_uri = uri, None, None, None

        def _f(self, name):
            return FakeFile(self.label + name)

    monkeypatch.setattr(gio_storage, "GioStorage", MtpStorage)
    plugged.drives = drives

    def drive(label="GARMIN", port="1-4", devnum=12, disk="sdb"):
        """A Garmin drive on USB (also in /sys/bus/usb/devices) and mounted by the system."""
        mass_storage_device(plugged.root, name=port)
        return FakeDriveMount(drive_folder(drives, label=label),
                              usb_drive(drives, port=port, devnum=devnum, disk=disk,
                                        partition=disk + "1"), name=label)

    plugged.drive = drive
    return plugged


def test_a_garmin_drive_opens_only_when_asked_for(watch_and_drives, monkeypatch):
    """Without allow_ums, as before: MTP alone (the command line, the background service)."""
    from openexpress import service

    monitor_with(monkeypatch, watch_and_drives.drive())
    with pytest.raises(gio_storage.NotThereYet) as error:
        service.open_session()
    assert str(error.value) == gio_storage.not_ready_text(gio_storage.MASS_STORAGE)
    s = service.open_session(allow_ums=True)
    assert s.mass_storage and s.storage.mass_storage and s.storage.usb_id == "091e:20ee"
    assert s.device.description == "Varia RTL515" and s.device.unit_id == 3000000003


def test_mtp_comes_before_a_garmin_drive(watch_and_drives, monkeypatch, caplog):
    """D1: the MTP device opens as it does today; the drive is skipped, and the log says so."""
    from openexpress import service

    monitor_with(monkeypatch, watch_and_drives.drive())
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    with caplog.at_level(logging.INFO, logger="openexpress"):
        s = service.open_session(allow_ums=True)
    assert not s.mass_storage and s.device.description == "Test watch"
    assert "1 Garmin drive in mass storage mode skipped" in caplog.text
    assert "opened in mass storage mode" not in caplog.text


# --- the command line opens a Garmin drive as the window does (Varia, step 3) ---------------------


def test_the_command_line_opens_a_garmin_drive_without_path(watch_and_drives, monkeypatch,
                                                            capsys):
    from openexpress.cli import main

    monitor_with(monkeypatch, watch_and_drives.drive())
    assert main(["info"]) == 0
    out = capsys.readouterr().out
    assert "Varia RTL515" in out and "Data types:   0" in out


def test_the_command_line_opens_mtp_before_a_garmin_drive(watch_and_drives, monkeypatch,
                                                          capsys, caplog):
    """D1, as in the window: the MTP device opens, the drive is skipped and the log says so."""
    from openexpress.cli import main

    monitor_with(monkeypatch, watch_and_drives.drive())
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert main(["info"]) == 0
    assert "Test watch" in capsys.readouterr().out
    assert "1 Garmin drive in mass storage mode skipped" in caplog.text


def test_a_garmin_drive_not_mounted_is_told_without_error_or_path(plugged, monkeypatch,
                                                                  capsys):
    """A notice, not an error: the words of the window, without "Error:" and without advice
    about --path; still exit code 1, which scripts check."""
    from openexpress.cli import main

    monitor_with(monkeypatch)  # on USB, not mounted
    mass_storage_device(plugged.root)
    assert main(["--no-wait", "info"]) == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == DRIVE_NOT_MOUNTED_TEXT
    assert "Error" not in captured.err and "--path" not in captured.err


@pytest.mark.parametrize("command", [["sync"], ["baseline"], ["apps"], ["music"], ["maps"],
                                     ["backup"]])
def test_commands_that_do_not_apply_to_a_drive_refuse(watch_and_drives, monkeypatch, capsys,
                                                      command):
    """The refusal of the window, in one sentence, before any request or login."""
    from openexpress.cli import main

    monitor_with(monkeypatch, watch_and_drives.drive())
    assert main(command) == 1
    captured = capsys.readouterr()
    assert MASS_STORAGE_TEXT in captured.err and captured.err.count("\n") == 1


def test_sync_refuses_a_folder_without_data_types(tmp_path, capsys):
    """A copy of a Varia with --path: nothing to sync, said so, instead of trying."""
    from openexpress.cli import main

    (tmp_path / "GARMIN").mkdir()
    (tmp_path / "GARMIN" / "GarminDevice.xml").write_text(RTL515_XML)
    assert main(["--path", str(tmp_path), "sync"]) == 1
    assert "declares no data types" in capsys.readouterr().err


def test_updates_work_on_a_garmin_drive(watch_and_drives, monkeypatch, capsys):
    from openexpress import cli

    monitor_with(monkeypatch, watch_and_drives.drive())
    monkeypatch.setattr(cli, "GarminClient", lambda settings: types.SimpleNamespace(
        software_updates=lambda raw, include_reinstalls=False: {}))
    assert cli.main(["updates"]) == 0
    out = capsys.readouterr().out
    assert "Varia RTL515" in out and "Software is up to date." in out


def test_a_watch_on_its_way_comes_before_a_garmin_drive(watch_and_drives, monkeypatch, caplog):
    """A watch that is still starting is waited for (by the window), not passed over."""
    from openexpress import service

    monitor_with(monkeypatch, watch_and_drives.drive())
    watch_and_drives.plug("0003")
    with caplog.at_level(logging.INFO, logger="openexpress"), \
            pytest.raises(gio_storage.NotThereYet) as error:
        service.open_session(allow_ums=True)
    assert str(error.value) == "The device is starting. Try again in a moment."
    assert "opened in mass storage mode" not in caplog.text


def test_several_mtp_devices_next_to_a_garmin_drive(watch_and_drives, monkeypatch):
    """Two MTP devices: "Several devices" as before, with their addresses."""
    from openexpress import service

    monitor_with(monkeypatch, watch_and_drives.drive())
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    watch_and_drives.offered.append(gio_storage.FoundDevice(NAMED, "mtp", True))
    with pytest.raises(gio_storage.SeveralDevices, match="--device-uri: " + NUMERIC):
        service.open_session(allow_ums=True)


def test_several_garmin_drives(watch_and_drives, monkeypatch):
    """Two drives: the same error, without file:// addresses (the user name) and without the
    advice about --device-uri, which takes no drive."""
    from openexpress import service

    monitor_with(monkeypatch, watch_and_drives.drive(),
                 watch_and_drives.drive("GARMIN 2", port="1-5", devnum=13, disk="sdc"))
    with pytest.raises(gio_storage.SeveralDevices) as error:
        service.open_session(allow_ums=True)
    text = str(error.value)
    assert text == ("Several Garmin devices are connected in mass storage mode. Connect only "
                    "one of them.")
    assert "file:" not in text and "--device-uri" not in text and MEDIA_USER not in text
    assert error.value.__cause__ is None


def test_several_mtp_devices_come_with_the_devices_found(watch_and_drives, monkeypatch):
    """K1: the error carries what was found (kind, address, USB id), for the window to offer
    a choice; its text is the same as before."""
    from openexpress import service

    monitor_with(monkeypatch)  # no drive
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    watch_and_drives.offered.append(gio_storage.FoundDevice(
        NAMED, "Edge", True, usb=gio_storage.GarminUsb("5158", "usb", 1, 10)))
    with pytest.raises(gio_storage.SeveralDevices) as error:
        service.open_session(allow_ums=True)
    assert str(error.value) == ("Several devices connected, choose one with --device-uri: "
                                f"{NUMERIC}, {NAMED}")
    assert [(d.kind, d.uri, d.usb.usb_id) for d in error.value.devices] == [
        ("mtp", NUMERIC, "091e:51b4"), ("mtp", NAMED, "091e:5158")]


def test_several_garmin_drives_come_with_the_drives_found(watch_and_drives, monkeypatch):
    """K1: two drives, the same: kind, address and USB id of each; the text as before."""
    from openexpress import service

    first = watch_and_drives.drive()
    second = watch_and_drives.drive("GARMIN 2", port="1-5", devnum=13, disk="sdc")
    monitor_with(monkeypatch, first, second)
    with pytest.raises(gio_storage.SeveralDevices) as error:
        service.open_session(allow_ums=True)
    assert str(error.value) == ("Several Garmin devices are connected in mass storage mode. "
                                "Connect only one of them.")
    assert [(d.kind, d.uri, d.usb.usb_id) for d in error.value.devices] == [
        ("ums", first.root.get_uri(), "091e:20ee"), ("ums", second.root.get_uri(), "091e:20ee")]


def found_list():
    """What find_all_devices gives: kind, address and USB id of each device."""
    return [(d.kind, d.uri, d.usb.usb_id if d.usb else None)
            for d in gio_storage.find_all_devices()]


def test_all_devices_with_an_mtp_device_alone(watch_and_drives, monkeypatch):
    """K2: one MTP device, no drive: one entry of kind mtp."""
    monitor_with(monkeypatch)
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    assert found_list() == [("mtp", NUMERIC, "091e:51b4")]


def test_all_devices_with_a_garmin_drive_alone(watch_and_drives, monkeypatch):
    """K2: one Garmin drive, no MTP device: one entry of kind ums."""
    drive = watch_and_drives.drive()
    monitor_with(monkeypatch, drive)
    assert found_list() == [("ums", drive.root.get_uri(), "091e:20ee")]


def test_all_devices_lists_an_mtp_device_and_a_garmin_drive(watch_and_drives, monkeypatch):
    """K2: both at once are both listed (opening one still puts MTP first, D1); nothing is
    opened and no GarminDevice.xml is read for the list."""
    drive = watch_and_drives.drive()
    monitor_with(monkeypatch, drive)
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    read = []
    monkeypatch.setattr(gio_storage.LocalStorage, "read",
                        lambda self, path: read.append(path) or b"")
    assert found_list() == [("mtp", NUMERIC, "091e:51b4"),
                            ("ums", drive.root.get_uri(), "091e:20ee")]
    assert read == [] and watch_and_drives.opened == []


def summary_of(summary):
    return (summary.found.kind, summary.description, summary.part_number,
            summary.software_version, summary.reason)


def test_probe_reads_a_garmin_drive(watch_and_drives, monkeypatch):
    """K3: model, part number and version from the drive's GarminDevice.xml; the unit ID
    is kept in memory only."""
    drive = watch_and_drives.drive()
    monitor_with(monkeypatch, drive)
    (found,) = gio_storage.find_all_devices()
    summary = gio_storage.probe(found)
    assert summary_of(summary) == ("ums", "Varia RTL515", "006-B3592-03", "3.36", None)
    assert summary.ok and summary.unit_id == 3000000003 and summary.found is found


@pytest.mark.parametrize("content, reason", [
    (None, "no-device-file"),            # a GARMIN folder without the file
    (b"<Device><Model>", "unreadable"),  # cut short
])
def test_probe_of_a_drive_without_a_readable_device_file(drives, content, reason):
    """K3: no exception; the summary says why it has no details."""
    folder = drive_folder(drives, xml_path=None)
    if content is not None:
        (folder / "GARMIN" / "GarminDevice.xml").write_bytes(content)
    found = gio_storage.FoundDevice(folder.as_uri(), "GARMIN", True,
                                    usb=gio_storage.GarminUsb("20ee", "usb", 1, 12), kind="ums")
    summary = gio_storage.probe(found)
    assert summary_of(summary) == ("ums", "", "", "", reason)
    assert not summary.ok and summary.unit_id is None


def test_probe_does_not_mount_an_mtp_device(watch_and_drives, monkeypatch):
    """K3 (P3): an MTP device whose volume is not mounted gets an entry without details;
    nothing is mounted or read."""
    mounted = []
    monkeypatch.setattr(gio_storage, "mount", lambda dev, *a, **kw: mounted.append(dev))
    monkeypatch.setattr(gio_storage, "GioStorage",
                        lambda uri: pytest.fail("nothing is opened for an unmounted device"))
    found = gio_storage.FoundDevice(NUMERIC, "fenix", False,
                                    usb=gio_storage.GarminUsb("51b4", "usb", 1, 9))
    summary = gio_storage.probe(found)
    assert summary_of(summary) == ("mtp", "", "", "", "not-mounted")
    assert mounted == []


def test_probe_never_writes_the_unit_id(watch_and_drives, monkeypatch, caplog):
    """K3: neither the log (the raw records, before the log filter) nor the summary's text
    shows the unit ID; the MTP device and the drive each get one line."""
    drive = watch_and_drives.drive()
    monitor_with(monkeypatch, drive)
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    with caplog.at_level(logging.DEBUG, logger="openexpress"):
        summaries = [gio_storage.probe(f) for f in gio_storage.find_all_devices()]
    assert [summary_of(s) for s in summaries] == [
        ("mtp", "Test watch", "006-B0000-00", "23.39", None),
        ("ums", "Varia RTL515", "006-B3592-03", "3.36", None)]
    assert [s.unit_id for s in summaries] == [1234567890, 3000000003]
    for secret in ("1234567890", "3000000003"):
        assert secret not in caplog.text and all(secret not in repr(s) for s in summaries)
    assert [r.getMessage() for r in caplog.records if "probed" in r.getMessage()] == [
        "device: probed mtp (USB 091e:51b4): details read",
        "device: probed ums (USB 091e:20ee): details read"]


def test_open_session_opens_the_mtp_device_chosen(watch_and_drives, monkeypatch):
    """K4: of two MTP devices, the one chosen from the list is opened, not the other; without
    a choice it is "Several devices" as before."""
    from openexpress import service

    monitor_with(monkeypatch)
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    edge = gio_storage.FoundDevice(NAMED, "Edge", True,
                                   usb=gio_storage.GarminUsb("5158", "usb", 1, 10))
    watch_and_drives.offered.append(edge)
    with pytest.raises(gio_storage.SeveralDevices):
        service.open_session(allow_ums=True)
    s = service.open_session(found=edge)
    assert s.storage.label.startswith(NAMED) and not s.storage.label.startswith(NUMERIC)
    assert s.storage.usb_id == "091e:5158" and not s.mass_storage
    assert s.device.description == "Test watch"


def test_open_session_opens_the_garmin_drive_chosen(watch_and_drives, monkeypatch):
    """K4: of two Garmin drives, the one chosen is opened."""
    from openexpress import service

    first = watch_and_drives.drive()
    second = watch_and_drives.drive("GARMIN 2", port="1-5", devnum=13, disk="sdc")
    monitor_with(monkeypatch, first, second)
    chosen = gio_storage.find_all_devices()[1]
    s = service.open_session(found=chosen)
    assert s.mass_storage and s.storage.device_uri == second.root.get_uri()
    assert s.device.description == "Varia RTL515" and s.storage.usb_id == "091e:20ee"


def test_watcher_is_not_started_by_a_garmin_drive(drives, monkeypatch):
    """watch.on_mount stays with garmin_usb (MTP): a drive starts no sync."""
    from openexpress import watch

    started = []
    monkeypatch.setattr(watch.GLib, "timeout_add_seconds", lambda delay, fn: started.append(fn))
    mount = FakeDriveMount(drive_folder(drives), usb_drive(drives))
    monitor_with(monkeypatch, mount)
    watch.Watcher().on_mount(None, mount)
    assert started == []


# --- the window and a Garmin drive -----------------------------------------------------------


@pytest.fixture
def drive_window(watch_and_drives, monkeypatch):
    """The window's own code for mount events and detection; a task runs at once and each
    `show_device` is recorded with the session it shows."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    timers = Timers()
    monkeypatch.setattr(app.GLib, "timeout_add", timers.add)
    monkeypatch.setattr(app.GLib, "source_remove", timers.remove)
    win = types.SimpleNamespace(busy=False, session=None, _refresh_pending=False, built=[],
                                _updated_apps={"an app waiting for the unplug"}, timers=timers,
                                world=watch_and_drives,
                                sections={"watch": types.SimpleNamespace(show=lambda s: None)})

    def run_task(title, fn, done, failed):
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - as the real one: the error goes to `failed`
            failed(e)
        else:
            done(result)

    win.run_task = run_task
    win.show_device = lambda: win.built.append(win.session)
    win.refresh_device = types.MethodType(MainWindow.refresh_device, win)
    return win


def test_window_detects_a_garmin_drive_when_it_is_mounted(drive_window, caplog):
    """Test 1: the mount of a Garmin drive leads to a detection; a pendrive does not."""
    from openexpress.gui.app import MainWindow

    win, world = drive_window, drive_window.world
    pendrive = FakeDriveMount(drive_folder(world.drives, label="STICK"),
                              usb_drive(world.drives, vendor="0781", product="5581", port="1-6",
                                        devnum=14, disk="sdd", partition="sdd1"), name="STICK")
    with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
        MainWindow._on_mount_added(win, None, pendrive)
        assert win.timers.live == {}
        MainWindow._on_mount_added(win, None, world.drive())
    assert len(win.timers.live) == 1
    assert [r.getMessage() for r in caplog.records] == [
        "mount added (file:): not a Garmin device, ignored",
        "mount added (file:): a Garmin device, detecting in 1.5 s (a device is open: False)"]


def test_window_opens_a_garmin_drive(drive_window, monkeypatch):
    """Test 2: the window's detection opens the RTL515 as a Garmin drive; nothing is asked of
    the account. An MTP device next to it would come first (step 4)."""
    monitor_with(monkeypatch, drive_window.world.drive())
    drive_window.refresh_device()
    (s,) = drive_window.built
    assert s is drive_window.session and s is not None and s.mass_storage
    assert s.device.description == "Varia RTL515" and s.storage.usb_id == "091e:20ee"
    assert s.ownership_error is None and s.account_devices is None


def drive_session(drive_window, monkeypatch):
    """A session on the RTL515 drive as the window gets it (step 4), without the window."""
    from openexpress import service

    monitor_with(monkeypatch, drive_window.world.drive())
    return service.check_session(service.open_session(allow_ums=True))


def test_picture_of_a_garmin_drive_comes_from_unit_info(drive_window, monkeypatch):
    """Not from the account, also when logged in: by unit-info, as for an MTP device that
    the account does not list."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    s, asked = drive_session(drive_window, monkeypatch), []
    s.client = types.SimpleNamespace(
        auth=object(), http=None,  # logged in
        device_image_url=lambda unit_id: asked.append(("account", unit_id)),
        unit_info=lambda unit_id, part: asked.append(("unit-info", unit_id, part))
        or {"graphicurl": "https://res.garmin.com/rtl515.png"})
    monkeypatch.setattr(app, "download_picture",
                        lambda http, url, name: asked.append(("download", url)) or (None, "test"))
    works, hidden = [], types.SimpleNamespace(set_visible=lambda visible: None)
    win = types.SimpleNamespace(session=s, device_image=hidden, device_picture=hidden,
                                in_background=lambda work, done: works.append(work))
    MainWindow._show_device_picture(win)
    assert works[0]() == (None, "download refused: test")
    assert asked == [("unit-info", 3000000003, "006-B3592-03"),
                     ("download", "https://res.garmin.com/rtl515.png")]


def _texts(widget):
    """The texts of all labels below a widget."""
    from openexpress.gui.app import Gtk

    found = [widget.get_label()] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child is not None:
        found += _texts(child)
        child = child.get_next_sibling()
    return found


def _drive_device_page(drive_window, monkeypatch):
    """(the texts on the device page of the RTL515 drive, what it started)."""
    from test_fixes_ui import page_window

    from openexpress.gui.app import MainWindow

    win, section = page_window("watch")
    win.session = drive_session(drive_window, monkeypatch)
    win.update_state, win.map_state, calls = None, None, []
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync"):
        setattr(win, name, lambda name=name: calls.append(name))
    for name in ("_render_update_state", "_render_map_state", "select_section", "backup",
                 "set_background", "logout"):
        setattr(win, name, lambda *a, **kw: None)
    win._background_enabled = lambda: False
    MainWindow.show_overview(win)
    return _texts(section.page), calls


@pytest.mark.needs_display
def test_device_page_of_a_garmin_drive(drive_window, monkeypatch):
    """Name, version, storage, the software check and the part number; no sync and no maps
    (and so no map check)."""
    texts, calls = _drive_device_page(drive_window, monkeypatch)
    for text in ("Varia RTL515", "Software 3.36", "Device storage", "Device software",
                 "Part number", "006-B3592-03"):
        assert text in texts
    for text in ("Sync with Garmin Connect", "Last sync", "Automatic sync is off", "Maps"):
        assert text not in texts
    assert calls == ["_show_device_picture", "_check_updates_quietly"]


@pytest.mark.needs_display
def test_device_page_of_a_garmin_drive_says_what_applies(drive_window, monkeypatch):
    """D6: no backup. A short description in place of the sync; not the words for a drive the
    window cannot open (step 1)."""
    texts, _calls = _drive_device_page(drive_window, monkeypatch)
    assert "Backup" not in texts
    assert "Mass storage mode" in texts and MASS_STORAGE_TEXT in texts
    assert gio_storage.not_ready_text(gio_storage.MASS_STORAGE) not in texts


def test_window_after_a_garmin_drive_is_unplugged(drive_window, monkeypatch, caplog):
    """Mounted, detected and opened; unplugged: no device, as after an MTP device."""
    from openexpress.gui.app import MainWindow

    win, drive = drive_window, drive_window.world.drive()
    monitor_with(monkeypatch, drive)
    MainWindow._on_mount_added(win, None, drive)
    win.timers.fire()
    assert win.session is not None and win.session.mass_storage
    # Unplugged: the system takes the mount away and /sys has no block device for it.
    for link in win.world.drives.block.iterdir():
        link.unlink()
    monitor_with(monkeypatch)
    caplog.clear()  # only what the unplug writes
    with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
        MainWindow._on_mount_removed(win, None, drive)
    assert win.session is None and win.built[-1] is None and win._updated_apps == set()
    assert [r.getMessage() for r in caplog.records] == [
        "mount removed (file:): the open device, showing no device (a task is running: False)"]


def _run_at_once(work, done, failed):
    try:
        result = work()
    except Exception as e:  # noqa: BLE001 - as the window's in_background: any failure
        failed(e)
    else:
        done(result)


def test_a_second_mount_event_of_the_open_drive_reloads_nothing(drive_window, monkeypatch,
                                                                caplog):
    """The same drive reported again right after it was opened: the drive still answers, so
    the pages stay as they are (no second "all pages are loaded anew")."""
    from openexpress.gui.app import MainWindow

    win, drive = drive_window, drive_window.world.drive()
    win.in_background = _run_at_once
    monitor_with(monkeypatch, drive)
    MainWindow._on_mount_added(win, None, drive)
    win.timers.fire()
    opened = win.session
    assert opened is not None and opened.mass_storage and win.built == [opened]
    caplog.clear()  # only what the second event writes
    with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
        MainWindow._on_mount_added(win, None, drive)  # within the same 2 s
        win.timers.fire()
    assert win.session is opened and win.built == [opened]
    assert "all pages are loaded anew" not in caplog.text
    assert "device detection: the open device is still there, nothing to do" in caplog.text


def test_two_mount_events_of_a_drive_before_it_is_open_open_it_once(drive_window, monkeypatch):
    """Two reports of one drive before the first detection: one detection, one opening."""
    from openexpress.gui.app import MainWindow

    win, drive = drive_window, drive_window.world.drive()
    monitor_with(monkeypatch, drive)
    MainWindow._on_mount_added(win, None, drive)
    MainWindow._on_mount_added(win, None, drive)
    assert len(win.timers.live) == 1
    win.timers.fire()
    assert len(win.built) == 1 and win.built[0] is not None and win.built[0].mass_storage


def _open_below(root: Path) -> list[str]:
    """The files and folders this process has open below `root` (Linux: /proc/self/fd)."""
    found = []
    for fd in Path("/proc/self/fd").iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target == str(root) or target.startswith(str(root) + os.sep):
            found.append(target)
    return found


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_reading_a_garmin_drive_keeps_nothing_open_on_it(drive_window, monkeypatch):
    """After the window read the drive (GarminDevice.xml, folder listings, the start of a file,
    free space), no file or folder on it stays open and the current folder is not on it:
    nothing makes the system say the drive is busy when it is ejected."""
    s = drive_session(drive_window, monkeypatch)
    root = s.storage.root
    cwd = os.getcwd()
    s.storage.read("GARMIN/GarminDevice.xml")
    s.storage.listdir("GARMIN")
    assert s.storage.exists("GARMIN/GarminDevice.xml")
    s.storage.size("GARMIN/GarminDevice.xml")
    s.storage.read_start("GARMIN/GarminDevice.xml", 16)
    s.storage.free_space()
    assert _open_below(root) == []
    assert os.getcwd() == cwd and not cwd.startswith(str(root))


def test_flatpak_window_tells_it_does_not_open_a_garmin_drive(drive_window, monkeypatch,
                                                              caplog):
    """Test 12 (F1): in the Flatpak the drive of a Garmin device is known from /sys (vendor
    091e) and from gvfs, but its folder under /run/media is not in the sandbox. No session;
    the page says that this version has no access to the drive's folder, and the log says why
    it was skipped."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    monkeypatch.setattr(app.paths, "in_flatpak", lambda: True)
    win, drive = drive_window, drive_window.world.drive()
    shutil.rmtree(drive.root.path)  # /run/media/<user>/GARMIN does not exist in the sandbox
    monitor_with(monkeypatch, drive)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win.refresh_device()
    assert win.session is None and win.built == [None]
    assert ("device: a Garmin drive (USB 091e:20ee) is not visible in the Flatpak, skipped"
            in caplog.text)
    assert "without GARMIN/GarminDevice.xml" not in caplog.text

    shown = []
    page = types.SimpleNamespace(session=None, busy=False, _usb_state=gio_storage.ABSENT,
                                 _usb_since=0.0, _usb_title="", _usb_notice=MainWindow._usb_notice,
                                 _update_actions=lambda: None, refresh_device=lambda: None,
                                 start=FakeStartPage(),
                                 sections={"watch": types.SimpleNamespace(
                                     set_empty=lambda *notice: shown.append(notice))})
    MainWindow._poll_usb(page, clock=win.world.clock)
    assert shown == [("Mass storage mode", FLATPAK_DRIVE_TEXT, False)]


def test_the_flatpak_says_what_the_source_version_says_unless_the_drive_is_hidden(
        drive_window, monkeypatch):
    """The Flatpak with its access to /run/media: a Garmin device in mass storage mode whose
    drive is not mounted gets the words of the version from source; only a drive mounted
    where the sandbox cannot see gets the Flatpak's sentence (the condition that skips it in
    the detection)."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    monkeypatch.setattr(app.paths, "in_flatpak", lambda: True)
    monitor_with(monkeypatch)  # on USB, not mounted
    assert MainWindow._usb_notice(gio_storage.MASS_STORAGE, 0) == (
        "Mass storage mode", DRIVE_NOT_MOUNTED_TEXT, False)
    drive = drive_window.world.drive()
    monitor_with(monkeypatch, drive)  # mounted and visible: the window opens it
    assert not gio_storage.drive_hidden_in_sandbox()
    assert MainWindow._usb_notice(gio_storage.MASS_STORAGE, 0)[1] == DRIVE_NOT_MOUNTED_TEXT
    shutil.rmtree(drive.root.path)  # mounted, but out of the sandbox's sight
    assert gio_storage.drive_hidden_in_sandbox()
    assert MainWindow._usb_notice(gio_storage.MASS_STORAGE, 0) == (
        "Mass storage mode", FLATPAK_DRIVE_TEXT, False)
    assert "--path" not in FLATPAK_DRIVE_TEXT


# --- several devices: a list to choose from (start page, K5) ---------------------------------

EDGE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
 <Model><PartNumber>006-B4440-00</PartNumber><SoftwareVersion>3311</SoftwareVersion>
  <Description>Edge 1050</Description></Model>
 <Id>3000000002</Id>
</Device>
"""


@pytest.fixture
def choosing_window(drive_window):
    """The window's detection with the list to choose from: what the list would show is
    recorded (kind and name of each entry); opening one goes through the real code."""
    from openexpress.gui.app import MainWindow

    win = drive_window
    win._choices, win._choices_usb, win.shown_choices, win.errors = None, None, [], []
    win.show_choices = lambda: win.shown_choices.append(
        [(s.found.kind, s.description) for s in win._choices])
    # Bound only when it exists, so that on code without it the tests fail on what they check.
    if hasattr(MainWindow, "open_choice"):
        win.open_choice = types.MethodType(MainWindow.open_choice, win)
    win.error = lambda title, text: win.errors.append(text)
    # The start page reads the list again when another device goes away (D5): recorded here.
    win.rereads = []
    win._reread_connected = lambda: win.rereads.append(win.session)
    return win


def two_watches(world, monkeypatch, tmp_path, device_root):
    """Two different MTP devices: the test watch (NUMERIC) and an Edge 1050 (NAMED), each
    with its own GarminDevice.xml."""
    edge_root = tmp_path / "edge"
    (edge_root / "GARMIN").mkdir(parents=True)
    (edge_root / "GARMIN" / "GarminDevice.xml").write_text(EDGE_XML)
    mtp = gio_storage.GioStorage  # the stand-in of watch_and_drives

    class TwoDevices(mtp):
        def __init__(self, uri):
            super().__init__(uri)
            if uri.startswith(NAMED):
                self.root = edge_root

    monkeypatch.setattr(gio_storage, "GioStorage", TwoDevices)
    world.plug("51b4")
    world.offer()
    edge = gio_storage.FoundDevice(NAMED, "Edge", True,
                                   usb=gio_storage.GarminUsb("5158", "usb", 1, 10))
    world.offered.append(edge)
    return edge


def test_window_offers_a_choice_of_two_mtp_devices(choosing_window, monkeypatch, tmp_path,
                                                   device_root):
    """K5: two MTP devices: a list of both by name, no session, no "starting" or "PIN",
    and no detection planned again."""
    win = choosing_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    assert win.session is None and win.built == []
    assert win.shown_choices == [[("mtp", "Test watch"), ("mtp", "Edge 1050")]]
    assert win.timers.live == {}


def test_window_offers_an_mtp_device_and_a_garmin_drive(choosing_window, monkeypatch):
    """K5 (P2): an MTP device and a Garmin drive, two different devices: both on the list
    (opening without a choice would take the MTP device, D1)."""
    win = choosing_window
    monitor_with(monkeypatch, win.world.drive())
    win.world.plug("51b4")
    win.world.offer()
    win.refresh_device()
    assert win.session is None
    assert win.shown_choices == [[("mtp", "Test watch"), ("ums", "Varia RTL515")]]


def test_one_device_in_both_modes_is_no_choice(choosing_window, monkeypatch, device_root):
    """K5 (P2, D1): the same device seen over MTP and as a drive (the same unit ID) is one
    device: no list; it opens over MTP, as before."""
    win = choosing_window
    (device_root / "GARMIN" / "GarminDevice.xml").write_text(RTL515_XML)
    monitor_with(monkeypatch, win.world.drive())
    win.world.plug("51b4")
    win.world.offer()
    win.refresh_device()
    assert win.shown_choices == []
    (s,) = win.built
    assert s is win.session and not s.mass_storage


def test_the_devices_to_choose_from_are_merged_by_unit_id():
    """K5: one entry per device: an MTP entry and a drive with the same unit ID are one
    (the MTP entry stays, D1); entries without details are kept apart."""
    usb = gio_storage.GarminUsb("20ee", "usb", 1, 12)

    def summary(kind, uri, unit_id, reason=None):
        return gio_storage.DeviceSummary(
            gio_storage.FoundDevice(uri, "x", True, usb=usb, kind=kind),
            "name" if reason is None else "", "", "", unit_id, reason)

    mtp, drive = summary("mtp", NUMERIC, 3000000003), summary("ums", "file:///d/", 3000000003)
    other = summary("mtp", NAMED, 3000000002)
    unknown = [summary("mtp", "mtp://a/", None, "not-mounted"),
               summary("mtp", "mtp://b/", None, "not-mounted")]
    assert gio_storage.distinct_devices([drive, mtp, other, *unknown]) == [
        mtp, other, *unknown]


def test_choosing_a_device_opens_it(choosing_window, monkeypatch, tmp_path, device_root):
    """K5: a click on an entry opens that device, as if it were the only one."""
    win = choosing_window
    monitor_with(monkeypatch)
    edge = two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    chosen = next(s for s in win._choices if s.found is edge)
    win.open_choice(chosen.found)
    (s,) = win.built
    assert s is win.session and s.storage.label.startswith(NAMED)
    assert s.device.description == "Edge 1050" and win._choices is None


def test_unplugging_down_to_one_device_opens_it(choosing_window, monkeypatch):
    """K5 (D7): two drives on the list; one is unplugged: the list is detected again and the
    one left is opened."""
    from openexpress.gui.app import MainWindow

    win = choosing_window
    first = win.world.drive()
    second = win.world.drive("GARMIN 2", port="1-5", devnum=13, disk="sdc")
    # Another radar: its own unit ID.
    xml = Path(second.root.path) / "GARMIN" / "GarminDevice.xml"
    xml.write_text(RTL515_XML.replace("3000000003", "3000000004"))
    monitor_with(monkeypatch, first, second)
    win.refresh_device()
    assert win.session is None and win.shown_choices == [
        [("ums", "Varia RTL515"), ("ums", "Varia RTL515")]]
    for name in ("sdc", "sdc1"):  # unplugged: gone from /sys
        (win.world.drives.block / name).unlink()
    monitor_with(monkeypatch, first)
    MainWindow._on_mount_removed(win, None, second)
    assert len(win.timers.live) == 1
    win.timers.fire()
    (s,) = win.built
    assert s is win.session and s.mass_storage and s.storage.device_uri == first.root.get_uri()


def test_refresh_keeps_the_device_chosen(choosing_window, monkeypatch, tmp_path, device_root):
    """K5: with two devices connected and one chosen, Refresh opens the chosen one again
    instead of going back to the list."""
    win = choosing_window
    monitor_with(monkeypatch)
    edge = two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    win.open_choice(edge)
    win.refresh_device()  # "Refresh"
    assert len(win.shown_choices) == 1  # the list only once, at the start
    first, again = win.built
    assert again is win.session and again is not first
    assert again.storage.label.startswith(NAMED) and again.device.description == "Edge 1050"


def test_one_device_found_twice_opens_without_a_list(choosing_window, monkeypatch):
    """K5: two entries of one device (the same unit ID) where opening says "Several devices":
    it is opened at once, not offered as a list of one."""
    win = choosing_window
    first = win.world.drive()
    second = win.world.drive("GARMIN 2", port="1-5", devnum=13, disk="sdc")
    monitor_with(monkeypatch, first, second)  # both with the same GarminDevice.xml
    win.refresh_device()
    assert win.shown_choices == []
    (s,) = win.built
    assert s is win.session and s.mass_storage and s.device.description == "Varia RTL515"


def test_a_second_device_plugged_in_while_one_is_open_gives_the_list(choosing_window,
                                                                    monkeypatch, tmp_path,
                                                                    device_root):
    """K5, found on the devices: the Edge is open alone, then the fēnix is mounted. The
    detection that follows lists both; it does not open the Edge again (keep_open is only
    for Refresh and the like, not for a device plugged in)."""
    from openexpress.gui.app import MainWindow

    win, world = choosing_window, choosing_window.world
    monitor_with(monkeypatch)
    edge = two_watches(world, monkeypatch, tmp_path, device_root)
    fenix = world.offered[0]
    world.offered[:] = [edge]  # the Edge alone first
    win.refresh_device()
    (opened,) = win.built
    assert opened is win.session and opened.storage.label.startswith(NAMED)
    world.offered[:] = [fenix, edge]  # then gvfs mounts the fēnix
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    win.timers.fire()
    assert win.shown_choices == [[("mtp", "Test watch"), ("mtp", "Edge 1050")]]
    assert win.built == [opened] and win.session is None  # the Edge was not opened again


def test_two_devices_without_an_id_are_not_merged(choosing_window, monkeypatch, tmp_path,
                                                  device_root):
    """K5: GarminDevice.xml without <Id> gives no unit ID (None, not 0): two such devices
    are two entries, never merged."""
    import re

    win = choosing_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    for xml in (device_root / "GARMIN" / "GarminDevice.xml",
                tmp_path / "edge" / "GARMIN" / "GarminDevice.xml"):
        xml.write_text(re.sub(r"\s*<Id>\d+</Id>", "", xml.read_text()))
    win.refresh_device()
    assert win.session is None
    assert win.shown_choices == [[("mtp", "Test watch"), ("mtp", "Edge 1050")]]
    assert [s.unit_id for s in win._choices] == [None, None]


def test_two_mtp_devices_known_by_their_usb_device_are_both_listed(choosing_window, monkeypatch,
                                                                  tmp_path, device_root):
    """K5, as on the devices: the Edge's address has its name ("Garmin_…"), the fēnix's only
    the IDs ("091e_51b4_…", libmtp does not know it); both are Garmin devices by vendor
    091e in /sys (the real find_devices), so both are on the list."""
    win, world = choosing_window, choosing_window.world
    two_watches(world, monkeypatch, tmp_path, device_root)
    monkeypatch.setattr(gio_storage, "find_devices", REAL_FIND_DEVICES)
    usb_device(world.root, "1-2", 1, 28, "091e", "5158")
    usb_device(world.root, "1-8", 1, 31, "091e", "51b4")
    monitor = FakeMonitor(mounts=[FakeMount(NAMED, "Edge 1050"), FakeMount(NUMERIC, "091e 51b4")],
                          volumes=[FakeVolume(NAMED, "/dev/bus/usb/001/028", "Edge 1050"),
                                   FakeVolume(NUMERIC, "/dev/bus/usb/001/031", "091e 51b4")])
    monkeypatch.setattr(gio_storage.Gio.VolumeMonitor, "get", staticmethod(lambda: monitor))
    assert [(d.kind, d.uri, d.usb.usb_id, d.usb.source)
            for d in gio_storage.find_all_devices()] == [
        ("mtp", NAMED, "091e:5158", "usb"), ("mtp", NUMERIC, "091e:51b4", "usb")]
    win.refresh_device()
    assert win.shown_choices == [[("mtp", "Edge 1050"), ("mtp", "Test watch")]]


def three_devices(win, monkeypatch, tmp_path, device_root):
    """The test watch and an Edge over MTP, and a Varia drive: the list of three."""
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    usb_device(win.world.root, "1-2", 1, 10, "091e", "5158")  # the Edge on USB
    drive = win.world.drive()
    monitor_with(monkeypatch, drive)
    win.refresh_device()
    assert win.shown_choices == [
        [("mtp", "Test watch"), ("mtp", "Edge 1050"), ("ums", "Varia RTL515")]]
    return drive


def test_unplugging_another_device_keeps_the_open_one(choosing_window, monkeypatch, tmp_path,
                                                      device_root):
    """K6: the test watch was chosen from the list and is open; the Edge, which is not the
    one shown, is unplugged. The open device stays as it is: not closed, no list."""
    from openexpress.gui.app import MainWindow

    win, world = choosing_window, choosing_window.world
    monitor_with(monkeypatch)
    edge = two_watches(world, monkeypatch, tmp_path, device_root)
    usb_device(world.root, "1-2", 1, 10, "091e", "5158")
    win.refresh_device()
    fenix = next(s.found for s in win._choices if s.found is not edge)
    win.open_choice(fenix)
    (opened,) = win.built
    # The Edge is unplugged: gone from gvfs and from /sys.
    world.offered.remove(edge)
    for f in (world.root / "1-2").iterdir():
        f.unlink()
    (world.root / "1-2").rmdir()
    MainWindow._on_mount_removed(win, None, FakeMount(NAMED))
    win.timers.fire()
    assert win.session is opened and win.built == [opened] and len(win.shown_choices) == 1


def test_a_device_gone_from_the_list_of_three_leaves_two(choosing_window, monkeypatch,
                                                         tmp_path, device_root):
    """K6 (guards K5): the list of three; the drive is unplugged: the list of the two
    others."""
    from openexpress.gui.app import MainWindow

    win = choosing_window
    drive = three_devices(win, monkeypatch, tmp_path, device_root)
    for name in ("sdb", "sdb1"):  # unplugged: gone from /sys
        (win.world.drives.block / name).unlink()
    monitor_with(monkeypatch)
    MainWindow._on_mount_removed(win, None, drive)
    win.timers.fire()
    assert win.session is None
    assert win.shown_choices[-1] == [("mtp", "Test watch"), ("mtp", "Edge 1050")]


def test_a_third_device_makes_the_list_grow(choosing_window, monkeypatch, tmp_path,
                                            device_root):
    """K6 (guards K5): the list of two; a drive is mounted: the list of three."""
    from openexpress.gui.app import MainWindow

    win = choosing_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    assert win.shown_choices == [[("mtp", "Test watch"), ("mtp", "Edge 1050")]]
    drive = win.world.drive()
    monitor_with(monkeypatch, drive)
    MainWindow._on_mount_added(win, None, drive)
    win.timers.fire()
    assert win.session is None and win.shown_choices[-1] == [
        ("mtp", "Test watch"), ("mtp", "Edge 1050"), ("ums", "Varia RTL515")]


def test_a_device_never_mounted_that_leaves_is_noticed(choosing_window, monkeypatch, tmp_path,
                                                       device_root):
    """K6 (guards K5): an Edge whose volume was never mounted (an entry without details)
    leaves USB without any mount event; the poll of USB notices it, and the one device
    left opens (D7)."""
    from openexpress.gui.app import MainWindow

    win, world = choosing_window, choosing_window.world
    monitor_with(monkeypatch)
    edge = two_watches(world, monkeypatch, tmp_path, device_root)
    edge.mounted = False
    usb_device(world.root, "1-2", 1, 10, "091e", "5158")
    win.refresh_device()
    assert win.shown_choices == [[("mtp", "Test watch"), ("mtp", "")]]
    world.offered.remove(edge)
    for f in (world.root / "1-2").iterdir():
        f.unlink()
    (world.root / "1-2").rmdir()
    MainWindow._poll_usb(win)
    win.timers.fire()
    (s,) = win.built
    assert s is win.session and s.storage.label.startswith(NUMERIC)


def test_devices_not_on_the_account_are_on_the_list(choosing_window, monkeypatch):
    """K5: the list comes from what is plugged in, not from the account: a drive and an MTP
    device that the account does not list are both there, and the account is not asked."""
    from openexpress import service

    asked = []
    monkeypatch.setattr(service, "require_account_device",
                        lambda client, unit_id: asked.append(unit_id) or [])
    monkeypatch.setattr(service, "check_session",
                        lambda s: asked.append("check") or s)
    win = choosing_window
    monitor_with(monkeypatch, win.world.drive())
    win.world.plug("51b4")
    win.world.offer()
    win.refresh_device()
    assert win.shown_choices == [[("mtp", "Test watch"), ("ums", "Varia RTL515")]]
    assert asked == []


@pytest.mark.needs_display
def test_list_shows_names_and_never_addresses(monkeypatch):
    """K5, on the start page since D5 (T2): one tile per device: its name (or "Garmin
    device" without details), how it is connected, its version or why it has no details;
    never an MTP address; the other pages say to choose a device."""
    from openexpress.gui.app import MainWindow, Section
    from openexpress.gui.start import StartPage

    maps = Section("maps", "Maps", "x-symbolic", "Loading…")
    win = start_stand_in(StartPage(), {"maps": maps})
    usb = gio_storage.GarminUsb("51b4", "usb", 1, 9)
    win._choices = [
        gio_storage.DeviceSummary(gio_storage.FoundDevice(NUMERIC, "x", True, usb=usb),
                                  "fēnix 8", "006-B4532-00", "23.39", 1234567890),
        gio_storage.DeviceSummary(gio_storage.FoundDevice(NAMED, "x", False, usb=usb),
                                  reason=gio_storage.NOT_MOUNTED),
        gio_storage.DeviceSummary(gio_storage.FoundDevice("file:///run/media/u/GARMIN/", "x",
                                                          True, usb=usb, kind="ums"),
                                  "Varia RTL515", "006-B3592-03", "3.36", 3000000003)]
    MainWindow.show_choices(win)
    texts = _texts(win.start.page)
    rows = list(itertools.pairwise(texts))  # a tile's name, then how it is connected
    for title, subtitle in (("fēnix 8", "Over USB (MTP) · Software 23.39"),
                            ("Garmin device", ("Over USB (MTP) · Not mounted; choose it to "
                                               "open it")),
                            ("Varia RTL515", "USB drive · Software 3.36")):
        assert (title, subtitle) in rows
    assert not any("mtp://" in t or "0000abcdef" in t or "file:" in t for t in texts)
    assert not any("PIN" in t or "starting" in t for t in texts)
    assert maps.empty.get_title() == "Choose a device"
    assert win.navigation.stack == [win.start.page]


# --- several devices: edge cases (after K1–K7) -----------------------------------------------


def another_drive(world, unit_id, label="GARMIN 2", port="1-5", devnum=13, disk="sdc"):
    """One more Varia drive, with its own unit ID."""
    drive = world.drive(label, port=port, devnum=devnum, disk=disk)
    xml = Path(drive.root.path) / "GARMIN" / "GarminDevice.xml"
    xml.write_text(RTL515_XML.replace("3000000003", str(unit_id)))
    return drive


def unplug_drive(win, monkeypatch, drive, disk, *left):
    """A drive unplugged: gone from /sys and from gvfs; the window hears of it."""
    from openexpress.gui.app import MainWindow

    for name in (disk, disk + "1"):
        (win.world.drives.block / name).unlink()
    monitor_with(monkeypatch, *left)
    MainWindow._on_mount_removed(win, None, drive)
    win.timers.fire()


def unplug_mtp(win, found, usb_folder=None):
    """An MTP device unplugged: gone from gvfs (and from /sys); the window hears of it."""
    from openexpress.gui.app import MainWindow

    win.world.offered.remove(found)
    if usb_folder is not None:
        for f in usb_folder.iterdir():
            f.unlink()
        usb_folder.rmdir()
    MainWindow._on_mount_removed(win, None, FakeMount(found.uri))
    win.timers.fire()


def rows_of(win):
    """The rows of the list now shown: name and subtitle, as the window writes them."""
    from openexpress.gui.app import choice_subtitle

    return [(s.description, choice_subtitle(s)) for s in win._choices]


def test_three_devices_give_three_rows(choosing_window, monkeypatch, tmp_path, device_root):
    """A1: three devices at once: three rows, each with its name and how it is connected."""
    win = choosing_window
    three_devices(win, monkeypatch, tmp_path, device_root)
    assert rows_of(win) == [("Test watch", "Over USB (MTP) · Software 23.39"),
                            ("Edge 1050", "Over USB (MTP) · Software 33.11"),
                            ("Varia RTL515", "USB drive · Software 3.36")]


def test_four_devices_give_four_rows(choosing_window, monkeypatch, tmp_path, device_root):
    """A2: two MTP devices and two drives: four rows."""
    win = choosing_window
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    monitor_with(monkeypatch, win.world.drive(), another_drive(win.world, 3000000004))
    win.refresh_device()
    assert win.shown_choices == [[("mtp", "Test watch"), ("mtp", "Edge 1050"),
                                  ("ums", "Varia RTL515"), ("ums", "Varia RTL515")]]


def test_unplugging_one_by_one_down_to_none(choosing_window, monkeypatch):
    """A3: three drives on the list, unplugged one after another: two on the list, then the
    last one open (D7), then no device at all; never a list of one or of none."""
    win = choosing_window
    first = win.world.drive()
    second = another_drive(win.world, 3000000004)
    third = another_drive(win.world, 3000000005, "GARMIN 3", "1-6", 14, "sdd")
    monitor_with(monkeypatch, first, second, third)
    win.refresh_device()
    assert len(win._choices) == 3
    unplug_drive(win, monkeypatch, third, "sdd", first, second)
    assert win.session is None and len(win._choices) == 2 and win.built == []
    unplug_drive(win, monkeypatch, second, "sdc", first)
    (s,) = win.built
    assert s is win.session and s.storage.device_uri == first.root.get_uri()
    unplug_drive(win, monkeypatch, first, "sdb")
    assert win.session is None and win.built[-1] is None and not win._choices
    assert all(len(shown) > 1 for shown in win.shown_choices)


@pytest.mark.parametrize("left", ["mtp", "ums"])
def test_the_one_device_left_opens_whatever_its_kind(choosing_window, monkeypatch, left):
    """A4 (D7): an MTP device and a drive on the list; one goes: the other one opens, an MTP
    device as well as a drive."""
    win, world = choosing_window, choosing_window.world
    drive = world.drive()
    monitor_with(monkeypatch, drive)
    world.plug("51b4")
    world.offer()
    win.refresh_device()
    assert len(win._choices) == 2
    if left == "mtp":
        unplug_drive(win, monkeypatch, drive, "sdb")
    else:
        unplug_mtp(win, world.offered[0], world.root / "garmin51b4")
    (s,) = win.built
    assert s is win.session and s.mass_storage == (left == "ums")
    assert s.device.description == ("Varia RTL515" if left == "ums" else "Test watch")


def vanishing(monkeypatch, *uris):
    """MTP devices at these addresses are gone the moment they are read (gvfs still lists
    them for a moment): their storage cannot be listed."""
    mtp = gio_storage.GioStorage

    class Vanishing(mtp):
        def __init__(self, uri):
            super().__init__(uri)
            if any(uri.startswith(u) for u in uris):
                self.root = self.root / "gone"  # listing it fails: "Not a directory"

    monkeypatch.setattr(gio_storage, "GioStorage", Vanishing)


def test_a_device_gone_before_it_is_read_is_not_listed(choosing_window, monkeypatch, tmp_path,
                                                       device_root):
    """B5: found, then gone from USB before its GarminDevice.xml was read: no row for it;
    the one device left opens (D7)."""
    win = choosing_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)  # the Edge was never in /sys
    vanishing(monkeypatch, NAMED)
    win.refresh_device()
    assert win.shown_choices == []
    (s,) = win.built
    assert s is win.session and s.storage.label.startswith(NUMERIC)


def test_all_devices_gone_before_they_are_read(choosing_window, monkeypatch, tmp_path,
                                              device_root):
    """B5: both gone before they were read: no list (not even an empty one), no device."""
    win = choosing_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.world.plug(None)  # gone from /sys
    vanishing(monkeypatch, NUMERIC, NAMED)
    win.refresh_device()
    assert win.shown_choices == [] and win.session is None and win.built == [None]
    assert not win._choices


def test_choosing_a_device_that_is_gone(choosing_window, monkeypatch, tmp_path, device_root):
    """B6: the Edge is chosen just after it was unplugged: a message says that it could not
    be opened, and the detection runs again (the test watch alone: it opens)."""
    win, world = choosing_window, choosing_window.world
    monitor_with(monkeypatch)
    edge = two_watches(world, monkeypatch, tmp_path, device_root)
    usb_device(world.root, "1-2", 1, 10, "091e", "5158")
    win.refresh_device()
    assert len(win._choices) == 2
    world.offered.remove(edge)  # unplugged…
    for f in (world.root / "1-2").iterdir():
        f.unlink()
    (world.root / "1-2").rmdir()
    vanishing(monkeypatch, NAMED)
    win.open_choice(edge)  # … and chosen before the list followed
    assert len(win.errors) == 1 and win.errors[0]
    (s,) = win.built
    assert s is win.session and s.storage.label.startswith(NUMERIC)


def test_a_quick_unplug_and_plug_in_makes_one_detection(choosing_window, monkeypatch):
    """B7: a drive on the list is unplugged and plugged in again at once: one detection, the
    same two rows, nothing opened."""
    from openexpress.gui.app import MainWindow

    win = choosing_window
    first, second = win.world.drive(), another_drive(win.world, 3000000004)
    monitor_with(monkeypatch, first, second)
    win.refresh_device()
    MainWindow._on_mount_removed(win, None, second)
    MainWindow._on_mount_added(win, None, second)
    assert len(win.timers.live) == 1  # one timer, moved by the second event
    win.timers.fire()
    assert win.shown_choices == [[("ums", "Varia RTL515"), ("ums", "Varia RTL515")]] * 2
    assert win.session is None and win.built == []


def test_two_devices_not_mounted_are_two_rows(choosing_window, monkeypatch, tmp_path,
                                             device_root):
    """C8 (P3): two MTP devices whose volumes are not mounted, both on USB: two rows to
    click, "not mounted", without details; never merged (no unit ID is no match)."""
    win, world = choosing_window, choosing_window.world
    monitor_with(monkeypatch)
    edge = two_watches(world, monkeypatch, tmp_path, device_root)
    usb_device(world.root, "1-2", 1, 10, "091e", "5158")
    for found in world.offered:
        found.mounted = False
    win.refresh_device()
    assert win.session is None and win.built == []
    assert rows_of(win) == [("", "Over USB (MTP) · Not mounted; choose it to open it")] * 2
    assert [s.found for s in win._choices] == [world.offered[0], edge]


def test_a_damaged_device_file_next_to_a_good_device(choosing_window, monkeypatch, tmp_path,
                                                     device_root):
    """C9: the Edge's GarminDevice.xml is cut short; the test watch is fine: a row "could
    not be read" next to the normal one; nothing stops the list."""
    win, world = choosing_window, choosing_window.world
    monitor_with(monkeypatch)
    two_watches(world, monkeypatch, tmp_path, device_root)
    usb_device(world.root, "1-2", 1, 10, "091e", "5158")
    (tmp_path / "edge" / "GARMIN" / "GarminDevice.xml").write_text("<Device><Model>")
    win.refresh_device()
    assert rows_of(win) == [("Test watch", "Over USB (MTP) · Software 23.39"),
                            ("", "Over USB (MTP) · Could not be read")]


def test_an_mtp_device_and_two_drives_give_three_rows(choosing_window, monkeypatch):
    """D11: an MTP device and two Garmin drives: three rows."""
    win = choosing_window
    monitor_with(monkeypatch, win.world.drive(), another_drive(win.world, 3000000004))
    win.world.plug("51b4")
    win.world.offer()
    win.refresh_device()
    assert win.shown_choices == [[("mtp", "Test watch"), ("ums", "Varia RTL515"),
                                  ("ums", "Varia RTL515")]]


def test_one_device_seen_both_ways_next_to_another(choosing_window, monkeypatch, tmp_path,
                                                   device_root):
    """D12 (P2, D1): the radar is seen over MTP and as a drive at once (the same unit ID),
    an Edge is connected too: the radar is one row (its MTP entry), the Edge another; two
    rows, not three."""
    win = choosing_window
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    (device_root / "GARMIN" / "GarminDevice.xml").write_text(RTL515_XML)  # the radar over MTP
    monitor_with(monkeypatch, win.world.drive())
    win.refresh_device()
    assert win.shown_choices == [[("mtp", "Varia RTL515"), ("mtp", "Edge 1050")]]


def test_devices_on_and_off_the_account_are_both_listed(choosing_window, monkeypatch, tmp_path,
                                                        device_root):
    """D13: the account lists the Edge but not the test watch: both are on the list, and
    the list does not ask the account (K5)."""
    from openexpress import service

    asked = []
    monkeypatch.setattr(service, "require_account_device",
                        lambda client, unit_id: asked.append(unit_id) or [{"unitid": 3000000002}])
    monkeypatch.setattr(service, "check_session", lambda s: asked.append("check") or s)
    win = choosing_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    assert win.shown_choices == [[("mtp", "Test watch"), ("mtp", "Edge 1050")]]
    assert asked == []


# --- the devices seen go to devices.json (start page, D3) -------------------------------------


def remembered():
    """What devices.json holds: unit ID → (kind, name, part number, version)."""
    from openexpress import known_devices

    return {uid: (d.kind, d.description, d.part_number, d.software_version)
            for uid, d in known_devices.load().items()}


def test_opening_an_mtp_device_remembers_it(watch_and_drives, monkeypatch):
    """D3: a session on an MTP device (window, background service): its entry is written."""
    from openexpress import service

    monitor_with(monkeypatch)
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    service.open_session(allow_ums=True)
    assert remembered() == {1234567890: ("mtp", "Test watch", "006-B0000-00", "23.39")}


def test_opening_a_garmin_drive_remembers_it(watch_and_drives, monkeypatch):
    """D3: the same for a Garmin drive, of kind ums."""
    from openexpress import service

    monitor_with(monkeypatch, watch_and_drives.drive())
    service.open_session(allow_ums=True)
    assert remembered() == {3000000003: ("ums", "Varia RTL515", "006-B3592-03", "3.36")}


def test_the_command_line_remembers_a_device_but_not_a_folder(watch_and_drives, monkeypatch,
                                                              device_root):
    """D3: the command line opens devices without open_session; a device found is
    remembered, a folder given with --path (e.g. a copy) is not."""
    import types

    from openexpress import cli

    monitor_with(monkeypatch)
    args = types.SimpleNamespace(path=str(device_root), device_uri=None, no_wait=True,
                                 apply=False, cmd="info")
    cli.open_device(args)
    assert remembered() == {}
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    cli.open_device(types.SimpleNamespace(**{**vars(args), "path": None}))
    assert remembered() == {1234567890: ("mtp", "Test watch", "006-B0000-00", "23.39")}


def test_the_list_remembers_each_device_with_details(choosing_window, monkeypatch, tmp_path,
                                                     device_root):
    """D3: the list to choose from remembers every entry whose GarminDevice.xml was read,
    before anything is chosen."""
    win = choosing_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    assert win.session is None and len(win._choices) == 2
    assert remembered() == {1234567890: ("mtp", "Test watch", "006-B0000-00", "23.39"),
                            3000000002: ("mtp", "Edge 1050", "006-B4440-00", "33.11")}


def test_an_entry_without_details_is_not_remembered(choosing_window, monkeypatch, tmp_path,
                                                    device_root):
    """D3: an Edge whose volume is not mounted has no unit ID read: only the test watch is
    remembered."""
    win = choosing_window
    monitor_with(monkeypatch)
    edge = two_watches(win.world, monkeypatch, tmp_path, device_root)
    edge.mounted = False
    usb_device(win.world.root, "1-2", 1, 10, "091e", "5158")
    win.refresh_device()
    assert len(win._choices) == 2
    assert remembered() == {1234567890: ("mtp", "Test watch", "006-B0000-00", "23.39")}


def test_a_failed_remembering_does_not_stop_the_detection(choosing_window, monkeypatch,
                                                          tmp_path, device_root, caplog):
    """D3: devices.json cannot be written (e.g. the disk is full): said in the log; the device
    opens all the same, and the list is shown all the same."""
    import errno

    from openexpress import paths, service
    from openexpress.settings import Settings

    def full(path, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    Settings.load()  # writes settings.json on a new profile: before the disk is "full"
    monkeypatch.setattr(paths, "write_private", full)
    monitor_with(monkeypatch)
    watch_and_drives = choosing_window.world
    watch_and_drives.plug("51b4")
    watch_and_drives.offer()
    with caplog.at_level(logging.INFO, logger="openexpress"):
        s = service.open_session(allow_ums=True)
        two_watches(watch_and_drives, monkeypatch, tmp_path, device_root)
        choosing_window.refresh_device()
    assert s.device.description == "Test watch"
    assert len(choosing_window._choices) == 2
    assert "devices: not remembered (OSError)" in caplog.text
    assert remembered() == {}


# --- installing an update on a Garmin drive (step 7) ----------------------------------------

VARIA_UPDATE = b"GCD\x00" + bytes(range(256)) * 40  # made up; not an RGN file
BIKE_ACCESSORY_TEXT = (
    "Unplug the device and turn it on — it installs the update on its own and won't work for "
    "a short while. The lights flash and may briefly go dark during the install; when it's "
    "done, a light flashes green and the main light comes on.")


def varia_answer(data=VARIA_UPDATE):
    """The update server's answer (keys as GarminClient returns them): one update of the
    RTL515's own software, at the place its GarminDevice.xml names (GARMIN/GUPDATE.GCD)."""
    return {"updates": [{
        "partnumber": "006-B3592-03", "productname": "Varia RTL515", "displayversion": "3.40",
        "severity": "Recommended", "releasenotes": [], "eulaurl": "",
        "deliverable": {"securedownloadurl": "https://download.example/rtl515.gcd",
                        "md5": hashlib.md5(data).hexdigest(), "sizeinbytes": len(data)},
        "installation": {"isreinstall": False, "path": "GARMIN", "filename": "GUPDATE.GCD",
                         "isrestartrequired": False, "order": 0}}]}


def downloaded_from_nowhere(monkeypatch, data=VARIA_UPDATE):
    """The download writes `data` to the cache, as a finished download would."""
    from openexpress import updates

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    monkeypatch.setattr(updates.download, "fetch", fetch)


def drive_tree(root: Path) -> dict[str, int | None]:
    """Every file (its size) and folder (None) below a drive's folder."""
    return {p.relative_to(root).as_posix(): (p.stat().st_size if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


def record_fsyncs(monkeypatch) -> list[str]:
    """What os.fsync is called on, as paths (Linux: /proc/self/fd)."""
    synced, real = [], os.fsync

    def fsync(fd):
        synced.append(os.readlink(f"/proc/self/fd/{fd}"))
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    return synced


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_an_update_written_to_a_drive_is_on_the_disk_before_it_counts(drive_window, monkeypatch,
                                                                      tmp_path):
    """Test 1: the file goes where GarminDevice.xml says, with its content, under a temporary
    name until it is complete; the file and then its folder are synced (a Varia resets its
    USB when the cable goes out, so only what is on the disk counts). Progress as it goes."""
    from openexpress.device import storage

    s = drive_session(drive_window, monkeypatch)
    garmin = s.storage.root / "GARMIN"
    local = tmp_path / "rtl515.gcd"
    local.write_bytes(VARIA_UPDATE)
    monkeypatch.setattr(storage, "COPY_CHUNK", 4096, raising=False)
    synced, steps = record_fsyncs(monkeypatch), []
    real = s.storage.write_file("GARMIN/GUPDATE.GCD", local,
                                lambda done, total: steps.append((done, total)))
    assert real == "GARMIN/GUPDATE.GCD"
    assert (garmin / "GUPDATE.GCD").read_bytes() == VARIA_UPDATE
    # The file (still under its temporary name) first, then the folder with the new name.
    assert len(synced) == 2 and Path(synced[0]).parent == garmin and synced[1] == str(garmin)
    assert sorted(p.name for p in garmin.iterdir()) == ["GUPDATE.GCD", "GarminDevice.xml"]
    size = len(VARIA_UPDATE)
    assert len(steps) == 3 and steps[-1] == (size, size)  # 10 kB in 4 kB steps


@pytest.mark.needs_display
@pytest.mark.parametrize("segment, notes", [
    ("bikeaccessory", [("Done", "Update saved. You can unplug the cable."),
                       ("Finish the update", BIKE_ACCESSORY_TEXT)]),
    ("fitness", [("Done", "Update saved. You can unplug the cable.")]),
])
def test_window_installs_an_update_on_a_garmin_drive(drive_window, monkeypatch, segment, notes):
    """D3 lifted: the window asks, then copies; exactly one file comes to the drive. It says
    the cable can be unplugged (a Varia is not worth ejecting), and for a bike accessory, as
    Garmin Express does, how the device finishes the update."""
    from gi.repository import Adw

    from openexpress import updates
    from openexpress.gui.app import MainWindow

    s = drive_session(drive_window, monkeypatch)
    asked = []
    s.client = types.SimpleNamespace(
        http=None, unit_info=lambda unit, part: asked.append((unit, part)) or {
            "segmenttype": segment, "graphicurl": "https://res.garmin.com/rtl515.png"})
    downloaded_from_nowhere(monkeypatch)
    items = updates.mark_present(s.storage, updates.parse_updates(varia_answer()))
    dialogs, toasts = [], []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: dialogs.append(self))
    win = types.SimpleNamespace(session=s, updates_cache=items, updates_beta=False,
                                toast=toasts.append, progress=lambda text: None, fraction=None,
                                _show_installed=lambda session, chosen: None,
                                run_task=lambda title, work, done, failed=None, device=True:
                                done(work()))
    win.error = types.MethodType(MainWindow.error, win)
    before = drive_tree(s.storage.root)
    MainWindow.confirm_update(win, items[0])
    assert toasts == [] and len(dialogs) == 1  # asked first, as for an MTP device
    dialogs[0].emit("response", "ok")
    after = drive_tree(s.storage.root)
    assert {k: v for k, v in after.items() if before.get(k, -1) != v} == {
        "GARMIN/GUPDATE.GCD": len(VARIA_UPDATE)}
    assert set(before) <= set(after)  # nothing went away
    assert (s.storage.root / "GARMIN" / "GUPDATE.GCD").read_bytes() == VARIA_UPDATE
    # The second note comes when the first is closed.
    shown = [(dialogs[1].get_heading(), dialogs[1].get_body())]
    dialogs[1].emit("response", "ok")
    shown += [(d.get_heading(), d.get_body()) for d in dialogs[2:]]
    assert shown == notes
    assert asked == [(3000000003, "006-B3592-03")]  # one question to the update server


def test_a_failed_write_to_a_drive_says_what_failed_and_leaves_no_half_file(drive_window,
                                                                            monkeypatch):
    """Test 2: the drive runs out of space in the middle of the copy. The error names the file
    and the reason; neither half a GUPDATE.GCD nor a temporary file stays on the drive."""
    import errno

    from openexpress import updates
    from openexpress.device.storage import DeviceError

    s = drive_session(drive_window, monkeypatch)
    downloaded_from_nowhere(monkeypatch)
    items = updates.mark_present(s.storage, updates.parse_updates(varia_answer()))
    before = drive_tree(s.storage.root)
    real_write, writes = os.write, []

    def write(fd, data):
        if writes:
            raise OSError(errno.ENOSPC, "No space left on device")
        writes.append(fd)
        return real_write(fd, bytes(data[:len(data) // 2]))  # half of it went on the drive

    monkeypatch.setattr(os, "write", write)
    with pytest.raises(DeviceError) as error:
        updates.install(items, s.storage)
    monkeypatch.undo()
    assert str(error.value) == "Cannot write GARMIN/GUPDATE.GCD: No space left on device"
    assert writes  # the copy had begun
    assert drive_tree(s.storage.root) == before


def _recording_page(key):
    """Stands in for a Section: records what the page is made to show."""
    seen = {}
    error = types.SimpleNamespace(set_title=lambda title: seen.update(title=title),
                                  set_description=lambda text: seen.update(description=text))
    return types.SimpleNamespace(key=key, title=key.title(), loaded=False, error=error, seen=seen,
                                 clear=lambda: seen.update(cleared=True),
                                 show=lambda name: seen.update(shown=name))


@pytest.mark.parametrize("force", [False, True])  # True: "Try Again"
def test_pages_not_available_for_a_garmin_drive(drive_window, monkeypatch, force):
    """Test 11 (D5): Maps, Apps and Music load nothing for a drive (no request, no "Internal
    error") and say in one sentence why; the Updates page loads as for any device."""
    from openexpress.gui.app import MainWindow

    loaded = []
    keys = ("updates", "maps", "apps", "music")
    win = types.SimpleNamespace(session=drive_session(drive_window, monkeypatch),
                                sections={key: _recording_page(key) for key in keys},
                                refresh_device=lambda: loaded.append("refresh"))
    for key in keys:
        setattr(win, f"load_{key}", lambda key=key: loaded.append(key))
    for key in keys:
        MainWindow.load_section(win, key, force=force)
    assert loaded == ["updates"]
    for key in ("maps", "apps", "music"):
        seen = win.sections[key].seen
        assert seen == {"cleared": True, "title": key.title(), "description": MASS_STORAGE_TEXT,
                        "shown": "error"}
        assert seen["description"] != gio_storage.not_ready_text(gio_storage.MASS_STORAGE)
    # Another device: the pages load as before.
    win.session.storage.mass_storage = False
    loaded.clear()
    for key in keys:
        MainWindow.load_section(win, key, force=force)
    assert loaded == list(keys)


@pytest.mark.parametrize("logged_in", [False, True])
def test_sync_is_grey_for_a_garmin_drive(drive_window, monkeypatch, logged_in):
    """With its reason in the tooltip, the words of step 1; and no sync starts anyway."""
    from openexpress.gui.app import MainWindow

    s = drive_session(drive_window, monkeypatch)
    if logged_in:
        s.client.auth = object()
    win = _sync_window(s)
    win.update()
    assert _looks(win) == (False, False, "Mass storage mode", "Mass storage mode")
    started = []
    win.run_task = lambda *a, **kw: started.append(a)
    MainWindow.start_sync(win)
    assert started == []


@pytest.mark.parametrize("logged_in", [False, True])
def test_no_banner_for_a_garmin_drive(drive_window, monkeypatch, logged_in):
    """Neither "Not logged in" nor the one-time setup: neither is needed for a drive."""
    from openexpress.gui.app import MainWindow

    s = drive_session(drive_window, monkeypatch)
    if logged_in:
        s.client.auth = object()
    seen = {}
    win = types.SimpleNamespace(session=s, _ownership_title=MainWindow._ownership_title,
                                banner=types.SimpleNamespace(
        set_title=lambda t: seen.update(title=t),
        set_button_label=lambda b: seen.update(button=b),
        set_revealed=lambda r: seen.update(revealed=r)))
    MainWindow._update_banner(win)
    assert seen == {"revealed": False}


# --- the device to ourselves for a PTP session ----------------------------------------------


@pytest.fixture
def exclusive(tmp_path, monkeypatch):
    """A storage of a mounted Garmin device; unmounting, mounting and USB are recorded.
    The device stays on USB (a made-up /sys) unless a test unplugs it."""
    from ptp_fake import FakeDevice

    from openexpress.device import ptp

    events = []
    fake = FakeDevice()
    usb_device(tmp_path / "sys", "3-1", 3, 14, "091e", "51b4")
    monkeypatch.setattr(gio_storage, "SYSFS_USB", tmp_path / "sys")
    monkeypatch.setattr(gio_storage, "_quiet_usb", {})
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


# --- a Flatpak with the --device=usb permission ------------------------------------------


def node(usb_nodes, bus=3, address=14, mode=0o600):
    path = usb_nodes / f"{bus:03d}" / f"{address:03d}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.chmod(mode)
    return path


def test_the_usb_node_of_a_device(usb_nodes):
    usb = gio_storage.GarminUsb("51b4", "usb", 3, 14)
    assert gio_storage.usb_node(usb) is None  # not there: no access
    path = node(usb_nodes)
    assert gio_storage.usb_node(usb) == path and path.name == "014" and path.parent.name == "003"
    assert gio_storage.usb_node(gio_storage.GarminUsb("51b4", "uri")) is None
    assert gio_storage.usb_node(None) is None


def test_flatpak_without_access_to_usb_stays_as_before(exclusive, monkeypatch):
    st, _fake, events = exclusive
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    assert st.usb_blocked() and st.exclusive_ptp() is None
    assert events == []  # nothing unmounted


@pytest.mark.skipif(os.geteuid() == 0, reason="root may write any file")
def test_flatpak_with_a_node_it_may_not_write_stays_as_before(exclusive, usb_nodes,
                                                               monkeypatch):
    st, _fake, events = exclusive
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    node(usb_nodes, mode=0o400)
    assert st.usb_blocked() and st.exclusive_ptp() is None and events == []


def test_flatpak_with_access_to_usb_opens_the_device_through_its_node(exclusive, usb_nodes,
                                                                     monkeypatch):
    from openexpress.device import ptp

    st, fake, events = exclusive
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    path = node(usb_nodes)
    monkeypatch.setattr(ptp.LibusbTransport, "open_node",
                        classmethod(lambda cls, p: events.append(("node", p)) or fake))
    assert not st.usb_blocked()
    with st.exclusive_ptp() as session:
        assert session is not None and fake.session_open
        assert events == [("unmount", NUMERIC), ("node", path)]  # not by scanning USB
    assert events[-1] == ("mount", NUMERIC, False)


def test_the_version_from_source_still_opens_the_device_by_its_place_on_usb(exclusive,
                                                                           usb_nodes):
    st, _fake, events = exclusive
    node(usb_nodes)
    assert not st.usb_blocked()
    with st.exclusive_ptp():
        assert events == [("unmount", NUMERIC), ("usb", 3, 14)]


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


def test_an_unplug_right_after_our_own_session_is_an_unplug(exclusive, monkeypatch, caplog):
    import shutil

    from openexpress import watch
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    st, _fake, _events = exclusive
    with st.exclusive_ptp():
        pass
    assert gio_storage.own_remount(FakeMount(NUMERIC))  # still at its place: our own doing
    sysfs = gio_storage.SYSFS_USB
    shutil.rmtree(sysfs / "3-1")  # unplugged, as the user is told to after an install
    shown = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(storage=types.SimpleNamespace(label=NUMERIC + "Internal")),
        busy=False, show_device=lambda: shown.append(1), _updated_apps={"waiting"})
    with caplog.at_level(logging.INFO):
        MainWindow._on_mount_removed(win, None, FakeMount(NUMERIC))
    assert win.session is None and shown == [1] and win._updated_apps == set()
    assert "device: unplugged after our own PTP session" in caplog.text
    assert "the open device, showing no device" in caplog.text

    # Plugged in again a moment later, at a new place: the window and the watcher see it.
    usb_device(sysfs, "3-1", 3, 15, "091e", "51b4")
    timers, started = [], []
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: timers.append(ms))
    monkeypatch.setattr(watch.GLib, "timeout_add_seconds", lambda delay, fn: started.append(fn))
    monkeypatch.setattr(watch, "garmin_usb", lambda mount: True)
    monkeypatch.setattr(app, "garmin_usb", lambda mount: True)
    MainWindow._on_mount_added(win, None, FakeMount(NUMERIC))
    watch.Watcher().on_mount(None, FakeMount(NUMERIC))
    assert timers == [1500] and len(started) == 1


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


def mass_storage_device(root, name="1-4", product="20ee", classes=("08",)):
    """A Garmin device in mass storage mode, like the Varia RTL515 (091e:20ee). As in /sys,
    its interfaces are folders "<name>:<configuration>.<number>" with their class (None: a
    folder without it)."""
    d = usb_device(root, name, 1, 12, "091e", product)
    for number, cls in enumerate(classes):
        interface = d / f"{name}:1.{number}"
        interface.mkdir()
        if cls is not None:
            (interface / "bInterfaceClass").write_text(f"{cls}\n")
    return d


def test_usb_state_ignores_a_garmin_in_mass_storage_mode(plugged):
    """A Garmin device whose interfaces are all mass storage (class 08) is no MTP device on
    its way: there is nothing to wait for. A watch next to it is waited for as before."""
    mass_storage_device(plugged.root)
    assert gio_storage.usb_state() != gio_storage.PRESENT
    assert gio_storage.usb_state() == gio_storage.MASS_STORAGE
    assert gio_storage.garmin_products_on_usb() == ["20ee"]  # still named in the log
    plugged.plug("0003")
    assert gio_storage.usb_state() == gio_storage.STARTING
    plugged.plug("51b4")
    assert gio_storage.usb_state() == gio_storage.PRESENT


@pytest.mark.parametrize("classes", [("08", "ff"), ("06",), ("ff",), (None,), ()])
def test_a_garmin_with_other_interfaces_is_waited_for_as_before(plugged, classes):
    """Not only mass storage, or interfaces whose class cannot be read: as before."""
    mass_storage_device(plugged.root, product="51b4", classes=classes)
    assert gio_storage.usb_state() == gio_storage.PRESENT


def test_cli_does_not_wait_for_a_garmin_in_mass_storage_mode(plugged):
    """Not 45 s of "the device is starting" and then the advice about a PIN: at once, the
    words of the window for a drive that is not mounted, as a notice."""
    mass_storage_device(plugged.root)
    for wait in (True, False):
        with pytest.raises(gio_storage.NotThereYet) as error:
            plugged.open(wait=wait)
        assert plugged.clock.slept == 0 and plugged.notices == []
        assert "PIN" not in str(error.value)
        assert str(error.value) == gio_storage.not_ready_text(gio_storage.MASS_STORAGE)
        assert "--path" not in str(error.value) and plugged.opened == []
        assert isinstance(error.value, gio_storage.MassStorageNotice)


def test_a_watch_is_waited_for_next_to_a_garmin_in_mass_storage_mode(plugged):
    """The fēnix 8 is plugged in while a radar is connected as a USB drive: its short
    absence between the two modes is still no unplug."""
    mass_storage_device(plugged.root)
    plugged.plug("0003")
    plugged.steps[18.5] = lambda: plugged.plug(None)
    plugged.steps[20.0] = lambda: plugged.plug("51b4")
    plugged.steps[22.0] = plugged.offer
    st = plugged.open()
    assert st.uri == NUMERIC and plugged.notices == ["The device is starting…"]
    assert 22 <= plugged.clock.now < 24


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
                                _usb_notice=MainWindow._usb_notice, start=FakeStartPage(),
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


def test_window_tells_a_garmin_in_mass_storage_mode(plugged, monkeypatch):
    """No spinner, no search over MTP and, after the wait, no advice about a PIN: the page
    says at once what the device is."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    shown, refreshed, updates = [], [], []
    section = types.SimpleNamespace(set_empty=lambda *notice: shown.append(notice))
    win = types.SimpleNamespace(session=None, busy=False, sections={"watch": section},
                                _usb_state=gio_storage.ABSENT, _usb_since=0.0, _usb_title="",
                                _usb_notice=MainWindow._usb_notice, start=FakeStartPage(),
                                refresh_device=lambda: refreshed.append(1))
    win._update_actions = lambda: updates.append(win._usb_title)
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: fn())
    mass_storage_device(plugged.root)
    for now in (10, 10 + gio_storage.WAIT_FOR_DEVICE_S + 1):
        plugged.clock.now = now
        MainWindow._poll_usb(win, clock=plugged.clock)
        assert refreshed == []  # nothing to look for over MTP
        assert shown[-1][2] is False and "PIN" not in shown[-1][1]
        # The window opens such a drive once it is mounted (step 5c): no advice about --path.
        assert shown[-1] == ("Mass storage mode", DRIVE_NOT_MOUNTED_TEXT, False)
        assert "--path" not in shown[-1][1]
    assert updates == ["Mass storage mode"]  # the Sync button says why, told once


def test_the_flatpak_window_without_a_hidden_drive_speaks_as_from_source(plugged, monkeypatch):
    """The Flatpak reaches /run/media: with no drive out of its sight, the words of the
    version from source, without advice about --path."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(app.paths, "in_flatpak", lambda: True)
    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    monitor_with(monkeypatch)  # nothing mounted
    mass_storage_device(plugged.root)
    notice = MainWindow._usb_notice(gio_storage.MASS_STORAGE, 0)
    assert notice == ("Mass storage mode", DRIVE_NOT_MOUNTED_TEXT, False)
    assert "--path" not in notice[1]


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


# --- the start page and D7 (D5, T2) ------------------------------------------------------------


class FakeStartPage:
    """The start page: what it shows is recorded (name, connected, how; the notice)."""

    def __init__(self):
        self.page = "start page"
        self.shown, self.notices, self.pictures = [], [], []

    def show(self, entries, notice):
        self.shown.append(([(t.description, t.connected, how) for t, how in entries], notice))

    def set_empty(self, title, description, busy):
        self.notices.append((title, description, busy))

    def set_picture(self, part_number, texture):
        self.pictures.append((part_number, texture))


class FakeNavigation:
    """Adw.NavigationView: a stack of pages; popping says so to those listening."""

    def __init__(self, root):
        self.stack, self.listeners = [root], []

    def get_visible_page(self):
        return self.stack[-1]

    def push(self, page):
        self.stack.append(page)

    def pop_to_page(self, page):
        while self.stack[-1] is not page:
            popped = self.stack.pop()
            for fn in self.listeners:
                fn(self, popped)

    def connect(self, signal, fn):
        assert signal == "popped"
        self.listeners.append(fn)

    def set_animate_transitions(self, on):
        pass


class FakeRememberedPage:
    """The read-only view of a remembered device: what it shows is recorded."""

    def __init__(self):
        self.page = "remembered view"
        self.shown = []

    def show(self, **fields):
        self.shown.append(fields)

    def show_icon(self, part_number, icon):
        self.icon = (part_number, icon)

    def set_picture(self, part_number, texture):
        return False


class FakeSection:
    def __init__(self):
        self.loaded, self.state, self.empty = False, None, None

    def clear(self):
        pass

    def show(self, name):
        self.state = name

    def set_empty(self, *notice):
        self.empty = notice


START_METHODS = ("show_device", "show_choices", "_show_start", "_show_device_view", "_is_open",
                 "_show_tiles", "_render_start", "_on_start_page", "_open_tile", "_on_refresh",
                 "_on_popped", "_reread_connected", "_show_remembered", "_fetch_account",
                 "_set_account", "_show_account", "_last_sync_text", "logout", "_start",
                 "_want_account", "_load_tile_pictures", "_tile_picture_done",
                 "_show_page_picture", "_on_remembered_page", "_start_summaries")


def start_stand_in(start, sections, win=None):
    """A stand-in of the window with the real code of the start page and of D7 on a fake
    navigation; the device view's pages are recorded, not built."""
    from openexpress.gui.app import MainWindow

    win = win or types.SimpleNamespace(session=None, busy=False, _choices=None, errors=[])
    win.start, win.device_view = start, "device view"
    win.remembered = FakeRememberedPage()
    win.navigation = FakeNavigation(start.page)
    win.sections = sections
    win.pages = types.SimpleNamespace(get_visible_child_name=lambda: "watch")
    win._update_banner = win._update_actions = lambda: None
    win.overviews = []
    win.show_overview = lambda: win.overviews.append(win.session)
    win.load_section = lambda key: None
    win._poll_usb = lambda: None
    win.in_background = lambda work, done, failed=None: done(work())
    win.toast = win.errors.append
    win._start_targets, win._stay_on_start, win._navigated = [], False, False
    win._connected, win._reopening, win._reread_pending = [], False, False
    win._account, win._account_wanted = None, False
    win._drawn_connected = frozenset()
    for name in START_METHODS:
        if hasattr(MainWindow, name):  # so that on old code the tests fail on what they check
            setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.navigation.connect("popped", lambda nav, page: win._on_popped(nav, page))
    return win


@pytest.fixture
def start_window(choosing_window):
    """The window's detection (choosing_window) with the start page and D7."""
    return start_stand_in(FakeStartPage(), {"watch": FakeSection(), "maps": FakeSection()},
                          choosing_window)


def one_watch(win, monkeypatch):
    monitor_with(monkeypatch)
    win.world.plug("51b4")
    win.world.offer()


def back(win):
    """The back arrow (or Alt+Left, or the swipe): the start page."""
    win.navigation.pop_to_page(win.start.page)


def test_one_device_connected_opens_its_view(start_window, monkeypatch):
    """D7: exactly one device connected: its view at once, as before."""
    win = start_window
    one_watch(win, monkeypatch)
    win.refresh_device()
    assert win.session is not None and win.overviews == [win.session]
    assert win.navigation.stack == ["start page", "device view"]


def test_several_devices_connected_show_the_start_page(start_window, monkeypatch, tmp_path,
                                                       device_root):
    """D7: several devices: the start page with a tile each (connected, how, never the
    address), no device open; the other pages say to choose one."""
    win = start_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    assert win.session is None and win.navigation.stack == ["start page"]
    tiles, notice = win.start.shown[-1]
    assert [(name, on) for name, on, _how in tiles] == [("Test watch", True),
                                                       ("Edge 1050", True)]
    assert all(how.startswith("Over USB (MTP) · Software ") for _n, _on, how in tiles)
    assert notice is False
    assert win.sections["maps"].empty[0] == "Choose a device"


def test_a_tile_opens_its_device_and_back_keeps_it_open(start_window, monkeypatch, tmp_path,
                                                        device_root):
    """A click on a tile opens that device in its view; back on the start page it stays
    open, its tile opens its view again without opening it anew, another tile opens the
    other device."""
    win = start_window
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    names = [name for name, _on, _how in win.start.shown[-1][0]]
    win._open_tile(names.index("Edge 1050"))
    s = win.session
    assert s.device.description == "Edge 1050"
    assert win.navigation.stack == ["start page", "device view"] and win.overviews == [s]

    back(win)
    assert win.session is s and win.navigation.stack == ["start page"]
    tiles, notice = win.start.shown[-1]
    assert [(name, on) for name, on, _how in tiles] == [("Test watch", True),
                                                       ("Edge 1050", True)]
    assert notice is False
    win._open_tile(1)  # the Edge, open already
    assert win.session is s and win.overviews == [s]
    assert win.navigation.stack == ["start page", "device view"]

    back(win)
    win._open_tile(0)
    assert win.session.device.description == "Test watch"
    assert win.navigation.stack == ["start page", "device view"]


def test_unplugging_the_open_device_shows_the_start_page(start_window, monkeypatch):
    """The open device unplugged: the start page, with the notice of the USB state on top
    and no device connected; the one unplugged is remembered (T3)."""
    win = start_window
    one_watch(win, monkeypatch)
    win.refresh_device()
    win.session = None  # as _on_mount_removed does
    win.show_device()
    assert win.navigation.stack == ["start page"]
    tiles, notice = win.start.shown[-1]
    assert [(name, on) for name, on, _how in tiles] == [("Test watch", False)]
    assert notice is True


def test_refresh_on_the_start_page_stays_there(start_window, monkeypatch):
    """Decision 7: Refresh on the start page, one device connected: the start page stays,
    with its tile; Refresh in the device view stays in the device view."""
    win = start_window
    one_watch(win, monkeypatch)
    win.refresh_device()
    back(win)
    win._on_refresh()
    assert win.session is not None and win.navigation.stack == ["start page"]
    tiles, _notice = win.start.shown[-1]
    assert [(name, on) for name, on, _how in tiles] == [("Test watch", True)]
    win._open_tile(0)
    win._on_refresh()
    assert win.navigation.stack == ["start page", "device view"]


def test_the_start_page_tells_the_usb_state(plugged, monkeypatch):
    """With no device open, the start page has the words of the pages on top: no device,
    a device that is starting, or one that is not ready."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    shown = []
    section = types.SimpleNamespace(set_empty=lambda *notice: shown.append(notice))
    start = FakeStartPage()
    win = types.SimpleNamespace(session=None, busy=False, sections={"watch": section},
                                start=start, _usb_state=gio_storage.ABSENT, _usb_since=0.0,
                                _usb_title="", _usb_notice=MainWindow._usb_notice,
                                refresh_device=lambda: None, _update_actions=lambda: None)
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: fn())
    MainWindow._poll_usb(win, clock=plugged.clock)
    assert start.notices[-1] == shown[-1] and shown[-1][0] == "Connect your device"
    usb_device(plugged.root, "1-4", 1, 40, "091e", "0003")  # starting
    MainWindow._poll_usb(win, clock=plugged.clock)
    assert start.notices[-1] == shown[-1] and shown[-1][0] == "The device is starting…"


@pytest.mark.needs_display
def test_a_connected_tile_has_the_accent_frame():
    """The tile of a connected device is framed; one not connected is not."""
    from openexpress.devicelist import Tile
    from openexpress.gui.start import StartPage

    def tile(connected):
        return Tile("fēnix 8", "006-B4532-00", "23.39", "mtp", connected, False, False, None,
                    "006-B4532-00", None, None)

    start = StartPage()
    start.show([(tile(True), "Over USB (MTP) · Software 23.39"), (tile(False), "")], False)
    first, second = start.tiles()
    assert first.has_css_class("connected") and not second.has_css_class("connected")
    assert first.has_css_class("card") and second.has_css_class("card")


@pytest.mark.needs_display
def test_the_window_starts_on_the_start_page_with_one_back_arrow(monkeypatch):
    """The real window: the start page is the root of the navigation and the device view is
    a page above it, whose sidebar header shows the back arrow; the header of its content
    only when folded (there it goes back to the sidebar), so never two arrows."""
    from openexpress import netstatus
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(MainWindow, "refresh_device", lambda self, keep_open=True: None)
    monkeypatch.setattr(app.GLib, "timeout_add_seconds", lambda s, fn: 0)
    win = MainWindow(None)
    try:
        assert win.navigation.get_visible_page() is win.start.page
        assert win.device_view.get_child() is win.split
        assert win.sidebar_header.get_show_back_button() is True
        assert win.content_header.get_show_back_button() is False
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()


# --- the start page with a device open: no flicker, no reopening (live test of T2) -----------


def defer_background(win):
    """Work in the background waits until the test runs it (`run_later`)."""
    later = []
    win.in_background = lambda work, done, failed=None: later.append((work, done))
    return later


def run_later(later):
    while later:
        work, done = later.pop(0)
        done(work())


def drawn_names(win, since=0):
    """The names on the tiles of each drawing of the start page since `since`."""
    return [[name for name, _on, _how in tiles] for tiles, _notice in win.start.shown[since:]]


def two_and_one_open(win, monkeypatch, tmp_path, device_root, name):
    """Two watches connected, `name` chosen on the start page and open, then back on the
    start page with the list drawn; the Edge is on USB at 1-2."""
    monitor_with(monkeypatch)
    edge = two_watches(win.world, monkeypatch, tmp_path, device_root)
    usb_device(win.world.root, "1-2", 1, 10, "091e", "5158")
    win.refresh_device()
    win._open_tile(drawn_names(win)[-1].index(name))
    assert win.session.device.description == name
    return edge


def count_opening(monkeypatch):
    """Each device opened and each account check, from now on."""
    from openexpress import service

    calls = []
    for name in ("open_session", "check_session"):
        real = getattr(service, name)
        monkeypatch.setattr(service, name, lambda *a, _real=real, _name=name, **k: (
            calls.append(_name), _real(*a, **k))[1])
    return calls


def test_back_draws_the_list_at_once_never_one_tile_of_two(start_window, monkeypatch,
                                                           tmp_path, device_root):
    """Live test of T2 (17.10.2026): two devices, back from the view of one: the start page
    is drawn once, at once, with both tiles; never only the open one first, and nothing is
    read again."""
    win = start_window
    later = defer_background(win)
    two_and_one_open(win, monkeypatch, tmp_path, device_root, "Edge 1050")
    drawn = len(win.start.shown)
    back(win)
    assert drawn_names(win, drawn) == [["Test watch", "Edge 1050"]]
    assert later == []


def test_refresh_on_the_start_page_reads_the_list_only(start_window, monkeypatch, tmp_path,
                                                       device_root):
    """Decision A: Refresh on the start page with a device open reads the list again, once,
    in whole: the open device is not opened again, the account not asked, its view not
    rebuilt; the start page stays and shows both tiles all the time."""
    win = start_window
    later = defer_background(win)
    two_and_one_open(win, monkeypatch, tmp_path, device_root, "Edge 1050")
    back(win)
    run_later(later)
    opening, built, drawn = count_opening(monkeypatch), len(win.overviews), len(win.start.shown)
    win._on_refresh()
    run_later(later)
    assert opening == [] and len(win.overviews) == built
    assert win.navigation.stack == ["start page"]
    assert drawn_names(win, drawn) == [["Test watch", "Edge 1050"]]


def test_reading_the_list_again_never_opens_a_device_view(start_window, monkeypatch, tmp_path,
                                                          device_root):
    """Guards D7: the list read again with one device left (the Edge unplugged) shows its
    tile; the start page stays, no device view comes up."""
    win = start_window
    later = defer_background(win)
    edge = two_and_one_open(win, monkeypatch, tmp_path, device_root, "Test watch")
    back(win)
    run_later(later)
    win.world.offered.remove(edge)
    win._on_refresh()
    run_later(later)
    assert win.navigation.stack == ["start page"]
    # The Edge is no longer connected; it is remembered (T3).
    assert [(name, on) for name, on, _how in drawn_tiles(win)] == [
        ("Test watch", True), ("Edge 1050", False)]


def test_another_device_unplugged_while_one_is_open_updates_the_list(start_window, monkeypatch,
                                                                     tmp_path, device_root):
    """K6 with the start page: the test watch open, the start page shown, the Edge unplugged:
    the list is read again and drawn once, with the test watch only; it stays open."""
    from openexpress.gui.app import MainWindow

    win = start_window
    later = defer_background(win)
    edge = two_and_one_open(win, monkeypatch, tmp_path, device_root, "Test watch")
    opened = win.session
    back(win)
    run_later(later)
    drawn = len(win.start.shown)
    win.world.offered.remove(edge)
    for f in (win.world.root / "1-2").iterdir():
        f.unlink()
    (win.world.root / "1-2").rmdir()
    MainWindow._on_mount_removed(win, None, FakeMount(NAMED))
    run_later(later)
    assert win.session is opened and win.navigation.stack == ["start page"]
    # Drawn once: the Edge is no longer connected, it is remembered (T3).
    assert len(win.start.shown) == drawn + 1
    assert [(name, on) for name, on, _how in drawn_tiles(win)] == [
        ("Test watch", True), ("Edge 1050", False)]


def test_a_pending_refresh_stays_on_the_start_page(start_window, monkeypatch, tmp_path,
                                                   device_root):
    """A detection with a device open while the start page is shown (a refresh left over
    from a task, "Try Again" on the banner) keeps the start page: the device view comes up
    only when chosen."""
    win = start_window
    later = defer_background(win)
    two_and_one_open(win, monkeypatch, tmp_path, device_root, "Edge 1050")
    back(win)
    run_later(later)
    win.refresh_device()
    run_later(later)
    assert win.navigation.stack == ["start page"]
    assert drawn_names(win)[-1] == ["Test watch", "Edge 1050"]


# --- opening a device from the start page: nothing undoes the choice (live test of T2) -------


def mounts_reported_at_start(win):
    """gvfs reports the mounts of the devices already connected as the window starts: a
    detection planned for each, in 1.5 s."""
    from openexpress.gui.app import schedule_detection

    schedule_detection(win, 1500, NUMERIC)
    schedule_detection(win, 1500, NAMED)


@pytest.mark.parametrize("when", ["after the choice", "during the opening"])
def test_a_detection_planned_before_the_choice_leaves_the_device_open(
        start_window, monkeypatch, tmp_path, device_root, when):
    """Live test of T2 (17.10.2026): two devices on the start page, the mounts of both
    reported at the start; the Edge is clicked. The detection planned before the click finds
    only devices already on the list: the Edge stays open, its view stays up, and the start
    page below is not drawn again (the window used to close the Edge, go back and draw the
    tiles anew, whose first frames showed empty under the animation)."""
    from openexpress.gui.app import MainWindow

    win = start_window
    later = defer_background(win)
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    usb_device(win.world.root, "1-2", 1, 10, "091e", "5158")  # the Edge on USB
    win.refresh_device()
    mounts_reported_at_start(win)
    drawn = len(win.start.shown)
    opening = count_opening(monkeypatch)
    if when == "during the opening":
        # The detection is due while the device is being opened: after that task.
        real_run_task = win.run_task

        def run_task(title, fn, done, failed):
            win.busy = True
            win.timers.fire()
            win.busy = False
            real_run_task(title, fn, done, failed)
            MainWindow._run_pending_refresh(win)

        win.run_task = run_task
    win._open_tile(drawn_names(win)[-1].index("Edge 1050"))
    win.timers.fire()
    run_later(later)
    assert win.session is not None and win.session.device.description == "Edge 1050"
    assert win.navigation.stack == ["start page", "device view"]
    assert len(win.start.shown) == drawn  # not drawn again
    assert opening == ["open_session", "check_session"]  # the Edge, once


def test_a_device_mounted_after_the_list_still_brings_the_list(start_window, monkeypatch,
                                                              tmp_path, device_root):
    """Guards D7 (eaaa05b): a device that was not on the list is mounted while one is open:
    the list to choose from, as before."""
    win = start_window
    one_watch(win, monkeypatch)
    win.refresh_device()
    assert win.navigation.stack == ["start page", "device view"]
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    from openexpress.gui.app import schedule_detection

    schedule_detection(win, 1500, NAMED)
    win.timers.fire()
    assert win.session is None and win.navigation.stack == ["start page"]
    assert drawn_names(win)[-1] == ["Test watch", "Edge 1050"]


@pytest.mark.needs_display
def test_the_same_list_keeps_its_tiles():
    """The start page given the same tiles again keeps the widgets it has (nothing removed
    and added); other tiles are built anew."""
    from openexpress.devicelist import Tile
    from openexpress.gui.start import StartPage

    def tile(name):
        return Tile(name, "006-B4532-00", "23.39", "mtp", True, False, False, None,
                    "006-B4532-00", None, None)

    entries = [(tile("fēnix 8"), "Over USB (MTP) · Software 23.39"),
               (tile("Edge 1050"), "Over USB (MTP) · Software 33.12")]
    start = StartPage()
    start.show(entries, False)
    before = start.tiles()
    start.show(list(entries), False)
    assert all(a is b for a, b in zip(before, start.tiles(), strict=True))
    start.show(entries[:1], False)
    assert len(start.tiles()) == 1 and start.tiles()[0] is not before[0]


# --- remembered devices on the start page (D5, T3) ---------------------------------------------

TEST_WATCH = 1234567890  # the <Id> of conftest's test watch (made up)
VARIA, VENU = 3000000003, 3000000004
DAY = 86400.0


def remember_others(now):
    """The test watch (seen long ago), a Varia (seen yesterday) and a Venu 2 (three days ago)
    remembered by this installation."""
    from openexpress import known_devices

    known_devices.remember(TEST_WATCH, "Test watch", "006-B0000-00", "1.00", "mtp",
                           now=now - 9 * DAY)
    known_devices.remember(VARIA, "Varia RTL515", "006-B3592-03", "3.36", "ums", now=now - DAY)
    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", now=now - 3 * DAY)


def drawn_tiles(win):
    """(name, connected, how) of each tile of the last drawing of the start page."""
    return win.start.shown[-1][0]


def nothing_connected(win, monkeypatch):
    """No Garmin device on USB: the start page with the notice and what is remembered."""
    monitor_with(monkeypatch)
    win.refresh_device()
    assert win.session is None and win.navigation.stack == ["start page"]


def test_the_start_page_lists_remembered_devices_after_the_connected(start_window, monkeypatch,
                                                                     tmp_path, device_root):
    """T3: the devices remembered and not connected come after the connected ones, the one
    seen last first; one both connected and remembered is listed once, as connected. A
    remembered tile says its version and when it was seen; never a unit ID."""
    import time

    win = start_window
    remember_others(time.time())
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    tiles = drawn_tiles(win)
    assert [(name, on) for name, on, _how in tiles] == [
        ("Test watch", True), ("Edge 1050", True), ("Varia RTL515", False), ("Venu 2", False)]
    assert tiles[2][2].startswith("Software 3.36 · Last seen ")
    assert tiles[3][2].startswith("Software 19.05 · Last seen ")
    assert not any(str(u) in how for _n, _on, how in tiles
                   for u in (TEST_WATCH, VARIA, VENU, 3000000002))


def test_a_remembered_tile_opens_a_read_only_view(start_window, monkeypatch):
    """T3: a remembered device that is not connected opens its read-only view: its name,
    version, when it was seen and its last sync, as remembered. Nothing is opened, nothing
    is read from USB, nothing is asked of a server; back is the start page."""
    import time

    from openexpress import service
    from openexpress.gui import app

    win = start_window
    remember_others(time.time())
    service.record_sync(VENU, uploaded=2, downloaded=1, skipped_known=0, errors=[], lines=[])
    nothing_connected(win, monkeypatch)
    names = [name for name, _on, _how in drawn_tiles(win)]
    assert names == ["Test watch", "Varia RTL515", "Venu 2"]
    opening = count_opening(monkeypatch)

    def no_device(*_a, **_k):
        raise AssertionError("a device was looked for")

    for name in ("find_all_devices", "probe", "usb_state", "usb_still_there"):
        monkeypatch.setattr(app, name, no_device)
    monkeypatch.setattr(app.service, "make_client", no_device)
    win._open_tile(names.index("Venu 2"))
    assert win.navigation.stack == ["start page", "remembered view"]
    assert opening == [] and win.session is None
    venu = win.remembered.shown[-1]
    assert (venu["name"], venu["version"], venu["part_number"]) == (
        "Venu 2", "Software 19.05", "006-B3703-00")
    assert venu["last_seen"].startswith("Last seen ")
    assert venu["last_sync"] != "Not synced yet with openExpress" and "2" in venu["last_sync"]
    back(win)
    assert win.navigation.stack == ["start page"]
    win._open_tile(names.index("Varia RTL515"))
    assert win.remembered.shown[-1]["last_sync"] == "Not synced yet with openExpress"
    assert not any(str(u) in str(f) for f in win.remembered.shown for u in (VARIA, VENU))


def test_a_connected_tile_still_opens_its_device(start_window, monkeypatch, tmp_path,
                                                 device_root):
    """Guards T2: with remembered devices on the list, a connected tile opens that device,
    a remembered one opens nothing."""
    import time

    win = start_window
    remember_others(time.time())
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    names = [name for name, _on, _how in drawn_tiles(win)]
    win._open_tile(names.index("Edge 1050"))
    assert win.session.device.description == "Edge 1050"
    assert win.navigation.stack == ["start page", "device view"]


def test_a_device_plugged_in_while_a_remembered_one_is_shown_opens_its_view(start_window,
                                                                            monkeypatch):
    """D7 also from the read-only view: one device plugged in and detected: its view."""
    import time

    win = start_window
    remember_others(time.time())
    nothing_connected(win, monkeypatch)
    win._open_tile(0)
    assert win.navigation.stack == ["start page", "remembered view"]
    one_watch(win, monkeypatch)
    win.refresh_device()
    assert win.session is not None
    assert win.navigation.stack == ["start page", "device view"]


def test_a_detection_that_still_finds_nothing_keeps_the_remembered_view(start_window,
                                                                        monkeypatch):
    """A detection that ends with no device, as before it: the read-only view of the
    remembered device stays; the start page under it is drawn anew."""
    import time

    win = start_window
    remember_others(time.time())
    nothing_connected(win, monkeypatch)
    names = [name for name, _on, _how in drawn_tiles(win)]
    win._open_tile(names.index("Venu 2"))
    drawings = len(win.start.shown)
    win.refresh_device()
    assert win.session is None
    assert win.navigation.stack == ["start page", "remembered view"]
    assert len(win.start.shown) == drawings + 1
    assert [name for name, _on, _how in drawn_tiles(win)] == names


def test_a_detection_that_finds_the_same_devices_keeps_the_remembered_view(start_window,
                                                                           monkeypatch,
                                                                           tmp_path,
                                                                           device_root):
    """Two devices listed; a second detection (e.g. after gvfs tells of the mount of one of
    them) finds the same two: the remembered view stays, the tiles are drawn under it."""
    import time

    win = start_window
    remember_others(time.time())
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    names = [name for name, _on, _how in drawn_tiles(win)]
    win._open_tile(names.index("Venu 2"))
    assert win.navigation.stack == ["start page", "remembered view"]
    drawings = len(win.start.shown)
    win.refresh_device()
    assert win.session is None
    assert win.navigation.stack == ["start page", "remembered view"]
    assert len(win.start.shown) == drawings + 1
    assert [name for name, _on, _how in drawn_tiles(win)] == names


def test_the_account_list_arriving_keeps_the_remembered_view(start_window, monkeypatch):
    """The account's list read while a remembered device is shown: its view stays; the start
    page under it gets the account's tile."""
    import time

    win = start_window
    remember_others(time.time())
    later = defer_background(win)
    with_account(monkeypatch, FakeAccountClient())
    nothing_connected(win, monkeypatch)
    run_later(later)
    names = [name for name, _on, _how in drawn_tiles(win)]
    assert "Forerunner 965" not in names
    win._open_tile(names.index("Venu 2"))
    win._fetch_account()
    run_later(later)
    assert win.navigation.stack == ["start page", "remembered view"]
    assert "Forerunner 965" in [name for name, _on, _how in drawn_tiles(win)]


def test_devices_plugged_in_while_a_remembered_one_is_shown_give_the_start_page(
        start_window, monkeypatch, tmp_path, device_root):
    """Guards D7: something did change (two devices came): the start page with their
    tiles, as before."""
    import time

    win = start_window
    remember_others(time.time())
    nothing_connected(win, monkeypatch)
    win._open_tile(0)
    assert win.navigation.stack == ["start page", "remembered view"]
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    assert win.session is None and win.navigation.stack == ["start page"]
    assert [(name, on) for name, on, _how in drawn_tiles(win)][:2] == [
        ("Test watch", True), ("Edge 1050", True)]


def test_the_open_device_unplugged_while_a_remembered_one_is_shown_gives_the_start_page(
        start_window, monkeypatch):
    """Guards: the open device gone is a change: the start page, as before."""
    import time

    win = start_window
    remember_others(time.time())
    one_watch(win, monkeypatch)
    win.refresh_device()
    back(win)
    names = [name for name, _on, _how in drawn_tiles(win)]
    win._open_tile(names.index("Venu 2"))
    assert win.navigation.stack == ["start page", "remembered view"]
    win.session = None  # as _on_mount_removed does
    win.show_device()
    assert win.navigation.stack == ["start page"]


@pytest.mark.needs_display
def test_the_remembered_section_only_with_remembered_devices():
    """The section "Remembered" with its tiles (unframed) only when there are some; the
    connected ones above it."""
    from openexpress.devicelist import Tile
    from openexpress.gui.start import StartPage

    def tile(name, connected):
        return Tile(name, "006-B4532-00", "23.39", "mtp", connected, False, False,
                    None if connected else 1.0, "006-B4532-00", None, None)

    start = StartPage()
    start.show([(tile("fēnix 8", True), "Over USB (MTP) · Software 23.39")], False)
    assert start.connected_title.get_visible()
    assert not start.remembered_title.get_visible()
    start.show([(tile("fēnix 8", True), "x"), (tile("Venu 2", False), "y")], False)
    assert start.remembered_title.get_visible() and start.remembered_title.get_label() == (
        "Remembered")
    connected, remembered = start.tiles()
    assert connected.get_parent() is start.grid
    assert remembered.get_parent() is start.remembered_grid
    assert connected.has_css_class("connected") and not remembered.has_css_class("connected")
    start.show([(tile("Venu 2", False), "y")], True)
    assert not start.connected_title.get_visible() and start.remembered_title.get_visible()


@pytest.mark.needs_display
def test_the_read_only_view_offers_no_action():
    """The read-only view: the banner says the device is not connected; Sync and the rows of
    updates and maps cannot be used."""
    from openexpress.gui.start import RememberedPage

    view = RememberedPage()
    view.show(name="Venu 2", version="Software 19.05", part_number="006-B3703-00",
              last_seen="Last seen 2026-10-17 14:27", last_sync="Not synced yet with openExpress")
    assert view.banner.get_revealed()
    assert view.banner.get_title() == ("Device not connected — connect it to sync or update")
    assert not view.sync_button.get_sensitive()
    assert not view.update_row.get_sensitive() and not view.map_row.get_sensitive()
    assert view.page.get_title() == "Venu 2"


# --- devices of the Garmin account on the start page (D5, T4) ---------------------------------

FORERUNNER = 3000000005
ACCOUNT_LIST = [  # as GET device-service/deviceregistration/devices gives it (made up)
    {"unitId": 3000000002, "productDisplayName": "Edge 1050", "partNumber": "006-B4440-00",
     "currentFirmwareVersion": "33.12", "primaryActivityTrackerIndicator": False},
    {"unitId": VENU, "productDisplayName": "Venu 2", "partNumber": "006-B3703-00",
     "currentFirmwareVersion": "19.05", "primaryActivityTrackerIndicator": False},
    {"unitId": FORERUNNER, "productDisplayName": "Forerunner 965",
     "partNumber": "006-B4315-00", "currentFirmwareVersion": "22.26",
     "primaryActivityTrackerIndicator": True}]


class FakeAccountClient:
    """The client of service.make_client: logged in or not; its device list or an error.
    No request leaves the test."""

    def __init__(self, logged_in=True, error=None):
        self.auth = object() if logged_in else None
        self.error, self.asked = error, 0

    def account_devices(self):
        from openexpress.connect import lower_keys

        self.asked += 1
        if self.error is not None:
            raise self.error
        return lower_keys(ACCOUNT_LIST)


def with_account(monkeypatch, client):
    """The client the start page asks for the account's devices (opening a device keeps
    its own)."""
    from openexpress.gui import app

    monkeypatch.setattr(app, "account_client", lambda: client)
    return client


def sections(win, since=-1):
    """([(name, connected)], the tiles) of one drawing of the start page."""
    tiles = win.start.shown[since][0]
    return [(name, on) for name, on, _how in tiles], tiles


def test_the_account_list_comes_after_the_first_drawing_with_one_more(start_window,
                                                                      monkeypatch, tmp_path,
                                                                      device_root):
    """T4: the start page is drawn at once with the connected and remembered devices; the
    account's list, read in the background, adds its devices that are neither (the
    Forerunner) with exactly one more drawing: the tiles before are the same, no device
    view comes up. A device of the account that is connected or remembered is not
    repeated."""
    import time

    win = start_window
    later = defer_background(win)
    client = with_account(monkeypatch, FakeAccountClient())
    remember_others(time.time())
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    first, first_tiles = sections(win)
    assert first == [("Test watch", True), ("Edge 1050", True), ("Varia RTL515", False),
                     ("Venu 2", False)]
    drawn = len(win.start.shown)
    win._fetch_account()
    assert len(win.start.shown) == drawn  # nothing until the answer
    run_later(later)
    assert client.asked == 1 and len(win.start.shown) == drawn + 1
    now, now_tiles = sections(win)
    assert now == [*first, ("Forerunner 965", False)]
    assert now_tiles[:4] == first_tiles  # the same names and words
    assert now_tiles[4][2] == "Software 22.26 · Not seen on this computer"
    assert win.navigation.stack == ["start page"] and win.session is None
    assert not any(str(u) in how for _n, _on, how in now_tiles for u in (FORERUNNER, VENU))


def test_the_account_list_while_a_device_view_is_shown(start_window, monkeypatch):
    """D7 stays: one device open in its view; the account's list arrives: nothing is drawn
    and the view stays up; back on the start page, its devices are there."""
    win = start_window
    later = defer_background(win)
    with_account(monkeypatch, FakeAccountClient())
    one_watch(win, monkeypatch)
    win.refresh_device()
    assert win.navigation.stack == ["start page", "device view"]
    drawn = len(win.start.shown)
    win._fetch_account()
    run_later(later)
    assert len(win.start.shown) == drawn
    assert win.navigation.stack == ["start page", "device view"]
    back(win)
    names = [name for name, _on in sections(win)[0]]
    assert names == ["Test watch", "Edge 1050", "Forerunner 965", "Venu 2"]  # by name


def test_not_logged_in_there_are_no_account_devices(start_window, monkeypatch, caplog):
    """Not logged in: nothing is asked, no tile of the account; after logging out the
    devices of the account are gone from the start page."""
    import logging

    win = start_window
    later = defer_background(win)
    client = with_account(monkeypatch, FakeAccountClient(logged_in=False))
    nothing_connected(win, monkeypatch)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win._fetch_account()
        run_later(later)
    assert client.asked == 0 and sections(win)[0] == []
    assert "start page: not logged in, no devices of the account" in caplog.text
    with_account(monkeypatch, FakeAccountClient())
    win._fetch_account()
    run_later(later)
    assert [name for name, _on in sections(win)[0]] == ["Edge 1050", "Forerunner 965",
                                                        "Venu 2"]
    win.logout()
    run_later(later)
    assert sections(win)[0] == []


def test_the_account_list_failing_leaves_no_section(start_window, monkeypatch, caplog):
    """A network or server error: no tile of the account, no exception, one line in the
    log (never the answer)."""
    import logging

    from openexpress.connect import ApiError

    win = start_window
    later = defer_background(win)
    win.in_background = lambda work, done, failed=None: later.append(
        (work, done, failed))

    def run_with_errors():
        while later:
            work, done, failed = later.pop(0)
            try:
                result = work()
            except Exception as e:  # noqa: BLE001 - as in_background: to `failed`
                if failed is not None:
                    failed(e)
            else:
                done(result)

    with_account(monkeypatch, FakeAccountClient(error=ApiError("HTTP 503")))
    nothing_connected(win, monkeypatch)
    drawn = len(win.start.shown)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        win._fetch_account()
        run_with_errors()
    assert len(win.start.shown) == drawn and sections(win)[0] == []
    assert "start page: the devices of the account could not be read (ApiError)" in (
        caplog.text)


def test_an_account_tile_opens_a_read_only_view(start_window, monkeypatch):
    """T4: a tile only on the account opens the read-only view with what the account says:
    its name and version; it was never seen here and never synced from here. Nothing is
    opened or read from USB."""
    from openexpress.gui import app

    win = start_window
    later = defer_background(win)
    with_account(monkeypatch, FakeAccountClient())
    nothing_connected(win, monkeypatch)
    win._fetch_account()
    run_later(later)
    opening = count_opening(monkeypatch)

    def no_device(*_a, **_k):
        raise AssertionError("a device was looked for")

    for name in ("find_all_devices", "probe", "usb_state", "usb_still_there"):
        monkeypatch.setattr(app, name, no_device)
    names = [name for name, _on in sections(win)[0]]
    win._open_tile(names.index("Forerunner 965"))
    assert win.navigation.stack == ["start page", "remembered view"]
    assert opening == [] and win.session is None
    assert win.remembered.shown[-1] == {
        "name": "Forerunner 965", "version": "Software 22.26", "part_number": "006-B4315-00",
        "last_seen": "Not seen on this computer",
        "last_sync": "Not synced yet with openExpress"}


@pytest.mark.needs_display
def test_the_account_section_adds_tiles_without_touching_the_others():
    """The section "On the account" comes after the others, only with such tiles; adding it
    keeps the tiles of the other sections (the same widgets)."""
    from openexpress.devicelist import Tile
    from openexpress.gui.start import StartPage

    def tile(name, connected, last_seen, on_account=False):
        return Tile(name, "006-B4532-00", "23.39", "mtp" if last_seen else None, connected,
                    on_account, False, last_seen, "006-B4532-00", None, None)

    first = [(tile("fēnix 8", True, None), "Over USB (MTP)"),
             (tile("Venu 2", False, 1.0), "Software 19.05 · Last seen")]
    start = StartPage()
    start.show(first, False)
    assert not start.account_title.get_visible()
    before = start.tiles()
    # The account's list adds a device and its flags to the connected one.
    start.show([(tile("fēnix 8", True, None, on_account=True), "Over USB (MTP)"), first[1],
                (tile("Forerunner 965", False, None, on_account=True),
                 "Software 22.26 · Not seen on this computer")], False)
    assert start.account_title.get_visible()
    assert start.account_title.get_label() == "On the account"
    after = start.tiles()
    assert after[0] is before[0] and after[1] is before[1] and len(after) == 3
    assert after[2].get_parent() is start.account_grid


# --- the account's list asked once (after T4) ---------------------------------------------------


def logged_in_everywhere(monkeypatch, more=()):
    """Logged in for every client (the start page's and the one opening a device); each
    request for the account's devices is counted and answered here, with the test watch on
    the account. `more`: devices the account lists from the second answer on."""
    from openexpress.auth import store
    from openexpress.connect import GarminClient, lower_keys

    asked, saved = [], [store.ConnectAuth("token", "secret")]

    def load():
        if not saved:
            raise store.NotLoggedIn("logged out")
        return saved[0]

    monkeypatch.setattr(store, "load", load)
    monkeypatch.setattr(store, "delete", saved.clear)  # logging out

    def account_devices(client):
        asked.append(1)
        listed = [{"unitId": TEST_WATCH, "productDisplayName": "Test watch",
                   "partNumber": "006-B0000-00"}, *ACCOUNT_LIST]
        return lower_keys(listed + (list(more) if len(asked) > 1 else []))

    monkeypatch.setattr(GarminClient, "account_devices", account_devices)
    return asked


def names_on_the_start_page(win):
    return [name for name, _on in sections(win)[0]]


def test_one_device_at_the_start_asks_the_account_once(start_window, monkeypatch):
    """The window opens with one device connected and an account logged in: the device is
    opened (D7), whose ownership check reads the account's list; the start page uses that
    list instead of asking again. One request, and the section "On the account" as before."""
    win = start_window
    later = defer_background(win)
    asked = logged_in_everywhere(monkeypatch)
    one_watch(win, monkeypatch)
    win._start()  # what the window does as it opens
    run_later(later)
    assert asked == [1]
    assert win.session.account_devices is not None
    assert win.navigation.stack == ["start page", "device view"]
    back(win)
    assert names_on_the_start_page(win) == ["Test watch", "Edge 1050", "Forerunner 965",
                                            "Venu 2"]
    assert [on for _n, on in sections(win)[0]] == [True, False, False, False]


def test_refresh_on_the_start_page_opening_a_device_asks_once(start_window, monkeypatch):
    """Refresh on the start page with no device open, one plugged in meanwhile: the
    detection opens it (the start page stays, decision 7) and its check reads the account's
    list: one request, not two."""
    win = start_window
    later = defer_background(win)
    asked = logged_in_everywhere(monkeypatch)
    nothing_connected(win, monkeypatch)
    win._fetch_account()
    run_later(later)
    assert asked == [1]
    win.world.plug("51b4")
    win.world.offer()
    win._on_refresh()
    run_later(later)
    assert asked == [1, 1]
    assert win.session is not None and win.navigation.stack == ["start page"]
    assert names_on_the_start_page(win) == ["Test watch", "Edge 1050", "Forerunner 965",
                                            "Venu 2"]


def test_refresh_on_the_start_page_reads_the_account_again(start_window, monkeypatch):
    """Guards T4: with a device open, Refresh on the start page asks the account once more
    and shows what it says now; logging out removes its devices."""
    win = start_window
    later = defer_background(win)
    asked = logged_in_everywhere(monkeypatch, more=[
        {"unitId": 3000000007, "productDisplayName": "Instinct 3", "partNumber": ""}])
    one_watch(win, monkeypatch)
    win._start()
    run_later(later)
    back(win)
    win._on_refresh()
    run_later(later)
    assert asked == [1, 1]
    assert names_on_the_start_page(win) == ["Test watch", "Edge 1050", "Forerunner 965",
                                            "Instinct 3", "Venu 2"]
    win.logout()  # in the device view; the one device is opened again (D7)
    run_later(later)
    assert win.navigation.stack == ["start page", "device view"]
    back(win)
    assert names_on_the_start_page(win) == ["Test watch"]
    assert asked == [1, 1]  # logged out: nothing asked


# --- pictures on the tiles (D5, T5) -------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"made up"


def fake_pictures(monkeypatch):
    """Pictures as textures named after their file; downloads counted and answered here
    (`answers`: url -> True to save it, False to fail). No request leaves the test."""
    from openexpress.gui import app

    fakes = types.SimpleNamespace(downloads=[], answers={})
    monkeypatch.setattr(app, "load_texture", lambda path: f"texture of {path.name}")

    def download(http, url, name):
        fakes.downloads.append(url)
        if not fakes.answers.get(url):
            return None, "network error"
        target = app.device_picture_path(name[len("device-"):-len(".png")])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PNG)
        return target, ""

    monkeypatch.setattr(app, "download_picture", download)
    return fakes


def in_the_cache(part_number):
    from openexpress.gui.app import device_picture_path

    path = device_picture_path(part_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)


def test_a_connected_tile_shows_the_picture_of_the_device_view(start_window, monkeypatch,
                                                               tmp_path, device_root):
    """T5: a connected tile shows what the device view shows: its picture in memory at
    once (the same texture), else its file in the cache by part number, read in the
    background; nothing downloaded for a connected device the account does not list."""
    win = start_window
    later = defer_background(win)
    fakes = fake_pictures(monkeypatch)
    win._device_textures = {"006-B0000-00": "the device view's texture"}
    in_the_cache("006-B4440-00")
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    assert win.start.pictures == [("006-B0000-00", "the device view's texture")]
    run_later(later)
    assert win.start.pictures[1:] == [("006-B4440-00", "texture of device-006-B4440-00.png")]
    assert fakes.downloads == []


def test_a_remembered_tile_shows_its_picture_from_the_cache_only(start_window, monkeypatch):
    """T5: a remembered device: its picture from the cache by part number (the Venu 2);
    none in the cache (the Varia): the icon stays, nothing is downloaded."""
    import time

    win = start_window
    later = defer_background(win)
    fakes = fake_pictures(monkeypatch)
    remember_others(time.time())
    in_the_cache("006-B3703-00")
    nothing_connected(win, monkeypatch)
    run_later(later)
    assert win.start.pictures == [("006-B3703-00", "texture of device-006-B3703-00.png")]
    assert fakes.downloads == []


def test_a_picture_comes_into_the_tile_there_and_nothing_else_moves(start_window, monkeypatch,
                                                                    tmp_path, device_root):
    """T5 with d348bcf and f2f2615: the picture read in the background is put into the tile
    that is there: the start page is not drawn again, no view comes up or goes; read once,
    not at each drawing."""
    win = start_window
    later = defer_background(win)
    fake_pictures(monkeypatch)
    in_the_cache("006-B4440-00")
    monitor_with(monkeypatch)
    two_watches(win.world, monkeypatch, tmp_path, device_root)
    win.refresh_device()
    drawn, stack = len(win.start.shown), list(win.navigation.stack)
    run_later(later)
    assert len(win.start.shown) == drawn and win.navigation.stack == stack
    assert win.start.pictures == [("006-B4440-00", "texture of device-006-B4440-00.png")]
    win._render_start()  # drawn again: the picture known already, nothing read again
    assert later == []
    assert win.start.pictures[-1] == ("006-B4440-00", "texture of device-006-B4440-00.png")


def test_an_account_tile_downloads_its_picture_and_a_failure_leaves_the_icon(start_window,
                                                                             monkeypatch):
    """T5: a device only on the account: its picture from the account's address, into the
    cache by part number; a download that fails leaves the icon, raises nothing, and is not
    tried again at each drawing."""
    from openexpress.connect import lower_keys

    win = start_window
    later = defer_background(win)
    fakes = fake_pictures(monkeypatch)
    good = "https://res.garmin.com/en/products/forerunner.png"
    bad = "https://res.garmin.com/en/products/venu.png"
    fakes.answers = {good: True, bad: False}

    class WithPictures(FakeAccountClient):
        http = object()

        def account_devices(self):
            listed = lower_keys(ACCOUNT_LIST)
            listed[1]["imageurl"], listed[2]["imageurl"] = bad, good
            return listed

    with_account(monkeypatch, WithPictures())
    nothing_connected(win, monkeypatch)
    win._fetch_account()
    run_later(later)
    run_later(later)
    assert sorted(fakes.downloads) == sorted([good, bad])
    assert win.start.pictures == [("006-B4315-00", "texture of device-006-B4315-00.png")]
    win._render_start()
    run_later(later)
    assert sorted(fakes.downloads) == sorted([good, bad])  # not tried again


@pytest.mark.needs_display
def test_the_picture_goes_into_the_tile_that_is_there():
    """StartPage.set_picture puts the picture into the tiles of that part number, the same
    widgets; a tile built later gets it at once."""
    from openexpress.devicelist import Tile
    from openexpress.gui.app import Gdk, GLib
    from openexpress.gui.start import StartPage, tile_image

    def tile(name, part, connected=True):
        return Tile(name, part, "1.00", "mtp", connected, False, False,
                    None if connected else 1.0, part, None, None)

    texture = Gdk.MemoryTexture.new(4, 4, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(bytes(48)),
                                    12)
    start = StartPage()
    start.show([(tile("fēnix 8", "006-B4532-00"), "x"), (tile("Edge 1050", "006-B4440-00"), "y")],
               False)
    before = start.tiles()
    start.set_picture("006-B4532-00", texture)
    assert start.tiles() == before
    assert tile_image(before[0]).get_paintable() is texture
    assert tile_image(before[1]).get_paintable() is None  # still the icon
    start.show([(tile("fēnix 8", "006-B4532-00"), "x"),
                (tile("Venu 2", "006-B3703-00", connected=False), "z"),
                (tile("fēnix 8 (2)", "006-B4532-00", connected=False), "w")], False)
    assert tile_image(start.tiles()[2]).get_paintable() is texture


# --- the kind of a device on its tile (D5, T5.1) -----------------------------------------------


def test_unit_info_read_for_the_picture_tells_a_bike_accessory(monkeypatch):
    """The device view asks unit-info for the picture of a device not on the account (as
    before); its segmenttype bikeaccessory is kept as the device's kind. One request, the
    same as before; another segment teaches nothing."""
    from openexpress import known_devices
    from openexpress.gui.app import picture_from_unit_info

    known_devices.remember(VARIA, "Varia RTL515", "006-B3592-03", "3.36", "ums", now=1.0)
    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", now=1.0)
    asked = []

    class Client:
        def __init__(self, segment):
            self.segment = segment

        def unit_info(self, unit_id, part_number):
            asked.append(part_number)
            return {"graphicurl": "https://res.garmin.com/x.png", "segmenttype": self.segment}

    varia = types.SimpleNamespace(unit_id=VARIA, part_number="006-B3592-03")
    venu = types.SimpleNamespace(unit_id=VENU, part_number="006-B3703-00")
    assert picture_from_unit_info(Client("bikeaccessory"), varia) == (
        "https://res.garmin.com/x.png")
    assert picture_from_unit_info(Client("fitness"), venu) == "https://res.garmin.com/x.png"
    assert asked == ["006-B3592-03", "006-B3703-00"]
    known = known_devices.load()
    assert known[VARIA].category == "bike-accessory" and known[VENU].category is None


def test_the_account_list_keeps_the_kind_of_a_remembered_device(start_window, monkeypatch):
    """The account's list read for the start page keeps the kinds it tells of remembered
    devices (the Edge is a cycling computer), for when there is no account."""
    from openexpress import known_devices
    from openexpress.connect import lower_keys

    class WithKinds(FakeAccountClient):
        def account_devices(self):
            listed = lower_keys(ACCOUNT_LIST)
            listed[0]["devicecategories"] = ["FITNESS", "DEDICATED_BIKE_COMPUTER"]
            return listed

    win = start_window
    later = defer_background(win)
    known_devices.remember(3000000002, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=1.0)
    with_account(monkeypatch, WithKinds())
    nothing_connected(win, monkeypatch)
    win._fetch_account()
    run_later(later)
    run_later(later)
    assert known_devices.load()[3000000002].category == "cycling-computer"


def test_tiles_without_a_picture_ask_nothing(start_window, monkeypatch):
    """Tiles whose devices have no picture: the icon of their kind, and nothing is asked
    (no unit-info, no download)."""
    import time

    from openexpress.gui import app

    def asked(*_a, **_k):
        raise AssertionError("asked for a tile")

    win = start_window
    later = defer_background(win)
    fakes = fake_pictures(monkeypatch)
    monkeypatch.setattr(app, "picture_from_unit_info", asked)
    remember_others(time.time())
    nothing_connected(win, monkeypatch)
    run_later(later)
    assert fakes.downloads == [] and win.start.pictures == []


@pytest.mark.needs_display
def test_a_tile_without_a_picture_shows_the_icon_of_its_kind():
    """The icon of the kind of the device (a watch, a cycling computer, a bike accessory);
    without a kind a neutral one, never the watch; a picture before both. A kind learnt
    later changes the icon in the tile that is there."""
    from openexpress.devicelist import Tile
    from openexpress.gui.app import Gdk, GLib
    from openexpress.gui.start import DEVICE_ICON, KIND_ICONS, StartPage, tile_image

    def tile(name, part, category, connected=True):
        return Tile(name, part, "1.00", "mtp", connected, False, False,
                    None if connected else 1.0, part, None, category)

    start = StartPage()
    start.show([(tile("Edge 1050", "006-B4440-00", "cycling-computer"), "a"),
                (tile("fēnix 8", "006-B4532-00", "watch"), "b"),
                (tile("Varia RTL515", "006-B3592-03", "bike-accessory", False), "c"),
                (tile("Test device", "006-B3703-00", None, False), "d")], False)
    icons = [tile_image(child).get_icon_name() for child in start.tiles()]
    assert icons == [KIND_ICONS["cycling-computer"], KIND_ICONS["watch"],
                     KIND_ICONS["bike-accessory"], DEVICE_ICON]
    assert len(set(icons)) == 4 and KIND_ICONS["watch"] not in (icons[0], icons[2], icons[3])
    texture = Gdk.MemoryTexture.new(4, 4, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(bytes(48)),
                                    12)
    start.set_picture("006-B4532-00", texture)
    before = start.tiles()
    start.show([(tile("Edge 1050", "006-B4440-00", "cycling-computer"), "a"),
                (tile("fēnix 8", "006-B4532-00", "watch"), "b"),
                (tile("Varia RTL515", "006-B3592-03", "bike-accessory", False), "c"),
                (tile("Test device", "006-B3703-00", "watch", False), "d")], False)
    assert start.tiles() == before  # the same widgets
    assert tile_image(before[1]).get_paintable() is texture  # the picture first
    assert tile_image(before[3]).get_icon_name() == KIND_ICONS["watch"]  # learnt later


def test_the_icons_of_the_kinds_are_there():
    """The icons are files of the program, symbolic, made of shapes (GTK fills every shape
    of a symbolic icon: a stroke would come out solid) by tools/symbolic-icons.py from their
    line drawings in packaging/icons; the Flatpak installs them."""
    from openexpress.gui.start import DEVICE_ICON, KIND_ICONS

    root = Path(__file__).parent.parent
    manifest = (root / "packaging" / "flatpak"
                / "io.github.tomaszbojanowski.OpenExpress.yml").read_text()
    for kind, name in (*KIND_ICONS.items(), ("device", DEVICE_ICON)):
        assert name.endswith(f"-{kind}-symbolic")
        icon = (root / "openexpress" / "data" / f"{name}.svg").read_text()
        assert "stroke" not in icon and f"packaging/icons/{kind}.svg" in icon
        assert 'stroke="currentColor"' in (root / "packaging" / "icons" / f"{kind}.svg").read_text()
        assert f"{name}.svg" in manifest.replace("${FLATPAK_ID}",
                                                 "io.github.tomaszbojanowski.OpenExpress")


@pytest.mark.needs_display
def test_the_icons_of_the_kinds_are_found_as_the_window_finds_them():
    """With the folder of the program on the icon theme's search path (as App.do_startup
    adds it), each icon is found (no "missing image" in place of a kind) and is drawn in the
    colour of the theme: readable in the light and the dark one."""
    from gi.repository import Graphene, Gsk

    from openexpress.gui.app import Gdk, Gtk
    from openexpress.gui.start import DEVICE_ICON, KIND_ICONS

    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display)
    theme.add_search_path(str(Path(__file__).parent.parent / "openexpress" / "data"))
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(display)

    def drawn(name, colour):
        """The icon at 96 px, drawn as symbolic in this colour: its RGBA bytes."""
        rgba = Gdk.RGBA()
        rgba.parse(colour)
        snapshot = Gtk.Snapshot()
        theme.lookup_icon(name, None, 96, 1, Gtk.TextDirection.NONE, 0).snapshot_symbolic(
            snapshot, 96, 96, [rgba])
        texture = renderer.render_texture(snapshot.to_node(),
                                          Graphene.Rect().init(0, 0, 96, 96))
        downloader = Gdk.TextureDownloader.new(texture)
        downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
        return downloader.download_bytes()[0].get_data()

    def painted(raw, x, y):
        """Whether the point (x, y) of the drawing (viewBox -30..30) is painted."""
        px, py = round((x + 30) / 60 * 96), round((y + 30) / 60 * 96)
        return raw[(py * 96 + px) * 4 + 3] > 128

    # A point on a line of each drawing, and one inside an outline (empty: a line drawing,
    # not a solid shape).
    points = {"watch": ((17, 0), (-8, 5)), "cycling-computer": ((15, 0), (0, -5)),
              "bike-accessory": ((8, 15), (0, 20)), "device": ((14, 0), (7, 7))}
    try:
        for kind, name in (*KIND_ICONS.items(), ("device", DEVICE_ICON)):
            assert theme.has_icon(name), name
            light, dark = drawn(name, "#ffffff"), drawn(name, "#202020")
            # Light on a dark theme, dark on a light one (GTK draws a symbolic icon as a
            # mask in the colour it is given).
            assert {light[i] for i in range(0, len(light), 4) if light[i + 3] > 128} == {255}
            assert max(dark[i] for i in range(0, len(dark), 4) if dark[i + 3] > 128) <= 0x30
            on_line, inside = points[kind]
            assert not painted(light, *inside), (name, "drawn solid, not as lines")
            assert painted(light, *on_line), (name, "the line is missing")
    finally:
        renderer.unrealize()


# --- the device view's icon before its picture (T5.2) -------------------------------------------


def _device_view_icon(drive_window, monkeypatch, account=None, name=None):
    """The icon the device view of the RTL515 drive shows before its picture comes (the
    picture is not asked here), and the stand-in of the window; `name`: another model name
    (one that tells no kind)."""
    from test_fixes_ui import page_window

    from openexpress.gui.app import MainWindow

    win, _section = page_window("watch")
    win.session = drive_session(drive_window, monkeypatch)
    if name is not None:
        win.session.device.description = name
    win._account = account
    win.update_state, win.map_state = None, None
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync", "_render_update_state", "_render_map_state",
                 "select_section", "backup", "set_background", "logout"):
        setattr(win, name, lambda *a, **kw: None)
    win._background_enabled = lambda: False
    MainWindow.show_overview(win)
    return win.device_image.get_icon_name(), win


@pytest.mark.needs_display
def test_the_device_view_waits_with_the_neutral_icon_like_the_tile(drive_window, monkeypatch):
    """T5.2: a device of an unknown kind, no picture in the cache: the device view shows the
    icon its tile shows (the neutral one), not the icon of the program."""
    from openexpress.gui.app import APP_ID
    from openexpress.gui.start import kind_icon

    icon, _win = _device_view_icon(drive_window, monkeypatch, name="Test drive")
    assert icon == kind_icon(None) and icon != APP_ID


@pytest.mark.needs_display
def test_the_device_view_waits_with_the_icon_of_its_kind(drive_window, monkeypatch):
    """T5.2: the kind as the tiles know it (devices.json, or the account: also for D7, with
    no tile before): the same icon in the device view."""
    from openexpress import known_devices
    from openexpress.connect import lower_keys
    from openexpress.gui.app import MainWindow
    from openexpress.gui.start import kind_icon

    known_devices.remember(3000000003, "Varia RTL515", "006-B3592-03", "3.36", "ums", now=1.0)
    known_devices.set_category(3000000003, "bike-accessory")
    icon, win = _device_view_icon(drive_window, monkeypatch)
    assert icon == kind_icon("bike-accessory")
    known_devices.forget(3000000003)
    # Opened at once (D7, no tile before): the kind from the account's list all the same.
    win._account = lower_keys([{"unitId": 3000000003, "productDisplayName": "Varia RTL515",
                                "deviceCategories": ["DEDICATED_BIKE_COMPUTER"]}])
    MainWindow.show_overview(win)
    assert win.device_image.get_icon_name() == kind_icon("cycling-computer")


@pytest.mark.needs_display
def test_unit_info_read_for_the_picture_sets_the_icon_while_there_is_no_picture(
        drive_window, monkeypatch):
    """T5.2: the unit-info the device view asks for the picture (as before, one request)
    tells a bike accessory and gives no picture: the icon becomes the radar; nothing more
    is asked for the icon."""
    from openexpress.gui.app import MainWindow
    from openexpress.gui.start import kind_icon

    icon, win = _device_view_icon(drive_window, monkeypatch, name="Test drive")
    assert icon == kind_icon(None)
    asked = []

    def unit_info(unit_id, part_number):
        asked.append(part_number)
        return {"segmenttype": "bikeaccessory"}  # no picture address

    monkeypatch.setattr(win.session.client, "unit_info", unit_info)
    win.in_background = lambda work, done, failed=None: done(work())
    win._show_device_picture = types.MethodType(MainWindow._show_device_picture, win)
    win._set_device_texture = types.MethodType(MainWindow._set_device_texture, win)
    win._show_device_picture()
    assert asked == ["006-B3592-03"]
    assert win.device_image.get_icon_name() == kind_icon("bike-accessory")


@pytest.mark.needs_display
def test_a_tile_without_a_word_from_garmin_shows_the_icon_its_name_tells():
    """The model name, only for the icon and only when Garmin says no kind: Venu 2 a watch,
    Varia a radar; an unknown name the neutral icon."""
    from openexpress.devicelist import Tile
    from openexpress.gui.start import DEVICE_ICON, KIND_ICONS, StartPage, tile_image

    def tile(name, part):
        return Tile(name, part, "1.00", "mtp", False, False, False, 1.0, part, None, None)

    start = StartPage()
    start.show([(tile("Test device", "006-B0000-01"), "a"),
                (tile("Varia RTL515", "006-B3592-03"), "b"),
                (tile("Venu 2", "006-B3703-00"), "c")], False)
    assert [tile_image(child).get_icon_name() for child in start.tiles()] == [
        DEVICE_ICON, KIND_ICONS["bike-accessory"], KIND_ICONS["watch"]]


# --- a list instead of tiles for many devices (T6a) --------------------------------------------


def seven_watches():
    """Seven different watches connected (made up), as the detection lists them."""
    usb = gio_storage.GarminUsb("51b4", "usb", 1, 9)
    return [gio_storage.DeviceSummary(
        gio_storage.FoundDevice(f"mtp://watch-{n}/", "x", True, usb=usb),
        f"Watch {n}", f"006-B000{n}-00", "1.00", 3000000100 + n) for n in range(7)]


@pytest.mark.needs_display
def test_seven_connected_devices_are_a_list_with_the_icons_of_tiles(monkeypatch):
    """T6a: seven devices connected: their section is a list, not tiles; each row has the
    name, the words and the icon its tile would have (the kind, else the neutral one), and
    nothing is asked for the pictures (no unit-info, no download). A click on a row opens
    that device, as a click on its tile. Two remembered devices stay tiles."""
    from openexpress import known_devices
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow, Section
    from openexpress.gui.start import StartPage, kind_icon, tile_image

    def asked(*_a, **_k):
        raise AssertionError("asked for a picture")

    monkeypatch.setattr(app, "picture_from_unit_info", asked)
    monkeypatch.setattr(app, "download_picture", asked)
    known_devices.remember(3000000101, "Watch 1", "006-B0001-00", "1.00", "mtp", now=1.0)
    known_devices.set_category(3000000101, "watch")
    known_devices.remember(VARIA, "Varia RTL515", "006-B3592-03", "3.36", "ums", now=2.0)
    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", now=3.0)
    opened = []
    start = StartPage(opened.append)
    win = start_stand_in(start, {"maps": Section("maps", "Maps", "x-symbolic", "Loading…")})
    later = defer_background(win)
    win._choices = seven_watches()
    MainWindow.show_choices(win)
    assert later == []  # nothing to read: no file in the cache, no address
    assert not start.grid.get_visible() and start.grid.get_first_child() is None
    assert start.list.get_visible()
    rows = start.tiles()[:7]
    assert all(row.get_parent() is start.list for row in rows)
    assert [row.get_title() for row in rows] == [f"Watch {n}" for n in range(7)]
    assert rows[0].get_subtitle() == "Over USB (MTP) · Software 1.00"
    assert tile_image(rows[1]).get_icon_name() == kind_icon("watch")
    assert tile_image(rows[0]).get_icon_name() == kind_icon(None)
    remembered = start.tiles()[7:]
    assert len(remembered) == 2
    # Tiles there: the empty list of that section is hidden, not only empty.
    assert start.remembered_grid.get_visible() and not start.remembered_list.get_visible()
    assert all(t.get_parent() is start.remembered_grid for t in remembered)
    start.list.emit("row-activated", rows[3])
    start.remembered_grid.emit("child-activated", remembered[1])
    assert opened == [3, 8]  # the places in the whole list, as for tiles
    assert win._start_targets[3].uri == "mtp://watch-3/"  # the device of that row


@pytest.mark.needs_display
def test_a_row_gets_its_picture_like_a_tile():
    """T6a: the picture of a part number goes into the row that is there (the same widget)
    and into rows built later, as into tiles."""
    from openexpress.devicelist import Tile
    from openexpress.gui.app import Gdk, GLib
    from openexpress.gui.start import StartPage, tile_image

    def tile(n):
        return Tile(f"Watch {n}", f"006-B000{n}-00", "1.00", "mtp", True, False, False, None,
                    f"006-B000{n}-00", None, None)

    entries = [(tile(n), "x") for n in range(7)]
    six = StartPage()
    six.show(entries[:6], False)  # six: tiles, the empty list hidden
    assert six.grid.get_visible() and not six.list.get_visible()
    assert six.list.get_first_child() is None
    start = StartPage()
    start.show(entries, False)
    rows = start.tiles()
    assert len(rows) == 7 and all(row.get_parent() is start.list for row in rows)
    texture = Gdk.MemoryTexture.new(4, 4, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(bytes(48)),
                                    12)
    start.set_picture("006-B0002-00", texture)
    assert start.tiles() == rows
    assert tile_image(rows[2]).get_paintable() is texture
    assert tile_image(rows[1]).get_paintable() is None
    start.show([*entries, (tile(8), "y")], False)  # one more: rows built anew
    assert tile_image(start.tiles()[2]).get_paintable() is texture
