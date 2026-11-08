"""`openexpress activities`: the activities of the connected device and of its backups, one
row each - date, sport, time, distance, ascent, source, file - the newest 30 unless told
otherwise. A folder stands for the device (as over MTP), or the same folder as a USB drive;
made-up files only (fakes.fit_activity)."""

import os
import re
from datetime import UTC, datetime

import pytest
from conftest import DEVICE_XML
from fakes import drive_session, fit_activity

from openexpress import cli, service

FIT_EPOCH = int(datetime(1989, 12, 31, tzinfo=UTC).timestamp())
UNIT = 1234567890  # the unit ID of DEVICE_XML
RUNNING, CYCLING, TRAINING = 1, 2, 10


def fit_time(*when) -> int:
    """A moment of local time as a time of the FIT protocol."""
    return int(datetime(*when).timestamp()) - FIT_EPOCH


@pytest.fixture
def device(tmp_path):
    root = tmp_path / "device"
    (root / "GARMIN" / "Activity").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    return root


def on_device(root, name: str, data: bytes, changed: int) -> None:
    path = root / "GARMIN" / "Activity" / name
    path.write_bytes(data)
    os.utime(path, (changed, changed))


def in_backup(folder, stamp: str, name: str, data: bytes) -> None:
    activities = folder / str(UNIT) / stamp / "FIT_TYPE_4"
    activities.mkdir(parents=True, exist_ok=True)
    (activities / name).write_bytes(data)


def run(capsys, *argv) -> tuple[int, list[str]]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out.splitlines()


def table(lines: list[str]) -> list[str]:
    """The lines from the heading of the table on (before it: the device, and the step of the
    reading as the command line writes it when it is no terminal)."""
    return lines[next(n for n, line in enumerate(lines) if line.startswith("Date")):]


def columns(line: str) -> list[str]:
    return re.split(r"\s{2,}", line.strip())


def a_week(root, backups) -> None:
    """A made-up week on the device and a ride only in a backup."""
    on_device(root, "2026-10-30-07-00-00.fit",
              fit_activity(fit_time(2026, 10, 30, 7, 0), sport=RUNNING, timer_ms=3_731_000,
                           distance_cm=1_240_000, ascent=135), 6_000)
    on_device(root, "2026-10-29-18-00-00.fit",
              fit_activity(fit_time(2026, 10, 29, 18, 0), sport=TRAINING, timer_ms=2_702_000,
                           distance_cm=None, ascent=None), 5_000)
    on_device(root, "2026-10-28-06-00-00.fit",
              fit_activity(fit_time(2026, 10, 28, 6, 0), records=20)[:-40], 4_000)
    on_device(root, "2026-10-27-07-00-00.fit",
              fit_activity(fit_time(2026, 10, 27, 7, 0), bad_crc=True), 3_000)
    on_device(root, "2026-10-26-07-00-00.fit", b"no FIT file at all", 2_000)
    in_backup(backups, "2025-10-02 (10.00.00)", "2025-09-14-09-00-00.fit",
              fit_activity(fit_time(2025, 9, 14, 9, 0), sport=CYCLING, timer_ms=13_280_000,
                           distance_cm=8_810_000, ascent=960))


def test_the_columns(device, backups, capsys, log_file):
    a_week(device, backups)
    code, lines = run(capsys, "--path", str(device), "activities")
    assert code == 0
    assert lines[:2] == ["Test watch, software 23.39", "  Reading activities"]
    assert columns(lines[2]) == ["Date", "Sport", "Time", "Distance", "Ascent", "Source", "File"]
    assert columns(lines[3]) == ["2026-10-30 07:00", "Running", "1:02:11", "12.40 km", "135 m",
                                 "device", "2026-10-30-07-00-00.fit"]
    # No file name nor date in the log.
    log = log_file.read_text(encoding="utf-8")
    assert "2026-10-30" not in log and "activities:" in log


