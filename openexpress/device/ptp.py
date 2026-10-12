"""A minimal PTP/MTP client for the files a Garmin device hides from ordinary MTP.

gvfs (libmtp) shows only what the standard `GetObjectHandles` returns. Some devices
keep the Connect IQ app folder out of that list and return it only through Garmin's
own operation 0x9002, so app files cannot be written there through gvfs. This module
talks PTP over USB directly, for exactly that case; everything else stays with gvfs.

Three layers:

* a transport: bulk transfers to one USB device (`LibusbTransport`, libusb-1.0
  through ctypes; tests use a fake device instead),
* `Session`: PTP containers and transactions,
* the file operations on top of it: list, look up, send, read, delete.

The device must not be in use by another MTP client (unmount it from gvfs first).
Nothing here creates folders.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import struct
from dataclasses import dataclass
from typing import Protocol

from openexpress.device.storage import DeviceError
from openexpress.i18n import _

# Operations. The 0x9xxx one is Garmin's extension.
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
GARMIN_GET_OBJECT_IDS = 0x9002

OPERATIONS = {
    OPEN_SESSION: "OpenSession", CLOSE_SESSION: "CloseSession",
    GET_STORAGE_IDS: "GetStorageIDs", GET_STORAGE_INFO: "GetStorageInfo",
    GET_OBJECT_HANDLES: "GetObjectHandles", GET_OBJECT_INFO: "GetObjectInfo",
    GET_OBJECT: "GetObject", DELETE_OBJECT: "DeleteObject",
    SEND_OBJECT_INFO: "SendObjectInfo", SEND_OBJECT: "SendObject",
    GARMIN_GET_OBJECT_IDS: "Garmin GetObjectIds",
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
WRITE_CHUNK = 1024 * 1024
TIMEOUT_MS = 30000


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

    def write(self, data: bytes) -> None: ...

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


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
        "libusb_get_active_config_descriptor": (c_int, [p, ctypes.POINTER(ctypes.POINTER(_Config))]),
        "libusb_free_config_descriptor": (None, [ctypes.POINTER(_Config)]),
        "libusb_set_auto_detach_kernel_driver": (c_int, [p, c_int]),
        "libusb_claim_interface": (c_int, [p, c_int]),
        "libusb_release_interface": (c_int, [p, c_int]),
        "libusb_bulk_transfer": (c_int, [p, ctypes.c_ubyte, ctypes.c_char_p, c_int,
                                         ctypes.POINTER(c_int), ctypes.c_uint]),
        "libusb_error_name": (ctypes.c_char_p, [c_int]),
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
                 packet_size: int, timeout_ms: int = TIMEOUT_MS):
        self._lib, self._context, self._handle = lib, context, handle
        self._interface, self._ep_out, self._ep_in = interface, ep_out, ep_in
        self.packet_size, self.timeout_ms = packet_size, timeout_ms

    @classmethod
    def open(cls, bus: int, address: int, timeout_ms: int = TIMEOUT_MS) -> LibusbTransport:
        """Open the device at this bus and device number (as in /sys/bus/usb/devices)."""
        lib = load_libusb()
        context = ctypes.c_void_p()
        cls._check(lib, lib.libusb_init(ctypes.byref(context)))
        handle = ctypes.c_void_p()
        try:
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
            return cls._claim(lib, context, handle, timeout_ms)
        except BaseException:
            if handle:
                lib.libusb_close(handle)
            lib.libusb_exit(context)
            raise

    @classmethod
    def from_fd(cls, fd: int, timeout_ms: int = TIMEOUT_MS) -> LibusbTransport:
        """Use a device file descriptor opened by someone else (the USB portal)."""
        lib = load_libusb()
        if not hasattr(lib, "libusb_set_option") or not hasattr(lib, "libusb_wrap_sys_device"):
            raise UsbUnavailable(_("This libusb cannot use a device opened by the system."))
        # Without this libusb would scan the USB tree, which a sandbox does not show.
        lib.libusb_set_option(None, _LIBUSB_OPTION_NO_DEVICE_DISCOVERY)
        context = ctypes.c_void_p()
        cls._check(lib, lib.libusb_init(ctypes.byref(context)))
        handle = ctypes.c_void_p()
        try:
            cls._check(lib, lib.libusb_wrap_sys_device(context, fd, ctypes.byref(handle)))
            return cls._claim(lib, context, handle, timeout_ms)
        except BaseException:
            if handle:
                lib.libusb_close(handle)
            lib.libusb_exit(context)
            raise

    @staticmethod
    def _check(lib, result: int) -> None:
        if result < 0:
            raise UsbUnavailable(_("The USB device cannot be used: {error}").format(
                error=lib.libusb_error_name(result).decode()))

    @classmethod
    def _claim(cls, lib, context, handle, timeout_ms: int) -> LibusbTransport:
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
        return cls(lib, context, handle, interface, ep_out, ep_in, packet_size, timeout_ms)

    def write(self, data: bytes) -> None:
        done = ctypes.c_int()
        result = self._lib.libusb_bulk_transfer(self._handle, self._ep_out, bytes(data), len(data),
                                                ctypes.byref(done), self.timeout_ms)
        if result < 0 or done.value != len(data):
            raise PtpError(_("Sending to the device failed: {error}").format(
                error=self._lib.libusb_error_name(result).decode() if result < 0 else "short write"))

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

    def close(self) -> None:
        if self._handle:
            self._lib.libusb_release_interface(self._handle, self._interface)
            self._lib.libusb_close(self._handle)
            self._lib.libusb_exit(self._context)
            self._handle = None


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
        code, _data, _params = self.transact(OPEN_SESSION, SESSION_ID)
        if code == SESSION_ALREADY_OPEN:
            # Left behind by a client that did not close it. Start over.
            self.transact(CLOSE_SESSION)
            self._transaction = 0
            code, _data, _params = self.transact(OPEN_SESSION, SESSION_ID)
        if code != OK:
            raise PtpRefused(OPEN_SESSION, code)
        self._open = True

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

    def transact(self, operation: int, *params: int,
                 data_out: bytes | None = None) -> tuple[int, bytes, list[int]]:
        """One transaction: (response code, data received, response parameters)."""
        if self._broken:
            raise PtpError(_("The connection to the device is out of step."))
        if data_out is not None and len(data_out) > MAX_OBJECT_SIZE:
            raise PtpError(_("The file is too large to send."))
        transaction, self._transaction = self._transaction, self._transaction + 1
        try:
            self.transport.write(command(operation, transaction, params))
            if data_out is not None:
                # Header and payload as separate transfers, like the Windows MTP stack.
                self.transport.write(data_header(operation, transaction, len(data_out)))
                for start in range(0, len(data_out), WRITE_CHUNK):
                    self.transport.write(data_out[start:start + WRITE_CHUNK])
                if len(data_out) % self.transport.packet_size == 0:
                    self.transport.write(b"")  # a full last packet does not end the transfer
            data = b""
            while True:
                kind, code, answered, payload = self._container()
                if answered != transaction or kind not in (DATA, RESPONSE):
                    raise PtpError(_("The device answered out of step."))
                if kind == DATA:
                    data = payload
                else:
                    return code, data, list(struct.unpack(f"<{len(payload) // 4}I",
                                                          payload[:len(payload) // 4 * 4]))
        except BaseException:
            self._broken = True
            raise

    def _container(self) -> tuple[int, int, int, bytes]:
        self._fill(HEADER.size)
        length, kind, code, transaction = HEADER.unpack_from(self._buffer)
        if length < HEADER.size or length == 0xFFFFFFFF:
            raise PtpError(_("The device answered out of step."))
        self._fill(length)
        payload, self._buffer = self._buffer[HEADER.size:length], self._buffer[length:]
        return kind, code, transaction, payload

    def _fill(self, size: int) -> None:
        empty = 0
        while len(self._buffer) < size:
            chunk = self.transport.read(READ_SIZE)
            empty = 0 if chunk else empty + 1
            if empty > 3:  # a zero-length packet may end a data phase; several mean silence
                raise PtpError(_("The device did not answer in time."))
            self._buffer += chunk

    def _ok(self, operation: int, *params: int,
            data_out: bytes | None = None) -> tuple[bytes, list[int]]:
        code, data, answer = self.transact(operation, *params, data_out=data_out)
        if code != OK:
            raise PtpRefused(operation, code)
        return data, answer

    # --- storage and objects -------------------------------------------------------------

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

    def all_children(self, storage: int, parent: int) -> list[ObjectInfo]:
        """Ordinary and hidden objects of a folder; just the ordinary ones on a device
        without Garmin's listing or in the root, where it was never seen used."""
        found = self.children(storage, parent)
        if parent == ROOT:
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
            current = _named(self.all_children(storage, parent), part)
            if current is None:
                return None
        return current

    def read_file(self, handle: int) -> bytes:
        """The object's content as the device returns it. App files come back encrypted,
        so this cannot verify them."""
        return self._ok(GET_OBJECT, handle)[0]

    def send_file(self, storage: int, parent: int, name: str, data: bytes,
                  verify_content: bool = False) -> ObjectInfo:
        """Write a new file into an existing folder and check that the device lists it
        with this name and size. A freshly written file shows in the ordinary listing even
        in a hidden folder. `verify_content` also reads it back (plain files only)."""
        if parent == ROOT:
            raise PtpWriteError(_("Refusing to write into the top folder of the device."))
        if _named(self.all_children(storage, parent), name) is not None:
            raise PtpWriteError(_("“{name}” already exists on the device.").format(name=name))
        _data, answer = self._ok(SEND_OBJECT_INFO, storage, parent,
                                 data_out=object_info_dataset(name, len(data)))
        if len(answer) < 3:
            raise PtpWriteError(_("The device did not say where it put “{name}”.").format(name=name))
        handle = answer[2]
        try:
            self._ok(SEND_OBJECT, data_out=data)
        except PtpError as e:
            raise PtpWriteError(str(e), handle) from e
        written = next((item for item in self.children(storage, parent)
                        if item.handle == handle), None)
        if written is None or written.name != name or written.size != len(data):
            raise PtpWriteError(_("“{name}” was not written correctly.").format(name=name), handle)
        if verify_content and self.read_file(handle) != data:
            raise PtpWriteError(_("“{name}” was not written correctly.").format(name=name), handle)
        return written

    def delete(self, handle: int) -> None:
        self._ok(DELETE_OBJECT, handle, 0)


def _named(items: list[ObjectInfo], name: str) -> ObjectInfo | None:
    wanted = name.casefold()
    return next((item for item in items if item.name.casefold() == wanted), None)
