"""The states of the sync said the same way everywhere: "Never synced" (no state.json here) on
tile and page, "Synced" only with a sync recorded, the count of "To sync" at the end of "Last
sync", "Primary training device" when it cannot be read, and the start page without the
account's list. All devices and files are made up."""

import types

import pytest
from fakes import record_sync, window_methods
from fakes import window as stand_in

from openexpress import i18n, service, sync

NB = "\u00a0"
UNIT = 1234567890  # the made-up device of conftest.DEVICE_XML
NEVER = "Never synced"
NO_RECORD = "No record of the last sync; the next sync uploads new data only"


def _state():
    """The device synced here before (or its files marked as uploaded): its state.json."""
    sync.SyncState(UNIT).save()


def _record(**fields):
    values = dict(uploaded=1, downloaded=0, skipped_known=0, errors=[], lines=[])
    values.update(fields)
    record_sync(UNIT, **values)


def _tile(**fields):
    from openexpress.devicelist import Tile

    values = dict(description="fēnix 8", part_number="006-B4532-00", software_version="23.39",
                  kind="mtp", connected=True, on_account=True, primary=False, last_seen=None,
                  image_part_number="006-B4532-00", image_url=None, category=None,
                  account_known=True)
    values.update(fields)
    return Tile(**values)


def _chips(tile):
    from openexpress.gui import start

    return start.chips(tile)


# --- one source for "never synced" -----------------------------------------------------------


def test_synced_here_is_whether_the_device_has_its_state():
    assert sync.synced_here(UNIT) is False
    _record()  # a record alone (state.json gone): never synced here all the same
    assert sync.synced_here(UNIT) is False
    _state()
    assert sync.synced_here(UNIT) is True


