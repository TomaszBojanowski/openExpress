"""Point 3 of package 3: TrueUp waits as Garmin Express does. Garmin Connect's `check_back`
says when to ask again: under 300 s the whole time, from 300 s on 90 s, at most three rounds.
The wait has a known length, so it is told with it (the window fills its bar, the command line
one line), and it can be stopped (the window's Stop Waiting, Ctrl+C on the command line, the
background service quitting): nothing more is asked or written, and the sync ends as usual.
Every file and answer here is made up."""

import struct
import threading
import types

import pytest

from openexpress import trueup
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.fit import FIT_EPOCH_OFFSET
from openexpress.progress import TimedWait

NOW = 2_000_000_000
CHECK = {"messageid": 1, "messagetype": "check_back", "devicexmldatatype": "FIT_TYPE_39",
         "metadata": {"messageurl": "cb"}}
TRUE = {"messageid": 2, "messagetype": "true_up", "devicexmldatatype": "FIT_TYPE_39",
        "metadata": {"messageurl": "tu"}}


def check_back_file(delay: float) -> bytes:
    """A check_back FIT file: Garmin Connect asks to check back `delay` s from NOW."""
    definition = bytes([0x40, 0, 0]) + struct.pack("<H", 197) + bytes([1, 0, 4, 0x86])
    body = definition + bytes([0]) + struct.pack("<I", int(NOW + delay - FIT_EPOCH_OFFSET))
    return struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0) + body + b"\0\0"


@pytest.fixture
def watch(device_root):
    xml = (device_root / "GARMIN/GarminDevice.xml").read_text().replace(
        "</MassStorageMode>",
        "<DataType><Name>FIT_TYPE_39</Name><File><Location><Path>Garmin/NewFiles</Path>"
        "<FileExtension>FIT</FileExtension></Location><TransferDirection>InputToUnit"
        "</TransferDirection></File></DataType></MassStorageMode>")
    return parse(xml.encode()), LocalStorage(device_root)


def client(delay: float, later=None):
    """Garmin Connect: the check_back file says `delay`; asked again, the queue is `later`
    (the true_up file by default)."""
    asked = []
    return types.SimpleNamespace(
        download_message=lambda url, absolute: check_back_file(delay) if url == "cb"
        else b"TRUEUP",
        ack_message=lambda mid: None,
        device_messages=lambda unit: asked.append(unit) or list(later or [TRUE]),
        asked=asked)


def written(storage):
    return [p for p, _e in storage.walk_files("Garmin/NewFiles")]


@pytest.mark.parametrize(("delay", "waited"), [
    (-5, 0), (0, 0), (20, 20), (200, 200), (299, 299), (300, 90), (3600, 90)])
def test_trueup_waits_the_whole_check_back_time_under_300_s(watch, delay, waited):
    dev, st = watch
    slept = []
    res = trueup.run(dev, st, client(delay), [CHECK], sleep=slept.append, now=lambda: NOW)
    assert sum(slept) == pytest.approx(waited) and res.waited_s == pytest.approx(waited)
    assert res.written is not None and not res.stopped


def test_trueup_waits_in_ten_steps_and_tells_each(watch):
    dev, st = watch
    slept, seen = [], []
    trueup.run(dev, st, client(200), [CHECK], progress=seen.append, sleep=slept.append,
               now=lambda: NOW)
    assert slept == [20.0] * 10
    waits = [m for m in seen if isinstance(m, TimedWait)]
    assert [(m.seconds, round(m.done, 2)) for m in waits] == [
        (200.0, i / 10) for i in range(11)]
    assert str(waits[0]) == ("Waiting 3\u00a0min 20\u00a0s for Garmin Connect to prepare "
                             "TrueUp (the daily totals of all your devices)…")


def test_trueup_asks_again_at_most_three_times(watch):
    dev, st = watch
    c = client(20, later=[CHECK])
    res = trueup.run(dev, st, c, [CHECK], sleep=lambda s: None, now=lambda: NOW)
    assert len(c.asked) == 3 and res.written is None and written(st) == []


def test_a_stopped_wait_writes_nothing_and_asks_no_more(watch):
    dev, st = watch
    stop = threading.Event()
    seen = []
    c = client(200)

    def step(seconds):  # the user stops it during the third step
        if len([m for m in seen if isinstance(m, TimedWait)]) == 3:
            stop.set()
        return stop.is_set()

    stop.wait = step
    res = trueup.run(dev, st, c, [CHECK], progress=seen.append, now=lambda: NOW, stop=stop)
    assert res.stopped and res.written is None and written(st) == []
    assert c.asked == [] and res.waited_s == pytest.approx(40)
    assert seen[-1] == "Stopped waiting for TrueUp; Garmin Connect sends it with the next sync."


def test_ctrl_c_during_the_wait_stops_it_without_harm(watch):
    dev, st = watch

    def interrupted(seconds):
        raise KeyboardInterrupt

    res = trueup.run(dev, st, client(200), [CHECK], sleep=interrupted, now=lambda: NOW)
    assert res.stopped and res.written is None and written(st) == []


