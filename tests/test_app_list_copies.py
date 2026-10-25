"""The copies of the app list (GARMIN-APPS …, 25.10.2026): before a change no new copy is made
while the apps and their order are those of the newest copy (whatever else OUT.BIN says, such
as the free space); at most APP_LIST_COPIES_KEPT copies are kept for a device, the oldest go
when a new one is made, as the full backups keep their newest three. The full backups and
anything else in the folder stay. All devices and apps are made up."""

import datetime
import uuid

from test_ciq_music import PRELOADED, STORE_ID, copy_names, iq_device, out_bin  # noqa: F401

from openexpress import ciq, proto

WIDGET = uuid.UUID("0b6a4c20-35a1-4c37-8b1a-7f1d6e2a9c01")


def _stamps():
    return iter(datetime.datetime(2026, 10, 1 + n // 60, 10, n % 60, 0) for n in range(600))


def _with_space(apps, space):
    """OUT.BIN with these apps and another free space: the same list, other bytes."""
    data = out_bin(apps)
    return data.replace(proto.enc_varint(1, 66_000_000), proto.enc_varint(1, space))


APPS = [(STORE_ID, 1, "Connect IQ Store", False, "DADE7E4"), (PRELOADED, 8, "Softball", True, ""),
        (WIDGET, 2, "Weather", False, "")]


def _list(device_root, data):
    (device_root / "GARMIN/APPS/OUT.BIN").write_bytes(data)


# --- (1) no copy of the same list --------------------------------------------------------------


def test_the_same_apps_in_the_same_order_make_no_new_copy(iq_device, device_root, backups):
    """The free space of the device changed (other bytes), the list did not: the newest copy
    is named, none made."""
    st, dev = iq_device
    stamps = _stamps()
    _list(device_root, out_bin(APPS))
    first = ciq.save_app_list_copy(dev, st, now=next(stamps))
    _list(device_root, _with_space(APPS, 65_000_000))
    assert ciq.save_app_list_copy(dev, st, now=next(stamps)) == first
    assert copy_names(dev, backups) == [first]


def test_another_order_or_another_list_makes_a_new_copy(iq_device, device_root, backups):
    st, dev = iq_device
    stamps = _stamps()
    _list(device_root, out_bin(APPS))
    first = ciq.save_app_list_copy(dev, st, now=next(stamps))
    _list(device_root, out_bin([APPS[0], APPS[2], APPS[1]]))  # the same apps, reordered
    second = ciq.save_app_list_copy(dev, st, now=next(stamps))
    _list(device_root, out_bin(APPS[:2]))  # one app fewer
    third = ciq.save_app_list_copy(dev, st, now=next(stamps))
    assert copy_names(dev, backups) == [first, second, third]


def test_only_the_copies_of_the_app_list_count_as_the_newest(iq_device, device_root, backups):
    """A folder of the user's with an OUT.BIN in it, named so that it sorts last: not a copy
    of openExpress, so the newest copy is still compared."""
    st, dev = iq_device
    stamps = _stamps()
    _list(device_root, out_bin(APPS))
    first = ciq.save_app_list_copy(dev, st, now=next(stamps))
    mine = backups / str(dev.unit_id) / "zz my own copy"
    mine.mkdir()
    (mine / "OUT.BIN").write_bytes(out_bin(APPS[:1]))
    _list(device_root, out_bin(APPS[:1]))
    second = ciq.save_app_list_copy(dev, st, now=next(stamps))
    assert second != first and second.startswith(ciq.APP_LIST_COPY_PREFIX)


def test_a_damaged_newest_copy_is_compared_byte_for_byte(iq_device, device_root, backups):
    st, dev = iq_device
    stamps = _stamps()
    _list(device_root, b"not an app list")
    first = ciq.save_app_list_copy(dev, st, now=next(stamps))
    assert ciq.save_app_list_copy(dev, st, now=next(stamps)) == first
    _list(device_root, b"not an app list either")
    assert ciq.save_app_list_copy(dev, st, now=next(stamps)) != first


# --- (2) at most ten copies --------------------------------------------------------------------


def _full_backups(backups, dev):
    from openexpress.backup import STAMP

    folders = []
    for n in range(3):
        folder = backups / str(dev.unit_id) / datetime.datetime(2026, 9, 1, 8, n).strftime(STAMP)
        (folder / "GARMIN").mkdir(parents=True)
        (folder / "GARMIN" / "GarminDevice.xml").write_text("<Device/>")
        folders.append(folder.name)
    return folders


def test_the_eleventh_copy_takes_the_place_of_the_oldest(iq_device, device_root, backups):
    st, dev = iq_device
    assert ciq.APP_LIST_COPIES_KEPT == 10
    full = _full_backups(backups, dev)
    mine = backups / str(dev.unit_id) / "my notes"
    mine.mkdir()
    stamps = _stamps()
    made = []
    for n in range(11):
        order = APPS if n % 2 == 0 else [APPS[1], APPS[0], APPS[2]]
        _list(device_root, out_bin(order))
        made.append(ciq.save_app_list_copy(dev, st, now=next(stamps)))
    assert len(set(made)) == 11
    assert copy_names(dev, backups) == made[1:]  # the oldest went
    unit = backups / str(dev.unit_id)
    assert sorted(p.name for p in unit.iterdir() if not p.name.startswith("GARMIN-APPS")) == (
        sorted([*full, "my notes"]))  # the full backups and the user's folder stay


def test_ten_copies_and_the_same_list_remove_nothing(iq_device, device_root, backups):
    st, dev = iq_device
    stamps = _stamps()
    for n in range(10):
        _list(device_root, out_bin(APPS if n % 2 == 0 else [APPS[1], APPS[0], APPS[2]]))
        ciq.save_app_list_copy(dev, st, now=next(stamps))
    before = copy_names(dev, backups)
    assert ciq.save_app_list_copy(dev, st, now=next(stamps)) == before[-1]  # the same list
    assert copy_names(dev, backups) == before


def test_the_full_backups_keep_their_own_three(iq_device, device_root, backups, monkeypatch):
    """The same keeping for both, each of its own kind: three full backups, the copies of the
    app list beside them untouched."""
    from openexpress import backup

    class Clock(datetime.datetime):
        moments = iter(datetime.datetime(2026, 10, 20, 9, 0, n) for n in range(10))

        @classmethod
        def now(cls, tz=None):
            return next(cls.moments)

    monkeypatch.setattr(backup, "datetime", Clock)  # backups are named by the second
    st, dev = iq_device
    stamps = _stamps()
    _list(device_root, out_bin(APPS))
    copy = ciq.save_app_list_copy(dev, st, now=next(stamps))
    made = [backup.run_backup(dev, st, backups, keep=3).path.name for _n in range(4)]
    unit = backups / str(dev.unit_id)
    assert sorted(p.name for p in unit.iterdir() if backup.is_backup_dir(p)) == made[1:]
    assert copy_names(dev, backups) == [copy]


def test_old_copies_are_removed_with_the_same_rule_as_the_backups():
    """One rule for both (backup.keep_newest): the newest `keep` stay, and never the one just
    made."""
    from pathlib import Path

    from openexpress import backup

    removed = []
    folders = [Path(f"/made-up/{n}") for n in range(5)]  # oldest first
    assert backup.keep_newest(folders, 3, new=folders[0], remove=removed.append) == [folders[1]]
    assert removed == [folders[1]]
    assert backup.keep_newest(folders, 0, remove=removed.append) == []  # 0: keep all
