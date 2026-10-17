"""What this installation remembers of the Garmin devices it has seen (D1): devices.json in
the data folder. All unit IDs here are made up."""

import logging
import stat

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
