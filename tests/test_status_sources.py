"""One answer for each question of the page Device Status, whoever asks it: the crashes, the
memory dumps, whether the device has diagnostic data and whether its error logs were read are the
same on the page of the device connected, on its page from the records, on the command line and
in the report; the device log keeps what the page from the records needs (every crash, the
connection told from the next one), drops nothing for a clock set wrong, skips a line it cannot
read, and is not made again for a device forgotten meanwhile; the space of the main folder's
files goes to their kind. All devices, files and times are made up."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import types
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta

import pytest
from conftest import DEVICE_XML

from openexpress import (
    crashes,
    diagnostics,
    forget,
    freeing,
    history,
    known_devices,
    paths,
    report,
    status,
)
from openexpress.device.storage import DeviceError, LocalStorage
from openexpress.device.xmldevice import parse

UNIT = 1234567890  # the Id of DEVICE_XML


class Device(LocalStorage):
    """A made-up watch over MTP on this computer's disk, at a place on USB."""

    def __init__(self, root, address=5, bus=1):
        super().__init__(root)
        self.device_uri = "mtp://made-up/"
        self.usb = types.SimpleNamespace(bus=bus, address=address, serial="MADE-UP")
        self.mass_storage = False

    def capacity(self):
        return 10_000_000

    def free_space(self):
        return 4_000_000

    def usb_blocked(self):
        return False

    def exclusive_ptp(self):
        return None


def entry(kind, time, **details):
    return {"error_cause": kind, "system_version": "23.39",
            "proprietary_info": {"Time": time, **details}}


def trap(time, error="0x003", pc="0x10000abc"):
    return entry("RTL Trap", time, **{"RTL Error Number": error, "programCounter": pc})


def make_root(tmp_path, xml=DEVICE_XML, err=None, dumps=()):
    root = tmp_path / "device"
    (root / "GARMIN" / "Debug" / "dump").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(xml)
    if err is not None:
        (root / "GARMIN" / "Debug" / "err_log.txt").write_bytes(json.dumps(err).encode())
    for n, (name, size) in enumerate(dumps):
        p = root / "GARMIN" / "Debug" / "dump" / name
        p.write_bytes(b"d" * size)
        os.utime(p, (1_700_000_000 + n, 1_700_000_000 + n))
    return root


def opened(root, **kw):
    st = Device(root, **kw)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def session_of(st, dev, found):
    """A session as the window leaves it once its reading of the error logs is done."""
    return types.SimpleNamespace(device=dev, storage=st, diagnostics=found,
                                 diagnostics_reading=None, space=None, client=None)


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    monkeypatch.setattr(history, "_noted", set())
    monkeypatch.setattr(history, "_forgotten", {})
    monkeypatch.setattr(diagnostics, "_done", {})


def kept_copy(kind, files, when="2026-10-21 11.00.00"):
    """A copy of diagnostic files as the window keeps it (diagnostics: objects, snapshots)."""
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


def write_lines(*lines):
    where = history.path(UNIT)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text("".join(json.dumps(x) + "\n" for x in lines))


def line_at(when, n=0, **fields):
    return {"v": 1, "time": when.astimezone().isoformat(timespec="seconds"),
            "conn": f"made-up-{n}", "via": "mtp", "software": "23.39", "categories": {},
            **fields}


# --- the crashes ------------------------------------------------------------------------------


def test_after_copy_crash_logs_the_page_and_the_report_count_the_boot_logs_crashes(tmp_path,
                                                                                  clock):
    root = make_root(tmp_path, err=[trap("2026-10-20 22:24:06")])
    st, dev = opened(root)
    found = diagnostics.at_open(dev, st)
    s = session_of(st, dev, found)
    assert len(status.read(s).crashes) == 1
    boot = (b"[09/01/26 10:00:00 1]: RTL Trap detected, errnum: 0x1a2b\n"
            b"[09/05/26 11:00:00 1]: RTL Trap detected, errnum: 0x1a2b\n")
    kept_copy("crash logs", {"Garmin/Debug/recovery_mode_log.txt": boot})
    page = status.read(s)
    text = report.build(dev, st, space=(4_000_000, 10_000_000), found=found)
    assert len(page.crashes) == 3
    assert "- Crashes that restarted the device: 3" in text


def test_the_page_from_the_records_tells_as_many_crashes_as_the_page_did(tmp_path, clock):
    """Keep Diagnostic Files off (no copy): the device log keeps every crash the error logs
    held at the connection, not only the newest."""
    root = make_root(tmp_path, err=[trap("2026-10-20 22:00:00"), trap("2026-10-15 08:00:00"),
                                    trap("2026-10-10 09:30:00")])
    (root / "GARMIN" / "EVNTLOGS").mkdir()
    st, dev = opened(root)
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    live = status.read(session_of(st, dev, found))
    kept = status.read_kept(UNIT)
    assert len(status.recent(live.crashes, clock.current)) == 3
    assert len(status.recent(kept.crashes, clock.current)) == 3


def test_a_device_that_restarts_again_and_again_has_each_crash_counted(tmp_path, clock):
    times = ["2026-10-20 22:00:00", "2026-10-20 22:00:45", "2026-10-20 22:01:30",
             "2026-10-20 22:02:15"]
    root = make_root(tmp_path, err=[trap(t) for t in times])
    st, _dev = opened(root)
    assert len(crashes.history(UNIT, diagnostics.read_errors(st))) == 4


def test_a_boot_log_line_joins_one_crash_never_two(tmp_path, clock):
    """The boot log's line, written at the start after a crash, between two crashes of the
    error log: it joins the crash before it, and the next crash stays one of its own."""
    root = make_root(tmp_path, err=[trap("2026-10-20 22:00:00"), trap("2026-10-20 22:00:50")])
    st, _dev = opened(root)
    boot = b"[10/20/26 22:00:36 1]: RTL Trap detected, errnum: 0x1a2b\n"
    kept_copy("crash logs", {"Garmin/Debug/recovery_mode_log.txt": boot})
    found = crashes.history(UNIT, diagnostics.read_errors(st))
    assert len(found) == 2
    assert [c.errnum for c in found] == [None, "0x1a2b"]  # newest first: 22:00:00 has it


def test_the_same_crash_seen_in_every_copy_and_the_device_log_counts_once(tmp_path, clock):
    err = [trap("2026-10-20 22:00:00"), trap("2026-10-12 07:00:00")]
    root = make_root(tmp_path, err=err)
    st, dev = opened(root)
    data = json.dumps(err).encode()
    for n in range(3):
        kept_copy("files", {"Garmin/Debug/err_log.txt": data}, when=f"2026-10-2{n} 11.00.00")
    history.note_connection(dev, st)
    assert len(crashes.history(UNIT, diagnostics.read_errors(st))) == 2


# --- the memory dumps -------------------------------------------------------------------------


def test_after_a_removal_the_report_tells_the_dumps_of_now(tmp_path, clock):
    """The report of the window tells what the window read at the opening, which Refresh keeps:
    after Free Up Space, the dumps counted again (status.counted_again), also after Refresh."""
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])
    st, dev = opened(root)
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    s = session_of(st, dev, found)
    (root / "GARMIN" / "Debug" / "dump" / "RAM_bak1.zip").unlink()  # Free Up Space removed it
    status.counted_again(s, status.recount(dev, st))
    refreshed = diagnostics.at_open(dev, Device(root))  # Refresh: the same connection
    text = report.build(dev, st, space=(4_000_000, 10_000_000), found=refreshed)
    assert "- Memory dumps: 1, 300 B" in text


# --- whether there is diagnostic data, whether the error logs were read ------------------------


def test_a_device_without_diagnostic_data_is_so_connected_and_from_its_records(tmp_path,
                                                                              clock):
    root = make_root(tmp_path)  # no error log, no dumps
    (root / "GARMIN" / "EVNTLOGS").mkdir()  # shows its event logs: nothing hidden
    st, dev = opened(root)
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    live = status.read(session_of(st, dev, found))
    kept = status.read_kept(UNIT)
    assert (live.has_diagnostics, kept.has_diagnostics) == (False, False)


def test_records_without_the_error_logs_say_they_were_not_read(clock):
    """The row says "Error Logs Not Read": the page it opens says it too, not "No Diagnostic
    Data"."""
    write_lines(line_at(clock.current - timedelta(days=1), capacity=10, free=5))
    kept = status.read_kept(UNIT)
    assert kept.errors_known is False and kept.has_diagnostics is True


