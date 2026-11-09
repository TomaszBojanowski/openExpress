"""The device log (history.jsonl in the device's folder): one line for each connection of a
device, whoever opened it (the window, the command line, the background sync), at most once a
connection; the space each kind of file takes, the memory dumps and the newest crash of the
error logs; never a file name, the unit ID or a serial number. Read back line by line, a damaged
or foreign line skipped; old lines dropped. All devices, files and entries are made up."""

import json
import os
import stat
import types
from datetime import datetime, timedelta

import pytest
from conftest import DEVICE_XML
from fakes import window as stand_in

from openexpress import history, paths
from openexpress.device.storage import DeviceError, LocalStorage
from openexpress.device.xmldevice import parse

UNIT = 1234567890  # the Id of DEVICE_XML
SERIAL = "MADE-UP-SERIAL-7"
OTHER_UNIT = "3999999990"  # the unit ID in the made-up error log
BACKTRACE = "0badc0de" * 8
CAPACITY, FREE = 10_000_000, 4_000_000

# Kinds of file the made-up device declares besides those of DEVICE_XML (activities in
# Garmin/Activity, apps in GARMIN/APPS/MEDIA, an update file in Garmin): maps, music, courses,
# locations, an error report, satellite data, and a type of the whole main folder, which is
# never looked through.
TYPES = """
  <DataType><Name>NMaps</Name>
   <File><Location><Path>Garmin/Maps</Path><FileExtension>img</FileExtension></Location>
    <TransferDirection>InputOutput</TransferDirection></File></DataType>
  <DataType><Name>MEDIA_TRACK</Name>
   <File><Location><Path>Music</Path></Location>
    <TransferDirection>InputOutput</TransferDirection></File></DataType>
  <DataType><Name>FIT_TYPE_6</Name>
   <File><Location><Path>Garmin/Courses</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
  <DataType><Name>FIT_TYPE_8</Name>
   <File><Location><Path>Garmin/Locations</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
  <DataType><Name>ErrorShutdownReports</Name>
   <File><Location><Path>Garmin/Debug/Uploads/Er</Path><FileExtension>txt</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
  <DataType><Name>EphemerisMadeUp</Name>
   <File><Location><Path>Garmin/GPS</Path><FileExtension>bin</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
  <DataType><Name>FITBinary</Name>
   <File><Location><Path>Garmin</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>InputOutput</TransferDirection></File></DataType>
"""

TRAP = {"unit_id": OTHER_UNIT, "backtrace": BACKTRACE, "error_cause": "RTL Trap",
        "ms_since_epoch": "1792585446000",
        "proprietary_info": {"Time": "2026-10-20 22:24:06", "RTL Error Number": "0x003",
                             "programCounter": "0x10000abc", "pageStack": "2301"}}

FILES = {  # path: size, or the content
    "GARMIN/Activity/2026-10-01-10-00-00.fit": 1000,
    "GARMIN/Activity/2026-10-02-10-00-00.fit": 2000,
    "GARMIN/Maps/topo.img": 50_000,
    "GARMIN/Maps/readme.txt": 10,  # not a map: no kind
    "Music/Made-up Artist/song.mp3": 7000,
    "GARMIN/APPS/MEDIA/app.PRG": 4000,
    "GARMIN/Courses/loop.fit": 300,
    "GARMIN/Locations/Locations.fit": 200,
    "GARMIN/GUPDATE.GCD": 9000,
    "GARMIN/GPS/eph.bin": 800,
    "GARMIN/Debug/err_log.bak": json.dumps([TRAP]).encode(),
    "GARMIN/Debug/dump/RAM.zip": 300,
    "GARMIN/Debug/dump/RAM_bak1.zip": 200,
    "GARMIN/Debug/Uploads/Er/E1.txt": 50,
    "GARMIN/Monitor/M1.FIT": 100,  # no kind: part of System
    "GARMIN/Secret/Deep/hidden.FIT": 10,  # under the main folder: never looked through
    "GARMIN/NewFiles/queued.FIT": 70,  # what Garmin Connect queued: on its way, no kind
}


def size_of(value) -> int:
    return value if isinstance(value, int) else len(value)


