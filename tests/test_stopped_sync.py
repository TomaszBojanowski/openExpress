"""A sync stopped after its uploads, also by Ctrl+C on the command line, keeps its record: its
files are in Garmin Connect and state.json is written, so last_sync.json says what it did and,
in a line of its errors, why it stopped. A sync stopped before its uploads did nothing and keeps
no record. All answers are made up."""

import json
import shutil
import threading
import types

import pytest
from fakes import RunNow, SyncAccount
from fakes import window as stand_in

from openexpress import applog, cli, service, sync
from openexpress.connect import ApiError
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

UNIT = SyncAccount.UNIT
PATTERN = "format-0.7.10-stopped.json"
STOPPED = "The sync stopped before the end: Connect file queue: HTTP 503"
INTERRUPTED = "The sync was interrupted before the end (Ctrl+C)"


def _queue_fails(error):
    def device_messages(unit_id):
        raise error

    return device_messages


def _synced_before():
    state = sync.SyncState(UNIT)
    state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-01-10-00-00.fit")
    state.save()


def _session(device_root, tmp_path, account):
    root = tmp_path / "copy"
    shutil.copytree(device_root, root)
    storage = LocalStorage(root)
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    return service.DeviceSession(storage, device, Settings(), account)


def _log(tmp_path):
    return (tmp_path / "state" / "openexpress" / applog.LOG_NAME).read_text()


@pytest.fixture
def folders(tmp_path, monkeypatch):
    for var, name in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                      ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(var, str(tmp_path / name))
    monkeypatch.delenv(applog.ENV, raising=False)
    return tmp_path


def _stopped_by(error, device_root, folders):
    _synced_before()
    account = SyncAccount(verified_unit_id=None)
    account.device_messages = _queue_fails(error)
    s = _session(device_root, folders, account)
    applog.setup("gui")
    try:
        with pytest.raises(type(error)):
            service.run_sync(s)
    finally:
        applog.close()
    return account


def test_a_sync_stopped_after_its_uploads_keeps_its_record(device_root, folders):
    account = _stopped_by(ApiError("Connect file queue: HTTP 503", 503), device_root, folders)
    record = service.load_last_sync(UNIT)
    assert record is not None and len(account.uploads) == 2
    assert record["uploaded"] == 2 and record["errors"] == [STOPPED]
    assert [line["kind"] for line in record["lines"]] == ["new_files", "uploads"]
    assert json.loads(service.device_last_sync_path(UNIT).read_text()) == record
    log = _log(folders)
    assert "WARNING openexpress.service: sync: stopped after " in log
    assert ("INFO openexpress.service: sync: the record of the stopped sync kept: 2 uploaded, "
            "0 already known, 0 downloaded, 1 errors") in log


def test_the_pattern_is_what_a_stopped_sync_writes(device_root, folders):
    from test_last_sync_patterns import PATTERNS

    _stopped_by(ApiError("Connect file queue: HTTP 503", 503), device_root, folders)
    written = service.load_last_sync(UNIT)
    pattern = json.loads((PATTERNS / PATTERN).read_text(encoding="utf-8"))
    leave_out = ("time", "unit_id", "synthetic")
    assert ({k: v for k, v in pattern.items() if k not in leave_out}
            == {k: v for k, v in written.items() if k not in leave_out})


def test_ctrl_c_after_the_uploads_keeps_the_record_too(device_root, folders):
    _stopped_by(KeyboardInterrupt(), device_root, folders)
    record = service.load_last_sync(UNIT)
    assert record is not None and record["uploaded"] == 2
    assert record["errors"] == [INTERRUPTED]
    assert "sync: interrupted after " in _log(folders)


def test_a_sync_stopped_before_its_uploads_keeps_no_record(device_root, folders):
    _synced_before()
    account = SyncAccount(verified_unit_id=None)

    def no_answer(unit_id):
        raise ApiError("Upload settings: HTTP 503", 503)

    account.upload_settings = no_answer
    s = _session(device_root, folders, account)
    with pytest.raises(ApiError):
        service.run_sync(s)
    assert account.uploads == [] and service.load_last_sync(UNIT) is None


def test_the_command_line_tells_the_uploads_before_ctrl_c(device_root, folders, monkeypatch,
                                                          capsys):
    """As after an error: the steps of the uploads, then "Interrupted." (exit code 130)."""
    _synced_before()
    account = SyncAccount(verified_unit_id=None)
    account.device_messages = _queue_fails(KeyboardInterrupt())
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    try:
        assert cli.main(["--path", str(device_root), "sync", "--apply"]) == 130
    finally:
        applog.close()
    said = capsys.readouterr()
    assert "Files from the device: activities, monitoring" in said.out.splitlines()
    assert INTERRUPTED not in said.out  # in the record, not printed: the error line says it
    assert said.err.splitlines()[-1] == "Interrupted."
    assert service.load_last_sync(UNIT)["errors"] == [INTERRUPTED]


def test_the_window_shows_the_record_of_a_stopped_sync(monkeypatch):
    from openexpress.gui import app

    shown = []

    def run_sync(s, progress=None, options=None):
        raise ApiError("Connect file queue: HTTP 503", 503)

    monkeypatch.setattr(service, "run_sync", run_sync)
    s = types.SimpleNamespace(mass_storage=False, logged_in=True, ownership_error=None,
                              stop_waiting=threading.Event())
    win = stand_in(
        session=s, run_task=RunNow(error="record"), progress=lambda *a: None,
        toast=lambda text: None, _show_last_sync=lambda: shown.append("last sync"),
        _count_activities_again=lambda: None)
    app.MainWindow.start_sync(win)
    assert shown == ["last sync"]


def test_the_line_is_kept_in_the_language_of_the_sync(device_root, folders):
    """As every error of a sync: in the words of its moment, here a Polish session."""
    from openexpress import i18n

    i18n.setup("pl")
    try:
        _stopped_by(ApiError("Connect file queue: HTTP 503", 503), device_root, folders)
    finally:
        i18n.setup("en")
    assert service.load_last_sync(UNIT)["errors"] == [
        "Synchronizację przerwano przed końcem: Connect file queue: HTTP 503"]
