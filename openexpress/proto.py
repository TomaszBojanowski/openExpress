"""Minimal protobuf wire-format encoder/decoder (enough for Garmin device files)."""

from __future__ import annotations

from dataclasses import dataclass

from openexpress.i18n import _

VARINT, I64, LEN, I32 = 0, 1, 2, 5


class ProtoError(Exception):
    pass


@dataclass(frozen=True)
class Field:
    number: int
    wire_type: int
    value: int | bytes


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            raise ProtoError(_("Truncated varint"))
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ProtoError(_("Varint too long"))


def decode(data: bytes) -> list[Field]:
    out: list[Field] = []
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        number, wt = key >> 3, key & 7
        if wt == VARINT:
            value, pos = _read_varint(data, pos)
        elif wt == LEN:
            n, pos = _read_varint(data, pos)
            if pos + n > len(data):
                raise ProtoError(_("Truncated length-delimited field"))
            value, pos = data[pos : pos + n], pos + n
        elif wt == I32:
            value, pos = data[pos : pos + 4], pos + 4
        elif wt == I64:
            value, pos = data[pos : pos + 8], pos + 8
        else:
            raise ProtoError(_("Unsupported wire type {type}").format(type=wt))
        out.append(Field(number, wt, value))
    return out


def first(fields: list[Field], number: int, default=None):
    return next((f.value for f in fields if f.number == number), default)


def all_of(fields: list[Field], number: int) -> list:
    return [f.value for f in fields if f.number == number]


def _varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def enc_varint(number: int, value: int | bool) -> bytes:
    return _varint(number << 3 | VARINT) + _varint(int(value))


def enc_bytes(number: int, value: bytes) -> bytes:
    return _varint(number << 3 | LEN) + _varint(len(value)) + value


def enc_str(number: int, value: str) -> bytes:
    return enc_bytes(number, value.encode("utf-8"))