LOGS = sum(size_of(v) for p, v in FILES.items() if p.startswith("GARMIN/Debug/"))
KINDS = {"maps": 50_000, "activities": 3000, "music": 7000, "apps": 4000, "courses": 500,
         "updates": 9800, "logs": LOGS}


class Device(LocalStorage):
    """A device at a place on USB, of a made-up capacity and free space; `lists` and `reads`
    count what was listed and read through the ordinary connection."""

    def __init__(self, root, address=5, drive=False):
        super().__init__(root)
        self.usb = types.SimpleNamespace(bus=1, address=address, serial=SERIAL)
        self.mass_storage = drive
        self.lists, self.reads = [], []

    def capacity(self):
        return CAPACITY

    def free_space(self):
        return FREE

    def _list(self, real_path):
        self.lists.append(real_path)
        return super()._list(real_path)

    def _read(self, real_path):
        self.reads.append(real_path)
        return super()._read(real_path)


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "device"
    (root / "GARMIN").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(
        DEVICE_XML.replace("</MassStorageMode>", TYPES + "</MassStorageMode>"))
    for path, value in FILES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(value if isinstance(value, bytes) else b"x" * value)
    return root


@pytest.fixture
def made_up(root):
    st = Device(root)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def device_of(st):
    return parse(st.read("GARMIN/GarminDevice.xml"))


def written(**changes) -> dict:
    """A line as the program writes it (made-up values), with `changes`."""
    line = {"v": 1, "time": "2026-10-20T12:00:00+02:00", "conn": "made-up-a", "via": "mtp",
            "software": "23.39", "capacity": CAPACITY, "free": FREE,
            "categories": {"maps": 1, "system": 2}, "dumps": {"count": 0, "bytes": 0},
            "crash": None}
    return {**line, **changes}


def write_lines(*lines) -> None:
    where = history.path(UNIT)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines))


# --- what a line holds ----------------------------------------------------------------------


def test_a_connection_writes_one_line_of_counts(made_up, clock):
    st, dev = made_up
    assert history.note_connection(dev, st)
    (line,) = history.read(UNIT).lines
    assert (line["v"], line["via"], line["software"]) == (1, "mtp", "23.39")
    assert (line["capacity"], line["free"]) == (CAPACITY, FREE)
    system = CAPACITY - FREE - sum(KINDS.values())
    assert line["categories"] == {**KINDS, "system": system}
    assert line["dumps"] == {"count": 2, "bytes": 500}
    assert line["crash"] == {"kind": "RTL Trap", "at": "2026-10-20 22:24:06", "error": "0x003",
                             "pc": "0x10000abc", "screen": "2301"}
    assert datetime.fromisoformat(line["time"]).astimezone().replace(tzinfo=None) == clock.current


def test_the_line_names_no_file_no_unit_id_and_no_serial_number(made_up, clock):
    st, dev = made_up
    history.note_connection(dev, st)
    where = history.path(UNIT)
    assert where.parent == paths.device_dir(UNIT)
    assert stat.S_IMODE(os.stat(where).st_mode) == 0o600
    text = where.read_text()
    found = [word for word in (str(UNIT), OTHER_UNIT, SERIAL, BACKTRACE[:16], "RAM", "err_log",
                               "2026-10-01-10", "topo", "song", "Made-up Artist", "GUPDATE",
                               "Locations.fit") if word in text]
    assert found == []


def test_the_main_folder_and_the_queue_are_not_looked_through(made_up, clock):
    st, dev = made_up
    history.note_connection(dev, st)
    looked = [p for p in st.lists if p.casefold().startswith(("garmin/secret", "garmin/newfiles",
                                                             "garmin/monitor"))]
    assert looked == []


def test_a_drive_gets_what_it_shows(root, clock):
    for path in ("GARMIN/Debug/err_log.bak", "GARMIN/Debug/dump/RAM.zip",
                 "GARMIN/Debug/dump/RAM_bak1.zip"):
        (root / path).unlink()
    st = Device(root, drive=True)
    assert history.note_connection(device_of(st), st)
    (line,) = history.read(UNIT).lines
    assert line["via"] == "drive" and line["crash"] is None
    assert line["dumps"] == {"count": 0, "bytes": 0}
    assert line["categories"]["activities"] == 3000


