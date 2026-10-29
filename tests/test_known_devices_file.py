"""devices.json as known_devices reads and writes it: a pattern with every field (tests/data/
devices/devices.json, written by the program, without a newline at its end) read into the same
entries and written back byte for byte; what each change writes and logs, and when nothing is
written; a file that may not be written over (damaged, unreadable) never is. All unit IDs,
serials and addresses are made up."""

import json
import logging
import shutil
from pathlib import Path

import pytest

PATTERN = Path(__file__).parent / "data" / "devices" / "devices.json"
FENIX, VARIA, EPIX, OTHER = 3000000001, 3000000002, 3000000003, 3000000009
NEW_PICTURE = "https://static.garmincdn.com/pumac/fenix8.jpg"


@pytest.fixture
def store(tmp_path):
    """The pattern as this test's devices.json (XDG_DATA_HOME is tmp_path/data: conftest)."""
    from openexpress import known_devices

    path = known_devices.path()
    assert path == tmp_path / "data" / "openexpress" / "devices.json"
    path.parent.mkdir(parents=True)
    shutil.copyfile(PATTERN, path)
    return path


def _entries():
    """The pattern's entries, every field named."""
    from openexpress.known_devices import (
        AccountFact,
        KnownDevice,
        SyncCount,
        SyncedFact,
        UpdateCheck,
        UpdateSummary,
    )

    return {
        FENIX: KnownDevice(
            unit_id=FENIX, description="fēnix 8", part_number="006-B4532-00",
            software_version="23.39", kind="mtp", first_seen=1000.0, last_seen=2000.0,
            category="watch",
            image_url="https://res.garmin.com/en/products/010-02904-00/g/cf-lg.png",
            update_found=1900.0,
            update_summary=UpdateSummary(1, "23.39", "Map Region Grouping file", "2.31", False),
            update_check=UpdateCheck(1900.0, "23.39", False),
            sync_count=SyncCount(3, 1950.0, "state-1"),
            account=AccountFact(True, True, 1960.0, "login-1"),
            synced=SyncedFact(True, 1800.0, False, 1970.0, "state-1"),
            model_name="fēnix 8, 47 mm, AMOLED"),
        VARIA: KnownDevice(
            unit_id=VARIA, description="Varia RTL515", part_number="006-B3596-00",
            software_version="3.20", kind="ums", first_seen=1100.0, last_seen=2100.0,
            category="bike-accessory", update_found=2050.0,
            update_summary=UpdateSummary(2, "3.20"),
            update_check=UpdateCheck(2050.0, "3.20", True),
            usb_product="0f1d", usb_serial="MADE-UP-SERIAL-1", volume_uuid="1234-ABCD"),
        EPIX: KnownDevice(
            unit_id=EPIX, description="EPIX", part_number="006-B3943-00",
            software_version="21.19", kind="mtp", first_seen=1200.0, last_seen=1200.0,
            model_name="epix (Gen 2)"),
    }


def _pattern() -> dict:
    return json.loads(PATTERN.read_text(encoding="utf-8"))


def _said(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "openexpress.known_devices"]


def _as_written(data: dict) -> bytes:
    """`data` as the program writes devices.json."""
    return json.dumps(data, indent=1, ensure_ascii=False).encode()


# --- the pattern ------------------------------------------------------------------------------


def test_the_pattern_is_read_with_every_field(store):
    from openexpress import known_devices

    assert known_devices.load() == _entries()


def test_the_pattern_is_written_back_byte_for_byte(store):
    from openexpress import known_devices

    known_devices._write(known_devices.load())
    assert store.read_bytes() == PATTERN.read_bytes()


def test_the_entries_are_written_as_the_pattern(store):
    """Written from the entries alone, the file is the pattern: the order of the keys, the
    optional ones left out, the facts of the tile in their own object."""
    from openexpress import known_devices

    store.unlink()
    known_devices._write(_entries())
    assert store.read_bytes() == PATTERN.read_bytes()


# --- what each change writes ------------------------------------------------------------------


def _remember_again(known_devices):
    return known_devices.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=3000.0)


