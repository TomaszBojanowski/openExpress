"""Tests the device's error logs, read at each opening (an "RTL Trap" of the last 30 days gets a
row on the device page), and the copy of its diagnostic files: off by default, made once per
connection, never sent; the hidden crash logs and memory dumps only on request. All entries,
files and devices are made up."""

import hashlib
import json
import os
import stat
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import DEVICE_XML
from fakes import descendants, device_session
from fakes import window as stand_in

from openexpress import diagnostics, forget, paths, status
from openexpress.device import ptp
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.settings import Settings

UNIT = 1234567890  # the Id of DEVICE_XML
MADE_UP_UNIT = "3999999990"  # the unit ID in the made-up error logs
BACKTRACE = "0badc0de" * 48
CRASH_AT = "2026-10-20 22:24:06"  # a day before the made-up clock's noon

TYPES = """
  <DataType><Name>ErrorShutdownReports</Name>
   <File><Location><Path>GARMIN/Reports/Er</Path><BaseName>err_log</BaseName></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
  <DataType><Name>IQErrorReports</Name>
   <File><Location><Path>GARMIN/APPS/LOGS</Path></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
"""


def entry(kind, time, **details):
    """An entry as a fēnix 8 logs it (the shape seen; every value made up)."""
    moment = datetime.strptime(time, "%Y-%m-%d %H:%M:%S")
    return {"unit_id": MADE_UP_UNIT, "product_name": "Test watch",
            "software_part_number": "006-B0000-00",
            "ms_since_epoch": str(int((moment - datetime(1970, 1, 1)).total_seconds() * 1000)),
            "system_version": "23.39", "build_type": "Production", "backtrace": BACKTRACE,
            "error_cause": kind,
            "proprietary_info": {"Commit ID": "0" * 40, "Time": time, "uptime": "1", **details}}


TRAP = entry("RTL Trap", CRASH_AT, **{"RTL Error Number": "0x003",
                                      "programCounter": "0x10000abc", "pageStack": "2301",
                                      "pageStackDepth": "1", "stackPointer": "0x30000000",
                                      "rtl_reg": "1" * 36})
POWERDOWN = entry("Abnormal Powerdown", "2026-10-21 08:00:00", reset_info="RST:0x31,PMC:0x0000")


def err_log(*entries) -> bytes:
    return json.dumps(list(entries), indent=1).encode()


class Device(LocalStorage):
    """A device over MTP at a place on USB; `fake` answers a session of our own; `reads`
    counts the files read and `lists` the folders listed through the ordinary connection."""

    def __init__(self, root, fake=None, address=5):
        super().__init__(root)
        self.device_uri = "mtp://made-up/"
        self.usb = types.SimpleNamespace(bus=1, address=address)
        self.fake = fake
        self.sessions = 0
        self.reads = []
        self.lists = []
        self.infos = []
        self.blocked = False

    def _read(self, real_path):
        self.reads.append(real_path)
        return super()._read(real_path)

    def _list(self, real_path):
        self.lists.append(real_path)
        return super()._list(real_path)

    def info(self, real_path):
        self.infos.append(real_path)
        return super().info(real_path)

    def usb_blocked(self):
        return self.blocked

    def exclusive_ptp(self):
        if self.fake is None:
            return None
        return self._session()

    def _session(self):
        import contextlib

        @contextlib.contextmanager
        def opened():
            self.sessions += 1
            self.fake.reconnect()
            with ptp.Session(self.fake) as session:
                yield session

        return opened()


@pytest.fixture
def device(tmp_path):
    """A made-up watch: its error logs, other diagnostic files, health metrics, settings, memory
    dumps, the folders of two diagnostic types and no event logs it shows."""
    root = tmp_path / "device"
    debug = root / "GARMIN" / "Debug"
    for folder in ("MetricsBackup", "Backup", "dump", "Uploads/ULF"):
        (debug / folder).mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(
        DEVICE_XML.replace("</MassStorageMode>", TYPES + "</MassStorageMode>"))
    (debug / "err_log.bak").write_bytes(err_log(TRAP))
    (debug / "err_log.txt").write_bytes(b"")
    (debug / "SNS_ERR.TXT").write_text("sensor hub history")
    (debug / "MetricsBackup" / "M1.FIT").write_bytes(b"health metrics")
    (debug / "Backup" / "P1.fit").write_bytes(b"profile settings")
    (debug / "dump" / "RAM.zip").write_bytes(b"d" * 300)
    (debug / "dump" / "RAM_bak1.zip").write_bytes(b"e" * 200)
    os.utime(debug / "dump" / "RAM_bak1.zip", (1_700_000_000, 1_700_000_000))
    (root / "GARMIN" / "Reports" / "Error").mkdir(parents=True)
    (root / "GARMIN" / "Reports" / "Error" / "E1.txt").write_text("error report")
    (root / "GARMIN" / "APPS" / "LOGS").mkdir(parents=True)
    (root / "GARMIN" / "APPS" / "LOGS" / "CIQ_LOG.BAK").write_text("app error")
    storage = Device(root)
    return storage, parse(storage.read("GARMIN/GarminDevice.xml"))


def copy_on(on=True):
    settings = Settings.load()
    settings.diagnostics_copy = on
    settings.save()


def newest(kind="files"):
    snaps = sorted((diagnostics.folder(UNIT) / "snapshots").glob("*.json"))
    found = [json.loads(p.read_text()) for p in snaps]
    return [s for s in found if s["kind"] == kind][-1]


# --- the error logs -------------------------------------------------------------------------


def test_an_entry_keeps_neither_the_unit_id_nor_the_backtrace():
    (e,) = diagnostics.parse_err_log(err_log(TRAP), "err_log.bak")
    assert e == diagnostics.ErrorEntry("RTL Trap", datetime(2026, 10, 20, 22, 24, 6), "0x003",
                                       "0x10000abc", "2301", "err_log.bak", "23.39")
    assert e.is_crash
    assert e.words() == "RTL Trap at 2026-10-20 22:24:06, error 0x003, PC 0x10000abc, screen 2301"
    for text in (repr(e), e.words()):
        assert MADE_UP_UNIT not in text and BACKTRACE[:16] not in text


def test_other_kinds_and_other_shapes():
    asserts = [entry("Assert", f"2026-10-20 00:16:{s:02}") for s in (50, 51, 52)]
    found = diagnostics.parse_err_log(err_log(POWERDOWN, *asserts))
    assert [e.kind for e in found] == ["Abnormal Powerdown", "Assert", "Assert", "Assert"]
    assert not any(e.is_crash for e in found) and found[0].error is None
    # One object alone, a damaged or empty file, other values: read leniently.
    assert [e.kind for e in diagnostics.parse_err_log(json.dumps(TRAP).encode())] == ["RTL Trap"]
    for data in (b"", b"[{", b"[1, \"x\", {}]", b"{\"error_cause\": 7}", b"\xff\xfe"):
        assert all(e.kind != "RTL Trap" for e in diagnostics.parse_err_log(data))


def test_the_time_is_the_device_local_time():
    """"Time" of the details; without it ms_since_epoch, which tells the same wall clock."""
    no_time = json.loads(json.dumps(TRAP))
    del no_time["proprietary_info"]["Time"]
    (e,) = diagnostics.parse_err_log(err_log(no_time))
    assert e.at == datetime(2026, 10, 20, 22, 24, 6)
    no_time["ms_since_epoch"] = "soon"
    assert diagnostics.parse_err_log(err_log(no_time))[0].at is None


