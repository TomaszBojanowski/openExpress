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
        _update_actions=lambda: None)
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
FLATPAK_DRIVE_TEXT = ("The Garmin device is connected as a USB drive. This version (Flatpak) "
                      "does not open drives; use the version from source, or the command line "
                      "with --path and the folder where the system mounted the device.")
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
    the page says that this version does not open drives, and the log says why it was skipped."""
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
                                 sections={"watch": types.SimpleNamespace(
                                     set_empty=lambda *notice: shown.append(notice))})
    MainWindow._poll_usb(page, clock=win.world.clock)
    assert shown == [("Mass storage mode", FLATPAK_DRIVE_TEXT, False)]


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
    """K5: one row per device: its name (or "Garmin device" without details), how it is
    connected, its version or why it has no details; never an MTP address; the other pages
    say to choose a device."""
    from test_fixes_ui import page_window

    from openexpress.gui.app import MainWindow, Section

    win, section = page_window("watch")
    maps = Section("maps", "Maps", "x-symbolic", "Loading…")
    win.sections["maps"] = maps
    win._update_banner = win._update_actions = lambda: None
    win.open_choice = lambda found: None
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
    texts = _texts(section.page)
    rows = list(itertools.pairwise(texts))  # a row's title, then its subtitle
    for title, subtitle in (("fēnix 8", "Over USB (MTP) · Software 23.39"),
                            ("Garmin device", ("Over USB (MTP) · Not mounted; choose it to "
                                               "open it")),
                            ("Varia RTL515", "USB drive · Software 3.36")):
        assert (title, subtitle) in rows
    assert "Several Garmin devices are connected" in texts
    assert not any("mtp://" in t or "0000abcdef" in t or "file:" in t for t in texts)
    assert not any("PIN" in t or "starting" in t for t in texts)
    assert maps.empty.get_title() == "Choose a device"


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
    """Not 45 s of "the device is starting" and then the advice about a PIN: at once, what
    to do with such a device."""
    mass_storage_device(plugged.root)
    for wait in (True, False):
        with pytest.raises(gio_storage.NotThereYet) as error:
            plugged.open(wait=wait)
        assert plugged.clock.slept == 0 and plugged.notices == []
        assert "PIN" not in str(error.value)
        assert str(error.value) == gio_storage.not_ready_text(gio_storage.MASS_STORAGE)
        assert "--path" in str(error.value) and plugged.opened == []


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


def test_window_tells_a_garmin_in_mass_storage_mode(plugged, monkeypatch):
    """No spinner, no search over MTP and, after the wait, no advice about a PIN: the page
    says at once what the device is."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    shown, refreshed, updates = [], [], []
    section = types.SimpleNamespace(set_empty=lambda *notice: shown.append(notice))
    win = types.SimpleNamespace(session=None, busy=False, sections={"watch": section},
                                _usb_state=gio_storage.ABSENT, _usb_since=0.0, _usb_title="",
                                _usb_notice=MainWindow._usb_notice,
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


def test_flatpak_window_still_sends_a_garmin_drive_to_the_command_line(plugged, monkeypatch):
    """The Flatpak window cannot open a drive (step 6, F1): the only place where the advice
    about --path stays."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(app.paths, "in_flatpak", lambda: True)
    mass_storage_device(plugged.root)
    assert MainWindow._usb_notice(gio_storage.MASS_STORAGE, 0) == (
        "Mass storage mode", FLATPAK_DRIVE_TEXT, False)


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
