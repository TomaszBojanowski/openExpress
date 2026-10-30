"""What the log never names: the courses and workouts of the Garmin Connect queue (names given
by the user, often places), the files of activities, and the signed addresses of downloads.
The log goes with `report --log` into public reports. What the window and the command line
tell the user may still name them."""

import logging

import pytest
from fakes import SyncClient
from test_fixes_net import DATA, md5hex, server  # noqa: F401 - the fixture
from test_sync import UPLOAD_CONFIG
from test_upload_consent import Account

from openexpress import applog, download, service, sync
from openexpress.connect import ApiError, UploadResult
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

COURSE = "Evening loop past the Kowalskis' house"
ACTIVITY = "2026-10-01-10-00-00.fit"  # a file of the device_root fixture
SIGNATURE = "1761000000_" + "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _setup(device_root, tmp_path):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    return st, dev, sync.SyncState(dev.unit_id, base=tmp_path / "state")


def _course(dev, mid=7):
    return sync.plan_downloads(dev, [
        {"messageid": mid, "messagetype": "file", "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": f"course/{mid}", "messagename": COURSE}}])


def _logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


class AckFails(SyncClient):
    def ack_message(self, mid):
        raise ConnectionError("ack failed")


class DownloadFails(SyncClient):
    def download_message(self, url, absolute):
        raise ApiError("HTTP 500")


def test_a_course_whose_acknowledgement_fails_is_logged_without_its_name(device_root, tmp_path,
                                                                        caplog):
    """The type and the number of the queue message instead; the user is told its name."""
    st, dev, state = _setup(device_root, tmp_path)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        rep = sync.run_downloads(_course(dev), st, AckFails(), state=state)
        state = sync.SyncState(dev.unit_id, base=tmp_path / "state")
        again = sync.run_downloads(_course(dev), st, AckFails(), state=state)  # tried again
    assert COURSE in rep.ack_failed[0] and COURSE in again.ack_failed[0]  # the user's words
    logged = _logged(caplog)
    assert COURSE not in logged and "Evening" not in logged
    assert "Acknowledgement of FIT_TYPE_6 (message 7) failed" in logged
    assert "Acknowledgement of FIT_TYPE_6 (message 7) failed again" in logged


def test_a_course_that_cannot_be_downloaded_is_logged_without_its_name(device_root, tmp_path,
                                                                       caplog):
    st, dev, state = _setup(device_root, tmp_path)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        rep = sync.run_downloads(_course(dev), st, DownloadFails(), state=state)
    assert rep.failed and COURSE in rep.failed[0][0]
    assert COURSE not in _logged(caplog)


class Troubled(Account):
    """The account of a sync where everything goes wrong once: an activity is refused, its
    processing cannot be confirmed, a course cannot be downloaded."""

    def upload_file(self, url, data, *, is_zip, last_file, authenticated):
        self.uploads.append((url, len(data), last_file))
        if "activity" in url and len(self.uploads) == 1:
            return UploadResult(500, {}, None, None)
        return UploadResult(201, {}, "https://up.example/activity-service/1", 0)

    def poll_location(self, url, delay):
        raise ConnectionError("no answer")

    def download_message(self, url, absolute):
        raise ApiError("HTTP 500")


def test_the_errors_of_a_sync_are_logged_without_names(device_root, xdg, caplog):
    """service.run_sync (the window and the background sync): every "sync error" line in the
    log names the type only; the errors kept for the user still name the files."""
    from openexpress.settings import Settings

    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    client = Troubled(messages=[{"messageid": 5, "devicexmldatatype": "FIT_TYPE_6",
                                 "metadata": {"messageurl": "course", "messagename": COURSE}}])
    s = service.DeviceSession(st, dev, Settings(), client)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        summary = service.run_sync(s, first_run="all")
    told = "\n".join(summary.errors)
    assert COURSE in told and ACTIVITY in told  # the window and the command line say which
    logged = _logged(caplog)
    assert "sync error: " in logged
    for name in (COURSE, ACTIVITY, "2026-10-01", "2026-10-02"):
        assert name not in logged, name
    assert "sync error: FIT_TYPE_6 (message 5)" in logged
    assert "sync error: FIT_TYPE_4" in logged
    assert service.load_last_sync(dev.unit_id)["errors"] == summary.errors  # kept as told


