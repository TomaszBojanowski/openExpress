"""A Garmin drive the system does not mount (KDE) sends no mount event, and gvfs may offer its
volume, or a medium in it, later. Tests that the window hears gvfs's volumes, watches USB with a
device open, and looks again a few times for a device in mass storage mode listed without a
drive. All devices are made up."""

import types

import pytest
from fakes import replace_in_window, window_methods
from fakes import window as stand_in
from test_detection import drives, usb_drive  # noqa: F401 - the fixture
from test_unmounted_drive import FR55, Clock, UnmountedVolume, _monitor, _on_usb, _sized

from openexpress.gui.navigation import READ_THE_LIST, Cause, Detect, ReadList, plan

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

PLACE = "1-12"  # bus 1, device number 12: where usb_drive puts it
OPEN = "mtp://091e_51b4_made-up/"  # the device open (an MTP watch)


def _drive(drives, monkeypatch, sectors=51_814, vendor="091e", on_usb=True):
    """A Forerunner 55 on USB, its volume not mounted (`sectors` 0: no medium), offered by
    gvfs; returns the volume and its key as a drive not mounted."""
    unix = usb_drive(drives, vendor=vendor, product=FR55)
    _sized(drives, unix, sectors)
    if on_usb:
        _on_usb(drives, monkeypatch)
    volume = UnmountedVolume(unix, drives.media / "GARMIN")
    return volume, gio_storage.UNMOUNTED_DRIVE + unix


# --- which volumes are Garmin drives ---------------------------------------------------------------

def test_a_volume_of_a_garmin_drive_is_named_by_its_key(drives, monkeypatch):
    volume, key = _drive(drives, monkeypatch)
    found_key, usb, medium = gio_storage.drive_volume(volume)
    assert (found_key, usb.usb_id, medium) == (key, "091e:0f1d", True)
    _sized(drives, volume.unix_device, 0)
    assert gio_storage.drive_volume(volume)[2] is False  # no medium (yet)


@pytest.mark.parametrize("what", ["another vendor", "an MTP device", "no device"])
def test_other_volumes_are_no_garmin_drives(drives, monkeypatch, what):
    from test_detection import FakeVolume

    if what == "another vendor":
        volume, _key = _drive(drives, monkeypatch, vendor="0781")
    else:
        volume = FakeVolume("mtp://x/", "/dev/bus/usb/001/008" if what == "an MTP device"
                            else None)
    assert gio_storage.drive_volume(volume) is None


# --- the devices in mass storage mode that the last list found without a drive ----------------------

def test_a_device_in_mass_storage_mode_without_a_volume_waits_for_one(drives, monkeypatch):
    volume, _key = _drive(drives, monkeypatch)
    _monitor(monkeypatch)  # gvfs has no volume of it yet
    assert gio_storage.find_ums_devices() == []
    assert gio_storage.drives_without_a_volume() == {PLACE}
    _monitor(monkeypatch, volumes=[volume])  # now it has
    assert len(gio_storage.find_ums_devices()) == 1
    assert gio_storage.drives_without_a_volume() == set()


def test_a_device_whose_volume_has_no_medium_yet_waits_too(drives, monkeypatch):
    volume, _key = _drive(drives, monkeypatch, sectors=0)
    _monitor(monkeypatch, volumes=[volume])
    assert gio_storage.find_ums_devices() == []
    assert gio_storage.drives_without_a_volume() == {PLACE}


def test_a_drive_ejected_by_openexpress_does_not_wait(drives, monkeypatch):
    volume, _key = _drive(drives, monkeypatch)
    _monitor(monkeypatch, volumes=[volume])
    usb = gio_storage.GarminUsb(FR55, 1, 12)
    with gio_storage._ejected_lock:
        gio_storage._ejected.append(usb)
    try:
        assert gio_storage.find_ums_devices() == []
        assert gio_storage.drives_without_a_volume() == set()
    finally:
        with gio_storage._ejected_lock:
            gio_storage._ejected.remove(usb)


# --- the window hears the volumes ------------------------------------------------------------------

VOLUME_METHODS = ("_on_volume_added", "_on_volume_changed", "_on_volume_removed",
                  "_on_drive_volume", "_drive_gone")


def _window(monkeypatch, session=None, listed=(), choosing=False):
    """The parts of the window that hear the volumes; the detections asked are recorded."""
    from openexpress.gui.connected import Connected

    asked = []
    replace_in_window(monkeypatch, "schedule_detection",
                      lambda win, ms, reason=None, cause=None: asked.append((ms, reason, cause)))
    win = stand_in(session=session, busy=False,
                   connected=Connected(devices=list(listed), choosing=choosing))
    return window_methods(win, VOLUME_METHODS), asked


