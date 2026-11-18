"""The PTP layer, against a fake device. No USB device is touched and all data is made up."""

import os
import struct
import types

import pytest
from ptp_fake import STORAGE, FakeDevice

from openexpress.device import ptp
from openexpress.device.storage import DeviceError

APP = bytes(range(256)) * 3  # 768 bytes, not a multiple of the packet size


@pytest.fixture
def device():
    """A device that hides its app folder, with one app the device already took over."""
    fake = FakeDevice()
    fake.add("GARMIN/GarminDevice.xml", b"<Device/>")
    fake.add("GARMIN/Apps/OUT.BIN", b"state")
    fake.add("GARMIN/Apps/Media", hidden=True)
    fake.add("GARMIN/Apps/Media/A1B2C3D4.PRG", b"old app", hidden=True)
    return fake


# --- containers --------------------------------------------------------------------------

def test_command_bytes():
    assert ptp.command(ptp.OPEN_SESSION, 0, (1,)).hex() == "10000000010002100000000001000000"
    assert ptp.command(ptp.GARMIN_GET_OBJECT_IDS, 5, (0x00010001, 0x2A)).hex() == (
        "14000000"   # length
        "0100"       # a command
        "0290"       # the operation
        "05000000"   # transaction
        "01000100"   # storage
        "2a000000"   # parent
    )
    assert ptp.command(ptp.CLOSE_SESSION, 9).hex() == "0c0000000100031009000000"


def test_data_header_bytes():
    assert ptp.data_header(ptp.SEND_OBJECT, 7, 768).hex() == (
        "0c030000"   # length
        "0200"       # data
        "0d10"       # the operation
        "07000000"   # transaction
    )


def test_strings():
    assert ptp.ptp_string("") == b"\x00"
    assert ptp.ptp_string("AB") == b"\x03A\x00B\x00\x00\x00"
    assert ptp.read_string(b"xx" + ptp.ptp_string("Zażółć") + b"rest", 2) == ("Zażółć", 17)
    with pytest.raises(ptp.PtpError):
        ptp.ptp_string("x" * 255)
    with pytest.raises(ptp.PtpError):
        ptp.read_string(b"\x05A\x00", 0)


def test_object_info_dataset_bytes():
    """Format and thumbnail format "undefined", size and name; the rest zero or empty."""
    data = ptp.object_info_dataset("AB.PRG", 0x0102)
    assert data.hex() == (
        "00000000"                           # storage
        "0030"                               # format
        "0000"                               # protection
        "02010000"                           # size
        "0030" + "00000000" * 7              # thumbnail format, six sizes, parent
        + (
            "0000"                           # association type
            "00000000"                       # association description
            "00000000"                       # sequence number
        )
        + "07" + "AB.PRG\0".encode("utf-16-le").hex()
        + (
            "00"                             # no capture date
            "00"                             # no modification date
            "00"                             # no keywords
        )
    )
    assert len(data) == 52 + 1 + 14 + 3


def test_object_info_round_trip(device):
    info = ptp.parse_object_info(0x42, device._object_info(device.objects[device.handle(
        "GARMIN/Apps/Media/A1B2C3D4.PRG")]))
    assert (info.handle, info.name, info.size, info.protection) == (0x42, "A1B2C3D4.PRG", 7, 1)
    assert info.parent == device.handle("GARMIN/Apps/Media") and not info.is_dir
    with pytest.raises(ptp.PtpError):
        ptp.parse_object_info(1, b"\x00" * 20)


def test_u32_array():
    assert ptp.u32_array(b"") == []
    assert ptp.u32_array(struct.pack("<III", 2, 7, 9)) == [7, 9]
    with pytest.raises(ptp.PtpError):
        ptp.u32_array(struct.pack("<II", 2, 7))


# --- session -----------------------------------------------------------------------------

def test_session_opens_and_closes(device):
    with ptp.Session(device) as session:
        assert session.storages() == [STORAGE]
        assert device.session_open
    assert not device.session_open and device.closed
    assert device.operations == [ptp.OPEN_SESSION, ptp.GET_STORAGE_IDS, ptp.CLOSE_SESSION]
    # Transaction IDs count up from zero.
    assert [struct.unpack_from("<I", t, 8)[0] for t in device.transfers] == [0, 1, 2]


def test_session_closes_after_an_error(device):
    with pytest.raises(RuntimeError), ptp.Session(device):
        raise RuntimeError("boom")
    assert not device.session_open and device.closed
    assert device.operations[-1] == ptp.CLOSE_SESSION


def test_stale_session_is_restarted(device):
    device.session_open = True  # left open by a client that crashed
    with ptp.Session(device) as session:
        assert session.storages() == [STORAGE]
    assert device.operations[:3] == [ptp.OPEN_SESSION, ptp.CLOSE_SESSION, ptp.OPEN_SESSION]


def test_refused_session_releases_the_device(device):
    device.refuse[ptp.OPEN_SESSION] = ptp.DEVICE_BUSY
    with pytest.raises(ptp.PtpRefused) as error, ptp.Session(device):
        pass
    assert error.value.code == ptp.DEVICE_BUSY and "DeviceBusy" in str(error.value)
    assert isinstance(error.value, DeviceError) and device.closed


def test_answer_split_over_many_reads(device):
    device.read_chunk = 5
    with ptp.Session(device) as session:
        names = [o.name for o in session.children(STORAGE, device.handle("GARMIN"))]
    assert names == ["GarminDevice.xml", "Apps"]


