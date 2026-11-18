"""A minimal PTP/MTP client over USB for the files a Garmin device hides from ordinary MTP;
everything else stays with gvfs. gvfs (libmtp) shows only what the standard
`GetObjectHandles` returns, and some devices return the Connect IQ app folder only through
Garmin's operation 0x9002, so app files cannot be written there through gvfs.

Layers: a transport of bulk transfers to one USB device (`LibusbTransport`, libusb-1.0
through ctypes), `Session` (PTP containers and transactions), and the file operations on
top: list, look up, send, read, delete. The device must not be in use by another MTP client
(unmount it from gvfs first). Nothing here creates folders.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import io
import logging
import os
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from openexpress import applog
from openexpress.device.storage import DeviceError, named_in_any_case
from openexpress.i18n import _

log = logging.getLogger(__name__)

# Operations. The 0x9xxx one is Garmin's extension.
GET_DEVICE_INFO = 0x1001
OPEN_SESSION = 0x1002
CLOSE_SESSION = 0x1003
GET_STORAGE_IDS = 0x1004
GET_STORAGE_INFO = 0x1005
GET_OBJECT_HANDLES = 0x1007
GET_OBJECT_INFO = 0x1008
GET_OBJECT = 0x1009
DELETE_OBJECT = 0x100B
SEND_OBJECT_INFO = 0x100C
SEND_OBJECT = 0x100D
GET_PARTIAL_OBJECT = 0x101B
GARMIN_GET_OBJECT_IDS = 0x9002

OPERATIONS = {
    GET_DEVICE_INFO: "GetDeviceInfo", OPEN_SESSION: "OpenSession", CLOSE_SESSION: "CloseSession",
    GET_STORAGE_IDS: "GetStorageIDs", GET_STORAGE_INFO: "GetStorageInfo",
    GET_OBJECT_HANDLES: "GetObjectHandles", GET_OBJECT_INFO: "GetObjectInfo",
    GET_OBJECT: "GetObject", DELETE_OBJECT: "DeleteObject",
    SEND_OBJECT_INFO: "SendObjectInfo", SEND_OBJECT: "SendObject",
    GET_PARTIAL_OBJECT: "GetPartialObject", GARMIN_GET_OBJECT_IDS: "Garmin GetObjectIds",
}

# Responses.
OK = 0x2001
GENERAL_ERROR = 0x2002
SESSION_NOT_OPEN = 0x2003
OPERATION_NOT_SUPPORTED = 0x2005
INVALID_STORAGE_ID = 0x2008
INVALID_OBJECT_HANDLE = 0x2009
STORE_FULL = 0x200C
OBJECT_WRITE_PROTECTED = 0x200D
STORE_READ_ONLY = 0x200E
ACCESS_DENIED = 0x200F
NO_VALID_OBJECT_INFO = 0x2015
DEVICE_BUSY = 0x2019
INVALID_PARENT_OBJECT = 0x201A
INVALID_PARAMETER = 0x201D
SESSION_ALREADY_OPEN = 0x201E

RESPONSES = {
    OK: "OK", GENERAL_ERROR: "GeneralError", SESSION_NOT_OPEN: "SessionNotOpen",
    OPERATION_NOT_SUPPORTED: "OperationNotSupported", INVALID_STORAGE_ID: "InvalidStorageID",
    INVALID_OBJECT_HANDLE: "InvalidObjectHandle", STORE_FULL: "StoreFull",
    OBJECT_WRITE_PROTECTED: "ObjectWriteProtected", STORE_READ_ONLY: "StoreReadOnly",
    ACCESS_DENIED: "AccessDenied", NO_VALID_OBJECT_INFO: "NoValidObjectInfo",
    DEVICE_BUSY: "DeviceBusy", INVALID_PARENT_OBJECT: "InvalidParentObject",
    INVALID_PARAMETER: "InvalidParameter", SESSION_ALREADY_OPEN: "SessionAlreadyOpen",
}

COMMAND, DATA, RESPONSE = 1, 2, 3
HEADER = struct.Struct("<IHHI")  # length, container type, code, transaction ID

FORMAT_UNDEFINED = 0x3000
FORMAT_FOLDER = 0x3001
ROOT = 0xFFFFFFFF
SESSION_ID = 1
MAX_OBJECT_SIZE = 0xFFFFFFFF - HEADER.size
READ_SIZE = 64 * 1024
READ_CHUNK = 1024 * 1024  # the pieces of a file read back, handed on as they come
WRITE_CHUNK = 1024 * 1024
TIMEOUT_MS = 30000  # each bulk transfer: a piece written, a read
# OpenSession, answered at once by a device that listens: one that does not is mostly stuck in
# a transaction of before (Session.open), not slow.
OPEN_TIMEOUT_MS = 5000
# PTP over USB's class request Cancel (Still Image class, 5.2.1): the transaction the device is
# in, abandoned. Its data: the cancellation code and the transaction's ID.
CANCEL_REQUEST, CANCELLATION_CODE = 0x64, 0x4001
GET_DEVICE_STATUS = 0x67  # its answer: a length, then a response code (OK once ready)
CANCEL_WAIT_S = 2.0  # for the device to be ready after Cancel
DRAIN_TIMEOUT_MS = 300  # a read of what a device still had to send: it sends at once or never
# After the data of a large object, the device may take long to answer (it stores the file whole
# first), as it may to give one back or to delete one: then at least LARGE_ANSWER_MS, more for a
# larger one at SLOWEST_PACE. No answer by then breaks the session; nothing is tried again here.
LARGE_OBJECT = 256 * 1024 * 1024
LARGE_ANSWER_MS = 5 * 60 * 1000
SLOWEST_PACE = 5_000_000  # bytes a second


def answer_limit_ms(size: int | None) -> int:
    """How long the device may take to answer about an object of `size` bytes."""
    if not size or size <= LARGE_OBJECT:
        return TIMEOUT_MS
    return max(LARGE_ANSWER_MS, size * 1000 // SLOWEST_PACE)


class PtpError(DeviceError):
    """The USB connection or the PTP conversation failed."""


class UsbUnavailable(PtpError):
    """The USB device cannot be opened (no access, no libusb, not found)."""


class PtpRefused(PtpError):
    """The device answered an operation with a code other than OK."""

    def __init__(self, operation: int, code: int):
        self.operation, self.code = operation, code
        super().__init__(_("The device refused {operation} ({reason}).").format(
            operation=operation_name(operation), reason=response_name(code)))


class PtpWriteError(PtpError):
    """A file was not written as intended. `handle` is the object left on the device,
    when there is one."""

    def __init__(self, message: str, handle: int | None = None):
        self.handle = handle
        super().__init__(message)


@dataclass
class Outgoing:
    """The data of a SendObject read from a file as it goes, never held whole: `size` bytes of
    `stream`, sent WRITE_CHUNK bytes at a time; `sent` is told the bytes sent so far after each
    piece."""

    stream: BinaryIO
    size: int
    sent: Callable[[int], None] | None = None


def operation_name(code: int) -> str:
    return OPERATIONS.get(code, f"0x{code:04X}")


def response_name(code: int) -> str:
    return RESPONSES.get(code, f"0x{code:04X}")


# --- containers and datasets -------------------------------------------------------------

def command(operation: int, transaction: int, params: tuple[int, ...] = ()) -> bytes:
    return struct.pack(f"<IHHI{len(params)}I", HEADER.size + 4 * len(params), COMMAND,
                       operation, transaction, *params)


def data_header(operation: int, transaction: int, size: int) -> bytes:
    return HEADER.pack(HEADER.size + size, DATA, operation, transaction)


def ptp_string(text: str) -> bytes:
    """Length in characters including the final NUL, then UTF-16LE; empty is one zero byte."""
    if not text:
        return b"\x00"
    encoded = (text + "\x00").encode("utf-16-le")
    if len(encoded) // 2 > 255:
        raise PtpError(_("The name “{name}” is too long for the device.").format(name=text))
    return bytes([len(encoded) // 2]) + encoded


def read_string(data: bytes, offset: int) -> tuple[str, int]:
    """A PTP string at `offset` and the offset just after it."""
    if offset >= len(data):
        raise PtpError(_("The device sent an incomplete answer."))
    end = offset + 1 + 2 * data[offset]
    if end > len(data):
        raise PtpError(_("The device sent an incomplete answer."))
    return data[offset + 1:end].decode("utf-16-le", "replace").rstrip("\x00"), end


def operations_supported(data: bytes) -> list[int]:
    """The operations a DeviceInfo dataset lists (OperationsSupported). Nothing after them is
    read: not the device's maker, model, version nor serial number."""
    if len(data) < 8:  # the standard's version, the vendor's extension and its version
        raise PtpError(_("The device sent an incomplete answer."))
    _words, offset = read_string(data, 8)
    offset += 2  # the functional mode
    if offset + 4 > len(data):
        raise PtpError(_("The device sent an incomplete answer."))
    (count,) = struct.unpack_from("<I", data, offset)
    if offset + 4 + 2 * count > len(data):
        raise PtpError(_("The device sent an incomplete answer."))
    return list(struct.unpack_from(f"<{count}H", data, offset + 4))


