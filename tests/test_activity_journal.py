"""The activity journal (activity_journal.py): a line for each activity read (the summary, the
shape of its track without its place, its elevation profile), a line for each connection, the
same activity read again kept once, a file not whole left out, nothing of the device's
identity or a coordinate in it, the file 0600; its range and gaps; deleted on request.
Made-up files only; a folder stands for the device."""

import json
import logging
import os
import stat
import types
from datetime import datetime
from pathlib import Path

import pytest
from conftest import DEVICE_XML
from fakes import FitData, FitDefinition, fit_file

from openexpress import fit

START = 1_100_000_000  # a time of the FIT protocol
UNIX = START + fit.FIT_EPOCH_OFFSET
UNIT_ID = 1234567890  # the made-up watch of DEVICE_XML
SERIAL = 3912345678  # a made-up serial number in file_id
SEEN = datetime(2026, 11, 25, 18, 0)

# file_id: type, manufacturer, product, serial number, time created
FILE_ID = FitDefinition(0, 0, ((0, 0x00), (1, 0x84), (2, 0x84), (3, 0x8C), (4, 0x86)))
# timestamp, position, enhanced altitude, heart rate, distance
RECORD = FitDefinition(1, 20, ((253, 0x86), (0, 0x85), (1, 0x85), (78, 0x86), (3, 0x02),
                               (5, 0x86)))
# start time, sport, sub sport, elapsed and timer time, distance, average and maximum heart
# rate, ascent, descent
SESSION = FitDefinition(2, 18, ((2, 0x86), (5, 0x00), (6, 0x00), (7, 0x86), (8, 0x86),
                                (9, 0x86), (16, 0x02), (17, 0x02), (22, 0x84), (23, 0x84)))
# time in zone: reference message, its index, the times in the zones, their bounds
ZONES = FitDefinition(3, 216, ((0, 0x84), (1, 0x84), (2, 0x86, 24), (6, 0x02, 6)))
ACTIVITY = FitDefinition(4, 34, ((253, 0x86), (5, 0x86)))
USER_PROFILE = FitDefinition(5, 3, ((6, 0x00), (13, 0x00), (14, 0x00)))


def semicircles(degrees: float) -> int:
    return round(degrees * 2**31 / 180)


def altitude(metres: float) -> int:
    return round((metres + 500) * 5)


def a_run(created: int = START, *, records: int = 60, product: int = 4532,
          **options) -> bytes:
    """A made-up run around a square of about 200 m, starting at 52°N 21°E, climbing as it
    goes: a minute of records, a second apart."""
    side = records // 4 or 1
    points = []
    for n in range(records):
        leg, step = divmod(n, side)
        north, east = [(step, 0), (side, step), (side - step, side), (0, side - step)][leg % 4]
        points.append(FitData(1, (created + n, semicircles(52 + north * 3e-5),
                                  semicircles(21 + east * 5e-5), altitude(100 + n * 0.5),
                                  140 + n % 10, n * 350)))
    return fit_file(FILE_ID, FitData(0, (4, 1, product, SERIAL, created)), RECORD, *points,
                    SESSION,
                    FitData(2, (created, 1, 3, records * 1000, records * 1000, records * 350,
                                145, 150, 30, 0)),
                    ZONES, FitData(3, (18, 0, (60_000, 120_000, 180_000, 0, 0, 0),
                                       (100, 120, 140, 160, 180, 200))),
                    ACTIVITY, FitData(4, (created + records, created + records + 3600)),
                    USER_PROFILE, FitData(5, (0, 0, 0)), **options)


def samples_of(data: bytes):
    return fit.activity_data(data).samples


# --- an activity's line ------------------------------------------------------------------------