def test_a_folder_that_cannot_be_listed_leaves_its_kind_and_system_out(made_up, clock,
                                                                      monkeypatch):
    st, dev = made_up
    listing = st._list

    def failing(real_path):
        if real_path.casefold() == "garmin/maps":
            raise DeviceError("made-up failure")
        return listing(real_path)

    monkeypatch.setattr(st, "_list", failing)
    assert history.note_connection(dev, st)  # the opening goes on
    (line,) = history.read(UNIT).lines
    assert "maps" not in line["categories"] and "system" not in line["categories"]
    assert line["categories"]["activities"] == 3000


# --- once a connection, whoever opened the device -------------------------------------------


def test_once_a_connection(root, clock, monkeypatch):
    st = Device(root, address=5)
    assert history.note_connection(device_of(st), st)
    again = Device(root, address=5)  # another opening of the same connection
    assert not history.note_connection(device_of(again), again)
    monkeypatch.setattr(history, "_noted", set())  # another process: the file tells
    assert not history.note_connection(device_of(again), again)
    replugged = Device(root, address=6)
    assert history.note_connection(device_of(replugged), replugged)
    monkeypatch.setattr(history, "_boot", lambda: "made-up-other-boot")  # the computer restarted
    same_place = Device(root, address=6)
    assert history.note_connection(device_of(same_place), same_place)
    assert len(history.read(UNIT).lines) == 3


def test_the_window_writes_it_with_what_it_read_of_the_errors(made_up, clock, in_the_window):
    from openexpress.gui.device_page import read_diagnostics

    st, dev = made_up
    session = types.SimpleNamespace(device=dev, storage=st, diagnostics=None,
                                    diagnostics_reading=None)
    background = []
    in_the_window("device_error_row", lambda win, group=None: None)
    win = stand_in(session=session, in_background=lambda fn, done, failed=None:
                   background.append((fn, done)))
    read_diagnostics(win)
    (work, done), = background
    done(work())
    (line,) = history.read(UNIT).lines
    assert line["crash"]["error"] == "0x003"
    read_logs = [p for p in st.reads if p.endswith("err_log.bak")]
    assert len(read_logs) == 1  # read once, for the page and the line


def test_the_command_line_writes_it_at_the_first_command_of_a_connection(made_up, clock,
                                                                        monkeypatch, capsys):
    from openexpress import cli, service

    st, _dev = made_up
    monkeypatch.setattr(service, "open_device_or_drive", lambda **_kw: st)
    assert cli.main(["diagnostics"]) == 0
    assert cli.main(["diagnostics"]) == 0
    assert len(history.read(UNIT).lines) == 1


def test_a_folder_given_by_hand_writes_nothing(root, clock, capsys):
    from openexpress import cli

    assert cli.main(["--path", str(root), "diagnostics"]) == 0
    assert not history.path(UNIT).exists()


def test_the_background_sync_writes_it(made_up, clock, monkeypatch):
    from openexpress import watch

    st, dev = made_up
    w = watch.Watcher()
    session = types.SimpleNamespace(device=dev, storage=st, client=None, mass_storage=False,
                                    logged_in=False, first_sync=False)
    monkeypatch.setattr(watch.service, "open_session", lambda: session)
    monkeypatch.setattr(watch.GLib, "idle_add", lambda fn, *args: None)
    w._sync()  # not signed in: no sync, but the connection is noted
    assert len(history.read(UNIT).lines) == 1


def test_a_device_openexpress_cannot_use_writes_nothing(clock, monkeypatch, capsys):
    from openexpress import cli
    from openexpress.device import gio_storage

    def unsupported(*_args, **_kw):
        raise gio_storage.UnsupportedDevice("A made-up device that openExpress cannot use.")

    monkeypatch.setattr(gio_storage, "open_device_storage", unsupported)
    monkeypatch.setattr(gio_storage, "usb_state", lambda: "made-up: a device of another kind")
    assert cli.main(["diagnostics"]) != 0
    assert list(paths.data_dir().rglob(history.NAME)) == []


