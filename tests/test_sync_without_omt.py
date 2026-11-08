"""Garmin's server that says what to upload and where (OMT) cannot be reached, or answers with
an error, while Garmin Connect can: the sync skips the uploads, says so naming that server, and
does all the rest - the files of Garmin Connect's queue are written to the device and
acknowledged. The window, the command line and the dry run alike. All answers are made up."""

import shutil

import pytest
import requests
from fakes import COURSE, RunNow, SyncAccount
from fakes import window as stand_in

from openexpress import applog, cli, service, sync
from openexpress.connect import ApiError
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

UNIT = SyncAccount.UNIT
REFUSED = requests.ConnectionError(
    "HTTPSConnectionPool(host='omt.garmin.com', port=443): Max retries exceeded with url: "
    f"/UploadConfigurationService/UnitUploadSettings/{UNIT}?clientId=express-windows (Caused "
    "by NewConnectionError('<urllib3.connection.HTTPSConnection object>: Failed to establish "
    "a new connection: [Errno 111] Connection refused'))")
SKIPPED = "Uploads skipped: omt.garmin.com, Garmin's server that says what to upload, "
NOT_REACHED = SKIPPED + "could not be reached ("
ANSWERED = SKIPPED + "answered with an error ("


class WithoutOmt(SyncAccount):
    """The account, Garmin Connect answering, its queue a course; the upload configuration
    raises `error` (the other requests to OMT are not made by a sync)."""

    def __init__(self, error, **kw):
        super().__init__(messages=[COURSE], **kw)
        self.error = error

    def upload_settings(self, unit_id):
        self.asked.append("upload settings")
        raise self.error


@pytest.fixture
def device(device_root, tmp_path):
    """A copy of the test device, synced here before (not a first sync)."""
    root = tmp_path / "synced-device"
    shutil.copytree(device_root, root)
    state = sync.SyncState(UNIT)
    state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-01-10-00-00.fit")
    state.save()
    return root


def written(root) -> list[str]:
    return sorted(p.name.split("_")[0] for p in (root / "GARMIN" / "NewFiles").iterdir())


@pytest.mark.parametrize(("error", "said"), [(REFUSED, NOT_REACHED),
                                             (ApiError("Upload configuration: HTTP 503", 503),
                                              ANSWERED)])
def test_the_command_line_writes_the_queue_and_says_why_nothing_was_uploaded(
        device, monkeypatch, capsys, error, said):
    account = WithoutOmt(error)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    try:
        code = cli.main(["--path", str(device), "sync", "--apply"])
    finally:
        applog.close()
    out = capsys.readouterr().out
    assert code == 1  # done, with an error
    assert written(device) == ["course"]
    assert account.acks == [5] and account.uploads == []
    errors = [line.strip() for line in out.splitlines() if line.strip().startswith("Error: ")]
    assert len(errors) == 1 and errors[0].startswith("Error: " + said)
    assert str(UNIT) not in errors[0]  # the address of the request is left out
    record = service.load_last_sync(UNIT)
    assert record["downloaded"] == 1 and record["errors"][0].startswith(said)


def test_the_window_writes_the_queue_and_says_why_nothing_was_uploaded(device):
    from openexpress.gui import app

    storage = LocalStorage(device)
    account = WithoutOmt(REFUSED)
    said = []
    win = stand_in(
        session=service.DeviceSession(storage, parse(storage.read("GARMIN/GarminDevice.xml")),
                                      Settings(), account),
        run_task=RunNow(error="record"), progress=lambda *a: None, toast=said.append,
        _show_last_sync=lambda: None, _count_activities_again=lambda: None,
        ask_first_sync=lambda: said.append("first sync"), show_device=lambda: None)
    applog.setup("gui")
    try:
        app.MainWindow.start_sync(win)
    finally:
        applog.close()
    assert said == ["Sync finished with errors"] and win.run_task.errors == []
    assert written(device) == ["course"] and account.acks == [5]
    record = service.load_last_sync(UNIT)
    assert record["downloaded"] == 1 and record["errors"][0].startswith(NOT_REACHED)


def test_the_dry_run_plans_the_queue_and_says_why_nothing_would_be_uploaded(device,
                                                                           monkeypatch,
                                                                           capsys):
    account = WithoutOmt(REFUSED)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    assert cli.main(["--path", str(device), "sync"]) == 0
    out = capsys.readouterr().out
    assert any(line.startswith(NOT_REACHED) for line in out.splitlines())
    assert "To download from Garmin Connect to the device: 1" in out
    assert written(device) == [] and account.acks == []


def test_a_first_sync_without_omt_writes_the_queue_and_leaves_the_choice(device, monkeypatch,
                                                                       capsys):
    """The choice of the first sync is about the uploads: it stays to be made."""
    sync.state_path(UNIT).unlink()
    account = WithoutOmt(REFUSED)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    try:
        assert cli.main(["--path", str(device), "sync", "--apply"]) == 1
    finally:
        applog.close()
    assert written(device) == ["course"]
    assert not sync.state_path(UNIT).exists()
