"""Satellite data (experimental): only a device that has, or had, CPE.BIN of the one format known
is offered it, and only it is ever written to; Garmin's file is checked before a write, kept in
the cache for 4 hours, written only when it differs from the device's, read back whole; a file
the device renames to CPE_BAD.bin turns the feature off until it is turned on again; nothing is
asked of the network but through a stand-in. All files, devices and answers are made up."""

import contextlib
import gzip
import json
import logging
import types
from datetime import timedelta

import pytest
import requests
from fakes import made_up_cpe, satellite_answer, satellite_server
from ptp_fake import STORAGE, FakeDevice

from openexpress import cli, satellite
from openexpress.device import ptp
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


def test_cpe_bin_gone_while_on_is_written_again_by_the_next_sync(tmp_path, clock, monkeypatch):
    """Deleted by its owner after a write: the row stays, and with the switch on the next sync
    writes the file again; off, nothing is written."""
    storage, device = watch(tmp_path, {CPE: made_up_cpe(1)})
    satellite.seen(device, storage)
    satellite.set_on(UNIT, True)
    satellite_server(monkeypatch, satellite_answer(made_up_cpe(2)))
    assert satellite.update(device, storage).kind == "updated"
    (storage.root / CPE).unlink()
    assert satellite.seen(device, storage) is not None  # the row stays
    satellite.set_on(UNIT, False)
    assert satellite.update(device, storage) is None and len(storage.writes) == 1
    satellite.set_on(UNIT, True)
    assert satellite.update(device, storage) == satellite.Result("updated", satellite.SIZE)
    assert on_device(storage) == made_up_cpe(2)


def test_the_record_of_the_test_build_is_read_as_it_was(tmp_path, clock):
    from openexpress import paths

    record = {"on": True, "written_at": 1794500000.0, "written_sha": "ab" * 32,
              "stopped": False, "error": None}
    paths.make_dir(paths.device_dir(UNIT))
    (paths.device_dir(UNIT) / satellite.FILE_NAME).write_text(json.dumps(record))
    assert satellite.load(UNIT) == satellite.State(on=True, written_at=1794500000.0,
                                                   written_sha="ab" * 32)


# --- files of the rxnetworks kind (a watch such as the fēnix 8) ---------------------------------

GPE, GPES = satellite.RX_FILES
REMOTE = "GARMIN/RemoteSW"


def made_up_rx(spec, stamp: float, n: int = 1) -> tuple[bytes, bytes]:
    """A file of the rxnetworks kind as Garmin serves it (gzip) and as a watch keeps it
    (unpacked): 01 00, the time of its data, the bytes 6-15 every file has, made-up data."""
    raw = b"\x01\x00" + int(stamp).to_bytes(4, "big") + satellite.RX_HEADER
    raw += bytes((i * n) % 251 for i in range(spec.unpacked - len(raw)))
    return gzip.compress(raw, mtime=0), raw


