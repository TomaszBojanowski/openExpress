from openexpress import sync
from openexpress.connect import UploadResult
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

UPLOAD_CONFIG = {
    "unittypename": "test",
    "datatypes": [
        {"datatypename": "FIT_TYPE_4", "shoulddelete": False, "uploadlocations": [
            {"url": "https://up.example/activity", "requiresauthentication": True,
             "requiresuserconsent": False}]},
        {"datatypename": "FIT_TYPE_32", "uploadlocations": [
            {"url": "https://up.example/monitor", "requiresauthentication": True}]},
        {"datatypename": "FIT_TYPE_2", "uploadlocations": [
            {"url": "https://up.example/settings", "requiresauthentication": True}]},
        {"datatypename": "FIT_TYPE_44", "uploadlocations": [
            {"url": "https://analytics.example/", "requiresuserconsent": True,
             "requiredconsenttypes": [{"name": "Analytics"}]}]},
    ],
}


class FakeClient:
    def __init__(self, statuses=None, matches=None, messages=None):
        self.statuses = statuses or {}
        self.matches = matches or {}
        self.messages = messages or []
        self.uploads = []
        self.acks = []

    def require_verified(self, unit_id=None):
        pass

    def activity_matches(self, unit_id, ids):
        return {i: i in self.matches for i in ids}

    def upload_file(self, url, data, *, is_zip, last_file, authenticated):
        self.uploads.append((url, len(data), last_file))
        return UploadResult(self.statuses.get(url, 201), {}, None, None)

    def poll_location(self, url, delay):
        return 201

    def download_message(self, url, absolute):
        return b"COURSE" if "course" in url else None

    def ack_message(self, mid):
        self.acks.append(mid)


def _setup(device_root, tmp_path):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    state = sync.SyncState(dev.unit_id, base=tmp_path / "state")
    return st, dev, state


def test_plan_skips_consent_locations_and_non_matching(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state)
    names = [(c.datatype, c.name) for c in plan.candidates]
    assert ("FIT_TYPE_4", "notes.txt") not in names
    assert [n for n in names if n[0] == "FIT_TYPE_4"] == [
        ("FIT_TYPE_4", "2026-10-01-10-00-00.fit"), ("FIT_TYPE_4", "2026-10-02-10-00-00.fit")]
    assert names[-1] == ("FIT_TYPE_32", "M1.FIT")  # monitoring last
    # A type requiring a specific consent but absent from the device is not reported.
    assert "FIT_TYPE_44" not in plan.skipped_types
    assert all(c.datatype != "FIT_TYPE_44" for c in plan.candidates)


