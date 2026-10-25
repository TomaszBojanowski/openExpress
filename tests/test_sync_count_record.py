"""The tiles from their first frame (analysis 14, 25.10.2026; the rule of stage 2 for the
updates): what the count in the background last found for a connected device of the account is
kept with it (devices.json: how many to upload, when, and the version of its sync state it was
counted against), and its tile shows "To sync" / "Synced" from it at once while the record is
of this installation, at most 7 days old, and the state of the sync has not changed since. The
count runs as before: the same result changes nothing on the screen, another one changes the
chip and the record. A device never synced here says so from the first frame (no state.json:
nothing to count). No record for a drive or a device off the account; Forget removes it. All
devices and files are made up."""

import json
import time
import types

import pytest
from test_sync_chips import UNIT, _chips_on, _start_window, listed  # noqa: F401 - the fixture

from openexpress import known_devices, service, sync

DAY = 86400.0


def _remember():
    known_devices.remember(UNIT, "fēnix 8", "006-B4532-00", "23.39", "mtp")


def _state(*done):
    state = sync.SyncState(UNIT)
    for name in done:
        state.mark_completed(sync.ACTIVITY_TYPE, name)
    state.save()


def _synced():
    """A sync recorded, as the window's would leave it."""
    service.record_sync(UNIT, uploaded=1, downloaded=0, skipped_known=0, errors=[], lines=[])


def _keep(count, at=None, state=None):
    known_devices.set_sync_count(UNIT, known_devices.SyncCount(
        count, time.time() if at is None else at,
        service.sync_stamp(UNIT) if state is None else state))


# --- devices.json ---------------------------------------------------------------------------


def test_the_record_keeps_the_count_when_and_the_state_it_was_counted_against():
    _remember()
    _state()
    _keep(3, at=1_800_000_000.0)
    entry = known_devices.load()[UNIT]
    stamp = service.sync_stamp(UNIT)
    assert entry.sync_count == known_devices.SyncCount(3, 1_800_000_000.0, stamp)
    kept = json.loads(known_devices.path().read_text())["devices"][str(UNIT)]
    assert kept["sync_count"] == {"count": 3, "at": 1_800_000_000.0, "state": stamp}
    known_devices.remember(UNIT, "fēnix 8", "006-B4532-00", "23.39", "mtp")  # seen again
    assert known_devices.load()[UNIT].sync_count == entry.sync_count


@pytest.mark.parametrize("damaged", ["x", [1], {"count": -1, "at": 1.0, "state": "s"},
                                     {"count": True, "at": 1.0, "state": "s"},
                                     {"count": 1, "at": "noon", "state": "s"},
                                     {"count": 1, "at": 1.0, "state": 7}, {"count": 1}])
def test_a_damaged_record_is_as_none_and_the_rest_stays(damaged):
    _remember()
    _state()
    _keep(3)
    data = json.loads(known_devices.path().read_text())
    data["devices"][str(UNIT)]["sync_count"] = damaged
    known_devices.path().write_text(json.dumps(data))
    entry = known_devices.load()[UNIT]
    assert entry.sync_count is None and entry.description == "fēnix 8"


def test_the_version_of_the_state_changes_with_a_sync_and_is_none_without_one():
    assert service.sync_stamp(UNIT) is None  # never synced here
    _state()
    before = service.sync_stamp(UNIT)
    assert before is not None
    time.sleep(0.01)
    _state("2026-10-01-10-00-00.fit")
    assert service.sync_stamp(UNIT) != before
    after = service.sync_stamp(UNIT)
    _synced()
    assert service.sync_stamp(UNIT) != after  # the record of a sync too


def test_the_record_is_used_while_recent_and_of_the_same_state():
    _remember()
    _state()
    _keep(3)
    entry = known_devices.load()[UNIT]
    stamp = service.sync_stamp(UNIT)
    assert known_devices.recorded_sync_count(entry, stamp) == 3
    assert known_devices.recorded_sync_count(entry, stamp + "-other") is None
    assert known_devices.recorded_sync_count(entry, None) is None
    assert known_devices.recorded_sync_count(entry, stamp, now=time.time() + 7 * DAY) is None
    assert known_devices.recorded_sync_count(None, stamp) is None


def test_forget_removes_it():
    from openexpress import forget

    _remember()
    _state()
    _keep(3)
    assert forget.forget_device(UNIT)
    assert UNIT not in known_devices.load()


# --- the tile from the first frame --------------------------------------------------------------


def _summary(to_sync=None, kind="mtp"):
    from test_sync_chips import _summary

    return _summary(to_sync, kind=kind)


def _tile(summary, account=({"unitid": UNIT},), logged_in=True):
    from openexpress import devicelist

    (tile,) = devicelist.tiles([summary], known_devices.load(), list(account), logged_in)
    return tile


def test_a_recent_record_gives_the_chip_before_the_count():
    _remember()
    _state()
    _synced()
    _keep(3)
    tile = _tile(_summary())
    assert (tile.to_sync, tile.never_synced) == (3, False)
    _keep(0)
    assert _tile(_summary()).to_sync == 0  # "Synced"


