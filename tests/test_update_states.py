"""The states of the updates said the same way everywhere (analysis 14, stage 2, 25.10.2026).

An update copied already, waiting for the device to install it, is "ready": the chip under the
version says "Update ready", the row "Device software" says what to do, the line of the version
says nothing more; "ready" wins over updates still to install ("· N more available"), on the
row and at the top of the Updates page; the tile of the connected device says "Unplug to
install" ("Eject to install" for a drive). The last check that answered is kept with the
device (when, its software then, whether one was copied): the page of a remembered device says
what it found and when ("· checked {when}"), or "Not checked since {when}" for a check too old
or of other software; a failed check on the device page says when what it shows was found.
The page of a remembered device tells its texts against now once a minute, as the start page
does. All devices and updates are made up."""

import json
import time
import types
from datetime import datetime, timedelta

import pytest
from test_update_chips import _tile
from test_update_record import UNIT, _check, _component, _page, _remembered, _shown, _summary
from test_updates_system import (
    BUNDLE,
    _copied,
    _entry,
    _overview,
    _staged_items,
    _updates_page,
)

from openexpress import i18n, known_devices, updates
from openexpress.connect import lower_keys
from openexpress.device.storage import LocalStorage

NB = "\u00a0"
DAY = 86400.0
# A moment of the made-up clock: noon, so that a day back is "yesterday, 12:00".
NOON = datetime(2026, 10, 21, 12, 0)
READY = "Update ready: disconnect the device to install it"


def _bundle():
    return _entry("006-V0000-00", "Test Bundle", "Firmware", "Garmin", "GUPDATE.GSP", BUNDLE)


class _Clock(datetime):
    current = NOON

    @classmethod
    def now(cls, tz=None):
        return cls.current


@pytest.fixture
def clock(monkeypatch):
    """The window's clock (app.datetime), as the tests of the relative dates set it."""
    from openexpress.gui import app

    _Clock.current = NOON
    monkeypatch.setattr(app, "datetime", _Clock)
    return _Clock


# --- devices.json: the last check that answered --------------------------------------------


def test_the_last_check_is_kept_when_the_software_and_whether_one_was_copied():
    _remembered()
    at = NOON.timestamp()
    assert known_devices.set_update_found(UNIT, False, now=at, software_version="23.39",
                                          ready=True)
    entry = known_devices.load()[UNIT]
    assert entry.update_check == known_devices.UpdateCheck(at, "23.39", True)
    assert entry.update_found is None and entry.update_summary is None
    kept = json.loads(known_devices.path().read_text())["devices"][str(UNIT)]["tile_facts"]
    assert kept["update"]["check"] == {"at": at, "software_version": "23.39", "ready": True}
    # Seen again: what the check said stays.
    known_devices.remember(UNIT, "fēnix 8 - 47mm, Solar", "006-B4532-00", "23.39", "mtp")
    assert known_devices.load()[UNIT].update_check == known_devices.UpdateCheck(
        at, "23.39", True)
    # A check that found one to install, nothing copied.
    known_devices.set_update_found(UNIT, True, now=at + 60, summary=_summary(),
                                   software_version="23.39")
    entry = known_devices.load()[UNIT]
    assert entry.update_check == known_devices.UpdateCheck(at + 60, "23.39", False)
    assert entry.update_found == at + 60 and entry.update_summary == _summary()


def test_a_check_without_the_software_keeps_no_record_of_it():
    """As before (a caller that does not know the software): only what was found."""
    _remembered()
    known_devices.set_update_found(UNIT, True, now=NOON.timestamp())
    entry = known_devices.load()[UNIT]
    assert entry.update_check is None and entry.update_found == NOON.timestamp()
    kept = json.loads(known_devices.path().read_text())["devices"][str(UNIT)]
    assert "update_check" not in kept


@pytest.mark.parametrize("damaged", [
    "not an object", [1], {"at": "noon", "software_version": "23.39", "ready": False},
    {"at": 1.0, "software_version": 23.39, "ready": False}, {"at": 1.0, "ready": False},
    {"at": True, "software_version": "23.39", "ready": False},
    {"at": 1.0, "software_version": "23.39", "ready": "yes"},
])
def test_a_damaged_record_of_the_check_is_as_none_and_the_rest_stays(damaged):
    _remembered()
    known_devices.set_update_found(UNIT, True, now=NOON.timestamp(), summary=_summary(),
                                   software_version="23.39")
    data = json.loads(known_devices.path().read_text())
    data["devices"][str(UNIT)]["tile_facts"]["update"]["check"] = damaged
    known_devices.path().write_text(json.dumps(data))
    entry = known_devices.load()[UNIT]
    assert entry.update_check is None and entry.update_summary == _summary()


