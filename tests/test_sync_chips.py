"""The chips "To sync" and "Never synced" on the tile of a connected device of the account
(PLAN T9, 24.10.2026): its activities not uploaded yet, counted when it is listed or opened
(sync.activities_to_sync), counted again after a sync without asking the device. All devices
and files are made up."""

import types
from datetime import datetime

import pytest

from openexpress import service, sync
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

NOW = 1_800_000_000.0
DAY = 86400.0
UNIT = 1234567890  # the made-up device of conftest.DEVICE_XML


def _tile(**fields):
    from openexpress.devicelist import Tile

    values = dict(description="fēnix 8", part_number="006-B4532-00", software_version="23.39",
                  kind="mtp", connected=True, on_account=True, primary=False, last_seen=None,
                  image_part_number="006-B4532-00", image_url=None, category=None,
                  account_known=True)
    values.update(fields)
    return Tile(**values)


def _said(tile):
    from openexpress.gui import start

    return start.chips(tile, NOW)


def _words(tile):
    return [(text, style) for text, style, _tooltip in _said(tile)]


# --- the chips -----------------------------------------------------------------------------


def test_activities_not_uploaded_give_the_chip_to_sync_with_how_many_since_when():
    from openexpress.i18n import full_time

    when = full_time(datetime.fromtimestamp(NOW - DAY))
    ((words, style, tooltip),) = _said(_tile(to_sync=3, synced_at=NOW - DAY))
    assert (words, style, tooltip) == ("To sync", "accent", f"3 new activities since {when}")
    assert _said(_tile(to_sync=1, synced_at=NOW - DAY))[0][2] == f"1 new activity since {when}"
    assert _said(_tile(to_sync=2))[0][2] == "2 new activities"  # no sync recorded


def test_nothing_to_sync_or_not_known_gives_no_chip():
    assert _words(_tile(to_sync=0, synced_at=NOW)) == []
    assert _words(_tile(to_sync=None)) == []


def test_a_device_never_synced_here_says_so():
    assert _said(_tile(never_synced=True)) == [("Never synced", "dimmed", None)]


def test_the_primary_device_is_dimmed_the_accent_is_for_what_to_do():
    assert _words(_tile(primary=True)) == [("Primary", "dimmed")]


def test_the_order_of_the_chips_at_most_two():
    """An update, then the sync, then the primary device, then a drive or off the account."""
    update = dict(update_found=NOW)
    assert _words(_tile(to_sync=2, primary=True, **update)) == [
        ("Update available", "warning"), ("To sync", "accent")]
    assert _words(_tile(to_sync=2, primary=True)) == [("To sync", "accent"),
                                                      ("Primary", "dimmed")]
    assert _words(_tile(never_synced=True, primary=True)) == [("Never synced", "dimmed"),
                                                              ("Primary", "dimmed")]


@pytest.mark.parametrize("count, words", [(1, "1 nowa aktywność"), (3, "3 nowe aktywności"),
                                          (5, "5 nowych aktywności"), (22, "22 nowe aktywności"),
                                          (12, "12 nowych aktywności")])
def test_the_chips_in_polish(count, words):
    from openexpress import i18n

    i18n.setup("pl")
    try:
        ((chip, _style, tooltip),) = _said(_tile(to_sync=count, synced_at=NOW))
        assert chip == "Do synchronizacji"
        assert tooltip.startswith(words + " od ")
        assert _words(_tile(never_synced=True)) == [("Nie synchronizowano", "dimmed")]
        assert _words(_tile(primary=True)) == [("Główne", "dimmed")]
    finally:
        i18n.setup("en")


# --- the tiles ------------------------------------------------------------------------------


def _summary(to_sync, kind="mtp", unit_id=UNIT):
    from openexpress.device.gio_storage import DeviceSummary, FoundDevice

    summary = DeviceSummary(FoundDevice("mtp://made-up/", "x", True, kind=kind), "fēnix 8",
                            "006-B4532-00", "23.39", unit_id)
    summary.to_sync = to_sync
    return summary


def _record(lines=(), when=NOW - DAY):
    service._write_json(service.device_last_sync_path(UNIT), {
        "time": when, "unit_id": UNIT, "uploaded": 0, "downloaded": 0, "skipped_known": 0,
        "rejected": 0, "errors": [], "lines": list(lines)})


def _tile_of(summary, account=({"unitid": UNIT},), logged_in=True):
    from openexpress import devicelist

    (tile,) = devicelist.tiles([summary], {}, list(account), logged_in)
    return tile


def test_a_connected_device_of_the_account_gets_its_count_and_when_it_was_synced():
    _record()
    tile = _tile_of(_summary(sync.ActivitiesToSync(UNIT, frozenset({"a.fit"}), 1)))
    assert (tile.to_sync, tile.never_synced, tile.synced_at) == (1, False, NOW - DAY)
    never = _tile_of(_summary(sync.ActivitiesToSync(UNIT)))
    assert (never.to_sync, never.never_synced) == (None, True)


def test_nothing_of_the_sync_for_a_device_off_the_account_not_logged_in_or_a_drive():
    counted = sync.ActivitiesToSync(UNIT, frozenset({"a.fit"}), 1)
    for tile in (_tile_of(_summary(counted), account=()),
                 _tile_of(_summary(counted), logged_in=False),
                 _tile_of(_summary(counted, kind="ums")),
                 _tile_of(_summary(sync.ActivitiesToSync(UNIT)), account=())):
        assert (tile.to_sync, tile.never_synced) == (None, False)