def test_only_an_rtl_trap_of_the_last_30_days_is_a_crash():
    now = datetime(2026, 10, 21, 12, 0)
    old = entry("RTL Trap", "2026-09-01 10:00:00")
    newer_assert = entry("Assert", "2026-10-21 11:00:00")
    for entries, crash in (([TRAP], True), ([old], False), ([newer_assert, TRAP], True),
                           ([POWERDOWN], False), ([newer_assert], False)):
        found = diagnostics.last_crash(diagnostics.parse_err_log(err_log(*entries)), now)
        assert (found is not None) is crash
        assert found is None or found.kind == "RTL Trap"


def test_the_logs_are_read_with_the_copy_off_and_nothing_is_copied(device, clock):
    st, dev = device
    found = diagnostics.at_open(dev, st)
    crash = diagnostics.last_crash(found.entries, datetime.now())
    assert crash is not None and crash.at == datetime(2026, 10, 20, 22, 24, 6)
    assert found.copied is None
    assert not diagnostics.folder(UNIT).exists()
    assert sorted(path for path, _size in found.dumps) == ["GARMIN/Debug/dump/RAM.zip",
                                                           "GARMIN/Debug/dump/RAM_bak1.zip"]


def test_the_log_line_names_the_error_not_the_device(device, clock, caplog):
    import logging

    st, dev = device
    with caplog.at_level(logging.INFO, logger="openexpress"):
        diagnostics.at_open(dev, st)
    lines = [r.getMessage() for r in caplog.records if r.name == "openexpress.diagnostics"]
    assert ("diagnostics: the device logged RTL Trap at 2026-10-20 22:24:06, error 0x003, "
            "PC 0x10000abc, screen 2301") in lines
    assert not any(MADE_UP_UNIT in line or str(UNIT) in line for line in lines)


# --- the copy -------------------------------------------------------------------------------


def test_the_copy_takes_what_the_device_shows_and_lists_the_rest(device, clock, monkeypatch):
    st, dev = device
    monkeypatch.setattr(diagnostics, "BIG", 2000)
    (st.root / "GARMIN" / "Debug" / "usage.log").write_bytes(b"u" * 2001)  # big: listed only
    copy_on()
    found = diagnostics.at_open(dev, st)
    assert found.copied.saved and found.copied.new == 5
    snap = newest()
    assert sorted(snap["files"]) == [
        "GARMIN/APPS/LOGS/CIQ_LOG.BAK", "GARMIN/Debug/SNS_ERR.TXT", "GARMIN/Debug/err_log.bak",
        "GARMIN/Debug/err_log.txt", "GARMIN/Reports/Error/E1.txt"]
    # Health metrics, settings, dumps and a big file: only their names, sizes and times.
    assert sorted(snap["listed"]) == [
        "GARMIN/Debug/Backup/P1.fit", "GARMIN/Debug/MetricsBackup/M1.FIT",
        "GARMIN/Debug/dump/RAM.zip", "GARMIN/Debug/dump/RAM_bak1.zip", "GARMIN/Debug/usage.log"]
    where = diagnostics.folder(UNIT)
    item = snap["files"]["GARMIN/Debug/err_log.bak"]
    assert (where / "objects" / item["sha256"]).read_bytes() == err_log(TRAP)
    assert where.parent == paths.device_dir(UNIT)
    for p in [where, *where.rglob("*")]:
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == (0o700 if p.is_dir() else 0o600), p
    assert not any(p.name in ("M1.FIT", "P1.fit") or "dump" in p.parts
                   for p in where.rglob("*") if p.is_file() and p.parent.name != "objects")
    stored = {p.read_bytes() for p in (where / "objects").iterdir()}
    assert b"health metrics" not in stored and b"profile settings" not in stored


def test_a_second_copy_keeps_only_what_changed(device, clock):
    st, dev = device
    (st.root / "GARMIN" / "Debug" / "empty.log").write_bytes(b"")  # as err_log.txt
    first = diagnostics.copy_files(dev, st)
    # Unchanged means as in the copy before, not the same content as another file.
    assert (first.new, first.unchanged) == (6, 0)
    again = diagnostics.copy_files(dev, st)
    assert not again.saved and again.new == 0 and again.copies == 1
    (st.root / "GARMIN" / "Debug" / "err_log.txt").write_bytes(err_log(POWERDOWN))
    changed = diagnostics.copy_files(dev, st)
    assert changed.saved and changed.new == 1 and changed.copies == 2
    assert len(list((diagnostics.folder(UNIT) / "objects").iterdir())) == 6
    assert changed.unchanged == 5


def test_once_per_connection_and_never_a_session_of_our_own(device, clock):
    st, dev = device
    copy_on()

    def never():
        raise AssertionError("a session of our own at the connection")

    st.exclusive_ptp = never
    first = diagnostics.at_open(dev, st)
    read = len(st.reads)
    assert diagnostics.at_open(dev, st) is first and len(st.reads) == read
    st.usb.address = 6  # plugged in again: a new address on USB
    assert diagnostics.at_open(dev, st) is not first and len(st.reads) > read


def test_a_folder_that_cannot_be_listed_is_a_line_in_the_log(device, clock, caplog):
    import logging

    from openexpress.device.storage import DeviceError

    st, dev = device
    copy_on()

    def gone(path):
        raise DeviceError("gone")

    st.walk_files = gone
    with caplog.at_level(logging.INFO, logger="openexpress"):
        found = diagnostics.at_open(dev, st)
    assert any(e.is_crash for e in found.entries) and found.dumps == [] and found.copied is None
    messages = [r.getMessage() for r in caplog.records]
    assert "diagnostics: the folders not listed (DeviceError)" in messages
    assert "diagnostics: the copy failed (DeviceError: gone)" in messages


def test_rotation_keeps_30_copies_and_50_mb_and_never_the_newest(device, clock, monkeypatch):
    st, dev = device
    log = st.root / "GARMIN" / "Debug" / "err_log.txt"
    for n in range(4):
        log.write_bytes(b"x" * (10 + n))
        clock.current += timedelta(minutes=1)
        diagnostics.copy_files(dev, st)
    where = diagnostics.folder(UNIT)
    assert len(list((where / "snapshots").iterdir())) == 4
    copies, _size = diagnostics.rotate(where, copies=2)
    assert copies == 2
    # The content only the removed copies had is gone too.
    assert len(list((where / "objects").iterdir())) == 6
    # Over the limit even alone: the newest stays.
    copies, size = diagnostics.rotate(where, limit=1)
    assert copies == 1 and size > 1
    assert json.loads(next((where / "snapshots").iterdir()).read_text())["files"][
        "GARMIN/Debug/err_log.txt"]["size"] == 13


def test_forgetting_the_device_removes_its_log_and_every_copy(device, clock):
    """Forget Device leaves no history of crashes and no copy behind: the device log, the
    diagnostic copies and the copies of removed dumps, a test drive's too."""
    from openexpress import freeing, history, known_devices

    st, dev = device
    known_devices.seen(UNIT, "Test watch", "006-B0000-00", "23.39", "mtp")
    history.note_connection(dev, st)
    diagnostics.copy_files(dev, st)
    removal = freeing.removed_folder(UNIT) / "2026-10-21 12.00.00"
    removal.mkdir(parents=True)
    (removal / "RAM_bak1.zip").write_bytes(b"e" * 200)
    test_drive = history.path(UNIT, test_drive=True)
    test_drive.parent.mkdir(parents=True)
    test_drive.write_text("")
    assert history.path(UNIT).exists() and diagnostics.copies_facts(UNIT)[0] == 2
    assert forget.forget_device(UNIT)
    assert not paths.device_dir(UNIT).exists()