def test_the_record_of_the_check_is_used_while_recent_and_for_the_same_software():
    _remembered()
    at = NOON.timestamp()
    known_devices.set_update_found(UNIT, False, now=at, software_version="23.39")
    entry = known_devices.load()[UNIT]
    assert known_devices.recorded_check(entry, "23.39", now=at + DAY) == entry.update_check
    assert known_devices.recorded_check(entry, "23.40", now=at + DAY) is None
    assert known_devices.recorded_check(entry, "23.39", now=at + 30 * DAY) is None
    assert known_devices.recorded_check(None, "23.39") is None


# --- the words --------------------------------------------------------------------------------


def test_ready_wins_and_says_how_many_more_are_available():
    from openexpress.gui.words import update_state_text

    assert update_state_text("ready") == READY
    assert update_state_text("ready", 1) == f"{READY}{NB}· 1 more available"
    assert update_state_text("ready", 3) == f"{READY}{NB}· 3 more available"
    assert update_state_text("ready", 2, mass_storage=True) == (
        f"Update ready: eject the device to install it{NB}· 2 more available")


def test_ready_in_polish():
    from openexpress.gui.words import update_state_text

    i18n.setup("pl")
    try:
        said = [update_state_text("ready", n) for n in (1, 2, 5)]
    finally:
        i18n.setup("en")
    ready = "Aktualizacja gotowa: odłącz urządzenie, aby ją zainstalować"
    assert said == [f"{ready}{NB}· jeszcze 1 dostępna", f"{ready}{NB}· jeszcze 2 dostępne",
                    f"{ready}{NB}· jeszcze 5 dostępnych"]


def _entry_with(found=None, check=None, software="23.39"):
    """A remembered entry with what the last check said."""
    return known_devices.KnownDevice(
        UNIT, "fēnix 8 - 47mm, Solar", "006-B4532-00", software, "mtp", 0.0, 0.0,
        update_found=found, update_summary=_summary() if found is not None else None,
        update_check=check)


def test_the_page_of_a_remembered_device_says_what_the_last_check_found_and_when():
    from openexpress.gui.words import remembered_update_text

    now = NOON
    day = (now - timedelta(days=1)).timestamp()
    found = _entry_with(found=day, check=known_devices.UpdateCheck(day, "23.39"))
    assert remembered_update_text(found, now) == (
        f"Update available: Map Region Grouping file 2.31{NB}· checked yesterday, 12:00")
    # An entry of an older version: what was found, without a record of the check.
    assert remembered_update_text(_entry_with(found=day), now) == (
        f"Update available: Map Region Grouping file 2.31{NB}· checked yesterday, 12:00")
    three = (now - timedelta(days=3)).timestamp()
    current = _entry_with(check=known_devices.UpdateCheck(three, "23.39"))
    assert remembered_update_text(current, now) == (
        f"Software is up to date{NB}· checked 3 days ago")


def test_a_check_too_old_or_of_other_software_is_not_told_as_now():
    from openexpress.gui.words import remembered_update_text

    now = NOON
    old = (now - timedelta(days=31)).timestamp()
    when = i18n.full_time(datetime.fromtimestamp(old))
    assert remembered_update_text(
        _entry_with(check=known_devices.UpdateCheck(old, "23.39")), now) == (
        f"Not checked since {when}")
    assert remembered_update_text(
        _entry_with(found=old, check=known_devices.UpdateCheck(old, "23.39")), now) == (
        f"Not checked since {when}")
    day = (now - timedelta(days=1)).timestamp()
    when = i18n.full_time(datetime.fromtimestamp(day))
    # Seen since with other software: the check was of the software before.
    assert remembered_update_text(
        _entry_with(check=known_devices.UpdateCheck(day, "23.38")), now) == (
        f"Not checked since {when}")


def test_nothing_checked_or_one_copied_asks_to_connect_the_device():
    """No record; or the last check found the update copied: the device installs it once
    unplugged, which it has been since."""
    from openexpress.gui.words import remembered_update_text

    day = (NOON - timedelta(days=1)).timestamp()
    assert remembered_update_text(_entry_with(), NOON) == "Connect the device to check"
    assert remembered_update_text(
        _entry_with(check=known_devices.UpdateCheck(day, "23.39", ready=True)), NOON) == (
        "Connect the device to check")
    assert remembered_update_text(None, NOON) == "Connect the device to check"


