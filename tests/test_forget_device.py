"""Forgetting a device: everything this installation keeps of it goes, its entry in
devices.json last; files not uploaded yet only with consent; its backups only when asked.
All unit IDs here are made up."""

import json
import logging

import pytest

FENIX, EDGE = 3000000001, 3000000002


def remembered(unit_id=FENIX, description="fēnix 8", part="006-B4532-00", now=1.0):
    from openexpress import known_devices

    known_devices.remember(unit_id, description, part, "23.39", "mtp", now=now)


def kept_of(unit_id, pending=0):
    """What a device leaves in the data folder after syncs: its own folder (the sync state,
    its last sync, own maps, refused apps) and the shared last sync. `pending`: copies of
    files whose upload failed."""
    from openexpress import paths

    folder = paths.device_dir(unit_id)
    (folder / "sync").mkdir(parents=True)
    (folder / "sync" / "state.json").write_text('{"completed": {}}')
    (folder / "last_sync.json").write_text(json.dumps({"unit_id": unit_id, "time": 1.0}))
    (folder / "ownmaps.json").write_text("{}")
    (folder / "ciq.json").write_text("{}")
    for n in range(pending):
        path = folder / "sync" / "pending" / "FIT_TYPE_4" / f"ACT{n}.FIT"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fit")
    shared = paths.data_dir() / "last_sync.json"
    shared.write_text(json.dumps({"unit_id": unit_id, "time": 1.0}))
    return folder


def backups_of(unit_id, backups):
    """Two backups, a copy of the app list, a backup cut short, and a file of the user's."""
    folder = backups / str(unit_id)
    for name in ("2026-10-01 (10.00.00)", "2026-10-02 (10.00.00)",
                 "GARMIN-APPS 2026-10-03 (10.00.00) fēnix 8",
                 ".partial-2026-10-04 (10.00.00)"):
        (folder / name).mkdir(parents=True)
        (folder / name / "GarminDevice.xml").write_text("<Device/>")
    return folder


def test_forgetting_removes_what_this_installation_keeps_of_the_device(backups):
    """The entry, the device's own folder and the shared last sync, when it is of this
    device. Another device keeps everything; the cache, gui.json and the backups stay."""
    from openexpress import forget, known_devices, paths

    remembered(FENIX)
    remembered(EDGE, "Edge 1050", "006-B4440-00", now=2.0)
    folder = kept_of(FENIX)
    edge = paths.device_dir(EDGE) / "last_sync.json"
    edge.parent.mkdir(parents=True)
    edge.write_text("{}")
    cache = paths.cache_dir() / "images" / "picture.png"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"png")
    gui = paths.config_dir() / "gui.json"
    gui.parent.mkdir(parents=True)
    gui.write_text("{}")
    saved = backups_of(FENIX, backups)

    assert forget.forget_device(FENIX) is True
    assert set(known_devices.load()) == {EDGE}
    assert not folder.exists()
    assert not (paths.data_dir() / "last_sync.json").exists()
    assert edge.read_text() == "{}"
    assert cache.read_bytes() == b"png" and gui.read_text() == "{}"
    assert sorted(p.name for p in saved.iterdir()) == [
        ".partial-2026-10-04 (10.00.00)", "2026-10-01 (10.00.00)", "2026-10-02 (10.00.00)",
        "GARMIN-APPS 2026-10-03 (10.00.00) fēnix 8"]


def test_the_shared_last_sync_of_another_device_stays():
    """The shared last sync names the newest sync of any device: another device's stays."""
    from openexpress import forget, paths

    remembered(FENIX)
    kept_of(FENIX)
    shared = paths.data_dir() / "last_sync.json"
    shared.write_text(json.dumps({"unit_id": EDGE, "time": 2.0}))
    assert forget.forget_device(FENIX) is True
    assert json.loads(shared.read_text())["unit_id"] == EDGE


