"""Files the device is still writing are neither uploaded nor marked, and only FIT files are
checked."""

import logging

from conftest import make_fit
from fakes import SyncClient
from fakes import sync_setup as _setup
from test_sync import UPLOAD_CONFIG

from openexpress import sync


def _cut(path, count):
    whole = path.read_bytes()
    path.write_bytes(whole[:-count])
    return whole


def test_a_file_still_being_written_is_neither_uploaded_nor_marked(device_root, tmp_path,
                                                                     caplog):
    """A file the device was still writing when it was read (cut short at the size the
    device gave when it was connected): not uploaded, not counted as done, and read again
    by the next sync, which uploads it whole."""
    st, dev, state = _setup(device_root, tmp_path)
    settings = device_root / "GARMIN/Settings/Settings.fit"
    whole = _cut(settings, 5)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_2"})
    client = SyncClient()
    with caplog.at_level(logging.INFO, logger="openexpress.sync"):
        rep = sync.run_uploads(plan, dev, st, client, state)
    assert client.uploads == [] and rep.incomplete == ["FIT_TYPE_2/Settings.fit"]
    assert not rep.failed and not rep.rejected and not rep.uploaded
    lines = [r.getMessage() for r in caplog.records if "still being written" in r.getMessage()]
    assert len(lines) == 1 and "FIT_TYPE_2" in lines[0] and "Settings" not in lines[0]
    assert f"{len(whole) - 5} B" in lines[0] and f"{len(whole)} B" in lines[0]
    settings.write_bytes(whole)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_2"})
    rep = sync.run_uploads(plan, dev, st, SyncClient(), state)
    assert rep.uploaded == ["FIT_TYPE_2/Settings.fit"] and not rep.incomplete


def test_monitoring_waits_behind_a_file_still_being_written(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    _cut(device_root / "GARMIN/Monitor/M1.FIT", 3)
    (device_root / "GARMIN/Monitor/M2.FIT").write_bytes(make_fit(1100000600, 32))
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_32"})
    client = SyncClient()
    rep = sync.run_uploads(plan, dev, st, client, state)
    assert client.uploads == [] and rep.incomplete == ["FIT_TYPE_32/M1.FIT"]
    assert not state.is_completed("FIT_TYPE_32", "M1.FIT")
    assert not state.is_completed("FIT_TYPE_32", "M2.FIT")
    assert not state.pending_files("FIT_TYPE_32")


def test_an_activity_still_being_written_is_neither_matched_nor_uploaded(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    _cut(device_root / "GARMIN/Activity/2026-10-02-10-00-00.fit", 4)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    client = SyncClient()
    rep = sync.run_uploads(plan, dev, st, client, state)
    assert rep.incomplete == ["FIT_TYPE_4/2026-10-02-10-00-00.fit"]
    assert rep.uploaded == ["FIT_TYPE_4/2026-10-01-10-00-00.fit"] and len(client.uploads) == 1
    assert not state.is_completed("FIT_TYPE_4", "2026-10-02-10-00-00.fit")


def test_only_fit_files_are_checked():
    from openexpress.sync import UploadCandidate, still_being_written

    cut = make_fit(1100000000)[:-4]
    assert still_being_written(UploadCandidate("FIT_TYPE_2", "a.fit", 1, "a", [], False), cut)
    assert still_being_written(UploadCandidate("FIT_TYPE_2", "a.FIT", 1, "a", [], False), cut)
    assert still_being_written(UploadCandidate("FIT_TYPE_2", "a.zip", 1, "a", [], True), cut) is None
    assert still_being_written(UploadCandidate("X", "backup.db", 1, "a", [], False), cut) is None
