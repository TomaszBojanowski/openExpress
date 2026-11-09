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