def test_consent_flag_alone_does_not_skip_and_diagnostics_are_skipped(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    (device_root / "GARMIN/Debug").mkdir()
    (device_root / "GARMIN/Debug/err.txt").write_text("log")
    xml = (device_root / "GARMIN/GarminDevice.xml").read_text().replace(
        "</MassStorageMode>",
        "<DataType><Name>ErrorShutdownReports</Name><File><Location><Path>Garmin/Debug</Path>"
        "<FileExtension>txt</FileExtension></Location><TransferDirection>OutputFromUnit"
        "</TransferDirection></File></DataType></MassStorageMode>",
    )
    (device_root / "GARMIN/GarminDevice.xml").write_text(xml)
    dev = parse(xml.encode())
    cfg = {"datatypes": [
        {"datatypename": "FIT_TYPE_4", "uploadlocations": [
            {"url": "https://up.example/activity", "requiresuserconsent": True,
             "requiredconsenttypes": []}]},
        {"datatypename": "ErrorShutdownReports", "uploadlocations": [
            {"url": "https://api.gcs.garmin.com/era2-intake/uploadShutdown/connectLogin"}]},
    ]}
    plan = sync.plan_uploads(dev, st, cfg, state)
    assert {c.datatype for c in plan.candidates} == {"FIT_TYPE_4"}
    assert "ErrorShutdownReports" in plan.skipped_types
    plan = sync.plan_uploads(dev, st, cfg, state, include_diagnostics=True)
    assert "ErrorShutdownReports" in {c.datatype for c in plan.candidates}


def test_upload_marks_state_and_skips_known(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state)
    client = FakeClient(matches={"1100000000"},
                        statuses={"https://up.example/monitor": 409})
    rep = sync.run_uploads(plan, dev, st, client, state)
    assert rep.already_in_connect == ["2026-10-01-10-00-00.fit"]
    assert "FIT_TYPE_4/2026-10-02-10-00-00.fit" in rep.uploaded
    assert rep.duplicates == ["FIT_TYPE_32/M1.FIT"]
    assert client.uploads[-1][2] is True  # lastFile for the last monitoring file
    again = sync.plan_uploads(dev, st, UPLOAD_CONFIG, sync.SyncState(dev.unit_id, tmp_path / "state"))
    assert again.candidates == []
    # The settings file changed: it will be uploaded again.
    (device_root / "GARMIN/Settings/Settings.fit").write_bytes(b"changed")
    again = sync.plan_uploads(dev, st, UPLOAD_CONFIG, sync.SyncState(dev.unit_id, tmp_path / "state"))
    assert [c.name for c in again.candidates] == ["Settings.fit"]


def test_failed_upload_kept_as_pending(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    client = FakeClient(statuses={"https://up.example/activity": 500})
    rep = sync.run_uploads(plan, dev, st, client, state)
    assert len(rep.failed) == 2
    (device_root / "GARMIN/Activity/2026-10-01-10-00-00.fit").unlink()
    again = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    pending = [c for c in again.candidates if c.device_path is None]
    assert [c.name for c in pending] == ["2026-10-01-10-00-00.fit"]
    rep = sync.run_uploads(again, dev, st, FakeClient(), state)
    assert len(rep.uploaded) == 2
    assert not state.pending_files("FIT_TYPE_4")


def _failed_once(device_root, tmp_path):
    """A sync whose uploads of activities failed: a copy of each waits in sync/pending."""
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    sync.run_uploads(plan, dev, st, FakeClient(statuses={"https://up.example/activity": 500}),
                     state)
    assert [p.name for p in state.pending_files("FIT_TYPE_4")] == [
        "2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit"]
    return st, dev, state


def test_the_copy_of_a_failed_upload_is_private(device_root, tmp_path):
    """An activity is private: its copy is readable by its owner only (as the log is)."""
    _st, _dev, state = _failed_once(device_root, tmp_path)
    for p in state.pending_files("FIT_TYPE_4"):
        assert p.stat().st_mode & 0o777 == 0o600, p.name
    assert (device_root / "GARMIN/Activity/2026-10-01-10-00-00.fit").read_bytes() == \
        state.pending_path("FIT_TYPE_4", "2026-10-01-10-00-00.fit").read_bytes()


def test_the_copy_goes_when_connect_already_has_the_activity(device_root, tmp_path):
    """Found in Connect before the upload (by the time of the activity): nothing is left to
    upload, so the copy is not kept (it was kept for ever: the plan skips what is done)."""
    st, dev, state = _failed_once(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    rep = sync.run_uploads(plan, dev, st, FakeClient(matches={"1100000000"}), state)
    assert rep.already_in_connect == ["2026-10-01-10-00-00.fit"]
    assert not state.pending_files("FIT_TYPE_4")  # the other one was uploaded


def test_the_copy_goes_when_only_its_copy_is_left_and_connect_has_it(device_root, tmp_path):
    st, dev, state = _failed_once(device_root, tmp_path)
    (device_root / "GARMIN/Activity/2026-10-01-10-00-00.fit").unlink()  # the watch deleted it
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    assert [c.name for c in plan.candidates if c.device_path is None] == [
        "2026-10-01-10-00-00.fit"]
    rep = sync.run_uploads(plan, dev, st, FakeClient(matches={"1100000000"}), state)
    assert rep.already_in_connect == ["2026-10-01-10-00-00.fit"]
    assert not state.pending_files("FIT_TYPE_4")


def test_the_copy_goes_when_the_files_on_the_device_are_marked_as_uploaded(device_root,
                                                                           tmp_path):
    """baseline: the files on the device count as uploaded, so their copies are not kept; a
    copy whose file is no longer on the device waits for its upload, as before."""
    st, dev, state = _failed_once(device_root, tmp_path)
    (device_root / "GARMIN/Activity/2026-10-02-10-00-00.fit").unlink()
    sync.mark_baseline(dev, st, UPLOAD_CONFIG, state, FakeClient())
    assert [p.name for p in state.pending_files("FIT_TYPE_4")] == ["2026-10-02-10-00-00.fit"]


def test_baseline(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    assert sync.mark_baseline(dev, st, UPLOAD_CONFIG, state, FakeClient()) == 4
    assert sync.plan_uploads(dev, st, UPLOAD_CONFIG, state).candidates == []


def test_downloads(device_root, tmp_path):
    st, dev, _ = _setup(device_root, tmp_path)
    messages = [
        {"messageid": 1, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": "course/1", "messagename": "Route"}},
        {"messageid": 2, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_5",
         "metadata": {"messageurl": "workout/2"}},
        {"messageid": 3, "messagetype": "file", "devicexmldatatype": "IQWatchApps",
         "metadata": {"messageurl": "app/3"}},
        {"messageid": 4, "messagetype": "software-update", "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": "x"}},
        {"messageid": 5, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": "empty/5"}},
    ]
    items = sync.plan_downloads(dev, messages)
    assert [i.message_id for i in items] == [1, 2, 3, 5]
    assert items[1].unsupported  # FIT_TYPE_5 has no target on this device
    assert items[2].handled_by == "Connect IQ" and not items[2].unsupported
    client = FakeClient()
    rep = sync.run_downloads(items, st, client)
    assert len(rep.written) == 1 and len(rep.empty) == 1
    assert client.acks == [1, 5]
    written = list((device_root / "GARMIN/NewFiles").iterdir())
    assert len(written) == 1 and written[0].name.startswith("course_")
    assert written[0].read_bytes() == b"COURSE"


def test_a_file_written_but_interrupted_before_its_acknowledgement_is_not_written_again(
        device_root, tmp_path):
    """Ctrl+C while Garmin Connect is told a course was written: the course is remembered as
    written and not acknowledged, so the next sync acknowledges it without a second file on
    the device (as when the acknowledgement fails)."""
    import pytest

    st, dev, state = _setup(device_root, tmp_path)
    items = sync.plan_downloads(dev, [
        {"messageid": 7, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": "course/7", "messagename": "Route"}}])

    class Interrupted(FakeClient):
        def ack_message(self, mid):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        sync.run_downloads(items, st, Interrupted(), state=state)
    assert len(list((device_root / "GARMIN/NewFiles").iterdir())) == 1
    again = sync.SyncState(dev.unit_id, base=tmp_path / "state")
    assert again.unacked() == {"7"}
    client = FakeClient()
    rep = sync.run_downloads(items, st, client, state=again)
    assert client.acks == [7] and rep.written == []
    assert len(list((device_root / "GARMIN/NewFiles").iterdir())) == 1  # no second file
    assert sync.SyncState(dev.unit_id, base=tmp_path / "state").unacked() == set()


def test_the_state_of_the_sync_is_readable_by_its_owner_only(tmp_path, xdg, monkeypatch):
    """What was uploaded (state.json), what waits for its acknowledgement (unacked.json) and
    the last sync (last_sync.json, with the unit ID): 0600, as the log and the login, also
    where the folder was made 0755 by an older version."""
    import os
    import stat

    from openexpress import service

    old = os.umask(0o022)
    try:
        state = sync.SyncState(3000000001, base=tmp_path / "state")
        state.mark_completed("FIT_TYPE_4", "a.fit")
        state.save()
        state.save_unacked({"7"})
        service.save_last_sync(3000000001, service.SyncSummary(uploaded=1))
    finally:
        os.umask(old)
    files = [state.file, state.unacked_file, service.device_last_sync_path(3000000001),
             service.last_sync_path()]
    for f in files:
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name
    # Written as before: read back the same.
    again = sync.SyncState(3000000001, base=tmp_path / "state")
    assert again.is_completed("FIT_TYPE_4", "a.fit") and again.unacked() == {"7"}
    assert service.load_last_sync(3000000001)["uploaded"] == 1


def test_trueup_flow(device_root, tmp_path):
    import struct

    from openexpress import trueup
    from openexpress.fit import FIT_EPOCH_OFFSET

    xml = (device_root / "GARMIN/GarminDevice.xml").read_text().replace(
        "</MassStorageMode>",
        "<DataType><Name>FIT_TYPE_39</Name><File><Location><Path>Garmin/NewFiles</Path>"
        "<FileExtension>FIT</FileExtension></Location><TransferDirection>InputToUnit"
        "</TransferDirection></File></DataType></MassStorageMode>")
    dev = parse(xml.encode())
    st = LocalStorage(device_root)

    def callback_fit(unix_time):
        definition = bytes([0x40, 0, 0]) + struct.pack("<H", 197) + bytes([1, 0, 4, 0x86])
        body = definition + bytes([0]) + struct.pack("<I", unix_time - FIT_EPOCH_OFFSET)
        return struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0) + body + b"\0\0"

    now = 2_000_000_000
    check = {"messageid": 1, "messagetype": "check_back", "devicexmldatatype": "FIT_TYPE_39",
             "metadata": {"messageurl": "cb"}}
    true = {"messageid": 2, "messagetype": "true_up", "devicexmldatatype": "FIT_TYPE_39",
            "metadata": {"messageurl": "tu"}}

    class Client:
        acks = []
        calls = 0

        def download_message(self, url, absolute):
            return callback_fit(now + 20) if url == "cb" else b"TRUEUP"

        def ack_message(self, mid):
            self.acks.append(mid)

        def device_messages(self, unit):
            Client.calls += 1
            return [true]

    waits = []
    c = Client()
    res = trueup.run(dev, st, c, [check], sleep=waits.append, now=lambda: now)
    assert waits == [20] and c.acks == [1, 2]
    assert res.written.startswith("GARMIN/NewFiles/")
    assert (device_root / res.written).read_bytes() == b"TRUEUP"
