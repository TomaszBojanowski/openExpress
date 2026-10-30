"""A backup checks its FIT files as a sync does (fit.incompleteness: the header, the size it
declares, the CRC): a file the device was still writing when it was connected is read cut
short; it is left out of the backup, and the summary, in the window and on the command line,
says to disconnect and reconnect the device, then to make the backup again. All devices and
files are made up."""
import types

import pytest
from conftest import make_fit
from fakes import RunNow
from fakes import window as stand_in

from openexpress import backup, i18n
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

CUT = "GARMIN/Activity/2026-10-03-10-00-00.fit"
TEXT = ("1 file was still being written by the device and is not in the backup. Disconnect "
        "and reconnect the device, then make the backup again.")


def _device(device_root):
    whole = make_fit(1100172800)
    (device_root / CUT).write_bytes(whole[:-7])  # cut short as the device was writing it
    st = LocalStorage(device_root)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def test_a_fit_file_cut_short_is_left_out_and_counted(device_root, tmp_path, caplog):
    st, dev = _device(device_root)
    res = backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    assert res.incomplete == 1
    kept = {p.name for p in res.path.rglob("*") if p.is_file()}
    assert "2026-10-03-10-00-00.fit" not in kept
    assert {"2026-10-01-10-00-00.fit", "M1.FIT", "GarminDevice.xml"} <= kept
    assert res.files == len(kept)
    said = [r for r in caplog.records if "still being written by the device" in r.getMessage()]
    assert len(said) == 1 and said[0].levelname == "WARNING"
    assert "2026-10-03" not in said[0].getMessage()  # the type and the sizes, never the name


def test_whole_files_are_all_backed_up(device_root, tmp_path):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    res = backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    assert res.incomplete == 0
    assert res.files == len([p for p in res.path.rglob("*") if p.is_file()]) >= 4


def test_the_words_of_the_summary():
    assert backup.incomplete_text(1) == TEXT
    assert backup.incomplete_text(2).startswith("2 files were still being written")
    i18n.setup("pl")
    try:
        assert backup.incomplete_text(1) == (
            "1 plik był jeszcze zapisywany przez urządzenie i nie trafił do kopii. "
            "Odłącz i podłącz urządzenie, a potem zrób kopię jeszcze raz.")
        assert backup.incomplete_text(5).startswith("5 plików było jeszcze zapisywanych")
    finally:
        i18n.setup("en")


def test_the_command_line_says_it_after_the_copy(device_root, tmp_path, capsys):
    from openexpress.cli import main

    _device(device_root)
    assert main(["--path", str(device_root), "backup", "--apply",
                 "--dest", str(tmp_path / "bk")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == TEXT


@pytest.mark.needs_display
def test_the_window_says_it_in_a_message_after_its_toast(device_root, tmp_path, monkeypatch,
                                                        in_the_window):
    from openexpress.gui.app import MainWindow

    st, dev = _device(device_root)
    monkeypatch.setattr(backup, "run_backup", lambda device, storage, dest, progress=None:
                        backup.BackupResult(tmp_path / "bk", 342, 1000, [], incomplete=1))
    told = []
    in_the_window("inform", lambda win, name, heading, body, **kw: told.append(
        (name, heading, body)))
    win = stand_in(session=types.SimpleNamespace(device=dev, storage=st), run_task=RunNow(),
                   toast=lambda text: told.append(text), _closed=False,
                   progress=lambda message: None, backup_row=None)
    MainWindow.backup(win)
    assert told[0].startswith("Copied 342 files to ")
    assert told[1] == ("backup-incomplete", "Not everything was backed up", TEXT)
