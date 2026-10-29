"""Chips on the tiles of the start page: what the window's check of updates found is kept
with the device (devices.json) for its tile, and the tile shows a chip for an update, a USB
drive, the primary device and a device off the account.
All devices are made up."""

import time
import types

import pytest

from openexpress import known_devices

EDGE = 3000000002


def _window(unit_id=EDGE):
    return types.SimpleNamespace(
        session=types.SimpleNamespace(device=types.SimpleNamespace(unit_id=unit_id)),
        _render_update_state=lambda: None)


def _remembered():
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.13", "mtp", now=100.0)


def test_the_window_keeps_what_its_check_of_updates_found():
    """An update found (by the quiet check or the Updates page): its time is kept with the
    device; a check that finds none, and the state after an update was copied (nothing left
    to install, a system update waiting for the unplug), clear it."""
    from openexpress.gui.app import MainWindow

    _remembered()
    win = _window()
    MainWindow._set_update_state(win, ["an update"], [])
    found = known_devices.load()[EDGE].update_found
    assert found is not None and win.update_state == "available"
    MainWindow._set_update_state(win, [], [])  # up to date
    assert known_devices.load()[EDGE].update_found is None
    MainWindow._set_update_state(win, ["an update"], [])
    MainWindow._set_update_state(win, [], ["the system update, copied"])  # installed
    assert known_devices.load()[EDGE].update_found is None and win.update_state == "ready"


def test_a_device_not_remembered_or_without_a_unit_id_keeps_nothing():
    from openexpress.gui.app import MainWindow

    MainWindow._set_update_state(_window(), ["an update"], [])  # not remembered
    assert known_devices.load() == {}
    _remembered()
    MainWindow._set_update_state(_window(unit_id=None), ["an update"], [])
    assert known_devices.load()[EDGE].update_found is None


# --- the chips of a tile -----------------------------------------------------------------------

NOW = 1_800_000_000.0
DAY = 86400.0


def _tile(**fields):
    from openexpress.devicelist import Tile

    values = dict(description="Edge 1050", part_number="006-B4440-00", software_version="33.13",
                  kind="mtp", connected=True, on_account=True, primary=False, last_seen=None,
                  image_part_number="006-B4440-00", image_url=None, category=None,
                  update_found=None, account_known=True)
    values.update(fields)
    return Tile(**values)


def _words(tile, now=NOW):
    from openexpress.gui import start

    return [(text, style) for text, style, _tooltip in start.chips(tile, now)]


def test_no_chip_for_a_device_with_nothing_to_say():
    assert _words(_tile()) == []


def test_an_update_found_gives_its_chip_until_30_days_after_the_check():
    from openexpress.gui import start

    found = NOW - 2 * DAY
    assert _words(_tile(update_found=found)) == [("Update available", "warning")]
    ((_text, _style, tooltip),) = start.chips(_tile(update_found=found), NOW)
    assert tooltip.startswith("Checked ")
    assert _words(_tile(update_found=NOW - 31 * DAY)) == []  # too old: as none


def test_a_drive_the_primary_device_and_one_off_the_account():
    assert _words(_tile(kind="ums", connected=False)) == [("USB drive", "dimmed")]
    assert _words(_tile(primary=True)) == [("Primary", "dimmed")]
    assert _words(_tile(on_account=False)) == [("Not on your account", "dimmed")]


def test_off_the_account_only_when_the_account_is_known_and_never_for_a_drive():
    """Not logged in (or a device without a unit ID): whose it is is not known, no chip. A
    drive (a Varia) is never on the account: it needs none."""
    assert _words(_tile(on_account=False, account_known=False)) == []
    assert _words(_tile(on_account=False, kind="ums")) == [("USB drive", "dimmed")]


def test_at_most_two_chips_the_update_first():
    words = _words(_tile(update_found=NOW, primary=True, on_account=True, kind="mtp"))
    assert words == [("Update available", "warning"), ("Primary", "dimmed")]
    words = _words(_tile(update_found=NOW, kind="ums", on_account=False))
    assert words == [("Update available", "warning"), ("USB drive", "dimmed")]


def test_the_chips_in_polish():
    from openexpress import i18n

    i18n.setup("pl")
    try:
        assert _words(_tile(update_found=NOW, kind="ums")) == [
            ("Aktualizacja dostępna", "warning"), ("Dysk USB", "dimmed")]
        assert _words(_tile(primary=True)) == [("Główne", "dimmed")]
        assert _words(_tile(on_account=False)) == [("Poza kontem", "dimmed")]
    finally:
        i18n.setup("en")