def test_an_error_log_not_read_means_the_same_in_the_window_and_on_the_command_line(
        tmp_path, clock, capsys):
    """One of the error logs could not be read: the crashes are those kept on this computer, in
    the window and on the command line alike."""
    from openexpress import cli

    root = make_root(tmp_path, err=[trap("2026-10-20 22:24:06")])
    (root / "GARMIN" / "Debug" / "err_log.bak").write_bytes(b" " * (diagnostics.ERR_LOG_MAX + 1))
    st, dev = opened(root)
    found = diagnostics.at_open(dev, st)
    assert found.errors_known is False
    assert status.session_crashes(session_of(st, dev, found)) == []
    assert cli.main(["diagnostics", "--path", str(root)]) == 0
    out = capsys.readouterr().out
    assert "not every error log could be read" in out
    after = out.split("Crashes that restarted the device", 1)[1]
    assert "  none found" in after


def test_the_log_line_of_the_page_says_the_error_logs_were_not_read(caplog):
    from openexpress.gui import status_page

    facts = types.SimpleNamespace(
        crashes=[crashes.Crash(clock_now())], errors_known=False, free=5, capacity=10,
        has_diagnostics=True, older_dumps=list, not_installed=[], updates_why=None,
        connected=True)
    with caplog.at_level("INFO"):
        status_page._log_shown(facts, clock_now())
    assert "(error logs not read)" in caplog.text


def clock_now():
    from datetime import datetime

    return datetime(2026, 10, 30, 12, 0)


# --- the device log ---------------------------------------------------------------------------


def test_a_clock_set_wrong_once_drops_no_line(tmp_path, clock):
    root = make_root(tmp_path, err=[])
    write_lines(*[line_at(clock.current - timedelta(days=30 * n), n) for n in reversed(range(5))])
    st, dev = opened(root)
    assert history.note_connection(dev, st, now=clock.current + timedelta(days=3 * 365))
    assert len(history.read(UNIT).lines) == 6


def test_old_lines_still_go_after_two_years(tmp_path, clock, monkeypatch):
    monkeypatch.setattr(history, "KEEP_AT_LEAST", 2)
    root = make_root(tmp_path, err=[])
    write_lines(line_at(clock.current - timedelta(days=900), 0),
                line_at(clock.current - timedelta(days=800), 1),
                line_at(clock.current - timedelta(days=10), 2))
    st, dev = opened(root)
    assert history.note_connection(dev, st)
    kept = [x["conn"] for x in history.read(UNIT).lines]
    assert len(kept) == 2 and kept[0] == "made-up-2"  # 800 and 900 days old: gone


def test_a_line_with_a_time_the_readers_cannot_use_is_skipped(tmp_path, clock):
    from openexpress import cli

    bad = {"v": 1, "time": "0001-01-01T00:00:00", "conn": "made-up", "via": "mtp",
           "software": "23.39", "categories": {}}
    write_lines(bad)
    assert history.read(UNIT).lines == [] and history.read(UNIT).skipped == 1
    root = make_root(tmp_path, err=[])
    st, dev = opened(root)
    assert history.note_connection(dev, st)
    assert len(history.read(UNIT).lines) == 1
    cli.history_lines(UNIT)
    assert status.read_kept(UNIT) is not None


def test_a_device_forgotten_meanwhile_is_not_made_again_by_a_late_line(tmp_path, clock,
                                                                       monkeypatch):
    """The reading of a device just unplugged, or a count after a removal, ending after Forget
    Device: neither makes its folder again."""
    from openexpress import forget

    root = make_root(tmp_path, err=[])
    st, dev = opened(root)
    real_line = history._line

    def forgotten_meanwhile(*a, **kw):
        out = real_line(*a, **kw)
        forget._delete(UNIT, discard_pending=True, backups=False)
        return out

    monkeypatch.setattr(history, "_line", forgotten_meanwhile)
    assert not history.note_connection(dev, st)
    assert not paths.device_dir(UNIT).exists()
    monkeypatch.setattr(history, "_line", real_line)
    real_count = history.storage_by_kind

    def counted_meanwhile(*a, **kw):
        out = real_count(*a, **kw)
        forget._delete(UNIT, discard_pending=True, backups=False)
        return out

    history._noted.clear()
    assert history.note_connection(dev, st)
    monkeypatch.setattr(history, "storage_by_kind", counted_meanwhile)
    history.note_counted(dev, st)
    assert not paths.device_dir(UNIT).exists()


def test_a_device_plugged_in_again_is_a_new_connection_without_its_place_on_usb(tmp_path,
                                                                                clock):
    root = make_root(tmp_path, err=[])
    st, dev = opened(root, address=None, bus=None)
    assert history.note_connection(dev, st)
    history._noted.clear()  # the device came back
    newer = DEVICE_XML.replace("<SoftwareVersion>2339<", "<SoftwareVersion>2340<")
    (root / "GARMIN" / "GarminDevice.xml").write_text(newer)
    st2, dev2 = opened(root, address=None, bus=None)
    assert history.note_connection(dev2, st2)
    assert [x["software"] for x in history.read(UNIT).lines] == ["23.39", "23.40"]


def test_the_place_on_usb_comes_from_the_volume_when_sys_cannot_be_read(monkeypatch, tmp_path):
    from openexpress.device import gio_storage

    volume = types.SimpleNamespace(get_identifier=lambda name: "/dev/bus/usb/003/017")
    monkeypatch.setattr(gio_storage, "SYSFS_USB", tmp_path / "no-sys")
    usb = gio_storage._identify("mtp://091e_4b48_MADEUPSERIAL/", volume)
    # Not checked by /sys: no place for what asks it (a stall, the device still there), only
    # for what tells one connection from the next.
    assert (usb.product, usb.bus, usb.address, usb.told_place) == ("4b48", None, None, (3, 17))
    assert gio_storage.usb_place(usb) is None
    st = types.SimpleNamespace(usb=usb)
    dev = types.SimpleNamespace(unit_id=UNIT, software_version_str="23.39")
    other = types.SimpleNamespace(usb=gio_storage._identify(
        "mtp://091e_4b48_MADEUPSERIAL/",
        types.SimpleNamespace(get_identifier=lambda name: "/dev/bus/usb/003/018")))
    assert history._key(dev, st, "mtp") != history._key(dev, other, "mtp")


def test_two_writers_of_one_connection_write_one_line(tmp_path, clock, monkeypatch):
    root = make_root(tmp_path, err=[])
    st, dev = opened(root)
    monkeypatch.setattr(history, "_last_key", lambda where: None)  # both pass the first check
    gate = threading.Barrier(4)
    real_line = history._line

    def slow_line(*a, **kw):
        out = real_line(*a, **kw)
        gate.wait()
        return out

    monkeypatch.setattr(history, "_line", slow_line)
    results = []

    def one():
        history._noted.clear()
        results.append(history.note_connection(dev, Device(root)))

    threads = [threading.Thread(target=one) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, False, False, True]
    assert len(history.read(UNIT).lines) == 1


# --- the space of the main folder -------------------------------------------------------------

MAIN_TYPE = """
  <DataType><Name>FTLImageVersion</Name>
   <File><Location><Path>Garmin</Path>{extension}</Location>
    <TransferDirection>InputOutput</TransferDirection></File></DataType>
"""


@pytest.mark.parametrize("extension", ["<FileExtension>ver</FileExtension>", ""],
                         ids=["with an extension", "without"])
def test_an_update_waiting_in_the_main_folder_counts_as_an_update(tmp_path, clock, extension):
    """As a fēnix 8 (maps declared for the main folder) and an Edge 1050 (FTLImageVersion)
    declare it: GUPDATE.GCD there is an update, and GarminDevice.xml is no map."""
    root = make_root(tmp_path, err=[])
    (root / "GARMIN" / "GUPDATE.GCD").write_bytes(b"u" * 9000)
    (root / "GARMIN" / "fs_image.ver").write_bytes(b"v" * 30)
    xml = DEVICE_XML.replace("</MassStorageMode>",
                             MAIN_TYPE.format(extension=extension) + "</MassStorageMode>")
    (root / "GARMIN" / "GarminDevice.xml").write_text(xml)
    st, dev = opened(root)
    sizes = history.count(dev, st)
    assert sizes["updates"] == 9000
    assert sizes.get("maps", 0) == (30 if extension else 0)


def test_the_free_space_line_matches_the_page(tmp_path, clock, capsys, monkeypatch):
    """The command line's list of what can be freed and the page's facts: the same dumps."""
    from openexpress import cli, freeing

    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: ([], None))
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])
    st, dev = opened(root)
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["--lang", "en", "diagnostics", "--path", str(root), "--free-space"])
    found = diagnostics.at_open(dev, st)
    facts = status.read(session_of(st, dev, found))
    assert f"older memory dumps: {len(facts.older_dumps())} " in out.getvalue()


