"""The dry run of `sync` describes each file of Garmin Connect's queue the sync would write to
the device: it downloads it into memory and says what it is (a workout, a course, a schedule),
its sport, name or days, when it was made and whether it is whole. Nothing is written to the
device and nothing is acknowledged, so the sync that follows downloads it again. Made-up
files and a made-up account only."""

import os
from datetime import UTC, datetime

import pytest
from conftest import DEVICE_XML
from fakes import FitData, FitDefinition, SyncAccount, fit_file

from openexpress import cli, service
from openexpress.device.storage import LocalStorage

FIT_EPOCH = int(datetime(1989, 12, 31, tzinfo=UTC).timestamp())
ENUM, UINT16, UINT32, STRING = 0x00, 0x84, 0x86, 0x07
RUNNING, CYCLING = 1, 2


def fit_time(*when) -> int:
    """A moment of local time as a time of the FIT protocol."""
    return int(datetime(*when).timestamp()) - FIT_EPOCH


def local_fit_time(*when) -> int:
    """A day of the local clock as a local_date_time of the FIT protocol."""
    return int(datetime(*when, tzinfo=UTC).timestamp()) - FIT_EPOCH


def file_id(file_type: int, created: int) -> tuple:
    return (FitDefinition(0, 0, ((0, ENUM), (4, UINT32))), FitData(0, (file_type, created)))


WORKOUT = fit_file(
    *file_id(5, fit_time(2026, 10, 29, 20, 0)),
    # sport, num_valid_steps, wkt_name
    FitDefinition(1, 26, ((4, ENUM), (6, UINT16), (8, STRING, 24))),
    FitData(1, (RUNNING, 7, "Tempo 5×1 km")))
COURSE = fit_file(
    *file_id(6, fit_time(2026, 10, 30, 8, 0)),
    FitDefinition(1, 31, ((4, ENUM), (5, STRING, 16))), FitData(1, (CYCLING, "Morning loop")),
    FitDefinition(2, 20, ((253, UINT32),)), FitData(2, (fit_time(2026, 10, 30, 8, 0),)))
SCHEDULE = fit_file(
    *file_id(7, fit_time(2026, 10, 31, 9, 0)),
    # time_created of the workout, completed, type, scheduled_time
    FitDefinition(1, 28, ((3, UINT32), (4, ENUM), (5, ENUM), (6, UINT32))),
    *(FitData(1, (fit_time(2026, 10, 29, 20, 0), 0, 0, local_fit_time(2026, 11, day)))
      for day in (3, 17, 30)))

# The queue, as Garmin Connect lists it for the device.
QUEUE = [
    {"messageid": 11, "devicexmldatatype": "FIT_TYPE_5",
     "metadata": {"messageurl": "workout/11", "messagename": "Tempo"}},
    {"messageid": 12, "devicexmldatatype": "FIT_TYPE_6",
     "metadata": {"messageurl": "course/12", "messagename": "Morning loop"}},
    {"messageid": 13, "devicexmldatatype": "FIT_TYPE_7",
     "metadata": {"messageurl": "schedule/13", "messagename": "Plan"}},
]
FILES = {"workout/11": WORKOUT, "course/12": COURSE, "schedule/13": SCHEDULE}


class QueueAccount(SyncAccount):
    """The account, its queue of QUEUE, each file downloaded from FILES (an exception there:
    raised as the download's error)."""

    def __init__(self, files, **kw):
        super().__init__(messages=QUEUE, **kw)
        self.files = files

    def download_message(self, url, absolute):
        self.asked.append("download")
        self.require_verified()
        found = self.files[url]
        if isinstance(found, Exception):
            raise found
        return found


@pytest.fixture
def device(device_root, monkeypatch):
    """The test device, taking workouts and schedules from Garmin Connect too, over MTP."""
    takes = "".join(
        f"  <DataType><Name>{name}</Name>\n"
        "   <File><Location><Path>Garmin/NewFiles</Path><FileExtension>FIT</FileExtension>"
        "</Location>\n    <TransferDirection>InputToUnit</TransferDirection></File>\n"
        "  </DataType>\n" for name in ("FIT_TYPE_5", "FIT_TYPE_7"))
    xml = DEVICE_XML.replace("  <DataType><Name>FIT_TYPE_6</Name>",
                             takes + "  <DataType><Name>FIT_TYPE_6</Name>")
    (device_root / "GARMIN" / "GarminDevice.xml").write_text(xml)
    monkeypatch.setattr(service, "open_device_or_drive",
                        lambda **_kw: LocalStorage(device_root))
    return device_root


