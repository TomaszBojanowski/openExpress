"""A wait for Garmin's servers must not look like a hang: such a step is reported as a wait
(its length is unknown), and the window keeps the bar moving until the next step."""

import itertools
import struct
import time
import types

import pytest
from fakes import window as stand_in
from fakes import window_methods
from test_upload_consent import QUEUED, Account

from openexpress import service
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

TRUEUP_WAIT = "TrueUp: waiting for Garmin Connect…"
NOTE = "This can take a few minutes."


# --- the model: which steps are waits ------------------------------------------------------------

def test_trueup_reports_its_wait_for_connect_as_a_wait(device_root):
    from openexpress import trueup
    from openexpress.fit import FIT_EPOCH_OFFSET
    from openexpress.progress import Waiting

    xml = (device_root / "GARMIN/GarminDevice.xml").read_text().replace(
        "</MassStorageMode>",
        "<DataType><Name>FIT_TYPE_39</Name><File><Location><Path>Garmin/NewFiles</Path>"
        "<FileExtension>FIT</FileExtension></Location><TransferDirection>InputToUnit"
        "</TransferDirection></File></DataType></MassStorageMode>")
    dev = parse(xml.encode())
    now = 2_000_000_000
    definition = bytes([0x40, 0, 0]) + struct.pack("<H", 197) + bytes([1, 0, 4, 0x86])
    body = definition + bytes([0]) + struct.pack("<I", now + 20 - FIT_EPOCH_OFFSET)
    check_back = struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0) + body + b"\0\0"
    check = {"messageid": 1, "messagetype": "check_back", "devicexmldatatype": "FIT_TYPE_39",
             "metadata": {"messageurl": "cb"}}
    true = {"messageid": 2, "messagetype": "true_up", "devicexmldatatype": "FIT_TYPE_39",
            "metadata": {"messageurl": "tu"}}
    client = types.SimpleNamespace(
        download_message=lambda url, absolute: check_back if url == "cb" else b"TRUEUP",
        ack_message=lambda mid: None, device_messages=lambda unit: [true])
    seen = []
    trueup.run(dev, LocalStorage(device_root), client, [check], progress=seen.append,
               sleep=lambda s: None, now=lambda: now)
    # The wait of a known length (20 s here) is told step by step besides (test_trueup_wait).
    from openexpress.progress import TimedWait

    assert [m for m in seen if not isinstance(m, TimedWait)] == [
        TRUEUP_WAIT, "TrueUp: writing daily totals to the device"]
    assert [isinstance(m, Waiting) for m in seen if not isinstance(m, TimedWait)] == [True, False]
    assert len([m for m in seen if isinstance(m, TimedWait)]) == 11


def test_a_sync_reports_its_waits_for_the_server_as_waits(device_root, xdg):
    from openexpress.progress import Waiting

    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    seen = []
    service.run_sync(service.DeviceSession(st, dev, Settings(), Account(messages=QUEUED)),
                     first_run="all", progress=seen.append)
    assert [m for m in seen if isinstance(m, Waiting)] == [
        "Fetching configuration from the Garmin server…",
        "Checking for files from Garmin Connect…"]
    assert "Looking for new files on the device…" in seen  # the device: a step
    assert any(m.startswith("Uploading ") for m in seen)  # a transfer: steps


# --- the window: a moving bar while waiting ------------------------------------------------------

class _Label:
    text = None

    def set_text(self, text):
        self.text = text


class _Bar:
    def __init__(self):
        self.pulses, self.fraction, self.visible = 0, None, True

    def set_visible(self, visible):
        self.visible = visible

    def pulse(self):  # the bar moves and no longer shows a fraction
        self.pulses += 1
        self.fraction = None

    def set_fraction(self, fraction):
        self.fraction = fraction


class _Shown:
    def __init__(self):
        self.visible = True  # as GTK makes a widget

    def set_revealed(self, revealed):
        pass

    def set_visible(self, visible):
        self.visible = visible

    def set_sensitive(self, sensitive):
        pass


