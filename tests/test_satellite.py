"""Satellite data (experimental): only a device that has, or had, CPE.BIN of the one format known
is offered it, and only it is ever written to; Garmin's file is checked before a write, kept in
the cache for 4 hours, written only when it differs from the device's, read back whole; a file
the device renames to CPE_BAD.bin turns the feature off until it is turned on again; nothing is
asked of the network but through a stand-in. All files, devices and answers are made up."""

import logging
import types
from datetime import timedelta

import pytest
import requests
from fakes import made_up_cpe, satellite_answer, satellite_server

from openexpress import cli, satellite
from openexpress.device.storage import DeviceError, LocalStorage

UNIT = 3000000045
NB = "\u00a0"
CPE = "GARMIN/REMOTESW/CPE.BIN"


class Device(LocalStorage):
    """A watch on this computer's disk that counts its writes and can refuse them."""

    def __init__(self, root):
        super().__init__(root)
        self.writes = []
        self.refuse = False

    def _write(self, real_path, data):
        if self.refuse:
            raise DeviceError("made-up: the device refused the file")
        self.writes.append(real_path)
        return super()._write(real_path, data)


def watch(tmp_path, files: dict[str, bytes]):
    root = tmp_path / "device"
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    device = types.SimpleNamespace(unit_id=UNIT, part_number="006-B0000-00")
    return Device(root), device


def on_device(storage, path=CPE) -> bytes | None:
    found = storage.root / path
    return found.read_bytes() if found.exists() else None


def rejects(storage) -> None:
    """The watch renames the file written to CPE_BAD.bin, as it does with one it does not take."""
    (storage.root / CPE).rename(storage.root / "GARMIN" / "REMOTESW" / satellite.REJECTED)


# --- the format and the devices -----------------------------------------------------------------


def test_the_format_known_is_its_size_start_length_and_zeros_after_the_data():
    good = made_up_cpe()
    assert satellite.known_format(good)
    length = int.from_bytes(good[4:8], "little")
    assert not satellite.known_format(good[:-1])  # another size
    assert not satellite.known_format(b"\0" + good[1:])  # another start
    assert not satellite.known_format(good[:4] + (satellite.SIZE + 1).to_bytes(4, "little")
                                      + good[8:])  # a length beyond the file
    assert not satellite.known_format(good[:length] + b"\1" + good[length + 1:])  # not zeros
    assert not satellite.known_format(b"CPE_GPS.BIN" + bytes(satellite.SIZE - 11))  # a tar


def test_a_device_with_the_file_of_the_format_is_remembered_and_off(tmp_path, clock):
    storage, device = watch(tmp_path, {CPE: made_up_cpe()})
    state = satellite.seen(device, storage)
    assert state == satellite.State() and not state.on
    (storage.root / CPE).unlink()  # remembered also without its file
    assert satellite.seen(device, storage) == satellite.State()


@pytest.mark.parametrize("files", [
    # Like a fēnix 8: CPE.bin of another size and format; it is never read.
    {"GARMIN/RemoteSW/CPE.bin": b"\xb5\x62\x13\x20" + bytes(310_296 - 4),
     "GARMIN/RemoteSW/nvstg.dat": b"made up"},
    # Like an Edge 1050: no CPE.BIN, only GPES.bin.
    {"GARMIN/RemoteSW/GPES.bin": b"made up" * 100},
    # The tar archive of another address.
    {CPE: b"CPE_GPS.BIN" + bytes(satellite.SIZE - 11)},
], ids=["like-a-fenix", "like-an-edge", "a-tar"])
def test_a_device_without_the_file_of_the_format_never_gets_the_row_or_a_write(
        tmp_path, clock, monkeypatch, files):
    storage, device = watch(tmp_path, files)
    server = satellite_server(monkeypatch, satellite_answer(made_up_cpe()))
    assert satellite.seen(device, storage) is None
    assert satellite.load(UNIT) is None
    assert satellite.update(device, storage) is None
    assert storage.writes == [] and server.asked == 0
    assert cli._satellite_status(UNIT, clock.current) is None