def test_the_shape_keeps_no_place():
    from openexpress import activity_journal as journal

    track = journal.shape(samples_of(a_run()))
    points = track["points"]
    assert all(isinstance(v, int) and 0 <= v <= journal.SHAPE_SCALE for p in points for v in p)
    assert max(max(p[0] for p in points), max(p[1] for p in points)) == journal.SHAPE_SCALE
    # About 15 × 5e-5° of longitude at 52°N (~51 m) and 15 × 3e-5° of latitude (~50 m).
    assert 40 < track["width"] < 60 and 40 < track["height"] < 60
    # The start (the south-west corner, north up) is the first point.
    assert points[0][0] == 0 and points[0][1] == max(p[1] for p in points)
    text = json.dumps(track)
    assert "52." not in text and "21." not in text


def test_a_long_track_is_simplified_to_at_most_the_points_kept():
    from openexpress import activity_journal as journal

    zigzag = [(n, (n % 7) * 3) for n in range(5000)]
    kept = journal.simplified(zigzag, journal.SHAPE_POINTS)
    assert len(kept) <= journal.SHAPE_POINTS
    assert kept[0] == zigzag[0] and kept[-1] == zigzag[-1]
    straight = [(n, 0) for n in range(1000)]
    assert journal.simplified(straight, journal.SHAPE_POINTS) == [(0, 0), (999, 0)]


def test_without_positions_no_shape():
    from openexpress import activity_journal as journal

    assert journal.shape([]) is None
    one = fit.Sample(UNIX, latitude=52.0, longitude=21.0)
    assert journal.shape([one, one]) is None  # standing still


def test_the_elevation_profile_along_the_distance():
    from openexpress import activity_journal as journal

    climbing = [fit.Sample(UNIX + n, distance=n * 10.0, altitude=100 + n) for n in range(1000)]
    found = journal.profile(climbing)
    assert len(found) == journal.PROFILE_POINTS
    assert found[0] == 100 and found[-1] == 1099 and found == sorted(found)
    assert all(isinstance(v, int) for v in found)
    few = [fit.Sample(UNIX + n, distance=n * 10.0, altitude=200.0) for n in range(5)]
    assert journal.profile(few) == [200] * 5
    # Without distance: along the time; without altitude: none.
    on_the_spot = [fit.Sample(UNIX + n, altitude=50.0 + n) for n in range(10)]
    assert journal.profile(on_the_spot) == [50 + n for n in range(10)]
    assert journal.profile([fit.Sample(UNIX + n) for n in range(10)]) is None


def test_the_line_of_an_activity():
    from openexpress import activity_journal as journal

    line = journal.entry(fit.activity_data(a_run()), "2004-11-09-07-46-40.fit", 4321, "device",
                         SEEN)
    assert line["key"] == f"{UNIX}-4532"
    assert line["file"] == {"name": "2004-11-09-07-46-40.fit", "size": 4321}
    assert line["from"] == "device" and line["seen"].startswith("2026-11-25T18:00:00")
    assert (line["start"], line["utc_offset"], line["sport"], line["sub_sport"]) == (
        UNIX, 3600, 1, 3)
    assert (line["timer_time"], line["distance"], line["ascent"]) == (60, 210, 30)
    assert (line["avg_heart_rate"], line["max_heart_rate"]) == (145, 150)
    assert line["zones"] == [60, 120, 180, 0, 0, 0]  # the times; never the zones' bounds
    assert line["units"] == {"distance": "metric", "elevation": "metric", "speed": "metric"}
    assert line["has_track"] and line["has_heart_rate"]
    assert len(line["profile"]) == 60 and line["profile"][0] == 100
    text = json.dumps(line)
    assert str(SERIAL) not in text and str(UNIT_ID) not in text
    assert "200, 160" not in text and "180, 200" not in text  # no bounds of the zones


def test_a_file_not_whole_has_no_line():
    from openexpress import activity_journal as journal

    for data in (a_run(cut=200), a_run(bad_crc=True)):
        assert journal.entry(fit.activity_data(data), "x.fit", 1, "device", SEEN) is None


# --- at a connection ---------------------------------------------------------------------------

