"""Tests the device's error logs, read at each opening (an "RTL Trap" of the last 30 days gets a
row on the device page), and the copy of its diagnostic files: off by default, made once per
connection, never sent; the hidden crash logs and memory dumps only on request. All entries,
files and devices are made up."""

import json
import os
import stat
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import DEVICE_XML
from fakes import window as stand_in

from openexpress import diagnostics, forget, paths
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
                                       "0x10000abc", "2301", "err_log.bak")
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
    assert found.crash is not None and found.crash.at == datetime(2026, 10, 20, 22, 24, 6)
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
    assert found.crash is not None and found.dumps == [] and found.copied is None
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
    assert diagnostics.boot_log_crashes(UNIT) == [
        "2026-10-20 22:24: RTL Trap detected, errnum: 0x1a2b"]


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
    session = types.SimpleNamespace(device=dev, storage=st, diagnostics=None)
    assert with_space(session) is session and session.diagnostics is None


def _reading_window(session, in_the_window):
    """A window with `session` open, its work in the background kept to be run by the test,
    and the row of the device's errors noted when it is drawn."""
    background, drawn = [], []
    in_the_window("device_error_row",
                  lambda win, group=None: drawn.append(win.session.diagnostics))
    win = stand_in(session=session, in_background=lambda fn, done, failed=None:
                   background.append((fn, done)))
    return win, background, drawn


def test_they_are_read_in_the_background_once_the_page_is_shown(device, clock, in_the_window):
    from openexpress.gui.device_page import read_diagnostics

    st, dev = device
    copy_on()
    session = types.SimpleNamespace(device=dev, storage=st, diagnostics=None,
                                    diagnostics_reading=None)
    win, background, drawn = _reading_window(session, in_the_window)
    read_diagnostics(win)
    read_diagnostics(win)  # shown again: read once
    (work, done), = background
    reading = session.diagnostics_reading
    assert not reading.is_set() and session.diagnostics is None
    done(work())
    assert reading.is_set()
    assert session.diagnostics.crash is not None and session.diagnostics.copied.saved
    assert drawn == [session.diagnostics]  # the row of the errors drawn with them


def test_they_are_kept_but_not_drawn_for_a_device_no_longer_open(device, clock, in_the_window):
    from openexpress.gui.device_page import read_diagnostics

    st, dev = device
    session = types.SimpleNamespace(device=dev, storage=st, diagnostics=None,
                                    diagnostics_reading=None)
    win, background, drawn = _reading_window(session, in_the_window)
    read_diagnostics(win)
    win.session = None  # unplugged meanwhile
    (work, done), = background
    done(work())
    assert session.diagnostics is not None and drawn == []


def test_a_failure_still_lets_the_tasks_go_on(device, in_the_window):
    from openexpress.gui.device_page import read_diagnostics

    st, dev = device
    session = types.SimpleNamespace(device=dev, storage=st, diagnostics=None,
                                    diagnostics_reading=None)
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
    session = types.SimpleNamespace(diagnostics_reading=reading)
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


def _window(found):
    from fakes import RunNow

    toasts = []
    return stand_in(
        session=types.SimpleNamespace(device=None, storage=None, diagnostics=found),
        _button=lambda label, cb, style=None, with_button=False: _labelled(label),
        run_task=RunNow(), toast=toasts.append, toasts=toasts)


def _labelled(label):
    from gi.repository import Gtk

    return Gtk.Button(label=label)


def _rows(win):
    from gi.repository import Adw, Gtk

    row = win.device_error_row
    if row is None:
        return None
    out = [(row.get_title(), row.get_subtitle())]
    stack = [row]
    while stack:
        w = stack.pop()
        child = w.get_first_child()
        while child is not None:
            # (the header of the expander is an action row of its own, with its title)
            if isinstance(child, Adw.ActionRow) and child.get_title() != row.get_title():
                buttons = [b.get_label() for b in _all(child) if isinstance(b, Gtk.Button)
                           and b.get_label()]
                out.append((child.get_title(), child.get_subtitle(), buttons))
            else:
                stack.append(child)
            child = child.get_next_sibling()
    return out