def dry_run(monkeypatch, capsys, files):
    account = QueueAccount(files)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    code = cli.main(["sync", "--download-only"])
    return code, capsys.readouterr().out.splitlines(), account


def what_is_on(root) -> list[tuple[str, int, int]]:
    return sorted((os.path.relpath(os.path.join(top, name), root),
                   os.path.getsize(os.path.join(top, name)),
                   os.stat(os.path.join(top, name)).st_mtime_ns)
                  for top, _folders, names in os.walk(root) for name in names)


def described(lines, name) -> str:
    """The line under the queue's line of `name`."""
    at = next(n for n, line in enumerate(lines) if f" {name} -> " in line)
    return lines[at + 1]


def test_a_line_for_each_file_of_the_queue(device, monkeypatch, capsys):
    code, lines, _account = dry_run(monkeypatch, capsys, FILES)
    assert code == 0
    assert "To download from Garmin Connect to the device: 3" in lines
    assert described(lines, "Tempo") == (
        "      workouts · Running · Tempo 5×1 km · 7 steps · created 2026-10-29 · whole")
    assert described(lines, "Morning loop") == (
        "      courses · Cycling · Morning loop · created 2026-10-30 · whole")
    assert described(lines, "Plan") == (
        "      schedules · 3 days from 2026-11-03 to 2026-11-30 · created 2026-10-31 · whole")


def test_nothing_is_acknowledged_nor_written_to_the_device(device, monkeypatch, capsys):
    before = what_is_on(device)
    _code, _lines, account = dry_run(monkeypatch, capsys, FILES)
    assert account.asked.count("download") == 3
    assert "acknowledge" not in account.asked and account.acks == []
    assert what_is_on(device) == before


def test_a_file_that_is_not_whole_says_so(device, monkeypatch, capsys):
    _code, lines, _account = dry_run(monkeypatch, capsys, {
        **FILES, "workout/11": WORKOUT[:-10], "course/12": b"no FIT file"})
    assert described(lines, "Tempo").endswith(
        f"· cut short: {len(WORKOUT) - 10} B read, {len(WORKOUT)} B declared")
    assert described(lines, "Morning loop") == "      not a FIT file (11 B read)"


def test_a_download_that_fails_is_one_line_and_the_dry_run_ends_well(device, monkeypatch,
                                                                    capsys):
    from openexpress.connect import ApiError

    code, lines, _account = dry_run(monkeypatch, capsys, {
        **FILES, "course/12": ApiError("Downloading a file from Garmin Connect: made-up failure", 503)})
    assert code == 0
    assert described(lines, "Morning loop") == (
        "      could not be downloaded: Downloading a file from Garmin Connect: made-up failure")
    assert described(lines, "Plan").startswith("      schedules · ")


def test_only_the_dry_run_describes(device, monkeypatch):
    """--apply describes nothing: the queue is downloaded once, by the sync itself."""
    from openexpress import sync

    monkeypatch.setattr(sync, "describe_queue", lambda items, client: pytest.fail("described"))
    account = QueueAccount(FILES)
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    assert cli.main(["sync", "--download-only", "--apply"]) in (0, 1)


def test_a_file_another_device_of_the_account_made_is_said_with_its_maker(
        device, monkeypatch, capsys):
    """Garmin Connect forwards the measurements of one device to the others: the dry run names
    the device that made such a file (its file_id), as the account lists it."""
    made = fit_file(
        FitDefinition(0, 0, ((0, ENUM), (1, UINT16), (2, UINT16), (4, UINT32))),
        FitData(0, (6, 1, 4532, fit_time(2026, 10, 30, 8, 0))),
        FitDefinition(1, 31, ((4, ENUM), (5, STRING, 16))), FitData(1, (CYCLING, "Morning loop")))

    class Account(QueueAccount):
        def account_devices(self, fresh=False):
            return [{"unitid": self.UNIT},
                    {"unitid": 4222333444, "partnumber": "006-B4532-00",
                     "productdisplayname": "Made-up Watch 7"}]

    account = Account({**FILES, "course/12": made})
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth: account)
    assert cli.main(["sync", "--download-only"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert described(lines, "Morning loop") == (
        "      courses · Cycling · Morning loop · made by Made-up Watch 7 · created 2026-10-30"
        " · whole")