def test_forgetting_the_device_removes_the_copies(device, clock):
    from openexpress import known_devices

    st, dev = device
    known_devices.seen(UNIT, "Test watch", "006-B0000-00", "23.39", "mtp")
    diagnostics.copy_files(dev, st)
    assert diagnostics.folder(UNIT).is_dir()
    assert forget.forget_device(UNIT)
    assert not diagnostics.folder(UNIT).exists()


# --- the memory dumps -----------------------------------------------------------------------


def test_the_dumps_are_copied_on_request_the_newest_first_within_the_limit(device, clock,
                                                                          monkeypatch):
    st, dev = device
    monkeypatch.setattr(diagnostics, "LIMIT", 400)
    done = diagnostics.copy_dumps(dev, st)
    # RAM.zip (300 B) is the newest; RAM_bak1.zip (200 B) does not fit beside it.
    assert done.new == 1 and done.not_copied == ["RAM_bak1.zip"]
    assert sorted(newest("dumps")["files"]) == ["GARMIN/Debug/dump/RAM.zip"]


# --- the hidden crash logs ------------------------------------------------------------------


@pytest.fixture
def hiding(device):
    """Like a fēnix 8: its boot log and event logs only a session of our own shows; beside
    them other hidden files, which are not copied."""
    from ptp_fake import FakeDevice

    st, dev = device
    fake = FakeDevice()
    fake.add("GARMIN/Debug/err_log.bak", err_log(TRAP))
    fake.add("GARMIN/Debug/recovery_mode_log.txt",
             b"[10/20/26 22:24:39 1234]: RTL Trap detected, errnum: 0x1a2b\n"
             b"[10/20/26 22:25:28 1283]: New boot cycle\n", hidden=True)
    fake.add("GARMIN/Debug/BDM.bin", b"power log", hidden=True)
    fake.add("GARMIN/Debug/inr.log", b"other log", hidden=True)
    fake.add("GARMIN/EVNTLOGS", hidden=True)
    fake.add("GARMIN/EVNTLOGS/EVT0001.TXT", b"event log", hidden=True)
    st.fake = fake
    return st, dev, fake


def test_the_crash_logs_are_the_boot_log_and_the_event_logs_only(hiding, clock):
    st, dev, fake = hiding
    copy_on(False)  # an explicit request: also with the copy off
    done = diagnostics.copy_crash_logs(dev, st)
    assert done.saved and st.sessions == 1
    files = newest("crash logs")["files"]
    assert set(files) == {"Garmin/Debug/recovery_mode_log.txt", "GARMIN/EVNTLOGS/EVT0001.TXT"}
    assert fake.sent(ptp.GET_OBJECT) == 2  # nothing else read
    assert fake.sent(ptp.SEND_OBJECT_INFO) == fake.sent(ptp.DELETE_OBJECT) == 0
    from openexpress import crashes

    # The boot log wrote at the next start the crash the error log holds: one crash.
    (crash,) = crashes.history(UNIT, diagnostics.read_errors(st))
    assert (crash.error, crash.errnum) == ("0x003", "0x1a2b")
    assert crash.sources == {"err_log.bak", crashes.BOOT_LOG}


def test_no_crash_logs_found_is_remembered(device, clock):
    from ptp_fake import FakeDevice

    st, dev = device
    st.fake = FakeDevice()
    st.fake.add("GARMIN/Debug/err_log.bak", err_log(TRAP))
    assert diagnostics.at_open(dev, st).hidden_logs
    done = diagnostics.copy_crash_logs(dev, st)
    assert not done.saved and diagnostics.hidden_logs_known_absent(UNIT)
    st.usb.address = 7
    assert not diagnostics.at_open(dev, st).hidden_logs


def test_the_rule_of_copy_crash_logs(device):
    st, _dev = device
    assert diagnostics.may_have_hidden_logs(st)
    for shown in ("GARMIN/EVNTLOGS", "GARMIN/Debug/EventLogs"):  # a Venu 2, an Edge 1050
        (st.root / shown).mkdir()
        assert not diagnostics.may_have_hidden_logs(st)
        (st.root / shown).rmdir()
    st.mass_storage = True
    assert not diagnostics.may_have_hidden_logs(st)


# What the page's summary and the command line say while the hidden crash logs may hold crashes
# not counted.
NOT_COPIED_PAGE = "Crash logs not copied yet may hold more crashes: copy them below"
NOT_COPIED_CLI = ("The hidden crash logs, not copied yet, may hold more crashes: to count them, "
                  "add --crash-logs --apply.")


def told_now(st, dev, now, monkeypatch, capsys) -> tuple[int, bool, bool]:
    """At a new connection: the crashes the page counts, and whether its summary and
    `openexpress diagnostics` say the hidden crash logs may hold more."""
    from gi.repository import Adw, Gtk

    from openexpress import cli
    from openexpress.gui import status_page

    st.usb.address += 1
    found = diagnostics.at_open(dev, st)
    facts = status.read(device_session(device=dev, storage=st, diagnostics=found))
    group = Adw.PreferencesGroup()
    status_page._summary(types.SimpleNamespace(group=lambda: group), facts, now)
    labels = [w.get_label() for w in descendants(group) if isinstance(w, Gtk.Label)]
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert cli.main(["diagnostics"]) == 0
    printed = capsys.readouterr().out.splitlines()
    return len(facts.crashes), NOT_COPIED_PAGE in labels, NOT_COPIED_CLI in printed


@pytest.mark.needs_display
def test_crashes_the_hidden_crash_logs_may_hold_are_said_until_they_are_copied(
        device, clock, monkeypatch, capsys):
    """A device that shows its event logs (a Venu 2, an Edge 1050) never says it. A watch that
    hides its boot log says it until the boot log is copied here: then the sentence goes and
    the boot log's older crash counts; an error log with a crash after the copy says it again."""
    from ptp_fake import FakeDevice

    st, dev = device
    shown = st.root / "GARMIN" / "Debug" / "EventLogs"
    shown.mkdir()
    assert told_now(st, dev, clock.current, monkeypatch, capsys) == (1, False, False)
    shown.rmdir()

    fake = FakeDevice()
    fake.add("GARMIN/Debug/err_log.bak", err_log(TRAP))
    fake.add("GARMIN/Debug/recovery_mode_log.txt",
             b"[10/12/26 08:00:00 99]: RTL Trap detected, errnum: 0x1a2b\n"
             b"[10/20/26 22:24:39 1234]: RTL Trap detected, errnum: 0x1a2b\n", hidden=True)
    st.fake = fake
    assert told_now(st, dev, clock.current, monkeypatch, capsys) == (1, True, True)

    assert diagnostics.copy_crash_logs(dev, st).saved
    assert told_now(st, dev, clock.current, monkeypatch, capsys) == (2, False, False)

    clock.current += timedelta(hours=3)
    later = entry("RTL Trap", "2026-10-21 14:00:00",
                  **{"RTL Error Number": "0x003", "programCounter": "0x10000abc"})
    (st.root / "GARMIN" / "Debug" / "err_log.bak").write_bytes(err_log(TRAP, later))
    assert told_now(st, dev, clock.current, monkeypatch, capsys) == (3, True, True)


