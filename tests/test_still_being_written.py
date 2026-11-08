"""A FIT file the device was still writing when it was read (cut short at the size the device
gave when it was connected) is read once more after the device is mounted again, at most once
a sync (Storage.refresh); still cut short, it is not uploaded nor counted as done: the next sync
reads it again. The sync summary says so in one line (window and command line). All devices,
files and answers are made up."""

import json
import logging
import types
from pathlib import Path

import pytest
from fakes import MONITORING_URL, UPLOAD_SETTINGS, SyncAccount, SyncClient, stale_sizes

from openexpress import cli, i18n, progress, service, sync
from openexpress.device.gio_storage import DeviceNotBack
from openexpress.device.storage import LocalStorage, RemountFailed, UmsStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

UNIT = SyncAccount.UNIT
MONITORING = "GARMIN/Monitor/M1.FIT"
ACTIVITY = "GARMIN/Activity/2026-10-01-10-00-00.fit"
SETTINGS_FILE = "GARMIN/Settings/Settings.fit"
SETTINGS_URL = "https://up.example/settings"
# The upload settings of the test device with its settings file, a type of a single file.
WITH_SETTINGS = {"unittypename": "test", "datatypes": [
    *UPLOAD_SETTINGS["datatypes"],
    {"datatypename": "FIT_TYPE_2",
     "uploadlocations": [{"url": SETTINGS_URL, "requiresauthentication": True}]}]}
NB = " "
LINE = ("1 file was still being written by the device – disconnect and reconnect it, then "
        "sync again")


def _cut_monitoring(root: Path) -> bytes:
    monitoring = root / "GARMIN/Monitor/M1.FIT"
    whole = monitoring.read_bytes()
    monitoring.write_bytes(whole[:-4])
    return whole


def _session(root: Path, account):
    storage = LocalStorage(root)
    return service.DeviceSession(storage, parse(storage.read("GARMIN/GarminDevice.xml")),
                                 Settings(), account)


def test_a_sync_says_it_and_leaves_the_file_for_the_next_one(device_root):
    whole = _cut_monitoring(device_root)
    sync.SyncState(UNIT).save()
    summary = service.run_sync(_session(device_root, SyncAccount(verified_unit_id=None)))
    assert summary.uploaded == 2  # the two activities
    assert {"kind": "still_written", "count": 1} in service.load_last_sync(UNIT)["lines"]
    assert not sync.SyncState(UNIT).is_completed("FIT_TYPE_32", "M1.FIT")
    (device_root / "GARMIN/Monitor/M1.FIT").write_bytes(whole)
    summary = service.run_sync(_session(device_root, SyncAccount(verified_unit_id=None)))
    assert summary.uploaded == 1  # now whole
    assert "still_written" not in {line.get("kind") for line in summary.lines
                                   if isinstance(line, dict)}