def test_wrong_transaction_id_stops_the_conversation(device):
    with ptp.Session(device) as session:
        device.wrong_transaction = True
        with pytest.raises(ptp.PtpError, match="out of step"):
            session.storages()
        sent = len(device.transfers)
        with pytest.raises(ptp.PtpError):
            session.storages()
        assert len(device.transfers) == sent  # nothing more is sent
    assert device.closed and device.operations[-1] != ptp.CLOSE_SESSION


def test_silent_device(device):
    with ptp.Session(device) as session:
        device.silent = True
        with pytest.raises(ptp.PtpError, match="did not answer"):
            session.storages()
    assert device.closed


def test_free_space(device):
    device.free = 123456
    with ptp.Session(device) as session:
        assert session.free_space(STORAGE) == 123456


# --- what the device says it supports -----------------------------------------------------

def test_the_operations_the_device_lists(device):
    with ptp.Session(device) as session:
        assert session.operations() == device.supported
        assert session.storages() == [STORAGE]


def test_the_line_of_the_log_names_the_operations_that_matter_and_nothing_of_the_device(device):
    with ptp.Session(device) as session:
        line = ptp.operations_line(session)
    assert line == (
        "the device lists 37 operations; GetPartialObject yes, SendObject yes, DeleteObject yes, "
        "Garmin's hidden listing yes; all: 0x1001 0x1002 0x1003 0x1004 0x1005 0x1007 0x1008 "
        "0x1009 0x100A 0x100B 0x100C 0x100D 0x1010 0x1014 0x1015 0x1016 0x1019 0x101B 0x9000 "
        "0x9001 0x9002 0x9003 0x9004 0x9005 0x9006 0x9007 0x9008 0x9009 0x900A 0x900B 0x9801 "
        "0x9802 0x9803 0x9804 0x9805 0x9810 0x9811")
    assert not any(word in line for word in ("Made-up", "9.99", "MADEUPSERIAL", "microsoft"))


def test_the_line_says_what_the_device_does_not_list(device):
    device.supported = [0x1002, 0x1003, 0x1009, 0x100C, 0x100D]
    with ptp.Session(device) as session:
        line = ptp.operations_line(session)
    assert line.startswith("the device lists 5 operations; GetPartialObject no, SendObject yes, "
                           "DeleteObject no, Garmin's hidden listing no; all: 0x1002 ")


def test_a_device_that_does_not_list_its_operations_goes_on(device):
    device.refuse[ptp.GET_DEVICE_INFO] = ptp.OPERATION_NOT_SUPPORTED
    with ptp.Session(device) as session:
        line = ptp.operations_line(session)
        assert session.storages() == [STORAGE]  # the session goes on in step
    assert line == "the device did not list its operations (OperationNotSupported)"


@pytest.mark.parametrize("cut", ["in the versions", "in the vendor's words", "in the operations"])
def test_a_device_info_cut_short_is_an_error(cut):
    data = FakeDevice().device_info()
    events = data.index(b"\x02\x00\x00\x00\x02\x40")  # their count, then the first: after the operations
    keep = {"in the versions": 3, "in the vendor's words": 12, "in the operations": events - 1}[cut]
    with pytest.raises(ptp.PtpError):
        ptp.operations_supported(data[:keep])


@pytest.fixture
def own_session(device, monkeypatch):
    """gio_storage._exclusive opening the made-up device, nothing unmounted; each opening is
    a new session over the same cable."""
    import contextlib
    import time

    from openexpress.device import gio_storage

    @contextlib.contextmanager
    def unmounted(storage, what, closed):
        yield time.monotonic()

    def opened(cls, bus, address):
        device.reconnect()
        return device

    monkeypatch.setattr(gio_storage, "_unmounted", unmounted)
    monkeypatch.setattr(ptp.LibusbTransport, "open", classmethod(opened))
    return gio_storage._exclusive


def _plugged_at(address: int):
    from openexpress.device import gio_storage

    return types.SimpleNamespace(usb=gio_storage.GarminUsb(None, 3, address))


def test_a_session_of_its_own_logs_the_operations_once_it_is_open(device, own_session, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="openexpress.device.gio_storage"), \
            own_session(_plugged_at(14)) as session:
        assert session.storages() == [STORAGE]
    lines = [r.getMessage() for r in caplog.records if r.name == "openexpress.device.gio_storage"]
    opened = next(i for i, line in enumerate(lines) if line.startswith("PTP: session open after"))
    assert lines[opened + 1].startswith("PTP: the device lists 37 operations; GetPartialObject yes")
    assert device.operations[:2] == [ptp.OPEN_SESSION, ptp.GET_DEVICE_INFO]


def test_the_operations_are_logged_in_full_once_a_connection(device, own_session, caplog):
    """The device is asked for its operations at every session; the log has their list once
    a connection, and again only when the device lists others."""
    import logging

    first, again = _plugged_at(14), _plugged_at(15)  # unplugged and plugged in again
    with caplog.at_level(logging.INFO, logger="openexpress.device.gio_storage"):
        for storage in (first, first, again):
            with own_session(storage) as session:
                assert session.storages() == [STORAGE]
        device.supported = device.supported[:-1]
        with own_session(again):
            pass
    said = [r.getMessage() for r in caplog.records if r.getMessage().startswith("PTP: the device")]
    full = said[0]
    assert full.startswith("PTP: the device lists 37 operations; GetPartialObject yes")
    assert said[1:3] == ["PTP: the device lists the same operations as before", full]
    assert said[3].startswith("PTP: the device lists 36 operations; GetPartialObject yes")
    assert len(said) == 4 and device.sent(ptp.GET_DEVICE_INFO) == 4