def test_ctrl_c_elsewhere_still_stops_the_program(watch):
    """Only the wait is stopped by Ctrl+C: anywhere else it stops the program, as before."""
    dev, st = watch
    c = client(0)

    def interrupted(unit):
        raise KeyboardInterrupt

    c.device_messages = interrupted
    with pytest.raises(KeyboardInterrupt):
        trueup.run(dev, st, c, [CHECK], sleep=lambda s: None, now=lambda: NOW)


def test_the_duration_in_words():
    from openexpress.i18n import duration_text

    assert [duration_text(s) for s in (0, 45, 60, 200, 299.4, 3600)] == [
        "0\u00a0s", "45\u00a0s", "1\u00a0min", "3\u00a0min 20\u00a0s", "4\u00a0min 59\u00a0s",
        "60\u00a0min"]


# --- the sync and the session ----------------------------------------------------------------


def test_the_sync_passes_the_stop_of_its_session_as_it_is(watch, monkeypatch):
    """A stop asked during this sync (the background service quitting during the uploads)
    ends the wait at once; one left from an earlier sync of the window is cleared when the
    window starts a sync (test_trueup_stop.py). Until 28.10.2026 the service cleared it
    here, and the quit was lost."""
    from openexpress import service

    dev, st = watch
    stop = threading.Event()
    stop.set()  # asked during the uploads
    got = []

    def run(device, storage, client, messages, progress, stop=None):
        got.append(stop.is_set())
        return trueup.TrueUpResult(stopped=True)

    monkeypatch.setattr(trueup, "run", run)
    s = types.SimpleNamespace(device=dev, storage=st, client=None, stop_waiting=stop)
    errors = []
    assert service._trueup(s, [CHECK], lambda m: None, errors) == [{"kind": "trueup_stopped"}]
    assert got == [True] and errors == []


def test_a_session_has_its_own_stop():
    import dataclasses

    from openexpress import service

    field = next(f for f in dataclasses.fields(service.DeviceSession) if f.name == "stop_waiting")
    assert not field.repr and isinstance(field.default_factory(), threading.Event)


def test_the_background_service_stops_the_wait_when_it_quits(monkeypatch):
    from openexpress import watch as watcher

    stop = threading.Event()
    w = types.SimpleNamespace(quit_requested=False, running=True, _lock=threading.Lock(),
                              _session=types.SimpleNamespace(stop_waiting=stop),
                              release=lambda: pytest.fail("released during a sync"))
    watcher.Watcher.request_quit(w)
    assert w.quit_requested and stop.is_set()


# --- the command line ------------------------------------------------------------------------


class _Terminal:
    def __init__(self, tty):
        self.tty, self.text = tty, ""

    def write(self, text):
        self.text += text
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return self.tty


def test_the_command_line_shows_the_wait_on_one_line(monkeypatch, capsys):
    from openexpress import cli

    err = _Terminal(True)
    monkeypatch.setattr(cli.sys, "stderr", err)
    show = cli._SyncProgress()
    text = "Waiting 3 min 20 s for Garmin Connect to prepare TrueUp…"
    for done in (0, 0.5, 1):
        show(TimedWait(text, seconds=200, done=done))
    show("TrueUp: writing daily totals to the device")
    assert err.text == (f"\r  {text}   0%\r  {text}  50%\r  {text} 100%\n")
    assert capsys.readouterr().out == "  TrueUp: writing daily totals to the device\n"


def test_a_stopped_wait_ends_its_line_before_the_next(monkeypatch, capsys):
    from openexpress import cli

    err = _Terminal(True)
    monkeypatch.setattr(cli.sys, "stderr", err)
    show = cli._SyncProgress()
    show(TimedWait("Waiting", seconds=200, done=0.3))
    show("Stopped waiting for TrueUp; Garmin Connect sends it with the next sync.")
    assert err.text == "\r  Waiting  30%\n"


def test_not_on_a_terminal_the_wait_is_one_plain_line(monkeypatch, capsys):
    from openexpress import cli

    monkeypatch.setattr(cli.sys, "stderr", _Terminal(False))
    show = cli._SyncProgress()
    for done in (0, 0.5, 1):
        show(TimedWait("Waiting", seconds=200, done=done))
    assert capsys.readouterr().out == "  Waiting\n"


def test_the_wait_in_polish(watch):
    from openexpress import i18n

    dev, st = watch
    seen = []
    i18n.setup("pl")
    try:
        trueup.run(dev, st, client(200), [CHECK], progress=seen.append, sleep=lambda s: None,
                   now=lambda: NOW, stop=None)
        stopped = trueup.stopped_text()
    finally:
        i18n.setup("en")
    first = next(m for m in seen if isinstance(m, TimedWait))
    assert str(first) == ("Czekanie 3\u00a0min 20\u00a0s, aż Garmin Connect przygotuje TrueUp "
                          "(dzienne sumy ze wszystkich urządzeń)…")
    assert stopped == ("Przerwano czekanie na TrueUp; Garmin Connect wyśle je przy następnej "
                       "synchronizacji.")