def test_a_test_drive_keeps_its_own_log_not_the_watchs(root, clock):
    """A drive taken for a Garmin drive for testing carries the GarminDevice.xml of a real
    device: its connections go to its own records."""
    st = Device(root, drive=True)
    st.test_drive = True
    assert history.note_connection(device_of(st), st)
    assert not history.path(UNIT).exists()
    assert history.path(UNIT, test_drive=True).parent == paths.device_dir(UNIT) / "test-drive"
    assert len(history.read(UNIT, test_drive=True).lines) == 1


def test_forget_device_deletes_it(made_up, clock):
    from openexpress import forget, service

    st, _dev = made_up
    dev = service.read_device(st, found_now=True)
    history.note_connection(dev, st)
    assert forget.forget_device(UNIT)
    assert not history.path(UNIT).exists()


# --- reading it back ------------------------------------------------------------------------


def test_damaged_and_foreign_lines_are_skipped(made_up, clock):
    st, dev = made_up
    good = written()
    write_lines("not JSON", "[1, 2]", written(v=2), {"v": 1}, written(free="a lot"),
                written(via="bluetooth"), written(categories={"maps": -1}), good)
    with history.path(UNIT).open("ab") as f:
        f.write(b"\xff\x00 not text\n")
    kept = history.read(UNIT)
    assert kept.lines == [good] and kept.skipped == 8
    assert history.note_connection(dev, st)
    assert len(history.read(UNIT).lines) == 2


def test_a_line_cut_short_does_not_swallow_the_next(made_up, clock):
    st, dev = made_up
    where = history.path(UNIT)
    where.parent.mkdir(parents=True)
    where.write_text(json.dumps(written()) + "\n" + '{"v": 1, "ti')  # a write cut short
    assert history.note_connection(dev, st)
    kept = history.read(UNIT)
    assert len(kept.lines) == 2 and kept.skipped == 1


def test_an_unknown_kind_of_file_is_dropped_not_the_line(clock):
    write_lines(written(categories={"maps": 1, "made-up kind": 5}))
    (line,) = history.read(UNIT).lines
    assert line["categories"] == {"maps": 1}


def test_lines_older_than_two_years_are_dropped(made_up, clock):
    st, dev = made_up
    old = (clock.current - timedelta(days=731)).astimezone().isoformat(timespec="seconds")
    recent = (clock.current - timedelta(days=1)).astimezone().isoformat(timespec="seconds")
    write_lines(written(time=old, conn="made-up-old"), written(time=recent, conn="made-up-b"))
    assert history.note_connection(dev, st)
    conns = [line["conn"] for line in history.read(UNIT).lines]
    assert len(conns) == 2 and conns[0] == "made-up-b"


def test_the_log_keeps_its_newest_lines(made_up, clock, monkeypatch):
    st, dev = made_up
    monkeypatch.setattr(history, "MAX_LINES", 3)
    write_lines(*(written(conn=f"made-up-{n}",
                          time=(clock.current - timedelta(hours=10 - n)).astimezone()
                          .isoformat(timespec="seconds")) for n in range(5)))
    assert history.note_connection(dev, st)
    conns = [line["conn"] for line in history.read(UNIT).lines]
    assert conns[:2] == ["made-up-3", "made-up-4"] and len(conns) == 3
    assert stat.S_IMODE(os.stat(history.path(UNIT)).st_mode) == 0o600


# --- the command line -----------------------------------------------------------------------


