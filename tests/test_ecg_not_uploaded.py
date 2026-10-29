"""Tests that ECG recordings (FIT_TYPE_61) are never uploaded: Garmin's upload settings send
them to Garmin Health, which needs a sign-in of its own, and its 401 or 403 to the Connect login
would stop every sync. They stay on the device, with one line in the sync summary (window and
CLI). All devices, files and answers are made up."""

import json
from pathlib import Path

import pytest
from fakes import UPLOAD_SETTINGS, SyncAccount

from openexpress import cli, i18n, service, sync
from openexpress.connect import Unauthorized
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

UNIT = SyncAccount.UNIT
ECG_URL = "https://health.example/ghs/upload/v1/upload/fit"
ECG_TYPE = """  <DataType><Name>FIT_TYPE_61</Name>
   <File><Location><Path>Garmin/ECG</Path><FileExtension>ZIP</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
  </DataType>
"""
# As Garmin's upload settings name the type: one location, signed, that needs consent and
# the sign-in of Garmin Health.
SETTINGS = {"unittypename": "test", "datatypes": [
    *UPLOAD_SETTINGS["datatypes"],
    {"datatypename": "FIT_TYPE_61",
     "uploadlocations": [{"url": ECG_URL, "requiresauthentication": True,
                          "requiresuserconsent": True, "authenticationtype": 3,
                          "shoulddelete": True}]},
]}
LINE = "1 ECG recording was not uploaded: ECG recordings need a Garmin Health account"


def _declare_ecg(root: Path, recordings: int = 1) -> None:
    xml = root / "GARMIN/GarminDevice.xml"
    xml.write_text(xml.read_text().replace(
        "  <DataType><Name>FIT_TYPE_2</Name>", ECG_TYPE + "  <DataType><Name>FIT_TYPE_2</Name>",
        1))
    (root / "GARMIN/ECG").mkdir()
    for n in range(recordings):
        (root / f"GARMIN/ECG/ECG{n:05d}.ZIP").write_bytes(b"PK made-up ECG recording")


@pytest.fixture
def ecg_device(device_root):
    _declare_ecg(device_root)
    return device_root


class HealthAccount(SyncAccount):
    """The account, with the ECG type in the upload settings; Garmin Health answers an
    upload signed with the Connect login as the real client then reports it."""

    def upload_settings(self, unit_id):
        self.asked.append("upload settings")
        return SETTINGS

    def upload_file(self, url, data, **kw):
        if url == ECG_URL:
            self.asked.append("health")
            raise Unauthorized("Upload: unauthorized (HTTP 401). Please log in again.", 401)
        return super().upload_file(url, data, **kw)


def _storage_and_device(root):
    storage = LocalStorage(root)
    return storage, parse(storage.read("GARMIN/GarminDevice.xml"))


def test_ecg_recordings_are_never_planned_for_upload(ecg_device):
    storage, device = _storage_and_device(ecg_device)
    for only in (None, {"FIT_TYPE_61"}):
        plan = sync.plan_uploads(device, storage, SETTINGS, sync.SyncState(UNIT),
                                 only_types=only)
        assert "FIT_TYPE_61" not in {c.datatype for c in plan.candidates}, only
        assert plan.skipped_types["FIT_TYPE_61"] == "ECG (needs a Garmin Health account)"
        assert plan.ecg_recordings == 1


def test_a_first_sync_does_not_mark_them_as_uploaded(ecg_device):
    storage, device = _storage_and_device(ecg_device)
    marked = sync.plan_baseline(device, storage, SETTINGS, sync.SyncState(UNIT))
    assert "FIT_TYPE_61" not in {c.datatype for c in marked}


def test_an_ecg_recording_does_not_stop_the_sync(ecg_device):
    storage, device = _storage_and_device(ecg_device)
    account = HealthAccount(verified_unit_id=None)
    sync.SyncState(UNIT).save()
    summary = service.run_sync(service.DeviceSession(storage, device, Settings(), account))
    assert "health" not in account.asked and "queue" in account.asked
    assert summary.uploaded == 3  # two activities and the monitoring file
    record = service.load_last_sync(UNIT)
    assert {"kind": "ecg_not_uploaded", "count": 1} in record["lines"]
    assert (ecg_device / "GARMIN/ECG/ECG00000.ZIP").exists()  # left on the device


def test_the_command_line_says_so_once(ecg_device, monkeypatch, capsys):
    account = HealthAccount(verified_unit_id=None)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    sync.SyncState(UNIT).save()
    assert cli.main(["--path", str(ecg_device), "sync"]) == 0
    out = capsys.readouterr().out
    assert "FIT_TYPE_61" in out and "skipped: ECG (needs a Garmin Health account)" in out
    assert cli.main(["--path", str(ecg_device), "sync", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines().count(LINE) == 1  # as the other steps
    assert "health" not in account.asked


def test_no_line_without_recordings_on_the_device(device_root):
    _declare_ecg(device_root, recordings=0)
    storage, device = _storage_and_device(device_root)
    account = HealthAccount(verified_unit_id=None)
    sync.SyncState(UNIT).save()
    service.run_sync(service.DeviceSession(storage, device, Settings(), account))
    kinds = [line.get("kind") for line in service.load_last_sync(UNIT)["lines"]]
    assert "ecg_not_uploaded" not in kinds


def test_the_words_of_the_line(ecg_device):
    assert service._describe_step({"kind": "ecg_not_uploaded", "count": 1}) == [(LINE, False)]
    assert service._describe_step({"kind": "ecg_not_uploaded", "count": 3}) == [(
        "3 ECG recordings were not uploaded: ECG recordings need a Garmin Health account",
        False)]
    i18n.setup("pl")
    try:
        words = [service._describe_step({"kind": "ecg_not_uploaded", "count": n})[0][0]
                 for n in (1, 3, 5)]
    finally:
        i18n.setup("en")
    assert words == [
        "1 nagranie EKG nie zostało wysłane: nagrania EKG wymagają konta Garmin Health",
        "3 nagrania EKG nie zostały wysłane: nagrania EKG wymagają konta Garmin Health",
        "5 nagrań EKG nie zostało wysłanych: nagrania EKG wymagają konta Garmin Health"]


def test_the_synthetic_pattern_of_0_7_5_is_what_a_sync_writes():
    """tests/data/last_sync/format-0.7.5-ecg-not-uploaded.json, a synthetic pattern (no real
    sync has written one yet), has the lines a sync writes for a device with one new
    monitoring file and one ECG recording."""
    import types

    pattern = json.loads((Path(__file__).parent / "data/last_sync"
                          / "format-0.7.5-ecg-not-uploaded.json").read_text(encoding="utf-8"))
    report = types.SimpleNamespace(uploaded=[0], already_in_connect=[], duplicates=[],
                                   rejected=[], failed=[])
    assert pattern["lines"] == [
        service.new_files_step([types.SimpleNamespace(datatype="FIT_TYPE_32")]),
        service.uploads_step(report),
        service.ecg_not_uploaded_step(1)]