# The operations the log names, with whether the device lists them.
_TOLD = ((GET_PARTIAL_OBJECT, "GetPartialObject"), (SEND_OBJECT, "SendObject"),
         (DELETE_OBJECT, "DeleteObject"), (GARMIN_GET_OBJECT_IDS, "Garmin's hidden listing"))


def operations_line(session: Session) -> str:
    """One line for the log: the operations the device lists in its DeviceInfo, those that
    matter by name, then all of them by code; or that it did not list them."""
    try:
        listed = session.operations()
    except PtpRefused as e:
        return f"the device did not list its operations ({response_name(e.code)})"
    named = ", ".join(f"{name} {'yes' if code in listed else 'no'}" for code, name in _TOLD)
    codes = " ".join(f"0x{code:04X}" for code in sorted(set(listed)))
    return f"the device lists {len(listed)} operations; {named}; all: {codes}"


def u32_array(data: bytes) -> list[int]:
    if len(data) < 4:
        return []
    count = struct.unpack_from("<I", data)[0]
    if len(data) < 4 + 4 * count:
        raise PtpError(_("The device sent an incomplete answer."))
    return list(struct.unpack_from(f"<{count}I", data, 4))


def object_info_dataset(name: str, size: int) -> bytes:
    """The description of a file to send, the way Garmin Express sends it: format and
    thumbnail format "undefined", the size and the name; every other field zero or empty.
    Storage and parent travel as parameters of `SendObjectInfo`, not here."""
    head = struct.pack("<IHHI", 0, FORMAT_UNDEFINED, 0, size)
    rest = struct.pack("<HIIIIIIIHII", FORMAT_UNDEFINED, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    return head + rest + ptp_string(name) + ptp_string("") + ptp_string("") + ptp_string("")


@dataclass(frozen=True)
class ObjectInfo:
    handle: int
    name: str
    size: int
    format: int
    protection: int
    parent: int
    storage: int
    hidden: bool = False  # returned only by Garmin's listing, not by the standard one

    @property
    def is_dir(self) -> bool:
        return self.format == FORMAT_FOLDER


def parse_object_info(handle: int, data: bytes, hidden: bool = False) -> ObjectInfo:
    if len(data) < 53:
        raise PtpError(_("The device sent an incomplete answer."))
    storage, fmt, protection, size = struct.unpack_from("<IHHI", data)
    parent = struct.unpack_from("<I", data, 38)[0]
    name, _end = read_string(data, 52)
    return ObjectInfo(handle, name, size, fmt, protection, parent, storage, hidden)


# --- transport ---------------------------------------------------------------------------

class Transport(Protocol):
    """Bulk transfers to one device. `write` sends exactly one transfer (an empty one is
    a zero-length packet); `read` returns what one transfer brought, possibly nothing."""

    packet_size: int
    timeout_ms: int  # of each bulk transfer; the session lengthens it while a large answer is due

    def write(self, data: bytes) -> None: ...

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...

    # Optional (Session.open asks for it with getattr): the transaction the device may still be
    # in cancelled. Never raises.
    # def cancel(self) -> None: ...


class _Endpoint(ctypes.Structure):
    _fields_ = [("bLength", ctypes.c_uint8), ("bDescriptorType", ctypes.c_uint8),
                ("bEndpointAddress", ctypes.c_uint8), ("bmAttributes", ctypes.c_uint8),
                ("wMaxPacketSize", ctypes.c_uint16), ("bInterval", ctypes.c_uint8),
                ("bRefresh", ctypes.c_uint8), ("bSynchAddress", ctypes.c_uint8),
                ("extra", ctypes.c_void_p), ("extra_length", ctypes.c_int)]


class _InterfaceDescriptor(ctypes.Structure):
    _fields_ = [("bLength", ctypes.c_uint8), ("bDescriptorType", ctypes.c_uint8),
                ("bInterfaceNumber", ctypes.c_uint8), ("bAlternateSetting", ctypes.c_uint8),
                ("bNumEndpoints", ctypes.c_uint8), ("bInterfaceClass", ctypes.c_uint8),
                ("bInterfaceSubClass", ctypes.c_uint8), ("bInterfaceProtocol", ctypes.c_uint8),
                ("iInterface", ctypes.c_uint8), ("endpoint", ctypes.POINTER(_Endpoint)),
                ("extra", ctypes.c_void_p), ("extra_length", ctypes.c_int)]


class _Interface(ctypes.Structure):
    _fields_ = [("altsetting", ctypes.POINTER(_InterfaceDescriptor)),
                ("num_altsetting", ctypes.c_int)]


class _Config(ctypes.Structure):
    _fields_ = [("bLength", ctypes.c_uint8), ("bDescriptorType", ctypes.c_uint8),
                ("wTotalLength", ctypes.c_uint16), ("bNumInterfaces", ctypes.c_uint8),
                ("bConfigurationValue", ctypes.c_uint8), ("iConfiguration", ctypes.c_uint8),
                ("bmAttributes", ctypes.c_uint8), ("MaxPower", ctypes.c_uint8),
                ("interface", ctypes.POINTER(_Interface)),
                ("extra", ctypes.c_void_p), ("extra_length", ctypes.c_int)]


_ENDPOINT_IN = 0x80
_TRANSFER_BULK = 2
_LIBUSB_ERROR_TIMEOUT = -7
_LIBUSB_OPTION_NO_DEVICE_DISCOVERY = 2
_libusb = None


def load_libusb():
    global _libusb
    if _libusb is not None:
        return _libusb
    name = ctypes.util.find_library("usb-1.0") or "libusb-1.0.so.0"
    try:
        lib = ctypes.CDLL(name, use_errno=True)
    except OSError as e:
        raise UsbUnavailable(_("libusb is not available: {error}").format(error=e)) from e
    p, pp, c_int = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int
    signatures = {
        "libusb_init": (c_int, [pp]),
        "libusb_exit": (None, [p]),
        "libusb_get_device_list": (ctypes.c_ssize_t, [p, ctypes.POINTER(pp)]),
        "libusb_free_device_list": (None, [pp, c_int]),
        "libusb_get_bus_number": (ctypes.c_uint8, [p]),
        "libusb_get_device_address": (ctypes.c_uint8, [p]),
        "libusb_open": (c_int, [p, pp]),
        "libusb_wrap_sys_device": (c_int, [p, ctypes.c_ssize_t, pp]),
        "libusb_get_device": (p, [p]),
        "libusb_close": (None, [p]),
        "libusb_get_active_config_descriptor": (
            c_int, [p, ctypes.POINTER(ctypes.POINTER(_Config))]),
        "libusb_free_config_descriptor": (None, [ctypes.POINTER(_Config)]),
        "libusb_set_auto_detach_kernel_driver": (c_int, [p, c_int]),
        "libusb_claim_interface": (c_int, [p, c_int]),
        "libusb_release_interface": (c_int, [p, c_int]),
        "libusb_bulk_transfer": (c_int, [p, ctypes.c_ubyte, ctypes.c_char_p, c_int,
                                         ctypes.POINTER(c_int), ctypes.c_uint]),
        "libusb_error_name": (ctypes.c_char_p, [c_int]),
        "libusb_control_transfer": (c_int, [p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint16,
                                            ctypes.c_uint16, ctypes.c_char_p, ctypes.c_uint16,
                                            ctypes.c_uint]),
    }
    for function, (restype, argtypes) in signatures.items():
        if function == "libusb_wrap_sys_device" and not hasattr(lib, function):
            continue  # older libusb; only `from_fd` needs it
        getattr(lib, function).restype = restype
        getattr(lib, function).argtypes = argtypes
    _libusb = lib
    return lib


class LibusbTransport:
    """One claimed PTP interface of a USB device, through libusb-1.0."""

    def __init__(self, lib, context, handle, interface: int, ep_out: int, ep_in: int,
                 packet_size: int):
        self._lib, self._context, self._handle = lib, context, handle
        self._interface, self._ep_out, self._ep_in = interface, ep_out, ep_in
        self.packet_size = packet_size
        self.timeout_ms = TIMEOUT_MS  # of each bulk transfer; longer while a large answer is due
        self._fd: int | None = None  # the device node opened by `open_node`, closed with us

    @classmethod
    def open(cls, bus: int, address: int) -> LibusbTransport:
        """Open the device at this bus and device number (as in /sys/bus/usb/devices)."""
        lib = load_libusb()

        def find(context, handle):
            devices = ctypes.POINTER(ctypes.c_void_p)()
            count = lib.libusb_get_device_list(context, ctypes.byref(devices))
            cls._check(lib, min(count, 0))
            try:
                for i in range(count):
                    device = ctypes.c_void_p(devices[i])
                    if (lib.libusb_get_bus_number(device) == bus
                            and lib.libusb_get_device_address(device) == address):
                        cls._check(lib, lib.libusb_open(device, ctypes.byref(handle)))
                        break
                else:
                    raise UsbUnavailable(_("The USB device was not found."))
            finally:
                lib.libusb_free_device_list(devices, 1)

        return cls._opened(lib, find)

    @classmethod
    def from_fd(cls, fd: int) -> LibusbTransport:
        """Use a device file descriptor opened by someone else (open_node: the node in
        /dev/bus/usb that a Flatpak with --device=usb may open)."""
        lib = load_libusb()
        if not hasattr(lib, "libusb_set_option") or not hasattr(lib, "libusb_wrap_sys_device"):
            raise UsbUnavailable(_("This libusb cannot use a device opened by the system."))
        # Without this libusb would scan the USB tree, which a sandbox does not show.
        lib.libusb_set_option(None, _LIBUSB_OPTION_NO_DEVICE_DISCOVERY)

        def wrap(context, handle):
            cls._check(lib, lib.libusb_wrap_sys_device(context, fd, ctypes.byref(handle)))

        return cls._opened(lib, wrap)

    @classmethod
    def _opened(cls, lib, attach) -> LibusbTransport:
        """A libusb context, the device handle `attach(context, handle)` gives in it, and its
        file transfer interface claimed (_claim); whatever of them was got is released when a
        step fails, also on Ctrl+C."""
        context = ctypes.c_void_p()
        cls._check(lib, lib.libusb_init(ctypes.byref(context)))
        handle = ctypes.c_void_p()
        try:
            attach(context, handle)
            return cls._claim(lib, context, handle)
        except BaseException:
            if handle:
                lib.libusb_close(handle)
            lib.libusb_exit(context)
            raise

    @classmethod
    def open_node(cls, path) -> LibusbTransport:
        """Open the device through its node in /dev/bus/usb: in a Flatpak with the
        --device=usb permission the node is there, but no USB tree that libusb could scan."""
        try:
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except OSError as e:
            raise UsbUnavailable(_("The USB device cannot be opened: {error}").format(
                error=e.strerror or type(e).__name__)) from e
        try:
            transport = cls.from_fd(fd)
        except BaseException:
            os.close(fd)
            raise
        transport._fd = fd
        return transport

    @staticmethod
    def _check(lib, result: int) -> None:
        if result < 0:
            raise UsbUnavailable(_("The USB device cannot be used: {error}").format(
                error=lib.libusb_error_name(result).decode()))

    @classmethod
    def _claim(cls, lib, context, handle) -> LibusbTransport:
        config = ctypes.POINTER(_Config)()
        cls._check(lib, lib.libusb_get_active_config_descriptor(
            lib.libusb_get_device(handle), ctypes.byref(config)))
        try:
            found = _bulk_interface(config.contents)
        finally:
            lib.libusb_free_config_descriptor(config)
        if found is None:
            raise UsbUnavailable(_("The USB device has no file transfer interface."))
        interface, ep_out, ep_in, packet_size = found
        lib.libusb_set_auto_detach_kernel_driver(handle, 1)  # not supported everywhere; fine
        cls._check(lib, lib.libusb_claim_interface(handle, interface))
        return cls(lib, context, handle, interface, ep_out, ep_in, packet_size)

    def write(self, data: bytes) -> None:
        done = ctypes.c_int()
        result = self._lib.libusb_bulk_transfer(self._handle, self._ep_out, bytes(data), len(data),
                                                ctypes.byref(done), self.timeout_ms)
        if result < 0 or done.value != len(data):
            raise PtpError(_("Sending to the device failed: {error}").format(
                error=(self._lib.libusb_error_name(result).decode() if result < 0
                       else "short write")))

    def read(self, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        done = ctypes.c_int()
        result = self._lib.libusb_bulk_transfer(self._handle, self._ep_in, buffer, size,
                                                ctypes.byref(done), self.timeout_ms)
        if result == _LIBUSB_ERROR_TIMEOUT and done.value == 0:
            raise PtpError(_("The device did not answer in time."))
        if result < 0 and result != _LIBUSB_ERROR_TIMEOUT:
            raise PtpError(_("Reading from the device failed: {error}").format(
                error=self._lib.libusb_error_name(result).decode()))
        return buffer.raw[:done.value]

    def cancel(self) -> None:
        """The transaction the device may still be in cancelled (CANCEL_REQUEST, whatever its
        ID: the device has one at most), its status asked until it is OK again (Get Device
        Status, as the class asks before the next command) and what it still had to send
        read and dropped. A device whose transaction was cut off (its USB connection reset as
        it started, or a GetObject cut short by LIBUSB_ERROR_IO) otherwise takes
        no command again until it is unplugged; seen on a fēnix 8, which then said its
        session was open. The pipes are not cleared: a fēnix 8 then drops the next packets.
        Never raises."""
        if not self._handle:
            return
        data = struct.pack("<HI", CANCELLATION_CODE, 0)
        done = self._lib.libusb_control_transfer(self._handle, 0x21, CANCEL_REQUEST, 0,
                                                 self._interface, data, len(data), 2000)
        if done < 0:
            log.info("PTP: Cancel not taken (%s)", self._lib.libusb_error_name(done).decode())
        deadline = time.monotonic() + CANCEL_WAIT_S
        while self._device_status() not in (OK, None) and time.monotonic() < deadline:
            time.sleep(0.1)
        usual, self.timeout_ms = self.timeout_ms, DRAIN_TIMEOUT_MS
        try:
            for _read in range(16):
                if not self.read(self.packet_size * 64):
                    break
        except PtpError:
            pass  # nothing left to send: what is wanted
        finally:
            self.timeout_ms = usual

    def _device_status(self) -> int | None:
        """The response code of the class request Get Device Status; None: not told."""
        answer = ctypes.create_string_buffer(64)
        size = self._lib.libusb_control_transfer(self._handle, 0xA1, GET_DEVICE_STATUS, 0,
                                                 self._interface, answer, 64, 2000)
        return struct.unpack_from("<H", answer.raw, 2)[0] if size >= 4 else None

    def close(self) -> None:
        if self._handle:
            self._lib.libusb_release_interface(self._handle, self._interface)
            self._lib.libusb_close(self._handle)
            self._lib.libusb_exit(self._context)
            self._handle = None
        if self._fd is not None:  # libusb does not close a descriptor it was given
            os.close(self._fd)
            self._fd = None


def _bulk_interface(config: _Config) -> tuple[int, int, int, int] | None:
    """(interface number, bulk out, bulk in, packet size) of the first interface with both."""
    for i in range(config.bNumInterfaces):
        interface = config.interface[i]
        for a in range(interface.num_altsetting):
            alt = interface.altsetting[a]
            ep_out = ep_in = packet = None
            for e in range(alt.bNumEndpoints):
                endpoint = alt.endpoint[e]
                if endpoint.bmAttributes & 3 != _TRANSFER_BULK:
                    continue
                if endpoint.bEndpointAddress & _ENDPOINT_IN:
                    ep_in = endpoint.bEndpointAddress
                else:
                    ep_out, packet = endpoint.bEndpointAddress, endpoint.wMaxPacketSize
            if ep_out is not None and ep_in is not None:
                return alt.bInterfaceNumber, ep_out, ep_in, packet
    return None


# --- session -----------------------------------------------------------------------------

class Session:
    """One PTP session. Use as a context manager: the session is closed and the transport
    released on the way out, also after an error."""

    def __init__(self, transport: Transport):
        self.transport = transport
        self._transaction = 0
        self._buffer = b""
        self._open = False
        self._broken = False  # the conversation is out of step; send nothing more
        self._empty = 0  # reads in a row that brought nothing

    @property
    def broken(self) -> bool:
        """The conversation is out of step (a transfer failed or went unanswered): nothing
        more can be sent in this session."""
        return self._broken

    def __enter__(self) -> Session:
        try:
            self.open()
        except BaseException:
            self.transport.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        """The session opened. A device that does not take OpenSession in OPEN_TIMEOUT_MS is
        mostly still in a transaction that was cut off: it is cancelled (Transport.cancel),
        and OpenSession sent once more."""
        try:
            code = self._open_session()
        except PtpError as e:
            cancel = getattr(self.transport, "cancel", None)
            if cancel is None:
                raise
            log.warning("PTP: OpenSession not taken (%s); the device's transaction cancelled", e)
            cancel()
            self._broken, self._transaction = False, 0
            code = self._open_session()
        if code == SESSION_ALREADY_OPEN:
            # Left behind by a client that did not close it. Start over.
            self.transact(CLOSE_SESSION)
            self._transaction = 0
            code, _data, _params = self.transact(OPEN_SESSION, SESSION_ID)
        if code != OK:
            raise PtpRefused(OPEN_SESSION, code)
        self._open = True

    def _open_session(self) -> int:
        usual = self.transport.timeout_ms
        self.transport.timeout_ms = min(usual, OPEN_TIMEOUT_MS)  # also for the command sent
        try:
            return self.transact(OPEN_SESSION, SESSION_ID)[0]
        finally:
            self.transport.timeout_ms = usual

    def close(self) -> None:
        try:
            if self._open and not self._broken:
                self._open = False
                self.transact(CLOSE_SESSION)
        except PtpError:
            pass  # the device may already be gone; the transport is released either way
        finally:
            self._open = False
            self.transport.close()

    def transact(self, operation: int, *params: int, data_out: bytes | Outgoing | None = None,
                 data_in: Callable[[bytes], None] | None = None,
                 answer_ms: int | None = None) -> tuple[int, bytes, list[int]]:
        """One transaction: (response code, data received, response parameters). `data_out`:
        the data sent, as bytes or read from a file as it goes; `data_in`: given the data
        received piece by piece as it comes, never held whole (the data returned is then
        empty). `answer_ms`: how long the device may take to answer once the data it gets is
        sent (answer_limit_ms), instead of the usual TIMEOUT_MS."""
        out = Outgoing(io.BytesIO(data_out), len(data_out)) if isinstance(
            data_out, (bytes, bytearray)) else data_out
        received = 0
        sink = data_in
        if data_in is not None:
            def sink(piece: bytes) -> None:
                nonlocal received
                received += len(piece)
                data_in(piece)
        try:
            code, data, answer = self._transact(operation, *params, data_out=out, data_in=sink,
                                                answer_ms=answer_ms)
        except PtpError as e:
            log.warning("PTP 0x%04X failed: %s", operation, type(e).__name__)
            raise
        # Only codes and sizes: parameters are handles, the data may be a file.
        log.debug("PTP 0x%04X -> 0x%04X (sent %s, received %s)", operation, code,
                  applog.size(out.size if out is not None else 0),
                  applog.size(len(data) + received))
        return code, data, answer

    def _transact(self, operation: int, *params: int, data_out: Outgoing | None,
                  data_in: Callable[[bytes], None] | None,
                  answer_ms: int | None = None) -> tuple[int, bytes, list[int]]:
        if self._broken:
            raise PtpError(_("The connection to the device is out of step."))
        if data_out is not None and data_out.size > MAX_OBJECT_SIZE:
            raise PtpError(_("The file is too large to send."))
        transaction, self._transaction = self._transaction, self._transaction + 1
        try:
            self.transport.write(command(operation, transaction, params))
            if data_out is not None:
                # Header and payload as separate transfers, like the Windows MTP stack.
                self.transport.write(data_header(operation, transaction, data_out.size))
                self._send(data_out)
            usual = self.transport.timeout_ms
            if answer_ms is not None:
                self.transport.timeout_ms = answer_ms
            try:
                return self._answer(transaction, data_in)
            finally:
                self.transport.timeout_ms = usual
        except BaseException:
            self._broken = True
            raise

    def _answer(self, transaction: int,
                data_in: Callable[[bytes], None] | None) -> tuple[int, bytes, list[int]]:
        """The device's data, if any, and its response to `transaction`."""
        data = b""
        while True:
            kind, code, answered, length = self._header()
            if answered != transaction or kind not in (DATA, RESPONSE):
                raise PtpError(_("The device answered out of step."))
            if kind == DATA and data_in is not None:
                self._stream(length - HEADER.size, data_in)
                continue
            payload = self._take(length - HEADER.size)
            if kind == DATA:
                data = payload
            else:
                return code, data, list(struct.unpack(f"<{len(payload) // 4}I",
                                                      payload[:len(payload) // 4 * 4]))

    def _send(self, out: Outgoing) -> None:
        done = 0
        while done < out.size:
            piece = out.stream.read(min(WRITE_CHUNK, out.size - done))
            if not piece:
                raise PtpWriteError(_("The file on this computer ended before all of it was sent."))
            self.transport.write(piece)
            done += len(piece)
            if out.sent is not None:
                out.sent(done)
        if out.size % self.transport.packet_size == 0:
            self.transport.write(b"")  # a full last packet does not end the transfer

    def _header(self) -> tuple[int, int, int, int]:
        """The header of the next container: (kind, code, transaction, its whole length)."""
        self._fill(HEADER.size)
        length, kind, code, transaction = HEADER.unpack_from(self._buffer)
        if length < HEADER.size or length == 0xFFFFFFFF:
            raise PtpError(_("The device answered out of step."))
        self._buffer = self._buffer[HEADER.size:]
        return kind, code, transaction, length

    def _take(self, size: int) -> bytes:
        self._fill(size)
        payload, self._buffer = self._buffer[:size], self._buffer[size:]
        return payload

    def _stream(self, size: int, data_in: Callable[[bytes], None]) -> None:
        """`size` bytes of a data phase handed on as they come, READ_CHUNK at most at a time."""
        self._empty = 0
        while size > 0:
            if not self._buffer:
                self._read(READ_CHUNK)
                continue
            take = min(size, READ_CHUNK)
            piece, self._buffer = self._buffer[:take], self._buffer[take:]
            size -= len(piece)
            data_in(piece)

    def _fill(self, size: int) -> None:
        self._empty = 0
        while len(self._buffer) < size:
            self._read(READ_SIZE)

    def _read(self, size: int) -> None:
        chunk = self.transport.read(size)
        self._empty = 0 if chunk else self._empty + 1
        if self._empty > 3:  # a zero-length packet may end a data phase; several mean silence
            raise PtpError(_("The device did not answer in time."))
        self._buffer += chunk

    def _ok(self, operation: int, *params: int, data_out: bytes | Outgoing | None = None,
            data_in: Callable[[bytes], None] | None = None,
            answer_ms: int | None = None) -> tuple[bytes, list[int]]:
        code, data, answer = self.transact(operation, *params, data_out=data_out,
                                           data_in=data_in, answer_ms=answer_ms)
        if code != OK:
            raise PtpRefused(operation, code)
        return data, answer

    # --- storage and objects -------------------------------------------------------------

    def operations(self) -> list[int]:
        """The operations the device says it supports (GetDeviceInfo)."""
        return operations_supported(self._ok(GET_DEVICE_INFO)[0])

    def storages(self) -> list[int]:
        return u32_array(self._ok(GET_STORAGE_IDS)[0])

    def free_space(self, storage: int) -> int | None:
        data, _answer = self._ok(GET_STORAGE_INFO, storage)
        return struct.unpack_from("<Q", data, 14)[0] if len(data) >= 22 else None

    def info(self, handle: int, hidden: bool = False) -> ObjectInfo:
        data, _answer = self._ok(GET_OBJECT_INFO, handle)
        return parse_object_info(handle, data, hidden)

    def children(self, storage: int, parent: int = ROOT) -> list[ObjectInfo]:
        """What ordinary MTP shows in a folder."""
        data, _answer = self._ok(GET_OBJECT_HANDLES, storage, 0, parent)
        return [self.info(handle) for handle in u32_array(data)]

    def hidden_children(self, storage: int, parent: int) -> list[ObjectInfo]:
        """Objects of a folder that only Garmin's listing returns. Raises `PtpRefused`
        (OperationNotSupported) on a device without it."""
        data, _answer = self._ok(GARMIN_GET_OBJECT_IDS, storage, parent)
        return [self.info(handle, hidden=True) for handle in u32_array(data)]

    def all_children(self, storage: int, parent: int,
                     at_root: bool = False) -> list[ObjectInfo]:
        """Ordinary and hidden objects of a folder; just the ordinary ones on a device
        without Garmin's listing, and in the root unless `at_root` (a car navigator keeps its
        hidden folder there; a watch was never seen using Garmin's listing in the root)."""
        found = self.children(storage, parent)
        if parent == ROOT and not at_root:
            return found
        try:
            extra = self.hidden_children(storage, parent)
        except PtpRefused as e:
            if e.code != OPERATION_NOT_SUPPORTED:
                raise
            return found
        known = {item.handle for item in found}
        return found + [item for item in extra if item.handle not in known]

    def lookup(self, storage: int, path: str) -> ObjectInfo | None:
        """The object at a `/` path, matched case-insensitively like the rest of the
        program; hidden folders and files are found too."""
        current: ObjectInfo | None = None
        for part in [p for p in path.split("/") if p]:
            if current is not None and not current.is_dir:
                return None
            parent = ROOT if current is None else current.handle
            current = named_in_any_case(self.all_children(storage, parent), part)
            if current is None:
                return None
        return current

    def read_file(self, handle: int) -> bytes:
        """The object's content as the device returns it. App files come back encrypted,
        so this cannot verify them."""
        content = bytearray()
        self.read_into(handle, content.extend)
        return bytes(content)

    def read_into(self, handle: int, data_in: Callable[[bytes], None],
                  size: int | None = None) -> int:
        """The object's content given to `data_in` piece by piece as the device sends it,
        READ_CHUNK bytes at most at a time, never held whole; the bytes read. Raises PtpRefused
        when the device does not give it (nothing was given to `data_in`). `size`: the
        object's, when known: a large one may take the device longer (answer_limit_ms)."""
        read = 0

        def counted(piece: bytes) -> None:
            nonlocal read
            read += len(piece)
            data_in(piece)

        self._ok(GET_OBJECT, handle, data_in=counted, answer_ms=answer_limit_ms(size))
        return read

    def read_part(self, handle: int, offset: int, size: int) -> bytes:
        """At most `size` bytes of the object from `offset` (fewer at its end; the offset is
        32-bit, as no object over MAX_OBJECT_SIZE is ever sent). Raises PtpRefused
        (OperationNotSupported) on a device that does not read parts."""
        return self._ok(GET_PARTIAL_OBJECT, handle, offset, size)[0]

    def send_file(self, storage: int, parent: int, name: str, data: bytes,
                  verify_content: bool = False) -> ObjectInfo:
        """Write a new file into an existing folder and check that the device lists it
        with this name and size. A freshly written file shows in the ordinary listing even
        in a hidden folder. `verify_content` also reads it back (plain files only)."""
        if parent == ROOT:
            raise PtpWriteError(_("Refusing to write into the top folder of the device."))
        if named_in_any_case(self.all_children(storage, parent), name) is not None:
            raise PtpWriteError(_("“{name}” already exists on the device.").format(name=name))
        handle = self.send_object(storage, parent, name, data)
        written = next((item for item in self.children(storage, parent)
                        if item.handle == handle), None)
        if written is None or written.name != name or written.size != len(data):
            raise PtpWriteError(_("“{name}” was not written correctly.").format(name=name), handle)
        if verify_content and self.read_file(handle) != data:
            raise PtpWriteError(_("“{name}” was not written correctly.").format(name=name), handle)
        return written

    def send_object(self, storage: int, parent: int, name: str, data: bytes | Outgoing) -> int:
        """A new object `name` holding `data` (bytes, or read from a file as it is sent) in the
        folder `parent`, as Garmin Express writes one (SendObjectInfo, then SendObject); its
        handle. Nothing is checked here. A SendObject that fails raises PtpWriteError with the
        handle the device gave the object."""
        size = data.size if isinstance(data, Outgoing) else len(data)
        _data, answer = self._ok(SEND_OBJECT_INFO, storage, parent,
                                 data_out=object_info_dataset(name, size))
        if len(answer) < 3:
            raise PtpWriteError(
                _("The device did not say where it put “{name}”.").format(name=name))
        handle = answer[2]
        try:
            self._ok(SEND_OBJECT, data_out=data, answer_ms=answer_limit_ms(size))
        except PtpError as e:
            raise PtpWriteError(str(e), handle) from e
        return handle

    def delete(self, handle: int, size: int | None = None) -> None:
        """Delete an object; `size`: its, when known (a large one may take longer)."""
        self._ok(DELETE_OBJECT, handle, 0, answer_ms=answer_limit_ms(size))