def _remember_the_drive(known_devices, serial, uuid):
    return known_devices.remember(VARIA, "Varia RTL515", "006-B3596-00", "3.21", "ums",
                                  now=3000.0, usb_serial=serial, volume_uuid=uuid)


def _fenix(data):
    return data["devices"][str(FENIX)]


def _varia(data):
    return data["devices"][str(VARIA)]


def _epix(data):
    return data["devices"][str(EPIX)]


def _seen_again(data):
    _fenix(data)["last_seen"] = 3000.0


def _drive_seen_again(data, serial="MADE-UP-SERIAL-1", uuid="1234-ABCD"):
    _varia(data).update(software_version="3.21", last_seen=3000.0, usb_serial=serial,
                        volume_uuid=uuid)


def _no_update_now(data):
    _fenix(data)["tile_facts"]["update"] = {
        "check": {"at": 4000.0, "software_version": "23.40", "ready": False}}


def _an_update_for_the_epix(data):
    _epix(data)["tile_facts"] = {"update": {
        "found": 4000.0,
        "summary": {"count": 1, "title": "Software", "version": "21.20", "is_system": True,
                    "software_version": "21.19"},
        "check": {"at": 4000.0, "software_version": "21.19", "ready": False}}}


def _counted(data):
    _fenix(data)["tile_facts"]["sync"] = {"count": 5, "at": 4100.0, "state": "state-2"}


def _synced(data):
    _fenix(data)["tile_facts"]["synced"] = {"once": True, "last": 4150.0,
                                            "not_uploaded": True, "at": 4160.0,
                                            "state": "state-2"}


def _account_read(data):
    _fenix(data)["tile_facts"]["account"] = {"on_account": True, "primary": False,
                                             "at": 4200.0, "login": "login-2"}
    _varia(data)["tile_facts"]["account"] = {"on_account": False, "primary": False,
                                             "at": 4200.0, "login": "login-2"}


def _changes():
    """(id, the change, what it returns, how the pattern changes, its log lines)."""
    from openexpress import known_devices as kd
    from openexpress.known_devices import AccountFact, SyncCount, SyncedFact, UpdateSummary

    return [
        ("remember", lambda: _remember_again(kd).last_seen, 3000.0, _seen_again,
         ["devices: remembered 006-B4532-00"]),
        ("remember-a-drive-without-its-keys", lambda: _remember_the_drive(kd, None, None).usb_serial,
         "MADE-UP-SERIAL-1", _drive_seen_again, ["devices: remembered 006-B3596-00"]),
        ("remember-a-drive-with-new-keys",
         lambda: _remember_the_drive(kd, "MADE-UP-SERIAL-2", "5678-EF01").volume_uuid,
         "5678-EF01", lambda d: _drive_seen_again(d, "MADE-UP-SERIAL-2", "5678-EF01"),
         ["devices: remembered 006-B3596-00"]),
        ("remember-a-drive-with-keys-not-kept",
         lambda: _remember_the_drive(kd, "with a space", "").usb_serial,
         "MADE-UP-SERIAL-1", _drive_seen_again, ["devices: remembered 006-B3596-00"]),
        ("model-name", lambda: kd.set_model_name(EPIX, "epix™ (Gen 2) — Sapphire Edition"), True,
         lambda d: _epix(d).update(model_name="epix (Gen 2), Sapphire Edition"),
         ["devices: the name of 006-B3943-00 from unit-info kept"]),
        ("category", lambda: kd.set_category(FENIX, kd.CYCLING_COMPUTER), True,
         lambda d: _fenix(d).update(category="cycling-computer"),
         ["devices: 006-B4532-00 is a cycling-computer"]),
        ("image-url", lambda: kd.set_image_url(FENIX, NEW_PICTURE), True,
         lambda d: _fenix(d).update(image_url=NEW_PICTURE),
         ["devices: the picture of 006-B4532-00 is at static.garmincdn.com; no query"]),
        ("no-update", lambda: kd.set_update_found(FENIX, False, now=4000.0,
                                                  software_version="23.40"), True,
         _no_update_now, ["devices: no update waits for 006-B4532-00"]),
        ("an-update", lambda: kd.set_update_found(
            EPIX, True, now=4000.0, summary=UpdateSummary(1, "21.19", "Software", "21.20", True),
            software_version="21.19"), True, _an_update_for_the_epix,
         ["devices: an update for 006-B3943-00 found"]),
        ("sync-count", lambda: kd.set_sync_count(FENIX, SyncCount(5, 4100.0, "state-2")), True,
         _counted, []),
        ("synced", lambda: kd.set_synced(FENIX, SyncedFact(True, 4150.0, True, 4160.0,
                                                           "state-2")), True, _synced, []),
        ("account", lambda: kd.set_account_facts({
            FENIX: AccountFact(True, False, 4200.0, "login-2"),
            VARIA: AccountFact(False, False, 4200.0, "login-2"),
            OTHER: AccountFact(True, False, 4200.0, "login-2")}), 2, _account_read, []),
        ("forget", lambda: kd.forget(EPIX), True,
         lambda d: d["devices"].pop(str(EPIX)), ["devices: forgot 006-B3943-00"]),
    ]