# --- hidden objects ----------------------------------------------------------------------

def test_hidden_folder_is_only_in_garmins_listing(device):
    apps, media = device.handle("GARMIN/Apps"), device.handle("GARMIN/Apps/Media")
    with ptp.Session(device) as session:
        assert [o.name for o in session.children(STORAGE, apps)] == ["OUT.BIN"]
        hidden = session.hidden_children(STORAGE, apps)
        assert [(o.name, o.is_dir, o.protection, o.hidden) for o in hidden] == [
            ("Media", True, 1, True)]
        assert session.children(STORAGE, media) == []
        assert [o.name for o in session.hidden_children(STORAGE, media)] == ["A1B2C3D4.PRG"]


def test_lookup_finds_hidden_objects_case_insensitively(device):
    with ptp.Session(device) as session:
        media = session.lookup(STORAGE, "GARMIN/APPS/MEDIA")
        assert media.handle == device.handle("GARMIN/Apps/Media") and media.hidden
        assert session.lookup(STORAGE, "garmin/apps/media/a1b2c3d4.prg").size == 7
        assert not session.lookup(STORAGE, "Garmin/Apps/Out.bin").hidden
        assert session.lookup(STORAGE, "GARMIN/Apps/Nowhere") is None
        assert session.lookup(STORAGE, "GARMIN/Apps/OUT.BIN/deeper") is None
    assert not any(t[6:8] == b"\x02\x90" and struct.unpack_from("<I", t, 16)[0] == ptp.ROOT
                   for t in device.transfers)  # the hidden listing is not asked for the root


def test_device_without_garmins_listing(device):
    device.supports_hidden = False
    with ptp.Session(device) as session:
        assert session.lookup(STORAGE, "GARMIN/Apps/Media") is None
        assert session.lookup(STORAGE, "GARMIN/Apps/OUT.BIN").size == 5
        with pytest.raises(ptp.PtpRefused) as error:
            session.hidden_children(STORAGE, device.handle("GARMIN/Apps"))
        assert error.value.code == ptp.OPERATION_NOT_SUPPORTED
        assert session.storages() == [STORAGE]  # a refusal does not break the session


def test_other_refusal_of_the_hidden_listing_is_an_error(device):
    device.refuse[ptp.GARMIN_GET_OBJECT_IDS] = ptp.GENERAL_ERROR
    with ptp.Session(device) as session, pytest.raises(ptp.PtpRefused):
        session.lookup(STORAGE, "GARMIN/Apps/Media")


# --- writing -----------------------------------------------------------------------------

def test_send_file_into_the_hidden_folder(device):
    media = device.handle("GARMIN/Apps/Media")
    with ptp.Session(device) as session:
        before = len(device.transfers)
        written = session.send_file(STORAGE, media, "0A1B2C3D.PRG", APP)
        sent = device.transfers[before:]
    assert (written.name, written.size, written.protection, written.hidden) == (
        "0A1B2C3D.PRG", len(APP), 0, False)
    assert device.objects[written.handle].data == APP
    # The fresh file is in the ordinary listing; the old one stays hidden.
    assert device.names("GARMIN/Apps/Media", hidden=False) == ["0A1B2C3D.PRG"]
    assert device.names("GARMIN/Apps/Media", hidden=True) == ["A1B2C3D4.PRG"]
    # Storage and parent are parameters of SendObjectInfo; header and data go separately.
    info = next(i for i, t in enumerate(sent) if t[6:8] == b"\x0c\x10" and t[4:6] == b"\x01\x00")
    assert struct.unpack_from("<II", sent[info], 12) == (STORAGE, media)
    assert len(sent[info + 1]) == 12 and sent[info + 2] == ptp.object_info_dataset(
        "0A1B2C3D.PRG", len(APP))
    assert sent[info + 3][6:8] == b"\x0d\x10" and len(sent[info + 3]) == 12  # no parameters
    assert len(sent[info + 4]) == 12 and sent[info + 5] == APP
    # The content of an app file is not read back: the device returns it encrypted.
    assert device.sent(ptp.GET_OBJECT) == 0


def test_app_file_reads_back_different(device):
    with ptp.Session(device) as session:
        written = session.send_file(STORAGE, device.handle("GARMIN/Apps/Media"), "X.PRG", APP)
        assert session.read_file(written.handle) != APP
        with pytest.raises(ptp.PtpWriteError):
            session.send_file(STORAGE, device.handle("GARMIN/Apps/Media"), "Y.PRG", APP,
                              verify_content=True)


def test_plain_file_is_verified_by_content(device):
    apps = device.handle("GARMIN/Apps")
    with ptp.Session(device) as session:
        written = session.send_file(STORAGE, apps, "IN.BIN", b"request", verify_content=True)
        assert session.read_file(written.handle) == b"request"
    assert device.names("GARMIN/Apps", hidden=False) == ["IN.BIN", "OUT.BIN"]


