"""The chips "To sync" and "Never synced" on the tile of a connected device of the account
(PLAN T9, 24.10.2026): its activities not uploaded yet, counted when it is listed or opened
(sync.activities_to_sync), counted again after a sync without asking the device. All devices
and files are made up."""

import threading
import types
from datetime import datetime

import pytest
from fakes import record_sync

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


def test_not_counted_gives_no_chip():
    """While the count runs, or when it failed, nothing is known: no chip."""
    assert _words(_tile(to_sync=None)) == []
    assert _words(_tile(to_sync=None, synced_at=NOW)) == []


def test_synced_before_with_nothing_to_upload_says_synced():
    """Counted, nothing to upload, and synced here before (its sync state is there: a device
    never synced has no count): dimmed, as the primary device is, without a tooltip. Only
    with a sync recorded (25.10.2026): files marked as uploaded alone give none."""
    assert _said(_tile(to_sync=0, synced_at=NOW)) == [("Synced", "dimmed", None)]
    assert _said(_tile(to_sync=0)) == []


def test_synced_never_comes_with_to_sync_or_never_synced():
    assert _words(_tile(to_sync=2, synced_at=NOW)) == [("To sync", "accent")]
    assert _words(_tile(never_synced=True)) == [("Never synced", "dimmed")]
    assert _words(_tile(never_synced=True, to_sync=0)) == [("Never synced", "dimmed")]


def test_a_device_never_synced_here_says_so():
    assert _said(_tile(never_synced=True)) == [
        ("Never synced", "dimmed", "Never synced by this installation of openExpress")]


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


def test_synced_comes_last_and_gives_way_to_every_other_chip():
    """Last in the order, also after the primary device (decision of 25.10.2026): with the
    primary device both are there; with an update and the primary device, Synced is the one
    left out."""
    update = dict(update_found=NOW, synced_at=NOW)
    assert _words(_tile(to_sync=0, primary=True, synced_at=NOW)) == [("Primary", "dimmed"),
                                                                     ("Synced", "dimmed")]
    assert _words(_tile(to_sync=0, **update)) == [("Update available", "warning"),
                                                  ("Synced", "dimmed")]
    assert _words(_tile(to_sync=0, primary=True, **update)) == [
        ("Update available", "warning"), ("Primary", "dimmed")]


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
        assert _words(_tile(to_sync=0, synced_at=NOW)) == [("Zsynchronizowane", "dimmed")]
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
    """Its tile, once the window has written what it knows of it (fakes.noted), logged in
    or not."""
    from fakes import login, noted

    from openexpress import devicelist, known_devices

    login(logged_in)
    noted([summary], list(account) if logged_in else None)
    (tile,) = devicelist.tiles([summary], known_devices.load(), list(account), logged_in)
    return tile


def test_a_connected_device_of_the_account_gets_its_count_and_when_it_was_synced():
    sync.SyncState(UNIT).save()  # counted: synced here before
    _record()
    tile = _tile_of(_summary(sync.ActivitiesToSync(UNIT, frozenset({"a.fit"}), 1)))
    assert (tile.to_sync, tile.never_synced, tile.synced_at) == (1, False, NOW - DAY)
    sync.state_path(UNIT).unlink()  # never synced here: nothing to count
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
    sync.SyncState(UNIT).save()  # counted: synced here before
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
                                    mass_storage=False, ownership_error=None, to_sync=to_sync,
                                    stop_waiting=threading.Event())
    drawn = []
    return types.SimpleNamespace(
        session=session, run_task=RunNow(), progress=lambda *a: None, toast=lambda text: None,
        _show_last_sync=lambda: None, connected=Connected(devices=[other]),
        _on_start_page=lambda: True, _start_summaries=lambda: ([], False),
        _update_tile_chips=lambda: drawn.append("chips"), drawn=drawn)


def _done_all(device):
    state = sync.SyncState(device.unit_id)
    for name in ("2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit"):
        state.mark_completed(sync.ACTIVITY_TYPE, name)
    state.save()


