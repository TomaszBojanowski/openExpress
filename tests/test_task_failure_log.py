"""A task of the window, or work in the background, that fails: its traceback goes to the log
file, filtered, so that a report from the window tells where it failed (from the background at
INFO: DEBUG is below the level of the file). A message for the user (errors.UserFacingError) is
one line at INFO. All addresses are made up."""

import types

import pytest
from fakes import replace_in_window
from fakes import window as stand_in

ADDRESS = "mtp://Garmin_Edge_1050_0a1b2c3d4e5f/"
EMAIL = "name@example.com"


def _at_once(monkeypatch):
    """The window's thread and main loop, run at once."""
    from gi.repository import GLib

    replace_in_window(monkeypatch, "threading", types.SimpleNamespace(
        Thread=lambda target, daemon: types.SimpleNamespace(start=target)))
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: fn(*args))


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
    win = stand_in(busy=False, session=None, set_busy=lambda *a, **kw: None,
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
    assert told == [f"could not read {ADDRESS} for {EMAIL}"]  # the user is told it in full


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


# --- an ordinary stop is one line of INFO, not a failure -----------------------------------------

def _ordinary_stops():
    from openexpress import cache, forget, service
    from openexpress.auth import sso
    from openexpress.device import gio_storage

    return [service.FirstSyncChoiceNeeded, service.SyncBusy, gio_storage.MassStorageNotice,
            sso.LoginCancelled, forget.PendingFiles, cache.CacheBusy]


def test_a_first_sync_that_waits_for_its_choice_is_no_failure_in_the_log(device_root, xdg,
                                                                          log_file):
    """The service is asked to sync a device for the first time without the choice: one line
    of INFO says what it waits for; no WARNING, nothing "stopped" or "failed"."""
    from fakes import SyncAccount

    from openexpress import applog, service
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.settings import Settings

    st = LocalStorage(device_root)
    s = service.DeviceSession(st, parse(st.read("GARMIN/GarminDevice.xml")), Settings(),
                              SyncAccount(verified_unit_id=None))
    applog.setup("gui")
    with pytest.raises(service.FirstSyncChoiceNeeded):
        service.run_sync(s)
    applog.close()
    lines = _lines(log_file, " sync: ")
    assert [line for line in lines if " WARNING " in line] == []
    assert not any("stopped" in line or "failed" in line for line in lines)
    (said,) = _lines(log_file, "sync: waiting for the first-sync choice")
    assert " INFO " in said


@pytest.mark.parametrize("index", range(6))
def test_an_ordinary_stop_of_a_task_is_one_line_of_info(monkeypatch, log_file, index):
    """A task of the window, or work in the background, that ends in an ordinary stop: one
    line of INFO with what it waits for; no "failed", no traceback. The user is told as
    before."""
    from openexpress import applog
    from openexpress.errors import OrdinaryStop, UserFacingError
    from openexpress.gui import app

    stop = _ordinary_stops()[index]
    assert issubclass(stop, OrdinaryStop) and issubclass(stop, UserFacingError)
    assert stop.log_reason  # what the line of the log says

    from openexpress import forget

    # PendingFiles makes its own words from the number of files.
    made = stop(2) if stop is forget.PendingFiles else stop("Made-up words for the user.")

    def work():
        raise made

    _at_once(monkeypatch)
    told = _run(work)
    applog.setup("gui")
    app.MainWindow.in_background(work, lambda result: None, lambda error: None)
    applog.close()
    text = log_file.read_text()
    assert "failed" not in text and "Traceback" not in text and " WARNING " not in text
    said = _lines(log_file, f": {stop.log_reason}")
    assert len(said) == 2 and all(" INFO " in line for line in said)
    assert told == [str(made)]


def test_an_ordinary_stop_on_the_command_line_is_no_error_in_the_log(log_file, capsys):
    import types

    from openexpress import applog, cli, service

    def busy(_args):
        raise service.SyncBusy("A sync is already running in another process.")

    applog.setup("cli")
    code = cli._run(types.SimpleNamespace(func=busy, verbose=False), service, object())
    applog.close()
    assert code == 1
    assert "A sync is already running in another process." in capsys.readouterr().err
    assert "error:" not in log_file.read_text()
    (said,) = _lines(log_file, "stopped: another sync is running")
    assert " INFO " in said