@pytest.mark.parametrize("size", [0, 511, 512, 1024, 1500])
def test_zero_length_packet_after_a_full_last_packet(device, size):
    apps = device.handle("GARMIN/Apps")
    with ptp.Session(device) as session:
        before = len(device.transfers)
        session.send_file(STORAGE, apps, "IN.BIN", b"x" * size, verify_content=True)
        sent = device.transfers[before:]
    start = next(i for i, t in enumerate(sent) if t[6:8] == b"\x0d\x10" and t[4:6] == b"\x01\x00")
    after_header = sent[start + 2:start + 4]
    if size == 0:
        assert after_header[0] == b""  # nothing but the packet that ends the transfer
    elif size % 512 == 0:
        assert after_header == [b"x" * size, b""]
    else:
        assert after_header[0] == b"x" * size and after_header[1] != b""


def test_large_file_is_sent_in_chunks(device, monkeypatch):
    monkeypatch.setattr(ptp, "WRITE_CHUNK", 1024)
    data = bytes(range(256)) * 10 + b"tail"
    with ptp.Session(device) as session:
        written = session.send_file(STORAGE, device.handle("GARMIN/Apps"), "IN.BIN", data,
                                    verify_content=True)
    assert device.objects[written.handle].data == data
    assert sum(1 for t in device.transfers if len(t) == 1024) == 2


def test_existing_name_is_refused_before_anything_is_sent(device):
    media = device.handle("GARMIN/Apps/Media")
    with ptp.Session(device) as session:
        with pytest.raises(ptp.PtpWriteError, match="already exists"):
            session.send_file(STORAGE, media, "a1b2c3d4.prg", APP)  # hidden, other case
        with pytest.raises(ptp.PtpWriteError, match="already exists"):
            session.send_file(STORAGE, device.handle("GARMIN/Apps"), "OUT.BIN", b"x")
    assert device.sent(ptp.SEND_OBJECT_INFO) == 0


def test_nothing_is_written_into_the_root(device):
    with ptp.Session(device) as session, pytest.raises(ptp.PtpWriteError):
        session.send_file(STORAGE, ptp.ROOT, "X.PRG", APP)
    assert device.sent(ptp.SEND_OBJECT_INFO) == 0


def test_refused_object_info_writes_nothing(device):
    device.refuse[ptp.SEND_OBJECT_INFO] = ptp.ACCESS_DENIED
    with ptp.Session(device) as session:
        with pytest.raises(ptp.PtpRefused) as error:
            session.send_file(STORAGE, device.handle("GARMIN/Apps/Media"), "X.PRG", APP)
        assert session.storages() == [STORAGE]
    assert error.value.operation == ptp.SEND_OBJECT_INFO
    assert device.sent(ptp.SEND_OBJECT) == 0
    assert device.names("GARMIN/Apps/Media") == ["A1B2C3D4.PRG"]
    assert not device.session_open and device.closed


def test_refused_object_reports_the_handle_left_behind(device):
    device.refuse[ptp.SEND_OBJECT] = ptp.STORE_FULL
    with ptp.Session(device) as session, pytest.raises(ptp.PtpWriteError) as error:
        session.send_file(STORAGE, device.handle("GARMIN/Apps/Media"), "X.PRG", APP)
    assert device.objects[error.value.handle].name == "X.PRG" and "StoreFull" in str(error.value)
    assert not device.session_open


def test_full_storage(device):
    device.free = 100
    with ptp.Session(device) as session, pytest.raises(ptp.PtpRefused) as error:
        session.send_file(STORAGE, device.handle("GARMIN/Apps/Media"), "X.PRG", APP)
    assert error.value.code == ptp.STORE_FULL


def test_wrong_size_after_writing(device):
    device.truncate_written = 10
    with ptp.Session(device) as session, pytest.raises(ptp.PtpWriteError) as error:
        session.send_file(STORAGE, device.handle("GARMIN/Apps/Media"), "X.PRG", APP)
    assert error.value.handle is not None and "not written correctly" in str(error.value)


def test_the_device_refuses_new_folders(device):
    """The module never tries; this pins the fake to what the fēnix 8 does."""
    with ptp.Session(device) as session:
        dataset = bytearray(ptp.object_info_dataset("MEDIA", 0))
        struct.pack_into("<H", dataset, 4, ptp.FORMAT_FOLDER)
        code, _data, _params = session.transact(
            ptp.SEND_OBJECT_INFO, STORAGE, device.handle("GARMIN/Apps"), data_out=bytes(dataset))
    assert code == ptp.ACCESS_DENIED
    assert not hasattr(ptp.Session, "make_dir")


# --- files larger than memory: sent from a file, read back in pieces ----------------------

def _sent_after_the_header(transfers: list[bytes]) -> list[bytes]:
    """The transfers of the data phase of the last SendObject, after its header."""
    start = max(i for i, t in enumerate(transfers) if t[6:8] == b"\x0d\x10" and t[4:6] == b"\x01\x00")
    return transfers[start + 2:]


def test_a_file_is_sent_from_a_stream_as_the_bytes_would_be(device, monkeypatch):
    """The same transfers as from bytes, read 1 KiB at a time here, the bytes sent told after
    each piece."""
    import io

    monkeypatch.setattr(ptp, "WRITE_CHUNK", 1024)
    data = bytes(range(256)) * 10 + b"tail"
    apps = device.handle("GARMIN/Apps")
    with ptp.Session(device) as session:
        session.send_object(STORAGE, apps, "A.BIN", data)
        from_bytes = _sent_after_the_header(device.transfers)
        told = []
        handle = session.send_object(STORAGE, apps, "B.BIN",
                                     ptp.Outgoing(io.BytesIO(data), len(data), told.append))
        from_stream = _sent_after_the_header(device.transfers)
    assert from_stream == from_bytes and device.objects[handle].data == data
    assert told == [1024, 2048, len(data)]