def test_the_background_sync_does_not_log_its_progress(monkeypatch, caplog):
    """The watcher's progress lines name the files and the courses ("Downloading
    FIT_TYPE_6: <name>"): they are not logged; the sync logs its numbers itself."""
    import types

    from openexpress import watch

    said = []

    def run_sync(s, progress=lambda m: None, **kw):
        progress(f"Downloading FIT_TYPE_6: {COURSE}")
        progress(f"Uploading FIT_TYPE_4/{ACTIVITY} (1.5 kB)")
        said.append(1)
        return service.SyncSummary()

    session = types.SimpleNamespace(
        mass_storage=False, logged_in=True, first_sync=False, client=None,
        device=types.SimpleNamespace(unit_id=1, description="Test watch"))
    monkeypatch.setattr(watch.service, "open_session", lambda: session)
    monkeypatch.setattr(watch.service, "require_account_device", lambda client, unit, fresh=False: None)
    monkeypatch.setattr(watch.service, "run_sync", run_sync)
    monkeypatch.setattr(watch.GLib, "idle_add", lambda fn, *args: None)
    with caplog.at_level(logging.DEBUG, logger="openexpress"):
        watch.Watcher()._sync()
    assert said == [1]
    logged = _logged(caplog)
    assert COURSE not in logged and ACTIVITY not in logged


@pytest.mark.parametrize("size, md5", [(len(DATA) + 5, None), (None, "0" * 32)])
def test_a_download_error_names_the_address_without_its_signature(server, tmp_path, size, md5):
    """A wrong size or MD5: the address as far as its path (its query carries the signature
    of the download)."""
    server.routes["/maps/gmapsupp.img"] = (200, {}, DATA)
    url = server.url + "/maps/gmapsupp.img?garmindlm=" + SIGNATURE
    with pytest.raises(download.DownloadError) as e:
        download.fetch([url], tmp_path / "f.img", size, md5 or md5hex(DATA))
    text = str(e.value)
    assert server.url + "/maps/gmapsupp.img" in text
    assert "garmindlm" not in text and "?" not in text and SIGNATURE[11:] not in text


def test_the_log_filter_hides_a_signature_after_an_underscore(log_file):
    """16 hex digits or more are hidden also after "_" (garmindlm=<time>_<signature>), while
    the names of files with fewer stay readable; old lines of the log (report --log) are
    filtered again by the same rule."""
    line = f"download of https://example.org/gmapsupp.img?garmindlm={SIGNATURE} failed"
    hidden = applog.redact(line)
    assert SIGNATURE[11:] not in hidden and "garmindlm=<number>_<hex> failed" in hidden
    assert applog.redact(hidden) == hidden  # the same on a second pass (tail)
    for kept in ("copied to Garmin/RemoteSW/006DA06910.GCD", "Garmin/GUPDATE.GCD",
                 "NewFiles/course_1a2b3c4d-5e6f-4a5b-9c8d-7e6f5a4b3c2d.FIT",
                 "unlock file md5 ab12_cd34", "size 6_657_384_448 B"):
        assert applog.redact(kept) == kept, kept
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(line + "\n", encoding="utf-8")  # written before the rule
    assert applog.tail(5) == [hidden]


def test_the_activity_file_is_not_logged_when_its_processing_is_unconfirmed(device_root,
                                                                           tmp_path, caplog):
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})

    class Unconfirmed(SyncClient):
        def upload_file(self, url, data, *, is_zip, last_file, authenticated):
            return UploadResult(201, {}, "https://up.example/activity-service/1", 0)

        def poll_location(self, url, delay):
            raise ConnectionError("no answer")

    with caplog.at_level(logging.INFO, logger="openexpress"):
        rep = sync.run_uploads(plan, dev, st, Unconfirmed(), state)
    assert rep.not_confirmed and ACTIVITY in " ".join(rep.not_confirmed)
    assert ACTIVITY not in _logged(caplog)
    assert "Could not confirm processing of FIT_TYPE_4" in _logged(caplog)