@pytest.mark.parametrize("index", range(13), ids=[
    "remember", "remember-a-drive-without-its-keys", "remember-a-drive-with-new-keys",
    "remember-a-drive-with-keys-not-kept", "model-name", "category", "image-url", "no-update",
    "an-update", "sync-count", "synced", "account", "forget"])
def test_each_change_writes_only_what_it_changes(store, caplog, index):
    """Each change of an entry: what it returns, the file as the pattern with only that
    change, byte for byte, and its line in the log (none for the counts and the facts of the
    account, which have lines of their own elsewhere)."""
    name, change, returned, edit, said = _changes()[index]
    expected = _pattern()
    edit(expected)
    with caplog.at_level(logging.INFO, logger="openexpress.known_devices"):
        assert change() == returned
    assert store.read_bytes() == _as_written(expected), name
    assert _said(caplog) == said


@pytest.mark.parametrize("found, ready, said", [
    (True, True, "devices: an update for 006-B4532-00 found, another copied already"),
    (False, True, "devices: an update for 006-B4532-00 copied already, it waits to be installed"),
])
def test_the_log_of_an_update_copied_already(store, caplog, found, ready, said):
    from openexpress import known_devices
    from openexpress.known_devices import UpdateSummary

    with caplog.at_level(logging.INFO, logger="openexpress.known_devices"):
        assert known_devices.set_update_found(
            FENIX, found, now=4000.0, summary=UpdateSummary(2, "23.39") if found else None,
            software_version="23.39", ready=ready)
    assert _said(caplog) == [said]


def test_a_check_that_found_nothing_again_is_kept_without_a_word(store, caplog):
    """Nothing found before, nothing found now: only the time of the check is new; it is
    written, and the log says nothing."""
    from openexpress import known_devices

    known_devices.set_update_found(FENIX, False, now=4000.0, software_version="23.39")
    written = store.read_bytes()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="openexpress.known_devices"):
        assert known_devices.set_update_found(FENIX, False, now=4100.0,
                                              software_version="23.39") is True
    assert store.read_bytes() != written
    assert _said(caplog) == []


# --- when nothing is written ------------------------------------------------------------------


def _the_same_again():
    """Each change with what the pattern holds already: (id, the change)."""
    from openexpress import known_devices as kd

    entries = _entries()
    fenix = entries[FENIX]
    return [
        ("model-name", lambda: kd.set_model_name(EPIX, "epix (Gen 2)")),
        ("category", lambda: kd.set_category(FENIX, kd.WATCH)),
        ("image-url", lambda: kd.set_image_url(FENIX, fenix.image_url)),
        ("update", lambda: kd.set_update_found(FENIX, True, now=1900.0,
                                               summary=fenix.update_summary,
                                               software_version="23.39")),
        ("sync-count", lambda: kd.set_sync_count(FENIX, fenix.sync_count)),
        ("synced", lambda: kd.set_synced(FENIX, fenix.synced)),
        ("account", lambda: kd.set_account_facts({FENIX: fenix.account})),
    ]


@pytest.mark.parametrize("index", range(7), ids=[
    "model-name", "category", "image-url", "update", "sync-count", "synced", "account"])