def test_the_words_under_a_connected_device_are_its_version_only():
    """The words under the name of a connected device: its version (or why it has none),
    never how it is connected: a drive says it in its chip, an MTP device is the usual
    way."""
    from openexpress.device.gio_storage import NOT_MOUNTED
    from openexpress.gui.app import choice_subtitle

    def summary(kind, ok=True):
        return types.SimpleNamespace(found=types.SimpleNamespace(kind=kind), ok=ok,
                                     software_version="3.36", reason=NOT_MOUNTED)

    assert choice_subtitle(summary("ums")) == "Software 3.36"
    assert choice_subtitle(summary("mtp")) == "Software 3.36"
    assert choice_subtitle(summary("mtp", ok=False)) == "Not mounted; choose it to open it"


def test_the_tiles_get_what_is_remembered_and_whether_the_account_is_known():
    from fakes import login

    from openexpress import devicelist, tile_facts
    from openexpress.device.gio_storage import DeviceSummary, FoundDevice

    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.13", "mtp", now=100.0)
    known_devices.set_update_found(EDGE, True, now=NOW, software_version="33.13")
    fenix = 3000000001
    known_devices.remember(fenix, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=100.0)
    known_devices.set_update_found(fenix, True, now=NOW - DAY, software_version="23.39")
    connected = [DeviceSummary(FoundDevice("mtp://made-up/", "Edge", True), "Edge 1050",
                               "006-B4440-00", "33.13", EDGE)]
    for logged_in in (False, True):
        login(logged_in)
        tile_facts.note_account([{"unitid": EDGE}])  # read with the login, if any
        tiles = {t.description: t for t in devicelist.tiles(
            connected, known_devices.load(), [{"unitid": EDGE}], logged_in)}
        assert tiles["Edge 1050"].update_found == NOW
        assert tiles["fēnix 8"].update_found == NOW - DAY
        assert tiles["Edge 1050"].account_known is logged_in
        assert tiles["fēnix 8"].account_known is logged_in


def _labels(widget):
    from gi.repository import Gtk

    found = [widget] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child is not None:
        found += _labels(child)
        child = child.get_next_sibling()
    return found


@pytest.mark.needs_display
def test_a_tile_shows_its_chips_under_its_name_and_above_its_version():
    from openexpress.gui import start

    tile = _tile(update_found=time.time() - DAY, kind="ums")
    labels = _labels(start.tile_widget(tile, "Software 3.80"))
    assert [label.get_label() for label in labels] == [
        "Edge 1050", "Update available", "USB drive", "Software 3.80"]
    update, drive = labels[1], labels[2]
    assert {"oe-chip", "warning"} <= set(update.get_css_classes())
    assert {"oe-chip", "dimmed"} <= set(drive.get_css_classes())
    assert update.get_tooltip_text().startswith("Checked ") and drive.get_tooltip_text() is None
    plain = _labels(start.tile_widget(_tile(), "Software 33.13"))
    # no chip: an empty one, without colour, keeps the height of the line
    assert [label.get_label() for label in plain] == ["Edge 1050", "", "Software 33.13"]
    assert plain[1].get_css_classes() == ["oe-chip"]


@pytest.mark.needs_display
def test_a_tile_without_chips_is_as_tall_as_one_with_a_chip():
    """An empty row of chips keeps the height of one chip: tiles with a chip and without are
    as tall, and the words under their names are on one line."""
    from gi.repository import Gdk, Gtk

    from openexpress.gui import start

    start.load_css(Gdk.Display.get_default())  # the size of a chip, as in the window
    tiles = [start.tile_widget(_tile(), "Software 33.13"),
             start.tile_widget(_tile(primary=True), "Software 33.13"),
             start.tile_widget(_tile(update_found=time.time()), "Software 33.13"),
             start.tile_widget(_tile(kind="ums", connected=False), "Software 3.80")]
    heights = [tile.measure(Gtk.Orientation.VERTICAL, 300)[1] for tile in tiles]
    assert len(set(heights)) == 1, heights


@pytest.mark.needs_display
def test_the_start_page_draws_a_tile_again_when_its_chips_change():
    """The same device and words, an update found since (the check ended while the start
    page was shown, or a finding grew too old): its tile is built again, with or without
    the chip."""
    from openexpress.gui import start

    page = start.StartPage()
    page.show([(_tile(), "Software 33.13")], False)
    before = page.tiles()[0]
    page.show([(_tile(update_found=time.time()), "Software 33.13")], False)
    (after,) = page.tiles()
    assert after is not before and "Update available" in [x.get_label() for x in _labels(after)]
    page.show([(_tile(update_found=time.time() - 31 * DAY), "Software 33.13")], False)
    assert "Update available" not in [x.get_label() for x in _labels(page.tiles()[0])]