def test_the_sources_and_the_states(device, backups, capsys):
    a_week(device, backups)
    _code, lines = run(capsys, "--path", str(device), "activities")
    rows = table(lines)[1:]
    assert columns(rows[1]) == ["2026-10-29 18:00", "Training", "0:45:02", "–", "–", "device",
                                "2026-10-29-18-00-00.fit"]
    assert columns(rows[2]) == ["2026-10-28 06:00", "–", "–", "–", "–", "device",
                                "2026-10-28-06-00-00.fit"]
    assert rows[3].strip() == "still being written: unplug the device and plug it in again"
    # Under the sport, where the row's own words start.
    assert rows[3].index("still") == rows[2].index("–")
    assert columns(rows[4])[0] == "2026-10-27 07:00"
    assert rows[5].strip() == "damaged file"
    assert columns(rows[6]) == ["–", "–", "–", "–", "–", "device", "2026-10-26-07-00-00.fit"]
    assert rows[7].strip() == "unreadable file"
    assert columns(rows[8]) == ["2025-09-14 09:00", "Cycling", "3:41:20", "88.10 km", "960 m",
                                "backup 2025-10-02", "2025-09-14-09-00-00.fit"]
    assert rows[9:] == ["", "6 activities. Only in backups: 1."]


def test_a_usb_drive_lists_as_over_mtp(device, backups, capsys, monkeypatch):
    a_week(device, backups)
    _code, over_mtp = run(capsys, "--path", str(device), "activities")
    storage = drive_session(device, None).storage
    monkeypatch.setattr(service, "open_device_or_drive", lambda **_kw: storage)
    code, on_a_drive = run(capsys, "activities")
    assert code == 0
    assert on_a_drive == over_mtp


def test_a_device_without_activities_says_so(device, capsys):
    xml = device / "GARMIN" / "GarminDevice.xml"
    xml.write_text(DEVICE_XML.replace("<Name>FIT_TYPE_4</Name>", "<Name>FIT_TYPE_999</Name>"))
    code, lines = run(capsys, "--path", str(device), "activities")
    assert code == 0
    assert lines[1:] == ["This device keeps no activities."]


def test_no_activity_yet(device, capsys):
    code, lines = run(capsys, "--path", str(device), "activities")
    assert code == 0
    assert lines[1:] == ["No activities on the device or in its backups."]


def thirty_two(root) -> None:
    for n in range(32):
        on_device(root, f"2026-09-{n:02}.fit", fit_activity(fit_time(2026, 9, 1) + n * 3_600),
                  1_000 + n)


def test_the_newest_thirty_unless_told(device, capsys):
    thirty_two(device)
    _code, lines = run(capsys, "--path", str(device), "activities")
    rows = [line for line in table(lines)[1:] if line.strip()]
    assert len(rows) == 31  # and the line below them
    assert columns(rows[0])[-1] == "2026-09-31.fit"
    assert rows[-1] == "The newest 30 of 32; --all shows every one."


def test_limit_and_all(device, capsys):
    thirty_two(device)
    _code, lines = run(capsys, "--path", str(device), "activities", "--limit", "5")
    rows = table(lines)[1:]
    assert [columns(line)[-1] for line in rows[:5]] == [
        f"2026-09-{n:02}.fit" for n in (31, 30, 29, 28, 27)]
    assert rows[5:] == ["", "The newest 5 of 32; --all shows every one."]
    _code, lines = run(capsys, "--path", str(device), "activities", "--all")
    assert len(table(lines)[1:-2]) == 32
    assert lines[-1] == "32 activities."


@pytest.mark.parametrize("value", ["0", "-1", "many"])
def test_a_limit_must_be_a_number_from_one(device, value):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--path", str(device), "activities", "--limit", value])
    assert stopped.value.code == 2


def test_backups_in_another_folder(device, tmp_path, capsys):
    elsewhere = tmp_path / "elsewhere"
    in_backup(elsewhere, "2025-10-02 (10.00.00)", "2025-09-14-09-00-00.fit",
              fit_activity(fit_time(2025, 9, 14, 9, 0)))
    _code, lines = run(capsys, "--path", str(device), "activities")
    assert lines[1:] == ["No activities on the device or in its backups."]
    _code, lines = run(capsys, "--path", str(device), "activities", "--backups", str(elsewhere))
    assert columns(table(lines)[1])[-2:] == ["backup 2025-10-02", "2025-09-14-09-00-00.fit"]