def test_counted_again_after_the_window_s_sync(device_root, monkeypatch):
    """The session and the devices listed: what was listed against state.json as the sync
    left it; only the rows of chips of the start page changed; the device not asked."""
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
    assert win.drawn == ["chips"]


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
    start page is not changed while another page is shown."""
    from openexpress.gui import app

    device, _counted_now = _counted(device_root)
    other = _summary(None)
    win = _window(device, None, other)
    win._on_start_page = lambda: False
    app.MainWindow._count_activities_again(win)
    assert win.session.to_sync is None and other.to_sync is None and win.drawn == []


# --- counted in the background, once the tiles are drawn ------------------------------------


@pytest.fixture
def listed(device_root, monkeypatch):
    """The made-up device as the window's list reads it (probe), with one of its two
    activities uploaded."""
    from openexpress.device import gio_storage

    monkeypatch.setattr(gio_storage, "_mtp_storage", lambda uri, usb: LocalStorage(device_root))
    found = gio_storage.FoundDevice("mtp://made-up/", "x", True,
                                    usb=gio_storage.GarminUsb("51b4", "usb", 1, 9))
    summary = gio_storage.probe(found)
    state = sync.SyncState(UNIT)
    state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-01-10-00-00.fit")
    state.save()
    return summary


def _start_window(summaries, session=None, account=({"unitid": UNIT},)):
    """The window's start page (the real one) with the list of `summaries`, logged in, the
    account's list read (`account`: the device on it) and written with the devices as the
    window does (fakes.noted); work in the background waits in `later`."""
    from fakes import login, noted

    from openexpress.gui import app, start
    from openexpress.gui.connected import Connected

    login()
    noted(summaries, list(account))
    later = []
    win = types.SimpleNamespace(
        session=session, start=start.StartPage(None), _account=list(account),
        connected=Connected(devices=list(summaries), choosing=session is None),
        _start_entries=[], _start_texts=[], later=later, _load_tile_pictures=lambda t: None,
        in_background=lambda work, done, failed=None: later.append((work, done)))
    for name in ("_show_tiles", "_start_summaries", "_is_open", "_count_activities",
                 "_activities_counted", "_update_tile_chips"):
        setattr(win, name, types.MethodType(getattr(app.MainWindow, name, None), win))
    return win


def _chips_on(item):
    from gi.repository import Gtk

    found, stack = [], [item]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label) and widget.has_css_class("oe-chip") and widget.get_label():
            found.append(widget.get_label())
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


@pytest.mark.needs_display
def test_the_tiles_are_drawn_before_the_count_and_its_chip_comes_after(listed):
    """The list is drawn at once, the tile without a chip (not known yet); the count runs in
    the background, and its result puts the chip into the same tile (the page is not drawn
    again)."""
    win = _start_window([listed])
    win._show_tiles(*win._start_summaries())
    (tile,) = win.start.tiles()
    assert _chips_on(tile) == [] and len(win.later) == 1
    work, done = win.later.pop()
    done(work())
    assert win.start.tiles() == [tile] and _chips_on(tile) == ["To sync"]
    assert listed.to_sync.count == 1
    win._show_tiles(*win._start_summaries())  # drawn again: counted once, the tile stays
    assert win.later == [] and win.start.tiles() == [tile]


@pytest.mark.needs_display
@pytest.mark.parametrize("view", ["grid", "list"])
def test_nothing_to_upload_puts_synced_into_the_same_tile_or_row(listed, view):
    """Both activities uploaded before: the count of 0 puts Synced into the tile (or the row
    of a list) drawn before it, by its row of chips alone."""
    from openexpress.gui import start

    state = sync.SyncState(UNIT)
    state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-02-10-00-00.fit")
    state.save()  # by a sync that recorded itself
    record_sync(UNIT, uploaded=1, downloaded=0, skipped_known=0, errors=[], lines=[])
    win = _start_window([listed])
    win.start.view = view
    win._show_tiles(*win._start_summaries())
    (item,) = win.start.tiles()
    assert isinstance(item, start.DeviceRow) is (view == "list")
    assert _chips_on(item) == []
    drawn = []
    win.start.show = lambda *a, **kw: drawn.append(a)  # not drawn again
    work, done = win.later.pop()
    done(work())
    assert listed.to_sync.count == 0
    assert win.start.tiles() == [item] and _chips_on(item) == ["Synced"] and drawn == []


@pytest.mark.needs_display
def test_a_count_for_a_device_unplugged_meanwhile_is_dropped(listed, caplog):
    import logging

    win = _start_window([listed])
    win._show_tiles(*win._start_summaries())
    work, done = win.later.pop()
    win.connected.devices = []  # unplugged: the list read again without it
    win._show_tiles(*win._start_summaries())
    with caplog.at_level(logging.INFO, logger="openexpress"):
        done(work())
    # Its tile now: only on the account, without chips.
    assert listed.to_sync is None and [_chips_on(t) for t in win.start.tiles()] == [[]]
    assert "activities: the count of a device no longer listed or open is dropped" in caplog.text


@pytest.mark.needs_display
def test_the_open_device_is_counted_for_its_session(listed):
    """The device open now: counted once for its session, which keeps it (the tile of the
    list and the one made of the session say the same); dropped once another is open."""
    session = types.SimpleNamespace(
        device=listed.device, storage=listed.storage, mass_storage=False, logged_in=True,
        ownership_error=None, to_sync=None, to_sync_asked=False)
    win = _start_window([listed], session=session)
    win._show_tiles(*win._start_summaries())
    work, done = win.later.pop()
    done(work())
    assert session.to_sync.count == 1 and listed.to_sync is session.to_sync
    assert _chips_on(win.start.tiles()[0]) == ["To sync"]
    session.to_sync_asked, session.to_sync = False, None
    win._show_tiles(*win._start_summaries())
    work, done = win.later.pop()
    win.session = types.SimpleNamespace(**vars(session))  # another session meanwhile
    done(work())
    assert session.to_sync is None


@pytest.mark.needs_display
def test_no_count_for_a_device_off_the_account_or_a_drive(listed):
    win = _start_window([listed], account=[])  # logged in, the device not on the account
    win._show_tiles(*win._start_summaries())
    assert win.later == []
    off = types.SimpleNamespace(device=listed.device, storage=listed.storage,
                                mass_storage=False, ownership_error=Exception("not yours"),
                                to_sync=None, to_sync_asked=False)
    win = _start_window([listed], session=off)
    win._show_tiles(*win._start_summaries())
    assert win.later == []