# The window's progress and waits and the methods they call (once picked by name: "wait",
# "_show_", "indicator"), found along the whole MRO (fakes.window_methods).
PROGRESS_METHODS = ("progress", "fraction", "set_busy", "_show_account", "_show_bar",
                    "_show_device_picture", "_show_device_view", "_show_fraction",
                    "_show_installed", "_show_last_sync", "_show_page_picture",
                    "_show_primary_training", "_show_progress", "_show_remembered",
                    "_show_start", "_show_tiles", "_stop_waiting", "_waiting_note")


@pytest.fixture
def window(monkeypatch):
    """The progress part of the window with stand-in widgets; GLib's timers are kept in
    `window.timers` (id -> (interval in ms, function)) and run only when a test calls them."""
    from gi.repository import GLib

    timers = {}
    ids = itertools.count(100)

    def timeout_add(interval, fn, *args):
        source = next(ids)
        timers[source] = (interval, fn)
        return source

    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: fn(*args))
    monkeypatch.setattr(GLib, "timeout_add", timeout_add)
    monkeypatch.setattr(GLib, "source_remove", lambda source: timers.pop(source) and True)
    win = stand_in(
        session=None, busy=True, action_bar=_Shown(), progress_label=_Label(),
        progress_bar=_Bar(), unplug_label=_Shown(), banner=_Shown(), _action_buttons=[],
        _update_actions=lambda: None, _waiting=0, timers=timers, busy_spinner=_Shown(),
        stop_wait_button=_Shown())
    return window_methods(win, PROGRESS_METHODS)


def _tick(win):
    """Runs the one timer of the window, as GLib would."""
    ((_interval, fn),) = win.timers.values()
    return fn()


def test_while_waiting_for_the_server_the_bar_keeps_moving_until_the_next_step(window):
    from openexpress.progress import Waiting

    window.progress(Waiting(TRUEUP_WAIT))
    assert window.progress_label.text == TRUEUP_WAIT
    ((interval, _fn),) = window.timers.values()
    assert 100 <= interval <= 200  # about 150 ms
    pulses = window.progress_bar.pulses
    assert _tick(window) is True and _tick(window) is True  # GLib calls it again
    assert window.progress_bar.pulses == pulses + 2
    # A known fraction again (a download, a copy): the bar shows it, the moving stops.
    window.fraction(3, 4)
    assert window.timers == {} and window.progress_bar.fraction == 0.75
    # The next step stops it too, and so does the end of the task.
    window.progress(Waiting("Checking for files from Garmin Connect…"))
    window.progress("Downloading A course")
    assert window.timers == {} and window.progress_label.text == "Downloading A course"
    window.progress(Waiting(TRUEUP_WAIT))
    window.set_busy(False)
    assert window.timers == {}


def test_a_step_that_is_no_wait_moves_the_bar_once(window):
    window.progress("Uploading activity (1.0 kB)")
    assert window.timers == {} and window.progress_bar.pulses == 1


def test_after_20_s_of_waiting_the_label_says_it_can_take_a_while(window, monkeypatch):
    from openexpress.progress import Waiting

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def no_sleep(seconds):
        raise AssertionError("the window must not sleep: GTK would stand still")

    monkeypatch.setattr(time, "sleep", no_sleep)
    window.progress(Waiting(TRUEUP_WAIT))  # returns at once: the waiting is GLib's timer
    clock[0] += 19.5
    assert _tick(window) is True and window.progress_label.text == TRUEUP_WAIT
    clock[0] += 1
    assert _tick(window) is True
    assert window.progress_label.text == f"{TRUEUP_WAIT} {NOTE}"
    clock[0] += 30
    _tick(window)
    assert window.progress_label.text == f"{TRUEUP_WAIT} {NOTE}"  # said once
    # A new wait starts its own count.
    window.progress(Waiting("Checking for files from Garmin Connect…"))
    assert _tick(window) is True
    assert window.progress_label.text == "Checking for files from Garmin Connect…"