def test_the_words_of_a_check_in_polish():
    from openexpress.gui.words import remembered_update_text

    day = (NOON - timedelta(days=1)).timestamp()
    old = (NOON - timedelta(days=40)).timestamp()
    i18n.setup("pl")
    try:
        said = [remembered_update_text(_entry_with(check=known_devices.UpdateCheck(day, "23.39")),
                                       NOON),
                remembered_update_text(_entry_with(check=known_devices.UpdateCheck(old, "23.39")),
                                       NOON)]
        when = i18n.full_time(datetime.fromtimestamp(old))
    finally:
        i18n.setup("en")
    assert said == [f"Oprogramowanie jest aktualne{NB}· sprawdzono wczoraj, 12:00",
                    f"Nie sprawdzano od {when}"]


# --- the device page ----------------------------------------------------------------------------


def _staged_answer(device_root, more=True):
    """The bundle copied already; the component still to install, when `more`."""
    _copied(device_root, "006-V0000-00")
    return {"updates": [_bundle()] + ([_component()] if more else [])}


@pytest.mark.needs_display
def test_ready_has_its_chip_and_says_what_to_do_in_the_row_only(device_root):
    _remembered()
    win, _section = _page(device_root, _staged_answer(device_root, more=False))
    _check(win)
    assert win.update_state == "ready"
    assert _shown(win) == (True, READY, True)
    assert win.update_chip.get_label() == "Update ready"
    assert win.software_label.get_label() == "Software 23.39"  # nothing more


@pytest.mark.needs_display
def test_ready_wins_over_updates_still_to_install(device_root):
    _remembered()
    win, _section = _page(device_root, _staged_answer(device_root))
    _check(win)
    assert win.update_state == "ready" and win.update_count == 1
    assert _shown(win) == (True, f"{READY}{NB}· 1 more available", True)
    assert win.update_chip.get_label() == "Update ready"
    items = updates.mark_present(LocalStorage(device_root),
                                 updates.parse_updates(lower_keys(_staged_answer(device_root))))
    # Then the component is installed too: the copied one is left.
    win._set_update_state([], updates.staged(items))
    assert _shown(win) == (True, READY, True)
    # Up to date, then one to install again: the chip says that, not "ready".
    win._set_update_state([], [])
    assert _shown(win) == (False, "Software is up to date", False)
    win._set_update_state(updates.pending(items), [])
    assert _shown(win) == (True, "Update available: Map Region Grouping file 2.31", True)
    assert win.update_chip.get_label() == "Update available"


@pytest.mark.needs_display
def test_a_drive_says_eject():
    items = _staged_items(font_on_device=False)
    win, MainWindow = _overview(drive=True)
    MainWindow._set_update_state(win, updates.pending(items), updates.staged(items))
    assert win.update_row.get_subtitle() == (
        f"Update ready: eject the device to install it{NB}· 1 more available")


@pytest.mark.needs_display
def test_the_check_keeps_whether_one_was_copied(device_root):
    _remembered()
    win, _section = _page(device_root, _staged_answer(device_root))
    _check(win)
    entry = known_devices.load()[UNIT]
    assert entry.update_check.ready and entry.update_check.software_version == "23.39"
    assert entry.update_summary.count == 1  # the one still to install


@pytest.mark.needs_display
def test_the_page_shows_ready_from_the_record_from_its_first_frame(device_root):
    _remembered()
    known_devices.set_update_found(UNIT, True, now=time.time() - DAY, summary=_summary(),
                                   software_version="23.39", ready=True)
    win, _section = _page(device_root, _staged_answer(device_root))
    assert _shown(win) == (True, f"{READY}{NB}· 1 more available", True)
    assert len(win.later) == 1  # the check runs all the same


def _found_yesterday(clock):
    """A record of a day ago by the computer's clock (the page uses it only while recent),
    the window's clock a day after it; and the hour of the record."""
    _remembered()
    found = time.time() - DAY
    known_devices.set_update_found(UNIT, True, now=found, summary=_summary(),
                                   software_version="23.39")
    clock.current = datetime.fromtimestamp(found + DAY)
    return datetime.fromtimestamp(found).strftime("%H:%M")


