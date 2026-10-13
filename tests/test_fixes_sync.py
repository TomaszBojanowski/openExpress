import struct

import pytest
from conftest import make_fit
from test_sync import UPLOAD_CONFIG, FakeClient

from openexpress import sync
from openexpress.connect import ApiError, ClockSkew, UploadResult
from openexpress.device.storage import DeviceError, LocalStorage
from openexpress.device.xmldevice import DeviceFile, parse
from openexpress.fit import FitError, iter_messages, parse_file_id

ACT = "https://up.example/activity"
MON = "https://up.example/monitor"


def _setup(device_root, tmp_path):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    state = sync.SyncState(dev.unit_id, base=tmp_path / "state")
    return st, dev, state


def _truncated_definition() -> bytes:
    # Definition declares 5 fields but the file ends after one.
    body = bytes([0x40, 0, 0]) + struct.pack("<H", 0) + bytes([5, 0, 1, 0x00])
    return struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0) + body


def _truncated_dev_fields() -> bytes:
    body = bytes([0x60, 0, 0]) + struct.pack("<H", 0) + bytes([1, 0, 1, 0x00, 4, 1, 2])
    return struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0) + body


@pytest.mark.parametrize("data", [_truncated_definition(), _truncated_dev_fields()])
def test_truncated_definition_raises_fit_error(data):
    with pytest.raises(FitError):
        list(iter_messages(data))
    with pytest.raises(FitError):
        parse_file_id(data)


def test_damaged_files_only_raise_fit_error():
    import random

    good = make_fit(1100000000)
    rnd = random.Random(1)
    samples = [good[:n] for n in range(len(good))]
    for _ in range(2000):
        b = bytearray(good)
        for _i in range(3):
            b[rnd.randrange(14, len(b))] = rnd.randrange(256)
        samples.append(bytes(b))
    for data in samples:
        try:
            list(iter_messages(data))
        except FitError:
            pass
    assert parse_file_id(good).time_created == 1100000000


def test_damaged_activity_does_not_abort_sync(device_root, tmp_path):
    (device_root / "GARMIN/Activity/2026-10-03-10-00-00.fit").write_bytes(_truncated_definition())
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    rep = sync.run_uploads(plan, dev, st, FakeClient(), state)
    assert len(rep.uploaded) == 3 and not rep.failed


class PollFails(FakeClient):
    def upload_file(self, url, data, **kw):
        super().upload_file(url, data, **kw)
        return UploadResult(201, {}, "https://x/activity-service/1", 10)

    def poll_location(self, url, delay):
        raise ConnectionError("network down")


def test_poll_error_marks_not_confirmed(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    rep = sync.run_uploads(plan, dev, st, PollFails(), state)
    assert len(rep.uploaded) == 2 and len(rep.not_confirmed) == 2 and not rep.failed
    assert state.is_completed("FIT_TYPE_4", "2026-10-01-10-00-00.fit")


def _consent_cfg():
    return {"datatypes": [{"datatypename": "FIT_TYPE_32", "uploadlocations": [
        {"url": MON, "requiresauthentication": True, "requiresuserconsent": True}]}]}


@pytest.mark.parametrize("status,done", [(403, True), (404, True), (500, False),
                                         (503, False), (429, False), (408, False)])
def test_consent_location_status_handling(device_root, tmp_path, status, done):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, _consent_cfg(), state)
    rep = sync.run_uploads(plan, dev, st, FakeClient(statuses={MON: status}), state)
    assert state.is_completed("FIT_TYPE_32", "M1.FIT") is done
    assert bool(rep.failed) is not done
    assert bool(state.pending_files("FIT_TYPE_32")) is not done


class RaisingClient(FakeClient):
    def __init__(self, exc_matches=None, exc_upload=None, **kw):
        super().__init__(**kw)
        self.exc_matches, self.exc_upload = exc_matches, exc_upload

    def activity_matches(self, unit_id, ids):
        if self.exc_matches:
            raise self.exc_matches
        return super().activity_matches(unit_id, ids)

    def upload_file(self, url, data, **kw):
        if self.exc_upload:
            raise self.exc_upload
        return super().upload_file(url, data, **kw)


@pytest.mark.parametrize("exc", [ValueError("bad json"), ConnectionError("x"),
                                 AttributeError("y")])
def test_duplicate_check_errors_upload_everything(device_root, tmp_path, exc):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    rep = sync.run_uploads(plan, dev, st, RaisingClient(exc_matches=exc), state)
    assert len(rep.uploaded) == 2


