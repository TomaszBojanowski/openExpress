"""What this installation remembers of the Garmin devices it has seen (D1): devices.json in
the data folder. All unit IDs here are made up."""

import calendar
import logging
import stat
from pathlib import Path

import pytest

FENIX, EDGE = 3000000001, 3000000002


@pytest.fixture
def store(tmp_path):
    """The file of these tests: XDG_DATA_HOME is tmp_path/data (conftest)."""
    from openexpress import known_devices

    path = known_devices.path()
    assert path == tmp_path / "data" / "openexpress" / "devices.json"
    return path


def test_a_device_is_remembered_and_read_back(store):
    """The entry of a device: what GarminDevice.xml said, how it was connected, when it was
    first and last seen; seen again, it is updated and keeps when it was first seen."""
    from openexpress import known_devices

    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=100.0)
    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.40", "mtp", now=200.0)
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=150.0)
    known = known_devices.load()
    assert set(known) == {FENIX, EDGE}
    fenix = known[FENIX]
    assert (fenix.unit_id, fenix.description, fenix.part_number, fenix.software_version,
            fenix.kind, fenix.first_seen, fenix.last_seen) == (
        FENIX, "fēnix 8", "006-B4532-00", "23.40", "mtp", 100.0, 200.0)
    assert known[EDGE].first_seen == known[EDGE].last_seen == 150.0


def test_no_file_is_nothing_remembered(store):
    """No devices.json yet: nothing remembered, no error, and reading makes no file."""
    from openexpress import known_devices

    assert known_devices.load() == {}
    assert not store.exists()


def test_a_damaged_file_is_left_alone(store, caplog):
    """A damaged devices.json: nothing is remembered from it, it is said in the log, and it
    is never overwritten: not by reading, not by remembering, not by forgetting."""
    from openexpress import known_devices

    store.parent.mkdir(parents=True)
    store.write_text('{"version": 1, "devices": {"3000000001": {"description": ')
    damaged = store.read_bytes()
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert known_devices.load() == {}
        known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=1.0)
        assert known_devices.forget(FENIX) is False
    assert store.read_bytes() == damaged
    assert "devices.json is damaged" in caplog.text


def test_forget_removes_the_entry_and_nothing_else(store):
    """Forgetting removes the entry from devices.json, and nothing of the device's own
    folder (its sync state, own maps…); other entries stay."""
    from openexpress import known_devices, paths

    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=1.0)
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=2.0)
    sync_state = paths.device_dir(FENIX) / "sync" / "state.json"
    sync_state.parent.mkdir(parents=True)
    sync_state.write_text("{}")
    assert known_devices.forget(FENIX) is True
    assert set(known_devices.load()) == {EDGE}
    assert sync_state.read_text() == "{}"
    assert known_devices.forget(FENIX) is False  # not remembered any more


def test_the_unit_id_is_never_in_the_log(store, caplog):
    """The log names the part number, never the unit ID; nor does the entry's repr."""
    from openexpress import known_devices

    with caplog.at_level(logging.DEBUG, logger="openexpress"):
        entry = known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp",
                                       now=1.0)
        known_devices.load()
        known_devices.forget(FENIX)
    assert "devices: remembered 006-B4532-00" in caplog.text
    assert "devices: forgot 006-B4532-00" in caplog.text
    assert str(FENIX) not in caplog.text and str(FENIX) not in repr(entry)


def test_the_file_is_the_owners_alone(store):
    """devices.json is 0600, in the data folder, which is the owner's alone (0700)."""
    from openexpress import known_devices

    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=1.0)
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700


def test_the_flatpak_keeps_its_own_file(tmp_path, monkeypatch):
    """In the Flatpak XDG_DATA_HOME is ~/.var/app/<id>/data: its own devices.json."""
    from openexpress import known_devices

    sandbox = tmp_path / ".var" / "app" / "io.github.tomaszbojanowski.OpenExpress" / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(sandbox))
    assert known_devices.path() == sandbox / "openexpress" / "devices.json"


# --- the lock of devices.json (D2) ------------------------------------------------------------

HOLDER = """
import sys
from openexpress import known_devices

write = known_devices._write


def slow_write(known):
    # Read, not yet written: the moment another process could slip in.
    print("read", flush=True)
    sys.stdin.readline()
    write(known)


known_devices._write = slow_write
known_devices.remember(3000000001, "fenix 8", "006-B4532-00", "23.39", "mtp", now=1.0)
print("done", flush=True)
"""


def holder(tmp_path):
    """Another process (e.g. the background service) remembering the fēnix, stopped between
    reading devices.json and writing it."""
    import os
    import subprocess
    import sys

    from openexpress import known_devices

    script = tmp_path / "holder.py"
    script.write_text(HOLDER)
    env = {**os.environ, "PYTHONPATH": str(Path(known_devices.__file__).parent.parent)}
    proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    assert proc.stdout.readline().strip() == "read"
    return proc


