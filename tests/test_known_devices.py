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


FORGOTTEN_WITH_IT = (
    "What this installation keeps of it goes too: what was uploaded from it, its last sync, "
    "the maps of your own copied to it and the copies of its diagnostic files. Its next sync "
    "with this program will be a first sync again.")


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
        "  2. fēnix 8 · 006-B4532-00 · Software 23.39 · MTP · Last seen 2026-10-17 00:00"]
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
        FORGOTTEN_WITH_IT,
        "Dry run: nothing was forgotten. Add --apply to forget it."]
    assert set(known_devices.load()) == {FENIX, 3000000003}


def test_forget_with_apply_removes_the_entry_and_the_sync_state(store, utc, capsys):
    """--forget N --apply: the entry goes, and with it the device's own folder (its sync
    state): connected again, it is a new device (PLAN T7)."""
    from openexpress import known_devices, paths
    from openexpress.cli import main

    two_remembered()
    sync_state = paths.device_dir(FENIX) / "sync" / "state.json"
    sync_state.parent.mkdir(parents=True)
    sync_state.write_text("{}")
    assert main(["devices", "--forget", "2", "--apply"]) == 0
    assert capsys.readouterr().out.strip() == "Forgot fēnix 8 (006-B4532-00)."
    assert set(known_devices.load()) == {3000000003}
    assert not paths.device_dir(FENIX).exists()


def pending_of(unit_id, count):
    from openexpress import paths

    for n in range(count):
        path = paths.device_dir(unit_id) / "sync" / "pending" / "FIT_TYPE_4" / f"ACT{n}.FIT"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fit")


def test_forget_names_the_files_not_uploaded_and_refuses_them_without_consent(
        store, utc, capsys):
    """Files not uploaded yet: the dry run says they would be lost; --apply alone refuses
    (exit code 1, nothing forgotten); with --discard-pending they go."""
    from openexpress import known_devices
    from openexpress.cli import main

    two_remembered()
    pending_of(FENIX, 2)
    assert main(["devices", "--forget", "2"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Device to forget: fēnix 8 (006-B4532-00)",
        FORGOTTEN_WITH_IT,
        ("2 files of this device have not been uploaded yet and would be lost. "
         "Add --discard-pending to forget it anyway."),
        "Dry run: nothing was forgotten. Add --apply to forget it."]
    assert main(["devices", "--forget", "2", "--discard-pending"]) == 0
    assert capsys.readouterr().out.splitlines()[2] == (
        "2 files of this device have not been uploaded yet and would be lost.")
    assert main(["devices", "--forget", "2", "--apply"]) == 1
    assert capsys.readouterr().err.strip() == (
        "Error: 2 files of this device have not been uploaded yet and would be lost. "
        "Add --discard-pending to forget it anyway.")
    assert set(known_devices.load()) == {FENIX, 3000000003}
    assert main(["devices", "--forget", "2", "--discard-pending", "--apply"]) == 0
    assert capsys.readouterr().out.strip() == "Forgot fēnix 8 (006-B4532-00)."
    assert set(known_devices.load()) == {3000000003}


def test_forget_keeps_the_backups_unless_asked(store, utc, capsys, backups):
    """The backups stay, and the dry run says where (the folder above the device's, which is
    named after the unit ID); --backups deletes them too."""
    from openexpress import known_devices
    from openexpress.cli import main

    two_remembered()
    for name in ("2026-10-01 (10.00.00)", "GARMIN-APPS 2026-10-03 (10.00.00) fēnix 8"):
        (backups / str(FENIX) / name).mkdir(parents=True)
    assert main(["devices", "--forget", "2"]) == 0
    assert capsys.readouterr().out.splitlines()[2] == (
        f"2 backups of it stay in {backups} (add --backups to delete them too).")
    assert main(["devices", "--forget", "2", "--backups"]) == 0
    assert capsys.readouterr().out.splitlines()[2] == (
        f"2 backups of it in {backups} are deleted too.")
    assert main(["devices", "--forget", "2", "--apply"]) == 0
    assert (backups / str(FENIX)).is_dir()
    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=SEEN_FENIX)
    capsys.readouterr()
    assert main(["devices", "--forget", "2", "--backups", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Forgot fēnix 8 (006-B4532-00).", "Deleted 2 backups."]
    assert not (backups / str(FENIX)).exists()


@pytest.mark.parametrize("apply", [[], ["--apply"]])
def test_a_connected_device_is_not_forgotten(store, utc, capsys, monkeypatch, apply):
    """Connected now (its GarminDevice.xml names its unit ID): unplug it first; exit code 1,
    nothing forgotten, also in a dry run."""
    from openexpress import cli, known_devices

    two_remembered()
    monkeypatch.setattr(cli, "_connected_unit_ids", lambda: {FENIX})
    assert cli.main(["devices", "--forget", "2", *apply]) == 1
    assert capsys.readouterr().err.strip() == (
        "Error: fēnix 8 (006-B4532-00) is connected: unplug it to forget it.")
    assert set(known_devices.load()) == {FENIX, 3000000003}


def test_forget_while_a_sync_runs(store, utc, capsys):
    """A sync running holds the sync lock: an error, exit code 1, nothing forgotten."""
    from openexpress import known_devices, service
    from openexpress.cli import main

    two_remembered()
    with service.sync_lock():
        assert main(["devices", "--forget", "2", "--apply"]) == 1
    assert capsys.readouterr().err.strip() == (
        "Error: A sync is already running in another process.")
    assert set(known_devices.load()) == {FENIX, 3000000003}


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


# --- the kind of a device (T5.1) ---------------------------------------------------------------


def test_the_kind_is_kept_and_optional(store, caplog):
    """The kind is an optional field: a file without it is read as before (no kind), a
    device without a kind is written without the field; set_category keeps it, a device
    seen again keeps it, an unknown value counts as none. The log names the part number."""
    import json

    from openexpress import known_devices

    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"version": 1, "devices": {str(EDGE): {
        "description": "Edge 1050", "part_number": "006-B4440-00", "software_version": "33.11",
        "kind": "mtp", "first_seen": 1.0, "last_seen": 1.0}}}))
    assert known_devices.load()[EDGE].category is None
    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=2.0)
    assert "category" not in json.loads(store.read_text())["devices"][str(FENIX)]
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert known_devices.set_category(EDGE, "cycling-computer") is True
    assert "devices: 006-B4440-00 is a cycling-computer" in caplog.text
    assert str(EDGE) not in caplog.text
    assert known_devices.set_category(EDGE, "cycling-computer") is False  # no change
    assert known_devices.set_category(3000000009, "watch") is False  # not remembered
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.12", "mtp", now=3.0)
    assert known_devices.load()[EDGE].category == "cycling-computer"
    data = json.loads(store.read_text())
    data["devices"][str(EDGE)]["category"] = "toaster"
    store.write_text(json.dumps(data))
    assert known_devices.load()[EDGE].category is None