@pytest.mark.parametrize("size", [0, 512, 1024])
def test_a_stream_ends_with_the_packet_a_full_last_packet_needs(device, size):
    import io

    with ptp.Session(device) as session:
        session.send_object(STORAGE, device.handle("GARMIN/Apps"), "A.BIN",
                            ptp.Outgoing(io.BytesIO(b"x" * size), size))
        sent = _sent_after_the_header(device.transfers)
    assert sent[-2:] == ([b""] if size == 0 else [b"x" * size, b""])[-2:]


def test_only_the_size_announced_is_sent_from_a_longer_stream(device):
    import io

    with ptp.Session(device) as session:
        handle = session.send_object(STORAGE, device.handle("GARMIN/Apps"), "A.BIN",
                                     ptp.Outgoing(io.BytesIO(b"announced and more"), 9))
    assert device.objects[handle].data == b"announced"


def test_a_stream_that_ends_early_stops_the_conversation(device):
    import io

    with ptp.Session(device) as session:
        with pytest.raises(ptp.PtpWriteError, match="ended before"):
            session.send_object(STORAGE, device.handle("GARMIN/Apps"), "A.BIN",
                                ptp.Outgoing(io.BytesIO(b"short"), 100))
        sent = len(device.transfers)
        with pytest.raises(ptp.PtpError):
            session.storages()
        assert len(device.transfers) == sent  # out of step: nothing more is sent
    assert device.closed


def test_an_object_is_read_in_pieces_never_whole(device, monkeypatch):
    monkeypatch.setattr(ptp, "READ_CHUNK", 1000)
    data = bytes(range(256)) * 20  # 5120 bytes: more than five pieces
    handle = device.add("GARMIN/Apps/BIG.BIN", data)
    pieces = []
    with ptp.Session(device) as session:
        assert session.read_into(handle, pieces.append) == len(data)
        assert session.storages() == [STORAGE]  # the conversation goes on in step
    assert b"".join(pieces) == data and max(len(p) for p in pieces) <= 1000


def test_a_large_object_is_read_in_parts_where_the_device_reads_parts(device, monkeypatch):
    """A fēnix 8 cut a GetObject of a whole file short about once in three hundred, and sent
    1.1 MB/s; in parts of 256 KB none was cut, at 5.4 MB/s."""
    monkeypatch.setattr(ptp, "READ_PART", 1000)
    data = bytes(range(256)) * 20  # 5120 bytes: six parts
    handle = device.add("GARMIN/Activity/RUN.FIT", data)
    pieces = []
    with ptp.Session(device) as session:
        read = session.read_into(handle, pieces.append, size=len(data), in_parts=True)
        assert read == len(data)
    assert b"".join(pieces) == data
    assert device.operations.count(ptp.GET_PARTIAL_OBJECT) == 6
    assert ptp.GET_OBJECT not in device.operations


@pytest.mark.parametrize("why", ["not listed", "refused"])
def test_a_device_that_does_not_read_parts_reads_the_object_whole(device, monkeypatch, why):
    monkeypatch.setattr(ptp, "READ_PART", 1000)
    if why == "not listed":
        device.supported.remove(ptp.GET_PARTIAL_OBJECT)
    else:
        device.refuse[ptp.GET_PARTIAL_OBJECT] = ptp.OPERATION_NOT_SUPPORTED
    data = b"z" * 5000
    handle = device.add("GARMIN/Activity/RUN.FIT", data)
    pieces = []
    with ptp.Session(device) as session:
        read = session.read_into(handle, pieces.append, size=len(data), in_parts=True)
        assert read == len(data)
    assert b"".join(pieces) == data and ptp.GET_OBJECT in device.operations


def test_an_object_is_read_whole_unless_parts_are_asked_for(device, monkeypatch):
    monkeypatch.setattr(ptp, "READ_PART", 1000)
    handle = device.add("GARMIN/Apps/APP.PRG", b"app" * 1000)
    with ptp.Session(device) as session:
        session.read_into(handle, lambda piece: None, size=3000)
    assert ptp.GET_PARTIAL_OBJECT not in device.operations


def test_an_object_read_in_pieces_split_over_many_reads(device, monkeypatch):
    monkeypatch.setattr(ptp, "READ_CHUNK", 1000)
    device.read_chunk = 7
    data = bytes(range(256)) * 9
    handle = device.add("GARMIN/Apps/BIG.BIN", data)
    pieces = []
    with ptp.Session(device) as session:
        session.read_into(handle, pieces.append)
        assert session.read_file(handle) == data
    assert b"".join(pieces) == data


def test_a_read_refused_gives_nothing_to_the_reader(device):
    device.refuse[ptp.GET_OBJECT] = ptp.ACCESS_DENIED
    pieces = []
    with ptp.Session(device) as session:
        with pytest.raises(ptp.PtpRefused) as error:
            session.read_into(device.handle("GARMIN/Apps/OUT.BIN"), pieces.append)
        assert session.storages() == [STORAGE]  # a refusal leaves the conversation in step
    assert error.value.code == ptp.ACCESS_DENIED and pieces == []


