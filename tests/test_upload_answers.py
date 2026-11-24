"""What each answer of Garmin Connect to an upload means for the files of a sync."""
import logging

import pytest
from conftest import make_fit
from fakes import ACTIVITY_URL, SyncClient
from fakes import sync_setup as _setup
from test_sync import UPLOAD_CONFIG

from openexpress import sync
from openexpress.connect import ClockSkew, UploadResult

MON = "https://up.example/monitor"


class PollFails(SyncClient):
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


@pytest.mark.parametrize("status,refused", [(403, True), (404, True), (500, False),
                                            (503, False), (429, False), (408, False)])
def test_consent_location_status_handling(device_root, tmp_path, status, refused):
    """A location that needs a consent refusing the file (403, 404) is no error, and the
    file is not marked either: the next sync tries again from the device, as Garmin Express
    does; no copy is kept. Other answers are failures."""
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, _consent_cfg(), state)
    rep = sync.run_uploads(plan, dev, st, SyncClient(statuses={MON: status}), state)
    assert not state.is_completed("FIT_TYPE_32", "M1.FIT")
    assert bool(rep.failed) is not refused
    assert rep.not_taken == (["FIT_TYPE_32/M1.FIT"] if refused else [])
    assert bool(state.pending_files("FIT_TYPE_32")) is not refused


class RaisingClient(SyncClient):
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


class Conflict(SyncClient):
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


class Rejecting(SyncClient):
    def __init__(self, status, body):
        super().__init__()
        self.status, self.body = status, body

    def upload_file(self, url, data, **kw):
        super().upload_file(url, data, **kw)
        return UploadResult(self.status, self.body, None, None)


@pytest.mark.parametrize("status", [415, 200])
def test_a_rejected_upload_is_logged_with_the_answer(device_root, tmp_path, caplog, status):
    """A file the server rejects (an HTTP code, or failure codes in an answer that took it):
    its type, the HTTP code and what the server said, in one short line without what
    identifies the user, the account or a device, nor the file."""
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_32"})
    body = {"detailedimportresult": {"failures": [{"messages": [
                {"code": 7, "content": "Unsupported file content"}]}]},
            "owner": "jan.kowalski@example.org", "unitid": 3999888777,
            "accesstoken": "abc123SECRETxyz", "notes": "x" * 2000}
    with caplog.at_level(logging.INFO, logger="openexpress.sync"):
        rep = sync.run_uploads(plan, dev, st, Rejecting(status, body), state)
    if status == 415:
        assert rep.rejected == ["FIT_TYPE_32/M1.FIT"]
    else:
        assert [label for label, _error in rep.failed] == ["FIT_TYPE_32/M1.FIT"]
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("upload rejected") and f"HTTP {status}" in r.getMessage()]
    assert len(lines) == 1
    line = lines[0]
    assert "FIT_TYPE_32" in line and "Unsupported file content" in line
    for private in ("jan.kowalski", "3999888777", "abc123SECRETxyz", "M1.FIT"):
        assert private not in line
    assert len(line) < 650  # the answer cut to 500 characters


class AnswersInTurn(SyncClient):
    """Answers the uploads to an address with the given statuses in turn, then 201."""

    def __init__(self, turns, **kw):
        super().__init__(**kw)
        self.turns = {url: list(statuses) for url, statuses in turns.items()}

    def upload_file(self, url, data, **kw):
        super().upload_file(url, data, **kw)
        left = self.turns.get(url)
        return UploadResult(left.pop(0) if left else 201, {}, None, None)


FIRST, SECOND = "2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit"


def test_a_412_leaves_out_that_file_alone(device_root, tmp_path):
    """412: Garmin Connect has no consent for this one file. It is left out as a refusing
    location's file is (no error, not marked: the next sync tries it again) and the upload goes
    on with the next file."""
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    client = AnswersInTurn({ACTIVITY_URL: [412]})
    rep = sync.run_uploads(plan, dev, st, client, state)
    assert [u[0] for u in client.uploads] == [ACTIVITY_URL, ACTIVITY_URL]
    assert (len(rep.not_taken), len(rep.uploaded), rep.failed) == (1, 1, [])
    done = [state.is_completed("FIT_TYPE_4", name) for name in (FIRST, SECOND)]
    assert sorted(done) == [False, True]


def test_a_412_is_not_undone_by_a_location_that_needs_a_consent(device_root, tmp_path):
    """412 at the main location: Garmin Connect has not the file, whatever an optional
    location that needs a consent answers; it is left out, not marked, tried again later."""
    config = {"datatypes": [{"datatypename": "FIT_TYPE_4", "uploadlocations": [
        {"url": ACTIVITY_URL, "requiresauthentication": True, "requiresuserconsent": False},
        {"url": MON, "requiresauthentication": True, "requiresuserconsent": True}]}]}
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, config, state, only_types={"FIT_TYPE_4"})
    rep = sync.run_uploads(plan, dev, st, SyncClient(statuses={ACTIVITY_URL: 412, MON: 201}), state)
    assert (len(rep.not_taken), rep.uploaded, rep.failed) == (2, [], [])
    assert not any(state.is_completed("FIT_TYPE_4", name) for name in (FIRST, SECOND))


@pytest.mark.parametrize("datatype, main, optional", [("FIT_TYPE_4", ACTIVITY_URL, MON),
                                                      ("FIT_TYPE_32", MON, ACTIVITY_URL)])
def test_a_412_at_a_location_that_needs_a_consent_is_that_location_s_refusal(
        device_root, tmp_path, datatype, main, optional):
    """The main location took the file and an optional one that needs a consent (HRV, skin
    temperature) answers 412: the file is uploaded and marked like any refused optional
    location's, and the files after it (monitoring and sleep data come in order) go on."""
    config = {"datatypes": [{"datatypename": datatype, "uploadlocations": [
        {"url": main, "requiresauthentication": True, "requiresuserconsent": False},
        {"url": optional, "requiresauthentication": True, "requiresuserconsent": True}]}]}
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, config, state, only_types={datatype})
    rep = sync.run_uploads(plan, dev, st, SyncClient(statuses={main: 201, optional: 412}), state)
    assert plan.candidates and (rep.not_taken, rep.failed) == ([], [])
    assert len(rep.uploaded) == len(plan.candidates)


def test_a_429_holds_back_the_rest_of_that_kind_only(device_root, tmp_path):
    """429: too many uploads. The file fails (the next sync tries it again) and the other files
    of its kind wait for the next sync; the other kinds go on."""
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state,
                             only_types={"FIT_TYPE_4", "FIT_TYPE_32"})
    client = AnswersInTurn({ACTIVITY_URL: [429]})
    rep = sync.run_uploads(plan, dev, st, client, state)
    sent = [u[0] for u in client.uploads]
    assert sent.count(ACTIVITY_URL) == 1 and sent.count(MON) == 1
    assert len(rep.failed) == 1 and len(rep.uploaded) == 1
    assert not any(state.is_completed("FIT_TYPE_4", name) for name in (FIRST, SECOND))


def test_a_409_is_a_file_connect_already_has(device_root, tmp_path):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    rep = sync.run_uploads(plan, dev, st, SyncClient(statuses={ACTIVITY_URL: 409}), state)
    assert (len(rep.duplicates), rep.failed) == (2, [])
    assert all(state.is_completed("FIT_TYPE_4", name) for name in (FIRST, SECOND))