def _all(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _all(child)
        child = child.get_next_sibling()


def _found(**kw):
    (crash,) = diagnostics.parse_err_log(err_log(TRAP))
    values = {"entries": [crash], "crash": crash, "dumps": [("GARMIN/Debug/dump/RAM.zip", 8_090_553)],
              "hidden_logs": True}
    return diagnostics.AtOpen(**{**values, **kw})


@pytest.mark.needs_display
def test_the_row_says_the_crash_with_the_copy_off():
    from gi.repository import Adw

    from openexpress.gui.device_page import device_error_row

    win = _window(_found())
    group = Adw.PreferencesGroup()
    device_error_row(win, group)
    assert _rows(win) == [
        ("Device error log", "RTL Trap\u00a0· 2026-10-20 22:24"),
        ("What the device logged", "error 0x003\u00a0· at 0x10000abc\u00a0· screen 2301", []),
        ("Crash logs", ("Hidden from the file manager. While they are copied, the device "
                        "leaves it for a moment"), ["Copy Crash Logs"]),
        ("Memory dumps", "1 dump\u00a0· 8.1\u00a0MB\u00a0· encrypted", ["Copy Memory Dumps"])]


@pytest.mark.needs_display
def test_no_row_without_a_recent_crash_and_no_buttons_that_do_not_apply():
    from gi.repository import Adw

    from openexpress.gui.device_page import device_error_row

    win = _window(_found(crash=None))
    device_error_row(win, Adw.PreferencesGroup())
    assert win.device_error_row is None
    win = _window(None)  # not read
    device_error_row(win, Adw.PreferencesGroup())
    assert win.device_error_row is None
    win = _window(_found(hidden_logs=False, dumps=[]))
    device_error_row(win, Adw.PreferencesGroup())
    assert [r[0] for r in _rows(win)] == ["Device error log", "What the device logged"]


@pytest.mark.needs_display
def test_no_crash_logs_found_takes_the_button_away(monkeypatch):
    from gi.repository import Adw

    from openexpress.gui.device_page import copy_crash_logs, device_error_row

    found = _found()
    win = _window(found)
    group = Adw.PreferencesGroup()
    device_error_row(win, group)
    monkeypatch.setattr(diagnostics, "copy_crash_logs",
                        lambda device, storage: diagnostics.Copied())
    copy_crash_logs(win)
    assert win.toasts == ["The device has no hidden crash logs"]
    assert not found.hidden_logs
    assert "Crash logs" not in [r[0] for r in _rows(win)]
    monkeypatch.setattr(diagnostics, "copy_crash_logs",
                        lambda device, storage: diagnostics.Copied(new=2, saved=True))
    copy_crash_logs(win)
    assert win.toasts[-1] == "Crash logs copied to this computer"


@pytest.mark.needs_display
def test_copy_memory_dumps_says_what_did_not_fit(monkeypatch):
    from openexpress.gui.device_page import copy_dumps

    win = _window(_found())
    monkeypatch.setattr(diagnostics, "copy_dumps", lambda device, storage: diagnostics.Copied(
        new=1, unchanged=1, saved=True, not_copied=["RAM_bak2.zip"]))
    copy_dumps(win)
    assert win.toasts == [("2 memory dumps copied to this computer\u00a0· 1 not: over the "
                           "limit of 50.0\u00a0MB")]


@pytest.mark.needs_display
def test_the_preferences_switch_turns_the_copy_on_and_off():
    from gi.repository import Adw

    from openexpress.gui.preferences import diagnostics_group

    win = stand_in(error=lambda title, text: pytest.fail(text))
    group = diagnostics_group(win)
    switch = next(w for w in _all(group) if isinstance(w, Adw.SwitchRow))
    assert switch.get_title() == "Keep a copy of the device's diagnostic files on this computer"
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
    assert out[-1] == "Dry run. Add --apply to copy the files the device shows now."
    assert not diagnostics.folder(UNIT).exists() and st.sessions == 0
    assert not any(MADE_UP_UNIT in line or str(UNIT) in line or BACKTRACE[:16] in line
                   for line in out)


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
def test_the_row_in_polish():
    from gi.repository import Adw

    from openexpress import i18n
    from openexpress.gui.device_page import device_error_row

    i18n.setup("pl")
    try:
        win = _window(_found())
        device_error_row(win, Adw.PreferencesGroup())
        rows = _rows(win)
    finally:
        i18n.setup("en")
    assert rows == [
        ("Dziennik błędów urządzenia", "RTL Trap\u00a0· 20.10.2026 o\u00a022:24"),
        ("Co zapisało urządzenie", "błąd 0x003\u00a0· adres 0x10000abc\u00a0· ekran 2301", []),
        ("Dzienniki awarii", ("Ukryte przed menedżerem plików. Podczas kopiowania urządzenie "
                              "na chwilę z\u00a0niego zniknie"), ["Skopiuj dzienniki awarii"]),
        ("Zrzuty pamięci", "1 zrzut\u00a0· 8,1\u00a0MB\u00a0· zawartość zaszyfrowana",
         ["Skopiuj zrzuty pamięci"])]


# --- the places of the diagnostic files, kept for the next openings ---------------------------
# Over MTP each listing of a folder is a round trip: looking through the diagnostic folders takes
# 4-5 s on a Venu 2, even with no file to read. The first opening looks through them and keeps
# where it found the files and folders (in the device's folder); the next ones
# read those paths only, without listing; a path gone, one not there before that is there now,
# or other software on the device: looked through again, and the note made anew.


def _connected_again(st):
    st.usb.address += 1  # plugged in again: a new connection (at_open is once per connection)
    st.lists.clear()
    st.reads.clear()
    st.infos.clear()


def _same(a, b):
    assert (a.entries, a.crash, a.dumps, a.hidden_logs) == (b.entries, b.crash, b.dumps,
                                                            b.hidden_logs)


@pytest.mark.parametrize("copy", [True, False])
def test_a_second_opening_reads_the_places_kept_without_listing(device, clock, copy):
    st, dev = device
    copy_on(copy)
    first = diagnostics.at_open(dev, st)
    assert st.lists  # the first one looks through the folders
    assert diagnostics.places_path(UNIT).exists()
    _connected_again(st)
    again = diagnostics.at_open(dev, st)
    assert st.lists == []
    _same(again, first)
    if copy:
        assert again.copied.unchanged == first.copied.new and not again.copied.saved
    # Only the error logs are read again, as at each opening.
    assert sorted(Path(p).name for p in st.reads) == ["err_log.bak", "err_log.txt"]


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
    assert st.lists == []  # the note made anew holds


def test_the_places_not_there_are_not_asked_about_again(device, clock):
    """A path looked for in vain (the event logs, err_log.txt…) is kept as not there: the next
    openings ask the device about the places found only (on a Venu 2, 4 of its 9 questions
    are about paths it does not have)."""
    st, dev = device
    diagnostics.at_open(dev, st)
    absent = json.loads(diagnostics.places_path(UNIT).read_text())["absent"]
    assert "GARMIN/EVNTLOGS" in absent
    _connected_again(st)
    diagnostics.at_open(dev, st)
    assert st.infos and not set(st.infos) & set(absent) and st.lists == []


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
    assert diagnostics.at_open(dev, st).hidden_logs and st.lists == []
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
    assert st.lists == []