def test_a_copy_of_a_devices_files_is_never_offered_it(tmp_path, clock):
    storage, device = watch(tmp_path, {CPE: made_up_cpe()})
    storage.test_drive = True
    assert satellite.seen(device, storage) is None and satellite.load(UNIT) is None


# --- the sync -----------------------------------------------------------------------------------


def test_off_nothing_is_asked_or_written(tmp_path, clock, monkeypatch):
    storage, device = watch(tmp_path, {CPE: made_up_cpe(1)})
    satellite.seen(device, storage)
    server = satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)))
    assert satellite.update(device, storage) is None
    assert server.asked == 0 and storage.writes == []


def test_on_garmins_file_is_written_read_back_and_kept_in_the_cache_4_hours(
        tmp_path, clock, monkeypatch):
    storage, device = watch(tmp_path, {CPE: made_up_cpe(1)})
    satellite.seen(device, storage)
    satellite.set_on(UNIT, True)
    server = satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)),
                              satellite_answer(made_up_cpe(3)))
    assert satellite.update(device, storage, apply=False) == satellite.Result("would",
                                                                              satellite.SIZE)
    assert storage.writes == [] and satellite.load(UNIT).written_at is None  # nothing kept
    assert satellite.update(device, storage) == satellite.Result("updated", satellite.SIZE)
    assert on_device(storage) == made_up_cpe(2) and server.asked == 1  # the cache's file
    state = satellite.load(UNIT)
    assert state.on and state.written_at == clock.current.timestamp() and state.error is None
    # The same file again: nothing written, nothing asked while it is fresh.
    clock.current += timedelta(hours=3, minutes=59)
    assert satellite.update(device, storage) == satellite.Result("current")
    assert len(storage.writes) == 1 and server.asked == 1
    clock.current += timedelta(minutes=2)
    assert satellite.update(device, storage) == satellite.Result("updated", satellite.SIZE)
    assert on_device(storage) == made_up_cpe(3) and server.asked == 2


def test_the_same_file_on_the_watch_is_said_in_the_log(tmp_path, clock, monkeypatch, caplog):
    storage, device = watch(tmp_path, {CPE: made_up_cpe(2)})
    satellite.seen(device, storage)
    satellite.set_on(UNIT, True)
    satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)))
    with caplog.at_level(logging.INFO, logger="openexpress.satellite"):
        assert satellite.update(device, storage) == satellite.Result("current")
    assert ("satellite data: CPE.BIN on the device is the same file, nothing written"
            in caplog.messages)
    assert storage.writes == []


@pytest.mark.parametrize("answer, reason", [
    (requests.ConnectionError("made-up: no route to https://api.gcs.garmin.com/x"),
     "network:made-up: no route to"),
    (satellite_answer(b"Not Found", status=404), "network:404 Client Error: Not Found"),
    (satellite_answer(b"CPE_GPS.BIN" + bytes(satellite.SIZE - 11)), satellite.FORMAT),
    (satellite_answer(made_up_cpe(2), kind="application/x-tar"), satellite.FORMAT),
])
def test_a_file_not_had_or_not_of_the_format_writes_nothing(tmp_path, clock, monkeypatch,
                                                              answer, reason):
    storage, device = watch(tmp_path, {CPE: made_up_cpe(1)})
    satellite.seen(device, storage)
    satellite.set_on(UNIT, True)
    satellite_server(monkeypatch, answer)
    result = satellite.update(device, storage)
    assert result.kind == "failed" and result.reason.startswith(reason)
    assert "api.gcs.garmin.com" not in result.reason  # the address is not told
    assert on_device(storage) == made_up_cpe(1) and storage.writes == []
    assert satellite.load(UNIT).error == result.reason


def test_a_file_the_device_does_not_take_is_said_and_the_old_one_stays(tmp_path, clock,
                                                                         monkeypatch):
    storage, device = watch(tmp_path, {CPE: made_up_cpe(1)})
    satellite.seen(device, storage)
    satellite.set_on(UNIT, True)
    satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)))
    storage.refuse = True
    assert satellite.update(device, storage) == satellite.Result("failed",
                                                                 reason=satellite.WRITE)
    assert on_device(storage) == made_up_cpe(1)


