"""Minimal FIT parser: reads the creation time from the file_id message.

Enough to derive the activity identifier Garmin Connect uses for duplicate
checks (time_created in seconds since the FIT epoch).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from openexpress.i18n import _

FIT_EPOCH_OFFSET = 631065600  # 1989-12-31T00:00:00Z as Unix time

_MESG_FILE_ID = 0
_FIELD_TIME_CREATED = 4


class FitError(Exception):
    pass


@dataclass
class FileId:
    time_created: int | None = None


@dataclass
class _Def:
    big_endian: bool
    global_num: int
    fields: list[tuple[int, int]]  # (field number, size)
    dev_size: int


def _read_uint(raw: bytes, big_endian: bool) -> int | None:
    if len(raw) not in (1, 2, 4):
        return None
    fmt = {1: "B", 2: "H", 4: "I"}[len(raw)]
    value = struct.unpack((">" if big_endian else "<") + fmt, raw)[0]
    invalid = (1 << (8 * len(raw))) - 1
    return None if value == invalid else value


def iter_messages(data: bytes):
    """Yields (global message number, {field number: unsigned value}) for each data message.

    Malformed or truncated input raises FitError (never IndexError/struct.error).
    """
    try:
        yield from _iter_messages(data)
    except (IndexError, struct.error) as e:
        raise FitError(_("Damaged FIT file ({error})").format(error=e)) from e


def _iter_messages(data: bytes):
    if len(data) < 12:
        raise FitError(_("File too short"))
    header_size = data[0]
    if header_size not in (12, 14) or data[8:12] != b".FIT":
        raise FitError(_("Not a FIT file"))
    data_size = struct.unpack_from("<I", data, 4)[0]
    end = min(len(data), header_size + data_size)
    pos = header_size
    defs: dict[int, _Def] = {}
    while pos < end:
        hdr = data[pos]
        pos += 1
        if hdr & 0x80:  # compressed timestamp: a regular data message
            local = (hdr >> 5) & 0x03
            is_def = False
            has_dev = False
        else:
            local = hdr & 0x0F
            is_def = bool(hdr & 0x40)
            has_dev = bool(hdr & 0x20)
        if is_def:
            if pos + 5 > end:
                break
            big_endian = data[pos + 1] == 1
            global_num = struct.unpack_from(">H" if big_endian else "<H", data, pos + 2)[0]
            nfields = data[pos + 4]
            pos += 5
            if pos + 3 * nfields > end:
                raise FitError(_("Truncated FIT definition message"))
            fields = []
            for _field in range(nfields):
                fields.append((data[pos], data[pos + 1]))
                pos += 3
            dev_size = 0
            if has_dev:
                if pos + 1 > end:
                    raise FitError(_("Truncated FIT definition message"))
                ndev = data[pos]
                pos += 1
                if pos + 3 * ndev > end:
                    raise FitError(_("Truncated FIT definition message"))
                for _dev in range(ndev):
                    dev_size += data[pos + 1]
                    pos += 3
            defs[local] = _Def(big_endian, global_num, fields, dev_size)
            continue
        d = defs.get(local)
        if d is None:
            raise FitError(_("Data message without a definition (local type {local})")
                           .format(local=local))
        values: dict[int, int | None] = {}
        p = pos
        for num, size in d.fields:
            values[num] = _read_uint(data[p : p + size], d.big_endian)
            p += size
        yield d.global_num, values
        pos += sum(size for _, size in d.fields) + d.dev_size


# CRC-16 of the FIT protocol, four bits at a time.
_CRC_TABLE = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def not_whole(data: bytes) -> tuple[bool, str] | None:
    """Why `data` is not a whole FIT file, as (cut short, the words for the log), or None when
    it is: every part (a file may chain several) has a header of 12 or 14 bytes with ".FIT",
    the size its header declares (header, data and the CRC of 2 bytes) and that CRC; zeros
    after the last part are padding. Cut short: a header declares more bytes than were read,
    as when the device was still writing the file; else no header, data after the end that
    are no FIT file, or a CRC that does not match. For the log: sizes, never the content."""
    pos = 0
    while True:
        if pos and not any(data[pos:]):
            return None  # zeros after whole FIT data: padding, nothing cut short
        if len(data) - pos < 12 or data[pos] not in (12, 14) or data[pos + 8:pos + 12] != b".FIT":
            return False, (f"no FIT header ({len(data)} B read)" if pos == 0
                           else f"{len(data) - pos} B after the whole FIT data are no FIT file")
        header = data[pos]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        end = pos + header + size + 2
        if end > len(data):
            return True, f"{len(data)} B read, {end} B declared"
        (crc,) = struct.unpack_from("<H", data, end - 2)
        if crc16(data[pos:end - 2]) != crc:
            return False, f"the CRC does not match ({len(data)} B read)"
        if end == len(data):
            return None
        pos = end


def incompleteness(data: bytes) -> str | None:
    """Why `data` is not a whole FIT file (not_whole), for the log; None when it is."""
    found = not_whole(data)
    return found[1] if found else None


def find_message(data: bytes, global_num: int) -> dict[int, int | None] | None:
    return next((v for g, v in iter_messages(data) if g == global_num), None)


def parse_file_id(data: bytes) -> FileId:
    v = find_message(data, _MESG_FILE_ID)
    if v is None:
        raise FitError(_("No file_id message"))
    return FileId(time_created=v.get(_FIELD_TIME_CREATED))


MESG_CALLBACK_DELAY = 197  # Garmin-internal message used by TrueUp


def true_up_time(data: bytes) -> int | None:
    """Unix time at which Connect asks the client to check back (TrueUp)."""
    v = find_message(data, MESG_CALLBACK_DELAY)
    t = v.get(0) if v else None
    return None if t is None else t + FIT_EPOCH_OFFSET