VENU = 3000000004
PICTURE = "https://res.garmin.com/en/products/010-02430-11/g/cf-lg.png"
NEWER_PICTURE = "https://static.garmincdn.com/pumac/venu2.jpg"


def test_the_address_of_a_picture_is_kept_and_optional(store, caplog):
    """The address of the picture of a model (Garmin's: from the account, or its update
    server for a device off the account) is an optional field, as the kind: a file without it
    is read as before, a device without one is written without the field; set_image_url keeps
    it, a newer address replaces it, a device seen again keeps it. The log names the part
    number and the host, never the unit ID or the whole address."""
    import json

    from openexpress import known_devices

    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"version": 1, "devices": {str(VENU): {
        "description": "Venu 2", "part_number": "006-B3703-00", "software_version": "19.05",
        "kind": "mtp", "first_seen": 1.0, "last_seen": 1.0}}}))
    assert known_devices.load()[VENU].image_url is None
    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=2.0)
    assert "image_url" not in json.loads(store.read_text())["devices"][str(FENIX)]
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert known_devices.set_image_url(VENU, PICTURE) is True
    assert "devices: the picture of 006-B3703-00 is at res.garmin.com" in caplog.text
    assert str(VENU) not in caplog.text and "010-02430-11" not in caplog.text
    assert known_devices.set_image_url(VENU, PICTURE) is False  # no change
    assert known_devices.set_image_url(VENU, NEWER_PICTURE) is True
    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.06", "mtp", now=3.0)
    assert known_devices.load()[VENU].image_url == NEWER_PICTURE
    assert json.loads(store.read_text())["devices"][str(VENU)]["image_url"] == NEWER_PICTURE


