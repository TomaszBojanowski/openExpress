"""The view of the start page chosen in its menu (PLAN T6b, 24.10.2026): "View" with
Automatic, Grid and List; automatic (a list from seven devices in a section, each section on
its own) unless another is chosen; kept in gui.json ("start_view"). A change draws the page
again from what is known, the devices are not asked. All devices are made up."""

import types

import pytest
from test_sync_chips import UNIT, _chips_on, _start_window, listed  # noqa: F401 - a fixture

from openexpress import sync

# --- gui.json ---------------------------------------------------------------------------------


def test_the_view_is_automatic_unless_one_was_chosen():
    """No gui.json, no key, a value of another type or not known: automatic, and the file is
    not written again."""
    from openexpress.gui import app

    assert app.StartView.saved() == "auto"
    path = app.gui_settings_path()
    for bad in ("tiles", 3, None, ["list"], {"view": "list"}, True):
        app.save_gui_settings({"start_view": bad, "window": {"width": 800}})
        before = path.read_bytes()
        assert app.StartView.saved() == "auto"
        assert path.read_bytes() == before
    path.write_text("[1, 2]")  # not an object (edited by hand)
    assert app.StartView.saved() == "auto"


def test_the_view_and_the_window_size_keep_each_other():
    from openexpress.gui import app

    app.save_window_geometry(900, 700, False)
    app.StartView.save("list")
    assert app.load_gui_settings() == {
        "window": {"width": 900, "height": 700, "maximized": False}, "start_view": "list"}
    app.save_window_geometry(1000, 740, True)
    assert app.StartView.saved() == "list"
    assert app.load_gui_settings()["window"] == {"width": 1000, "height": 740,
                                                 "maximized": True}


# --- the start page ---------------------------------------------------------------------------


def _tile(name, connected=True):
    from openexpress.devicelist import Tile

    return Tile(name, "006-B4440-00", "33.13", "mtp" if connected else None, connected, False,
                False, None if connected else 1.0, "006-B4440-00", None, None)


def _entries(connected: int, remembered: int):
    return ([(_tile(f"Watch {n}"), "Software 33.13") for n in range(connected)]
            + [(_tile(f"Old {n}", connected=False), "Last seen 2 days ago")
               for n in range(remembered)])


@pytest.mark.needs_display
def test_the_view_chosen_lays_out_the_sections_and_a_click_opens_the_same_device():
    from gi.repository import Gtk

    from openexpress.gui.start import DeviceRow, StartPage

    opened = []
    start = StartPage(opened.append)
    start.view = "list"
    start.show(_entries(3, 1), False)
    rows = start.tiles()
    assert all(isinstance(row, DeviceRow) for row in rows)
    assert [row.get_parent() for row in rows] == [start.list] * 3 + [start.remembered_list]
    assert not start.grid.get_visible() and start.list.get_visible()
    start.list.emit("row-activated", rows[1])
    start.remembered_list.emit("row-activated", rows[3])
    assert opened == [1, 3]
    start.view = "grid"
    start.show(_entries(7, 1), False)
    assert all(isinstance(t, Gtk.FlowBoxChild) for t in start.tiles())
    assert start.grid.get_visible() and not start.list.get_visible()
    start.view = "auto"
    start.show(_entries(7, 1), False)
    assert [type(t).__name__ for t in start.tiles()] == ["DeviceRow"] * 7 + ["FlowBoxChild"]


@pytest.mark.needs_display
def test_a_section_whose_layout_stays_is_not_built_again():
    """Automatic, three connected (tiles) and seven remembered (a list); then List: only the
    tiles become rows, the rows of the remembered ones are the same."""
    from openexpress.gui.start import StartPage

    start = StartPage()
    entries = _entries(3, 7)
    start.show(entries, False)
    before = start.tiles()
    start.view = "list"
    start.show(entries, False)
    after = start.tiles()
    assert after[3:] == before[3:]
    assert all(a is not b for a, b in zip(after[:3], before[:3], strict=True))


# --- the window -------------------------------------------------------------------------------


