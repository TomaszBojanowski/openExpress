"""A test drive: a USB drive taken for a Garmin drive (gio_storage.TEST_DRIVE_ENV: a USB vendor
other than Garmin's) carries the GarminDevice.xml of a real watch. Its sync uploads its files,
leaves Garmin Connect's queue and TrueUp to the watch (nothing downloaded or acknowledged), says
so once, and keeps its records apart (devices/<unit>/test-drive/): the watch's records and its
tile stay as they were. A Garmin drive syncs with the queue, into the watch's records.
Made-up devices and answers only."""

import json
import types

import pytest
from fakes import COURSE, DriveAccount, SyncAccount, drive_session
from fakes import window as stand_in

from openexpress import cli, known_devices, paths, service, sync, tile_facts

gio_storage = pytest.importorskip("openexpress.device.gio_storage")

UNIT = SyncAccount.UNIT
SAID = ("A test drive: Garmin Connect's queue and TrueUp are left to the device it stands in "
        "for")
VENDOR = "abcd"  # a made-up USB vendor, not Garmin's


def a_test_drive(root, account=None):
    return drive_session(root, account, vendor=VENDOR)


def own_folder():
    return paths.device_dir(UNIT) / "test-drive"


def watch_records():
    """The watch's records: its folder (but the test drive's), the newest sync of any device and
    the remembered devices with the facts of their tiles."""
    folder = paths.device_dir(UNIT)
    found = {(str(p.relative_to(folder)), p.stat().st_mtime_ns, p.read_bytes())
             for p in folder.rglob("*")
             if p.is_file() and "test-drive" not in p.relative_to(folder).parts}
    for path in (service.last_sync_path(), known_devices.path()):
        if path.exists():
            found.add((path.name, path.stat().st_mtime_ns, path.read_bytes()))
    return found


def watch_synced():
    """The watch synced here before: its record of what was sent, its last sync and tile."""
    known_devices.remember(UNIT, "Made-up Watch", "006-B0000-00", "1.00", "mtp")
    sync.SyncState(UNIT).save()
    service.save_last_sync(UNIT, service.SyncSummary(uploaded=1))


def test_a_drive_of_another_vendor_is_a_test_drive_and_a_garmin_drive_is_not(device_root):
    assert a_test_drive(device_root).test_drive
    assert a_test_drive(device_root).storage.test_drive
    assert not drive_session(device_root, None).test_drive


def test_a_test_drive_uploads_and_leaves_the_queue_and_trueup_to_the_watch(device_root):
    watch_synced()
    before = watch_records()
    account = DriveAccount({"connectsupport": "Required"}, messages=[COURSE])
    summary = service.run_sync(a_test_drive(device_root, account),
                               options=service.SyncOptions(drive=True, first_run="all"))
    assert (summary.uploaded, summary.downloaded, summary.errors) == (3, 0, [])
    assert "queue" not in account.asked and account.acks == []
    assert list((device_root / "GARMIN" / "NewFiles").iterdir()) == []
    assert (SAID, False) in service.describe_steps(summary.lines)
    # Its records apart: what it sent and its last sync.
    record = json.loads((own_folder() / "last_sync.json").read_text())
    assert record["uploaded"] == 3 and {"kind": "test_drive"} in record["lines"]
    assert (own_folder() / "sync" / "state.json").exists()
    assert service.load_last_sync(UNIT, test_drive=True)["uploaded"] == 3
    # The watch's records and its tile are as they were.
    assert watch_records() == before
    assert service.load_last_sync(UNIT)["uploaded"] == 1


def test_the_first_sync_of_a_test_drive_starts_anew_though_the_watch_was_synced(device_root):
    watch_synced()
    account = DriveAccount({"connectsupport": "Required"})
    with pytest.raises(service.FirstSyncChoiceNeeded):
        service.run_sync(a_test_drive(device_root, account),
                         options=service.SyncOptions(drive=True))
    assert account.uploads == []