def _listed(key):
    """The drive on the start page's list, not mounted."""
    return gio_storage.DeviceSummary(
        gio_storage.FoundDevice(key, "GARMIN", False, kind="ums",
                                usb=gio_storage.GarminUsb(FR55, 1, 12)),
        reason=gio_storage.NOT_MOUNTED)


def _open_session():
    return types.SimpleNamespace(storage=types.SimpleNamespace(label=OPEN, usb=None))


@pytest.mark.parametrize("open_device", [False, True])
def test_a_drive_volume_added_brings_its_tile(drives, monkeypatch, open_device):
    """With no device open, a detection; with one open (an Edge 540, opened as the one
    device), the list read again and the open one stays."""
    volume, key = _drive(drives, monkeypatch)
    win, asked = _window(monkeypatch, session=_open_session() if open_device else None)
    win._on_volume_added(None, volume)
    assert asked == [(2500, key, Cause.PLUGGED)]
    decided = plan(Cause.PLUGGED, [key], open_label=OPEN if open_device else None,
                   listed=frozenset({OPEN.rstrip("/")}) if open_device else frozenset())
    assert decided == (ReadList() if open_device else Detect())


def test_a_medium_that_comes_later_brings_its_tile(drives, monkeypatch):
    volume, key = _drive(drives, monkeypatch, sectors=0)
    win, asked = _window(monkeypatch)
    win._on_volume_added(None, volume)
    assert asked == []  # no medium yet: nothing to list
    _sized(drives, volume.unix_device, 51_814)
    win._on_volume_changed(None, volume)
    assert asked == [(2500, key, Cause.PLUGGED)]


def test_a_drive_already_on_the_list_brings_nothing(drives, monkeypatch):
    volume, key = _drive(drives, monkeypatch)
    win, asked = _window(monkeypatch, listed=[_listed(key)], choosing=True)
    win._on_volume_changed(None, volume)
    win._on_volume_added(None, volume)
    assert asked == []


@pytest.mark.parametrize("what", ["mounted", "another vendor", "ejected by openExpress"])
def test_these_volumes_bring_nothing(drives, monkeypatch, what):
    """A mounted one comes with its mount (mount-added); a drive ejected by openExpress stays
    closed until it is unplugged."""
    volume, _key = _drive(drives, monkeypatch, vendor="0781" if what == "another vendor"
                          else "091e")
    if what == "mounted":
        volume.mounted_at = object()
    usb = gio_storage.GarminUsb(FR55, 1, 12)
    win, asked = _window(monkeypatch)
    with gio_storage._ejected_lock:
        if what == "ejected by openExpress":
            gio_storage._ejected.append(usb)
    try:
        win._on_volume_added(None, volume)
        win._on_volume_changed(None, volume)
    finally:
        with gio_storage._ejected_lock:
            if usb in gio_storage._ejected:
                gio_storage._ejected.remove(usb)
    assert asked == []


@pytest.mark.parametrize(("open_device", "expected"), [
    (True, (1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)),
    (False, (1500, None, Cause.UNPLUGGED_CHOICE)),
])
def test_a_drive_on_the_list_that_goes_away_leaves_it(drives, monkeypatch, open_device,
                                                      expected):
    """Unplugged while it is not mounted: no mount event either. Today's window kept its tile
    while a device was open."""
    volume, key = _drive(drives, monkeypatch, on_usb=False)  # already gone from /sys
    win, asked = _window(monkeypatch, session=_open_session() if open_device else None,
                         listed=[_listed(key)], choosing=not open_device)
    win._on_volume_removed(None, volume)
    assert asked == [expected]


def test_a_drive_on_the_list_whose_medium_goes_leaves_it(drives, monkeypatch):
    volume, key = _drive(drives, monkeypatch)
    win, asked = _window(monkeypatch, session=_open_session(), listed=[_listed(key)])
    _sized(drives, volume.unix_device, 0)
    win._on_volume_changed(None, volume)
    assert asked == [(1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)]


def test_a_volume_not_on_the_list_that_goes_away_brings_nothing(drives, monkeypatch):
    volume, _key = _drive(drives, monkeypatch, on_usb=False)
    win, asked = _window(monkeypatch, session=_open_session())
    win._on_volume_removed(None, volume)
    assert asked == []


@pytest.mark.needs_display
@pytest.mark.filterwarnings("ignore:'asyncio.*:DeprecationWarning")
def test_the_window_hears_the_volumes_of_gvfs(drives, monkeypatch, in_the_window):
    """The real window: the three signals of the volume monitor are heard."""
    from conftest import stop_windows
    from gi.repository import Gio

    from openexpress.gui import app

    asked = []
    monkeypatch.setattr(app.MainWindow, "refresh_device", lambda self, cause=None: None)
    in_the_window("schedule_detection",
                  lambda win, ms, reason=None, cause=None: asked.append((ms, reason, cause)))
    volume, key = _drive(drives, monkeypatch)
    monitor = Gio.VolumeMonitor.get()
    win = app.MainWindow(None)
    try:
        monitor.emit("volume-added", volume)
        assert asked == [(2500, key, Cause.PLUGGED)]
    finally:
        stop_windows(monitor)
        win.destroy()