def _menu_items(model):
    """(label, action, target) of the items of a Gio.MenuModel, by section: [(heading,
    items)]."""
    from gi.repository import GLib

    def attribute(menu, i, name):
        value = menu.get_item_attribute_value(i, name, None)
        if value is None:
            return None
        return value.get_string() if value.is_of_type(GLib.VariantType("s")) else value

    sections = []
    for i in range(model.get_n_items()):
        section = model.get_item_link(i, "section")
        items = [(attribute(section, j, "label"), attribute(section, j, "action"),
                  attribute(section, j, "target")) for j in range(section.get_n_items())]
        sections.append((attribute(model, i, "label"), items))
    return sections


def _menu_of(header):
    from gi.repository import Gtk

    stack = [header]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.MenuButton):
            return widget.get_menu_model()
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    raise AssertionError("no menu button")


@pytest.mark.needs_display
def test_the_window_offers_the_view_in_the_menu_of_the_start_page_only(monkeypatch):
    """The view chosen before is the action's state at start; choosing another keeps it in
    gui.json (the window's size stays) and draws the start page again; a view not known is
    not taken. The menu of the device view stays as it was."""
    from gi.repository import GLib

    from openexpress import netstatus
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    app.save_gui_settings({"window": {"width": 900, "height": 700}, "start_view": "list"})
    monkeypatch.setattr(MainWindow, "refresh_device", lambda self, cause=None: None)
    monkeypatch.setattr(app.GLib, "timeout_add_seconds", lambda s, fn: 0)
    win = MainWindow(None)
    try:
        action = win.lookup_action("start-view")
        assert action.get_state().get_string() == "list" and win.start.view == "list"
        main = _menu_items(_menu_of(win.sidebar_header))
        (view, *rest) = _menu_items(_menu_of(win.start.header))
        assert view == ("View", [("Automatic", "win.start-view", "auto"),
                                 ("Grid", "win.start-view", "grid"),
                                 ("List", "win.start-view", "list")])
        assert rest == main and "View" not in [heading for heading, _items in main]
        drawn = []
        win._render_start = lambda: drawn.append(win.start.view)
        action.activate(GLib.Variant.new_string("grid"))
        assert drawn == ["grid"] and action.get_state().get_string() == "grid"
        assert app.load_gui_settings() == {"window": {"width": 900, "height": 700},
                                           "start_view": "grid"}
        action.activate(GLib.Variant.new_string("tiles"))
        assert drawn == ["grid"] and action.get_state().get_string() == "grid"
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()


@pytest.mark.needs_display
def test_in_polish():
    from openexpress import i18n
    from openexpress.gui import app

    i18n.setup("pl")
    try:
        (view,) = _menu_items(app.StartView.menu(app.Gio.Menu()))
    finally:
        i18n.setup("en")
    assert view == ("Widok", [("Automatycznie", "win.start-view", "auto"),
                              ("Siatka", "win.start-view", "grid"),
                              ("Lista", "win.start-view", "list")])


@pytest.mark.needs_display
def test_the_chip_to_sync_stays_through_a_change_of_view_and_a_later_count_lands(
        listed, monkeypatch):
    """A device counted on the start page: a change of view draws it again with its chip,
    nothing is counted or asked again; a count after a sync changes the chips of the row
    shown then, and back in the grid the tile says the same."""
    from openexpress.device import gio_storage
    from openexpress.gui import app, start

    def asked(*_a, **_k):
        raise AssertionError("a device was asked")

    win = _start_window([listed])
    for name in ("_render_start", "_count_activities_again"):
        setattr(win, name, types.MethodType(getattr(app.MainWindow, name), win))
    win._on_start_page = lambda: True
    win._show_tiles(*win._start_summaries())
    work, done = win.later.pop()
    done(work())
    assert _chips_on(win.start.tiles()[0]) == ["To sync"]
    for name in ("find_all_devices", "probe"):
        monkeypatch.setattr(gio_storage, name, asked)
    app.StartView.choose(win, "list")
    (row,) = win.start.tiles()
    assert isinstance(row, start.DeviceRow) and _chips_on(row) == ["To sync"]
    assert win.later == []  # not counted again
    state = sync.SyncState(UNIT)
    state.mark_completed(sync.ACTIVITY_TYPE, "2026-10-02-10-00-00.fit")
    state.save()  # the other activity uploaded since
    win._count_activities_again()
    assert win.start.tiles() == [row] and _chips_on(row) == []
    app.StartView.choose(win, "grid")
    (tile,) = win.start.tiles()
    assert not isinstance(tile, start.DeviceRow) and _chips_on(tile) == []