# --- the device log written again, and what it tells after a removal --------------------------


def test_a_clock_ahead_once_drops_nothing_at_the_next_connection_either(clock):
    from datetime import datetime

    def at(when):
        return when.astimezone().isoformat(timespec="seconds")

    base = datetime(2025, 1, 1, 12, 0)
    lines = [{"time": at(base + timedelta(days=n)), "conn": f"c{n}"} for n in range(150)]
    wrong = {"time": at(datetime(2099, 1, 1, 12, 0)), "conn": "wrong"}
    after_wrong = history._kept([*lines, wrong])
    right = base + timedelta(days=151)
    after_next = history._kept([*after_wrong, {"time": at(right), "conn": "next"}])
    assert len(after_next) == 152


def test_a_line_it_cannot_read_and_fields_it_does_not_know_stay_when_it_writes(tmp_path, clock,
                                                                              monkeypatch):
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])
    st, dev = opened(root)
    assert history.note_connection(dev, st)
    where = history.path(UNIT)
    item = json.loads(where.read_text().splitlines()[-1])
    item["made_up_field"] = 7
    where.write_text("not a line of the log\n" + json.dumps(item) + "\n")
    history.note_counted(dev, st)
    text = where.read_text().splitlines()
    assert text[0] == "not a line of the log"
    assert json.loads(text[1])["made_up_field"] == 7 and "counted" in json.loads(text[1])
    monkeypatch.setattr(history, "MAX_LINES", 1)
    history._noted.clear()
    st2, dev2 = opened(root, address=9)
    assert history.note_connection(dev2, st2)
    assert where.read_text().splitlines()[0] == "not a line of the log"


def test_a_removal_not_counted_again_puts_the_line_right_by_what_it_removed(tmp_path, clock):
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])
    st, dev = opened(root)
    assert history.note_connection(dev, st)
    before = history.read(UNIT).lines[-1]
    assert history.note_removed(dev, st, [("GARMIN/Debug/dump/RAM_bak1.zip", 200)], "logs")
    after = history.read(UNIT).lines[-1]
    assert after["dumps"] == {"count": 1, "bytes": 300}
    assert after["free"] == before["free"] + 200
    assert after["categories"]["logs"] == before["categories"]["logs"] - 200
    kept = status.read_kept(UNIT)
    assert kept.dumps_at_last == (1, 300) and kept.recounted


def test_a_line_counted_after_the_removal_is_not_put_right_again(tmp_path, clock):
    """The window and the command line at once: the device was counted again after a removal
    that could not count it; that removal must not take its sizes off the newer count."""
    from datetime import timedelta

    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])
    st, dev = opened(root)
    assert history.note_connection(dev, st)
    removed_at = clock.current
    clock.current += timedelta(minutes=1)
    history.note_counted(dev, st, clock.current)
    counted = history.read(UNIT).lines[-1]
    history.note_removed(dev, st, [("GARMIN/Debug/dump/RAM_bak1.zip", 200)], "logs",
                         now=removed_at)
    assert history.read(UNIT).lines[-1] == counted


def test_the_report_tells_the_dumps_after_a_removal_from_the_command_line(tmp_path, clock):
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])
    st, dev = opened(root)
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    (root / "GARMIN" / "Debug" / "dump" / "RAM_bak1.zip").unlink()  # removed by the CLI
    history.note_counted(dev, st)  # as the CLI does after it
    text = report.build(dev, st, space=(4_000_000, 10_000_000), found=found)
    assert "- Memory dumps: 1, 300 B" in text


def test_copy_memory_dumps_never_pushes_out_the_copies_of_the_error_logs(tmp_path, clock,
                                                                        monkeypatch):
    monkeypatch.setattr(diagnostics, "LIMIT", 1_000)
    monkeypatch.setattr(diagnostics.rotate, "__defaults__", (diagnostics.COPIES_KEPT, 1_000))
    root = make_root(tmp_path, err=[trap("2026-10-01 10:00:00")], dumps=[("RAM.zip", 900)])
    st, dev = opened(root)
    diagnostics.copy_files(dev, st)
    assert len(crashes.history(UNIT)) == 1
    clock.current += timedelta(minutes=1)
    diagnostics.copy_dumps(dev, st)
    assert len(crashes.history(UNIT)) == 1