def _last_sync_row(to_sync=None, ownership_error=None):
    """The row "Last sync" of the device page as the window fills it."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    win = stand_in(
        last_sync_row=Adw.ExpanderRow(title="Last sync"), _last_sync_children=[],
        session=types.SimpleNamespace(device=types.SimpleNamespace(unit_id=UNIT),
                                      ownership_error=ownership_error, to_sync=to_sync))
    MainWindow._show_last_sync(win)
    return win


@pytest.mark.needs_display
def test_a_device_never_synced_here_says_so_on_its_page_as_on_its_tile():
    win = _last_sync_row()
    row = win.last_sync_row
    ((chip, _style, tooltip),) = _chips(_tile(never_synced=True))
    assert row.get_subtitle() == chip == NEVER
    assert row.get_tooltip_text() == tooltip == "Never synced by this installation of openExpress"
    assert not row.get_enable_expansion()


@pytest.mark.needs_display
def test_a_record_without_a_state_is_never_synced_too():
    """state.json is the one source: the first sync asks again how to begin."""
    _record()
    assert _last_sync_row().last_sync_row.get_subtitle() == NEVER


@pytest.mark.needs_display
def test_files_marked_as_uploaded_without_a_sync_say_what_comes_next():
    _state()  # baseline --apply alone
    win = _last_sync_row()
    assert win.last_sync_row.get_subtitle() == NO_RECORD
    _record()
    win = _last_sync_row()
    assert win.last_sync_row.get_subtitle().startswith("just now")


def test_synced_needs_a_sync_recorded():
    """A count of 0 comes for a device with its state.json; the chip "Synced" also needs the
    time of a sync (from last_sync.json): files marked as uploaded alone give no chip."""
    assert [w for w, _s, _t in _chips(_tile(to_sync=0))] == []
    assert [w for w, _s, _t in _chips(_tile(to_sync=0, synced_at=1.0))] == ["Synced"]


def test_the_tiles_after_baseline_alone_say_nothing_of_the_sync():
    from openexpress import devicelist, known_devices
    from openexpress.device.gio_storage import DeviceSummary, FoundDevice

    _state()
    summary = DeviceSummary(FoundDevice("mtp://made-up/", "fenix", True), "fēnix 8",
                            "006-B4532-00", "23.39", UNIT)
    summary.to_sync = sync.ActivitiesToSync(UNIT, frozenset(), 0)

    def chips():
        from fakes import login, noted

        login()
        summary.to_sync = summary.to_sync.again()  # as the window counts after a sync
        noted([summary], [{"unitid": UNIT}])
        (tile,) = devicelist.tiles([summary], known_devices.load(), [{"unitid": UNIT}], True)
        return [w for w, _s, _t in _chips(tile)]

    assert chips() == []
    _record()
    assert chips() == ["Synced"]


@pytest.mark.needs_display
def test_the_page_of_a_remembered_device_says_the_same():
    from fakes import remembered_device as _remembered
    from fakes import remembered_window as _window

    known = _remembered()  # the unit of conftest's device
    assert known.unit_id == UNIT
    win = _window()
    win._show_remembered(known)
    assert win.remembered.last_sync_row.get_subtitle() == NEVER
    _state()
    win._show_remembered(known)
    assert win.remembered.last_sync_row.get_subtitle() == NO_RECORD


def test_never_synced_in_polish():
    i18n.setup("pl")
    try:
        ((chip, _style, tooltip),) = _chips(_tile(never_synced=True))
        from openexpress.gui.words import no_sync_record

        record = no_sync_record()
    finally:
        i18n.setup("en")
    assert (chip, tooltip) == ("Nie synchronizowano",
                               "Ta instalacja openExpress nie synchronizowała tego urządzenia")
    assert record == ("Brak zapisu ostatniej synchronizacji; następna wyśle tylko nowe dane")


# --- how many activities there are to upload -----------------------------------------------


def _counted(count):
    """`count` activities listed on the device, none of them uploaded yet."""
    return sync.ActivitiesToSync(UNIT, frozenset(f"{n}.fit" for n in range(count)), count)


@pytest.mark.needs_display
def test_the_last_sync_says_how_many_new_activities_there_are_to_upload():
    _state()
    _record()
    said = _last_sync_row(_counted(3)).last_sync_row.get_subtitle()
    assert said.startswith("just now") and said.endswith(f"{NB}· 3 new activities to upload")
    assert _last_sync_row(_counted(1)).last_sync_row.get_subtitle().endswith(
        f"{NB}· 1 new activity to upload")
    assert "to upload" not in _last_sync_row(_counted(0)).last_sync_row.get_subtitle()
    assert "to upload" not in _last_sync_row(None).last_sync_row.get_subtitle()


@pytest.mark.needs_display
def test_after_baseline_alone_the_count_is_said_too():
    _state()
    assert _last_sync_row(_counted(2)).last_sync_row.get_subtitle() == (
        f"{NO_RECORD}{NB}· 2 new activities to upload")


@pytest.mark.needs_display
def test_no_count_when_the_last_sync_could_not_upload():
    """As the tile, which shows no chip then: a sync would not help, the details say why."""
    _state()
    _record(uploaded=0, lines=[service.not_uploaded_step(types.SimpleNamespace(
        allowed=False, reason="consent"))])
    assert "to upload" not in _last_sync_row(_counted(3)).last_sync_row.get_subtitle()


def test_the_count_in_polish():
    from openexpress.gui.words import to_upload_text

    i18n.setup("pl")
    try:
        said = [to_upload_text(n) for n in (1, 2, 5)]
    finally:
        i18n.setup("en")
    assert said == ["1 nowa aktywność do wysłania", "2 nowe aktywności do wysłania",
                    "5 nowych aktywności do wysłania"]


@pytest.mark.needs_display
def test_the_count_that_comes_later_is_said_on_the_page():
    """The count runs in the background: once it comes for the open device, the row says
    it, and the tiles get their chips too."""
    from openexpress.gui.app import MainWindow

    _state()
    _record()
    win = _last_sync_row()
    s = win.session
    chips = []
    win.connected = types.SimpleNamespace(devices=[])
    win._is_open = lambda summary: False
    win._update_tile_chips = lambda: chips.append(1)
    MainWindow._activities_counted(win, s, _counted(4))
    assert win.last_sync_row.get_subtitle().endswith(f"{NB}· 4 new activities to upload")
    assert chips == [1]


def test_the_open_device_is_counted_once_without_the_start_page():
    """Opened at once (one device plugged in): its page asks for the count, once."""
    from openexpress.gui.device_page import count_open_activities

    asked = []
    s = types.SimpleNamespace(
        device=types.SimpleNamespace(unit_id=UNIT), storage=types.SimpleNamespace(),
        mass_storage=False, ownership_error=None, logged_in=True, to_sync_asked=False)
    win = stand_in(session=s,
                   in_background=lambda work, done, failed=None: asked.append(work))
    count_open_activities(win)
    count_open_activities(win)
    assert len(asked) == 1 and s.to_sync_asked


@pytest.mark.parametrize("fields", [{"mass_storage": True,
                                     "drive_refusal": ("connect-disabled", "")},
                                    {"logged_in": False},
                                    {"ownership_error": RuntimeError("x")},
                                    {"to_sync_asked": True}])
def test_a_drive_not_synced_one_not_logged_in_or_off_the_account_is_not_counted(fields):
    from openexpress.gui.device_page import count_open_activities

    asked = []
    values = dict(device=types.SimpleNamespace(unit_id=UNIT), storage=types.SimpleNamespace(),
                  mass_storage=False, ownership_error=None, logged_in=True, to_sync_asked=False)
    values.update(fields)
    win = stand_in(session=types.SimpleNamespace(**values),
                   in_background=lambda work, done, failed=None: asked.append(work))
    count_open_activities(win)
    assert asked == []


# --- the tooltip of the chip "Never synced" ---------------------------------------------------


def test_the_chip_never_synced_says_by_which_installation():
    ((_chip, _style, tooltip),) = _chips(_tile(never_synced=True))
    assert tooltip == "Never synced by this installation of openExpress"


# --- the row of the primary training device stays ------------------------------------------


def _page_texts(**session):
    from test_one_language import _device_page, _texts

    _win, section = _device_page(**session)
    return _texts(section)


def _primary(texts, title="Primary training device"):
    return [sub for row, sub in texts if row == title]


@pytest.mark.needs_display
def test_not_logged_in_the_row_says_to_log_in():
    texts = _page_texts(logged_in=False)
    assert ("Garmin Connect account", "") in texts
    assert _primary(texts) == ["Log in to see"]


@pytest.mark.needs_display
def test_off_the_account_or_a_failed_check_the_row_says_so():
    off = service.NotAccountDevice("This device is not registered to the Garmin account.")
    assert _primary(_page_texts(logged_in=True, ownership_error=off)) == ["Not available"]
    failed = RuntimeError("the account could not be read")
    assert _primary(_page_texts(logged_in=True, ownership_error=failed)) == ["Could not check"]
    assert _primary(_page_texts(logged_in=True, account_devices=None)) == ["Could not check"]


@pytest.mark.needs_display
def test_a_drive_has_no_row_of_the_primary_training_device():
    assert _primary(_page_texts(logged_in=False, mass_storage=True)) == []
    assert _primary(_page_texts(logged_in=True, mass_storage=True)) == []


@pytest.mark.needs_display
def test_the_row_in_polish():
    i18n.setup("pl")
    try:
        texts = _page_texts(logged_in=False)
        off = service.NotAccountDevice("x")
        off_texts = _page_texts(logged_in=True, ownership_error=off)
    finally:
        i18n.setup("en")
    title = "Podstawowe urządzenie treningowe"
    assert _primary(texts, title) == ["Zaloguj się, aby zobaczyć"]
    assert _primary(off_texts, title) == ["Niedostępne"]


# --- the account's list not known ----------------------------------------------------------


LOGGED_OUT = ("Not logged in to Garmin Connect: the tiles do not show what is on your "
              "account or what there is to sync.")
FAILED = ("Your Garmin Connect account could not be read: the tiles show only what was known "
          "of it before.")


def _account_window(why=None):

    notes = []
    win = stand_in(
        _account=None, _account_why=why, drawn=[], _start_entries=[("a tile", "its source")],
        start=types.SimpleNamespace(set_account_note=notes.append),
        _on_start_page=lambda: True, _on_remembered_page=lambda: False,
        in_background=lambda work, done, failed=None: done(work()))
    win._render_start = lambda: win.drawn.append(1)
    window_methods(win, ["_set_account"])
    return win, notes


def test_what_the_window_knows_of_the_account(in_the_window):
    from openexpress.gui.app import MainWindow

    win, notes = _account_window()
    for answer, why in ((None, "logged-out"), ([{"unitid": 1}], None)):
        in_the_window("account_client", lambda answer=answer: types.SimpleNamespace(
            auth=None if answer is None else object(), account_devices=lambda fresh=False: answer))
        MainWindow._fetch_account(win)
        assert (win._account, win._account_why) == (answer, why)
    win.in_background = lambda work, done, failed=None: failed(OSError("no network"))
    MainWindow._fetch_account(win)
    assert (win._account, win._account_why) == (None, "failed")
    # The list came, then went: drawn; not logged in at first: the same tiles, the sentence
    # under them alone.
    assert len(win.drawn) == 2 and notes == [LOGGED_OUT]


@pytest.mark.parametrize("why, note", [("logged-out", LOGGED_OUT), ("failed", FAILED),
                                       (None, None)])
def test_the_start_page_says_once_why_it_says_nothing_of_the_account(why, note):
    from openexpress.gui.start_window import account_note

    assert account_note(why) == note


@pytest.mark.needs_display
def test_the_note_is_under_the_tiles_and_only_with_tiles():
    from gi.repository import Gtk

    from openexpress.gui import start

    page = start.StartPage()
    page.show([(_tile(), "Software 23.39")], False)
    page.set_account_note(LOGGED_OUT)
    note = page.account_note
    assert note.get_visible() and note.get_label() == LOGGED_OUT and note.get_wrap()
    assert note.has_css_class("dim-label")
    # Below the last section.
    box = note.get_parent()
    assert isinstance(box, Gtk.Box) and box.get_last_child() is note
    page.set_account_note(None)
    assert not note.get_visible()


def test_the_window_gives_the_note_with_the_tiles_drawn():
    """Drawn with the tiles: the note for the state of the account, none without tiles."""
    from openexpress.gui.start_window import show_account_note

    notes = []
    win = stand_in(_account_why="logged-out",
                   start=types.SimpleNamespace(set_account_note=notes.append))
    show_account_note(win, [("a tile", "its source")])
    show_account_note(win, [])
    win._account_why = None
    show_account_note(win, [("a tile", "its source")])
    assert notes == [LOGGED_OUT, None, None]


def test_logging_out_says_it_on_the_start_page(monkeypatch, in_the_window):
    from openexpress.auth import store
    from openexpress.gui.app import MainWindow

    win = stand_in(_account=[{"unitid": 1}], _account_why=None, toasts=[])
    win.toast = win.toasts.append
    changed = []
    in_the_window("account_changed", lambda w, cause: changed.append(cause))
    monkeypatch.setattr(store, "delete", lambda: None)
    MainWindow.logout(win)
    assert (win._account, win._account_why) == (None, "logged-out") and changed


def test_the_notes_in_polish():
    from openexpress.gui.start_window import account_note

    i18n.setup("pl")
    try:
        said = [account_note("logged-out"), account_note("failed")]
    finally:
        i18n.setup("en")
    assert said == [
        ("Nie zalogowano do Garmin Connect: kafle nie pokazują, co jest na koncie ani co jest "
         "do synchronizacji."),
        ("Nie udało się odczytać konta Garmin Connect: kafle pokazują tylko to, co było "
         "o\u00a0nim wiadomo wcześniej.")]