def test_clock_skew_is_not_swallowed(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    with pytest.raises(ClockSkew):
        sync.run_uploads(plan, dev, st, RaisingClient(exc_matches=ClockSkew("skew")), state)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    client = RaisingClient(exc_upload=ClockSkew("skew"))
    with pytest.raises(ClockSkew):
        sync.run_uploads(plan, dev, st, client, state)


class Conflict(FakeClient):
    def __init__(self, body_code=False):
        super().__init__()
        self.body_code = body_code

    def upload_file(self, url, data, **kw):
        super().upload_file(url, data, **kw)
        if self.body_code:
            body = {"detailedimportresult": {"failures": [{"messages": [{"code": 419}]}]}}
            return UploadResult(200, body, None, None)
        return UploadResult(419, {}, None, None)


@pytest.mark.parametrize("body_code", [False, True])
def test_tracker_conflict(device_root, tmp_path, body_code):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_32"})
    (device_root / "GARMIN/Monitor/M2.FIT").write_bytes(make_fit(1100000600, 32))
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_32"})
    client = Conflict(body_code)
    rep = sync.run_uploads(plan, dev, st, client, state)
    assert rep.tracker_conflict == ["FIT_TYPE_32/M1.FIT", "FIT_TYPE_32/M2.FIT"]
    assert len(client.uploads) == 2 and not rep.failed
    assert state.is_completed("FIT_TYPE_32", "M2.FIT")


class AckFails(FakeClient):
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
        if path.endswith("2026-10-01-10-00-00.fit") or path.endswith("M1.FIT"):
            raise DeviceError("I/O error")
        return real_read(path)

    st.read = read
    rep = sync.run_uploads(plan, dev, st, FakeClient(), state)
    assert sorted(label for label, _ in rep.failed) == [
        "FIT_TYPE_32/M1.FIT", "FIT_TYPE_4/2026-10-01-10-00-00.fit"]
    assert "FIT_TYPE_4/2026-10-02-10-00-00.fit" in rep.uploaded
    assert all(c.data is None for c in plan.candidates)
    # Activities are read once for the duplicate check and again for upload.
    assert sum(r.endswith("2026-10-02-10-00-00.fit") for r in reads) == 2


def test_pending_files_are_read_lazily(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    sync.run_uploads(plan, dev, st, FakeClient(statuses={ACT: 500}), state)
    (device_root / "GARMIN/Activity/2026-10-01-10-00-00.fit").unlink()
    again = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    pending = [c for c in again.candidates if c.device_path is None]
    assert pending and pending[0].data is None
    rep = sync.run_uploads(again, dev, st, FakeClient(), state)
    assert len(rep.uploaded) == 2


def test_record_sync(xdg):
    from openexpress import service

    service.record_sync(42, 1, 2, 3, ["e"], ["line"])
    data = service.load_last_sync(42)
    assert data["uploaded"] == 1 and data["downloaded"] == 2 and data["lines"] == ["line"]


# --- background service / watcher ---------------------------------------------------------


class SyncThread:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_set_enabled_reports_errors(monkeypatch):
    from openexpress import background

    monkeypatch.setattr(background, "in_flatpak", lambda: False)
    monkeypatch.setattr(background.threading, "Thread", SyncThread)
    monkeypatch.setattr(background.GLib, "idle_add", lambda f, *a: f(*a))

    def boom(**kw):
        raise OSError("read-only")

    monkeypatch.setattr(background.desktop, "install", boom)
    results = []
    background.set_enabled(True, lambda ok, err: results.append((ok, err)))
    assert results == [(False, "read-only")]

    def no_systemctl(*a, **kw):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(background.subprocess, "run", no_systemctl)
    background.set_enabled(False, lambda ok, err: results.append((ok, err)))
    assert results[-1][0] is False and "systemctl" in results[-1][1]
    assert background.is_enabled() is False


def test_watcher_pending_and_deferred_quit(monkeypatch):
    from openexpress import watch

    monkeypatch.setattr(watch.threading, "Thread", SyncThread)
    monkeypatch.setattr(watch.GLib, "idle_add", lambda f, *a: f(*a))
    w = watch.Watcher()
    released = []
    monkeypatch.setattr(w, "release", lambda: released.append(True))
    runs = []

    def fake_sync():
        runs.append(1)
        if len(runs) == 1:
            w.start_sync()  # re-plugged during the sync

    monkeypatch.setattr(w, "_sync", fake_sync)
    w.start_sync()
    assert len(runs) == 2 and not w.running

    runs.clear()

    def quit_during():
        runs.append(1)
        w.start_sync()
        w.request_quit()
        assert not released  # deferred while syncing

    monkeypatch.setattr(w, "_sync", quit_during)
    w.start_sync()
    assert runs == [1] and released == [True]


def test_watcher_retries_open_session(monkeypatch):
    from openexpress import watch

    calls = []

    def open_session():
        calls.append(1)
        if len(calls) < 3:
            raise DeviceError("not ready")
        return "session"

    monkeypatch.setattr(watch.service, "open_session", open_session)
    w = watch.Watcher()
    sleeps = []
    assert w._open_session(sleep=sleeps.append) == "session"
    assert sleeps == [5, 5]
    calls.clear()
    monkeypatch.setattr(watch.service, "open_session",
                        lambda: (_ for _ in ()).throw(DeviceError("gone")))
    with pytest.raises(DeviceError):
        w._open_session(sleep=sleeps.append)


def test_upload_412_still_raises(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    with pytest.raises(ApiError):
        sync.run_uploads(plan, dev, st, FakeClient(statuses={ACT: 412}), state)
