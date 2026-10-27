"""A task of the window that fails (code audit of 25.10.2026): its traceback goes to the log
file with the line that says so, through the filter of the log (addresses, e-mails…), so that
a report from the window tells where it failed; work in the background that fails likewise,
at INFO. Before, both tracebacks were written at DEBUG only, below the level of the file.
Since 26.10.2026 only an error nobody expected has its traceback: one that is a message for
the user (errors.UserFacingError) is one line at INFO, as before. All addresses are made up."""

import types

import pytest
from fakes import replace_in_window

ADDRESS = "mtp://Garmin_Edge_1050_0a1b2c3d4e5f/"
EMAIL = "name@example.com"


def _at_once(monkeypatch):
    """The window's thread and main loop, run at once."""
    from openexpress.gui import app

    replace_in_window(monkeypatch, "threading", types.SimpleNamespace(
        Thread=lambda target, daemon: types.SimpleNamespace(start=target)))
    monkeypatch.setattr(app.GLib, "idle_add", lambda fn, *args: fn(*args))


def _failing_work():
    raise RuntimeError(f"could not read {ADDRESS} for {EMAIL}")


def _work_not_ready():
    from openexpress.device.gio_storage import NotThereYet

    raise NotThereYet(f"{ADDRESS} is not ready yet")


def _lines(log_file, words):
    return [line for line in log_file.read_text().splitlines() if words in line]


def _run(work):
    """`work` as a task of the window; what the window told the user."""
    from openexpress import applog
    from openexpress.gui import app

    applog.setup("gui")
    told = []
    win = types.SimpleNamespace(busy=False, session=None, set_busy=lambda *a, **kw: None,
                                error=lambda title, text: told.append(text),
                                _run_pending_refresh=lambda: None)
    app.MainWindow.run_task(win, "Reading…", work)
    applog.close()
    return told


def test_a_failed_task_writes_its_traceback_to_the_log_filtered(monkeypatch, log_file):
    _at_once(monkeypatch)
    told = _run(_failing_work)
    (line,) = _lines(log_file, "task failed: RuntimeError")
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
    (line,) = _lines(log_file, "failed: RuntimeError")
    assert " INFO " in line and "_failing_work" in line
    text = log_file.read_text()
    assert "Traceback (most recent call last)" in text
    assert ADDRESS not in text and EMAIL not in text
    assert len(failed) == 1


def test_a_task_failed_with_a_message_for_the_user_is_one_line(monkeypatch, log_file):
    """The device not ready yet: what the user is told, in one line at INFO, filtered; no
    traceback (nothing went wrong in the program)."""
    _at_once(monkeypatch)
    told = _run(_work_not_ready)
    (line,) = _lines(log_file, "task failed: NotThereYet: ")
    assert " INFO " in line and line.endswith("is not ready yet")
    text = log_file.read_text()
    assert "Traceback" not in text and ADDRESS not in text
    assert told == [f"{ADDRESS} is not ready yet"]


def test_work_in_the_background_with_a_message_for_the_user_is_one_line(monkeypatch,
                                                                        log_file):
    from openexpress import applog
    from openexpress.gui import app

    _at_once(monkeypatch)
    applog.setup("gui")
    app.MainWindow.in_background(_work_not_ready, lambda result: None, lambda error: None)
    applog.close()
    (line,) = _lines(log_file, "failed: NotThereYet: ")
    assert " INFO " in line and "Traceback" not in log_file.read_text()


def _messages_for_the_user():
    from openexpress import cache, ciq, connect, download, forget, maps, music, service, updates
    from openexpress.auth import sso, store
    from openexpress.device import gio_storage, ptp, storage
    from openexpress.settings import SetupError

    return [gio_storage.NotThereYet, gio_storage.MassStorageNotice, gio_storage.DriveNotMounted,
            gio_storage.SeveralDevices, storage.DeviceError, ptp.PtpError,
            connect.OwnershipError, connect.NotAccountDevice, connect.OwnershipUnknown,
            connect.DeviceNotVerified, connect.ApiError, connect.Unauthorized,
            connect.ClockSkew, store.NotLoggedIn, sso.LoginCancelled, SetupError,
            service.MassStorageMode, service.SyncBusy, service.PrimaryTrainingError,
            service.FirstSyncChoiceNeeded, ciq.CiqError, download.DownloadError,
            maps.MapError, maps.MapsNotSupported, music.MusicError, updates.UpdateError,
            cache.CacheBusy, forget.PendingFiles]


@pytest.mark.parametrize("index", range(28))
def test_the_errors_that_are_a_message_for_the_user_have_one_base(index):
    from openexpress.errors import UserFacingError

    error = _messages_for_the_user()[index]
    assert issubclass(error, UserFacingError), error.__name__