def test_without_access_to_usb_it_says_why(device):
    st, dev = device
    st.blocked = True
    with pytest.raises(diagnostics.DiagnosticsError, match="Flatpak version has no access"):
        diagnostics.copy_crash_logs(dev, st)
    st.blocked = False
    with pytest.raises(diagnostics.DiagnosticsError, match="cannot open its own USB session"):
        diagnostics.copy_crash_logs(dev, st)


# --- the window -----------------------------------------------------------------------------


# The first opening of a watch can take 4.5-5 s, 3.7-4.4 s of it reading its diagnostics, and
# its tile would say nothing meanwhile. So the device page comes first, the diagnostics are
# read in the background after it, and a task that writes to the device waits for them, so
# that the copy is still of the device before any change.


def test_the_opening_does_not_wait_for_them(device, monkeypatch):
    """with_space (the task that opens the device) reads its free space only."""
    from openexpress.gui.detection import with_space

    st, dev = device
    monkeypatch.setattr(diagnostics, "at_open", lambda *a: pytest.fail("read in the opening"))
    session = device_session(device=dev, storage=st, diagnostics=None)
    assert with_space(session) is session and session.diagnostics is None


def _reading_window(session, in_the_window):
    """A window with `session` open, its work in the background kept to be run by the test,
    and the row of the device's errors noted when it is drawn."""
    background, drawn = [], []
    in_the_window("status_row",
                  lambda win, group=None: drawn.append(win.session.diagnostics))
    win = stand_in(session=session, in_background=lambda fn, done, failed=None:
                   background.append((fn, done)))
    return win, background, drawn


def test_they_are_read_in_the_background_once_the_page_is_shown(device, clock, in_the_window):
    from openexpress.gui.device_page import read_diagnostics

    st, dev = device
    copy_on()
    session = device_session(device=dev, storage=st)
    win, background, drawn = _reading_window(session, in_the_window)
    read_diagnostics(win)
    read_diagnostics(win)  # shown again: read once
    (work, done), = background
    reading = session.diagnostics_reading
    assert not reading.is_set() and session.diagnostics is None
    done(work())
    assert reading.is_set()
    assert (any(e.is_crash for e in session.diagnostics.entries)
            and session.diagnostics.copied.saved)
    assert drawn == [session.diagnostics]  # the row "Device Status" drawn with them
    assert [c.error for c in status.told(session).crashes] == ["0x003"]


def test_they_are_kept_but_not_drawn_for_a_device_no_longer_open(device, clock, in_the_window):
    from openexpress.gui.device_page import read_diagnostics

    st, dev = device
    session = device_session(device=dev, storage=st)
    win, background, drawn = _reading_window(session, in_the_window)
    read_diagnostics(win)
    win.session = None  # unplugged meanwhile
    (work, done), = background
    done(work())
    assert session.diagnostics is not None and drawn == []


def test_a_failure_still_lets_the_tasks_go_on(device, in_the_window):
    from openexpress.gui.device_page import read_diagnostics

    st, dev = device
    session = device_session(device=dev, storage=st)
    win, background, _drawn = _reading_window(session, in_the_window)
    in_the_window("diagnostics", types.SimpleNamespace(
        at_open=lambda device, storage: (_ for _ in ()).throw(OSError("gone"))))
    read_diagnostics(win)
    (work, _done), = background
    with pytest.raises(OSError):
        work()
    assert session.diagnostics_reading.is_set()


@pytest.mark.parametrize("writes", [True, False])
def test_a_task_that_writes_waits_for_them(monkeypatch, in_the_window, writes):
    """Only a task that writes to the device waits until they are read; one that only reads
    it does not."""
    import threading
    import time

    from gi.repository import GLib

    from openexpress.gui import app
    from openexpress.gui.tasks import writes_to_device

    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: fn(*args))
    in_the_window("refresh_space", lambda win, session: None)
    reading = threading.Event()
    session = device_session(diagnostics_reading=reading)
    order = []
    win = stand_in(busy=False, session=session, _task_lets_close=False,
                   set_busy=lambda busy, *a, **kw: None,
                   _run_pending_refresh=lambda: None)

    def work():
        order.append("work")

    app.MainWindow.run_task(win, "Saving…", writes_to_device(work) if writes else work,
                            lambda result: order.append("done"))
    time.sleep(0.3)  # long enough for a task that does not wait
    assert order == ([] if writes else ["work", "done"])
    reading.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(order) < 2:
        time.sleep(0.01)
    assert order == ["work", "done"]


def test_the_state_of_the_reading_is_worked_out_in_one_place():
    """Not started, reading, or read (also when the reading failed, its event set without
    diagnostics): the row, the page, the reading and the tasks that write ask this one."""
    import threading

    from openexpress.gui.window_tasks import diagnostics_state

    reading = threading.Event()
    assert diagnostics_state(device_session()) == "none"
    assert diagnostics_state(device_session(diagnostics_reading=reading)) == "reading"
    assert diagnostics_state(device_session(diagnostics=object())) == "read"
    reading.set()  # done and failed: nothing in the session
    assert diagnostics_state(device_session(diagnostics_reading=reading)) == "read"


def test_any_task_of_the_device_waits_for_its_count_after_a_write(monkeypatch, in_the_window):
    """The count after a write (in the background) ends before any task of the device, also
    one that only reads it, asks the device: one thing asks it at a time."""
    import threading
    import time

    from gi.repository import GLib

    from openexpress.gui import app

    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: fn(*args))
    counting = threading.Event()
    session = device_session(counting=counting)
    order = []
    win = stand_in(busy=False, session=session, _task_lets_close=False,
                   set_busy=lambda busy, *a, **kw: None, _run_pending_refresh=lambda: None)
    app.MainWindow.run_task(win, "Reading…", lambda: order.append("work"),
                            lambda result: order.append("done"))
    time.sleep(0.3)  # long enough for a task that does not wait
    assert order == []
    counting.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(order) < 2:
        time.sleep(0.01)
    assert order == ["work", "done"]


def test_files_unchanged_in_size_and_time_are_not_read_again(device, clock):
    """A file unchanged in size and time is taken from the copy before, also a small one: it
    is not read again at each opening."""
    st, dev = device
    first = diagnostics.copy_files(dev, st)
    st.reads.clear()
    again = diagnostics.copy_files(dev, st)
    assert st.reads == [] and again.unchanged == first.new and not again.saved
    changed = st.root / "GARMIN" / "Debug" / "SNS_ERR.TXT"
    changed.write_text("sensor hub history, longer")
    later = diagnostics.copy_files(dev, st)
    assert [Path(p).name for p in st.reads] == ["SNS_ERR.TXT"] and later.new == 1


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def test_a_file_of_another_time_with_the_same_bytes_is_unchanged(device, clock):
    """Its time changed: it is read again, and as its content is the one of the copy before,
    it counts as unchanged and nothing new is kept; the copy keeps its new time."""
    st, dev = device
    first = diagnostics.copy_files(dev, st)
    touched = st.root / "GARMIN" / "Debug" / "SNS_ERR.TXT"
    os.utime(touched, (1_700_000_000, 1_700_000_000))
    clock.current += timedelta(minutes=1)
    st.reads.clear()
    again = diagnostics.copy_files(dev, st)
    assert [Path(p).name for p in st.reads] == ["SNS_ERR.TXT"]
    assert (again.new, again.unchanged, again.new_bytes, again.saved) == (0, first.new, 0, True)
    assert newest()["files"]["GARMIN/Debug/SNS_ERR.TXT"] == {
        "sha256": _sha(b"sensor hub history"), "size": 18, "mtime": 1_700_000_000}


