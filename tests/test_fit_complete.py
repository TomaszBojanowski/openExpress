"""A FIT file read from a device is whole only when its header, the size it declares and its
CRC agree: a file the device was still writing is read cut short."""
import struct

from conftest import fit_crc, make_fit

from openexpress import fit


def test_the_crc_of_the_fit_protocol():
    assert fit.crc16(b"123456789") == 0xBB3D == fit_crc(b"123456789")


def test_a_whole_file_is_complete():
    assert fit.incompleteness(make_fit(1100000000)) is None


def test_a_file_cut_short_says_both_sizes():
    data = make_fit(1100000000)
    why = fit.incompleteness(data[:-6])
    assert why is not None and f"{len(data) - 6} B" in why and f"{len(data)} B" in why


def test_a_wrong_crc_at_the_full_size():
    data = bytearray(make_fit(1100000000))
    data[16] ^= 0x01
    assert "CRC" in fit.incompleteness(bytes(data))


def test_no_fit_header():
    for data in (b"", b"\x0e", b"not a FIT file at all, only text", make_fit(1)[:10]):
        assert fit.incompleteness(data) is not None


def test_a_twelve_byte_header():
    body = make_fit(1100000000)[14:-2]
    header = struct.pack("<BBHI4s", 12, 0x10, 2100, len(body), b".FIT")
    data = header + body
    assert fit.incompleteness(data + struct.pack("<H", fit_crc(data))) is None


def test_chained_files_are_whole_only_together():
    two = make_fit(1100000000) + make_fit(1100000500, 32)
    assert fit.incompleteness(two) is None
    assert fit.incompleteness(two[:-1]) is not None
    assert fit.incompleteness(two + b"\x0e\x20") is not None  # a third begun, cut short


def test_zeros_after_the_data_are_padding():
    assert fit.incompleteness(make_fit(1100000000) + bytes(16)) is None
