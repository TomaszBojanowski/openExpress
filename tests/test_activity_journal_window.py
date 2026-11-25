"""The activity journal in the window: brought up to date in the background once a device is
opened, after its diagnostics are read, once a session, only after the user's yes and never for
a device on another Garmin Connect account; before the yes and after a no nothing is read for
it. Made-up files only; a folder stands for the device."""

import json
import types

import pytest
from conftest import DEVICE_XML
from fakes import device_session, fit_activity, fit_time, window

pytestmark = pytest.mark.journal_answer

UNIT = 1234567890  # the unit ID of DEVICE_XML


@pytest.fixture
def opened(tmp_path):
    """A session of a folder as a watch with two activities, its reads counted."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    root = tmp_path / "device"
    (root / "GARMIN" / "Activity").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    for day in (29, 30):
        (root / "GARMIN" / "Activity" / f"2026-10-{day}-07-00-00.fit").write_bytes(
            fit_activity(fit_time(2026, 10, day, 7, 0)))
    storage = LocalStorage(root)
    reads = []
    plain = storage.read_at
    storage.read_at = lambda path, told=None: reads.append(path) or plain(path, told)
    s = device_session(device=parse(storage.read("GARMIN/GarminDevice.xml")), storage=storage)
    return types.SimpleNamespace(session=s, reads=reads)


def answer(value) -> None:
    from openexpress.settings import Settings

    s = Settings.load()
    s.activity_journal = value
    s.save()


def journal():
    from openexpress import activity_journal

    return activity_journal.path(UNIT)


def at_once(fn, done, failed=None):
    """The window's background work done at once, in this thread."""
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 - as the window's in_background passes it on
        if failed is not None:
            failed(e)
        return
    done(result)


@pytest.mark.parametrize("value", [None, False])
def test_before_the_yes_the_window_reads_nothing_for_it(opened, value):
    from openexpress.gui.activities_journal import read_journal

    answer(value)
    started = []
    win = window(session=opened.session, in_background=lambda *a: started.append(a))
    read_journal(win)
    assert started == [] and opened.reads == [] and not journal().exists()


def test_after_the_yes_once_a_session(opened):
    from openexpress.gui.activities_journal import read_journal

    answer(True)
    win = window(session=opened.session, in_background=at_once)
    read_journal(win)
    assert len(opened.reads) == 2 and journal().is_file()
    read_journal(win)  # the page shown again, the same session
    assert len(opened.reads) == 2


def test_a_device_on_another_account_reads_nothing(opened):
    from openexpress.connect import NotAccountDevice
    from openexpress.gui.activities_journal import read_journal

    answer(True)
    opened.session.ownership_error = NotAccountDevice("made up: on another account")
    win = window(session=opened.session, in_background=at_once)
    read_journal(win)
    assert opened.reads == [] and not journal().exists()


def test_it_starts_once_the_diagnostics_are_read(opened, monkeypatch, in_the_window):
    from openexpress import diagnostics
    from openexpress.gui import device_page

    answer(True)
    started = []
    in_the_window("read_journal", lambda win: started.append(win.session))
    monkeypatch.setattr(diagnostics, "at_open", lambda device, storage: None)
    in_the_window("status_row", lambda win, group=None: None)
    background = []
    win = window(session=opened.session,
                 in_background=lambda fn, done, failed=None: background.append((fn, done)))
    device_page.read_diagnostics(win)
    (work, done), = background
    assert started == []
    done(work())
    assert started == [opened.session]


# --- the switch in the preferences, with a device open -----------------------------------------

def preferences_of(win):
    """The journal's switch of the preferences, in the stand-in `win` (its toasts and errors
    recorded)."""
    from fakes import descendants
    from gi.repository import Adw

    from openexpress.gui.preferences import journal_group

    win.toast = lambda text, on_preferences=False: None
    win.error = lambda title, text, private=False: pytest.fail(f"{title}: {text}")
    (switch,) = [w for w in descendants(journal_group(win)) if isinstance(w, Adw.SwitchRow)]
    return switch


def activity_lines() -> list[dict]:
    """The activity lines of the journal as written, each one (not only the last of a key)."""
    return [line for line in map(json.loads, journal().read_text().splitlines())
            if line.get("kind") == "activity"]


@pytest.mark.needs_display
def test_switched_on_with_a_device_open_the_reading_starts_at_once(opened):
    answer(None)
    win = window(session=opened.session, in_background=at_once)
    preferences_of(win).set_active(True)
    assert len(opened.reads) == 2 and len(activity_lines()) == 2


@pytest.mark.needs_display
def test_switched_on_without_a_device_open_nothing_is_read(opened):
    answer(None)
    win = window(session=None, in_background=at_once)
    preferences_of(win).set_active(True)
    assert opened.reads == [] and not journal().exists()


@pytest.mark.needs_display
def test_switched_on_for_a_device_on_another_account_nothing_is_read(opened):
    from openexpress.connect import NotAccountDevice

    answer(None)
    opened.session.ownership_error = NotAccountDevice("made up: on another account")
    win = window(session=opened.session, in_background=at_once)
    preferences_of(win).set_active(True)
    assert opened.reads == [] and not journal().exists()


@pytest.mark.needs_display
def test_switched_off_meanwhile_the_reading_stops_and_on_again_it_goes_on_once(opened):
    """Turned off while it reads: what it wrote stays, the rest is not read. Turned on again
    with the device still open: the rest read, nothing twice."""
    answer(None)
    win = window(session=opened.session, in_background=at_once)
    switch = preferences_of(win)
    plain = opened.session.storage.read_at

    def off_at_the_second(path, told=None):
        data = plain(path, told)
        if len(opened.reads) == 2:
            # Turned off (what the switch saves) while the second file is read: the first is
            # written, the second not yet.
            answer(False)
        return data
    opened.session.storage.read_at = off_at_the_second
    switch.set_active(True)
    assert len(opened.reads) == 2 and len(activity_lines()) == 1
    opened.session.storage.read_at = plain
    switch.set_active(False)  # the switch as the answer is now: nothing saved, nothing read
    assert len(opened.reads) == 2
    switch.set_active(True)
    lines = activity_lines()
    assert len(opened.reads) == 3  # only the one not written
    assert len(lines) == 2 and len({line["key"] for line in lines}) == 2
