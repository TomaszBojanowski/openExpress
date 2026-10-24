"""The activities on a connected device that this installation has not uploaded yet (PLAN T9,
the chip "To sync", 24.10.2026): its activity folder (GarminDevice.xml) listed, the names held
against sync/state.json and the copies kept after a failed upload; no file is read. All
devices and files are made up."""

import logging
import re
import types

import pytest
from fakes import SyncClient

from openexpress import sync
from openexpress.device.storage import DeviceError, LocalStorage
from openexpress.device.xmldevice import parse


def _device(device_root):
    storage = LocalStorage(device_root)
    return parse(storage.read("GARMIN/GarminDevice.xml")), storage


def _synced(device, done=()):
    """state.json of the device, as a sync here leaves it, with these activities done."""
    state = sync.SyncState(device.unit_id)
    for name in done:
        state.mark_completed(sync.ACTIVITY_TYPE, name)
    state.save()
    return state


class Untouchable:
    """A device that must not be asked anything."""

    mass_storage = False

    def __getattr__(self, name):
        pytest.fail(f"the device was asked: {name}")


def test_a_device_never_synced_here_is_never_and_nothing_is_listed(device_root):
    device, _storage = _device(device_root)
    result = sync.activities_to_sync(device, Untouchable())
    assert result is not None and result.never_synced and result.count is None


def test_the_activities_state_json_does_not_hold_are_counted(device_root):
    """Two activities on the device, one done (its name in other letters' case); a file of
    the folder that is not an activity (notes.txt) is not one."""
    device, storage = _device(device_root)
    _synced(device, done=["2026-10-01-10-00-00.FIT"])
    result = sync.activities_to_sync(device, storage)
    assert result.count == 1 and not result.never_synced
    _synced(device, done=["2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit"])
    assert sync.activities_to_sync(device, storage).count == 0


class Answering(SyncClient):
    """Connect answering each upload in turn with the next of `answers`."""

    def __init__(self, answers, **kw):
        super().__init__(**kw)
        self.answers = list(answers)

    def upload_file(self, url, data, *, is_zip, last_file, authenticated):
        from openexpress.connect import UploadResult

        self.uploads.append((url, len(data), last_file))
        return UploadResult(self.answers.pop(0), {}, None, None)


def test_what_garmin_refused_or_already_has_is_done_a_failed_upload_is_not(device_root):
    """Refused by Garmin (400) and already in Connect (the phone sent it): done, not to sync
    again; a failed upload (500): still to sync."""
    from conftest import make_fit

    (device_root / "GARMIN/Activity/2026-10-03-10-00-00.fit").write_bytes(make_fit(1100172800))
    device, storage = _device(device_root)
    state = _synced(device)
    config = {"datatypes": [{"datatypename": "FIT_TYPE_4", "uploadlocations": [
        {"url": "https://up.example/activity", "requiresauthentication": True}]}]}
    plan = sync.plan_uploads(device, storage, config, state)
    client = Answering([400, 500], matches={"1100086400": True},
                       verified_unit_id=device.unit_id)
    report = sync.run_uploads(plan, device, storage, client, state, wait_for_processing=False)
    assert (len(report.rejected), len(report.already_in_connect), len(report.failed)) == (1, 1, 1)
    assert sync.activities_to_sync(device, storage).count == 1


def test_a_copy_kept_after_a_failed_upload_counts_also_once_gone_from_the_device(device_root):
    device, storage = _device(device_root)
    state = _synced(device, done=["2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit"])
    state.keep_pending(sync.ACTIVITY_TYPE, "2026-09-30-10-00-00.fit", b"made up")
    assert sync.activities_to_sync(device, storage).count == 1
    # Kept and still on the device: one activity, not two.
    state.keep_pending(sync.ACTIVITY_TYPE, "2026-10-02-10-00-00.fit", b"made up")
    state.completed[sync.ACTIVITY_TYPE].discard("2026-10-02-10-00-00.fit")
    state.save()
    assert sync.activities_to_sync(device, storage).count == 2


class Refusing(LocalStorage):
    def walk_files(self, path):
        raise DeviceError("the device went away")


def test_a_folder_that_cannot_be_listed_leaves_it_unknown_never_0(device_root, caplog):
    device, _storage = _device(device_root)
    _synced(device)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert sync.activities_to_sync(device, Refusing(device_root)) is None
    assert "activities: not counted (DeviceError)" in caplog.text


def test_a_state_json_that_cannot_be_read_leaves_it_unknown(device_root):
    device, storage = _device(device_root)
    _synced(device).file.write_text("{not json")
    assert sync.activities_to_sync(device, storage) is None


def test_a_drive_or_a_device_without_a_unit_id_is_not_counted(device_root):
    device, storage = _device(device_root)
    _synced(device)
    drive = types.SimpleNamespace(mass_storage=True)
    assert sync.activities_to_sync(device, drive) is None
    device.unit_id = 0  # GarminDevice.xml without <Id>
    assert sync.activities_to_sync(device, storage) is None