def test_a_part_of_an_object(device):
    data = bytes(range(256)) * 4
    handle = device.add("GARMIN/Apps/BIG.BIN", data)
    with ptp.Session(device) as session:
        assert session.read_part(handle, 300, 100) == data[300:400]
        assert session.read_part(handle, 1000, 100) == data[1000:]  # the end of the object
    command_sent = next(t for t in device.transfers if t[6:8] == b"\x1b\x10")
    assert struct.unpack_from("<III", command_sent, 12) == (handle, 300, 100)


def test_a_device_that_does_not_read_parts_says_so(device):
    device.refuse[ptp.GET_PARTIAL_OBJECT] = ptp.OPERATION_NOT_SUPPORTED
    with ptp.Session(device) as session:
        with pytest.raises(ptp.PtpRefused) as error:
            session.read_part(device.handle("GARMIN/Apps/OUT.BIN"), 0, 2)
        assert session.storages() == [STORAGE]
    assert error.value.code == ptp.OPERATION_NOT_SUPPORTED


# --- how long the device may take to answer ---------------------------------------------------

@pytest.fixture
def large(monkeypatch):
    """Objects over 1 000 bytes count as large here; at least 123 s for an answer, at the
    slowest 10 bytes a second."""
    monkeypatch.setattr(ptp, "LARGE_OBJECT", 1000)
    monkeypatch.setattr(ptp, "LARGE_ANSWER_MS", 123_000)
    monkeypatch.setattr(ptp, "SLOWEST_PACE", 10)


def test_the_limit_for_an_answer_grows_with_a_large_object(large):
    assert ptp.answer_limit_ms(None) == ptp.TIMEOUT_MS
    assert ptp.answer_limit_ms(1000) == ptp.TIMEOUT_MS  # not over the limit
    assert ptp.answer_limit_ms(1001) == 123_000  # at least
    assert ptp.answer_limit_ms(2000) == 200_000  # 2 000 bytes at 10 a second


def test_after_a_large_file_is_sent_the_device_has_longer_to_answer(device, large):
    import io

    data = b"x" * 2000
    with ptp.Session(device) as session:
        before = len(device.read_limits)
        session.send_object(STORAGE, device.handle("GARMIN/Apps"), "BIG.BIN",
                            ptp.Outgoing(io.BytesIO(data), len(data)))
        limits = device.read_limits[before:]
        assert device.timeout_ms == ptp.TIMEOUT_MS  # as it was, after the transaction
        session.storages()
    # SendObjectInfo answered at the usual limit, SendObject's answer at the longer one.
    assert limits[0] == ptp.TIMEOUT_MS and limits[-1] == 200_000
    assert device.read_limits[-1] == ptp.TIMEOUT_MS


def test_a_small_file_keeps_the_usual_limit(device, large):
    with ptp.Session(device) as session:
        before = len(device.read_limits)
        session.send_object(STORAGE, device.handle("GARMIN/Apps"), "SMALL.BIN", b"x" * 10)
        assert set(device.read_limits[before:]) == {ptp.TIMEOUT_MS}


def test_a_large_file_read_back_and_deleted_has_longer_to_answer(device, large):
    handle = device.add("GARMIN/Apps/BIG.BIN", b"y" * 2000)
    with ptp.Session(device) as session:
        before = len(device.read_limits)
        session.read_into(handle, lambda piece: None, size=2000)
        read = device.read_limits[before:]
        before = len(device.read_limits)
        session.delete(handle, size=2000)
        deleted = device.read_limits[before:]
    assert set(read) == {200_000} and set(deleted) == {200_000}


def test_no_answer_in_time_after_a_large_file_breaks_the_session(device, large):
    import io

    with ptp.Session(device) as session:
        session.send_object(STORAGE, device.handle("GARMIN/Apps"), "A.BIN", b"a")
        device.silent = True
        with pytest.raises(ptp.PtpError, match="did not answer"):
            session.send_object(STORAGE, device.handle("GARMIN/Apps"), "BIG.BIN",
                                ptp.Outgoing(io.BytesIO(b"x" * 2000), 2000))
        assert session.broken  # nothing more is sent, nothing is tried again here


# --- deleting ----------------------------------------------------------------------------

def test_delete_a_fresh_file(device):
    media = device.handle("GARMIN/Apps/Media")
    with ptp.Session(device) as session:
        written = session.send_file(STORAGE, media, "X.PRG", APP)
        session.delete(written.handle)
    assert device.names("GARMIN/Apps/Media") == ["A1B2C3D4.PRG"]
    assert device.transfers[-2][12:] == struct.pack("<II", written.handle, 0)


def test_protected_app_file_can_be_deleted(device):
    """Like the fēnix 8: a file the device took over is deleted by the ordinary operation."""
    with ptp.Session(device) as session:
        session.delete(device.handle("GARMIN/Apps/Media/A1B2C3D4.PRG"))
    assert device.names("GARMIN/Apps/Media") == []


def test_refused_delete_is_reported(device):
    device.delete_protected = ptp.OBJECT_WRITE_PROTECTED
    with ptp.Session(device) as session, pytest.raises(ptp.PtpRefused) as error:
        session.delete(device.handle("GARMIN/Apps/Media/A1B2C3D4.PRG"))
    assert error.value.code == ptp.OBJECT_WRITE_PROTECTED
    assert device.names("GARMIN/Apps/Media") == ["A1B2C3D4.PRG"]


