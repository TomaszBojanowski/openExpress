"""What a sync writes to the device from Garmin's queue counts as received (PLAN (9),
24.10.2026): the files of the queue, and also TrueUp and the Connect IQ apps written, so that
the row "Last sync" does not say "nothing to upload" alone when something came to the device;
an app left in the queue (not written) is not received. All answers here are made up."""

from test_upload_consent import UNIT, Account

from openexpress import ciq, i18n, last_sync, service, sync
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

TRUEUP = {"messageid": 9, "messagetype": "true_up", "devicexmldatatype": "FIT_TYPE_39",
          "metadata": {"messageurl": "trueup/9"}}
APP = {"messageid": 7, "devicexmldatatype": "IQWatchFaces",
       "metadata": {"messageurl": "app/7", "messagename": "Face It",
                    "appdetails": {"id": "2a6b4b8e-9a37-4a3e-8f0f-2e7d1f0b9c11"}}}


# The made-up device takes TrueUp and watch faces too (as the fēnix 8 does).
TAKES = ("<DataType><Name>FIT_TYPE_39</Name><File><Location><Path>Garmin/NewFiles</Path>"
         "<FileExtension>FIT</FileExtension></Location><TransferDirection>InputToUnit"
         "</TransferDirection></File></DataType>"
         "<DataType><Name>IQWatchFaces</Name><File><Location><Path>GARMIN/APPS/MEDIA</Path>"
         "<FileExtension>PRG</FileExtension></Location><TransferDirection>InputOutput"
         "</TransferDirection></File></DataType>")


def _synced_once(device_root):
    """A session of the made-up device synced before (its files are known): the next sync has
    nothing to upload."""
    xml = device_root / "GARMIN/GarminDevice.xml"
    xml.write_text(xml.read_text().replace("</MassStorageMode>", TAKES + "</MassStorageMode>"))
    storage = LocalStorage(device_root)
    session = service.DeviceSession(storage, parse(storage.read("GARMIN/GarminDevice.xml")),
                                    Settings(), Account(messages=[]))
    state = sync.SyncState(UNIT)
    if state.file.exists():
        state.file.unlink()
    service.run_sync(session, first_run="baseline")
    return session


def _row(record, language="en"):
    i18n.setup(language)
    try:
        return last_sync.describe(record).counts
    finally:
        i18n.setup("en")


def test_trueup_written_from_the_queue_is_received(device_root, xdg):
    session = _synced_once(device_root)
    session.client.messages = [TRUEUP]
    summary = service.run_sync(session)
    assert (summary.uploaded, summary.downloaded) == (0, 1)
    assert service.describe_lines(summary.lines) == [
        "TrueUp: daily totals aligned with your other Garmin devices"]
    record = service.load_last_sync(UNIT)
    assert record["downloaded"] == 1
    assert _row(record) == "nothing to upload · 1 received"
    assert _row(record, "pl") == "nic do wysłania · 1 odebrany"


def test_an_app_written_from_the_queue_is_received_one_left_there_is_not(device_root, xdg,
                                                                         monkeypatch):
    def written(device, storage, client, messages, known, **kw):
        return ciq.IqInstallReport(installed=["Face It"])

    def left(device, storage, client, messages, known, **kw):
        return ciq.IqInstallReport(for_phone=["Face It"])

    session = _synced_once(device_root)
    session.client.messages = [APP]
    monkeypatch.setattr(ciq, "install_queued", written)
    summary = service.run_sync(session)
    assert summary.downloaded == 1
    assert "Installed Connect IQ apps: Face It" in service.describe_lines(summary.lines)
    monkeypatch.setattr(ciq, "install_queued", left)
    summary = service.run_sync(session)
    assert summary.downloaded == 0
    assert _row(service.load_last_sync(UNIT)) == "nothing to upload"


def test_files_trueup_and_apps_are_received_together(device_root, xdg, monkeypatch):
    """A course (a file of the queue), TrueUp and an app: three received."""
    session = _synced_once(device_root)
    session.client.messages = [
        {"messageid": 5, "devicexmldatatype": "FIT_TYPE_6",
         "metadata": {"messageurl": "course", "messagename": "A course"}}, TRUEUP, APP]
    monkeypatch.setattr(ciq, "install_queued", lambda *a, **kw: ciq.IqInstallReport(
        installed=["Face It"]))
    summary = service.run_sync(session)
    assert summary.downloaded == 3
    assert _row(service.load_last_sync(UNIT)) == "nothing to upload · 3 received"
