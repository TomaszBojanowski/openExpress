"""`openexpress activities export NAME…`: an activity of the connected device or of its backups
as a GPX file "YYYY-MM-DD HH.MM Sport.gpx", written only with --apply, never over a file that
is there, by default into the GPX folder next to the backups (Documents/Garmin/GPX). A folder
stands for the device; made-up files only (fakes.fit_activity)."""

import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest
from conftest import DEVICE_XML
from fakes import fit_activity

from openexpress import cli

FIT_EPOCH = int(datetime(1989, 12, 31, tzinfo=UTC).timestamp())
UNIT = 1234567890  # the unit ID of DEVICE_XML
RUNNING, CYCLING, TRAINING = 1, 2, 10
WARSAW = (624_000_000, 250_000_000)  # a made-up position, in semicircles
GPX = "{http://www.topografix.com/GPX/1/1}"
RUN = "2026-10-30-07-00-00.fit"
RUN_GPX = "2026-10-30 07.00 Running.gpx"


def fit_time(*when) -> int:
    """A moment of local time as a time of the FIT protocol."""
    return int(datetime(*when).timestamp()) - FIT_EPOCH


@pytest.fixture
def device(tmp_path, backups):
    """A device with a run, a session of strength training (no positions) and a file still
    being written; a ride only in a backup."""
    root = tmp_path / "device"
    activities = root / "GARMIN" / "Activity"
    activities.mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    files = {
        RUN: fit_activity(fit_time(2026, 10, 30, 7, 0), sport=RUNNING, records=3,
                          position=WARSAW),
        "2026-10-29-18-00-00.fit": fit_activity(fit_time(2026, 10, 29, 18, 0), sport=TRAINING,
                                                records=3),
        "2026-10-28-06-00-00.fit": fit_activity(fit_time(2026, 10, 28, 6, 0), records=20,
                                                position=WARSAW)[:-40],
    }
    for n, (name, data) in enumerate(files.items()):
        (activities / name).write_bytes(data)
        os.utime(activities / name, (9_000 - n, 9_000 - n))
    copies = backups / str(UNIT) / "2025-10-02 (10.00.00)" / "FIT_TYPE_4"
    copies.mkdir(parents=True)
    (copies / "2025-09-14-09-00-00.fit").write_bytes(
        fit_activity(fit_time(2025, 9, 14, 9, 0), sport=CYCLING, records=4, position=WARSAW))
    return root


@pytest.fixture
def gpx_folder(backups):
    """The GPX folder of the tests: next to their folder of backups, as in Documents/Garmin."""
    return backups.parent / "GPX"


def export(capsys, device, *argv) -> tuple[int, list[str], str]:
    code = cli.main(["--path", str(device), "activities", "export", *argv])
    out, err = capsys.readouterr()
    return code, out.splitlines(), err


def test_a_dry_run_names_the_file_and_writes_nothing(device, gpx_folder, capsys):
    code, lines, _err = export(capsys, device, RUN)
    assert code == 0
    assert lines[1:] == [f"Would write {gpx_folder / RUN_GPX} (3 points)",
                         "Dry run: nothing was written. Add --apply to write it."]
    assert not gpx_folder.exists()


def test_apply_writes(device, gpx_folder, capsys, log_file):
    code, lines, _err = export(capsys, device, RUN, "--apply")
    assert code == 0
    assert lines[-1] == f"Wrote {gpx_folder / RUN_GPX} (3 points)."
    root = ET.fromstring((gpx_folder / RUN_GPX).read_bytes())
    assert root.findtext(f"{GPX}trk/{GPX}name") == "2026-10-30 07.00 Running"
    assert len(root.findall(f"{GPX}trk/{GPX}trkseg/{GPX}trkpt")) == 3
    log = log_file.read_text(encoding="utf-8")
    assert "activities export" in log and RUN not in log and "Running.gpx" not in log


@pytest.mark.parametrize("apply", [False, True])
def test_a_file_that_is_there_is_never_overwritten(device, gpx_folder, capsys, apply):
    gpx_folder.mkdir()
    (gpx_folder / RUN_GPX).write_text("mine")
    code, _lines, err = export(capsys, device, RUN, *(["--apply"] if apply else []))
    assert code == 1
    assert err.splitlines() == [
        f"Error: {gpx_folder / RUN_GPX} exists already; nothing is overwritten.",
        "Nothing was written."]
    assert (gpx_folder / RUN_GPX).read_text() == "mine"


def test_dest_writes_elsewhere(device, gpx_folder, tmp_path, capsys):
    elsewhere = tmp_path / "elsewhere" / "tracks"
    code, lines, _err = export(capsys, device, RUN, "--dest", str(elsewhere), "--apply")
    assert code == 0
    assert (elsewhere / RUN_GPX).is_file()
    assert lines[-1] == f"Wrote {elsewhere / RUN_GPX} (3 points)."
    assert not gpx_folder.exists()


def test_an_activity_only_in_a_backup(device, gpx_folder, capsys):
    code, lines, _err = export(capsys, device, "2025-09-14-09-00-00.fit", "--apply")
    assert code == 0
    assert lines[-1] == f"Wrote {gpx_folder / '2025-09-14 09.00 Cycling.gpx'} (4 points)."


def test_what_cannot_be_exported_says_why_and_nothing_is_written(device, gpx_folder, capsys):
    code, _lines, err = export(capsys, device, RUN, "2026-10-29-18-00-00.fit",
                               "2026-10-28-06-00-00.fit", "gone.fit", "--apply")
    assert code == 1
    no_positions = ("Error: 2026-10-29-18-00-00.fit: This activity has no GPS positions, so it "
                    "cannot be exported as GPX.")
    still_written = ("Error: 2026-10-28-06-00-00.fit was still being written by the device when "
                     "it was read. Unplug the device and plug it in again, then export it again.")
    assert err.splitlines() == [
        no_positions, still_written,
        "Error: No activity named gone.fit is on the device or in its backups.",
        "Nothing was written."]
    assert not gpx_folder.exists()  # not the run either


def test_export_needs_a_name(device):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--path", str(device), "activities", "export"])
    assert stopped.value.code == 2