# --- the whole sequence of an install ----------------------------------------------------

def test_install_sequence_and_take_over(device):
    with ptp.Session(device) as session:
        storage = session.storages()[0]
        media = session.lookup(storage, "GARMIN/APPS/MEDIA")
        apps = session.lookup(storage, "GARMIN/APPS")
        assert session.lookup(storage, "GARMIN/APPS/IN.BIN") is None
        assert session.free_space(storage) > len(APP)
        session.send_file(storage, media.handle, "0A1B2C3D.PRG", APP)
        session.send_file(storage, apps.handle, "IN.BIN", b"app list", verify_content=True)
    device.take_over("GARMIN/Apps/Media")
    device.reconnect()
    with ptp.Session(device) as session:
        media = session.lookup(STORAGE, "GARMIN/Apps/Media")
        assert session.children(STORAGE, media.handle) == []
        assert sorted((o.name, o.protection) for o in session.hidden_children(
            STORAGE, media.handle)) == [("0A1B2C3D.PRG", 1), ("A1B2C3D4.PRG", 1)]
    # Only these operations were ever sent.
    assert set(device.operations) == {
        ptp.OPEN_SESSION, ptp.CLOSE_SESSION, ptp.GET_STORAGE_IDS, ptp.GET_STORAGE_INFO,
        ptp.GET_OBJECT_HANDLES, ptp.GET_OBJECT_INFO, ptp.GARMIN_GET_OBJECT_IDS,
        ptp.SEND_OBJECT_INFO, ptp.SEND_OBJECT, ptp.GET_OBJECT}


# --- libusb ------------------------------------------------------------------------------

def test_libusb_loads():
    """Only loads the library; no USB device is listed or opened."""
    try:
        lib = ptp.load_libusb()
    except ptp.UsbUnavailable:
        pytest.skip("libusb-1.0 is not installed")
    assert lib.libusb_error_name(-7) == b"LIBUSB_ERROR_TIMEOUT"


def test_bulk_interface_is_found_in_a_descriptor():
    endpoints = (ptp._Endpoint * 3)()
    endpoints[0].bEndpointAddress, endpoints[0].bmAttributes = 0x83, 3  # interrupt in
    endpoints[1].bEndpointAddress, endpoints[1].bmAttributes = 0x81, 2  # bulk in
    endpoints[2].bEndpointAddress, endpoints[2].bmAttributes = 0x02, 2  # bulk out
    endpoints[2].wMaxPacketSize = 512
    alt = ptp._InterfaceDescriptor(bInterfaceNumber=1, bNumEndpoints=3, endpoint=endpoints)
    other = ptp._InterfaceDescriptor(bInterfaceNumber=0, bNumEndpoints=0)
    interfaces = (ptp._Interface * 2)()
    interfaces[0].altsetting, interfaces[0].num_altsetting = ptp.ctypes.pointer(other), 1
    interfaces[1].altsetting, interfaces[1].num_altsetting = ptp.ctypes.pointer(alt), 1
    config = ptp._Config(bNumInterfaces=2, interface=interfaces)
    assert ptp._bulk_interface(config) == (1, 0x02, 0x81, 512)
    assert ptp._bulk_interface(ptp._Config(bNumInterfaces=1, interface=interfaces)) is None


# --- a device opened through its node (a Flatpak with --device=usb) -----------------------


class FakeLibusb:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append(name) or 0


def test_a_device_opened_through_its_node_closes_the_descriptor(tmp_path, monkeypatch):
    path = tmp_path / "014"
    path.write_bytes(b"")
    given = []

    def from_fd(cls, fd):
        given.append(fd)
        return cls(FakeLibusb(), None, 1, 0, 1, 0x81, 512)

    monkeypatch.setattr(ptp.LibusbTransport, "from_fd", classmethod(from_fd))
    transport = ptp.LibusbTransport.open_node(path)
    os.fstat(given[0])  # open while the transport is
    transport.close()
    assert transport._lib.calls == ["libusb_release_interface", "libusb_close", "libusb_exit"]
    with pytest.raises(OSError):
        os.fstat(given[0])
    transport.close()  # a second close does nothing


def test_a_node_that_cannot_be_used_leaves_no_descriptor_open(tmp_path, monkeypatch):
    path = tmp_path / "014"
    path.write_bytes(b"")
    given = []

    def refused(cls, fd):
        given.append(fd)
        raise ptp.UsbUnavailable("busy")

    monkeypatch.setattr(ptp.LibusbTransport, "from_fd", classmethod(refused))
    with pytest.raises(ptp.UsbUnavailable, match="busy"):
        ptp.LibusbTransport.open_node(path)
    with pytest.raises(OSError):
        os.fstat(given[0])
    with pytest.raises(ptp.UsbUnavailable, match="cannot be opened"):
        ptp.LibusbTransport.open_node(tmp_path / "missing")



# --- a device opened by its place on USB or by a descriptor: what is released after a failure --