def on_device(folder: Path, files: dict[str, bytes]):
    """A folder as a device with these activity files: its device and storage."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    activity = folder / "GARMIN" / "Activity"
    activity.mkdir(parents=True, exist_ok=True)
    (folder / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    for n, (name, data) in enumerate(files.items()):
        (activity / name).write_bytes(data)
        os.utime(activity / name, (1_000 + n, 1_000 + n))
    storage = LocalStorage(folder)
    return types.SimpleNamespace(device=parse(storage.read("GARMIN/GarminDevice.xml")),
                                 storage=storage)


def in_a_backup(backups: Path, files: dict[str, bytes]) -> None:
    from openexpress.backup import STAMP
    from openexpress.sync import ACTIVITY_TYPE

    folder = backups / str(UNIT_ID) / datetime(2025, 10, 2, 10, 0).strftime(STAMP) / ACTIVITY_TYPE
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)


DAY = 86_400
THREE = {"a.fit": a_run(START), "b.fit": a_run(START + DAY), "c.fit": a_run(START + 2 * DAY)}
NOT_WHOLE = {"cut.fit": a_run(START + 3 * DAY, cut=300), "bad.fit": a_run(START + 4 * DAY,
                                                                          bad_crc=True),
             "notes.fit": b"no FIT file"}


def counted_reads(found):
    """A read_file for add that counts the files it reads."""
    reads = []

    def read_file(a):
        reads.append(a.name)
        if a.source.value == "device":
            return found.storage.read_at(a.path)
        return Path(a.path).read_bytes()
    return reads, read_file


def test_the_first_reading_takes_the_device_and_its_backups(tmp_path, backups, caplog):
    from openexpress import activity_journal as journal

    found = on_device(tmp_path / "device", {**THREE, **NOT_WHOLE})
    in_a_backup(backups, {"old.fit": a_run(START - 30 * DAY), "a.fit": THREE["a.fit"]})
    reads, read_file = counted_reads(found)
    caplog.set_level(logging.INFO, logger="openexpress")
    did = journal.add(found.device, found.storage, backups, now=SEEN, read_file=read_file)
    assert (did.added, did.on_device, did.not_whole) == (4, 6, 3)
    assert sorted(reads) == ["a.fit", "b.fit", "bad.fit", "c.fit", "cut.fit", "notes.fit",
                             "old.fit"]
    where = journal.path(UNIT_ID)
    assert stat.S_IMODE(where.stat().st_mode) == 0o600
    kept = journal.read(where)
    assert len(kept.activities) == 4 and kept.skipped == 0
    assert kept.oldest() == UNIX - 30 * DAY
    assert [c["on_device"] for c in kept.connections] == [6]
    assert (kept.connections[0]["oldest"], kept.connections[0]["newest"]) == (UNIX,
                                                                            UNIX + 2 * DAY)
    said = " ".join(r.getMessage() for r in caplog.records if "journal" in r.getMessage())
    assert "4 added, 6 on the device, 3 not whole" in said
    # Numbers and times only: no file name, date, sport or place.
    import re

    assert ".fit" not in said and str(UNIT_ID) not in said
    assert not re.search(r"\d{4}-\d{2}-\d{2}|running|52\.|21\.", said)


def test_the_device_report_holds_nothing_of_it(tmp_path, backups):
    from openexpress import activity_journal as journal
    from openexpress import report

    found = on_device(tmp_path / "device", dict(THREE))
    journal.add(found.device, found.storage, backups, now=SEEN)
    text = report.build(found.device, found.storage, logged_in=True)
    assert "journal" not in text.casefold()
    assert "2004-11" not in text and "a.fit" not in text


def test_the_next_reading_reads_only_what_is_new(tmp_path, backups):
    from openexpress import activity_journal as journal

    found = on_device(tmp_path / "device", dict(THREE))
    journal.add(found.device, found.storage, backups, now=SEEN)
    reads, read_file = counted_reads(found)
    did = journal.add(found.device, found.storage, backups, now=SEEN, read_file=read_file)
    assert (did.added, reads) == (0, [])
    found = on_device(tmp_path / "device", {"d.fit": a_run(START + 5 * DAY)})
    did = journal.add(found.device, found.storage, backups, now=SEEN, read_file=read_file)
    assert (did.added, reads) == (1, ["d.fit"])
    kept = journal.read(journal.path(UNIT_ID))
    assert len(kept.activities) == 4 and len(kept.connections) == 3


def test_the_same_activity_found_again_is_one(tmp_path, backups):
    """The same file under another name (copied back, renamed): one activity."""
    from openexpress import activity_journal as journal

    found = on_device(tmp_path / "device", {"a.fit": THREE["a.fit"],
                                            "copy.fit": THREE["a.fit"]})
    did = journal.add(found.device, found.storage, backups, now=SEEN)
    assert did.added == 2
    assert len(journal.read(journal.path(UNIT_ID)).activities) == 1


def test_turned_off_meanwhile_it_stops_and_keeps_what_was_written(tmp_path, backups):
    from openexpress import activity_journal as journal

    found = on_device(tmp_path / "device", dict(THREE))
    answers = iter([True, False])
    did = journal.add(found.device, found.storage, backups, now=SEEN,
                      keep_going=lambda: next(answers))
    assert did.added == 1
    kept = journal.read(journal.path(UNIT_ID))
    assert len(kept.activities) == 1 and kept.connections == []


def test_a_device_that_stops_answering_keeps_what_was_written(tmp_path, backups):
    from openexpress import activity_journal as journal
    from openexpress.device.storage import DeviceError

    found = on_device(tmp_path / "device", dict(THREE))
    reads, read_file = counted_reads(found)

    def failing(a):
        if len(reads) == 2:
            raise DeviceError("made up: unplugged")
        return read_file(a)
    with pytest.raises(DeviceError):
        journal.add(found.device, found.storage, backups, now=SEEN, read_file=failing)
    assert len(journal.read(journal.path(UNIT_ID)).activities) == 2


def test_a_test_drive_keeps_its_own(tmp_path, backups):
    from openexpress import activity_journal as journal

    found = on_device(tmp_path / "device", dict(THREE))
    journal.add(found.device, found.storage, backups, now=SEEN, test_drive=True)
    assert journal.path(UNIT_ID, True).is_file() and not journal.path(UNIT_ID).exists()


# --- the file ----------------------------------------------------------------------------------

def connection(oldest, newest, on_device=1):
    from openexpress import activity_journal as journal

    return journal.connection(SEEN, on_device, oldest, newest)


def test_the_gaps():
    from openexpress import activity_journal as journal

    kept = journal.Journal(connections=[connection(10, 50), connection(30, 80),
                                        connection(120, 200), connection(None, None, 0),
                                        connection(150, 260)])
    # 80 known, then the oldest on the device 120: what was between may be gone unseen. The
    # device without any activity, then one that still has a known one: no gap.
    assert kept.gaps() == [(80, 120)]
    assert journal.Journal().gaps() == []


def test_a_line_the_program_did_not_write_is_skipped_and_kept(tmp_path, backups):
    from openexpress import activity_journal as journal

    where = journal.path(UNIT_ID)
    where.parent.mkdir(parents=True)
    where.write_text('{"v": 99, "kind": "activity"}\nnot json')  # the last one cut short
    found = on_device(tmp_path / "device", {"a.fit": THREE["a.fit"]})
    journal.add(found.device, found.storage, backups, now=SEEN)
    text = where.read_text()
    assert text.startswith('{"v": 99, "kind": "activity"}\nnot json\n')
    kept = journal.read(where)
    assert kept.skipped == 2 and len(kept.activities) == 1 and len(kept.connections) == 1


def test_deleted_one_or_all(tmp_path, backups):
    from openexpress import activity_journal as journal

    found = on_device(tmp_path / "device", dict(THREE))
    journal.add(found.device, found.storage, backups, now=SEEN)
    journal.add(found.device, found.storage, backups, now=SEEN, test_drive=True)
    other = journal.path(987654321)
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps(connection(1, 2)) + "\n")
    assert len(journal.journals()) == 3
    assert journal.delete([journal.path(UNIT_ID)]) == 3
    assert not journal.path(UNIT_ID).exists() and journal.path(UNIT_ID, True).exists()
    assert journal.delete(journal.journals()) == 3
    assert journal.journals() == []