def test_the_log_tells_the_counts_and_no_name(device_root, caplog):
    device, storage = _device(device_root)
    _synced(device, done=["2026-10-01-10-00-00.fit"])
    with caplog.at_level(logging.INFO, logger="openexpress"):
        sync.activities_to_sync(device, storage)
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("activities")]
    assert re.fullmatch(r"activities: 2 on the device, 1 not uploaded yet \(listed in "
                        r"\d+\.\d\d s\)", line)
    assert "2026-10" not in caplog.text and "1234567890" not in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="openexpress"):
        sync.SyncState(device.unit_id).file.unlink()
        sync.activities_to_sync(device, Untouchable())
    assert "activities: not counted, the device was never synced here" in caplog.text


def test_counted_again_after_a_sync_without_asking_the_device(device_root):
    """After a sync (of the window or of the background service): what was listed, against
    state.json as it is now; the device is not asked again."""
    device, storage = _device(device_root)
    _synced(device)
    first = sync.activities_to_sync(device, storage)
    assert first.count == 2
    _synced(device, done=["2026-10-01-10-00-00.fit"])
    again = first.again()
    assert again.count == 1 and again.again().count == 1
    sync.SyncState(device.unit_id).file.unlink()  # (a state.json removed meanwhile)
    assert first.again().never_synced


def test_the_list_of_devices_counts_each_mtp_device_it_reads(device_root, monkeypatch):
    """The window's list of several devices (probe): the activities of each MTP device it
    reads, with the same storage; a drive gets none (it is never synced)."""
    from openexpress.device import gio_storage

    asked = []

    def count(device, storage):
        asked.append((device.unit_id, storage))
        return "counted"

    monkeypatch.setattr(gio_storage, "_mtp_storage",
                        lambda uri, usb: LocalStorage(device_root))
    found = gio_storage.FoundDevice("mtp://made-up/", "x", True,
                                    usb=gio_storage.GarminUsb("5158", "usb", 1, 9))
    summary = gio_storage.probe(found, count_activities=count)
    assert summary.ok and summary.to_sync == "counted" and asked[0][0] == 1234567890
    assert gio_storage.probe(found).to_sync is None  # nothing asked for: nothing counted
    drive = gio_storage.FoundDevice(device_root.as_uri(), "GARMIN", True,
                                    usb=gio_storage.GarminUsb("20ee", "usb", 1, 12), kind="ums")
    assert gio_storage.probe(drive, count_activities=count).to_sync is None
    assert len(asked) == 1


def _session(device, storage, **fields):
    values = dict(device=device, storage=storage, mass_storage=False, ownership_error=None,
                  to_sync=None)
    values.update(fields)
    return types.SimpleNamespace(**values)


def test_the_window_counts_the_device_it_opens_only_one_of_the_account(device_root):
    from openexpress.connect import OwnershipError
    from openexpress.gui import app

    device, storage = _device(device_root)
    _synced(device)
    assert app.with_activities(_session(device, storage)).to_sync.count == 2
    off = _session(device, Untouchable(), ownership_error=OwnershipError("not yours"))
    assert app.with_activities(off).to_sync is None
    drive = _session(device, Untouchable(), mass_storage=True)
    assert app.with_activities(drive).to_sync is None
    choices = app.DeviceChoices([])
    assert app.with_activities(choices) is choices


def test_opening_a_device_from_the_list_counts_its_activities(device_root, monkeypatch):
    """The device chosen on the start page: counted in the thread that opens it."""
    from fakes import RunNow

    from openexpress import service
    from openexpress.gui import app

    device, storage = _device(device_root)
    _synced(device)
    session = _session(device, storage, logged_in=True)
    monkeypatch.setattr(service, "open_session", lambda found: session)
    monkeypatch.setattr(service, "check_session", lambda s: s)
    monkeypatch.setattr(app, "read_space", lambda storage: (None, None))
    win = types.SimpleNamespace(busy=False, run_task=RunNow(), show_device=lambda: None,
                                connected=types.SimpleNamespace(stop_choosing=lambda: None))
    app.MainWindow.open_choice(win, "the device chosen")
    assert win.session is session and session.to_sync.count == 2


def test_detecting_the_device_counts_its_activities(device_root, monkeypatch):
    """The one device connected, found by a detection: counted in the thread that opens it."""
    from fakes import RunNow

    from openexpress.gui import app

    device, storage = _device(device_root)
    _synced(device)
    session = _session(device, storage)
    monkeypatch.setattr(app, "detect_devices", lambda current_uri=None: session)
    monkeypatch.setattr(app, "read_space", lambda storage: (None, None))
    win = types.SimpleNamespace(
        busy=False, session=None, run_task=RunNow(), show_device=lambda: None,
        connected=types.SimpleNamespace(found_alone=lambda: None, stop_choosing=lambda: None),
        sections={"watch": types.SimpleNamespace(show=lambda what: None)})
    app.MainWindow.refresh_device(win)
    assert win.session is session and session.to_sync.count == 2
