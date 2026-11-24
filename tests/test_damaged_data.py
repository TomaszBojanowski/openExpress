"""Damaged data (FIT files, protobuf messages) is refused without stopping a sync."""

import struct

import pytest
from conftest import fit_crc, make_fit
from fakes import SyncClient
from fakes import sync_setup as _setup
from test_sync import UPLOAD_CONFIG

from openexpress import proto, sync
from openexpress.fit import FitError, iter_messages, parse_file_id


def test_proto_rejects_truncated_fixed_and_field_zero():
    with pytest.raises(proto.ProtoError):
        proto.decode(bytes([1 << 3 | proto.I32, 1, 2]))
    with pytest.raises(proto.ProtoError):
        proto.decode(bytes([1 << 3 | proto.I64]) + b"\x00" * 7)
    with pytest.raises(proto.ProtoError):
        proto.decode(bytes([0 << 3 | proto.VARINT, 1]))
    fields = proto.decode(bytes([1 << 3 | proto.I32]) + b"abcd")
    assert {f.number: f.value for f in fields}[1] == b"abcd"


def _truncated_definition() -> bytes:
    # Definition declares 5 fields but the file ends after one.
    body = bytes([0x40, 0, 0]) + struct.pack("<H", 0) + bytes([5, 0, 1, 0x00])
    return struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0) + body


def _truncated_dev_fields() -> bytes:
    body = bytes([0x60, 0, 0]) + struct.pack("<H", 0) + bytes([1, 0, 1, 0x00, 4, 1, 2])
    return struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0) + body


@pytest.mark.parametrize("data", [_truncated_definition(), _truncated_dev_fields()])
def test_truncated_definition_raises_fit_error(data):
    with pytest.raises(FitError):
        list(iter_messages(data))
    with pytest.raises(FitError):
        parse_file_id(data)


def test_damaged_files_only_raise_fit_error():
    import random

    good = make_fit(1100000000)
    rnd = random.Random(1)
    samples = [good[:n] for n in range(len(good))]
    for _ in range(2000):
        b = bytearray(good)
        for _i in range(3):
            b[rnd.randrange(14, len(b))] = rnd.randrange(256)
        samples.append(bytes(b))
    for data in samples:
        try:
            list(iter_messages(data))
        except FitError:
            pass
    assert parse_file_id(good).time_created == 1100000000


def _whole(data: bytes) -> bytes:
    """`data` with the CRC a whole file ends with."""
    return data + struct.pack("<H", fit_crc(data))


@pytest.mark.parametrize(("data", "uploaded", "incomplete"), [
    (_whole(_truncated_definition()), 3, 0),  # whole, its content unreadable: sent all the same
    (_truncated_definition(), 2, 1),  # cut short (no CRC): it waits for the next sync
])
def test_damaged_activity_does_not_abort_sync(device_root, tmp_path, data, uploaded, incomplete):
    (device_root / "GARMIN/Activity/2026-10-03-10-00-00.fit").write_bytes(data)
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state, only_types={"FIT_TYPE_4"})
    rep = sync.run_uploads(plan, dev, st, SyncClient(), state)
    assert len(rep.uploaded) == uploaded and len(rep.incomplete) == incomplete and not rep.failed