def test_dumps_copied_again_keep_their_content_once(device, clock):
    st, dev = device
    first = diagnostics.copy_dumps(dev, st)
    clock.current += timedelta(minutes=1)
    again = diagnostics.copy_dumps(dev, st)
    assert (first.new, first.unchanged, first.new_bytes) == (2, 0, 500)
    assert (again.new, again.unchanged, again.new_bytes) == (0, 2, 0)
    assert newest("dumps")["files"]["GARMIN/Debug/dump/RAM_bak1.zip"] == {
        "sha256": _sha(b"e" * 200), "size": 200, "mtime": 1_700_000_000}


def test_crash_logs_copied_again_keep_their_content_once(hiding, clock):
    st, dev, _fake = hiding
    first = diagnostics.copy_crash_logs(dev, st)
    clock.current += timedelta(minutes=1)
    again = diagnostics.copy_crash_logs(dev, st)
    # The boot log (101 bytes) and the event log (9).
    assert (first.new, first.unchanged, first.new_bytes) == (2, 0, 110)
    assert (again.new, again.unchanged, again.new_bytes) == (0, 2, 0)
    assert newest("crash logs")["files"]["GARMIN/EVNTLOGS/EVT0001.TXT"] == {
        "sha256": _sha(b"event log"), "size": 9, "mtime": None}


def _window(found, reading=None):
    from fakes import RunNow

    toasts = []
    return stand_in(
        session=device_session(device=None, storage=None, diagnostics=found,
                               diagnostics_reading=reading),
        _button=lambda label, cb, style=None, with_button=False: _labelled(label),
        run_task=RunNow(), toast=toasts.append, toasts=toasts, progress=lambda message: None)


def _labelled(label):
    from gi.repository import Gtk

    return Gtk.Button(label=label)


def _found(**kw):
    (crash,) = diagnostics.parse_err_log(err_log(TRAP))
    values = {"entries": [crash], "dumps": [("GARMIN/Debug/dump/RAM.zip", 8_090_553)],
              "hidden_logs": True}
    return diagnostics.AtOpen(**{**values, **kw})


def _row(win):
    row = win.status_row
    return None if row is None else (row.get_title(), row.get_subtitle())


@pytest.mark.needs_display
def test_the_row_of_the_device_page_says_the_summary(clock):
    from gi.repository import Adw

    from openexpress.gui.device_page import status_row

    win = _window(_found())
    status_row(win, Adw.PreferencesGroup())
    # A sentence under the row's title, not the title of the page's summary.
    assert _row(win) == ("Device Status", "1 crash in the last 30 days")


@pytest.mark.needs_display
def test_the_row_before_the_reading_and_after_it_failed(clock):
    import threading

    from gi.repository import Adw

    from openexpress.gui.device_page import status_row

    win = _window(_found(entries=[]))
    status_row(win, Adw.PreferencesGroup())
    assert _row(win) == ("Device Status", "No problems found")
    win = _window(None, reading=threading.Event())  # being read
    status_row(win, Adw.PreferencesGroup())
    assert _row(win) == ("Device Status", "Reading…")
    win.session.diagnostics_reading.set()  # done, and failed: as the page tells it
    status_row(win)
    assert _row(win) == ("Device Status", "Error logs not read")


@pytest.mark.needs_display
def test_no_crash_logs_found_takes_the_button_away(monkeypatch):
    from gi.repository import Adw

    from openexpress.gui.device_page import status_row
    from openexpress.gui.status_page import copy_crash_logs

    found = _found()
    win = _window(found)
    group = Adw.PreferencesGroup()
    status_row(win, group)
    monkeypatch.setattr(diagnostics, "copy_crash_logs",
                        lambda device, storage, progress=None: diagnostics.Copied())
    copy_crash_logs(win)
    assert win.toasts == ["The device has no hidden crash logs"]
    assert not found.hidden_logs  # the page Device Status offers no Copy Crash Logs
    assert win.status_row is not None  # drawn again
    monkeypatch.setattr(diagnostics, "copy_crash_logs",
                        lambda device, storage, progress=None: diagnostics.Copied(
                            new=2, saved=True))
    win.status_row = None
    copy_crash_logs(win)
    assert win.toasts[-1] == "Crash logs copied to this computer"
    # The row drawn again (its crashes counted anew with the copies kept now: status.told).
    assert win.status_row is not None


@pytest.mark.needs_display
def test_copy_memory_dumps_says_what_did_not_fit(monkeypatch):
    from openexpress.gui.status_page import copy_dumps

    win = _window(_found())
    monkeypatch.setattr(diagnostics, "copy_dumps",
                        lambda device, storage, progress=None: diagnostics.Copied(
                            new=1, unchanged=1, saved=True, not_copied=["RAM_bak2.zip"]))
    copy_dumps(win)
    assert win.toasts == [("2 memory dumps copied to this computer\u00a0· 1 file skipped: "
                           "did not fit in 50.0\u00a0MB")]


@pytest.mark.needs_display
def test_the_preferences_switch_turns_the_copy_on_and_off():
    from gi.repository import Adw

    from openexpress.gui.preferences import diagnostics_group

    win = stand_in(error=lambda title, text: pytest.fail(text))
    group = diagnostics_group(win)
    switch = next(w for w in descendants(group) if isinstance(w, Adw.SwitchRow))
    assert switch.get_title() == "Keep Diagnostic Files"
    assert not switch.get_active() and not diagnostics.copy_on()
    switch.set_active(True)
    assert diagnostics.copy_on() and Settings.load().diagnostics_copy
    switch.set_active(False)
    assert not diagnostics.copy_on()


# --- the command line -----------------------------------------------------------------------