def test_a_test_drive_has_its_own_copies_everywhere(tmp_path, clock):
    class Drive(Device):
        test_drive = True

    root = make_root(tmp_path, err=[trap("2026-10-01 10:00:00")])
    real, dev = opened(root)
    diagnostics.copy_files(dev, real)  # the real device's copy of its error log
    drive_root = tmp_path / "drive"
    (drive_root / "GARMIN" / "Debug").mkdir(parents=True)
    (drive_root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    drive = Drive(drive_root)
    assert crashes.history(UNIT, test_drive=True) == []  # not the device's copies
    assert diagnostics.copies_facts(UNIT, True)[0] == 0
    assert diagnostics.copies_text(UNIT, True) == diagnostics.copies_text(UNIT, test_drive=True)
    assert diagnostics.folder(UNIT, True) != diagnostics.folder(UNIT)
    assert drive.test_drive


def test_a_file_of_an_update_is_read_once_while_it_stays_the_same(tmp_path, clock):
    import hashlib as _h

    from openexpress import updates

    root = make_root(tmp_path, err=[])
    where = root / "GARMIN" / "RemoteSW" / "GPS.GCD"
    where.parent.mkdir(parents=True)
    where.write_bytes(b"g" * 4_000)
    os.utime(where, (1_700_000_000, 1_700_000_000))
    st, _dev = opened(root)
    reads = []
    real_read = st.read
    st.read = lambda path: reads.append(path) or real_read(path)
    u = types.SimpleNamespace(device_path="GARMIN/RemoteSW/GPS.GCD", size=4_000,
                              md5=_h.md5(b"g" * 4_000).hexdigest(), part_number="006-D0000-00")
    assert updates.presence(st, u).why == updates.MATCHES
    assert updates.presence(st, u).why == updates.MATCHES
    assert len(reads) == 1
    where.write_bytes(b"h" * 4_000)
    os.utime(where, (1_700_000_500, 1_700_000_500))
    assert updates.presence(st, u).why == updates.MD5_DIFFERS
    assert len(reads) == 2


def test_a_linked_folder_is_still_walked_where_links_are_followed(tmp_path):
    root = tmp_path / "copy"
    (root / "GARMIN").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.fit").write_bytes(b"x" * 10)
    (root / "GARMIN" / "Activity").symlink_to(elsewhere, target_is_directory=True)
    st = LocalStorage(root)
    assert [p for p, _e in st.walk_files("GARMIN")] == ["GARMIN/Activity/a.fit"]


# --- the copies of each kind, the device log's extremes, like crashes of one file --------------


def test_copy_memory_dumps_never_pushes_out_a_crash_log_copy_by_their_number(tmp_path, clock):
    """Copied again and again (the same dumps each time), the dumps never push out the copy of
    the boot log, whose crash no other source holds."""
    root = make_root(tmp_path, dumps=[("RAM.zip", 50)])
    st, dev = opened(root)
    kept_copy("crash logs", {diagnostics.CRASH_LOGS[0]:
                             b"[10/20/26 09:15:00 123]: RTL Trap detected, errnum: 0x6e5d8003\n"})
    assert len(crashes.history(UNIT)) == 1
    for n in range(diagnostics.COPIES_KEPT + 2):
        diagnostics.copy_dumps(dev, st, now=clock.current + timedelta(minutes=n))
    assert len(crashes.history(UNIT)) == 1


def test_copies_of_the_error_logs_never_push_out_the_newest_copy_of_the_dumps(tmp_path, clock):
    root = make_root(tmp_path, dumps=[("RAM.zip", 50)])
    st, dev = opened(root)
    diagnostics.copy_dumps(dev, st, now=clock.current - timedelta(days=1))
    where = diagnostics.folder(UNIT)
    for n in range(diagnostics.COPIES_KEPT + 2):
        diagnostics._save(where, "files", {f"GARMIN/Debug/x{n}": {}}, {},
                          clock.current + timedelta(minutes=n), "23.39")
        diagnostics.rotate(where)
    kinds = [json.loads(p.read_text())["kind"] for p in (where / "snapshots").glob("*.json")]
    assert kinds.count("dumps") == 1 and kinds.count("files") == diagnostics.COPIES_KEPT


def test_a_clock_wrong_at_two_connections_drops_no_real_line(clock):
    def at(when):
        return when.astimezone().isoformat(timespec="seconds")

    base = clock.current - timedelta(days=300)
    lines = [{"time": at(base + timedelta(days=n)), "conn": f"c{n}"} for n in range(150)]
    wrong = clock.current + timedelta(days=3 * 365)
    first = history._kept([*lines, {"time": at(wrong), "conn": "w1"}])
    second = history._kept([*first, {"time": at(wrong + timedelta(hours=1)), "conn": "w2"}])
    assert len(second) == 152


def test_a_line_that_is_not_utf8_stays_as_it_was_when_the_log_is_written_again(tmp_path, clock):
    root = make_root(tmp_path, err=[])
    st, dev = opened(root)
    assert history.note_connection(dev, st)
    where = history.path(UNIT)
    damaged = b"\xff\xfe a line of another program \x80\n"
    where.write_bytes(damaged + where.read_bytes())
    assert history.read(UNIT).skipped == 1
    history.note_counted(dev, st)
    assert where.read_bytes().startswith(damaged)
    assert "counted" in history.read(UNIT).lines[-1]


def test_two_like_entries_of_one_file_are_two_crashes(tmp_path, clock):
    """A device restarting again and again may write two entries of the same second and codes:
    two crashes, also once its copies and its device log hold them too."""
    root = make_root(tmp_path, err=[trap("2026-10-20 10:00:00"), trap("2026-10-20 10:00:00")])
    st, dev = opened(root)
    found = diagnostics.read_errors(st)
    assert len(crashes.history(None, found)) == 2
    history.note_connection(dev, st, found=diagnostics.AtOpen(
        entries=found, crash=None, hidden_logs=False, dumps=[]))
    kept_copy("files", {"GARMIN/Debug/err_log.txt": (root / "GARMIN/Debug/err_log.txt")
                        .read_bytes()})
    assert len(crashes.history(UNIT, found)) == 2


def test_the_same_error_as_the_one_before_is_the_crash_just_before(clock):
    x = {"error": "0x003", "program_counter": "0x1"}
    y = {"error": "0x009", "program_counter": "0x2"}
    found = [crashes.Crash(clock.current - timedelta(days=d), **kind)
             for d, kind in ((1, x), (2, y), (3, x), (4, x))]
    assert [e.same_before for e in status.events(found, [], None)] == [0, 0, 1, 0]


def test_a_line_with_a_size_too_large_is_skipped(clock):
    from openexpress import cli

    huge = 10 ** 400
    write_lines(line_at(clock.current, capacity=huge, free=huge // 2))
    assert history.read(UNIT).lines == [] and history.read(UNIT).skipped == 1
    cli.history_lines(UNIT)


def test_no_diagnostic_data_means_the_same_connected_and_from_the_records(tmp_path, clock):
    """Copy Crash Logs found none on a watch that hides them, with nothing else to show: the
    page of the device connected and the page from its records both say No Diagnostic Data."""
    root = make_root(tmp_path, err=[])
    st, dev = opened(root)
    found = diagnostics.AtOpen(entries=[], crash=None, hidden_logs=True, dumps=[])
    history.note_connection(dev, st, found=found)
    where = diagnostics.folder(UNIT)
    where.mkdir(parents=True, exist_ok=True)
    (where / "state.json").write_text(json.dumps({"crash_logs": "none"}))
    s = session_of(st, dev, found)
    assert status.read(s).has_diagnostics is False
    assert status.read_kept(UNIT).has_diagnostics is False


def test_a_test_drive_changes_nothing_of_the_device_s_records(tmp_path, clock, monkeypatch):
    """A test drive carries a real device's unit ID: neither the places of its diagnostic files
    nor what can be freed on it changes what this computer keeps of that device."""
    from openexpress import freeing, known_devices

    root = make_root(tmp_path, err=[])
    st, dev = opened(root)
    st.test_drive, st.mass_storage = True, True
    diagnostics.seen_now(dev, st, with_files=False)
    assert diagnostics.places_path(UNIT, True).exists()
    assert not diagnostics.places_path(UNIT).exists()
    forgotten = []
    monkeypatch.setattr(known_devices, "keep_copied",
                        lambda unit, offered: forgotten.append(unit))
    monkeypatch.setattr(known_devices, "load", lambda: {UNIT: types.SimpleNamespace(
        copied_updates=(("006-D0000-00.6.10", 1_700_000_000.0),), unplugged_at=None)})
    client = types.SimpleNamespace(software_updates=lambda raw, **kw: {"updates": []})
    assert freeing.updates_not_installed(client, st, dev) == ([], None)
    assert forgotten == []


# --- what guards each rule of the crashes, the dumps and the records -------------------------


_AT = __import__("datetime").datetime(2026, 10, 20, 22, 0, 0)


def test_a_boot_log_line_more_than_a_minute_from_a_crash_is_a_crash_of_its_own():
    """The device writes the boot log's line at the start after a crash, within a minute of it:
    a line further away is another crash, even with codes that agree."""
    logged = crashes.Crash(_AT, error="0x003", program_counter="0x10000abc",
                           sources={"err_log.txt"})

    def boot(seconds):
        return crashes.Crash(_AT + timedelta(seconds=seconds), errnum="0x1a2b",
                             sources={crashes.BOOT_LOG})

    assert len(crashes.merge([logged, boot(40)])) == 1
    assert len(crashes.merge([logged, boot(90)])) == 2


def test_the_same_error_number_at_another_program_counter_is_not_the_same_error():
    """By the error log, the same error is the same error number at the same program counter."""
    newest = crashes.Crash(_AT, error="0x003", program_counter="0x10000abc")
    before = crashes.Crash(_AT - timedelta(days=3), error="0x003", program_counter="0x10000def")
    assert not crashes.same_error(newest, before)


def test_a_link_named_like_the_folder_of_dumps_is_not_followed(tmp_path):
    """A folder on this computer (a test drive, --path) may hold links: the dumps of a folder that
    a link named like the folder of dumps leads to elsewhere are not the device's."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "RAM_bak9.zip").write_bytes(b"k" * 50)
    root = tmp_path / "device"
    (root / "GARMIN" / "Debug").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    (root / "GARMIN" / "Debug" / "dump").symlink_to(elsewhere, target_is_directory=True)
    assert diagnostics.dumps_on_device(LocalStorage(root)) == []


def test_records_with_only_crash_logs_to_copy_have_the_page(clock):
    """Crash logs that only Copy Crash Logs reads are diagnostic data too: without any other
    (no entry, crash, dump or copy), the records still lead to the page, not to "No Diagnostic
    Data"."""
    nothing_else = dict(capacity=10, free=5, crash=None, crashes=[], errors=0,
                        dumps={"count": 0, "bytes": 0})
    yesterday = clock.current - timedelta(days=1)
    write_lines(line_at(yesterday, hidden=False, **nothing_else))
    assert status.read_kept(UNIT).has_diagnostics is False
    write_lines(line_at(yesterday, hidden=True, **nothing_else))
    assert status.read_kept(UNIT).has_diagnostics is True


def test_a_file_of_an_update_without_a_time_is_read_each_time(tmp_path, monkeypatch):
    """A file read once is not read again while its size and its time stay the same: one whose
    time is not known is read again each time, never taken from an earlier reading."""
    from openexpress import updates

    root = tmp_path / "device"
    where = root / "GARMIN" / "RemoteSW" / "GPS.GCD"
    where.parent.mkdir(parents=True)
    where.write_bytes(b"g" * 4_000)
    os.utime(where, (0, 0))  # no time known
    st = LocalStorage(root)
    reads = []
    real_read = st.read
    monkeypatch.setattr(st, "read", lambda path: reads.append(path) or real_read(path))
    u = types.SimpleNamespace(device_path="GARMIN/RemoteSW/GPS.GCD", size=4_000,
                              md5=hashlib.md5(b"g" * 4_000).hexdigest())
    assert updates.presence(st, u).why == updates.MATCHES
    where.write_bytes(b"h" * 4_000)  # rewritten, its time still not known
    os.utime(where, (0, 0))
    assert updates.presence(st, u).why == updates.MD5_DIFFERS
    assert len(reads) == 2


def test_a_count_after_a_removal_waits_for_the_lock_of_the_records(tmp_path, clock):
    """Another process removing, writing the device log or forgetting the device holds the lock
    of the records: the count puts the line right only after it."""
    root = make_root(tmp_path, err=[])
    st, dev = opened(root)
    assert history.note_connection(dev, st)
    done = []
    with history.device_locked(UNIT):
        counting = threading.Thread(target=lambda: done.append(history.note_counted(dev, st)))
        counting.start()
        counting.join(0.3)
        assert done == [] and "counted" not in history.read(UNIT).lines[-1]
    counting.join(5)
    assert len(done) == 1 and "counted" in history.read(UNIT).lines[-1]


# --- a crash in both error logs, the latest count, places kept before, a test drive's updates


def make_logs_root(tmp_path, txt=None, bak=None, dumps=()):
    root = tmp_path / "device"
    (root / "GARMIN" / "Debug" / "dump").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    if txt is not None:
        (root / "GARMIN" / "Debug" / "err_log.txt").write_bytes(json.dumps(txt).encode())
    if bak is not None:
        (root / "GARMIN" / "Debug" / "err_log.bak").write_bytes(json.dumps(bak).encode())
    for name, size, when in dumps:
        p = root / "GARMIN" / "Debug" / "dump" / name
        p.write_bytes(b"d" * size)
        os.utime(p, (when, when))
    return root


class Client:
    """The update server: offers one update whose file is GARMIN/RemoteSW/006-D0000-00.GCD."""

    def __init__(self, data):
        self.data = data

    def software_updates(self, raw, include_reinstalls=True, fresh=False):
        return {"updates": [{
            "partnumber": "006-D0000-00", "productname": "GPS software", "version": "620",
            "displayversion": "6.20",
            "installation": {"path": "GARMIN/RemoteSW", "filename": "006-D0000-00.GCD"},
            "deliverable": {"md5": hashlib.md5(self.data).hexdigest(),
                            "sizeinbytes": len(self.data)}}]}


def drive_with_update(tmp_path, test_drive):
    root = tmp_path / "drive"
    (root / "GARMIN" / "RemoteSW").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    data = b"u" * 4_100
    (root / "GARMIN" / "RemoteSW" / "006-D0000-00.GCD").write_bytes(data)
    st = LocalStorage(root)
    st.test_drive = test_drive
    return st, parse(st.read("GARMIN/GarminDevice.xml")), data


def test_one_entry_in_both_error_logs_is_one_crash_also_once_the_device_log_has_it(
        tmp_path, clock):
    """The same entry in both error logs is one crash, before and after the device log holds it."""
    entry = trap("2026-10-20 10:00:00")
    st, dev = opened(make_logs_root(tmp_path, txt=[entry], bak=[entry]))
    entries = diagnostics.read_errors(st)
    before = len(crashes.history(UNIT, entries))
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    after = len(crashes.history(UNIT, entries))
    kept = len(status.read_kept(UNIT).crashes)
    assert (before, after, kept) == (1, 1, 1)


def test_two_crashes_of_the_two_error_logs_within_a_minute_stay_two(clock):
    """Two crashes of the error logs 40 s apart, with the same codes, are two whichever log holds
    them: only a boot log's line joins a crash."""
    from openexpress.diagnostics import ErrorEntry

    t = datetime(2026, 10, 20, 10, 0, 0)
    a = ErrorEntry("RTL Trap", t, "0x003", "0x10000abc", None, "err_log.txt")
    b_same_file = ErrorEntry("RTL Trap", t + timedelta(seconds=40), "0x003", "0x10000abc",
                             None, "err_log.txt")
    b_other_file = ErrorEntry("RTL Trap", t + timedelta(seconds=40), "0x003", "0x10000abc",
                              None, "err_log.bak")
    in_one = len(crashes.history(None, [b_same_file, a]))
    in_two = len(crashes.history(None, [b_other_file, a]))
    assert (in_one, in_two) == (2, 2)


def test_a_crash_kept_only_in_a_copy_and_one_on_the_device_40_s_later_are_two(tmp_path,
                                                                                clock):
    """A crash only an earlier copy of the error log holds and the next one on the device, 40 s
    later with the same codes, are two crashes, live and from the records."""
    import hashlib

    x, y = trap("2026-10-20 10:00:00"), trap("2026-10-20 10:00:40")
    where = diagnostics.folder(UNIT)
    (where / "objects").mkdir(parents=True)
    (where / "snapshots").mkdir()
    data = json.dumps([x]).encode()
    sha = hashlib.sha256(data).hexdigest()
    (where / "objects" / sha).write_bytes(data)
    (where / "snapshots" / "2026-10-20 11.00.00.json").write_text(json.dumps({
        "kind": "files", "at": "2026-10-20T11:00:00", "software_version": "23.39",
        "files": {"GARMIN/Debug/err_log.txt": {"sha256": sha, "size": len(data),
                                                "mtime": None}}, "listed": {}}))
    st, dev = opened(make_logs_root(tmp_path, txt=[y]))
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    live = len(crashes.history(UNIT, found.entries))
    kept = len(status.read_kept(UNIT).crashes)
    assert (live, kept) == (2, 2)


def test_the_page_of_a_connection_without_its_line_does_not_show_another_connections_count(
        tmp_path, clock):
    """The page takes only this connection's line of the device log, never an earlier connection's."""
    st, dev = opened(make_logs_root(tmp_path))
    old = (clock.current - timedelta(days=20)).astimezone().isoformat(timespec="seconds")
    where = history.path(UNIT)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps({"v": 1, "time": old, "conn": "an-earlier-connection",
                                 "via": "mtp", "software": "23.39",
                                 "categories": {"music": 3_000_000}, "free": 1,
                                 "capacity": 10_000_000, "counted": old}) + "\n")
    found = diagnostics.AtOpen([], None, [], False)
    s = types.SimpleNamespace(device=dev, storage=st, diagnostics=found,
                              diagnostics_reading=None, space=(None, None), client=None)
    made = status.read(s)
    assert history.connection_line(dev, st) is None
    assert made.categories == {} and made.free is None, (
        f"the page shows {made.categories}, free {made.free}, counted {made.counted_at}, "
        f"recounted {made.recounted}: another connection's")


def test_the_count_never_takes_a_folder_behind_a_link(tmp_path, clock):
    """A declared folder that is a link to a folder elsewhere on this computer is not the device's:
    not counted."""
    root = make_logs_root(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "big.fit").write_bytes(b"x" * 500_000)
    (root / "GARMIN" / "Activity").symlink_to(elsewhere, target_is_directory=True)
    st, dev = opened(root)
    kinds = history.count(dev, st)
    assert kinds.get("activities", 0) == 0, f"counted through a link: {kinds}"


def test_places_kept_by_an_earlier_rule_do_not_count_a_text_as_a_dump(tmp_path, clock):
    """Places kept by an earlier rule of what a dump is (any file of the folder of dumps) are
    looked for again: the opening, the device log and the page count the dumps by one rule."""
    root = make_logs_root(tmp_path, dumps=[("RAM.zip", 90, 1_700_000_400)])
    (root / "GARMIN" / "Debug" / "dump" / "notes.txt").write_text("not a dump")
    st, dev = opened(root)
    note = {"software_version": dev.software_version_str, "err_logs": [],
            "dumps": ["GARMIN/Debug/dump/RAM.zip", "GARMIN/Debug/dump/notes.txt"],
            "files": None, "folders": ["GARMIN/Debug", "GARMIN/Debug/dump"],
            "event_logs": False}
    where = diagnostics.places_path(UNIT)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps(note))
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    listed = len(diagnostics.dump_files(st))
    at_open = len(found.dumps)
    in_log = history.connection_line(dev, st)["dumps"]["count"]
    assert (listed, at_open, in_log) == (1, 1, 1)


def test_a_count_that_could_not_ask_the_capacity_keeps_the_capacity_known(clock):
    """A count whose capacity question failed keeps the capacity known: it does not change."""
    s = types.SimpleNamespace(space=(100, 1_000), recount=None, diagnostics=None)
    counted = status.Recount({}, 200, None, clock.current, dumps=None)
    status.counted_again(s, counted)
    assert s.space == (200, 1_000), f"the session's space after the count: {s.space}"


def test_the_report_after_a_removal_whose_dumps_could_not_be_listed(tmp_path, clock,
                                                                      monkeypatch):
    """After a removal whose count could not list the dumps, the report tells the dumps of now or
    that they are not known, never those of before the removal."""
    from openexpress import report
    from openexpress.device.storage import DeviceError

    root = make_logs_root(tmp_path, dumps=[("RAM.zip", 90, 1_700_000_400),
                                      ("RAM_bak1.zip", 300, 1_700_000_300),
                                      ("RAM_bak2.zip", 200, 1_700_000_200)])
    st, dev = opened(root)
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    s = types.SimpleNamespace(device=dev, storage=st, diagnostics=found, recount=None,
                              space=(4_000_000, 10_000_000))
    plan = freeing.dump_plan([(p, e.size, e.mtime) for p, e in diagnostics.dump_files(st)])
    freed = freeing.remove_dumps(st, UNIT, plan)
    assert len(freed.removed) == 2

    def not_listed(storage):
        raise DeviceError("Cannot list GARMIN/Debug/dump")

    monkeypatch.setattr(diagnostics, "dumps_on_device", not_listed)
    counted = status.recount(dev, st, removed={"logs": freed.removed})
    status.counted_again(s, counted)
    lines = report._diagnostics(dev, st, False, s.diagnostics)
    said = next(line for line in lines if line.startswith("- Memory dumps"))
    assert said in ("- Memory dumps: 1, 90 B", "- Memory dumps: not known"), said


def test_a_test_drive_never_takes_the_real_devices_copied_updates(tmp_path, clock):
    """A test drive carries a real device's unit ID: what was copied to that device is not offered
    for removal on the drive."""
    now = clock.current.timestamp()
    known_devices.remember(UNIT, "Made-up watch", "006-B0000-00", "1.00", "mtp", now=now - 900)
    known_devices.note_copied(UNIT, ["006-D0000-00.6.20"], now=now - 600)
    known_devices.note_unplugged(UNIT, now=now - 300)
    real_st, dev, data = drive_with_update(tmp_path / "real", test_drive=False)
    assert [u.name for u in freeing.plan(real_st, dev, Client(data)).updates] == [
        "GPS software"]  # the real device: right
    st, dev, data = drive_with_update(tmp_path / "test", test_drive=True)
    shown = [u.name for u in freeing.plan(st, dev, Client(data)).updates]
    assert shown == [], "the test drive is offered a removal from the real device's records"


# --- the nearest crash for a boot log line, like update files of two devices -----------------


def test_a_boot_log_line_joins_the_nearest_crash_before_it(tmp_path, clock):
    """The boot log's line is written at the start after the crash that restarted the device:
    of two crashes within a minute before it, it joins the nearest, not the earlier."""
    root = make_root(tmp_path, err=[trap("2026-10-20 22:00:00"),
                                    trap("2026-10-20 22:00:40", error="0x005", pc="0x10000123")])
    st, _dev = opened(root)
    boot = b"[10/20/26 22:00:45 1]: RTL Trap detected, errnum: 0x1a2b\n"
    kept_copy("crash logs", {"Garmin/Debug/recovery_mode_log.txt": boot})
    found = crashes.history(UNIT, diagnostics.read_errors(st))
    assert [(c.error, c.errnum) for c in found] == [("0x005", "0x1a2b"), ("0x003", None)]


def test_like_update_files_of_two_devices_are_each_read(tmp_path, monkeypatch):
    """An MD5 read before is kept for its device: another device's file at the same path, of the
    same size and time but with other bytes, is read, never taken for the first one's."""
    from openexpress import updates

    monkeypatch.setattr(updates, "_md5_read", {})
    stores = []
    for name, data in (("first", b"g" * 4_000), ("second", b"h" * 4_000)):
        where = tmp_path / name / "GARMIN" / "RemoteSW" / "GPS.GCD"
        where.parent.mkdir(parents=True)
        where.write_bytes(data)
        os.utime(where, (1_700_000_000, 1_700_000_000))
        stores.append(LocalStorage(tmp_path / name))
    u = types.SimpleNamespace(device_path="GARMIN/RemoteSW/GPS.GCD", size=4_000,
                              md5=hashlib.md5(b"g" * 4_000).hexdigest())
    assert [updates.presence(st, u).why for st in stores] == [updates.MATCHES,
                                                              updates.MD5_DIFFERS]


@pytest.mark.parametrize("test_drive", [False, True])
def test_updates_copied_to_a_test_drive_are_not_the_device_s(tmp_path, monkeypatch, test_drive):
    """What was copied to a test drive is not kept as copied to the real device whose unit ID
    the drive carries."""
    from openexpress import updates

    noted = []
    monkeypatch.setattr(known_devices, "note_copied", lambda unit, keys: noted.append(unit))

    def install(updates_, storage, *args, report, **kwargs):
        report.written.append(types.SimpleNamespace(key="006-D0000-00.6.20", is_metadata=False))
        return report

    monkeypatch.setattr(updates, "_install", install)
    st = LocalStorage(make_root(tmp_path))
    st.test_drive = test_drive
    updates.install([], st, unit_id=UNIT)
    assert noted == ([] if test_drive else [UNIT])


# --- folders that cannot be listed, the latest count, one boot log line, a device forgotten -


def test_a_dump_left_out_for_want_of_room_is_told_it_did_not_fit(tmp_path, clock, monkeypatch,
                                                                capsys):
    """A dump left out of Copy Memory Dumps did not fit in the room left, whatever its own size:
    never told as larger than the limit."""
    from openexpress import cli

    monkeypatch.setattr(diagnostics, "LIMIT", 1_000)
    root = make_root(tmp_path, dumps=[("RAM_bak1.zip", 600), ("RAM.zip", 600)])
    st, dev = opened(root)
    assert diagnostics.copy_dumps(dev, st).not_copied == ["RAM_bak1.zip"]
    cli.main(["diagnostics", "--path", str(root), "--apply", "--dumps"])
    said = [x for x in capsys.readouterr().out.splitlines() if x.startswith("Not copied")]
    assert len(said) == 1 and "did not fit in" in said[0] and said[0].endswith("RAM_bak1.zip")


class Sized(Device):
    """Free space that grows when files are removed."""

    CAP = 10_000_000

    def free_space(self):
        used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        return self.CAP - 1_000_000 - used

    def capacity(self):
        return self.CAP


def test_after_the_command_line_counted_later_the_report_tells_the_pages_free_space(
        tmp_path, clock):
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200_000), ("RAM.zip", 300)])
    st = Sized(root)
    dev = opened(root)[1]
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    s = session_of(st, dev, found)
    s.space = (st.free_space(), st.capacity())  # as the window read it at the opening
    clock.current += timedelta(minutes=5)
    (root / "GARMIN" / "Debug" / "dump" / "RAM_bak1.zip").unlink()  # the command line removed
    history.after_removal(dev, st, {"logs": [("GARMIN/Debug/dump/RAM_bak1.zip", 200_000)]})
    page_free = status.told(s).free
    text = report.build(dev, st, space=status.space_now(s), found=found)  # as the window
    line = next(x for x in text.splitlines() if x.startswith("- Storage:"))
    assert page_free == st.free_space()  # the page tells the count of the command line
    assert report._size(page_free) in line, (page_free, line)


