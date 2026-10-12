"""A fake Garmin device on the other end of a PTP transport, for tests.

It answers the way the fēnix 8 was seen to answer:

* a folder taken over by the device is missing from the ordinary listing and comes
  back only from Garmin's 0x9002, with protection 1; so do the app files inside it,
* a freshly written file shows in the ordinary listing, with protection 0,
* reading an app file (.PRG) returns other bytes than were written,
* SendObjectInfo takes storage and parent as parameters and returns the new handle.

* DeleteObject removes a protected app file too (seen when Garmin Express removed an
  app); `delete_protected` can make the fake refuse instead.

Everything in the tree is made up.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from openexpress.device import ptp

STORAGE = 0x00010001


@dataclass
class FakeObject:
    name: str
    parent: int
    data: bytes | None = None  # None: a folder
    hidden: bool = False

    @property
    def protection(self) -> int:
        return 1 if self.hidden else 0


class FakeDevice:
    """Implements `ptp.Transport`. `transfers` records every transfer written to it."""

    def __init__(self, packet_size: int = 512, read_chunk: int | None = None):
        self.packet_size = packet_size
        self.read_chunk = read_chunk  # split answers into reads of this size
        self.objects: dict[int, FakeObject] = {}
        self.free = 500 * 1024 * 1024
        self.refuse: dict[int, int] = {}  # operation -> response code
        self.refuse_names: dict[str, int] = {}  # file name -> code, at SendObjectInfo
        self.supports_hidden = True
        self.delete_protected = ptp.OK  # or a refusal such as ptp.OBJECT_WRITE_PROTECTED
        self.session_open = False
        self.wrong_transaction = False
        self.silent = False
        self.truncate_written = 0  # bytes cut from the next stored file
        self.transfers: list[bytes] = []
        self.operations: list[int] = []
        self.closed = False
        self._next = 0x100
        self._out = b""
        self._pending: tuple[int, int, tuple[int, ...]] | None = None  # awaiting data
        self._announced: tuple[int, int] | None = None  # (handle, size) from SendObjectInfo
        self._answers: list[bytes] = []

    # --- building the tree ---------------------------------------------------------------

    def add(self, path: str, data: bytes | None = None, hidden: bool = False) -> int:
        """Add a folder (no data) or a file; missing parents become ordinary folders."""
        parent = ptp.ROOT
        parts = [p for p in path.split("/") if p]
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            found = next((h for h, o in self.objects.items()
                          if o.parent == parent and o.name == part), None)
            if found is None:
                found = self._next
                self._next += 1
                self.objects[found] = FakeObject(part, parent, data if last else None,
                                                 hidden if last else False)
            parent = found
        return parent

    def handle(self, path: str) -> int | None:
        parent = ptp.ROOT
        for part in [p for p in path.split("/") if p]:
            parent = next((h for h, o in self.objects.items()
                           if o.parent == parent and o.name == part), None)
            if parent is None:
                return None
        return parent

    def names(self, path: str, hidden: bool | None = None) -> list[str]:
        parent = self.handle(path)
        return sorted(o.name for o in self.objects.values()
                      if o.parent == parent and hidden in (None, o.hidden))

    def take_over(self, folder: str) -> None:
        """What the device does after it is unplugged: the files in an app folder
        become its own."""
        parent = self.handle(folder)
        for o in self.objects.values():
            if o.parent == parent:
                o.hidden = True

    def reconnect(self) -> None:
        """The cable was unplugged and plugged in again."""
        self.closed = self.session_open = False
        self._out, self._pending, self._announced, self._answers = b"", None, None, []

    def sent(self, operation: int) -> int:
        return self.operations.count(operation)

    # --- transport -----------------------------------------------------------------------

    def write(self, data: bytes) -> None:
        assert not self.closed
        self.transfers.append(bytes(data))
        if self.silent:
            return
        self._out += data
        while len(self._out) >= ptp.HEADER.size:
            length, kind, code, transaction = ptp.HEADER.unpack_from(self._out)
            if len(self._out) < length:
                return
            payload, self._out = self._out[ptp.HEADER.size:length], self._out[length:]
            if kind == ptp.COMMAND:
                params = struct.unpack(f"<{len(payload) // 4}I", payload)
                self.operations.append(code)
                if code in (ptp.SEND_OBJECT_INFO, ptp.SEND_OBJECT):
                    self._pending = (code, transaction, params)
                else:
                    self._respond(code, transaction, *self._handle(code, params, None))
            else:
                assert kind == ptp.DATA and self._pending is not None
                operation, expected, params = self._pending
                assert (code, transaction) == (operation, expected)
                self._pending = None
                self._respond(operation, transaction, *self._handle(operation, params, payload))

    def read(self, size: int) -> bytes:
        assert not self.closed
        if not self._answers:
            return b""
        answer = self._answers.pop(0)
        limit = min(size, self.read_chunk or size)
        if len(answer) > limit:
            self._answers.insert(0, answer[limit:])
        return answer[:limit]

    def close(self) -> None:
        self.closed = True

    def _respond(self, operation: int, transaction: int, code: int, data: bytes | None = None,
                 params: tuple[int, ...] = ()) -> None:
        if self.wrong_transaction:
            transaction += 7
        if data is not None:
            self._answers.append(ptp.data_header(operation, transaction, len(data)) + data)
        self._answers.append(struct.pack(f"<IHHI{len(params)}I", 12 + 4 * len(params),
                                         ptp.RESPONSE, code, transaction, *params))

    # --- operations ----------------------------------------------------------------------

    def _handle(self, operation, params, data):
        if operation == ptp.OPEN_SESSION:
            if self.session_open:
                return (ptp.SESSION_ALREADY_OPEN,)
            if operation in self.refuse:
                return (self.refuse[operation],)
            self.session_open = True
            return (ptp.OK,)
        if not self.session_open:
            return (ptp.SESSION_NOT_OPEN,)
        if operation == ptp.CLOSE_SESSION:
            self.session_open = False
            return (ptp.OK,)
        if operation in self.refuse:
            return (self.refuse[operation],)
        if operation == ptp.GET_STORAGE_IDS:
            return ptp.OK, struct.pack("<II", 1, STORAGE)
        if operation == ptp.GET_STORAGE_INFO:
            return ptp.OK, (struct.pack("<HHHQQI", 3, 2, 0, 2 * self.free, self.free, 0xFFFFFFFF)
                            + ptp.ptp_string("Internal") + ptp.ptp_string(""))
        if operation in (ptp.GET_OBJECT_HANDLES, ptp.GARMIN_GET_OBJECT_IDS):
            hidden = operation == ptp.GARMIN_GET_OBJECT_IDS
            if hidden and not self.supports_hidden:
                return (ptp.OPERATION_NOT_SUPPORTED,)
            parent = params[1] if hidden else params[2]
            if params[0] != STORAGE:
                return (ptp.INVALID_STORAGE_ID,)
            if parent != ptp.ROOT and (parent not in self.objects
                                       or self.objects[parent].data is not None):
                return (ptp.INVALID_PARENT_OBJECT,)
            handles = [h for h, o in self.objects.items()
                       if o.parent == parent and o.hidden == hidden]
            return ptp.OK, struct.pack(f"<I{len(handles)}I", len(handles), *handles)
        if operation == ptp.SEND_OBJECT_INFO:
            return self._send_object_info(params, data)
        if operation == ptp.SEND_OBJECT:
            return self._send_object(data)
        obj = self.objects.get(params[0]) if params else None
        if obj is None:
            return (ptp.INVALID_OBJECT_HANDLE,)
        if operation == ptp.GET_OBJECT_INFO:
            return ptp.OK, self._object_info(obj)
        if operation == ptp.GET_OBJECT:
            if obj.data is None:
                return (ptp.INVALID_OBJECT_HANDLE,)
            if obj.name.upper().endswith(".PRG"):
                return ptp.OK, bytes(b ^ 0xA5 for b in obj.data)  # "encrypted"
            return ptp.OK, obj.data
        if operation == ptp.DELETE_OBJECT:
            if obj.protection and self.delete_protected != ptp.OK:
                return (self.delete_protected,)
            for h in [params[0]] + [h for h, o in self.objects.items() if o.parent == params[0]]:
                del self.objects[h]
            return (ptp.OK,)
        return (ptp.OPERATION_NOT_SUPPORTED,)

    def _object_info(self, obj: FakeObject) -> bytes:
        fmt = ptp.FORMAT_FOLDER if obj.data is None else ptp.FORMAT_UNDEFINED
        return (struct.pack("<IHHI", STORAGE, fmt, obj.protection, len(obj.data or b""))
                + struct.pack("<HIIIIIIIHII", 0, 0, 0, 0, 0, 0, 0, obj.parent, 0, 0, 0)
                + ptp.ptp_string(obj.name) + ptp.ptp_string("") + ptp.ptp_string("")
                + ptp.ptp_string(""))

    def _send_object_info(self, params, data):
        self._announced = None
        storage, parent = params[0], params[1]
        if storage != STORAGE:
            return (ptp.INVALID_STORAGE_ID,)
        if parent not in self.objects or self.objects[parent].data is not None:
            return (ptp.INVALID_PARENT_OBJECT,)
        fmt, size = struct.unpack_from("<H", data, 4)[0], struct.unpack_from("<I", data, 8)[0]
        name, _end = ptp.read_string(data, 52)
        if fmt == ptp.FORMAT_FOLDER:
            return (ptp.ACCESS_DENIED,)  # the device does not let folders be created here
        if name in self.refuse_names:
            return (self.refuse_names[name],)
        if size > self.free:
            return (ptp.STORE_FULL,)
        handle = self._next
        self._next += 1
        self.objects[handle] = FakeObject(name, parent, b"")
        self._announced = (handle, size)
        return ptp.OK, None, (storage, parent, handle)

    def _send_object(self, data):
        if self._announced is None:
            return (ptp.NO_VALID_OBJECT_INFO,)
        (handle, size), self._announced = self._announced, None
        if len(data) != size:
            return (ptp.GENERAL_ERROR,)
        if self.truncate_written:
            data, self.truncate_written = data[:-self.truncate_written], 0
        self.objects[handle].data = bytes(data)
        self.free -= len(data)
        return (ptp.OK,)