def test_two_processes_remembering_at_once_keep_both(store, tmp_path):
    """The other process has read devices.json and is about to write it; this one remembers
    the Edge meanwhile. With the lock the Edge waits and reads the fēnix in: both entries
    stay (without it, the other process would write over the Edge)."""
    import threading

    from openexpress import known_devices

    proc = holder(tmp_path)
    edge = threading.Thread(target=known_devices.remember,
                            args=(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", 2.0))
    edge.start()
    edge.join(0.5)  # it waits for the other process (with the lock)
    proc.stdin.write("\n")
    proc.stdin.flush()
    assert proc.stdout.readline().strip() == "done" and proc.wait(timeout=20) == 0
    edge.join(10)
    assert not edge.is_alive()
    assert set(known_devices.load()) == {FENIX, EDGE}


def test_the_lock_goes_with_the_process(store, tmp_path):
    """A process that holds the lock and ends (here: killed between reading and writing)
    leaves nothing behind: remembering goes on at once."""
    import threading

    from openexpress import known_devices

    proc = holder(tmp_path)
    proc.kill()
    proc.wait(timeout=20)
    edge = threading.Thread(target=known_devices.remember,
                            args=(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", 2.0))
    edge.start()
    edge.join(10)
    assert not edge.is_alive()
    assert set(known_devices.load()) == {EDGE}


# --- `openexpress devices` (D4) -----------------------------------------------------------------

SEEN_FENIX = float(calendar.timegm((2026, 10, 17, 0, 0, 0)))  # UTC
SEEN_VARIA = float(calendar.timegm((2026, 10, 17, 2, 20, 0)))


@pytest.fixture
def utc():
    """Local time is UTC in these tests, whatever the computer says."""
    import os
    import time

    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    time.tzset()


def two_remembered():
    from openexpress import known_devices

    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=SEEN_FENIX)
    known_devices.remember(3000000003, "Varia RTL515", "006-B3592-03", "3.36", "ums",
                           now=SEEN_VARIA)


def test_devices_lists_what_is_remembered(store, utc, capsys):
    """Numbered, the device seen last first: name, part number, version, how it was connected,
    when it was seen; never the unit ID."""
    from openexpress.cli import main

    two_remembered()
    assert main(["devices"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "Devices this installation has seen:",
        "  1. Varia RTL515 · 006-B3592-03 · Software 3.36 · USB drive · Last seen 2026-10-17 02:20",
        "  2. fēnix 8 · 006-B4532-00 · Software 23.39 · Over USB (MTP) · Last seen 2026-10-17 00:00"]
    assert str(FENIX) not in out and "3000000003" not in out


def test_devices_with_nothing_remembered(store, capsys):
    """No device remembered yet: said so, exit code 0."""
    from openexpress.cli import main

    assert main(["devices"]) == 0
    assert capsys.readouterr().out.strip() == "No devices are remembered yet."


def test_forget_without_apply_forgets_nothing(store, utc, capsys):
    """--forget N: which device would be forgotten; nothing is."""
    from openexpress import known_devices
    from openexpress.cli import main

    two_remembered()
    assert main(["devices", "--forget", "2"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Device to forget: fēnix 8 (006-B4532-00)",
        "Dry run: nothing was forgotten. Add --apply to forget it."]
    assert set(known_devices.load()) == {FENIX, 3000000003}


def test_forget_with_apply_removes_the_entry_only(store, utc, capsys):
    """--forget N --apply: the entry goes; the device's own folder (its sync state) stays."""
    from openexpress import known_devices, paths
    from openexpress.cli import main

    two_remembered()
    sync_state = paths.device_dir(FENIX) / "sync" / "state.json"
    sync_state.parent.mkdir(parents=True)
    sync_state.write_text("{}")
    assert main(["devices", "--forget", "2", "--apply"]) == 0
    assert capsys.readouterr().out.strip() == "Forgot fēnix 8 (006-B4532-00)."
    assert set(known_devices.load()) == {3000000003}
    assert sync_state.read_text() == "{}"


@pytest.mark.parametrize("number", ["3", "0"])
def test_forget_a_number_not_on_the_list(store, utc, capsys, number):
    """A number that is not on the list: an error, exit code 1, nothing forgotten."""
    from openexpress import known_devices
    from openexpress.cli import main

    two_remembered()
    assert main(["devices", "--forget", number, "--apply"]) == 1
    assert capsys.readouterr().err.strip() == (
        f"Error: There is no device number {number} on the list (it has 2).")
    assert set(known_devices.load()) == {FENIX, 3000000003}
