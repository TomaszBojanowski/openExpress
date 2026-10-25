"""A task of the window that fails (code audit of 25.10.2026): its traceback goes to the log
file with the line that says so, through the filter of the log (addresses, e-mails…), so that
a report from the window tells where it failed; work in the background that fails likewise,
at INFO. Before, both tracebacks were written at DEBUG only, below the level of the file.
All addresses are made up."""

import types

ADDRESS = "mtp://Garmin_Edge_1050_0a1b2c3d4e5f/"
EMAIL = "name@example.com"


def _at_once(monkeypatch):
    """The window's thread and main loop, run at once."""
    from openexpress.gui import app

    monkeypatch.setattr(app, "threading", types.SimpleNamespace(
        Thread=lambda target, daemon: types.SimpleNamespace(start=target)))
    monkeypatch.setattr(app.GLib, "idle_add", lambda fn, *args: fn(*args))


def _failing_work():
    from openexpress.device.storage import DeviceError

    raise DeviceError(f"could not read {ADDRESS} for {EMAIL}")


def _lines(log_file, words):
    return [line for line in log_file.read_text().splitlines() if words in line]


def test_a_failed_task_writes_its_traceback_to_the_log_filtered(monkeypatch, log_file):
    from openexpress import applog
    from openexpress.gui import app

    _at_once(monkeypatch)
    applog.setup("gui")
    told = []
    win = types.SimpleNamespace(busy=False, session=None, set_busy=lambda *a, **kw: None,
                                error=lambda title, text: told.append(text),
                                _run_pending_refresh=lambda: None)
    app.MainWindow.run_task(win, "Reading…", _failing_work)
    applog.close()
    (line,) = _lines(log_file, "task failed: DeviceError")
    assert " WARNING " in line
    text = log_file.read_text()
    assert "Traceback (most recent call last)" in text and "in _failing_work" in text
    assert ADDRESS not in text and EMAIL not in text and "Garmin_Edge" not in text
    assert told == [f"could not read {ADDRESS} for {EMAIL}"]  # the user is told as before


def test_work_in_the_background_that_fails_writes_its_traceback_at_info(monkeypatch,
                                                                        log_file):
    from openexpress import applog
    from openexpress.gui import app

    _at_once(monkeypatch)
    applog.setup("gui")
    failed = []
    app.MainWindow.in_background(_failing_work, lambda result: None, failed.append)
    applog.close()
    (line,) = _lines(log_file, "failed: DeviceError")
    assert " INFO " in line and "_failing_work" in line
    text = log_file.read_text()
    assert "Traceback (most recent call last)" in text
    assert ADDRESS not in text and EMAIL not in text
    assert len(failed) == 1
