"""The copies of the app list (GARMIN-APPS …, 25.10.2026): before a change no new copy is made
while the apps and their order are those of the newest copy (whatever else OUT.BIN says, such
as the free space); at most APP_LIST_COPIES_KEPT copies are kept for a device, the oldest go
when a new one is made, as the full backups keep their newest three. The full backups and
anything else in the folder stay. All devices and apps are made up."""

import datetime
import types
import uuid

import pytest
from test_ciq_music import PRELOADED, STORE_ID, copy_names, iq_device, out_bin  # noqa: F401

from openexpress import ciq, proto

WIDGET = uuid.UUID("0b6a4c20-35a1-4c37-8b1a-7f1d6e2a9c01")
CLOCK = uuid.UUID("0b6a4c20-35a1-4c37-8b1a-7f1d6e2a9c02")


def _stamps():
    return iter(datetime.datetime(2026, 10, 1 + n // 60, 10, n % 60, 0) for n in range(600))


def _with_space(apps, space):
    """OUT.BIN with these apps and another free space: the same list, other bytes (the space
    takes as many bytes, so that the lengths around it stay right)."""
    old, new = proto.enc_varint(1, 66_000_000), proto.enc_varint(1, space)
    assert len(old) == len(new)
    return out_bin(apps).replace(old, new)


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


def _distinct(n):
    """The n-th of lists that all differ (one widget more of its own); a list seen before
    keeps only its newest copy (25.10.2026), so the limit is seen with these."""
    return out_bin(APPS + [(uuid.UUID(int=n + 1), 2, f"W{n}", False, "")])


def test_the_eleventh_copy_takes_the_place_of_the_oldest(iq_device, device_root, backups):
    st, dev = iq_device
    assert ciq.APP_LIST_COPIES_KEPT == 10
    full = _full_backups(backups, dev)
    mine = backups / str(dev.unit_id) / "my notes"
    mine.mkdir()
    stamps = _stamps()
    made = []
    for n in range(11):
        _list(device_root, _distinct(n))
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
        _list(device_root, _distinct(n))
        ciq.save_app_list_copy(dev, st, now=next(stamps))
    before = copy_names(dev, backups)
    assert len(before) == 10
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


# --- the dialog "Restore the order from a copy" lists only copies that change something --------


class _Dialog:
    """Adw.AlertDialog as choose_order uses it: what it is given is kept."""

    made: list = []

    def __init__(self, **kw):
        self.kw, self.responses, self.extra, self.handlers = kw, [], None, {}
        _Dialog.made.append(self)

    def add_response(self, response, label):
        self.responses.append((response, label))

    def set_extra_child(self, widget):
        self.extra = widget

    def connect(self, signal, handler):
        self.handlers[signal] = handler

    def __getattr__(self, name):
        return lambda *a, **kw: None


def _copy_folder(backups, dev, stamp, data, model=" Test watch"):
    """A copy of the app list as openExpress names it, made at `stamp` (HH.MM.SS)."""
    folder = backups / str(dev.unit_id) / f"GARMIN-APPS 2026-10-12 ({stamp}){model}"
    folder.mkdir(parents=True)
    (folder / "OUT.BIN").write_bytes(data)
    return folder.name


def _dialog_of(monkeypatch, st, dev, backups):
    """choose_order of a window that is never shown: the dialog it made, and the errors."""
    from fakes import RunNow

    from openexpress.gui import app

    _Dialog.made = []
    monkeypatch.setattr(app.Adw, "AlertDialog", _Dialog)
    real = ciq.order_choices
    monkeypatch.setattr(ciq, "order_choices", lambda device, state: real(device, state, backups))
    errors = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=st), run_task=RunNow(),
        error=lambda title, text: errors.append(text), _order_blocked=lambda state: None,
        _order_rows=app.MainWindow._order_rows, confirm_order=lambda *a: None)
    app.MainWindow.choose_order(win)
    return _Dialog.made, errors


def _titles(dialog):
    box = dialog.extra.get_child().get_child()
    rows, n = [], 0
    while (row := box.get_row_at_index(n)) is not None:
        rows.append(row.get_title())
        n += 1
    return rows


@pytest.mark.needs_display
def test_the_dialog_lists_only_the_copies_that_change_the_order(iq_device, device_root,
                                                                backups, monkeypatch):
    st, dev = iq_device
    # Two widgets: the device orders each type on its own, so they are what can move.
    clock = (CLOCK, 2, "Clock", False, "")
    now = [*APPS, clock]
    _list(device_root, out_bin(now))
    _copy_folder(backups, dev, "10.00.00", out_bin([*APPS[:2], clock, APPS[2]]))
    _copy_folder(backups, dev, "11.00.00", _with_space(now, 12_345_678))  # the order of now
    _copy_folder(backups, dev, "12.00.00", out_bin([clock, *APPS]))
    (dialog,), errors = _dialog_of(monkeypatch, st, dev, backups)
    assert errors == []
    assert _titles(dialog) == ["2026-10-12 (12.00.00) Test watch",
                               "2026-10-12 (10.00.00) Test watch"]
    assert [r for r, _label in dialog.responses] == ["cancel", "next"]


