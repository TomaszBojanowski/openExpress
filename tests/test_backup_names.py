"""Backups: what a failed one leaves, and names from the device that never lead out of its
folder."""

from datetime import datetime
from pathlib import Path

import pytest

from openexpress import backup
from openexpress.device.storage import DeviceError, LocalStorage
from openexpress.device.xmldevice import parse

# --- backup ---------------------------------------------------------------------------


def test_backup_retention_ignores_foreign_dirs_and_cleans_partial(device_root, tmp_path):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    unit = tmp_path / "bk" / str(dev.unit_id)
    unit.mkdir(parents=True)
    old = [unit / f"2020-01-0{i} (10.00.00)" for i in (1, 2, 3)]
    for p in old:
        p.mkdir()
    (unit / "zzz notes").mkdir()
    (unit / ".partial-2020-01-04 (10.00.00)").mkdir()
    res = backup.run_backup(dev, st, tmp_path / "bk", keep=2)
    assert res.path.is_dir() and (res.path / "GarminDevice.xml").exists()
    assert sorted(res.removed) == old[:2]
    assert (unit / "zzz notes").is_dir() and old[2].is_dir()
    assert not list(unit.glob(".partial-*"))
    datetime.strptime(res.path.name, backup.STAMP)


def test_backup_failure_removes_partial(device_root, tmp_path):
    class Broken(LocalStorage):
        def _read(self, real_path):
            raise DeviceError("unplugged")

    st = Broken(device_root)
    dev = parse((device_root / "GARMIN/GarminDevice.xml").read_bytes())
    with pytest.raises(DeviceError):
        backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    assert list((tmp_path / "bk" / str(dev.unit_id)).iterdir()) == []


# --- names from GarminDevice.xml never lead out of the backup --------------------------------


def _files_under(root: Path) -> set[Path]:
    return {p.resolve() for p in root.rglob("*") if p.is_file()}


def _with_type(dev, name):
    """The device with the first data type of the backup renamed (a crafted XML)."""
    import dataclasses

    first = backup.backup_files(dev)[0]
    dev.files = [dataclasses.replace(f, datatype=name) if f is first else f for f in dev.files]
    return dev


def _nothing_outside(tmp_path, before, target):
    """No file came to be anywhere but under the backup folder (resolved paths)."""
    target = target.resolve()
    new = _files_under(tmp_path) - before
    assert [p for p in new if not p.is_relative_to(target)] == []


@pytest.mark.parametrize("name", ["../../escape", "ABSOLUTE", "", ".", "..", "a/b"])
def test_a_crafted_data_type_name_is_refused_and_writes_nothing(device_root, tmp_path, name):
    """A data type whose name is no folder name (with "..", absolute, empty, with a
    separator) refuses the whole backup, as other errors of GarminDevice.xml do: nothing is
    written outside the backup folder, and no partial backup is left."""
    if name == "ABSOLUTE":
        name = str(tmp_path / "outside")  # absolute, but inside the test's own folder
    st = LocalStorage(device_root)
    dev = _with_type(parse((device_root / "GARMIN/GarminDevice.xml").read_bytes()), name)
    before = _files_under(tmp_path)
    with pytest.raises(DeviceError, match="cannot be used as a path"):
        backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    unit = tmp_path / "bk" / str(dev.unit_id)
    _nothing_outside(tmp_path, before, unit)
    assert list(unit.iterdir()) == []


def test_a_crafted_file_name_from_the_device_is_refused(device_root, tmp_path):
    """A file the device lists under a name with ".." (a device may list any name over
    MTP): refused the same way."""
    class Crafted(LocalStorage):
        def walk_files(self, path):
            files = super().walk_files(path)
            if not files:
                return files
            real, entry = files[0]
            return [(real.rsplit("/", 1)[0] + "/../../../escape.fit", entry), *files[1:]]

        def read(self, path):
            return b"x" if path.endswith("escape.fit") else super().read(path)

    st = Crafted(device_root)
    dev = parse((device_root / "GARMIN/GarminDevice.xml").read_bytes())
    before = _files_under(tmp_path)
    with pytest.raises(DeviceError, match="cannot be used as a path"):
        backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    _nothing_outside(tmp_path, before, tmp_path / "bk" / str(dev.unit_id))


def test_a_plain_backup_stays_inside_its_folder(device_root, tmp_path):
    """Guards: an ordinary backup writes only below its own folder."""
    st = LocalStorage(device_root)
    dev = parse((device_root / "GARMIN/GarminDevice.xml").read_bytes())
    before = _files_under(tmp_path)
    res = backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    assert res.files > 1
    _nothing_outside(tmp_path, before, res.path)


def test_a_link_named_like_an_old_backup_is_left_alone(device_root, tmp_path):
    """A link in the folder of backups, named like one (the user's own, to a folder kept
    elsewhere), is no backup: not counted among the old ones, never removed, and the backup
    is made without an error, as backups_of lists them."""
    st = LocalStorage(device_root)
    dev = parse((device_root / "GARMIN/GarminDevice.xml").read_bytes())
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "GARMIN").mkdir(parents=True)
    (elsewhere / "GARMIN" / "kept.txt").write_text("x")
    unit_dir = tmp_path / "bk" / str(dev.unit_id)
    unit_dir.mkdir(parents=True)
    link = unit_dir / "2020-01-01 (10.00.00)"
    link.symlink_to(elsewhere, target_is_directory=True)
    res = backup.run_backup(dev, st, tmp_path / "bk", keep=1)
    assert res.removed == [] and link.is_symlink()
    assert (elsewhere / "GARMIN" / "kept.txt").read_text() == "x"
