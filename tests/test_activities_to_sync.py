"""The activities on a connected device that this installation has not uploaded yet (the chip
"To sync"): its activity folder (GarminDevice.xml) listed, the names held against
sync/state.json and the copies kept after a failed upload; no file is read. All devices and
files are made up."""

import logging
import re
import types

import pytest
from fakes import SyncClient
from fakes import window as stand_in

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


class Uncounted:
    """sync.activities_to_sync that must not be called: the thread that reads the list or
    opens a device does not count (it is counted in the background once drawn)."""

    def __init__(self):
        self.calls = []

    def __call__(self, device, storage):
        self.calls.append(device.unit_id)
        return sync.ActivitiesToSync(device.unit_id, frozenset(), 0)


def _second_device(device_root, tmp_path):
    """A copy of the made-up device with another unit ID."""
    import shutil

    other = tmp_path / "other"
    shutil.copytree(device_root, other)
    xml = other / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("<Id>1234567890</Id>", "<Id>3000000002</Id>"))
    return other


def test_the_list_of_devices_counts_nothing_and_keeps_what_it_opened(device_root, tmp_path,
                                                                     monkeypatch, in_the_window):
    """The window's list (the detection's and the one read again while a device is open):
    drawn without waiting for a count; each MTP device keeps what probe opened (its
    GarminDevice.xml and storage), for the count in the background; a drive keeps none."""
    from openexpress.device import gio_storage
    from openexpress.gui import app

    roots = {"mtp://one/": device_root, "mtp://two/": _second_device(device_root, tmp_path)}
    monkeypatch.setattr(gio_storage, "_mtp_storage",
                        lambda uri, usb: LocalStorage(roots[uri]))
    counted = Uncounted()
    monkeypatch.setattr(sync, "activities_to_sync", counted)
    found = [gio_storage.FoundDevice(uri, "x", True,
                                     usb=gio_storage.GarminUsb(product, 1, number))
             for uri, product, number in (("mtp://one/", "51b4", 9), ("mtp://two/", "5158", 10))]
    choices = app._choose(found)
    assert isinstance(choices, app.DeviceChoices)
    in_the_window("find_all_devices", lambda: found)
    for summaries in (choices.summaries, app.connected_devices()):
        assert [s.to_sync for s in summaries] == [None, None]
        assert sorted(s.device.unit_id for s in summaries) == [1234567890, 3000000002]
        assert all(isinstance(s.storage, LocalStorage) for s in summaries)
    assert counted.calls == []
    drive = gio_storage.FoundDevice(device_root.as_uri(), "GARMIN", True,
                                    usb=gio_storage.GarminUsb("20ee", 1, 12), kind="ums")
    summary = gio_storage.probe(drive)
    assert summary.ok and summary.device is None and summary.storage is None


def _session(device, storage, **fields):
    values = dict(device=device, storage=storage, mass_storage=False, ownership_error=None,
                  to_sync=None, to_sync_asked=False)
    values.update(fields)
    return types.SimpleNamespace(**values)


def test_opening_a_device_from_the_list_counts_nothing_in_its_thread(device_root,
                                                                   monkeypatch, in_the_window):
    """The device chosen on the start page: its view is drawn without waiting for a count."""
    from fakes import RunNow

    from openexpress import service
    from openexpress.gui import app

    device, storage = _device(device_root)
    _synced(device)
    session = _session(device, storage, logged_in=True)
    monkeypatch.setattr(service, "open_session", lambda found: session)
    monkeypatch.setattr(service, "check_session", lambda s: s)
    in_the_window("read_space", lambda storage: (None, None))
    counted = Uncounted()
    monkeypatch.setattr(sync, "activities_to_sync", counted)
    win = stand_in(busy=False, run_task=RunNow(), show_device=lambda: None,
                   connected=types.SimpleNamespace(stop_choosing=lambda: None))
    app.MainWindow.open_choice(win, "the device chosen")
    assert win.session is session and session.to_sync is None and counted.calls == []


def test_detecting_the_device_counts_nothing_in_its_thread(device_root, monkeypatch, in_the_window):
    """The one device connected, found by a detection: drawn without waiting for a count."""
    from fakes import RunNow

    from openexpress.gui import app

    device, storage = _device(device_root)
    _synced(device)
    session = _session(device, storage)
    in_the_window("detect_devices", lambda current_uri=None: session)
    in_the_window("read_space", lambda storage: (None, None))
    counted = Uncounted()
    monkeypatch.setattr(sync, "activities_to_sync", counted)
    win = stand_in(
        busy=False, session=None, run_task=RunNow(), show_device=lambda: None,
        connected=types.SimpleNamespace(found_alone=lambda: None, stop_choosing=lambda: None),
        sections={"watch": types.SimpleNamespace(show=lambda what: None)})
    app.MainWindow.refresh_device(win)
    assert win.session is session and session.to_sync is None and counted.calls == []
