"""What a sync writes to the device from Garmin's queue counts as received (PLAN (9),
24.10.2026): the files of the queue, and also TrueUp and the Connect IQ apps written, so that
the row "Last sync" does not say "nothing to upload" alone when something came to the device;
an app left in the queue (not written) is not received. All answers here are made up."""

import pytest
from test_upload_consent import UNIT, Account

from openexpress import ciq, i18n, last_sync, service, sync
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

NB = "\u00a0"

TRUEUP = {"messageid": 9, "messagetype": "true_up", "devicexmldatatype": "FIT_TYPE_39",
          "metadata": {"messageurl": "trueup/9"}}
APP = {"messageid": 7, "devicexmldatatype": "IQWatchFaces",
       "metadata": {"messageurl": "app/7", "messagename": "Face It",
                    "appdetails": {"id": "2a6b4b8e-9a37-4a3e-8f0f-2e7d1f0b9c11"}}}


# The made-up device takes TrueUp, metrics and watch faces too (as the fēnix 8 does).
TAKES = ("<DataType><Name>FIT_TYPE_39</Name><File><Location><Path>Garmin/NewFiles</Path>"
         "<FileExtension>FIT</FileExtension></Location><TransferDirection>InputToUnit"
         "</TransferDirection></File></DataType>"
         "<DataType><Name>FIT_TYPE_44</Name><File><Location><Path>Garmin/NewFiles</Path>"
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


# --- the line of the files of the queue (25.10.2026) ----------------------------------------

METRICS = [{"messageid": 20 + n, "devicexmldatatype": "FIT_TYPE_44",
            "metadata": {"messageurl": f"metrics/{n}", "messagename": "Metrics"}}
           for n in range(3)]
COURSE = {"messageid": 5, "devicexmldatatype": "FIT_TYPE_6",
          "metadata": {"messageurl": "course", "messagename": "A course"}}


def _details(record, language="en"):
    i18n.setup(language)
    try:
        return [text for text, _warning in last_sync.describe(record).steps]
    finally:
        i18n.setup("en")


def test_the_files_of_the_queue_get_a_line_of_their_types(device_root, xdg, monkeypatch):
    """As the sync of an Edge 1050 of 25.10.2026: three metrics files and TrueUp from the
    queue (and here an app): one line for the three files, by their type, kept as data; TrueUp
    and the app have their own lines and are not counted in it."""
    session = _synced_once(device_root)
    session.client.messages = [*METRICS, TRUEUP, APP]
    monkeypatch.setattr(ciq, "install_queued", lambda *a, **kw: ciq.IqInstallReport(
        installed=["Face It"]))
    summary = service.run_sync(session)
    assert summary.downloaded == 5
    record = service.load_last_sync(UNIT)
    assert service.received_files_step(["FIT_TYPE_44"] * 3) == {
        "kind": "received_files", "types": {"FIT_TYPE_44": 3}}
    assert [line for line in record["lines"]
            if line.get("kind") == "received_files"] == [service.received_files_step(
                ["FIT_TYPE_44"] * 3)]
    assert _details(record) == [
        "3 files from Garmin Connect: metrics ×3",
        "Installed Connect IQ apps: Face It",
        "TrueUp: daily totals aligned with your other Garmin devices"]
    assert _details(record, "pl")[0] == f"3 pliki z{NB}Garmin Connect: metryki ×3"
    assert _row(record) == "nothing to upload · 5 received"


def test_one_file_of_the_queue(device_root, xdg):
    session = _synced_once(device_root)
    session.client.messages = [COURSE]
    service.run_sync(session)
    record = service.load_last_sync(UNIT)
    assert _details(record) == ["1 file from Garmin Connect: courses"]
    assert _details(record, "pl") == [f"1 plik z{NB}Garmin Connect: kursy"]


def test_no_file_from_the_queue_no_line(device_root, xdg):
    """Only TrueUp from the queue, or nothing at all: no line of files."""
    session = _synced_once(device_root)
    for messages in ([TRUEUP], []):
        session.client.messages = messages
        service.run_sync(session)
        record = service.load_last_sync(UNIT)
        assert [line for line in record["lines"] if line.get("kind") == "received_files"] == []


@pytest.mark.parametrize("language, words", [
    ("en", ("6 files from Garmin Connect: activity summary, metrics ×3, other data "
            "(FIT_TYPE_99) ×2")),
    ("pl", (f"6 plików z{NB}Garmin Connect: podsumowanie aktywności, metryki ×3, inne dane "
            "(FIT_TYPE_99) ×2")),
])
def test_the_types_are_named_as_the_files_from_the_device(language, words):
    """Named as in "Files from the device": in the order of the names, ×N when more than one,
    a type without a name as other data; activity summary (FIT_TYPE_20) has a name."""
    line = service.received_files_step(["FIT_TYPE_99", "FIT_TYPE_44", "FIT_TYPE_20",
                                        "FIT_TYPE_44", "FIT_TYPE_99", "FIT_TYPE_44"])
    assert _details({"lines": [line]}, language) == [words]


def test_the_plural_forms_in_polish():
    def said(n):
        return _details({"lines": [service.received_files_step(["FIT_TYPE_44"] * n)]}, "pl")

    assert said(2) == [f"2 pliki z{NB}Garmin Connect: metryki ×2"]
    assert said(5) == [f"5 plików z{NB}Garmin Connect: metryki ×5"]
    assert said(22) == [f"22 pliki z{NB}Garmin Connect: metryki ×22"]


def test_a_record_without_the_line_is_shown_as_before():
    """A record of 0.6.20 or older (the files of the queue only counted): the same details."""
    record = {"downloaded": 4, "lines": [service.trueup_step()]}
    assert _details(record) == ["TrueUp: daily totals aligned with your other Garmin devices"]
    assert _row(record) == "nothing to upload · 4 received"


def test_the_pattern_of_0_6_21_is_what_a_sync_writes():
    """tests/data/last_sync/format-0.6.21-received-files.json has the lines a sync writes for
    the Edge's sync of 25.10.2026 (eight new files, three metrics files and TrueUp)."""
    import json
    import types
    from pathlib import Path

    pattern = json.loads((Path(__file__).parent / "data/last_sync"
                          / "format-0.6.21-received-files.json").read_text(encoding="utf-8"))
    report = types.SimpleNamespace(uploaded=[0] * 8, already_in_connect=[], duplicates=[],
                                   rejected=[], failed=[])
    candidates = [types.SimpleNamespace(datatype=t) for t, n in (
        ("FIT_TYPE_41", 1), ("FIT_TYPE_77", 3), ("FIT_TYPE_35", 1), ("FIT_TYPE_32", 3))
        for _ in range(n)]
    assert pattern["lines"] == [service.new_files_step(candidates),
                                service.uploads_step(report),
                                service.received_files_step(["FIT_TYPE_44"] * 3),
                                service.trueup_step()]


# Types that come from Garmin Connect's queue to a device (25.10.2026): workouts, courses,
# schedules, goals and segments (FIT SDK), personal records and gear tracking (FIT profile;
# both seen in the queue in the logs). Named as the other types are: what the data is, in the
# plural for things counted.
QUEUE_TYPES = [
    ("FIT_TYPE_5", "workouts", "treningi"),
    ("FIT_TYPE_6", "courses", "kursy"),
    ("FIT_TYPE_7", "schedules", "harmonogramy"),
    ("FIT_TYPE_11", "goals", "cele"),
    ("FIT_TYPE_29", "personal records", "rekordy osobiste"),  # as Garmin Connect
    ("FIT_TYPE_34", "segments", "segmenty"),
    ("FIT_TYPE_87", "gear tracking", "śledzenie sprzętu"),
]


@pytest.mark.parametrize("datatype, english, polish", QUEUE_TYPES)
def test_the_types_the_queue_brings_have_names(datatype, english, polish):
    assert service.type_name(datatype) == english
    i18n.setup("pl")
    try:
        assert service.type_name(datatype) == polish
    finally:
        i18n.setup("en")


def test_courses_and_workouts_in_the_line_of_the_queue():
    line = service.received_files_step(["FIT_TYPE_6", "FIT_TYPE_5", "FIT_TYPE_6"])
    assert _details({"lines": [line]}) == ["3 files from Garmin Connect: workouts, courses ×2"]
    assert _details({"lines": [line]}, "pl") == [
        f"3 pliki z{NB}Garmin Connect: treningi, kursy ×2"]