# --- the window looks at USB --------------------------------------------------------------------

def _polled(monkeypatch, session=None, listed=()):
    """The parts of the window that look at USB every few seconds (MainWindow._poll_usb)."""
    from test_detection import FakeStartPage

    from openexpress.gui.app import MainWindow
    from openexpress.gui.connected import Connected

    asked = []
    replace_in_window(monkeypatch, "schedule_detection",
                      lambda win, ms, reason=None, cause=None: asked.append((ms, reason, cause)))
    page = stand_in(
        session=session, busy=False, _usb_state=gio_storage.ABSENT, _usb_since=0.0,
        _usb_title="", _usb_notice=MainWindow._usb_notice, _update_actions=lambda: None,
        start=FakeStartPage(), connected=Connected(devices=list(listed)),
        sections={"watch": types.SimpleNamespace(set_empty=lambda *notice: None)})
    return page, asked


def test_a_drive_that_comes_while_a_device_is_open_has_the_list_read_again(drives, monkeypatch):
    """With an Edge 540 open, a Forerunner 55 plugged in next to it gets its tile without
    Refresh: the window looks at USB also while a device is open."""
    from openexpress.gui.app import MainWindow

    page, asked = _polled(monkeypatch, session=_open_session())
    clock = Clock()
    MainWindow._poll_usb(page, clock)  # what is on USB now: nothing new
    _drive(drives, monkeypatch)
    clock.now += 2
    MainWindow._poll_usb(page, clock)
    assert asked == [(2500, None, Cause.PLUGGED)]
    assert plan(Cause.PLUGGED, [None], open_label=OPEN) == ReadList()  # the open one stays
    clock.now += 2
    MainWindow._poll_usb(page, clock)
    assert len(asked) == 1  # once


def test_a_drive_not_mounted_that_goes_while_a_device_is_open_leaves_the_list(drives,
                                                                             monkeypatch):
    from openexpress.gui.app import MainWindow

    _volume, key = _drive(drives, monkeypatch)
    page, asked = _polled(monkeypatch, session=_open_session(), listed=[_listed(key)])
    clock = Clock()
    MainWindow._poll_usb(page, clock)
    monkeypatch.setattr(gio_storage, "SYSFS_USB", drives.media / "nothing")  # unplugged
    clock.now += 2
    MainWindow._poll_usb(page, clock)
    assert asked == [(1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)]


@pytest.mark.parametrize("open_device", [False, True])
@pytest.mark.parametrize("sectors", [None, 0])  # no volume yet; a volume without a medium yet
def test_a_device_found_without_a_drive_is_looked_for_again_a_few_times(drives, monkeypatch,
                                                                        open_device, sectors):
    """The one detection 2.5 s after the device came may find no drive yet: it is looked
    for again every DRIVE_RETRY_S, at most DRIVE_RETRIES times."""
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    volume, _key = _drive(drives, monkeypatch, sectors=sectors or 0)
    _monitor(monkeypatch, volumes=[] if sectors is None else [volume])
    page, asked = _polled(monkeypatch, session=_open_session() if open_device else None)
    clock = Clock()
    MainWindow._poll_usb(page, clock)
    first = list(asked)  # with none open: the detection of a device in mass storage mode
    assert first == ([] if open_device else [(2500, None, Cause.PLUGGED)])
    assert gio_storage.find_ums_devices() == []  # what that detection (or a list) found
    for _ in range(40):  # 80 s
        clock.now += 2
        MainWindow._poll_usb(page, clock)
    again = asked[len(first):]
    assert again == [(500, None, Cause.PLUGGED)] * app.DRIVE_RETRIES


def test_once_the_drive_is_found_it_is_not_looked_for_again(drives, monkeypatch):
    from openexpress.gui.app import MainWindow

    volume, _key = _drive(drives, monkeypatch)
    _monitor(monkeypatch)
    page, asked = _polled(monkeypatch)
    clock = Clock()
    MainWindow._poll_usb(page, clock)
    assert gio_storage.find_ums_devices() == []
    clock.now += 2
    MainWindow._poll_usb(page, clock)
    _monitor(monkeypatch, volumes=[volume])
    assert len(gio_storage.find_ums_devices()) == 1  # found by the next look
    count = len(asked)
    for _ in range(20):
        clock.now += 2
        MainWindow._poll_usb(page, clock)
    assert len(asked) == count