class DebugNotListed(Device):
    """A device whose folder Garmin/Debug cannot be listed (gvfs says an error, not a stall)."""

    def _list(self, real_path):
        if real_path.casefold() == "garmin/debug":
            raise DeviceError("Cannot list Garmin/Debug")
        return super()._list(real_path)


def test_error_logs_whose_folder_cannot_be_listed_are_not_read_not_none(tmp_path, clock):
    root = make_root(tmp_path, err=[trap("2026-10-20 22:24:06")])
    st = DebugNotListed(root)
    dev = opened(root)[1]
    found = diagnostics.at_open(dev, st)
    s = session_of(st, dev, found)
    t = status.told(s)
    # The error log holds a crash; it could not be read: "Error Logs Not Read", never
    # "no crash" (nor "No Diagnostic Data").
    assert found.errors_known is False, (found.errors_known, found.entries, t.has_diagnostics)


def test_the_command_line_says_the_error_logs_were_not_read_when_their_folder_fails(
        tmp_path, clock, capsys, monkeypatch):
    from openexpress import cli
    from openexpress.device import storage as storage_mod

    root = make_root(tmp_path, err=[trap("2026-10-20 22:24:06")])
    real = storage_mod.LocalStorage._list

    def _list(self, real_path):
        if real_path.casefold() == "garmin/debug":
            raise DeviceError("Cannot list Garmin/Debug")
        return real(self, real_path)

    monkeypatch.setattr(storage_mod.LocalStorage, "_list", _list)
    assert cli.main(["diagnostics", "--path", str(root)]) == 0
    out = capsys.readouterr().out
    assert "not every error log could be read" in out and "no entries" not in out, out


