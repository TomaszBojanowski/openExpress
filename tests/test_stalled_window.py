"""The window after a device stopped responding: a banner on the start page and on the device
view says it, with the device's name when it is known, until the device is gone from its place
on USB (unplugged, or restarted: a new device number); the session of that device is closed as
if it were unplugged; no error dialog says it again; the other devices stay as they are. All
devices are made up."""

import types

import pytest
from fakes import stalled_session, tasks_at_once, tells_stalls, usb_devices, window_methods
from fakes import window as stand_in

from openexpress import i18n
from openexpress.device.storage import DeviceError, DeviceNotResponding

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

FENIX = gio_storage.GarminUsb("51b4", 3, 14)
EDGE = gio_storage.GarminUsb("4e86", 3, 20)
NO_NAME = "The device stopped responding. Unplug it and plug it in again."
FENIX_TEXT = "The device fēnix 8 stopped responding. Unplug it and plug it in again."


def stall(usb=FENIX):
    error = DeviceNotResponding.during("Cannot read GARMIN/x.fit")
    error.usb = usb
    return error


def listed(usb, name):
    """An entry of the list of devices connected (a summary of its GarminDevice.xml)."""
    return types.SimpleNamespace(found=types.SimpleNamespace(usb=usb), description=name)


def a_window(monkeypatch, session=None, busy=False, connected=()):
    """A stand-in window with the window's own error(): what it tells goes to `told`."""
    def no_device():
        told.no_device += 1

    win = stand_in(session=session, busy=busy, show_no_device=no_device)
    told = tells_stalls(win, monkeypatch, connected)
    told.no_device = 0
    return win, told


def fails_with(error):
    def work():
        raise error
    return work


# --- a task meets a stall ------------------------------------------------------------------


def test_a_task_that_meets_a_stall_says_it_in_the_banner_and_closes_the_device(monkeypatch):
    """No error dialog, also from a page that tells its failure itself: the banner says it."""
    from openexpress.gui.app import MainWindow

    tasks_at_once(monkeypatch)
    win, told = a_window(monkeypatch, stalled_session(FENIX, "fēnix 8"))

    def failed(e):
        win.error("Cannot install the map", str(e))  # as a page tells its failure
        return True

    MainWindow.run_task(win, "Installing…", fails_with(stall()), on_error=failed)
    assert told.errors == []
    assert told.start[-1] == FENIX_TEXT and told.banner >= 1
    assert win.session is None and told.no_device == 1


def test_a_stall_is_in_the_banner_also_when_the_page_fails_to_tell_it(monkeypatch):
    """A page whose own telling of the failure breaks: the banner says the stall still."""
    from openexpress.gui.app import MainWindow

    tasks_at_once(monkeypatch)
    win, told = a_window(monkeypatch, stalled_session(FENIX, "fēnix 8"))

    def failed(e):
        raise RuntimeError("a made-up failure of the page")

    with pytest.raises(RuntimeError):
        MainWindow.run_task(win, "Installing…", fails_with(stall()), on_error=failed)
    assert told.start[-1:] == [FENIX_TEXT]


@pytest.mark.parametrize("error", [
    DeviceError("Cannot read GARMIN/x.fit: made-up refusal"),
    gio_storage.GioTimedOut("Cannot mount the device: the device did not answer in time")])
def test_another_failure_and_a_mount_that_reaches_its_limit_have_their_error_dialog(
        monkeypatch, error):
    """A guard: no banner, the device not closed, the error dialog as it was."""
    from openexpress.gui.app import MainWindow

    tasks_at_once(monkeypatch)
    win, told = a_window(monkeypatch, stalled_session(FENIX, "fēnix 8"))
    MainWindow.run_task(win, "Working…", fails_with(error))
    assert told.errors == [str(error)]
    assert told.start == [] and win.session is not None and told.no_device == 0


# --- the place on USB ------------------------------------------------------------------------


def test_the_banner_stays_while_the_device_is_at_its_place_and_goes_once_it_is_gone(
        monkeypatch, tmp_path):
    from openexpress.gui import stalled

    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14)})
    gio_storage.note_stalled(FENIX)
    win, told = a_window(monkeypatch)
    stalled.note(win, stall())
    stalled.look(win)
    assert told.start[-1] == NO_NAME  # no device open, none listed: its name is not known
    put({"3-1": ("51b4", 3, 15)})  # unplugged and plugged in again
    stalled.look(win)
    assert told.start[-1] is None and win._stalled == []


def test_no_detection_starts_while_the_stalled_device_stays(monkeypatch, tmp_path):
    """The USB poll counts it out: no detection of it; once it is back at a new device
    number, the banner goes and it is detected as any device plugged in."""
    from gi.repository import GLib

    from openexpress.gui import detection
    from openexpress.gui.app import MainWindow
    from openexpress.gui.connected import Connected

    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14)})
    gio_storage.note_stalled(FENIX)
    timers = []
    monkeypatch.setattr(GLib, "timeout_add", lambda ms, fn: timers.append(ms) or len(timers))
    win, told = a_window(monkeypatch)
    win.connected = Connected()  # no device listed
    win.sections = {"watch": types.SimpleNamespace(set_empty=lambda *notice, icon=None: None)}
    win._usb_notice, win._update_actions = MainWindow._usb_notice, lambda: None
    window_methods(win, ["_poll_usb"])
    win._poll_usb()
    assert timers == [] and told.start[-1] == NO_NAME
    put({"3-1": ("51b4", 3, 15)})
    win._poll_usb()
    assert timers == [detection.PLUGGED_MS] and told.start[-1] is None