def test_the_command_line_says_so_once(device_root, monkeypatch, capsys):
    _cut_monitoring(device_root)
    account = SyncAccount(verified_unit_id=None)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    sync.SyncState(UNIT).save()
    assert cli.main(["--path", str(device_root), "sync", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines().count(LINE) == 1


def test_the_words_of_the_line():
    assert service._describe_step({"kind": "still_written", "count": 1}) == [(LINE, True)]
    assert service._describe_step({"kind": "still_written", "count": 3}) == [(
        ("3 files were still being written by the device – disconnect and reconnect it, then "
         "sync again"), True)]
    i18n.setup("pl")
    try:
        words = [service._describe_step({"kind": "still_written", "count": n})[0][0]
                 for n in (1, 3, 5)]
    finally:
        i18n.setup("en")
    rest = f"przez urządzenie – odłącz je i{NB}podłącz ponownie, potem zsynchronizuj jeszcze raz"
    assert words == [f"1 plik był jeszcze zapisywany {rest}",
                     f"3 pliki były jeszcze zapisywane {rest}",
                     f"5 plików było jeszcze zapisywanych {rest}"]


def test_the_synthetic_pattern_is_what_a_sync_writes():
    """tests/data/last_sync/format-0.7.11-still-written.json, a synthetic pattern (no real sync
    has written one yet), has the lines a sync writes for a device with two new files, one of
    them still being written."""
    pattern = json.loads((Path(__file__).parent / "data/last_sync"
                          / "format-0.7.11-still-written.json").read_text(encoding="utf-8"))
    report = types.SimpleNamespace(uploaded=[0], already_in_connect=[], duplicates=[],
                                   rejected=[], failed=[])
    assert pattern["lines"] == [
        service.new_files_step([types.SimpleNamespace(datatype="FIT_TYPE_32"),
                                types.SimpleNamespace(datatype="FIT_TYPE_77")]),
        service.uploads_step(report),
        service.still_written_step(1)]


# --- read again after the device is mounted again ------------------------------------------


def _uploads(root: Path, storage, config=UPLOAD_SETTINGS, said=None):
    """The uploads of the made-up device in `root` read through `storage`: planned, then done,
    as a sync does them."""
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    state = sync.SyncState(UNIT)
    plan = sync.plan_uploads(device, storage, config, state)
    client = SyncClient(verified_unit_id=UNIT)
    report = sync.run_uploads(plan, device, storage, client, state, said)
    return report, client, state


def _short(root: Path, *paths: str) -> dict[str, int]:
    """Each file of `paths` 4 bytes shorter, as gvfs gives it when the device writes it."""
    return {p: (root / p).stat().st_size - 4 for p in paths}


def _sizes_to(client, url):
    return [size for to, size, _last in client.uploads if to == url]


def test_a_cut_short_file_is_read_again_after_a_refresh_and_uploaded_whole(device_root, caplog):
    whole = (device_root / MONITORING).read_bytes()
    storage = stale_sizes(_short(device_root, MONITORING))(device_root)
    said = []
    with caplog.at_level(logging.INFO, logger="openexpress"):
        report, client, state = _uploads(device_root, storage, said=said.append)
    assert len(storage.refreshes) == 1 and storage.refreshes[0] is not None  # its progress
    assert report.incomplete == [] and len(report.uploaded) == 3
    assert _sizes_to(client, MONITORING_URL) == [len(whole)]
    assert state.is_completed("FIT_TYPE_32", "M1.FIT")
    again = [r.getMessage() for r in caplog.records if "read again" in r.getMessage()]
    assert len(again) == 1 and "FIT_TYPE_32" in again[0] and "M1" not in caplog.text


def test_a_single_file_cut_short_when_planned_is_read_again(device_root):
    """A type of a single file is read already by the plan, for its MD5: that data is not
    used again once the device has been refreshed."""
    whole = (device_root / SETTINGS_FILE).read_bytes()
    storage = stale_sizes(_short(device_root, SETTINGS_FILE))(device_root)
    report, client, state = _uploads(device_root, storage, WITH_SETTINGS)
    assert len(storage.refreshes) == 1 and report.incomplete == []
    assert _sizes_to(client, SETTINGS_URL) == [len(whole)]
    assert not state.singleton_changed("FIT_TYPE_2", SETTINGS_FILE, sync.md5(whole))


def test_still_cut_short_after_the_refresh_waits_for_the_next_sync(device_root):
    cut = _short(device_root, MONITORING)
    storage = stale_sizes(cut, still_written=cut)(device_root)
    report, client, state = _uploads(device_root, storage)
    assert len(storage.refreshes) == 1 and report.incomplete == ["FIT_TYPE_32/M1.FIT"]
    assert _sizes_to(client, MONITORING_URL) == []
    assert not state.is_completed("FIT_TYPE_32", "M1.FIT")


def test_one_refresh_a_sync(device_root):
    """An activity cut short (the pass that asks Garmin Connect which activities it has),
    the settings file read cut short by the plan, and monitoring: all whole after one
    refresh."""
    storage = stale_sizes(_short(device_root, ACTIVITY, SETTINGS_FILE, MONITORING))(device_root)
    report, client, _state = _uploads(device_root, storage, WITH_SETTINGS)
    assert len(storage.refreshes) == 1
    assert report.incomplete == [] and len(report.uploaded) == 4
    assert _sizes_to(client, SETTINGS_URL) == [(device_root / SETTINGS_FILE).stat().st_size]


def test_monitoring_keeps_its_order(device_root):
    """The files of monitoring arrive in order: the one read again goes before the next."""
    (device_root / "GARMIN/Monitor/M2.FIT").write_bytes((device_root / MONITORING).read_bytes())
    storage = stale_sizes(_short(device_root, MONITORING))(device_root)
    report, _client, _state = _uploads(device_root, storage)
    assert [u for u in report.uploaded if u.startswith("FIT_TYPE_32/")] == [
        "FIT_TYPE_32/M1.FIT", "FIT_TYPE_32/M2.FIT"]


@pytest.mark.parametrize("kind", ["folder", "usb drive"])
def test_a_folder_and_a_usb_drive_are_not_refreshed(device_root, kind):
    """Their sizes are current: a file cut short there is cut short and left for the next
    sync, and nothing says a refresh."""
    _cut_monitoring(device_root)
    storage = (LocalStorage(device_root) if kind == "folder"
               else UmsStorage(device_root, device_root.as_uri() + "/"))
    said = []
    report, _client, state = _uploads(device_root, storage, said=said.append)
    assert report.incomplete == ["FIT_TYPE_32/M1.FIT"]
    assert not state.is_completed("FIT_TYPE_32", "M1.FIT")
    assert progress.refreshing_text() not in said


@pytest.mark.parametrize("error, told", [
    (RemountFailed("Cannot mount the device: made-up refusal"),
     "Cannot mount the device: made-up refusal. Unplug the device and plug it in again."),
    (DeviceNotBack("The device did not come back. Unplug it and plug it in again."),
     "The device did not come back. Unplug it and plug it in again.")])
def test_a_device_not_mounted_again_stops_the_sync_and_says_to_replug(device_root, error, told):
    """The device is not there to read: the sync stops, with what to do said once; the files
    uploaded before stay done."""
    storage = stale_sizes(_short(device_root, MONITORING), fails=error)(device_root)
    with pytest.raises(RemountFailed) as stopped:
        _uploads(device_root, storage)
    assert str(stopped.value) == told
    state = sync.SyncState(UNIT)
    assert state.is_completed("FIT_TYPE_4", "2026-10-01-10-00-00.fit")
    assert not state.is_completed("FIT_TYPE_32", "M1.FIT")


def test_the_words_of_the_refresh():
    assert progress.refreshing_text() == "Refreshing the device's files…"
    i18n.setup("pl")
    try:
        assert progress.refreshing_text() == "Odświeżanie plików urządzenia…"
    finally:
        i18n.setup("en")