@pytest.mark.needs_display
def test_a_failed_check_says_when_what_the_page_shows_was_found(device_root, clock):
    from openexpress.connect import ApiError

    hour = _found_yesterday(clock)
    win, _section = _page(device_root, {"updates": [_component()]})
    # While the check runs: as the record says, with no date (nothing moves if it agrees).
    assert _shown(win) == (True, "Update available: Map Region Grouping file 2.31", True)
    _check(win, fails=ApiError("Software update check: HTTP 500", 500))
    assert _shown(win) == (
        True, f"Update available: Map Region Grouping file 2.31{NB}· checked yesterday, {hour}",
        True)


@pytest.mark.needs_display
def test_the_date_of_a_failed_check_is_told_against_now_once_a_minute(device_root, clock):
    from openexpress.connect import ApiError
    from openexpress.gui.app import MainWindow

    _found_yesterday(clock)
    win, _section = _page(device_root, {"updates": [_component()]})
    _check(win, fails=ApiError("Software update check: HTTP 500", 500))
    win.device_view = object()
    win.start = types.SimpleNamespace(page=object())
    win.remembered = types.SimpleNamespace(page=object())
    win.navigation = types.SimpleNamespace(get_visible_page=lambda: win.device_view)
    clock.current += timedelta(days=2)
    assert MainWindow._refresh_relative_texts(win) is True
    assert win.update_row.get_subtitle() == (
        f"Update available: Map Region Grouping file 2.31{NB}· checked 3 days ago")


# --- the Updates page ---------------------------------------------------------------------------


@pytest.mark.needs_display
def test_the_top_of_the_updates_page_says_ready_and_how_many_more():
    _win, status, groups, _buttons = _updates_page(_staged_items(font_on_device=False))
    assert status["texts"][0] == (f"{READY}{NB}· 1 more available", ["title-1"])
    (available,) = groups
    assert [r.get_title() for r in available.rows] == ["Test Font"]


@pytest.mark.needs_display
def test_a_second_system_update_is_listed_while_one_is_ready():
    """Ready wins at the top; another system update (rare) is in the list, not at the top."""
    items = _staged_items()
    other = updates.parse_updates(lower_keys({"updates": [_entry(
        "006-V0001-00", "Other Bundle", "Firmware", "Garmin", "GUPDATE.GCD", b"other")]}))
    _win, status, groups, _buttons = _updates_page(items + other)
    assert status["texts"][0][0] == f"{READY}{NB}· 1 more available"
    (available, *_rest) = groups
    assert [r.get_title() for r in available.rows] == ["System software"]
    assert "Install" not in [label for label, _s in status["buttons"]]


# --- the tile -----------------------------------------------------------------------------------


NOW = 1_800_000_000.0


def _chips(tile, now=NOW):
    from openexpress.gui import start

    return [(text, style) for text, style, _tooltip in start.chips(tile, now)]


def test_a_tile_of_a_device_with_an_update_copied_says_to_unplug_it():
    from openexpress.gui import start

    assert _chips(_tile(update_ready=NOW - 60)) == [("Unplug to install", "warning")]
    ((_words, _style, tooltip),) = start.chips(_tile(update_ready=NOW - 60), NOW)
    assert tooltip.startswith("Checked ")
    assert _chips(_tile(update_ready=NOW - 60, kind="ums")) == [
        ("Eject to install", "warning"), ("USB drive", "dimmed")]
    # Ready wins over an update to install, as on the device page: one of them.
    assert _chips(_tile(update_ready=NOW - 60, update_found=NOW - 60, primary=True)) == [
        ("Unplug to install", "warning"), ("Primary", "dimmed")]


def test_the_tiles_say_unplug_only_for_a_connected_device_with_the_same_software():
    from openexpress import devicelist
    from openexpress.device.gio_storage import DeviceSummary, FoundDevice

    at = time.time() - 60
    known_devices.remember(UNIT, "fēnix 8 - 47mm, Solar", "006-B4532-00", "23.39", "mtp",
                           now=at)
    known_devices.set_update_found(UNIT, False, now=at, software_version="23.39", ready=True)

    def tile(software, connected=True):
        plugged = [DeviceSummary(FoundDevice("mtp://made-up/", "fenix", True),
                                 "fēnix 8 - 47mm, Solar", "006-B4532-00", software, UNIT)]
        (only,) = devicelist.tiles(plugged if connected else [], known_devices.load(), None,
                                   False)
        return only

    assert tile("23.39").update_ready == at
    assert tile("23.40").update_ready is None  # installed since
    assert tile("23.39", connected=False).update_ready is None  # unplugged: it installs it