def test_the_same_value_again_writes_nothing(store, caplog, index):
    name, change = _the_same_again()[index]
    before = store.stat().st_mtime_ns
    with caplog.at_level(logging.INFO, logger="openexpress.known_devices"):
        assert not change(), name
    assert store.read_bytes() == PATTERN.read_bytes()
    assert store.stat().st_mtime_ns == before
    assert _said(caplog) == []


def _for_a_device_not_remembered():
    from openexpress import known_devices as kd
    from openexpress.known_devices import AccountFact, SyncCount, SyncedFact

    return [
        lambda: kd.set_model_name(OTHER, "Edge 1050"),
        lambda: kd.set_category(OTHER, kd.WATCH),
        lambda: kd.set_image_url(OTHER, NEW_PICTURE),
        lambda: kd.set_update_found(OTHER, True, now=1.0, software_version="1.00"),
        lambda: kd.set_sync_count(OTHER, SyncCount(1, 1.0, "s")),
        lambda: kd.set_synced(OTHER, SyncedFact(True, None, False, 1.0, "s")),
        lambda: kd.set_account_facts({OTHER: AccountFact(True, False, 1.0, "l")}),
        lambda: kd.forget(OTHER),
    ]


@pytest.mark.parametrize("index", range(8))
def test_a_device_not_remembered_gets_no_entry(store, index):
    from openexpress import known_devices

    assert not _for_a_device_not_remembered()[index]()
    assert store.read_bytes() == PATTERN.read_bytes()
    assert OTHER not in known_devices.load()


# --- a file that may not be written over ------------------------------------------------------


def _every_change():
    from openexpress import known_devices as kd
    from openexpress.known_devices import AccountFact, SyncCount, SyncedFact

    return [
        ("remember", lambda: kd.remember(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp",
                                         now=1.0) is not None),
        ("seen", lambda: kd.seen(FENIX, "fēnix 8", "006-B4532-00", "23.39", "mtp") is None),
        ("model-name", lambda: kd.set_model_name(FENIX, "fēnix 8")),
        ("category", lambda: kd.set_category(FENIX, kd.WATCH)),
        ("image-url", lambda: kd.set_image_url(FENIX, NEW_PICTURE)),
        ("update", lambda: kd.set_update_found(FENIX, True, now=1.0, software_version="23.39")),
        ("sync-count", lambda: kd.set_sync_count(FENIX, SyncCount(1, 1.0, "s"))),
        ("synced", lambda: kd.set_synced(FENIX, SyncedFact(True, None, False, 1.0, "s"))),
        ("account", lambda: kd.set_account_facts({FENIX: AccountFact(True, False, 1.0, "l")})),
        ("forget", lambda: kd.forget(FENIX)),
    ]


# What each change returns on a file that may not be written over: remember() gives the entry
# it would have written, seen() nothing; every other one says nothing changed.
_RETURNS = {"remember": True, "seen": True}

_CHANGE_IDS = ["remember", "seen", "model-name", "category", "image-url", "update",
               "sync-count", "synced", "account", "forget"]


@pytest.mark.parametrize("index", range(10), ids=_CHANGE_IDS)
def test_a_damaged_file_is_never_written_over(store, caplog, index):
    """A damaged devices.json: no change writes over it, whatever the change; each is said
    in the log as damaged and returns as if nothing could change."""
    name, change = _every_change()[index]
    store.write_text('{"version": 1, "devices": {"3000000001": {"description": ')
    damaged = store.read_bytes()
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert bool(change()) == _RETURNS.get(name, False), name
    assert store.read_bytes() == damaged
    assert "devices.json is damaged" in caplog.text


@pytest.mark.parametrize("index", range(10), ids=_CHANGE_IDS)
def test_an_unreadable_file_is_never_written_over(store, caplog, index):
    """devices.json that cannot be read (here: a folder in its place): no change writes over
    it or fails; it is said in the log as unreadable."""
    name, change = _every_change()[index]
    store.unlink()
    store.mkdir()
    (store / "inside").write_text("kept")
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert bool(change()) == _RETURNS.get(name, False), name
    assert store.is_dir() and [p.name for p in store.iterdir()] == ["inside"]
    assert "devices.json cannot be read" in caplog.text