def test_dumps_not_listed_at_the_opening_but_listed_by_the_page_are_not_no_data(
        tmp_path, clock):
    """Dumps not listed at the opening are not known, not none: the page that lists them is no page
    without diagnostic data."""
    root = make_root(tmp_path, dumps=[("RAM_bak2.zip", 100), ("RAM_bak1.zip", 200),
                                      ("RAM.zip", 300)])
    (root / "GARMIN" / "EVNTLOGS").mkdir()  # nothing hidden

    class Once(Device):
        failed = []

        def _list(self, real_path):
            if real_path.casefold() == "garmin/debug/dump" and not self.failed:
                self.failed.append(1)
                raise DeviceError("Cannot list")
            return super()._list(real_path)

    st = Once(root)
    dev = opened(root)[1]
    found = diagnostics.at_open(dev, st)
    s = session_of(st, dev, found)
    facts = status.read(s)
    assert facts.dumps and len(facts.dumps) == 3
    assert facts.has_diagnostics, ("No Diagnostic Data while the page lists 3 dumps",
                                   found.dumps_known, found.errors_known)


class GarminNotListed(Device):
    """The folder GARMIN cannot be listed once (gvfs says an error, not a stall)."""

    failed = []

    def _list(self, real_path):
        if real_path.casefold() == "garmin" and not self.failed:
            self.failed.append(1)
            raise DeviceError("Cannot list GARMIN")
        return super()._list(real_path)


