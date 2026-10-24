"""Chips in the list of a section of many devices (more than devicelist.GRID_MAX; PLAN T9,
24.10.2026): a row is laid out as its tile, the name, its chips, the words under the name,
with the same chips (start.chips: the words, the classes, the order, at most two), changed
in place when a count comes; a row is reached and opened from the keyboard and tells a
screen reader its name, chips and words. All devices are made up."""

import dataclasses
import time

import pytest

NOW = time.time()


def _tile(name, **fields):
    from openexpress.devicelist import Tile

    values = dict(description=name, part_number="006-B4440-00", software_version="33.13",
                  kind="mtp", connected=True, on_account=True, primary=False, last_seen=None,
                  image_part_number="006-B4440-00", image_url=None, category=None,
                  update_found=None, account_known=True)
    values.update(fields)
    return Tile(**values)


def _entries():
    """Seven connected devices (a list), with every kind of chip and of pair; one of them
    would get three chips, one none."""
    return [(_tile("Watch 0", update_found=NOW, on_account=False), "Software 1.00"),
            (_tile("Varia RTL515", kind="ums"), "Software 3.80"),
            (_tile("Watch 2", to_sync=3, synced_at=NOW - 3600), "Software 1.02"),
            (_tile("Watch 3", never_synced=True, primary=True), "Software 1.03"),
            (_tile("Watch 4", update_found=NOW, to_sync=1, primary=True), "Software 1.04"),
            (_tile("Watch 5"), "Software 1.05"),
            (_tile("Watch 6", primary=True), "Software 1.06")]


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def _shown(widget) -> bool:
    """Shown with its row or tile (the page of these tests is never on a screen)."""
    from gi.repository import Gtk

    while widget is not None and not isinstance(widget, (Gtk.ListBoxRow, Gtk.FlowBoxChild)):
        if not widget.get_visible():
            return False
        widget = widget.get_parent()
    return True


def _chips(widget):
    """(words, classes, tooltip) of the chips shown in a tile or a row, in their order."""
    from gi.repository import Gtk

    return [(w.get_label(), set(w.get_css_classes()), w.get_tooltip_text())
            for w in _descendants(widget) if isinstance(w, Gtk.Label)
            and w.has_css_class("oe-chip") and w.get_label() and _shown(w)]


def _page(on_open=None):
    from openexpress.gui.start import StartPage

    start = StartPage(on_open)
    entries = _entries()
    start.show(entries, False)
    rows = start.tiles()
    assert len(rows) == 7 and all(row.get_parent() is start.list for row in rows)
    return start, entries, rows


@pytest.mark.needs_display
def test_a_row_shows_the_chips_of_its_tile():
    from openexpress.gui import start as start_page

    _start, entries, rows = _page()
    for (tile, how), row in zip(entries, rows, strict=True):
        said = [(words, {"oe-chip", style}, tooltip)
                for words, style, tooltip in start_page.chips(tile)]
        assert _chips(row) == _chips(start_page.tile_widget(tile, how)) == said
    assert [len(_chips(row)) for row in rows] == [2, 1, 1, 2, 2, 0, 1]
    assert [words for words, _c, _t in _chips(rows[4])] == ["Update available", "To sync"]


@pytest.mark.needs_display
def test_a_row_keeps_its_title_and_subtitle_and_the_tooltip_of_a_device_not_connected():
    from openexpress.gui.start import StartPage

    seen = NOW - 2 * 86400
    entries = [(_tile(f"Watch {n}", connected=False, kind=None, last_seen=seen),
                "Last seen 2 days ago") for n in range(7)]
    start = StartPage()
    start.show(entries, False)
    row = start.tiles()[0]
    assert (row.get_title(), row.get_subtitle()) == ("Watch 0", "Last seen 2 days ago")
    assert "activatable" in row.get_css_classes()  # lit under the pointer, as a row of Adw
    assert row.get_tooltip_text().startswith("Last seen ")


@pytest.mark.needs_display
def test_a_count_changes_the_chips_in_the_same_row():
    """The count of activities came after the list was drawn: set_chips changes the chips of
    that row, the same object, and the list is not built again."""
    start, entries, rows = _page()
    tile, how = entries[5]
    counted = dataclasses.replace(tile, to_sync=4, synced_at=NOW)
    assert start.set_chips(5, counted)
    assert start.tiles() == rows and start.tiles()[5] is rows[5]
    assert [words for words, _c, _t in _chips(rows[5])] == ["To sync"]
    assert rows[5].accessible_label == "Watch 5, To sync, Software 1.05"
    entries[5] = (counted, how)
    start.show(entries, False)  # the same as shown now: nothing is built again
    assert start.tiles() == rows
    assert start.set_chips(5, tile)  # counted again: nothing to sync, no chip
    assert _chips(rows[5]) == [] and rows[5].accessible_label == "Watch 5, Software 1.05"


@pytest.mark.needs_display
def test_a_row_is_opened_from_the_keyboard_and_tells_what_it_shows():
    """Focusable and activatable: Enter or Space on it (the list's binding for both) opens
    its device, by its place in the whole list. A screen reader hears its name, its chips
    and its words."""
    from gi.repository import GLib, Gtk

    opened = []
    start, _entries_, rows = _page(opened.append)
    window = Gtk.Window(default_width=600, default_height=800)
    window.set_child(start.page)
    window.present()
    try:
        context = GLib.MainContext.default()
        for _ in range(50):
            context.iteration(False)
        row = rows[3]
        assert row.get_focusable() and row.get_activatable()
        assert row.grab_focus()
        start.list.emit("activate-cursor-row")
        assert opened == [3]
        assert row.accessible_label == ("Watch 3, Never synced, Primary, Software 1.03")
    finally:
        window.destroy()