@pytest.mark.needs_display
def test_without_a_copy_that_differs_one_sentence_and_close(iq_device, device_root, backups,
                                                            monkeypatch):
    st, dev = iq_device
    _list(device_root, out_bin(APPS))
    _copy_folder(backups, dev, "11.00.00", out_bin(APPS))
    (dialog,), errors = _dialog_of(monkeypatch, st, dev, backups)
    assert errors == [] and dialog.extra is None
    assert dialog.kw == {"heading": "Restore the order from a copy",
                         "body": "No copy differs from the current order"}
    assert dialog.responses == [("ok", "Close")]


@pytest.mark.needs_display
def test_without_any_copy_the_dialog_is_as_before(iq_device, device_root, backups, monkeypatch):
    st, dev = iq_device
    _list(device_root, out_bin(APPS))
    made, errors = _dialog_of(monkeypatch, st, dev, backups)
    assert made == [] and errors == [
        ("There are no copies of the app list yet. A copy is made before every change of the "
         "apps on the device.")]


@pytest.mark.needs_display
def test_the_sentence_in_polish(iq_device, device_root, backups, monkeypatch):
    from openexpress import i18n

    st, dev = iq_device
    _list(device_root, out_bin(APPS))
    _copy_folder(backups, dev, "11.00.00", out_bin(APPS))
    i18n.setup("pl")
    try:
        (dialog,), _errors = _dialog_of(monkeypatch, st, dev, backups)
    finally:
        i18n.setup("en")
    assert dialog.kw["body"] == "Żadna kopia nie różni się od obecnej kolejności"
    assert dialog.responses == [("ok", "Zamknij")]


# --- a series of the same list keeps its newest copy --------------------------------------------


def test_older_copies_of_a_list_a_newer_copy_has_go(iq_device, device_root, backups):
    """A, B, A again, then C: the first A goes (the newer A has its list); B, the newer A and
    C stay. Only when a new copy is made."""
    st, dev = iq_device
    a, b = out_bin(APPS), out_bin([APPS[1], APPS[0], APPS[2]])
    first_a = _copy_folder(backups, dev, "10.00.00", a)
    b_name = _copy_folder(backups, dev, "11.00.00", b)
    second_a = _copy_folder(backups, dev, "12.00.00",
                            _with_space(APPS, 12_345_678))  # the same list
    _list(device_root, out_bin(APPS[:2]))
    c = ciq.save_app_list_copy(dev, st, now=datetime.datetime(2026, 10, 12, 13, 0, 0))
    assert copy_names(dev, backups) == [b_name, second_a, c]
    assert first_a not in copy_names(dev, backups)


def test_different_lists_all_stay(iq_device, device_root, backups):
    st, dev = iq_device
    made = [_copy_folder(backups, dev, f"1{n}.00.00", out_bin(order))
            for n, order in enumerate([APPS, [APPS[1], APPS[0], APPS[2]], APPS[:1]])]
    _list(device_root, out_bin(APPS[:2]))
    c = ciq.save_app_list_copy(dev, st, now=datetime.datetime(2026, 10, 12, 20, 0, 0))
    assert copy_names(dev, backups) == [*made, c]


def test_unreadable_copies_and_full_backups_are_left_alone(iq_device, device_root, backups):
    """Two copies that cannot be read, the same bytes: both stay (what they say is not
    known); a full backup too."""
    st, dev = iq_device
    damaged = [_copy_folder(backups, dev, f"1{n}.00.00", b"not an app list") for n in range(2)]
    full = _full_backups(backups, dev)
    _list(device_root, out_bin(APPS))
    c = ciq.save_app_list_copy(dev, st, now=datetime.datetime(2026, 10, 12, 20, 0, 0))
    unit = backups / str(dev.unit_id)
    assert sorted(p.name for p in unit.iterdir()) == sorted([*damaged, c, *full])


def test_the_limit_counts_after_the_series(iq_device, device_root, backups):
    """Ten copies, the fourth with the list of the eighth: it goes, and then ten are left with
    the new one, so the oldest stays."""
    st, dev = iq_device

    def listed(n):
        return out_bin(APPS + [(uuid.UUID(int=n + 1), 2, f"W{n}", False, "")])

    made = [_copy_folder(backups, dev, f"1{n}.00.00", listed(7 if n == 3 else n))
            for n in range(10)]
    _list(device_root, out_bin(APPS[:1]))
    c = ciq.save_app_list_copy(dev, st, now=datetime.datetime(2026, 10, 12, 20, 0, 0))
    assert copy_names(dev, backups) == [*made[:3], *made[4:], c]