@pytest.mark.parametrize("url, logged", [
    (PICTURE, "devices: the picture of 006-B3703-00 is at res.garmin.com; no query"),
    ("https://s3.amazonaws.com/garmin-connect-prod/devices/venu2.png?X-Amz-Signature=secret",
     "devices: the picture of 006-B3703-00 is at s3.amazonaws.com; with query"),
])
def test_the_log_says_whether_a_kept_address_has_a_query(store, caplog, url, logged):
    """The host and the fact of a query, never the path or the query itself (a signed address
    carries its token there)."""
    from openexpress import known_devices

    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp")
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert known_devices.set_image_url(VENU, url) is True
    assert logged in caplog.text.splitlines()[-1]
    for private in ("secret", "Signature", "venu2", "/devices", "010-02430-11"):
        assert private not in caplog.text, private


@pytest.mark.parametrize("url", [
    "http://example.org/a.png", "https://example.org/a.png",
    "https://s3.amazonaws.com/garmin-bucket/a.png?X-Amz-Signature=secret", "", None])
def test_an_address_that_is_not_allowed_is_never_kept(store, url):
    """Only an address that picture_urls allows is kept (the one rule of the downloads); one
    written into the file by other means reads as none. A device not remembered gets no
    entry for it."""
    import json

    from openexpress import known_devices

    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", now=1.0)
    assert known_devices.set_image_url(VENU, PICTURE) is True
    assert known_devices.set_image_url(VENU, url) is False
    assert known_devices.load()[VENU].image_url == PICTURE  # the one before stays
    assert known_devices.set_image_url(3000000009, PICTURE) is False
    assert 3000000009 not in known_devices.load()
    data = json.loads(store.read_text())
    data["devices"][str(VENU)]["image_url"] = url or 7
    store.write_text(json.dumps(data))
    assert known_devices.load()[VENU].image_url is None


def test_an_address_over_plain_http_is_kept_over_https(store):
    """Plain HTTP from Garmin's host is asked over HTTPS, so it is kept so."""
    from openexpress import known_devices

    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", now=1.0)
    assert known_devices.set_image_url(VENU, "http://res.garmin.com/a.png") is True
    assert known_devices.load()[VENU].image_url == "https://res.garmin.com/a.png"


# --- an update found by the quiet check (PLAN T9, chips) ----------------------------------------


def test_an_update_found_is_kept_and_optional(store, caplog):
    """When the window's check last found an update for a device is an optional field, as
    the kind: a file without it is read as before (none found), a device without one is
    written without the field; set_update_found keeps the time of the check that found one
    and clears it when a check finds none; a device seen again keeps it; a value that is
    not a number counts as none. The log names the part number, never the unit ID."""
    import json

    from openexpress import known_devices

    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"version": 1, "devices": {str(EDGE): {
        "description": "Edge 1050", "part_number": "006-B4440-00", "software_version": "33.13",
        "kind": "mtp", "first_seen": 1.0, "last_seen": 1.0}}}))
    assert known_devices.load()[EDGE].update_found is None
    known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=2.0)
    assert "update_found" not in json.loads(store.read_text())["devices"][str(FENIX)]
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert known_devices.set_update_found(EDGE, True, now=500.0) is True
    assert "devices: an update for 006-B4440-00 found" in caplog.text
    assert str(EDGE) not in caplog.text
    assert known_devices.load()[EDGE].update_found == 500.0
    assert known_devices.set_update_found(EDGE, True, now=600.0) is True  # checked again
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.13", "mtp", now=700.0)
    assert known_devices.load()[EDGE].update_found == 600.0
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert known_devices.set_update_found(EDGE, False) is True
    assert "devices: no update waits for 006-B4440-00" in caplog.text
    assert known_devices.load()[EDGE].update_found is None
    assert "update" not in json.loads(store.read_text())["devices"][str(EDGE)]["tile_facts"]
    assert known_devices.set_update_found(EDGE, False) is False  # nothing to clear
    assert known_devices.set_update_found(3000000009, True) is False  # not remembered
    data = json.loads(store.read_text())
    data["devices"][str(EDGE)]["tile_facts"]["update"] = {"found": "soon"}
    store.write_text(json.dumps(data))
    assert known_devices.load()[EDGE].update_found is None


@pytest.mark.parametrize("age_days, recent", [(0, True), (29.9, True), (30.1, False)])
def test_an_update_found_more_than_30_days_ago_counts_as_none(age_days, recent):
    from openexpress import known_devices

    now = 1_800_000_000.0
    found = now - age_days * 86400
    assert known_devices.update_found_recently(found, now) == (found if recent else None)
    assert known_devices.update_found_recently(None, now) is None