def test_cli_diagnostics_dry_run_copies_nothing(device, clock, monkeypatch, capsys):
    from openexpress import cli

    st, dev = device
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert cli.main(["diagnostics"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Copy of the device's diagnostic files at each connection: off"
    assert ("  RTL Trap at 2026-10-20 22:24:06, error 0x003, PC 0x10000abc, screen 2301 "
            "(err_log.bak)") in out
    assert "Memory dumps on the device: 2, 500 B (encrypted; copied only with --dumps)" in out
    assert "Copies on this computer: No copy yet" in out
    assert out[-1] == "Dry run: nothing was copied. Add --apply to copy the files the device shows now."
    assert not diagnostics.folder(UNIT).exists() and st.sessions == 0
    assert not any(MADE_UP_UNIT in line or str(UNIT) in line or BACKTRACE[:16] in line
                   for line in out)


def test_cli_diagnostics_shows_the_assert_entries_as_a_count(device, clock, monkeypatch,
                                                              capsys):
    """An Edge writes "Assert" entries at an ordinary start, many at once: one line with their
    number, each of them only with -v."""
    from openexpress import cli

    st, dev = device
    asserts = [entry("Assert", f"2026-10-21 08:0{n}:00") for n in range(3)]
    (st.root / "GARMIN" / "Debug" / "err_log.bak").write_bytes(err_log(TRAP, *asserts))
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert cli.main(["diagnostics"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "  3 Assert entries, not shown (all with -v)" in out
    assert not any(line.startswith("  Assert at") for line in out)
    assert any(line.startswith("  RTL Trap at") for line in out)
    assert cli.main(["-v", "diagnostics"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("  Assert at") for line in out) == 3
    assert not any("not shown" in line for line in out)


def test_cli_diagnostics_apply(hiding, clock, monkeypatch, capsys):
    from openexpress import cli

    st, dev, _fake = hiding
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert cli.main(["diagnostics", "--apply", "--crash-logs", "--dumps"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert any(line.startswith("Copied: 5 new files") for line in out)
    assert "Memory dumps copied: 2 new (500 B)." in out
    assert "Crash logs: 2 files read (2 new)." in out
    assert st.sessions == 1


def test_cli_setup_turns_the_copy_on(capsys):
    from openexpress import cli

    assert cli.main(["setup", "--diagnostics-copy", "on"]) == 0
    assert "Copy of the device's diagnostic files at each connection: on" in (
        capsys.readouterr().out.splitlines())
    assert diagnostics.copy_on()
    assert cli.main(["setup", "--diagnostics-copy", "off"]) == 0
    assert not diagnostics.copy_on()


def test_nothing_is_sent(device, clock):
    """The copy asks no server: the tests refuse every connection, and the module has no
    client."""
    st, dev = device
    copy_on()
    diagnostics.at_open(dev, st)
    import openexpress.diagnostics as module

    with open(module.__file__, encoding="utf-8") as f:
        source = f.read()
    assert "requests" not in source and "GarminClient" not in source


@pytest.mark.needs_display
def test_the_row_in_polish(clock):
    from gi.repository import Adw

    from openexpress import i18n
    from openexpress.gui.device_page import status_row

    i18n.setup("pl")
    try:
        win = _window(_found())
        status_row(win, Adw.PreferencesGroup())
        row = _row(win)
    finally:
        i18n.setup("en")
    assert row == ("Stan urządzenia", "1 awaria w\u00a0ostatnich 30 dniach")


# --- the places of the diagnostic files, kept for the next openings ---------------------------
# Over MTP each listing of a folder is a round trip: looking through the diagnostic folders takes
# 4-5 s on a Venu 2, even with no file to read. The first opening looks through them and keeps
# where it found the files and folders (in the device's folder); the next ones read those paths,
# listing only the folders of the error logs and of the dumps (LOGS_LISTED); a path gone, an
# error log or a dump written since, or other software on the device: looked through again, and
# the note made anew.

# What an opening at the places kept lists of the made-up watch: for a file written since.
LOGS_LISTED = ["GARMIN/Debug", "GARMIN/Debug/dump"]


def _connected_again(st):
    st.usb.address += 1  # plugged in again: a new connection (at_open is once per connection)
    st.lists.clear()
    st.reads.clear()
    st.infos.clear()


def _same(a, b):
    assert (a.entries, a.dumps, a.hidden_logs) == (b.entries, b.dumps, b.hidden_logs)


@pytest.mark.parametrize("copy", [True, False])
def test_a_second_opening_reads_the_places_kept_listing_only_the_logs_folders(device, clock, copy):
    st, dev = device
    copy_on(copy)
    first = diagnostics.at_open(dev, st)
    assert st.lists  # the first one looks through the folders
    assert diagnostics.places_path(UNIT).exists()
    _connected_again(st)
    again = diagnostics.at_open(dev, st)
    assert sorted(st.lists) == LOGS_LISTED
    _same(again, first)
    if copy:
        assert again.copied.unchanged == first.copied.new and not again.copied.saved
    # Only the error logs are read again, as at each opening.
    assert sorted(Path(p).name for p in st.reads) == ["err_log.bak", "err_log.txt"]


def test_an_error_log_and_a_dump_written_after_the_places_were_kept_are_found(tmp_path, clock):
    """A crash writes err_log.bak and a memory dump after the places were kept, the software
    the same: the next opening finds them, and keeps the places anew."""
    root = tmp_path / "device"
    debug = root / "GARMIN" / "Debug"
    debug.mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    st = Device(root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    first = diagnostics.at_open(dev, st)
    assert first.entries == [] and first.dumps == []
    (debug / "err_log.bak").write_bytes(err_log(TRAP))
    (debug / "dump").mkdir()
    (debug / "dump" / "RAM.zip").write_bytes(b"d" * 300)
    _connected_again(st)
    found = diagnostics.at_open(dev, st)
    assert any(e.is_crash for e in found.entries) and [size for _p, size in found.dumps] == [300]
    _connected_again(st)
    diagnostics.at_open(dev, st)
    assert sorted(st.lists) == LOGS_LISTED  # the new note holds


def test_the_memory_dumps_beside_the_error_logs_are_counted(device, clock):
    """An Edge 1050 wrote its memory dumps with older software in the folder of its error logs
    (ramdump1.log, with a ramdump_err1.txt that says what failed, a text): they are dumps too,
    and one written there after the places were kept is found."""
    st, dev = device
    debug = st.root / "GARMIN" / "Debug"
    (debug / "ramdump1.log").write_bytes(b"r" * 700)
    (debug / "ramdump_err1.txt").write_text("what failed")
    names = sorted(Path(p).name for p, _size in diagnostics.dumps_on_device(st))
    assert names == ["RAM.zip", "RAM_bak1.zip", "ramdump1.log"]
    assert sum(size for _p, size in diagnostics.at_open(dev, st).dumps) == 1200
    (debug / "ramdumpC2.log").write_bytes(b"c" * 50)
    _connected_again(st)
    assert sum(size for _p, size in diagnostics.at_open(dev, st).dumps) == 1250


def test_a_place_gone_is_looked_for_again(device, clock):
    st, dev = device
    copy_on()
    diagnostics.at_open(dev, st)
    (st.root / "GARMIN" / "Debug" / "SNS_ERR.TXT").unlink()
    _connected_again(st)
    diagnostics.at_open(dev, st)
    assert st.lists  # looked through again
    kept = diagnostics.places_path(UNIT).read_text()
    assert "SNS_ERR.TXT" not in kept
    _connected_again(st)
    diagnostics.at_open(dev, st)
    assert sorted(st.lists) == LOGS_LISTED  # the note made anew holds


def test_the_places_not_there_are_not_asked_about_again(device, clock):
    """A path looked for in vain (the event logs, err_log.txt…) is not asked about again: the
    next openings ask the device about the places found only (on a Venu 2, 4 of its 9
    questions are about paths it does not have)."""
    st, dev = device
    looked_for = {*diagnostics.ERR_LOGS, diagnostics.DEBUG, diagnostics.DUMPS,
                  *diagnostics.EVENT_LOGS}
    not_there = {path for path in looked_for if st.resolve(path) is None}
    assert "GARMIN/EVNTLOGS" in not_there
    diagnostics.at_open(dev, st)
    _connected_again(st)
    diagnostics.at_open(dev, st)
    assert st.infos and not set(st.infos) & not_there and sorted(st.lists) == LOGS_LISTED


def test_a_note_that_lists_the_places_not_there_still_holds(device, clock):
    """A note as older versions wrote it, with the paths looked for in vain ("absent"), holds:
    the folders are not looked through again."""
    st, dev = device
    diagnostics.at_open(dev, st)
    place = diagnostics.places_path(UNIT)
    note = json.loads(place.read_text())
    assert "absent" not in note
    place.write_text(json.dumps({**note, "absent": ["GARMIN/EVNTLOGS"]}))
    _connected_again(st)
    diagnostics.at_open(dev, st)
    assert sorted(st.lists) == LOGS_LISTED


def test_a_place_not_there_before_is_looked_for_with_other_software(device, clock):
    """Event logs the device did not show at first (a hidden folder, Copy Crash Logs offered)
    and shows now: not seen while the note holds; with other software on the device, looked
    through again, and the button is no longer offered."""
    st, dev = device
    first = diagnostics.at_open(dev, st)
    assert first.hidden_logs
    events = st.root / "GARMIN" / "EVNTLOGS"
    events.mkdir()
    (events / "E1.TXT").write_text("events")
    _connected_again(st)
    assert diagnostics.at_open(dev, st).hidden_logs and sorted(st.lists) == LOGS_LISTED
    newer = parse(st.read("GARMIN/GarminDevice.xml").replace(
        b"<SoftwareVersion>2339<", b"<SoftwareVersion>2340<"))
    _connected_again(st)
    again = diagnostics.at_open(newer, st)
    assert st.lists and not again.hidden_logs


def test_other_software_on_the_device_is_looked_for_again(device, clock):
    st, dev = device
    diagnostics.at_open(dev, st)
    newer = parse(st.read("GARMIN/GarminDevice.xml").replace(
        b"<SoftwareVersion>2339<", b"<SoftwareVersion>2340<"))
    assert newer.software_version_str != dev.software_version_str
    _connected_again(st)
    diagnostics.at_open(newer, st)
    assert st.lists


def test_the_copy_turned_on_looks_for_the_files_it_needs(device, clock):
    """Kept with the copy off, the note has no diagnostic files to copy: the first opening
    with the copy on looks through the folders for them."""
    st, dev = device
    copy_on(False)
    diagnostics.at_open(dev, st)
    copy_on()
    _connected_again(st)
    found = diagnostics.at_open(dev, st)
    assert st.lists and found.copied.saved
    _connected_again(st)
    diagnostics.at_open(dev, st)
    assert sorted(st.lists) == LOGS_LISTED


# --- the crashes, merged from every source --------------------------------------------------


def kept_copy(kind, files, when="2026-10-21 11.00.00"):
    """A copy kept on this computer as the program keeps it: its note and each content once."""
    where = diagnostics.folder(UNIT)
    (where / "objects").mkdir(parents=True, exist_ok=True)
    (where / "snapshots").mkdir(exist_ok=True)
    listed = {}
    for path, data in files.items():
        sha = hashlib.sha256(data).hexdigest()
        (where / "objects" / sha).write_bytes(data)
        listed[path] = {"sha256": sha, "size": len(data), "mtime": None}
    (where / "snapshots" / f"{when}.json").write_text(json.dumps(
        {"kind": kind, "at": when, "software_version": "23.39", "files": listed, "listed": {}}))


def boot_log(*stamps, errnum="0x1a2b") -> bytes:
    """The boot log as a fēnix 8 writes it (the shape seen; every value made up): a crash, then
    the next start."""
    return "".join(f"[{s} 1234]: RTL Trap detected, errnum: {errnum}\n"
                   f"[{s} 1300]: New boot cycle\n" for s in stamps).encode()


BOOT = "Garmin/Debug/recovery_mode_log.txt"
STAMPS = ("09/01/26 10:00:00", "09/05/26 11:00:00", "10/01/26 12:00:00", "10/01/26 12:30:00",
          "10/20/26 22:24:42")  # the last 36 s after the error log's crash (CRASH_AT)


def test_five_crashes_of_the_boot_log_and_one_of_the_error_log_at_once_are_five(device, clock):
    """The boot log has five crashes; the error log on the device holds the newest of them,
    written at the crash, 36 s before the boot log wrote it at the next start: five, not six."""
    from openexpress import crashes

    st, _dev = device
    kept_copy("crash logs", {BOOT: boot_log(*STAMPS)})
    found = crashes.history(UNIT, diagnostics.read_errors(st))
    assert len(found) == 5
    newest_one = found[0]
    assert newest_one.at == datetime(2026, 10, 20, 22, 24, 6)  # the error log's: the crash itself
    assert (newest_one.software, newest_one.error, newest_one.errnum) == ("23.39", "0x003",
                                                                          "0x1a2b")
    assert newest_one.sources == {"err_log.bak", crashes.BOOT_LOG}
    assert [c.software for c in found[1:]] == [None] * 4  # the boot log gives no version


def test_the_device_log_counts_its_crashes_once_connected_or_not(device, clock):
    """Like a fēnix 8 whose boot log was copied before its newest crash: that crash is in its
    error log and in the line the device log wrote at the connection. Five crashes, connected
    (the error log read) or not (the copies and the device log only): never four, never six."""
    from openexpress import crashes, history

    st, dev = device
    kept_copy("crash logs", {BOOT: boot_log(*STAMPS[:4])})
    history.note_connection(dev, st)
    away = crashes.history(UNIT)
    here = crashes.history(UNIT, diagnostics.read_errors(st))
    assert (len(away), len(here)) == (5, 5)
    assert away[0].sources == {crashes.DEVICE_LOG}
    assert here[0].sources == {"err_log.bak", crashes.DEVICE_LOG}
    assert (away[0].error, away[0].program_counter) == ("0x003", "0x10000abc")


def test_the_same_boot_log_in_several_copies_counts_once(device, clock):
    from openexpress import crashes

    kept_copy("crash logs", {BOOT: boot_log(*STAMPS[:2])}, "2026-10-01 11.00.00")
    kept_copy("crash logs", {BOOT: boot_log(*STAMPS[:3])}, "2026-10-05 11.00.00")
    assert len(crashes.history(UNIT, [])) == 3


def test_a_crash_kept_only_in_a_copy_is_found(device, clock):
    """The device keeps only its last entries: one gone from its error log is in a copy."""
    from openexpress import crashes

    st, _dev = device
    old = entry("RTL Trap", "2026-09-01 08:00:00", **{"RTL Error Number": "0x00f",
                                                      "programCounter": "0x10000def"})
    kept_copy("files", {"GARMIN/Debug/err_log.bak": err_log(old, TRAP)})
    found = crashes.history(UNIT, diagnostics.read_errors(st))
    assert [(c.error, c.sources) for c in found] == [
        ("0x003", {"err_log.bak", crashes.COPY}), ("0x00f", {crashes.COPY})]


def test_two_crashes_within_a_minute_with_other_errors_are_two(device, clock):
    from openexpress import crashes

    second = entry("RTL Trap", "2026-10-20 22:24:30", **{"RTL Error Number": "0x005",
                                                         "programCounter": "0x10000123"})
    st, _dev = device
    (st.root / "GARMIN" / "Debug" / "err_log.bak").write_bytes(err_log(TRAP, second))
    assert len(crashes.history(UNIT, diagnostics.read_errors(st))) == 2


def test_other_kinds_of_entry_are_no_crashes(device, clock):
    from openexpress import crashes

    st, _dev = device
    (st.root / "GARMIN" / "Debug" / "err_log.bak").write_bytes(
        err_log(POWERDOWN, entry("Assert", "2026-10-21 08:01:00")))
    assert crashes.history(UNIT, diagnostics.read_errors(st)) == []


def test_a_copy_that_cannot_be_read_is_passed_over(device, clock):
    from openexpress import crashes

    kept_copy("crash logs", {BOOT: boot_log(*STAMPS[:1])})
    where = diagnostics.folder(UNIT)
    (where / "snapshots" / "2026-10-22 11.00.00.json").write_text("not JSON")
    (where / "snapshots" / "2026-10-23 11.00.00.json").write_text(json.dumps(
        {"kind": "crash logs", "files": {BOOT: {"sha256": "../../../made-up"}}}))
    for sha in (where / "objects").iterdir():
        sha.write_bytes(b"\xff\x00 not a log")
    assert crashes.history(UNIT, []) == []


def test_the_same_error_is_told_by_the_boot_log_where_it_has_one(device, clock):
    """For "the same error as the crashes before" (the page): the boot log's error number where
    both crashes have one (the error log's numbers are of another kind), else the error log's
    error and program counter; a crash whose codes are of another kind than the other's is not
    the same error."""
    from openexpress import crashes

    st, _dev = device
    kept_copy("crash logs", {BOOT: boot_log(*STAMPS)})
    newest_one, *before = crashes.history(UNIT, diagnostics.read_errors(st))
    assert all(crashes.same_error(newest_one, other) for other in before)
    other_boot = crashes.Crash(datetime(2026, 9, 1), errnum="0x9999")
    assert not crashes.same_error(newest_one, other_boot)
    logged = crashes.Crash(datetime(2026, 9, 1), error="0x003", program_counter="0x10000abc")
    assert crashes.same_error(newest_one, logged)
    elsewhere = crashes.Crash(datetime(2026, 9, 1), error="0x003", program_counter="0x10000def")
    assert not crashes.same_error(newest_one, elsewhere)
    only_boot = crashes.Crash(datetime(2026, 9, 2), errnum="0x1a2b")
    assert not crashes.same_error(logged, only_boot)  # nothing to tell it by


def test_cli_diagnostics_lists_the_crashes_of_every_source(device, clock, monkeypatch, capsys):
    from openexpress import cli

    st, dev = device
    kept_copy("crash logs", {BOOT: boot_log(*STAMPS)})
    monkeypatch.setattr(cli, "open_device", lambda args: (st, dev))
    assert cli.main(["diagnostics"]) == 0
    out = capsys.readouterr().out.splitlines()
    start = out.index("Crashes that restarted the device:")
    # The command notes the connection with what it read: the connection log holds the crash
    # too.
    assert out[start + 1] == ("  2026-10-20 22:24: RTL Trap, software 23.39, error 0x003, PC "
                              "0x10000abc, screen 2301, boot log error 0x1a2b (err_log.bak, "
                              "boot log, connection log on this computer)")
    assert out[start + 5] == "  2026-09-01 10:00: RTL Trap, boot log error 0x1a2b (boot log)"
    assert not any("copied boot log" in line for line in out)


# --- the report ------------------------------------------------------------------------------


def test_the_report_tells_the_crashes_and_the_dumps(device, clock):
    from openexpress import report

    st, dev = device
    kept_copy("crash logs", {BOOT: boot_log(*STAMPS)})
    text = report.build(dev, st)
    section = text[text.index("### Diagnostics"):].split("\n\n")[0].splitlines()
    assert section[1] == "- Crashes that restarted the device: 5"
    # Both codes of a crash both logs hold: the same boot log error as the crashes before it.
    assert section[2] == "  - 2026-10-20 22:24: software 23.39, error 0x003, boot log error 0x1a2b"
    assert section[3] == "  - 2026-10-01 12:30: software unknown, boot log error 0x1a2b"
    assert "- Memory dumps: 2, 500 B" in section
    assert "- Copy of the diagnostic files: off" in section
    found = [word for word in (str(UNIT), MADE_UP_UNIT, BACKTRACE[:16], "0x10000abc", "2301")
             if word in text]
    assert found == []  # no unit ID, backtrace, program counter or screen


def test_the_report_in_the_window_asks_nothing_of_the_device(device, clock):
    """The window builds the report from what it read at the opening (session.diagnostics)
    and the copies on this computer; nothing is asked of the device."""
    from openexpress import report

    st, dev = device
    found = diagnostics.at_open(dev, st)
    st.lists.clear()
    st.reads.clear()
    st.infos.clear()
    text = report.build(dev, st, space=(1000, 2000), found=found)
    assert st.lists == st.reads == st.infos == []
    assert "- Crashes that restarted the device: 1" in text and "- Memory dumps: 2, 500 B" in text
    not_read = report.build(dev, st, space=(1000, 2000), found=None)
    assert "- Memory dumps: not read yet" in not_read
    assert st.lists == st.reads == st.infos == []
    # After a removal the window keeps the dumps counted again: the report tells them, still
    # without asking the device.
    status.counted_again(types.SimpleNamespace(diagnostics=found),
                         status.Recount({}, None, None, datetime(2026, 10, 30),
                                        dumps=[("GARMIN/Debug/dump/RAM.zip", 300)]))
    after = report.build(dev, st, space=(1000, 2000), found=found)
    assert "- Memory dumps: 1, 300 B" in after and st.lists == st.reads == st.infos == []


# --- the same dumps copied again, a test drive's copies on the command line ------------------


def watch(root):
    """A made-up watch with two memory dumps."""
    dump = root / "GARMIN" / "Debug" / "dump"
    dump.mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    (dump / "RAM.zip").write_bytes(b"d" * 300)
    (dump / "RAM_bak1.zip").write_bytes(b"e" * 200)
    os.utime(dump / "RAM_bak1.zip", (1_700_000_000, 1_700_000_000))
    st = LocalStorage(root)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def test_the_same_dumps_copied_again_make_no_new_copy(tmp_path, clock):
    """A copy of the dumps is kept only when they changed: copied again unchanged, no new copy
    is made (it would push older copies out for nothing)."""
    st, dev = watch(tmp_path / "device")
    assert diagnostics.copy_dumps(dev, st).saved
    clock.current += timedelta(minutes=1)
    again = diagnostics.copy_dumps(dev, st)
    assert not again.saved and again.copies == 1
    assert diagnostics.copies_facts(UNIT)[0] == 1


def test_the_command_line_tells_a_test_drives_own_copies(tmp_path, clock, monkeypatch, capsys):
    """A test drive carries a real device's unit ID: the command line tells the drive's own
    copies on this computer, never the real device's."""
    from openexpress import cli

    real, dev = watch(tmp_path / "real")
    diagnostics.copy_dumps(dev, real)
    assert diagnostics.copies_facts(UNIT)[0] == 1  # the real device's copy
    drive, _dev = watch(tmp_path / "drive")
    drive.test_drive = True
    monkeypatch.setattr(cli, "open_device", lambda args: (drive, dev))
    assert cli.main(["--path", str(drive.root), "diagnostics"]) == 0
    said = next(line for line in capsys.readouterr().out.splitlines()
                if line.startswith("Copies on this computer:"))
    assert said == "Copies on this computer: No copy yet"


# --- two copies of one second ----------------------------------------------------------------


def test_rotate_keeps_the_newer_of_two_copies_of_one_second(tmp_path):
    where = tmp_path / "diagnostics"
    now = datetime(2026, 10, 21, 12, 0, 0)
    diagnostics._keep(where, b"older")
    diagnostics._save(where, "files", {"GARMIN/Debug/err_log.txt": {
        "sha256": diagnostics._sha(b"older"), "size": 5, "mtime": None}}, {}, now, "23.39")
    diagnostics._keep(where, b"newer")
    diagnostics._save(where, "files", {"GARMIN/Debug/err_log.txt": {
        "sha256": diagnostics._sha(b"newer"), "size": 5, "mtime": None}}, {}, now, "23.39")
    diagnostics.rotate(where, copies=1)
    kept = diagnostics._last(where, "files")
    assert kept["files"]["GARMIN/Debug/err_log.txt"]["sha256"] == diagnostics._sha(b"newer"), \
        [p.name for p in diagnostics._snapshots(where)]