class Recording(FakeDevice):
    """The fake device at the other end of PTP, which keeps each operation with its first
    parameter."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[int, int | None]] = []

    def _handle(self, operation, params, data):
        self.calls.append((operation, params[0] if params else None))
        return super()._handle(operation, params, data)


class Fenix(Device):
    """A watch that keeps GPE.bin and GPES.bin hidden once it has unpacked them: the folder on
    disk is what the ordinary listing shows, the fake device what Garmin's listing shows, over
    a PTP session of our own. Its place on USB tells one connection from the next."""

    def __init__(self, root, fake):
        super().__init__(root)
        self.fake = fake
        self.sessions = 0
        self.usb = types.SimpleNamespace(bus=1, address=7)
        self.ptp = True

    def exclusive_ptp(self):
        if not self.ptp:
            return None

        @contextlib.contextmanager
        def opened():
            self.sessions += 1
            self.fake.reconnect()
            with ptp.Session(self.fake) as session:
                yield session

        return opened()


def fenix(tmp_path, clock, files: dict[str, float]):
    """A fēnix with CPE.bin of another format and nvstg.dat shown, and the rxnetworks files of
    `files` (name: their time) hidden, unpacked, as the phone gives them."""
    root = tmp_path / "device"
    fake = Recording()
    for name, data in (("CPE.bin", b"\xb5\x62\x13\x20" + bytes(96)), ("nvstg.dat", b"made up")):
        (root / REMOTE).mkdir(parents=True, exist_ok=True)
        (root / REMOTE / name).write_bytes(data)
        fake.add(f"{REMOTE}/{name}", data)
    for spec in satellite.RX_FILES:
        if spec.name in files:
            fake.add(f"{REMOTE}/{spec.name}", made_up_rx(spec, files[spec.name], 2)[1],
                     hidden=True)
    device = types.SimpleNamespace(unit_id=UNIT, part_number="006-B0000-00",
                                   software_version_str="1.00")
    return Fenix(root, fake), device


def on_watch(storage, name: str) -> bytes | None:
    handle = storage.fake.handle(f"{REMOTE}/{name}")
    return None if handle is None else storage.fake.objects[handle].data


def unplugged(storage) -> None:
    """Unplugged and plugged in again: the watch unpacks our files and hides them; the device
    gets another address on USB."""
    for spec in satellite.RX_FILES:
        handle = storage.fake.handle(f"{REMOTE}/{spec.name}")
        obj = storage.fake.objects.get(handle)
        if obj is not None and obj.data[:2] == b"\x1f\x8b":
            obj.data, obj.hidden = gzip.decompress(obj.data), True
    storage.usb = types.SimpleNamespace(bus=1, address=storage.usb.address + 1)


class RxServer:
    """Garmin's addresses of satellite data as a test answers them, by address, each answer in
    turn; `asked`: the addresses asked, in order."""

    def __init__(self, answers: dict):
        self.answers, self.asked = {url: list(a) for url, a in answers.items()}, []

    def get(self, url, headers=None, timeout=None):
        assert headers == {"Accept": "application/octet-stream"}
        self.asked.append(url)
        return self.answers[url].pop(0)


def rx_answer(content: bytes = b"", status: int = 200, retry_after: str | None = None):
    r = satellite_answer(content, status)
    if retry_after is not None:
        r.headers["retry-after"] = retry_after
    return r


def rx_server(monkeypatch, answers: dict) -> RxServer:
    from openexpress import useragent

    server = RxServer(answers)
    monkeypatch.setattr(useragent, "session", lambda *a, **kw: server)
    return server


def hours_ago(clock, hours: float) -> float:
    return (clock.current - timedelta(hours=hours)).timestamp()


def test_a_watch_that_hides_them_is_found_by_a_sync_and_offered_off(tmp_path, clock):
    storage, device = fenix(tmp_path, clock, {"GPE.bin": hours_ago(clock, 30),
                                              "GPES.bin": hours_ago(clock, 2)})
    assert satellite.seen(device, storage) is None and storage.sessions == 0  # an opening
    assert satellite.update(device, storage) is None  # the sync finds them, off
    assert storage.sessions == 1 and storage.fake.sent(ptp.DELETE_OBJECT) == 0
    state = satellite.seen(device, storage)
    assert (state.kind, state.on) == (satellite.RXNETWORKS, False)
    assert {n: k["time"] for n, k in state.files.items()} == {
        "GPE.bin": int(hours_ago(clock, 30)), "GPES.bin": int(hours_ago(clock, 2))}
    assert cli._satellite_status(UNIT, clock.current) == (
        "Satellite data (experimental): off (the switch is on the device page in the window)")


def test_a_watch_that_shows_them_as_an_edge_1050_is_not_offered(tmp_path, clock):
    storage, device = watch(tmp_path, {f"{REMOTE}/GPES.bin": made_up_rx(GPES, 0)[1]})
    storage.exclusive_ptp = lambda: pytest.fail("no PTP session for a device that shows them")
    assert satellite.update(device, storage) is None
    assert satellite.load(UNIT) is None and satellite.seen(device, storage) is None


def test_a_watch_without_them_is_looked_at_again_a_day_later(tmp_path, clock):
    storage, device = fenix(tmp_path, clock, {})
    assert satellite.update(device, storage) is None and storage.sessions == 1
    clock.current += timedelta(hours=23)
    assert satellite.update(device, storage) is None and storage.sessions == 1
    # The phone gives it GPES.bin; the next look, a day after the first, finds it alone.
    storage.fake.add(f"{REMOTE}/GPES.bin", made_up_rx(GPES, hours_ago(clock, 1))[1], hidden=True)
    clock.current += timedelta(hours=2)
    assert satellite.update(device, storage) is None and storage.sessions == 2
    assert list(satellite.load(UNIT).files) == ["GPES.bin"]


def test_only_the_files_it_has_are_written_each_on_its_own(tmp_path, clock, monkeypatch):
    storage, device = fenix(tmp_path, clock, {"GPES.bin": hours_ago(clock, 5)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    served = made_up_rx(GPES, hours_ago(clock, 0.5))[0]
    server = rx_server(monkeypatch, {GPES.url: [rx_answer(served)]})
    assert satellite.update(device, storage) == satellite.Result("updated", len(served))
    assert server.asked == [GPES.url] and on_watch(storage, "GPE.bin") is None
    assert on_watch(storage, "GPES.bin") == served  # the gzip as served, never changed


def test_the_old_file_goes_first_hidden_too_then_ours_and_its_start_is_read_back(
        tmp_path, clock, monkeypatch):
    storage, device = fenix(tmp_path, clock, {"GPES.bin": hours_ago(clock, 5)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    old = storage.fake.handle(f"{REMOTE}/GPES.bin")
    rx_server(monkeypatch, {GPES.url: [rx_answer(made_up_rx(GPES, hours_ago(clock, 0.5))[0])]})
    storage.fake.calls.clear()
    satellite.update(device, storage)
    new = storage.fake.handle(f"{REMOTE}/GPES.bin")
    changes = [(op, p) for op, p in storage.fake.calls
               if op in (ptp.DELETE_OBJECT, ptp.SEND_OBJECT_INFO, ptp.SEND_OBJECT)]
    assert changes == [(ptp.DELETE_OBJECT, old), (ptp.SEND_OBJECT_INFO, STORAGE),
                       (ptp.SEND_OBJECT, None)]
    after = storage.fake.calls[storage.fake.calls.index((ptp.SEND_OBJECT, None)):]
    assert (ptp.GET_OBJECT, new) in after  # read back


def test_files_young_enough_on_the_watch_are_neither_asked_for_nor_written(
        tmp_path, clock, monkeypatch):
    storage, device = fenix(tmp_path, clock, {"GPE.bin": hours_ago(clock, 10),
                                              "GPES.bin": hours_ago(clock, 1)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    server = rx_server(monkeypatch, {
        GPES.url: [rx_answer(made_up_rx(GPES, hours_ago(clock, 0))[0])]})
    assert satellite.update(device, storage) == satellite.Result("current")
    assert server.asked == [] and storage.sessions == 1  # only the one that found them
    assert cli._satellite_result(satellite.Result("current")) == "Satellite data already current"
    assert cli._satellite_status(UNIT, clock.current) == (
        f"Satellite data (experimental): on{NB}· the data on the device is current")
    # GPES.bin 4 hours old: it alone is asked for and written; GPE.bin, 13 hours, is not.
    clock.current += timedelta(hours=3)
    assert satellite.update(device, storage).kind == "updated"
    assert server.asked == [GPES.url] and storage.sessions == 2


def test_the_dry_run_never_leaves_the_device_to_a_session(tmp_path, clock, monkeypatch):
    storage, device = fenix(tmp_path, clock, {"GPES.bin": hours_ago(clock, 5)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    served = made_up_rx(GPES, hours_ago(clock, 0.5))[0]
    rx_server(monkeypatch, {GPES.url: [rx_answer(served)]})
    assert satellite.update(device, storage, apply=False) == satellite.Result("would",
                                                                              len(served))
    assert storage.sessions == 1 and on_watch(storage, "GPES.bin")[:2] == b"\x01\x00"


@pytest.mark.parametrize("served", [
    made_up_rx(GPES, 0)[1],  # unpacked, not as served
    gzip.compress(made_up_rx(GPE, 0)[1], mtime=0),  # the size of the other file
    gzip.compress(b"\x01\x00" + bytes(GPES.unpacked - 2), mtime=0),  # not of the kind
    made_up_rx(GPES, 0)[0] + b"\x00",  # more after the gzip stream
], ids=["not-gzip", "other-size", "other-header", "more-after"])
def test_a_file_of_garmin_not_of_the_kind_known_writes_nothing(tmp_path, clock, monkeypatch,
                                                                 served):
    storage, device = fenix(tmp_path, clock, {"GPES.bin": hours_ago(clock, 5)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    before = on_watch(storage, "GPES.bin")
    rx_server(monkeypatch, {GPES.url: [rx_answer(served)]})
    assert satellite.update(device, storage) == satellite.Result("failed",
                                                                 reason=satellite.FORMAT)
    assert on_watch(storage, "GPES.bin") == before


def written_once(tmp_path, clock, monkeypatch):
    """A fēnix with GPES.bin of the phone 5 hours old, turned on and synced once: ours written.
    Garmin's next files are 5 hours newer each."""
    storage, device = fenix(tmp_path, clock, {"GPES.bin": hours_ago(clock, 5)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    served = [made_up_rx(GPES, hours_ago(clock, 0.5 - 5 * n), n + 3)[0] for n in range(3)]
    server = rx_server(monkeypatch, {GPES.url: [rx_answer(s) for s in served]})
    assert satellite.update(device, storage).kind == "updated"
    return storage, device, server, served


def test_taken_once_unplugged_it_is_not_written_again_while_young(tmp_path, clock, monkeypatch):
    storage, device, server, served = written_once(tmp_path, clock, monkeypatch)
    unplugged(storage)  # the watch unpacked it: ours, unpacked, hidden
    clock.current += timedelta(hours=1)
    assert satellite.update(device, storage) == satellite.Result("current")
    assert satellite.load(UNIT).files["GPES.bin"]["pending"] is False
    assert len(server.asked) == 1 and storage.fake.sent(ptp.SEND_OBJECT) == 1


def test_not_unpacked_while_still_plugged_in_is_waited_for(tmp_path, clock, monkeypatch):
    storage, device, server, served = written_once(tmp_path, clock, monkeypatch)
    clock.current += timedelta(hours=5)  # a sync in the background, the same connection
    assert satellite.update(device, storage) == satellite.Result("current")
    state = satellite.load(UNIT)
    assert state.on and state.files["GPES.bin"]["pending"]
    assert on_watch(storage, "GPES.bin") == served[0] and len(server.asked) == 1


def test_not_unpacked_after_unplugging_turns_it_off(tmp_path, clock, monkeypatch):
    from openexpress.gui import words

    storage, device, server, served = written_once(tmp_path, clock, monkeypatch)
    storage.usb = types.SimpleNamespace(bus=1, address=storage.usb.address + 1)  # left as it was
    assert satellite.update(device, storage) == satellite.Result(
        "stopped", reason=satellite.NOT_TAKEN)
    state = satellite.load(UNIT)
    assert (state.on, state.stopped) == (False, True)
    assert words.satellite_subtitle(state, clock.current) == (
        "Turned off: the device did not take the last file")
    assert cli._satellite_status(UNIT, clock.current) == (
        f"Satellite data (experimental): off{NB}· the device did not take the last file")
    # Turned on again: the next sync tries once more.
    satellite.set_on(UNIT, True)
    clock.current += timedelta(hours=5)
    assert satellite.update(device, storage).kind == "updated"


def test_a_newer_one_from_the_phone_is_kept(tmp_path, clock, monkeypatch):
    storage, device, server, served = written_once(tmp_path, clock, monkeypatch)
    phone = made_up_rx(GPES, hours_ago(clock, 0), 9)[1]
    storage.fake.objects[storage.fake.handle(f"{REMOTE}/GPES.bin")].data = phone
    unplugged(storage)
    clock.current += timedelta(hours=1)
    assert satellite.update(device, storage) == satellite.Result("current")
    assert satellite.load(UNIT).on and on_watch(storage, "GPES.bin") == phone


def test_gone_once_it_is_written_again_gone_twice_it_turns_off(tmp_path, clock, monkeypatch):
    from openexpress.gui import words

    storage, device, server, served = written_once(tmp_path, clock, monkeypatch)
    storage.fake.objects.pop(storage.fake.handle(f"{REMOTE}/GPES.bin"))
    unplugged(storage)
    assert satellite.update(device, storage).kind == "updated"  # written again
    assert on_watch(storage, "GPES.bin") == served[0] and len(server.asked) == 1  # the cache's
    storage.fake.objects.pop(storage.fake.handle(f"{REMOTE}/GPES.bin"))
    unplugged(storage)
    assert satellite.update(device, storage) == satellite.Result(
        "stopped", reason=satellite.GONE_TWICE)
    assert words.satellite_subtitle(satellite.load(UNIT), clock.current) == (
        "Turned off: the written file disappeared twice")


def test_the_servers_wait_holds_for_every_file_and_every_watch(tmp_path, clock, monkeypatch):
    from openexpress.gui import words

    storage, device = fenix(tmp_path, clock, {"GPES.bin": hours_ago(clock, 5)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    server = rx_server(monkeypatch, {
        GPES.url: [rx_answer(b"error code: 1015", 429, retry_after="600"),
                   rx_answer(made_up_rx(GPES, hours_ago(clock, 0.5))[0])],
        satellite.URL: [satellite_answer(made_up_cpe(2))]})
    result = satellite.update(device, storage)
    until = (clock.current + timedelta(seconds=600)).strftime("%H:%M")
    assert result.kind == "failed" and words.satellite_reason(result.reason) == (
        f"Garmin's server asks to wait until {until}")
    assert words.satellite_subtitle(satellite.load(UNIT), clock.current) == (
        f"Experimental{NB}· not updated: Garmin's server asks to wait until {until}")
    # Another watch, of CPE.BIN, is not asked for meanwhile either.
    other, fr45 = watch(tmp_path / "fr45", {CPE: made_up_cpe(1)})
    fr45.unit_id = UNIT + 1
    satellite.seen(fr45, other)
    satellite.set_on(UNIT + 1, True)
    assert satellite.update(fr45, other).reason.startswith(satellite.WAIT)
    assert server.asked == [GPES.url]
    clock.current += timedelta(minutes=11)
    assert satellite.update(device, storage).kind == "updated"
    assert satellite.update(fr45, other).kind == "updated"
    assert server.asked == [GPES.url, GPES.url, satellite.URL]


def test_each_file_is_kept_in_the_cache_on_its_own(tmp_path, clock, monkeypatch):
    from openexpress import paths

    storage, device = fenix(tmp_path, clock, {"GPE.bin": hours_ago(clock, 30),
                                              "GPES.bin": hours_ago(clock, 5)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    gpe, gpes = made_up_rx(GPE, hours_ago(clock, 1))[0], made_up_rx(GPES, hours_ago(clock, 1))[0]
    server = rx_server(monkeypatch, {GPE.url: [rx_answer(gpe)], GPES.url: [rx_answer(gpes)]})
    assert satellite.update(device, storage) == satellite.Result("updated", len(gpe) + len(gpes))
    kept = paths.cache_dir() / "software" / "satellite"
    assert (kept / "GPE.bin").read_bytes() == gpe and (kept / "GPES.bin").read_bytes() == gpes
    assert not (kept / satellite.NAME).exists()
    assert sorted(server.asked) == sorted([GPE.url, GPES.url])


def test_without_a_session_of_its_own_here_it_says_so(tmp_path, clock, monkeypatch):
    from openexpress.gui import words

    storage, device = fenix(tmp_path, clock, {"GPES.bin": hours_ago(clock, 5)})
    satellite.update(device, storage)
    satellite.set_on(UNIT, True)
    storage.ptp = False  # e.g. a Flatpak without access to USB
    result = satellite.update(device, storage)
    assert result == satellite.Result("failed", reason=satellite.HIDDEN)
    assert words.satellite_reason(result.reason) == (
        "the device's hidden files cannot be reached here")


def test_the_lines_of_sync_say_why_it_turned_off():
    words = cli._satellite_result
    assert words(satellite.Result("stopped", reason=satellite.NOT_TAKEN)) == (
        "Satellite data not updated: the device did not take the last file")
    assert words(satellite.Result("stopped", reason=satellite.GONE_TWICE)) == (
        "Satellite data not updated: the written file disappeared twice")