def test_no_chip_while_the_account_does_not_let_a_sync_upload():
    """The last sync could not upload (no consent, device sync off in Garmin Connect): a sync
    would not help, so no chip; the device page says why."""
    _record(lines=[{"kind": "not_uploaded", "reason": "no-consent"}])
    tile = _tile_of(_summary(sync.ActivitiesToSync(UNIT, frozenset({"a.fit"}), 1)))
    assert (tile.to_sync, tile.never_synced) == (None, False)
    _record(lines=[{"kind": "upload_skipped"}])  # sync --download-only: the user's choice
    assert _tile_of(_summary(sync.ActivitiesToSync(UNIT, frozenset({"a.fit"}), 1))).to_sync == 1


def test_the_open_device_s_tile_has_what_its_session_counted():
    from openexpress.gui import app

    counted = sync.ActivitiesToSync(UNIT, frozenset({"a.fit"}), 1)
    session = types.SimpleNamespace(
        device=types.SimpleNamespace(description="fēnix 8", part_number="006-B4532-00",
                                     software_version_str="23.39", unit_id=UNIT),
        storage=types.SimpleNamespace(device_uri="mtp://made-up/"), mass_storage=False,
        to_sync=counted)
    assert app.session_summary(session).to_sync is counted


# --- counted again after a sync, without the device -----------------------------------------


class Untouchable:
    mass_storage = False

    def __getattr__(self, name):
        pytest.fail(f"the device was asked: {name}")


def _counted(device_root):
    storage = LocalStorage(device_root)
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    state = sync.SyncState(device.unit_id)
    state.save()
    return device, sync.activities_to_sync(device, storage)


def _window(device, to_sync, other):
    from fakes import RunNow

    from openexpress.gui.connected import Connected

    session = types.SimpleNamespace(device=device, storage=Untouchable(), logged_in=True,
                                    mass_storage=False, ownership_error=None, to_sync=to_sync)
    drawn = []
    return types.SimpleNamespace(
        session=session, run_task=RunNow(), progress=lambda *a: None, toast=lambda text: None,
        _show_last_sync=lambda: None, connected=Connected(devices=[other]),
        _on_start_page=lambda: True, _start_summaries=lambda: ([], False),
        _show_tiles=lambda summaries, notice: drawn.append(summaries), drawn=drawn)


def _done_all(device):
    state = sync.SyncState(device.unit_id)
    for name in ("2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit"):
        state.mark_completed(sync.ACTIVITY_TYPE, name)
    state.save()


def test_counted_again_after_the_window_s_sync(device_root, monkeypatch):
    """The session and the devices listed: what was listed against state.json as the sync
    left it; the start page drawn again; the device not asked."""
    from openexpress.gui import app

    device, counted = _counted(device_root)
    assert counted.count == 2
    other = _summary(counted)
    win = _window(device, counted, other)
    win._count_activities_again = lambda: app.MainWindow._count_activities_again(win)

    def run_sync(s, first_run, progress):
        _done_all(device)
        return service.SyncSummary()

    monkeypatch.setattr(service, "run_sync", run_sync)
    app.MainWindow.start_sync(win)
    assert win.session.to_sync.count == 0 and other.to_sync.count == 0
    assert win.drawn == [[]]


def test_counted_again_after_a_failed_sync(device_root, monkeypatch):
    """A sync that stopped half way: what it uploaded is done all the same."""
    from openexpress.gui import app

    device, counted = _counted(device_root)
    win = _window(device, counted, _summary(None))
    win._count_activities_again = lambda: app.MainWindow._count_activities_again(win)
    win.run_task.error = "record"

    def run_sync(s, first_run, progress):
        state = sync.SyncState(device.unit_id)
        state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-01-10-00-00.fit")
        state.save()
        raise service.ApiError("the server went away", 503)

    monkeypatch.setattr(service, "run_sync", run_sync)
    app.MainWindow.start_sync(win)
    assert win.session.to_sync.count == 1


def test_counted_again_when_the_background_service_synced(device_root):
    """last_sync.json changed (the background service, or the command line, synced): the
    window counts again, as after its own sync."""
    from gi.repository import Gio

    from openexpress.gui import app

    device, counted = _counted(device_root)
    win = _window(device, counted, _summary(None))
    win._count_activities_again = lambda: app.MainWindow._count_activities_again(win)
    app.MainWindow._watch_last_sync(win)
    _done_all(device)
    path = service.last_sync_path()
    win._last_sync_monitor.emit("changed", Gio.File.new_for_path(str(path)), None,
                                Gio.FileMonitorEvent.CHANGES_DONE_HINT)
    assert win.session.to_sync.count == 0
    win._last_sync_monitor.cancel()


def test_nothing_counted_is_left_as_it_is(device_root):
    """A device without a count (a drive, one off the account) gets none after a sync; the
    start page is not drawn while another page is shown."""
    from openexpress.gui import app

    device, _counted_now = _counted(device_root)
    other = _summary(None)
    win = _window(device, None, other)
    win._on_start_page = lambda: False
    app.MainWindow._count_activities_again(win)
    assert win.session.to_sync is None and other.to_sync is None and win.drawn == []
