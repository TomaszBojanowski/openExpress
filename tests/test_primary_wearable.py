"""The account's primary wearable (in Garmin Connect "Primary wearable" / "Podstawowe
urządzenie typu wearable": the device that steps and sleep come from), read only: the tooltip of
the chip "Primary" and the row "Primary wearable" of the device page, from the account's list it
has already. Set as Primary with the account: test_ownership. All devices and accounts are made
up."""

import types

import pytest

from openexpress import i18n

FENIX, EDGE, VENU = 3000000001, 3000000002, 3000000004


def _entry(unit, name, capable=True, primary=False):
    return {"unitid": unit, "productdisplayname": name,
            "primaryactivitytrackersettingcapable": capable,
            "primaryactivitytrackerindicator": primary}


def _account(primary=FENIX):
    return [_entry(FENIX, "fēnix 8 - 47mm, Solar", primary=primary == FENIX),
            _entry(EDGE, "Edge 1050", capable=False),
            _entry(VENU, "Venu 2", primary=primary == VENU)]


# --- what the account's list says -------------------------------------------------------------


def test_which_device_of_the_account_is_the_primary_wearable():
    from openexpress.devicelist import PrimaryWearable, primary_wearable

    assert primary_wearable(_account(), FENIX) == PrimaryWearable(True, "fēnix 8 - 47mm, Solar")
    assert primary_wearable(_account(VENU), FENIX) == PrimaryWearable(False, "Venu 2")
    assert primary_wearable(_account(None), FENIX) == PrimaryWearable(False, "", False)


def test_none_for_a_device_that_cannot_be_one_or_is_not_known():
    """Not able to (the Edge: primaryactivitytrackersettingcapable false), not said, not on
    the list, no list: no row."""
    from openexpress.devicelist import primary_wearable

    assert primary_wearable(_account(), EDGE) is None
    unknown = [dict(e) for e in _account()]
    del unknown[0]["primaryactivitytrackersettingcapable"]
    assert primary_wearable(unknown, FENIX) is not None  # it is the one: said all the same
    unknown[0]["primaryactivitytrackerindicator"] = False
    assert primary_wearable(unknown, FENIX) is None
    assert primary_wearable(_account(), 3000000099) is None
    assert primary_wearable(None, FENIX) is None
    assert primary_wearable([{"unitid": "not a number"}], FENIX) is None


def test_its_words_are_those_of_the_primary_training_device():
    from openexpress.devicelist import PrimaryWearable
    from openexpress.gui.words import primary_wearable_text

    assert primary_wearable_text(PrimaryWearable(True, "x")) == "This device"
    assert primary_wearable_text(PrimaryWearable(False, "Venu 2")) == "Venu 2"
    assert primary_wearable_text(PrimaryWearable(False, "")) == "Another device"
    assert primary_wearable_text(PrimaryWearable(False, "", False)) == "Not set"


# --- the chip -----------------------------------------------------------------------------------


def _tile(**fields):
    from openexpress.devicelist import Tile

    values = dict(description="fēnix 8", part_number="006-B4532-00", software_version="23.39",
                  kind="mtp", connected=True, on_account=True, primary=False, last_seen=None,
                  image_part_number="006-B4532-00", image_url=None, category=None,
                  account_known=True)
    values.update(fields)
    return Tile(**values)


def test_the_chip_primary_says_what_it_is_in_its_tooltip():
    from openexpress.gui import start

    ((words, style, tooltip),) = start.chips(_tile(primary=True))
    assert (words, style) == ("Primary", "dimmed")
    assert tooltip == "Primary wearable: steps and sleep in Garmin Connect come from this device"


# --- the device page ------------------------------------------------------------------------------