def test_a_device_with_nothing_kept_but_its_entry_is_forgotten():
    from openexpress import forget, known_devices

    remembered(FENIX)
    assert forget.forget_device(FENIX) is True
    assert known_devices.load() == {}


def test_a_device_not_remembered_is_not_forgotten():
    """Not in devices.json: nothing is deleted, not even a folder of the same unit ID."""
    from openexpress import forget

    remembered(EDGE, "Edge 1050", "006-B4440-00")
    folder = kept_of(FENIX)
    assert forget.forget_device(FENIX) is False
    assert (folder / "sync" / "state.json").exists()


def test_files_not_uploaded_yet_go_only_with_consent():
    """Copies of files whose upload failed are sent at the next sync: forgetting would lose
    them. Without consent nothing is deleted; with it they go with the rest."""
    from openexpress import forget, known_devices

    remembered(FENIX)
    folder = kept_of(FENIX, pending=2)
    assert forget.kept(FENIX).pending == 2
    with pytest.raises(forget.PendingFiles) as e:
        forget.forget_device(FENIX)
    assert e.value.count == 2
    assert str(e.value) == (
        "2 files of this device have not been uploaded yet and would be lost.")
    assert set(known_devices.load()) == {FENIX}
    assert len(list((folder / "sync" / "pending").rglob("*.FIT"))) == 2

    assert forget.forget_device(FENIX, discard_pending=True) is True
    assert known_devices.load() == {} and not folder.exists()


def test_the_backups_go_only_when_asked_and_only_ours(backups):
    """With `backups`: the backups, the copies of the app list and backups cut short go;
    anything else in that folder stays, and so does the folder then."""
    from openexpress import forget

    remembered(FENIX)
    kept_of(FENIX)
    saved = backups_of(FENIX, backups)
    (saved / "notes.txt").write_text("mine")
    assert forget.kept(FENIX).backups == 3
    assert forget.forget_device(FENIX, backups=True) is True
    assert [p.name for p in saved.iterdir()] == ["notes.txt"]


def test_with_only_backups_their_folder_goes_too(backups):
    from openexpress import forget

    remembered(FENIX)
    saved = backups_of(FENIX, backups)
    assert forget.forget_device(FENIX, backups=True) is True
    assert not saved.exists() and backups.exists()


def test_nothing_kept_counts_nothing():
    from openexpress import forget

    remembered(FENIX)
    assert (forget.kept(FENIX).pending, forget.kept(FENIX).backups) == (0, 0)


def test_a_sync_running_refuses_and_nothing_goes():
    """A sync (here, or in the background) holds the sync lock: forgetting waits for none,
    it refuses with the sync's own words and deletes nothing."""
    from openexpress import forget, known_devices, service

    remembered(FENIX)
    folder = kept_of(FENIX)
    with service.sync_lock(), pytest.raises(service.SyncBusy):
        forget.forget_device(FENIX)
    assert set(known_devices.load()) == {FENIX}
    assert (folder / "sync" / "state.json").exists()


def test_a_failed_deletion_keeps_the_entry(monkeypatch):
    """The entry goes last: when the folder cannot be deleted the device stays listed, to be
    forgotten again."""
    from openexpress import forget, known_devices

    remembered(FENIX)
    kept_of(FENIX)

    def refuse(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(forget.shutil, "rmtree", refuse)
    with pytest.raises(OSError):
        forget.forget_device(FENIX)
    assert set(known_devices.load()) == {FENIX}


def test_one_log_line_with_the_part_number_only(caplog, backups):
    from openexpress import forget

    remembered(FENIX)
    kept_of(FENIX, pending=1)
    backups_of(FENIX, backups)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        forget.forget_device(FENIX, discard_pending=True, backups=True)
    lines = [r.getMessage() for r in caplog.records if "forgot" in r.getMessage()]
    assert lines == [("devices: forgot 006-B4532-00 and what this installation kept of it "
                      "(not uploaded: 1, backups deleted: 3)")]
    assert str(FENIX) not in caplog.text