def test_the_chips_of_ready_in_polish():
    i18n.setup("pl")
    try:
        said = [_chips(_tile(update_ready=NOW)), _chips(_tile(update_ready=NOW, kind="ums"))]
    finally:
        i18n.setup("en")
    assert said == [[("Odłącz, aby zainstalować", "warning")],
                    [("Wysuń, aby zainstalować", "warning"), ("Dysk USB", "dimmed")]]


# --- the page of a remembered device ----------------------------------------------------------


@pytest.mark.needs_display
def test_the_page_of_a_remembered_device_shows_the_last_check():
    from test_remembered_picture import _window

    _remembered()
    found = time.time() - 3 * DAY
    known_devices.set_update_found(UNIT, True, now=found, summary=_summary(),
                                   software_version="23.39")
    win = _window()
    win._show_remembered(known_devices.load()[UNIT])
    assert win.remembered.update_row.get_subtitle() == (
        f"Update available: Map Region Grouping file 2.31{NB}· checked 3 days ago")
    assert win.remembered.map_row.get_subtitle() == "Connect the device to check"


@pytest.mark.needs_display
def test_the_page_of_a_device_only_on_the_account_asks_to_connect_it():
    from test_remembered_picture import _window

    win = _window()
    win._show_account({"unitid": 3000000002, "productdisplayname": "Edge 1050"})
    assert win.remembered.update_row.get_subtitle() == "Connect the device to check"


@pytest.mark.needs_display
def test_the_page_of_a_remembered_device_is_told_against_now_once_a_minute(monkeypatch):
    """As on the start page and the device view: "Last seen", "Last sync" and the check of
    updates drawn again once a minute, only where the words changed; the timer runs while
    the page is shown."""
    from openexpress import netstatus, service, sync
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    timers, real_remove = {}, app.GLib.source_remove

    def add_seconds(seconds, fn):
        timers[len(timers) + 1000] = (seconds, fn)
        return len(timers) + 999

    def remove(source):
        if timers.pop(source, None) is None:
            real_remove(source)

    def minute_timers():
        return [fn for seconds, fn in timers.values() if seconds == app.RELATIVE_REFRESH_S]

    monkeypatch.setattr(app, "datetime", _Clock)
    monkeypatch.setattr(MainWindow, "refresh_device", lambda self, **kw: None)
    monkeypatch.setattr(app.GLib, "timeout_add_seconds", add_seconds)
    monkeypatch.setattr(app.GLib, "source_remove", remove)
    _Clock.current = NOON
    seen = (NOON - timedelta(minutes=2)).timestamp()
    known_devices.remember(UNIT, "fēnix 8 - 47mm, Solar", "006-B4532-00", "23.39", "mtp",
                           now=seen)
    known_devices.set_update_found(UNIT, False, now=seen, software_version="23.39")
    sync.SyncState(UNIT).save()  # synced here before (stage 3: the one source)
    monkeypatch.setattr(service, "load_last_sync",
                        lambda unit_id=None: {"time": seen, "uploaded": 1})
    win = MainWindow(None)
    try:
        win.present()
        win._show_remembered(known_devices.load()[UNIT])
        page = win.remembered
        assert win.navigation.get_visible_page() is page.page
        assert page.seen_label.get_label() == "Last seen 2 min ago"
        assert page.last_sync_row.get_subtitle() == f"2 min ago{NB}· 1 new file, 1 uploaded"
        assert page.update_row.get_subtitle() == f"Software is up to date{NB}· checked 2 min ago"
        changes = []
        for widget, signal in ((page.seen_label, "notify::label"),
                               (page.last_sync_row, "notify::subtitle"),
                               (page.update_row, "notify::subtitle")):
            widget.connect(signal, lambda *_a: changes.append(1))
        (tick,) = minute_timers()
        tick()
        assert changes == []  # the same words: left as they are
        _Clock.current = NOON + timedelta(minutes=1)
        tick()
        assert page.seen_label.get_label() == "Last seen 3 min ago"
        assert page.last_sync_row.get_subtitle() == f"3 min ago{NB}· 1 new file, 1 uploaded"
        assert page.update_row.get_subtitle() == f"Software is up to date{NB}· checked 3 min ago"
        assert len(changes) == 3
        win.navigation.pop()
        assert len(minute_timers()) == 1  # the start page
        win.close()
        assert minute_timers() == []
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()