def test_a_wait_of_known_length_fills_the_bar_and_can_be_stopped(window):
    """TrueUp's wait has a known length: the bar shows how much of it has passed (no moving
    back and forth, no note after 20 s), and Stop Waiting is offered while it lasts."""
    import threading

    from openexpress.gui.window_tasks import stop_waiting
    from openexpress.progress import TimedWait

    text = "Waiting 3 min 20 s for Garmin Connect to prepare TrueUp…"
    window.session = types.SimpleNamespace(stop_waiting=threading.Event())
    window.progress(TimedWait(text, seconds=200, done=0.4))
    assert window.progress_bar.fraction == 0.4 and window.timers == {}
    assert window.progress_label.text == text and window.stop_wait_button.visible
    stop_waiting(window)
    assert window.session.stop_waiting.is_set() and not window.stop_wait_button.visible
    window.progress(TimedWait(text, seconds=200, done=0.5))  # the step that was under way
    assert not window.stop_wait_button.visible
    window.session.stop_waiting.clear()
    window.progress(TimedWait(text, seconds=200, done=1.0))
    assert not window.stop_wait_button.visible  # over
    window.progress(TimedWait(text, seconds=200, done=0.1))
    window.progress("TrueUp: writing daily totals to the device")  # another step
    assert not window.stop_wait_button.visible
    window.progress(TimedWait(text, seconds=200, done=0.1))
    window.set_busy(False)
    assert not window.stop_wait_button.visible


# --- one busy indicator: the spinner until the bar has something to show ------------------------

def _indicators(win):
    return win.busy_spinner.visible, win.progress_bar.visible


def test_a_task_starts_with_the_spinner_and_no_bar(window):
    """A task that tells no progress (detection, login, …): the spinner alone, not an empty
    bar that would look stuck."""
    window.set_busy(True, "Detecting the device…")
    assert _indicators(window) == (True, False)
    assert window.progress_label.text == "Detecting the device…"


@pytest.mark.parametrize("first", ["step", "fraction", "wait"])
def test_the_first_progress_turns_the_spinner_into_the_bar_for_good(window, first):
    from openexpress.progress import Waiting

    window.set_busy(True, "Syncing…")
    {"step": lambda: window.progress("Uploading activity (1.0 kB)"),
     "fraction": lambda: window.fraction(1, 4),
     "wait": lambda: window.progress(Waiting(TRUEUP_WAIT))}[first]()
    assert _indicators(window) == (False, True)
    window.progress("Downloading A course")  # one way only: never back to the spinner
    window.fraction(2, 4)
    window.progress(Waiting(TRUEUP_WAIT))
    assert _indicators(window) == (False, True)
    window.set_busy(False)
    window.set_busy(True, "Installing apps…")  # the next task starts anew
    assert _indicators(window) == (True, False)


def test_a_page_that_loads_has_its_own_indicator_only(window):
    """The page shows that it loads; the bottom bar keeps its words (and "do not unplug"),
    without a spinner or a bar of its own."""
    window.session = "device"
    window.set_busy(True, "Checking maps…", device=True, in_page=True)
    assert _indicators(window) == (False, False)
    assert window.progress_label.text == "Checking maps…" and window.unplug_label.visible


def test_loading_a_page_runs_its_task_as_one_with_its_own_indicator():
    from openexpress.gui.app import MainWindow

    asked = []
    section = types.SimpleNamespace(show=lambda name: None)
    win = stand_in(
        sections={"maps": section}, session="device", _retry_sources={}, busy=False,
        run_task=lambda *args, **kw: asked.append(kw))
    MainWindow._load(win, "maps", "Checking maps…", lambda: None, lambda result: None)
    assert asked == [{"device": True, "in_page": True}]
