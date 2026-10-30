"""The progress of a backup: which file of how many ("Copying to the backup · 120 of 1530"),
never the name of a file on the device. All devices and files are made up."""
from openexpress import backup, i18n
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse
from openexpress.progress import Step

NB = " "


def _device(device_root):
    for day in (1, 2, 3):
        (device_root / f"GARMIN/Activity/2026-10-0{day}-10-00-00.fit").write_bytes(b"x" * 10)
    st = LocalStorage(device_root)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def test_the_backup_counts_its_files(device_root, tmp_path):
    st, dev = _device(device_root)
    seen = []
    res = backup.run_backup(dev, st, tmp_path / "bk", keep=1, progress=seen.append)
    # Every file of the backup counted, GarminDevice.xml too: the last step says as many
    # files as the sentence at the end ("Copied 343 files").
    assert res.files >= 4 and all(isinstance(m, Step) for m in seen)
    assert [str(m) for m in seen] == [f"Copying to the backup{NB}· {n} of {res.files}"
                                      for n in range(1, res.files + 1)]
    assert not [m for m in seen if ".fit" in m.lower() or "GARMIN/" in m.upper()]


def test_backup_progress_in_polish(device_root, tmp_path):
    st, dev = _device(device_root)
    seen = []
    i18n.setup("pl")
    try:
        backup.run_backup(dev, st, tmp_path / "bk", keep=1, progress=seen.append)
    finally:
        i18n.setup("en")
    assert seen[0] == f"Kopiowanie do kopii zapasowej{NB}· 1 z{NB}{len(seen)}"


def test_the_command_line_tells_the_backup_once(device_root, tmp_path, capsys):
    """Not on a terminal: one line for the copy, not a line a file."""
    from openexpress.cli import main

    _device(device_root)
    assert main(["--path", str(device_root), "backup", "--apply",
                 "--dest", str(tmp_path / "bk")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line for line in out if line.startswith("  ")] == ["  Copying to the backup"]