class RecordingLibusb:
    """libusb as LibusbTransport opens a device with it: each call kept, with the device's place
    on USB or the descriptor; the devices on USB (bus, device number), the context (7) and
    the handle (99) made up. `fails`: the call ("init", "open", "wrap") that answers with an
    error, giving nothing."""

    CONTEXT, HANDLE = 7, 99

    def __init__(self, devices=((3, 14),), fails=None):
        self.calls, self.devices, self.fails = [], list(devices), fails
        self._list = (ptp.ctypes.c_void_p * len(self.devices))(
            *range(1, len(self.devices) + 1))

    def _gives(self, call, ref, value):
        self.calls.append(call)
        if (call if isinstance(call, str) else call[0]) == self.fails:
            return -1
        ref._obj.value = value
        return 0

    def libusb_init(self, ref):
        return self._gives("init", ref, self.CONTEXT)

    def libusb_set_option(self, context, option):
        self.calls.append(("set_option", context, option))

    def libusb_get_device_list(self, context, ref):
        self.calls.append("get_device_list")
        ref._obj.contents = ptp.ctypes.c_void_p.from_address(ptp.ctypes.addressof(self._list))
        return len(self.devices)

    def libusb_free_device_list(self, devices, unref):
        self.calls.append("free_device_list")

    def libusb_get_bus_number(self, device):
        return self.devices[device.value - 1][0]

    def libusb_get_device_address(self, device):
        return self.devices[device.value - 1][1]

    def libusb_open(self, device, ref):
        return self._gives(("open", *self.devices[device.value - 1]), ref, self.HANDLE)

    def libusb_wrap_sys_device(self, context, fd, ref):
        return self._gives(("wrap", context.value, fd), ref, self.HANDLE)

    def libusb_close(self, handle):
        self.calls.append(("close", handle.value))

    def libusb_exit(self, context):
        self.calls.append(("exit", context.value))

    def libusb_error_name(self, code):
        return b"LIBUSB_ERROR_IO"


@pytest.fixture
def libusb(monkeypatch):
    """A RecordingLibusb (`libusb.lib`, which a test may replace) and the claim of the
    interface, recorded with the context and the handle: it raises `libusb.claim_fails` when
    that is set."""
    state = types.SimpleNamespace(lib=RecordingLibusb(), claim_fails=None)

    def claim(cls, lib, context, handle):
        lib.calls.append(("claim", context.value, handle.value))
        if state.claim_fails is not None:
            raise state.claim_fails
        return "transport"

    monkeypatch.setattr(ptp, "load_libusb", lambda: state.lib)
    monkeypatch.setattr(ptp.LibusbTransport, "_claim", classmethod(claim))
    return state


CLOSE, EXIT = ("close", 99), ("exit", 7)
FOUND = ["init", "get_device_list", ("open", 3, 14), "free_device_list"]


def test_a_device_opened_by_its_place_on_usb(libusb):
    libusb.lib = RecordingLibusb([(1, 5), (3, 14)])  # another device first
    assert ptp.LibusbTransport.open(3, 14) == "transport"
    assert libusb.lib.calls == [*FOUND, ("claim", 7, 99)]  # nothing released: it is in use


@pytest.mark.parametrize("fails, calls", [
    ("init", ["init"]),
    ("open", [*FOUND, EXIT]),
], ids=["no context", "not opened"])
def test_a_device_libusb_does_not_open_releases_what_it_got(libusb, fails, calls):
    libusb.lib.fails = fails
    with pytest.raises(ptp.UsbUnavailable, match="cannot be used: LIBUSB_ERROR_IO"):
        ptp.LibusbTransport.open(3, 14)
    assert libusb.lib.calls == calls


def test_a_device_not_on_usb_releases_the_context(libusb):
    with pytest.raises(ptp.UsbUnavailable, match="was not found"):
        ptp.LibusbTransport.open(3, 15)
    assert libusb.lib.calls == ["init", "get_device_list", "free_device_list", EXIT]


@pytest.mark.parametrize("error", [ptp.UsbUnavailable("no interface"), KeyboardInterrupt()],
                         ids=["refused", "interrupted"])
def test_a_device_whose_interface_is_not_claimed_is_closed(libusb, error):
    libusb.claim_fails = error
    with pytest.raises(type(error)):
        ptp.LibusbTransport.open(3, 14)
    assert libusb.lib.calls == [*FOUND, ("claim", 7, 99), CLOSE, EXIT]


WRAPPED = [("set_option", None, ptp._LIBUSB_OPTION_NO_DEVICE_DISCOVERY), "init", ("wrap", 7, 5)]


def test_a_device_opened_by_its_descriptor(libusb):
    assert ptp.LibusbTransport.from_fd(5) == "transport"
    assert libusb.lib.calls == [*WRAPPED, ("claim", 7, 99)]


@pytest.mark.parametrize("fails, calls", [
    ("init", WRAPPED[:2]),
    ("wrap", [*WRAPPED, EXIT]),
], ids=["no context", "not taken"])
def test_a_descriptor_libusb_does_not_take_releases_what_it_got(libusb, fails, calls):
    libusb.lib.fails = fails
    with pytest.raises(ptp.UsbUnavailable, match="cannot be used: LIBUSB_ERROR_IO"):
        ptp.LibusbTransport.from_fd(5)
    assert libusb.lib.calls == calls


@pytest.mark.parametrize("error", [ptp.UsbUnavailable("no interface"), KeyboardInterrupt()],
                         ids=["refused", "interrupted"])
def test_a_descriptor_whose_interface_is_not_claimed_is_closed(libusb, error):
    libusb.claim_fails = error
    with pytest.raises(type(error)):
        ptp.LibusbTransport.from_fd(5)
    assert libusb.lib.calls == [*WRAPPED, ("claim", 7, 99), CLOSE, EXIT]