def test_dumps_whose_folder_above_cannot_be_listed_are_not_known_not_none(tmp_path, clock):
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])
    GarminNotListed.failed = []
    st = GarminNotListed(root)
    dumps = None
    try:
        dumps = diagnostics.dumps_on_device(st)
    except DeviceError:
        dumps = "not known (raised)"
    assert dumps != [], "two dumps on the device, told as none"


def test_the_command_line_tells_dumps_not_known_as_the_page_does(tmp_path, clock, capsys,
                                                                monkeypatch):
    """A folder of the logs that cannot be listed: the command line says the dumps are not known, as
    the page does, and goes on."""
    from openexpress import cli
    from openexpress.device import storage as storage_mod

    root = make_root(tmp_path, err=[], dumps=[("RAM.zip", 300)])
    real = storage_mod.LocalStorage._list

    def _list(self, real_path):
        if real_path.casefold() == "garmin/debug":
            raise DeviceError("Cannot list Garmin/Debug")
        return real(self, real_path)

    monkeypatch.setattr(storage_mod.LocalStorage, "_list", _list)
    code = cli.main(["diagnostics", "--path", str(root)])
    out, err = capsys.readouterr()
    assert code == 0 and "Copies on this computer" in out, (code, out[-300:], err[-300:])


def test_a_count_that_could_not_read_the_free_space_tells_it_not_known(tmp_path, clock):
    """After a removal whose count could not ask the free space, the page, its row and the records all
    say it is not known."""
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])

    class NoFree(Device):
        asked = []

        def free_space(self):
            if self.asked:
                from openexpress.device.storage import DeviceError as E
                raise E("free space not told")
            self.asked.append(1)
            return 4_000_000

    st = NoFree(root)
    dev = opened(root)[1]
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)  # free 4_000_000 at the connection
    s = session_of(st, dev, found)
    s.space = (4_000_000, 10_000_000)
    clock.current += timedelta(minutes=1)
    (root / "GARMIN" / "Debug" / "dump" / "RAM_bak1.zip").unlink()  # removed
    status.counted_again(s, status.recount(dev, st, removed={
        "logs": [("GARMIN/Debug/dump/RAM_bak1.zip", 200)]}))
    page = status.told(s).free
    records = status.read_kept(UNIT).free
    assert page == records, f"page {page}, records {records}"
    assert page is None, f"the page tells {page} free, the free space of before the removal"


def test_the_page_from_the_records_tells_a_crash_as_the_connected_page_did(tmp_path, clock):
    """With the copy of the diagnostic files off, the device log alone tells a crash on the page from
    the records as the connected page did, with its software version."""
    root = make_root(tmp_path, err=[trap("2026-10-20 22:00:00")])
    st, dev = opened(root)
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    live = status.read(session_of(st, dev, found)).crashes
    kept = status.read_kept(UNIT).crashes
    assert len(live) == len(kept) == 1
    assert (live[0].software, live[0].error) == (kept[0].software, kept[0].error), (
        f"connected: software {live[0].software}; from the records: {kept[0].software}")


def test_a_crash_kept_only_in_a_copy_does_not_join_the_crash_before_it_through_a_boot_line(
        tmp_path, clock):
    """A boot log line joins one crash: the next crash 40 s later, kept only in a copy, stays a crash
    of its own."""
    import json

    from test_status_sources import kept_copy

    from openexpress import crashes

    root = make_root(tmp_path, err=[trap("2026-10-20 22:00:00")])
    st, _dev = opened(root)
    kept_copy("files", {"GARMIN/Debug/err_log.txt": json.dumps([trap("2026-10-20 22:00:40")])
                        .encode()}, when="2026-10-20 23.00.00")
    kept_copy("crash logs", {"Garmin/Debug/recovery_mode_log.txt":
                             b"[10/20/26 22:00:05 1]: RTL Trap detected, errnum: 0x1a2b\n"},
              when="2026-10-20 23.30.00")
    found = crashes.history(UNIT, diagnostics.read_errors(st))
    assert len(found) == 2, [(c.at, sorted(c.sources)) for c in found]


def test_two_crashes_of_the_two_error_logs_stay_two_also_with_the_boot_line_of_the_first(clock):
    from datetime import datetime, timedelta

    from openexpress import crashes
    from openexpress.diagnostics import ErrorEntry

    t = datetime(2026, 10, 20, 10, 0, 0)
    a = ErrorEntry("RTL Trap", t, "0x003", "0x10000abc", None, "err_log.bak")
    b = ErrorEntry("RTL Trap", t + timedelta(seconds=40), "0x003", "0x10000abc", None,
                   "err_log.txt")
    found = [crashes._from_entry(a, "err_log.bak"), crashes._from_entry(b, "err_log.txt"),
             crashes.Crash(t + timedelta(seconds=5), errnum="0x1a2b", sources={"boot log"})]
    merged = crashes.merge(found)
    assert len(merged) == 2, [(c.at, sorted(c.sources)) for c in merged]


def test_the_reading_at_the_opening_does_not_make_a_forgotten_devices_folder_again(
        tmp_path, clock, monkeypatch):
    root = make_root(tmp_path, err=[trap("2026-10-20 22:24:06")], dumps=[("RAM.zip", 300)])
    st, dev = opened(root)
    history.note_connection(dev, st)  # an earlier connection: its folder exists
    assert paths.device_dir(UNIT).exists()
    real_look = diagnostics._look

    def forgotten_meanwhile(*a, **kw):
        out = real_look(*a, **kw)
        forget._delete(UNIT, discard_pending=True, backups=False)  # Forget Device meanwhile
        return out

    monkeypatch.setattr(diagnostics, "_look", forgotten_meanwhile)
    found = diagnostics.at_open(dev, st)  # the window's reading of the device just unplugged
    history.note_connection(dev, st, found=found)
    left = sorted(p.name for p in paths.device_dir(UNIT).rglob("*")) \
        if paths.device_dir(UNIT).exists() else []
    assert not paths.device_dir(UNIT).exists(), f"made again with {left}"


def test_the_copy_at_the_opening_does_not_make_a_forgotten_devices_folder_again(
        tmp_path, clock, monkeypatch):
    from openexpress.settings import Settings

    s = Settings.load()
    s.diagnostics_copy = True
    s.save()
    root = make_root(tmp_path, err=[trap("2026-10-20 22:24:06")])
    st, dev = opened(root)
    real = diagnostics._visible_files

    def forgotten_meanwhile(*a, **kw):
        out = real(*a, **kw)
        forget._delete(UNIT, discard_pending=True, backups=False)
        return out

    monkeypatch.setattr(diagnostics, "_visible_files", forgotten_meanwhile)
    monkeypatch.setattr(diagnostics, "seen_now",
                        lambda device, storage, with_files, began=None: diagnostics.Seen(
                            {}, [], None, False))
    diagnostics.at_open(dev, st)
    left = sorted(p.name for p in paths.device_dir(UNIT).rglob("*")) \
        if paths.device_dir(UNIT).exists() else []
    assert not paths.device_dir(UNIT).exists(), f"made again with {left}"


def test_a_device_forgotten_then_connected_again_has_a_line(tmp_path, clock):
    """A device forgotten, then plugged in again in the same run of the window, gets the line of this
    connection."""
    root = make_root(tmp_path, err=[])
    st, dev = opened(root, address=None, bus=None)
    assert history.note_connection(dev, st)
    forget._delete(UNIT, discard_pending=True, backups=False)  # unplugged, then forgotten
    st2, dev2 = opened(root, address=None, bus=None)  # plugged in again
    history.note_connection(dev2, st2)
    assert history.read(UNIT).lines, "no line: the records of the device stay empty"