@pytest.mark.parametrize("busy", [False, True])
def test_a_stall_in_a_background_work_is_found_by_the_usb_poll(monkeypatch, tmp_path, busy):
    """E.g. the free space read after a write: the banner comes with the next look at USB;
    the device is closed then, or by the running task, which fails at once with it."""
    from openexpress.gui import stalled

    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14)})
    gio_storage.note_stalled(FENIX)
    win, told = a_window(monkeypatch, stalled_session(FENIX, "fēnix 8"), busy=busy)
    stalled.look(win)
    assert told.start[-1] == FENIX_TEXT
    assert (win.session is None) is not busy


def test_a_stall_whose_place_is_not_known_is_told_and_goes_when_the_devices_on_usb_change(
        monkeypatch, tmp_path):
    """Nothing is kept out (no place to keep out); the banner goes when the Garmin devices on
    USB change."""
    from openexpress.gui import stalled

    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14)})
    win, told = a_window(monkeypatch, stalled_session(None, "fēnix 8", stalled=True))
    stalled.note(win, stall(usb=None))
    assert told.start[-1] == FENIX_TEXT and win.session is None
    assert gio_storage.stalled_places() == []
    stalled.look(win)
    assert told.start[-1] == FENIX_TEXT
    put({})  # unplugged
    stalled.look(win)
    assert told.start[-1] is None


def test_a_stall_whose_place_is_not_known_told_again_is_one_device(monkeypatch, tmp_path):
    """The same device met again (a detection that asks it once more): one device on the
    banner, still with its name, not two without one."""
    from openexpress.gui import stalled

    put = usb_devices(monkeypatch, tmp_path / "usb")
    put({"3-1": ("51b4", 3, 14)})
    win, told = a_window(monkeypatch, stalled_session(None, "fēnix 8", stalled=True))
    stalled.note(win, stall(usb=None))
    stalled.note(win, stall(usb=None))  # the device closed meanwhile: its name not known now
    assert len(win._stalled) == 1
    assert told.start[-1] == FENIX_TEXT


# --- several devices -------------------------------------------------------------------------


def test_another_device_stays_open_and_the_banner_names_the_stalled_one(monkeypatch):
    from openexpress.gui import stalled

    win, told = a_window(monkeypatch, stalled_session(EDGE, "Edge 1050"),
                         connected=[listed(FENIX, "fēnix 8"), listed(EDGE, "Edge 1050")])
    stalled.note(win, stall(FENIX))
    assert win.session is not None and told.no_device == 0
    assert told.start[-1] == FENIX_TEXT and told.banner >= 1


# --- the banner ------------------------------------------------------------------------------


def test_the_banner_of_a_stall_comes_before_the_setup_and_the_sign_in():
    """Nothing else can be done meanwhile; and no button: every try would wait for the same
    blocked device."""
    from openexpress.gui import stalled
    from openexpress.gui.app import MainWindow

    said = {}
    banner = types.SimpleNamespace(set_title=lambda t: said.update(title=t),
                                   set_button_label=lambda b: said.update(button=b),
                                   set_revealed=lambda r: said.update(revealed=r))
    session = types.SimpleNamespace(client=types.SimpleNamespace(consumer=None),
                                    logged_in=False, ownership_error=None)
    win = stand_in(session=session, banner=banner)
    win._stalled = [stalled.Stalled(FENIX, "fēnix 8")]
    MainWindow._update_banner(win)
    assert said == {"title": FENIX_TEXT, "button": None, "revealed": True}
    assert win._banner_kind == "stalled"


WORDS = {
    "en": [NO_NAME, FENIX_TEXT,
           ("The devices fēnix 8, Edge 1050 stopped responding. Unplug them and plug them in "
            "again."),
           "The devices stopped responding. Unplug them and plug them in again."],
    "pl": ["Urządzenie przestało odpowiadać. Odłącz je i\u00a0podłącz ponownie.",
           "Urządzenie fēnix 8 przestało odpowiadać. Odłącz je i\u00a0podłącz ponownie.",
           ("Urządzenia fēnix 8, Edge 1050 przestały odpowiadać. Odłącz je i\u00a0podłącz "
            "ponownie."),
           "Urządzenia przestały odpowiadać. Odłącz je i\u00a0podłącz ponownie."],
}


@pytest.mark.parametrize("language", WORDS)
def test_the_words_of_the_banner(language):
    """With the names when every one is known; two devices, both named."""
    from openexpress.gui import words

    i18n.setup(language)
    try:
        said = [words.stalled_text([None]), words.stalled_text(["fēnix 8"]),
                words.stalled_text(["fēnix 8", "Edge 1050"]),
                words.stalled_text(["fēnix 8", None])]
    finally:
        i18n.setup("en")
    assert said == WORDS[language]