def test_an_old_record_or_one_of_another_state_gives_no_chip():
    _remember()
    _state()
    _synced()
    _keep(3, at=time.time() - 8 * DAY)
    assert _tile(_summary()).to_sync is None
    _keep(3)
    time.sleep(0.01)
    _state("2026-10-01-10-00-00.fit")  # synced since (by the command line, say)
    assert _tile(_summary()).to_sync is None


def test_the_count_comes_before_the_record():
    _remember()
    _state()
    _synced()
    _keep(3)
    counted = sync.ActivitiesToSync(UNIT, frozenset({"a.fit"}), 1)
    assert _tile(_summary(counted)).to_sync == 1


def test_a_device_never_synced_here_says_so_from_the_first_frame():
    _remember()
    tile = _tile(_summary())
    assert (tile.to_sync, tile.never_synced) == (None, True)


def test_no_record_and_no_chip_for_a_drive_off_the_account_or_not_logged_in():
    _remember()
    _state()
    _synced()
    _keep(3)
    for tile in (_tile(_summary(kind="ums")), _tile(_summary(), account=()),
                 _tile(_summary(), logged_in=False)):
        assert (tile.to_sync, tile.never_synced) == (None, False)


def test_after_forget_no_chip():
    from openexpress import forget

    _remember()
    _state()
    _synced()
    _keep(3)
    forget.forget_device(UNIT)
    tile = _tile(_summary())
    # Forgotten: its state went too, so it is a device never synced here.
    assert (tile.to_sync, tile.never_synced) == (None, True)


# --- the window -----------------------------------------------------------------------------------


@pytest.mark.needs_display
def test_the_window_draws_the_chip_from_the_record_and_the_same_count_changes_nothing(listed):
    _remember()
    _synced()
    _keep(1)  # one of the two activities is uploaded (the fixture)
    win = _start_window([listed])
    set_chips = []
    real = win.start.set_chips
    win.start.set_chips = lambda key, tile: (set_chips.append(key), real(key, tile))[1]
    win._show_tiles(*win._start_summaries())
    (item,) = win.start.tiles()
    assert _chips_on(item) == ["To sync"]  # before the count
    at = known_devices.load()[UNIT].sync_count.at
    time.sleep(0.01)
    work, done = win.later.pop()
    done(work())
    assert listed.to_sync.count == 1 and set_chips == []  # the same: nothing drawn
    assert win.start.tiles() == [item] and _chips_on(item) == ["To sync"]
    assert known_devices.load()[UNIT].sync_count.at > at  # counted again: kept anew


@pytest.mark.needs_display
def test_another_count_changes_the_chip_and_the_record(listed):
    _remember()
    _synced()
    _keep(0)  # the record says nothing to upload; the device has one activity more
    win = _start_window([listed])
    set_chips = []
    real = win.start.set_chips
    win.start.set_chips = lambda key, tile: (set_chips.append(key), real(key, tile))[1]
    win._show_tiles(*win._start_summaries())
    (item,) = win.start.tiles()
    assert _chips_on(item) == ["Synced"]
    work, done = win.later.pop()
    done(work())
    assert set_chips == [0] and _chips_on(win.start.tiles()[0]) == ["To sync"]
    assert known_devices.load()[UNIT].sync_count.count == 1


@pytest.mark.needs_display
def test_after_a_sync_the_record_is_what_is_counted_again(listed):
    from openexpress.gui import app

    _remember()
    _synced()
    win = _start_window([listed])
    win._count_activities_again = types.MethodType(app.MainWindow._count_activities_again,
                                                   win)
    win._on_start_page = lambda: True
    win._show_tiles(*win._start_summaries())
    work, done = win.later.pop()
    done(work())
    assert known_devices.load()[UNIT].sync_count.count == 1
    _state("2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit")  # the other uploaded
    _synced()
    win._count_activities_again()
    kept = known_devices.load()[UNIT].sync_count
    assert kept.count == 0 and kept.state == service.sync_stamp(UNIT)


def test_a_count_not_known_keeps_the_record():
    """The count failed (None): what was kept stays, the tile keeps showing it."""
    from openexpress.gui import app

    _remember()
    _state()
    _keep(3)
    app.remember_sync_count(None)
    app.remember_sync_count(sync.ActivitiesToSync(UNIT))  # never synced: nothing to keep
    assert known_devices.load()[UNIT].sync_count.count == 3


# --- the page of the connected device ---------------------------------------------------------


@pytest.mark.needs_display
def test_the_last_sync_of_the_device_page_says_the_count_from_the_record_too():
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    _remember()
    _state()
    _synced()
    _keep(2)
    win = types.SimpleNamespace(
        last_sync_row=Adw.ExpanderRow(title="Last sync"), _last_sync_children=[],
        session=types.SimpleNamespace(device=types.SimpleNamespace(unit_id=UNIT),
                                      ownership_error=None, to_sync=None))
    MainWindow._show_last_sync(win)
    assert win.last_sync_row.get_subtitle().endswith("\u00a0· 2 new activities to upload")
