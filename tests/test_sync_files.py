"""Files of a sync: messages acknowledged, the user profile, read errors, reading only when
needed."""

from fakes import ACTIVITY_URL, SyncClient
from fakes import sync_setup as _setup
from test_sync import UPLOAD_CONFIG

from openexpress import sync
from openexpress.device.storage import DeviceError
from openexpress.device.xmldevice import DeviceFile


class AckFails(SyncClient):
    fail = True

    def ack_message(self, mid):
        if self.fail:
            raise ConnectionError("ack failed")
        super().ack_message(mid)


def _course_items(dev):
    return sync.plan_downloads(dev, [
        {"messageid": 7, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": "course/7", "messagename": "Route"}}])


def test_ack_failure_is_retried_without_rewrite(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    client = AckFails()
    rep = sync.run_downloads(_course_items(dev), st, client, state=state)
    assert len(rep.written) == 1 and rep.ack_failed and not rep.failed
    assert sync.SyncState(dev.unit_id, base=tmp_path / "state").unacked() == {"7"}
    client.fail = False
    state = sync.SyncState(dev.unit_id, base=tmp_path / "state")
    rep = sync.run_downloads(_course_items(dev), st, client, state=state)
    assert client.acks == [7] and not rep.written and not rep.ack_failed
    assert len(list((device_root / "GARMIN/NewFiles").iterdir())) == 1
    assert state.unacked() == set()
    assert not state.unacked_file.exists()


def test_user_profile_name():
    def item(dt, base):
        return sync.DownloadItem(1, dt, "", "", False,
                                 DeviceFile(dt, "InputToUnit", "Garmin/Profile", base, "FIT"))
    assert item("FitnessUserProfile", "").device_name() == "UserProfile.FIT"
    assert item("UserProfile", "user").device_name() == "user.FIT"
    assert item("FIT_TYPE_7", "x").device_name().startswith("x_")


def test_read_error_fails_only_that_file_and_data_is_freed(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state)
    reads = []
    real_read = st.read

    def read(path):
        reads.append(path)
        if path.endswith(("2026-10-01-10-00-00.fit", "M1.FIT")):
            raise DeviceError("I/O error")
        return real_read(path)

    st.read = read
    rep = sync.run_uploads(plan, dev, st, SyncClient(), state)
    assert sorted(label for label, _ in rep.failed) == [
        "FIT_TYPE_32/M1.FIT", "FIT_TYPE_4/2026-10-01-10-00-00.fit"]
    assert "FIT_TYPE_4/2026-10-02-10-00-00.fit" in rep.uploaded
    assert all(c.data is None for c in plan.candidates)
    # Activities are read once for the duplicate check and again for upload.
    assert sum(r.endswith("2026-10-02-10-00-00.fit") for r in reads) == 2


def test_pending_files_are_read_lazily(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    sync.run_uploads(plan, dev, st, SyncClient(statuses={ACTIVITY_URL: 500}), state)
    (device_root / "GARMIN/Activity/2026-10-01-10-00-00.fit").unlink()
    again = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    pending = [c for c in again.candidates if c.device_path is None]
    assert pending and pending[0].data is None
    rep = sync.run_uploads(again, dev, st, SyncClient(), state)
    assert len(rep.uploaded) == 2