def test_the_dry_run_of_a_test_drive_says_so_and_reads_no_queue(device_root, monkeypatch,
                                                                capsys):
    storage = a_test_drive(device_root).storage
    monkeypatch.setattr(service, "open_device_or_drive", lambda **_kw: storage)
    account = DriveAccount({"connectsupport": "Required"}, messages=[COURSE])
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert SAID in out.splitlines() and "To download from Garmin Connect" not in out
    assert "queue" not in account.asked


def test_a_garmin_drive_syncs_the_queue_into_the_watchs_records(device_root):
    sync.SyncState(UNIT).save()
    account = DriveAccount({"connectsupport": "Required"}, messages=[COURSE])
    summary = service.run_sync(drive_session(device_root, account),
                               options=service.SyncOptions(drive=True))
    assert summary.downloaded == 1 and account.acks == [COURSE["messageid"]]
    assert (SAID, False) not in service.describe_steps(summary.lines)
    assert service.device_last_sync_path(UNIT).exists() and not own_folder().exists()


def test_the_activities_of_a_test_drive_are_counted_against_its_own_record(device_root):
    """Never synced as a test drive: not counted, though the watch was synced; counted
    against its own record, the count is not kept for the watch's tile."""
    watch_synced()
    s = a_test_drive(device_root)
    counted = sync.activities_to_sync(s.device, s.storage)
    assert counted.count is None and counted.test_drive
    sync.state_of(UNIT, test_drive=True).save()
    counted = sync.activities_to_sync(s.device, s.storage)
    assert counted.count == 2 and counted.again().count == 2
    before = watch_records()
    tile_facts.note_count(counted)
    assert watch_records() == before
    assert sync.synced_here(UNIT, test_drive=True)


@pytest.mark.needs_display
def test_the_page_of_a_test_drive_shows_its_own_last_sync(device_root, xdg):
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    watch_synced()
    s = a_test_drive(device_root)
    win = stand_in(last_sync_row=Adw.ExpanderRow(title="Last sync"), _last_sync_children=[],
                   session=types.SimpleNamespace(ownership_error=None, device=s.device,
                                                 storage=s.storage))
    MainWindow._show_last_sync(win)
    assert win.last_sync_row.get_subtitle() == "Never synced"
    sync.state_of(UNIT, test_drive=True).save()
    service.save_last_sync(UNIT, service.SyncSummary(
        uploaded=2, lines=[service.test_drive_step()]), test_drive=True)
    MainWindow._show_last_sync(win)
    assert "2 uploaded" in win.last_sync_row.get_subtitle()


def test_baseline_of_a_test_drive_marks_its_own_record_not_the_watchs(device_root, monkeypatch,
                                                                      capsys):
    """Its first sync asks how to begin; baseline answers it in the test drive's record, so the
    next sync goes on, and the watch's records and tile stay as they were."""
    watch_synced()
    storage = a_test_drive(device_root).storage
    monkeypatch.setattr(service, "open_device_or_drive", lambda **_kw: storage)
    account = DriveAccount({"connectsupport": "Required"})
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    def watch_sync():
        """The watch's records of its sync and what its tile knows of it (opening a device
        from the command line remembers it, which is no sync)."""
        entry = known_devices.load()[UNIT]
        synced = entry.synced  # its facts, not when they were looked at again
        return ({r for r in watch_records() if r[0] != known_devices.path().name},
                synced and (synced.once, synced.last, synced.not_uploaded, synced.state),
                entry.sync_count)

    before = watch_sync()
    assert cli.main(["sync", "--apply"]) == cli.FIRST_SYNC_CHOICE
    assert cli.main(["baseline", "--apply"]) == 0
    assert (own_folder() / "sync" / "state.json").exists()
    assert watch_sync() == before
    assert cli.main(["sync", "--apply"]) == 0