def _page(unit=FENIX, account=None, **session):
    """The device page as the window builds it (test_one_language._device_page), logged in
    with the account's list as the ownership check read it; the primary training device is
    not asked (its row says "Checking…")."""
    from fakes import page_window
    from gi.repository import Adw
    from test_one_language import _texts

    from openexpress.gui.app import MainWindow

    win, section = page_window("watch")
    device = types.SimpleNamespace(description="fēnix 8 - 47mm, Solar",
                                   software_version_str="23.39", part_number="006-B4532-00",
                                   unit_id=unit)
    fields = dict(device=device, storage=None, logged_in=True, ownership_error=None,
                  account_devices=_account() if account is None else account,
                  mass_storage=False, space=(None, None),
                  client=types.SimpleNamespace(auth=types.SimpleNamespace(display="x")))
    fields.update(session)
    win.session = types.SimpleNamespace(**fields)
    win._account, win.update_state, win.update_count, win.map_state = None, None, 0, None
    win._update_check_session = None
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync", "_render_map_state", "select_section", "backup", "logout",
                 "_show_primary_training"):
        setattr(win, name, lambda *a, **kw: None)
    win._auto_sync_row = lambda: Adw.ActionRow(title="Sync automatically when connected")
    for name in ("show_overview", "_set_update_state", "_render_update_state",
                 "_highlight_update"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.show_overview()
    return win, section, _texts(section)


def _rows(texts):
    return sorted((title, sub) for title, sub in texts
                  if title in ("Primary training device", "Primary wearable"))


@pytest.mark.needs_display
def test_the_page_says_which_device_is_the_primary_wearable_under_the_training_device():
    from gi.repository import Adw

    _win, section, texts = _page()
    assert _rows(texts) == [("Primary training device", "Checking…"),
                            ("Primary wearable", "This device")]
    (group,) = [g for g in section.groups if g.get_title() == "Garmin Connect account"]
    titles = [w.get_title() for w in _descendants(group) if isinstance(w, Adw.ActionRow)]
    assert titles[-2:] == ["Primary training device", "Primary wearable"]  # just under it
    _win, _section, texts = _page(account=_account(VENU))
    assert _rows(texts)[1] == ("Primary wearable", "Venu 2")
    _win, _section, texts = _page(account=_account(None))
    assert _rows(texts)[1] == ("Primary wearable", "Not set")


@pytest.mark.needs_display
def test_the_row_offers_set_as_primary_when_another_device_is_it():
    from gi.repository import Adw, Gtk

    _win, section, _texts = _page(account=_account(VENU))
    (row,) = [w for g in section.groups for w in _descendants(g)
              if isinstance(w, Adw.ActionRow) and w.get_title() == "Primary wearable"]
    assert not row.get_activatable()
    assert [w.get_label() for w in _descendants(row) if isinstance(w, Gtk.Button)
            and w.get_label()] == ["Set as Primary"]


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


@pytest.mark.needs_display
def test_no_row_for_a_device_that_cannot_be_one_or_while_the_list_is_not_known():
    from openexpress import service

    assert _rows(_page(unit=EDGE)[2]) == [("Primary training device", "Checking…")]
    assert _rows(_page(logged_in=False)[2]) == [("Primary training device", "Log in to see")]
    off = service.NotAccountDevice("x")
    assert _rows(_page(unit=3000000099, ownership_error=off)[2]) == [
        ("Primary training device", "Not available")]


@pytest.mark.needs_display
def test_the_row_asks_nothing_of_the_account():
    """From the list the ownership check read: nothing runs in the background for it."""
    win, _section, _texts = _page()
    asked = []
    win.in_background = lambda *a, **kw: asked.append(a)
    win._show_primary_training = lambda row: None
    win.show_overview()
    assert asked == []


@pytest.mark.needs_display
def test_in_polish():
    from openexpress.gui import start

    i18n.setup("pl")
    try:
        _win, _section, texts = _page()
        rows = [(t, s) for t, s in texts if t == "Podstawowe urządzenie typu wearable"]
        ((_words, _style, tooltip),) = start.chips(_tile(primary=True))
        _win, _section, other = _page(account=_account(None))
        unset = [s for t, s in other if t == "Podstawowe urządzenie typu wearable"]
    finally:
        i18n.setup("en")
    assert rows == [("Podstawowe urządzenie typu wearable", "To urządzenie")]
    assert unset == ["Nie ustawiono"]
    # Hard spaces after the one-letter words (tools/po-hard-spaces.py).
    assert tooltip == ("Podstawowe urządzenie typu wearable: kroki i\u00a0sen w\u00a0Garmin Connect "
                       "pochodzą z\u00a0tego urządzenia")
