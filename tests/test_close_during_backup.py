"""The window may be closed during a backup, which only reads the device and writes on this
computer, so nothing on the device is left half-written; once the window is closed, the backup
stops at its next file and its unfinished copy is removed, so the process ends soon instead of
reading the device unseen. Every device is made up."""

import types

import pytest
from fakes import RunNow, replace_in_window

from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse


def _closing_window(**kw):
    values = dict(busy=True, _task_lets_close=False, _close_dialog=None, _closed=False,
                  _net_listener=None, _remember_geometry=lambda: None,
                  _update_relative_timer=lambda: None)
    values.update(kw)
    return types.SimpleNamespace(**values)


def test_the_window_closes_during_a_task_that_lets_it():
    from openexpress.gui.app import MainWindow

    win = _closing_window(_task_lets_close=True)
    assert MainWindow._on_close_request(win, None) is False  # closes
    assert win._closed and win._close_dialog is None


def _at_once(monkeypatch):
    """The window's thread and main loop, run at once."""
    from openexpress.gui import app

    replace_in_window(monkeypatch, "threading", types.SimpleNamespace(
        Thread=lambda target, daemon: types.SimpleNamespace(start=target)))
    monkeypatch.setattr(app.GLib, "idle_add", lambda fn, *args: fn(*args))


@pytest.mark.parametrize("lets", [True, False])
def test_run_task_tells_whether_its_task_lets_the_window_close(monkeypatch, lets):
    from openexpress.gui.app import MainWindow
    from openexpress.gui.tasks import lets_the_window_close

    _at_once(monkeypatch)
    seen = []
    win = types.SimpleNamespace(busy=False, session=None, _task_lets_close=False,
                                set_busy=lambda busy, *a, **kw: setattr(win, "busy", busy),
                                _run_pending_refresh=lambda: None)

    def work():
        seen.append(win._task_lets_close)

    MainWindow.run_task(win, "Working…", lets_the_window_close(work) if lets else work)
    assert seen == [lets]
    assert win._task_lets_close is False  # once it ended


def test_a_window_closed_during_a_task_ends_it_without_a_word(monkeypatch):
    """WindowClosed (the work stopped as the window closed) is no failure to tell."""
    from openexpress.gui.app import MainWindow
    from openexpress.gui.tasks import WindowClosed

    _at_once(monkeypatch)
    told = []
    win = types.SimpleNamespace(busy=False, session=None, _task_lets_close=False,
                                set_busy=lambda busy, *a, **kw: setattr(win, "busy", busy),
                                error=lambda title, text: told.append(text),
                                _run_pending_refresh=lambda: None)

    def work():
        raise WindowClosed

    MainWindow.run_task(win, "Working…", work, on_error=lambda e: told.append(e) and False)
    assert told == [] and win.busy is False


def test_a_backup_stops_once_the_window_is_closed(device_root, backups, log_file):
    from openexpress import applog
    from openexpress.gui.app import MainWindow

    storage = LocalStorage(device_root)
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    copied = []
    win = types.SimpleNamespace(session=types.SimpleNamespace(device=device, storage=storage),
                                run_task=RunNow(), toast=copied.append, _closed=False)

    def progress(path):
        copied.append(path)
        win._closed = True  # the window is closed while the first file is copied

    win.progress = progress
    applog.setup("gui")
    MainWindow.backup(win)
    applog.close()
    (call,) = win.run_task.calls
    assert call.fn.lets_the_window_close
    assert len(copied) == 1  # no file after the closing, no toast
    unit = backups / str(device.unit_id)
    assert list(unit.iterdir()) == []  # neither a backup nor its unfinished copy
    assert "task: stopped, the window was closed" in log_file.read_text()


def test_a_backup_with_the_window_open_makes_its_copy_and_says_so(device_root, backups):
    from openexpress.gui.app import MainWindow

    storage = LocalStorage(device_root)
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    told = []
    win = types.SimpleNamespace(session=types.SimpleNamespace(device=device, storage=storage),
                                run_task=RunNow(), toast=told.append, _closed=False,
                                progress=lambda path: None)
    win.backup_row = None
    MainWindow.backup(win)
    assert len(told) == 1 and told[0].startswith("Copied ")
    (made,) = (backups / str(device.unit_id)).iterdir()
    assert (made / "GarminDevice.xml").is_file()
