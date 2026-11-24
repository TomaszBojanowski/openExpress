"""`openexpress fit inspect FILE`: what a FIT file on this computer holds (its type,
manufacturer, product, creation time and messages), whether it is whole, and an exit code of 0
only when it is. Made-up files only (fakes.fit_file); never a serial number or a file name in
the log."""

import time

import pytest
from fakes import FIT_EPOCH, FitData, FitDefinition, fit_file

from openexpress import cli

ENUM, UINT8, UINT16, SINT32, UINT32, UINT32Z, STRING = 0x00, 0x02, 0x84, 0x85, 0x86, 0x8C, 0x07
TIMESTAMP = 253
CREATED = 1_100_000_000  # a made-up time, in seconds of the FIT protocol
SERIAL = 3_987_654_321  # a made-up serial number of a device


def a_course(*more, **options) -> bytes:
    """A made-up course: file_id (with a serial number), device_info (with it too), course,
    lap, 4 records, 2 events, 2 course points and a message the profile does not name."""
    return fit_file(
        FitDefinition(0, 0, ((0, ENUM), (1, UINT16), (2, UINT16), (3, UINT32Z), (4, UINT32))),
        FitData(0, (6, 1, 65534, SERIAL, CREATED)),
        FitDefinition(1, 23, ((3, UINT32Z), (2, UINT16))), FitData(1, (SERIAL, 1)),
        FitDefinition(2, 31, ((4, ENUM), (5, STRING, 16))), FitData(2, (1, "Pętla poranna")),
        FitDefinition(3, 19, ((TIMESTAMP, UINT32),)), FitData(3, (CREATED + 1,)),
        FitDefinition(4, 20, ((TIMESTAMP, UINT32), (0, SINT32), (1, SINT32))),
        *(FitData(4, (CREATED + i, 600_000, 200_000)) for i in range(4)),
        FitDefinition(5, 21, ((TIMESTAMP, UINT32),)), FitData(5, (CREATED,)),
        FitData(5, (CREATED + 5,)),
        FitDefinition(6, 32, ((TIMESTAMP, UINT32),)), FitData(6, (CREATED + 2,)),
        FitData(6, (CREATED + 3,)),
        FitDefinition(7, 288, ((0, UINT8),)), FitData(7, (1,)),
        *more, **options)


def inspect(tmp_path, capsys, data: bytes | None, name: str = "course.fit"):
    path = tmp_path / name
    if data is not None:
        path.write_bytes(data)
    code = cli.main(["fit", "inspect", str(path)])
    out, err = capsys.readouterr()
    return code, out.splitlines(), err


def test_the_lines_for_a_course(tmp_path, capsys):
    data = a_course()
    code, lines, _err = inspect(tmp_path, capsys, data)
    assert code == 0
    assert lines[:7] == [
        f"File: course.fit, {len(data)} B",
        f"Check: whole (header 14 B, data {len(data) - 16} B, CRC matches)",
        "Type: courses (FIT_TYPE_6)",
        "Manufacturer: Garmin (1)",
        "Product: 65534",
        "Created: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(CREATED + FIT_EPOCH)),
        "Messages: 8 kinds, 13 in all",
    ]
    # The most frequent first, then by number; named as the FIT profile names them.
    assert [line.split() for line in lines[7:15]] == [
        ["record", "(20)", "4"], ["event", "(21)", "2"], ["course_point", "(32)", "2"],
        ["file_id", "(0)", "1"], ["lap", "(19)", "1"], ["device_info", "(23)", "1"],
        ["course", "(31)", "1"], ["message", "288", "1"]]
    assert all(line.startswith("  ") for line in lines[7:15])
    assert lines[15:] == ["Developer data: none"]


def test_developer_data_is_told(tmp_path, capsys):
    _code, lines, _err = inspect(tmp_path, capsys, a_course(
        FitDefinition(8, 207, ((3, UINT8),)), FitData(8, (0,))))
    assert lines[-1] == "Developer data: present"
    assert ["developer_data_id", "(207)", "1"] in [line.split() for line in lines]


def test_the_serial_number_and_the_file_name_are_never_told_by_the_log(tmp_path, capsys,
                                                                      log_file):
    code, lines, err = inspect(tmp_path, capsys, a_course(), name="my-secret-route.fit")
    assert code == 0
    said = "\n".join(lines) + err
    assert str(SERIAL) not in said
    assert f"{SERIAL:x}" not in said.casefold()
    log = log_file.read_text(encoding="utf-8")
    assert "fit inspect" in log
    assert str(SERIAL) not in log
    assert "my-secret-route" not in log and str(tmp_path) not in log


def test_a_file_cut_short_says_what_was_read_and_what_its_header_declares(tmp_path, capsys):
    whole = a_course()
    code, lines, _err = inspect(tmp_path, capsys, whole[:-20])
    assert code == 1
    assert lines[1] == (f"Check: cut short: {len(whole) - 20} B read, {len(whole)} B declared "
                        "(still being written when it was read)")
    assert lines[2] == "Type: courses (FIT_TYPE_6)"  # and what was read before the cut


@pytest.mark.parametrize(("how", "check"), [
    ("wrong CRC", "Check: damaged: the CRC does not match ({size} B read)"),
    ("data after the end", "Check: damaged: 4 B after the end of the FIT data do not belong to a FIT file"),
])
def test_a_damaged_file_exits_with_1(tmp_path, capsys, how, check):
    data = a_course(bad_crc=True) if how == "wrong CRC" else a_course() + b"junk"
    code, lines, _err = inspect(tmp_path, capsys, data)
    assert code == 1
    assert lines[1] == check.format(size=len(data))


def test_messages_that_break_off_exit_with_1(tmp_path, capsys):
    # A data message of a local type no definition explains, the CRC right.
    code, lines, _err = inspect(tmp_path, capsys, a_course(b"\x0f\x00"))
    assert code == 1
    assert lines[1].startswith("Check: whole (")
    assert lines[-1] == "Reading stopped: Data message without a definition (local type 15)"


def test_what_is_no_fit_file_says_so_and_exits_with_1(tmp_path, capsys):
    text = b"just some text, no FIT"
    code, lines, _err = inspect(tmp_path, capsys, text, name="notes.txt")
    assert code == 1
    assert lines == [f"File: notes.txt, {len(text)} B",
                     f"Check: not a FIT file ({len(text)} B read)"]


def test_a_missing_file(tmp_path, capsys, log_file):
    code, lines, err = inspect(tmp_path, capsys, None, name="gone.fit")
    assert code == 1
    assert lines == []
    assert err.strip() == f"Error: File not found: {tmp_path / 'gone.fit'}"
    assert "gone.fit" not in log_file.read_text(encoding="utf-8")


def test_a_file_that_cannot_be_read(tmp_path, capsys):
    (tmp_path / "a-folder.fit").mkdir()
    code, lines, err = inspect(tmp_path, capsys, None, name="a-folder.fit")
    assert code == 1
    assert lines == []
    assert err.strip() == f"Error: Cannot read {tmp_path / 'a-folder.fit'}"


def test_fit_without_a_subcommand_is_a_wrong_command_line(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["fit"])
    assert stopped.value.code == 2
