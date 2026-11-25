"""The activity journal in the window: brought up to date in the background once a device is
opened, after its diagnostics are read, once a session, only after the user's yes and never for
a device on another Garmin Connect account; before the yes and after a no nothing is read for
it. Made-up files only; a folder stands for the device."""

import types

import pytest
from conftest import DEVICE_XML
from fakes import device_session, fit_activity, fit_time, window

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
    monkeypatch.setattr(device_page, "read_journal", lambda win: started.append(win.session))
    monkeypatch.setattr(diagnostics, "at_open", lambda device, storage: None)
    in_the_window("status_changed", lambda win: None)
    in_the_window("show_satellite", lambda win: None)
    background = []
    win = window(session=opened.session,
                 in_background=lambda fn, done, failed=None: background.append((fn, done)))
    device_page.read_diagnostics(win)
    (work, done), = background
    assert started == []
    done(work())
    assert started == [opened.session]