def test_diagnostics_shows_the_device_log(made_up, clock, monkeypatch, capsys):
    from openexpress import cli, service
    from openexpress.i18n import human_size, plain_spaces

    def size(n):
        return plain_spaces(human_size(n))

    st, _dev = made_up
    monkeypatch.setattr(service, "open_device_or_drive", lambda **_kw: st)
    assert cli.main(["diagnostics"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Device log on this computer: 1 connection, the first on 2026-10-21" in out
    line = (f"  2026-10-21 12:00 MTP: software 23.39, {size(FREE)} of "
            f"{size(CAPACITY)} free, 2 memory dumps ({size(500)}), last crash: "
            "RTL Trap at 2026-10-20 22:24:06, error 0x003, PC 0x10000abc, screen 2301")
    assert line in out
    storage = next(x for x in out if x.startswith("Storage at the last connection: "))
    assert storage.startswith(f"Storage at the last connection: maps {size(50_000)}, "
                              f"activities {size(3000)}, music and audio ")
    assert out[-1].startswith("Dry run: nothing was copied.")


def test_diagnostics_says_a_damaged_line_was_skipped(made_up, clock, monkeypatch, capsys):
    from openexpress import cli, service

    st, _dev = made_up
    write_lines("not JSON")
    monkeypatch.setattr(service, "open_device_or_drive", lambda **_kw: st)
    assert cli.main(["diagnostics"]) == 0
    assert "  1 damaged line skipped" in capsys.readouterr().out.splitlines()


def test_a_failure_not_foreseen_is_no_failed_opening(made_up, clock, monkeypatch):
    st, dev = made_up

    def broken(*_args):
        raise ValueError("made-up failure")

    monkeypatch.setattr(history, "count", broken)
    assert not history.note_connection(dev, st)
    assert not history.path(UNIT).exists()


# --- not known is not zero ------------------------------------------------------------------


def test_what_could_not_be_read_is_left_out_not_zero(made_up, clock, monkeypatch):
    """The error log not read, the folder of the dumps not listed and no capacity: the line
    has no crash, no dumps and no capacity (not known), not "none" or 0."""
    st, dev = made_up
    reading, listing = st._read, st._list

    def failing_read(real_path):
        if real_path.endswith("err_log.bak"):
            raise DeviceError("made-up failure")
        return reading(real_path)

    def failing_list(real_path):
        if real_path.casefold() == "garmin/debug/dump":
            raise DeviceError("made-up failure")
        return listing(real_path)

    monkeypatch.setattr(st, "_read", failing_read)
    monkeypatch.setattr(st, "_list", failing_list)
    monkeypatch.setattr(st, "capacity", lambda: None)
    assert history.note_connection(dev, st)
    (line,) = history.read(UNIT).lines
    assert not {"crash", "dumps", "capacity"} & line.keys()
    assert line["free"] == FREE


def test_the_window_passes_on_what_it_could_not_read(made_up, clock, in_the_window,
                                                     monkeypatch):
    from openexpress.gui.device_page import read_diagnostics

    st, dev = made_up
    reading = st._read

    def failing_read(real_path):
        if real_path.endswith("err_log.bak"):
            raise DeviceError("made-up failure")
        return reading(real_path)

    monkeypatch.setattr(st, "_read", failing_read)
    session = types.SimpleNamespace(device=dev, storage=st, diagnostics=None,
                                    diagnostics_reading=None)
    background = []
    in_the_window("device_error_row", lambda win, group=None: None)
    win = stand_in(session=session, in_background=lambda fn, done, failed=None:
                   background.append((fn, done)))
    read_diagnostics(win)
    (work, done), = background
    done(work())
    (line,) = history.read(UNIT).lines
    assert "crash" not in line and line["dumps"] == {"count": 2, "bytes": 500}


def test_a_line_without_what_was_not_known_is_read_back(clock):
    known = {k: v for k, v in written().items() if k not in ("crash", "dumps", "free")}
    write_lines(known, written(dumps={"count": "two", "bytes": 1}))
    kept = history.read(UNIT)
    assert kept.lines == [known] and kept.skipped == 1


def test_diagnostics_tells_not_known_from_none(clock):
    from openexpress import cli
    from openexpress.i18n import plain_spaces

    write_lines({k: v for k, v in written(conn="made-up-a").items()
                 if k not in ("crash", "dumps", "free")},
                written(conn="made-up-b", time="2026-10-20T13:00:00+02:00"))
    out = [plain_spaces(line) for line in cli.history_lines(UNIT)]
    newest, oldest = out[1], out[2]
    assert newest.endswith(", no memory dumps, no crash in the error logs")
    assert oldest.endswith(", free space not known, memory dumps not known, crashes not known")


def test_the_memory_dumps_beside_the_error_logs_are_in_the_line(root, clock):
    (root / "GARMIN" / "Debug" / "ramdump1.log").write_bytes(b"r" * 700)
    (root / "GARMIN" / "Debug" / "ramdump_err1.txt").write_text("what failed")
    st = Device(root)
    assert history.note_connection(device_of(st), st)
    (line,) = history.read(UNIT).lines
    assert line["dumps"] == {"count": 3, "bytes": 1200}