def test_rejected_it_turns_off_and_turned_on_again_it_tries_once_more(tmp_path, clock,
                                                                       monkeypatch):
    storage, device = watch(tmp_path, {CPE: made_up_cpe(1)})
    satellite.seen(device, storage)
    satellite.set_on(UNIT, True)
    satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)))
    assert satellite.update(device, storage).kind == "updated"
    rejects(storage)
    state = satellite.seen(device, storage)  # the next opening sees it
    assert (state.on, state.stopped) == (False, True)
    assert cli._satellite_status(UNIT, clock.current) == (
        f"Satellite data (experimental): off{NB}· the device rejected the last file (CPE_BAD.bin)")
    assert satellite.update(device, storage) is None  # off: no write
    # Turned on again: the next sync writes it again (the cache's file, the same one).
    satellite.set_on(UNIT, True)
    assert satellite.update(device, storage) == satellite.Result("updated", satellite.SIZE)
    assert on_device(storage) == made_up_cpe(2)
    rejects(storage)  # rejected again: off again, told by the sync itself
    assert satellite.update(device, storage) == satellite.Result("stopped")
    assert satellite.load(UNIT).stopped and not satellite.load(UNIT).on


def test_a_bad_file_it_did_not_write_turns_nothing_off(tmp_path, clock, monkeypatch):
    storage, device = watch(tmp_path, {CPE: made_up_cpe(1),
                                       "GARMIN/REMOTESW/CPE_BAD.bin": made_up_cpe(9)})
    satellite.seen(device, storage)
    satellite.set_on(UNIT, True)
    satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)))
    assert satellite.update(device, storage).kind == "updated"
    state = satellite.seen(device, storage)
    assert state.on and not state.stopped


# --- the command line ---------------------------------------------------------------------------


def test_the_line_of_status_says_what_the_row_says(tmp_path, clock, monkeypatch):
    storage, device = watch(tmp_path, {CPE: made_up_cpe(1)})
    satellite.seen(device, storage)
    line = cli._satellite_status
    assert line(UNIT, clock.current) == ("Satellite data (experimental): off (the switch is on "
                                         "the device page in the window)")
    satellite.set_on(UNIT, True)
    assert line(UNIT, clock.current) == (f"Satellite data (experimental): on{NB}· the next "
                                         "sync updates it")
    satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)))
    satellite.update(device, storage)
    clock.current += timedelta(days=1)
    when = (clock.current - timedelta(days=1)).strftime("%H:%M")
    assert line(UNIT, clock.current) == (f"Satellite data (experimental): on{NB}· updated "
                                         f"yesterday, {when}")


def test_the_lines_of_sync_say_what_it_did():
    words = cli._satellite_result
    assert words(None) is None
    assert words(satellite.Result("updated", satellite.SIZE)) == (
        f"Satellite data updated (63.5{NB}kB)")
    assert words(satellite.Result("would", satellite.SIZE)) == (
        f"Satellite data: would be updated (63.5{NB}kB)")
    assert words(satellite.Result("current")) == "Satellite data already current"
    assert words(satellite.Result("failed", reason=satellite.FORMAT)) == (
        "Satellite data not updated: Garmin's file is not in the format the device has")
    assert words(satellite.Result("stopped")) == (
        "Satellite data not updated: the device rejected the last file (CPE_BAD.bin)")


def test_the_cache_folder_is_that_of_software_updates(tmp_path, clock, monkeypatch):
    """Cleared with the software updates (cache --clear --only updates)."""
    from openexpress import cache, paths

    storage, device = watch(tmp_path, {CPE: made_up_cpe(1)})
    satellite.seen(device, storage)
    satellite.set_on(UNIT, True)
    satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)))
    satellite.update(device, storage)
    kept = paths.cache_dir() / "software" / "satellite" / satellite.NAME
    assert kept.read_bytes() == made_up_cpe(2)
    assert "software" in cache.CATEGORIES["updates"]