CHILD = textwrap.dedent("""
    import sys, time, types
    from pathlib import Path
    from openexpress import history
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    root, began, go = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])

    class Slow(LocalStorage):
        mass_storage = False
        def __init__(self, root):
            super().__init__(root)
            self.usb = types.SimpleNamespace(bus=1, address=7)
        def capacity(self):
            began.write_text("counting")
            while not go.exists():
                time.sleep(0.05)
            return 10_000_000
        def free_space(self):
            return 4_000_000

    st = Slow(root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    print(history.note_connection(dev, st))
""")


def test_a_line_begun_in_another_process_does_not_bring_a_forgotten_device_back(tmp_path,
                                                                               clock):
    root = make_root(tmp_path, err=[])
    (paths.device_dir(UNIT)).mkdir(parents=True)  # the device is remembered: its folder
    began, go = tmp_path / "began", tmp_path / "go"
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(root), str(began), str(go)],
                             cwd=os.getcwd(), env=dict(os.environ), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    end = time.monotonic() + 20
    while not began.exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert began.exists(), child.stderr.read() if child.poll() is not None else "no start"
    forget._delete(UNIT, discard_pending=True, backups=False)  # Forget Device in the window
    assert not paths.device_dir(UNIT).exists()
    go.write_text("go")
    out, err = child.communicate(timeout=30)
    left = sorted(p.name for p in paths.device_dir(UNIT).rglob("*")) \
        if paths.device_dir(UNIT).exists() else []
    assert not paths.device_dir(UNIT).exists(), (f"made again by the other process: {left}",
                                                 out, err[-500:])


# --- a removal put right after Forget Device, a boot log line that disagrees ------------------


def test_a_removal_put_right_after_forget_device_leaves_the_records_alone(tmp_path, clock,
                                                                         monkeypatch):
    """A removal not counted again waits for the lock of the records while Forget Device holds
    it: begun before the device was forgotten, it never changes the records kept after (a line
    of the same connection written again once it was forgotten)."""
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200), ("RAM.zip", 300)])
    st, dev = opened(root)
    assert history.note_connection(dev, st)
    later = history.read(UNIT).lines
    real_lock = history.device_locked
    waits = threading.Event()

    @contextmanager
    def lock_asked(unit_id):
        waits.set()
        with real_lock(unit_id):
            yield

    monkeypatch.setattr(history, "device_locked", lock_asked)
    put_right = []
    removal = threading.Thread(target=lambda: put_right.append(history.note_removed(
        dev, st, [("GARMIN/Debug/dump/RAM_bak1.zip", 200)], "logs")))
    with real_lock(UNIT):  # Forget Device
        removal.start()
        assert waits.wait(10), "the removal never asked for the lock of the records"
        shutil.rmtree(paths.device_dir(UNIT))
        history.forgotten(UNIT)
        write_lines(*later)  # the records kept after it: the connection's line written again
    removal.join(10)
    assert history.read(UNIT).lines == later
    assert put_right == [False]


def test_a_boot_log_line_whose_error_number_disagrees_is_a_crash_of_its_own():
    """A boot log's line joins a crash of another source within a minute only when their codes
    agree: a crash that tells another error number is another crash."""
    def told(errnum):
        return crashes.Crash(_AT, errnum=errnum, sources={"err_log.txt"})

    line = crashes.Crash(_AT + timedelta(seconds=40), errnum="0x1a2b",
                         sources={crashes.BOOT_LOG})
    assert len(crashes.merge([told("0x1a2b"), line])) == 1
    assert len(crashes.merge([told("0x0bad"), line])) == 2


# --- Refresh after a change outside the window, a count that meets a device unplugged ---------


def test_refresh_after_files_removed_outside_the_window_shows_the_free_space_of_now(
        tmp_path, clock):
    """A count put this connection's line right, then files were removed outside the window:
    after Refresh the row of the device page and the page tell the free space read now, not
    the one counted before."""
    root = make_root(tmp_path, err=[], dumps=[("RAM.zip", 300)])
    music = root / "Music"
    music.mkdir()
    (music / "made-up-song.mp3").write_bytes(b"m" * 2_000_000)
    st = Sized(root)
    dev = opened(root)[1]
    # The opening (detection.with_space, then read_diagnostics).
    s1 = session_of(st, dev, None)
    s1.space = (st.free_space(), st.capacity())
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    s1.diagnostics = found
    # A write of the window, then its count (window_tasks.count_again).
    clock.current += timedelta(minutes=2)
    (music / "made-up-song-2.mp3").write_bytes(b"n" * 100_000)
    status.counted_again(s1, status.recount(dev, st))
    counted_free = st.free_space()
    # Removed with the file manager meanwhile.
    clock.current += timedelta(minutes=5)
    (music / "made-up-song.mp3").unlink()
    now_free = st.free_space()
    assert now_free == counted_free + 2_000_000
    # Refresh: a new session of the same connection, its space read now (with_space), its
    # reading at the opening the one of this connection (at_open, once per connection).
    clock.current += timedelta(minutes=1)
    s2 = session_of(st, dev, diagnostics.at_open(dev, st))
    status.space_read(s2, (st.free_space(), st.capacity()))  # as detection.with_space
    history.note_connection(dev, st, found=s2.diagnostics)
    shown_row = status.space_now(s2)[0]
    shown_page = status.told(s2).free
    assert shown_row == now_free, (
        f"device page row after Refresh: {shown_row} free; the device has {now_free} "
        f"(the line counted {counted_free} before the change)")
    assert shown_page == now_free, f"page after Refresh: {shown_page}, device: {now_free}"


class Unpluggable(Device):
    """A made-up watch over MTP; once `gone`, every question gets gvfs's answer for a device
    that is no longer there (an error at once)."""

    gone = False
    CAP = 10_000_000

    def _check(self):
        if self.gone:
            raise DeviceError("Cannot list: The specified location is not mounted")

    def _list(self, real_path):
        self._check()
        return super()._list(real_path)

    def info(self, real_path):
        self._check()
        return super().info(real_path)

    def _read(self, real_path):
        self._check()
        return super()._read(real_path)

    def capacity(self):
        if self.gone:
            return None  # gio_storage._fs_attr: a DeviceError is "not known"
        return self.CAP

    def free_space(self):
        if self.gone:
            return None
        used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        return self.CAP - 1_000_000 - used


def test_a_removal_whose_count_meets_an_unplugged_device_puts_the_line_right(tmp_path, clock):
    """When the count after a removal tells nothing because the device was unplugged, the line
    of this connection is put right by what was removed (the command line prints the removal
    before the count: unplugging then is a normal moment)."""
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200_000), ("RAM.zip", 300)])
    st = Unpluggable(root)
    dev = opened(root)[1]
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    before = status.read_kept(UNIT)
    assert before.dumps_at_last == (2, 200_300) and before.free is not None
    clock.current += timedelta(minutes=1)
    freed = freeing.remove_dumps(st, UNIT, freeing.dump_plan(
        [(p, e.size, e.mtime) for p, e in diagnostics.dump_files(st)]))
    assert freed.removed == [("GARMIN/Debug/dump/RAM_bak1.zip", 200_000)]
    st.gone = True  # "Removed: 1 memory dump, 200.0 kB freed." printed: the user unplugs
    with pytest.raises(history.NotCounted):  # cli._tell_freed says it could not count
        history.after_removal(dev, st, {"logs": freed.removed})
    after = status.read_kept(UNIT)
    assert after.dumps_at_last == (1, 300), (
        f"records after the removal: dumps {after.dumps_at_last}, free {after.free}, "
        f"categories {after.categories}")
    assert after.free == before.free + 200_000, after.free


def test_the_window_s_removal_whose_count_meets_an_unplugged_device(tmp_path, clock):
    """The same through the window's work (status_page._then_counted -> status.recount)."""
    root = make_root(tmp_path, err=[], dumps=[("RAM_bak1.zip", 200_000), ("RAM.zip", 300)])
    st = Unpluggable(root)
    dev = opened(root)[1]
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    before = status.read_kept(UNIT)
    clock.current += timedelta(minutes=1)
    plan = freeing.dump_plan([(p, e.size, e.mtime) for p, e in diagnostics.dump_files(st)])
    freed = freeing.remove_dumps(st, UNIT, plan)
    st.gone = True  # unplugged once the last dump was removed
    with pytest.raises(history.NotCounted):  # _then_counted tells "Removal Not Finished"
        status.recount(dev, st, removed={"logs": freed.removed})
    after = status.read_kept(UNIT)
    assert after.dumps_at_last == (1, 300) and after.free == before.free + 200_000
