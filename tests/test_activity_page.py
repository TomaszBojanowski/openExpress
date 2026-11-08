"""The page of one activity, pushed onto the window's navigation from its row: its sport and
start in the header bar with the back arrow and the GPX export, its tiles, and what a file
that could not be read, one still being written, a damaged one and one without a summary say;
its file read again in a task from the device or from a backup's copy. Every file is made up
from the public FIT protocol."""

import types
from datetime import UTC, datetime

import pytest
from fakes import (
    FIT_HEART_RECORD,
    FIT_SESSION,
    FitData,
    FitDefinition,
    RunNow,
    contrast,
    descendants,
    fit_file,
    luminance,
    program_style,
    stalled_session,
    tasks_at_once,
    tells_stalls,
    window,
)

pytestmark = pytest.mark.needs_display

FIT_EPOCH = int(datetime(1989, 12, 31, tzinfo=UTC).timestamp())
START = 1_100_000_000  # a time of the FIT protocol
HIKING, TRAINING = 17, 10
SESSION = FIT_SESSION
PLACED = FitDefinition(1, 20, ((253, 0x86), (0, 0x85), (1, 0x85), (3, 0x02)))
HEART = FIT_HEART_RECORD
NOTHING_READ = "The file of this activity could not be read"  # a title: no period


def hike(cut=None, bad_crc=False) -> bytes:
    return fit_file(PLACED, FitData(1, (START, 624_000_000, 250_000_000, 120)),
                    FitData(1, (START + 1, 624_000_100, 250_000_000, 121)),
                    SESSION, FitData(2, (START, HIKING, 0, 14_400_000, 13_800_000, 1_240_000,
                                         900, None, 1000, 128, 165, 740)),
                    cut=cut, bad_crc=bad_crc)


def workout() -> bytes:
    return fit_file(HEART, FitData(1, (START, 110)), FitData(1, (START + 1, 112)),
                    SESSION, FitData(2, (START, TRAINING, 0, 2_700_000, 2_700_000, 0, 320,
                                         None, None, 112, 151, None)))


def read_of(source="device", sport=HIKING, state="WHOLE", positions=True):
    """The row's activity (activities.Read) and what its summary says (`state` None: no FIT
    file)."""
    from openexpress import activities, fit

    found = None if state is None else fit.ActivitySummary(
        getattr(fit.FileState, state), start=START + FIT_EPOCH, sport=sport, timer_time=600.0,
        has_positions=positions)
    where = activities.Activity(activities.Source(source), "GARMIN/Activity/a.fit", "a.fit",
                                100, 1.0, None if source == "device" else datetime(2026, 1, 2))
    return activities.Read(where, found)


def a_window(data, *, busy=False):
    """A stand-in window with the real navigation: the list under it; reading gives `data`
    (bytes, None: not readable, an exception: raised)."""
    from gi.repository import Adw, Gtk

    navigation = Adw.NavigationView()
    the_list = Adw.NavigationPage(title="Device", tag="device", child=Gtk.Label())
    navigation.add(the_list)
    toplevel = Gtk.Window(child=navigation)

    def read_file(activity):
        if isinstance(data, Exception):
            raise data
        return data

    toasts = []
    win = window(navigation=navigation, toplevel=toplevel, busy=busy, _action_buttons=[],
                 run_task=RunNow(), toast=toasts.append,
                 activities=types.SimpleNamespace(read_file=read_file))
    win.toasts = toasts
    return win, the_list


def shown(win):
    return win.navigation.get_visible_page()


def seen(widget, page) -> bool:
    """Whether the widget would be seen on the page: it and every widget up to the page shown
    (a page of a Gtk.Stack that is not the visible one is not)."""
    while widget is not page:
        if not widget.get_visible() or not widget.get_child_visible():
            return False
        widget = widget.get_parent()
    return True


def labels(page) -> list[str]:
    from gi.repository import Gtk

    return [w.get_label() for w in descendants(page) if isinstance(w, Gtk.Label) and seen(w, page)]


def status_texts(page) -> list[str]:
    from gi.repository import Adw

    return [t for w in descendants(page) if isinstance(w, Adw.StatusPage) and seen(w, page)
            for t in (w.get_title(), w.get_description()) if t]


def open_page(win, read):
    from openexpress.gui import activity_page

    activity_page.open_activity(win, read)
    return shown(win)


def test_a_row_opens_the_page_of_its_activity_and_reads_it():
    from gi.repository import Adw

    win, the_list = a_window(hike())
    page = open_page(win, read_of())
    assert page is not the_list and page.get_tag() == "activity"
    (call,) = win.run_task.calls
    assert call.title == "Reading the Activity…" and call.in_page and call.device
    title = next(w for w in descendants(page) if isinstance(w, Adw.WindowTitle))
    assert title.get_title() == "Hiking" and title.get_subtitle()
    texts = labels(page)
    for value, name in [("12.40 km", "Distance"), ("3:50:00", "Time"),
                        ("16:40 /km", "Average Pace"), ("128 bpm", "Average Heart Rate"),
                        ("740 m", "Total Ascent")]:
        assert value in texts and name in texts


def test_back_returns_to_the_list():
    win, the_list = a_window(hike())
    open_page(win, read_of())
    win.navigation.pop()
    assert shown(win) is the_list


def test_the_gpx_button_in_the_header_exports_as_the_row_does(monkeypatch):
    from gi.repository import Adw, Gtk

    from openexpress.gui import activities_page

    exported = []
    monkeypatch.setattr(activities_page, "_export",
                        lambda win, read, button: exported.append(read))
    win, _the_list = a_window(hike())
    read = read_of()
    page = open_page(win, read)
    header = next(w for w in descendants(page) if isinstance(w, Adw.HeaderBar))
    (button,) = [w for w in descendants(header) if isinstance(w, Gtk.Button)
                 and w.get_tooltip_text() == "Export as GPX…"]
    button.emit("clicked")
    assert exported == [read]


def test_an_activity_without_positions_has_its_gpx_button_off():
    from gi.repository import Gtk

    win, _the_list = a_window(workout())
    page = open_page(win, read_of(sport=TRAINING, positions=False))
    (button,) = [w for w in descendants(page) if isinstance(w, Gtk.Button)
                 and w.get_tooltip_text() == "No GPS positions in this activity"]
    assert not button.get_sensitive()
    texts = labels(page)
    assert {"45:00", "Time", "112 bpm", "151 bpm", "320 kcal", "Calories"} <= set(texts)
    assert "Distance" not in texts


def test_a_copy_in_a_backup_is_read_without_the_device():
    win, _the_list = a_window(hike())
    open_page(win, read_of(source="backup"))
    (call,) = win.run_task.calls
    assert not call.device


@pytest.mark.parametrize("fails", ["copy", "device", "no FIT file"])
def test_a_file_that_could_not_be_read_is_said_on_the_page(fails):
    """A copy in a backup that could not be read, an error of the device, a file that is no
    FIT file: the page says it, no error dialog (RunNow raises one)."""
    from openexpress.device.storage import DeviceError

    data = {"copy": None, "device": DeviceError("Cannot read GARMIN/Activity/a.fit: gone"),
            "no FIT file": b"not a FIT file at all"}[fails]
    win, _the_list = a_window(data)
    page = open_page(win, read_of())
    assert NOTHING_READ in status_texts(page)
    assert labels(page).count("12.40 km") == 0


@pytest.mark.parametrize("state", ["still being written", "damaged", "no summary"])
def test_what_a_file_still_being_written_a_damaged_one_and_one_without_a_summary_say(state):
    data = {"still being written": hike(cut=len(hike()) - 40), "damaged": hike(bad_crc=True),
            "no summary": fit_file(HEART, FitData(1, (START, 110)))}[state]  # no session
    win, _the_list = a_window(data)
    page = open_page(win, read_of())
    if state == "damaged":  # its tiles, and that the file is damaged
        assert "Damaged file" in labels(page) and "12.40 km" in labels(page)
    else:
        said = {"still being written": ["Still Being Written",
                                        "Unplug the device and plug it in again"],
                "no summary": ["No Summary", "This activity has no summary"]}[state]
        assert status_texts(page) == said


def test_while_another_task_runs_no_page_opens():
    win, the_list = a_window(hike(), busy=True)
    open_page(win, read_of())
    assert shown(win) is the_list and win.run_task.calls == []
    assert win.toasts == ["Another operation is in progress"]


def test_a_stall_while_the_activity_is_read_closes_the_page_with_the_device(monkeypatch):
    """No error dialog: the banner says it, and the page goes with the device."""
    from openexpress.device.gio_storage import GarminUsb
    from openexpress.device.storage import DeviceNotResponding
    from openexpress.gui.app import MainWindow

    tasks_at_once(monkeypatch)
    error = DeviceNotResponding.during("Cannot read GARMIN/Activity/a.fit")
    error.usb = GarminUsb("51b4", 3, 14)
    win, the_list = a_window(error)
    told = tells_stalls(win, monkeypatch)
    win.run_task = types.MethodType(MainWindow.run_task, win)
    win.session = stalled_session(GarminUsb("51b4", 3, 14), "Made-up watch", stalled=True)
    win.show_no_device = lambda: win.navigation.pop_to_page(the_list)
    open_page(win, read_of())
    assert told.errors == [] and shown(win) is the_list and win.session is None


def test_rows_open_their_activity_but_for_a_file_that_is_no_fit_file():
    from gi.repository import Gtk

    from openexpress.gui.activities_page import activity_row

    win, _the_list = a_window(hike())
    row = activity_row(win, read_of())
    assert row.get_activatable()
    assert "go-next-symbolic" in [w.get_icon_name() for w in descendants(row)
                                  if isinstance(w, Gtk.Image)]
    row.emit("activated")
    assert shown(win).get_tag() == "activity"
    unreadable = activity_row(win, read_of(state=None))
    assert not unreadable.get_activatable()


@pytest.mark.parametrize(("data", "sport", "count"), [(hike, HIKING, 5), (workout, TRAINING, 4)])
def test_the_tiles_share_the_whole_line_of_a_wide_window(data, sport, count):
    """No empty place at the end of the line: five tiles or four, they take its whole width."""
    from gi.repository import GLib, Gtk

    win, _the_list = a_window(data())
    page = open_page(win, read_of(sport=sport))
    win.toplevel.set_default_size(1000, 600)
    win.toplevel.present()
    try:
        flow = next(w for w in descendants(page) if isinstance(w, Gtk.FlowBox))
        tiles = [w for w in descendants(flow) if isinstance(w, Gtk.FlowBoxChild)]
        for _ in range(500):
            if flow.get_width() > 0:
                break
            GLib.MainContext.default().iteration(False)
        bounds = [t.compute_bounds(flow)[1] for t in tiles]
        assert len(tiles) == count and len({round(b.get_y()) for b in bounds}) == 1
        assert bounds[-1].get_x() + bounds[-1].get_width() >= flow.get_width() - 1
    finally:
        win.toplevel.destroy()


# --- the chart -------------------------------------------------------------------------------

WALKING = 11
# timestamp, position, enhanced altitude, heart rate, distance, enhanced speed
WALK_RECORD = FitDefinition(1, 20, ((253, 0x86), (0, 0x85), (1, 0x85), (78, 0x86), (3, 0x02),
                                    (5, 0x86), (73, 0x86)))


def walk() -> bytes:
    """Two minutes of a walk, a record each second: heart rate from 100 to 119 bpm and again,
    the elevation up from 100 m by 0.1 m a second, 1.2 m/s."""
    records = [FitData(1, (START + s, 624_000_000 + s * 10, 250_000_000,
                           round((100 + s * 0.1 + 500) * 5), 100 + s % 20, round(s * 120),
                           1200)) for s in range(120)]
    return fit_file(WALK_RECORD, *records, SESSION,
                    FitData(2, (START, WALKING, 0, 120_000, 119_000, 14_280, 30, None, 1200, 110,
                                119, 12)))


def heart_only() -> bytes:
    return fit_file(HEART, *(FitData(1, (START + s, 100 + s)) for s in range(30)), SESSION,
                    FitData(2, (START, TRAINING, 0, 30_000, 30_000, 0, 5, None, None, 115, 129,
                                None)))


def chart_of(page):
    from openexpress.gui.activity_chart import ActivityChart

    found = [w for w in descendants(page) if isinstance(w, ActivityChart)]
    return found[0] if found else None


def switch_of(page) -> list:
    from gi.repository import Gtk

    return [w for w in descendants(page) if isinstance(w, Gtk.ToggleButton)]


def test_the_chart_of_a_walk_switches_between_what_it_has():
    """Heart rate first; the switch shows the elevation and the pace in its place."""
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    buttons = switch_of(page)
    assert [b.get_label() for b in buttons] == ["Heart rate", "Elevation", "Pace"]
    assert [b.get_active() for b in buttons] == [True, False, False]
    chart = chart_of(page)
    assert chart.area.series.kind == "heart rate" and chart.value_label.get_label() == ""
    buttons[1].set_active(True)
    assert chart.area.series.kind == "elevation"
    assert [b.get_active() for b in buttons] == [False, True, False]


def test_one_series_has_its_name_in_place_of_the_switch():
    win, _the_list = a_window(heart_only())
    page = open_page(win, read_of(sport=TRAINING, positions=False))
    assert switch_of(page) == [] and chart_of(page).area.series.kind == "heart rate"
    assert "Heart rate" in labels(chart_of(page))


def test_without_heart_rate_elevation_or_speed_no_chart_but_a_line():
    win, _the_list = a_window(workout())  # its records: heart rate only…
    page = open_page(win, read_of(sport=TRAINING, positions=False))
    assert chart_of(page) is not None
    positions = fit_file(PLACED, *(FitData(1, (START + s, 624_000_000 + s, 250_000_000, None))
                                   for s in range(3)),
                         SESSION, FitData(2, (START, HIKING, 0, 14_400_000, 13_800_000, 1_240_000,
                                              900, None, 1000, None, None, 740)))
    win, _the_list = a_window(positions)  # …positions only: nothing to chart
    page = open_page(win, read_of())
    assert chart_of(page) is None
    assert "No heart rate, elevation or speed in this activity" in labels(page)


def controllers(widget, kind):
    model = widget.observe_controllers()
    return next(c for c in (model.get_item(i) for i in range(model.get_n_items()))
                if isinstance(c, kind))


def cursor_time(text: str) -> int:
    minutes, seconds = text.split(" ", 1)[0].split(":")
    return int(minutes) * 60 + int(seconds)


def test_the_cursor_by_click_drag_and_the_arrow_keys():
    """A click puts it where it is, a drag moves it, the arrows and Home and End too; another
    series keeps its place."""
    from gi.repository import Gdk, GLib, Gtk

    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    win.toplevel.set_default_size(1000, 700)
    win.toplevel.present()
    try:
        chart = chart_of(page)
        area = chart.area
        for _ in range(500):
            if area.get_width() > 0:
                break
            GLib.MainContext.default().iteration(False)
        drag = controllers(area, Gtk.GestureDrag)
        keys = controllers(area, Gtk.EventControllerKey)
        said = chart.value_label.get_label

        drag.emit("drag-begin", area.get_width() / 2, 100)
        middle = cursor_time(said())
        assert 40 <= middle <= 80 and said().endswith(" bpm")
        for _ in range(50):
            GLib.MainContext.default().iteration(False)
        value = chart.value_label.compute_bounds(chart)[1]
        switch = switch_of(page)[0].compute_bounds(chart)[1]
        assert value.get_y() < switch.get_y() + switch.get_height()  # beside it, wide
        drag.emit("drag-update", area.get_width() / 4, 0)
        assert cursor_time(said()) > middle + 15
        before = cursor_time(said())
        assert keys.emit("key-pressed", Gdk.KEY_Left, 0, Gdk.ModifierType(0))
        assert cursor_time(said()) < before
        keys.emit("key-pressed", Gdk.KEY_Home, 0, Gdk.ModifierType(0))
        assert said() == "0:00 · 100 bpm"
        keys.emit("key-pressed", Gdk.KEY_End, 0, Gdk.ModifierType(0))
        assert said() == "1:59 · 119 bpm"
        switch_of(page)[1].set_active(True)
        assert said() == "1:59 · 112 m"
    finally:
        win.toplevel.destroy()


def test_the_chart_draws_each_series_with_its_cursor():
    """Each series its line in the accent colour; the cursor adds to the picture."""
    import cairo

    from openexpress.gui import activity_chart, activity_colours

    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    chart = chart_of(page)

    def drawn(cursor) -> bytes:
        chart.area.cursor = cursor
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 600, 200)
        activity_chart._draw(chart.area, cairo.Context(surface), 600, 200)
        surface.flush()
        return bytes(surface.get_data())

    accent = activity_colours.accent_colour()
    line = [c * 255 for c in (accent.blue, accent.green, accent.red)]  # as cairo keeps a pixel
    for button in switch_of(page):
        button.set_active(True)
        plain, with_cursor = drawn(None), drawn(60)
        of_the_line = sum(1 for i in range(0, len(plain), 4) if plain[i + 3] == 255
                          and all(abs(plain[i + k] - line[k]) <= 1 for k in range(3)))
        assert of_the_line > 100, button.get_label()
        assert with_cursor != plain, button.get_label()


def closed_and_collected(win) -> None:
    """Back to the list, the stand-in letting go of what the window does not keep, and the
    garbage collected."""
    import gc

    from gi.repository import GLib

    win.navigation.pop()
    win.run_task.calls.clear()  # the stand-in keeps every task; the window keeps none
    win._action_buttons.clear()  # what the window drops as its pages are drawn again
    for _ in range(3):
        gc.collect()
        while GLib.main_context_default().iteration(False):
            pass


def test_the_page_and_its_chart_are_released_once_closed():
    """No handler of the chart holds its widget or a sibling: back to the list, they go."""
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    chart = chart_of(page)
    switch_of(page)[1].set_active(True)
    chart.area.place(30)
    gone = []
    chart.area.weak_ref(lambda: gone.append("chart"))
    page.weak_ref(lambda: gone.append("page"))
    del chart, page
    closed_and_collected(win)
    assert sorted(gone) == ["chart", "page"]


# --- the map ---------------------------------------------------------------------------------

def note_shown(page) -> bool:
    """Whether the map's note says what the tile server would learn."""
    return any("server then learns the area of each activity you open" in text
               for text in labels(page))


def map_of(page):
    from openexpress.gui.activity_map import ActivityMap

    found = [w for w in descendants(page) if isinstance(w, ActivityMap)]
    return found[0] if found else None


def tile_layers(widget) -> list:
    from gi.repository import Shumate

    from openexpress.gui.activity_map import TileLayer

    return [w for w in descendants(widget) if isinstance(w, (TileLayer, Shumate.MapLayer))]


def show_the_map_button(page):
    from gi.repository import Gtk

    found = [w for w in descendants(page) if isinstance(w, Gtk.Button)
             and w.get_label() == "Show the Map" and seen(w, page)]
    return found[0] if found else None


@pytest.fixture
def no_tile_server(monkeypatch):
    """Tiles asked are answered at once, never by a server: what was asked in `asked`."""
    from openexpress.gui import activity_map

    asked = []

    def fetch(url, etag):
        asked.append(url)
        return activity_map.Fetched(404, None, None, 0)

    monkeypatch.setattr(activity_map, "_fetch", fetch)
    return asked


def test_before_the_yes_the_track_is_on_a_plain_background_with_the_note(no_tile_server):
    """No tile layer, so nothing is asked of OpenStreetMap; the note and its button say what
    turning the tiles on does; no attribution without tiles."""
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    shown = map_of(page)
    assert shown is not None and tile_layers(shown) == [] and shown.tiles is None
    assert note_shown(page) and show_the_map_button(page) is not None
    assert not shown.attribution.get_visible()
    assert no_tile_server == []


def test_show_the_map_turns_the_tiles_on_for_the_program(no_tile_server):
    """Once for the program: the next activity has its tiles at once."""
    from openexpress.gui.activity_map import COPYRIGHT_URL
    from openexpress.gui.gui_settings import load_gui_settings

    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    show_the_map_button(page).emit("clicked")
    shown = map_of(page)
    assert load_gui_settings().get("map_tiles") is True
    assert len(tile_layers(shown)) == 1 and not note_shown(page)
    assert shown.attribution.get_visible()
    assert COPYRIGHT_URL in shown.attribution.get_label()
    assert "© OpenStreetMap contributors" in shown.attribution.get_label()
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    assert len(tile_layers(map_of(page))) == 1 and show_the_map_button(page) is None


def test_the_yes_is_taken_back_in_preferences(no_tile_server):
    """The switch in Preferences: off, the map open now loses its tiles and the note is back."""
    from gi.repository import Adw

    from openexpress.gui.activity_map import set_tiles
    from openexpress.gui.gui_settings import load_gui_settings
    from openexpress.gui.preferences import maps_group

    set_tiles(True)
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    group = maps_group(win)
    (row,) = [w for w in descendants(group) if isinstance(w, Adw.SwitchRow)]
    assert row.get_title() == "Streets and Terrain From OpenStreetMap" and row.get_active()
    assert row.get_subtitle() == ("The OpenStreetMap server learns the area of each activity "
                                  "you open")
    row.set_active(False)
    assert load_gui_settings().get("map_tiles") is False
    assert tile_layers(map_of(page)) == [] and note_shown(page)


def test_an_activity_without_positions_has_no_map():
    win, _the_list = a_window(heart_only())
    page = open_page(win, read_of(sport=TRAINING, positions=False))
    assert map_of(page) is None
    assert not [t for t in labels(page) if "map" in t.lower()]


def test_without_libshumate_a_line_says_what_is_missing(monkeypatch):
    from openexpress.gui import activity_map

    monkeypatch.setattr(activity_map, "Shumate", None)
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    assert map_of(page) is None
    assert "The map needs the libshumate library, which is not installed" in labels(page)


def test_a_libshumate_too_old_for_tiles_keeps_the_plain_track(monkeypatch, no_tile_server):
    """It cannot ask tiles as openExpress: no button, no tiles even with the yes, a line."""
    from openexpress.gui import activity_map

    monkeypatch.setattr(activity_map, "can_ask", lambda: False)
    activity_map.set_tiles(True)
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    shown = map_of(page)
    assert shown is not None and tile_layers(shown) == [] and show_the_map_button(page) is None
    assert ("The map needs a newer libshumate library to show streets and terrain"
            in labels(page))


def test_no_map_makes_libshumate_s_own_cache(tmp_path):
    """Neither with the tiles nor without them, nor with an older libshumate: its own cache
    (~/.cache/shumate, made as soon as one of its downloaders is) is never made. In a process
    of its own: GLib keeps the cache folder it found first, maybe before the tests' own."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import gi\n"
        "gi.require_version('Gtk', '4.0')\n"
        "from gi.repository import Gtk\n"
        "Gtk.init()\n"
        "from openexpress.gui import activity_map\n"
        "track = activity_map.Track((0, 1), ((51.5, -0.16), (51.501, -0.161)))\n"
        "for older in (False, True):\n"
        "    if older:\n"
        "        activity_map.can_ask = lambda: False\n"
        "    for tiles in (False, True):\n"
        "        activity_map.set_tiles(tiles)\n"
        "        activity_map.ActivityMap(track)\n")
    env = {**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "XDG_CONFIG_HOME": str(tmp_path / "config")}
    subprocess.run([sys.executable, "-c", code], env=env, check=True, timeout=60,
                   cwd=Path(__file__).resolve().parent.parent)
    assert not (tmp_path / "cache" / "shumate").exists()


class Monitor:
    """A stand-in for Gio.NetworkMonitor: `available`, and its change told to the map."""

    def __init__(self, available):
        self.available, self.handlers = available, {}

    def get_network_available(self):
        return self.available

    def connect(self, name, handler):
        self.handlers[len(self.handlers) + 1] = handler
        return len(self.handlers)

    def disconnect(self, handler_id):
        del self.handlers[handler_id]

    def change(self, available):
        self.available = available
        for handler in list(self.handlers.values()):
            handler(self, available)


def present(win):
    from gi.repository import GLib

    win.toplevel.set_default_size(1000, 800)
    win.toplevel.present()
    for _ in range(200):
        GLib.MainContext.default().iteration(False)


def test_without_internet_the_map_says_only_tiles_downloaded_before_are_shown(
        monkeypatch, no_tile_server):
    from openexpress.gui import activity_map

    monitor = Monitor(False)
    monkeypatch.setattr(activity_map, "_network_monitor", lambda: monitor)
    activity_map.set_tiles(True)
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    present(win)
    try:
        offline = ("No internet connection: only the parts of the map downloaded before are "
                   "shown")
        assert offline in labels(page)
        monitor.change(True)
        assert offline not in labels(page)
    finally:
        win.toplevel.destroy()
    assert monitor.handlers == {}  # let go once the map is gone from the screen


def test_the_chart_s_cursor_is_marked_on_the_map():
    """The marker at the record of the cursor; hidden until the cursor is placed."""
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    shown = map_of(page)
    assert not shown.marker.get_visible()
    chart_of(page).area.place(60)
    assert shown.marker.get_visible()
    point = shown.track.points[shown.track.times.index(60)]
    assert (shown.marker.get_latitude(), shown.marker.get_longitude()) == pytest.approx(point)


def test_the_map_has_a_name_for_a_screen_reader_and_is_released_once_closed(no_tile_server,
                                                                           monkeypatch):
    from gi.repository import Gtk

    from openexpress.gui import activity_map

    # GTK gives no way to read an accessible property back: the call that sets it is watched.
    told = []
    real = activity_map.Shumate.Map.update_property

    def update(widget, properties, values):
        told.append(dict(zip(properties, values, strict=True)))
        return real(widget, properties, values)

    monkeypatch.setattr(activity_map.Shumate.Map, "update_property", update)
    activity_map.set_tiles(True)
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    shown = map_of(page)
    assert {Gtk.AccessibleProperty.LABEL: "Map of the track"} in told
    gone = []
    shown.weak_ref(lambda: gone.append("map"))
    del shown, page
    closed_and_collected(win)
    assert gone == ["map"]


# --- the look of the map ---------------------------------------------------------------------


def a_map(track=None):
    """The map of a track (by default a straight one, 1.1 km from its start to its finish) in
    a window on the screen."""
    from gi.repository import GLib, Gtk

    from openexpress.gui import activity_map

    track = track or activity_map.Track((0, 600), ((51.5, -0.16), (51.51, -0.16)))
    shown = activity_map.ActivityMap(track)
    toplevel = Gtk.Window(child=shown, default_width=800)
    toplevel.present()
    for _ in range(100):
        GLib.MainContext.default().iteration(False)
    return shown, toplevel


def rgba(text):
    from gi.repository import Gdk

    color = Gdk.RGBA()
    color.parse(text)
    return color


def test_the_track_is_thick_in_its_own_colour_with_a_dark_edge():
    """Orange-red, apart from the water, the green and the streets of the tiles, whatever the
    accent colour of the desktop: 4 pixels of colour and 1.5 of a dark edge on each side."""
    shown, toplevel = a_map()
    try:
        from openexpress.gui import activity_map as am

        path = shown.path
        assert path.track_colour.equal(rgba(am.TRACK_COLOUR)) and path.edge.equal(
            rgba(am.TRACK_EDGE))
        assert (path.width, path.edge_width) == (am.TRACK_WIDTH, am.EDGE_WIDTH)
    finally:
        toplevel.destroy()


def layer_of(marker):
    return marker.get_parent()


def order_of(widget) -> int:
    parent, i = widget.get_parent(), 0
    child = parent.get_first_child()
    while child is not widget:
        child, i = child.get_next_sibling(), i + 1
    return i


def ends_of(shown) -> list:
    """The markers of the start and the finish of a map: all its markers but its cursor."""
    from openexpress.gui.activity_map import Shumate

    return [w for w in descendants(shown.map)
            if isinstance(w, Shumate.Marker) and w is not shown.marker]


def wheel_of(shown):
    """The map's own controller of the mouse wheel, which goes before libshumate's."""
    from gi.repository import Gtk

    controllers = shown.map.observe_controllers()
    return next(c for c in (controllers.get_item(i) for i in range(controllers.get_n_items()))
                if isinstance(c, Gtk.EventControllerScroll)
                and c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE)


def row_texts(row) -> list[str]:
    """The texts of a row of a section, in their order."""
    from gi.repository import Gtk

    return [w.get_label() for w in descendants(row) if isinstance(w, Gtk.Label)]


def bar_of(row, kind):
    return next((w for w in descendants(row) if isinstance(w, kind)), None)


def test_the_start_and_the_finish_are_marked_and_the_cursor_above_them():
    shown, toplevel = a_map()
    try:
        start, finish = sorted(ends_of(shown), key=lambda m: m.get_latitude())
        assert (start.get_latitude(), start.get_longitude()) == pytest.approx((51.5, -0.16))
        assert (finish.get_latitude(), finish.get_longitude()) == pytest.approx((51.51, -0.16))
        assert start.get_visible() and finish.get_visible()
        from openexpress.gui.activity_map import CURSOR_SIZE, END_SIZE

        assert shown.marker.get_child().get_content_width() == CURSOR_SIZE
        assert [m.get_child().get_content_width() for m in (start, finish)] == [END_SIZE] * 2
        assert END_SIZE < CURSOR_SIZE  # the cursor's is larger
        assert order_of(layer_of(shown.marker)) > order_of(layer_of(start)) > order_of(shown.path)
    finally:
        toplevel.destroy()


def test_a_loop_has_one_marker_for_its_start_and_finish():
    """The walk of the page: its finish a few metres from its start."""
    win, _the_list = a_window(walk())
    page = open_page(win, read_of(sport=WALKING))
    shown = map_of(page)
    from openexpress.gui.activity_map import BOTH_SIZE

    (marker,) = ends_of(shown)
    assert marker.get_child().get_content_width() == BOTH_SIZE
    assert (marker.get_latitude(), marker.get_longitude()) == pytest.approx(shown.track.points[0])


def plain_png(rgba_value=0xFFFFFFFF) -> bytes:
    """A made-up tile of one colour (by default white)."""
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 256, 256)
    pixbuf.fill(rgba_value)
    _ok, data = pixbuf.save_to_bufferv("png", [], [])
    return bytes(data)


def red_of(widget, scale):
    """The widget drawn on black as on a screen of this scale (the desktop's fractional
    scale): the red of each of its pixels, by rows, away from its outer edge."""
    from gi.repository import Gdk, Graphene, Gtk

    width, height = widget.get_width(), widget.get_height()
    snapshot = Gtk.Snapshot()
    snapshot.scale(scale, scale)
    snapshot.append_color(rgba("black"), Graphene.Rect().init(0, 0, width, height))
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, width, height)
    w, h = int(width * scale), int(height * scale)
    texture = widget.get_native().get_renderer().render_texture(
        snapshot.to_node(), Graphene.Rect().init(0, 0, w, h))
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    data = data.get_data()
    return [data[y * stride + 8:y * stride + (w - 2) * 4:4] for y in range(2, h - 2)]


def only_tiles(shown) -> None:
    """The track and every marker hidden: only the tiles drawn."""
    from gi.repository import Shumate

    from openexpress.gui.activity_map import TrackLayer

    for layer in descendants(shown.map):
        if isinstance(layer, (TrackLayer, Shumate.PathLayer, Shumate.MarkerLayer)):
            layer.set_visible(False)


def until(condition, seconds=5):
    import time

    from gi.repository import GLib

    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        GLib.MainContext.default().iteration(False)


def turn(seconds: float) -> None:
    """The main loop turned for this long (frames drawn, timers run)."""
    until(lambda: False, seconds)


@pytest.mark.parametrize("scale", [1.25, 1.5])
def test_the_tiles_meet_without_a_seam_at_a_fractional_scale(monkeypatch, scale):
    """White tiles on a black background, the track and its markers hidden: where two tiles
    meet between two pixels of the screen, no pixel darker than the tiles (the background
    showing through)."""
    from openexpress.gui import activity_map

    png = plain_png()
    monkeypatch.setattr(activity_map, "_fetch",
                        lambda url, etag: activity_map.Fetched(200, png, '"t"', 3600))
    activity_map.set_tiles(True)
    shown, toplevel = a_map()
    try:
        only_tiles(shown)
        # until the tiles cover the view
        until(lambda: sorted(r for row in red_of(shown.map, 1) for r in row)[100] == 255)
        darkest = min(min(row) for row in red_of(shown.map, scale))
        assert darkest == 255
    finally:
        toplevel.destroy()


def pending(monkeypatch):
    """Tiles asked of the server never answered: (url, cancellable, done) of each."""
    from openexpress.gui import activity_map

    asked = []
    monkeypatch.setattr(activity_map, "_download_later",
                        lambda url, etag, cancellable, done: asked.append((url, cancellable,
                                                                           done)))
    return asked


def test_only_the_tiles_in_view_are_asked_and_those_gone_from_it_let_go(monkeypatch):
    from openexpress.gui import activity_map

    asked = pending(monkeypatch)
    activity_map.set_tiles(True)
    shown, toplevel = a_map()
    try:
        until(lambda: asked, 2)
        turn(0.3)
        width, height = shown.map.get_width(), shown.map.get_height()
        viewport = shown.map.get_viewport()
        view = {activity_map.TILE_URL.format(z=z, x=x, y=y) for z, x, y in (
            p.key for p in activity_map.tile_places(viewport.get_zoom_level(),
                                                    viewport.get_latitude(),
                                                    viewport.get_longitude(), width, height))}
        assert {url for url, _c, _d in asked} == view
        shown.map.center_on(-33.86, 151.21)  # far away
        until(lambda: len(asked) > len(view), 2)
        before = [c for url, c, _d in asked if url in view]
        assert before and all(c.is_cancelled() for c in before)
    finally:
        toplevel.destroy()


def test_a_tile_that_failed_is_asked_again_once_the_network_is_back(monkeypatch):
    from openexpress.gui import activity_map

    monitor = Monitor(False)
    monkeypatch.setattr(activity_map, "_network_monitor", lambda: monitor)
    asked = pending(monkeypatch)
    activity_map.set_tiles(True)
    shown, toplevel = a_map()
    try:
        until(lambda: asked, 2)
        first = [url for url, _c, _d in asked]
        for _url, _c, done in list(asked):
            done(activity_map.Fetched(None, None, None, 0))  # not reached
        turn(0.3)
        assert len(asked) == len(first)  # not asked again while offline
        monitor.change(True)
        until(lambda: len(asked) >= 2 * len(first), 2)
        assert sorted(url for url, _c, _d in asked[len(first):]) == sorted(first)
    finally:
        toplevel.destroy()


def test_while_a_tile_loads_the_one_of_the_zoom_below_is_shown(monkeypatch):
    """The tiles of the view never answered; those of the zoom below already drawn once."""
    from gi.repository import Gdk, GLib

    from openexpress.gui import activity_map

    pending(monkeypatch)
    activity_map.set_tiles(True)
    shown, toplevel = a_map()
    try:
        only_tiles(shown)
        red = Gdk.Texture.new_from_bytes(GLib.Bytes.new(plain_png(0xFF0000FF)))
        viewport = shown.map.get_viewport()
        for place in activity_map.tile_places(viewport.get_zoom_level(),
                                              viewport.get_latitude(), viewport.get_longitude(),
                                              shown.map.get_width(), shown.map.get_height()):
            zoom, x, y = place.key
            shown.tiles.textures[(zoom - 1, x // 2, y // 2)] = red
        shown.tiles.queue_draw()

        def darkest():
            return min(min(row) for row in red_of(shown.map, 1))

        until(lambda: darkest() == 255, 2)  # drawn anew once the window's loop turns
        assert darkest() == 255
    finally:
        toplevel.destroy()


def white_tiles(monkeypatch) -> None:
    from openexpress.gui import activity_map

    png = plain_png()
    monkeypatch.setattr(activity_map, "_fetch",
                        lambda url, etag: activity_map.Fetched(200, png, '"t"', 3600))


def no_more_tiles(monkeypatch) -> None:
    """From now on no tile comes, neither from the server nor from the cache."""
    from openexpress.gui import activity_map

    pending(monkeypatch)
    monkeypatch.setattr(activity_map.TileSource, "do_start_request", never_answered)


def uncovered(widget, share=1.0) -> int:
    """The darkest red of the middle `share` of the widget drawn on black (255: tiles
    everywhere there)."""
    rows = red_of(widget, 1)
    height, width = len(rows), len(rows[0])
    top, left = round(height * (1 - share) / 2), round(width * (1 - share) / 2)
    return min(min(row[left:width - left]) for row in rows[top:height - top])


@pytest.mark.parametrize("steps", [
    (0.2, 0.4, 0.6, 0.8, 1.0, 1.2),  # in, a notch of the mouse wheel at a time
    (-0.2, -0.4, -0.8, -1.0, -1.2),  # out
    (1.0,), (-1.0,), (-2.0,)])
def test_while_the_zoom_changes_the_map_is_never_empty(monkeypatch, steps):
    """The tiles of one zoom drawn, then no tile comes any more: at each step of the zoom,
    everywhere the map showed before has a tile (those drawn, scaled: of the zoom below or
    above), never the background. Zoomed out, the view's new edges are not checked: no tile
    there was drawn yet."""
    from openexpress.gui import activity_map

    white_tiles(monkeypatch)
    activity_map.set_tiles(True)
    shown, toplevel = a_map()
    try:
        only_tiles(shown)
        viewport = shown.map.get_viewport()
        start = viewport.get_zoom_level()  # the zoom the page opens with, alone drawn
        until(lambda: uncovered(shown.map) == 255)
        assert uncovered(shown.map) == 255  # the first zoom drawn whole
        no_more_tiles(monkeypatch)
        for step in steps:
            viewport.set_zoom_level(start + step)
            turn(0.05)  # a few frames
            shown_before = min(1.0, 2 ** step)  # the middle of the view, zoomed out
            assert uncovered(shown.map, shown_before) == 255, f"empty at {step:+} zoom"
    finally:
        toplevel.destroy()


def test_the_tiles_of_the_zoom_before_are_let_go_once_the_new_ones_are_drawn(monkeypatch):
    from openexpress.gui import activity_map

    white_tiles(monkeypatch)
    activity_map.set_tiles(True)
    shown, toplevel = a_map()
    try:
        viewport = shown.map.get_viewport()
        zoom = round(viewport.get_zoom_level())
        until(lambda: uncovered(shown.map) == 255)
        viewport.set_zoom_level(zoom + 1)
        until(lambda: uncovered(shown.map) == 255 and not shown.tiles.asked)
        turn(0.1)
        assert {key[0] for key in shown.tiles.textures} == {zoom + 1}
    finally:
        toplevel.destroy()


def never_answered(self, x, y, zoom, cancellable):
    """A tile source whose tiles never come (not even from its cache)."""
    from gi.repository import Shumate

    return Shumate.DataSourceRequest.new(x, y, zoom)


# --- the laps --------------------------------------------------------------------------------


def lapped_walk(device_laps=True, metres=3250):
    """A walk of `metres` around a loop, a record each 2 s at 1.25 m/s; the device's laps of
    1 km (the last one shorter), or one lap of the whole of it."""
    import math

    from openexpress import fit

    begin = START + FIT_EPOCH
    samples, distance, t = [], 0.0, 0
    while distance < metres:
        distance = min(metres, distance + 2.5)
        angle = 2 * math.pi * distance / metres
        samples.append(fit.Sample(begin + t, distance=distance, speed=1.25,
                                  heart_rate=100 + int(distance // 1000) * 5,
                                  altitude=100 + 5 * math.sin(angle),
                                  latitude=51.5 + 0.004 * math.sin(angle),
                                  longitude=-0.16 + 0.008 * math.cos(angle)))
        t += 2
    laps, first = [], 0
    for i, sample in enumerate(samples):
        if sample.distance >= (len(laps) + 1) * 1000 or i == len(samples) - 1:
            time = sample.time - samples[first].time
            laps.append(fit.Lap(samples[first].time, float(time), float(time),
                                sample.distance - len(laps) * 1000, 1.25, 100 + len(laps) * 5,
                                110 + len(laps) * 5))
            first = i
    if not device_laps:
        laps = [fit.Lap(begin, float(t), float(t), metres)]
    return fit.ActivityData(fit.FileState.WHOLE, start=begin, sport=WALKING,
                            timer_time=float(t), elapsed_time=float(t), distance=metres,
                            ascent=10, avg_speed=1.25, avg_heart_rate=105, max_heart_rate=120,
                            samples=tuple(samples), laps=tuple(laps))


def page_of(monkeypatch, data, sport=WALKING):
    """The page of an activity whose file holds `data` (fit.ActivityData)."""
    from openexpress.gui import activity_page

    monkeypatch.setattr(activity_page, "_data", lambda found, activity: data)
    win, _the_list = a_window(b"")
    return win, open_page(win, read_of(sport=sport))


def lap_list(page):
    from openexpress.gui.activity_laps import LapList

    found = [w for w in descendants(page) if isinstance(w, LapList)]
    return found[0] if found else None


def lap_rows(page):
    from gi.repository import Adw

    return [w for w in descendants(lap_list(page)) if isinstance(w, Adw.ActionRow)]


def test_the_laps_are_rows_under_the_chart(monkeypatch):
    """Each lap of the device a row, its line as the words say it; under the chart."""
    from openexpress.gui.words import lap_caption

    data = lapped_walk()
    _win, page = page_of(monkeypatch, data)
    laps = lap_list(page)
    assert laps is not None and "Laps" in labels(page)
    rows = lap_rows(page)
    assert [r.get_title() for r in rows] == ["Lap 1", "Lap 2", "Lap 3", "Lap 4"]
    assert [r.get_subtitle() for r in rows] == [lap_caption(lap, WALKING) for lap in data.laps]
    assert order_of(laps) > order_of(chart_of(page))


def chosen_rows(page) -> list[int]:
    return [i for i, row in enumerate(lap_rows(page)) if row.has_css_class("oe-lap-chosen")]


def test_a_lap_chosen_is_shown_on_the_chart_and_the_map_and_chosen_again_it_is_not(monkeypatch):
    from openexpress.gui.activity_laps import lap_span

    data = lapped_walk()
    _win, page = page_of(monkeypatch, data)
    chart, shown = chart_of(page), map_of(page)
    origin = data.samples[0].time
    rows = lap_rows(page)
    assert chart.area.lap is None and shown.path.chosen is None and chosen_rows(page) == []
    rows[1].emit("activated")
    span = lap_span(data.laps[1], origin)
    assert chart.area.lap == span and shown.path.chosen == span and chosen_rows(page) == [1]
    rows[2].emit("activated")  # another: moved
    assert chart.area.lap == lap_span(data.laps[2], origin) and chosen_rows(page) == [2]
    rows[2].emit("activated")  # again: taken off
    assert chart.area.lap is None and shown.path.chosen is None and chosen_rows(page) == []


def test_a_lap_is_chosen_with_the_keyboard(monkeypatch):
    """A row takes the focus; Enter or Space activates it as a click."""
    _win, page = page_of(monkeypatch, lapped_walk())
    row = lap_rows(page)[0]
    assert row.get_activatable() and row.get_focusable()
    row.activate()
    assert chosen_rows(page) == [0]


def test_kilometres_in_place_of_laps_where_the_device_kept_none(monkeypatch):
    _win, page = page_of(monkeypatch, lapped_walk(device_laps=False))
    assert "Kilometres" in labels(page) and "Laps" not in labels(page)
    assert [r.get_title() for r in lap_rows(page)] == [
        "Kilometre 1", "Kilometre 2", "Kilometre 3", "Kilometre 4"]


@pytest.mark.parametrize("data", ["short", "workout"])
def test_no_laps_for_a_short_activity_without_laps_or_a_workout(monkeypatch, data):
    _win, page = page_of(monkeypatch, lapped_walk(device_laps=False, metres=1500)
                         if data == "short" else fit_of(heart_only()))
    assert lap_list(page) is None
    assert "Laps" not in labels(page) and "Kilometres" not in labels(page)


def fit_of(data: bytes):
    from openexpress import fit

    return fit.activity_data(data)


def colours_of(widget, background="black"):
    """The widget drawn at scale 1 on a background: (red, green, blue) of each pixel, by
    rows."""
    from gi.repository import Gdk, Graphene, Gtk

    width, height = widget.get_width(), widget.get_height()
    snapshot = Gtk.Snapshot()
    snapshot.append_color(rgba(background), Graphene.Rect().init(0, 0, width, height))
    # Drawn within its own bounds, unscaled (a window's hold the border it is resized by).
    _ok, bounds = widget.compute_bounds(widget)
    snapshot.translate(Graphene.Point().init(bounds.get_x(), bounds.get_y()))
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, bounds.get_width(), bounds.get_height())
    texture = widget.get_native().get_renderer().render_texture(
        snapshot.to_node(), Graphene.Rect().init(0, 0, width, height))
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    data = data.get_data()
    return [[tuple(data[y * stride + x * 4:y * stride + x * 4 + 3]) for x in range(width)]
            for y in range(height)]


def test_the_chosen_lap_is_a_band_behind_the_chart(monkeypatch):
    """A column of the chart within the lap's time is tinted, one outside is not."""
    from openexpress.gui import activity_chart

    data = lapped_walk()
    win, page = page_of(monkeypatch, data)
    present(win)
    try:
        chart = chart_of(page)
        lap_rows(page)[1].emit("activated")
        turn(0.2)
        area = chart.area
        plot = activity_chart._plot(area, area.get_width(), area.get_height())
        start, end = area.lap
        pixels = colours_of(area, "white")
        rows = range(round(plot.top) + 2, round(plot.bottom) - 2)

        def median_red(at):
            x = round(plot.x(at))
            return sorted(pixels[y][x][0] for y in rows)[len(rows) // 2]

        assert median_red((start + end) / 2) < 245
        assert median_red(start / 2) == 255
    finally:
        win.toplevel.destroy()


def test_the_track_is_drawn_where_the_map_places_it_and_a_chosen_lap_stands_out(monkeypatch):
    """The track's colour at the place of a record, as libshumate places it; a lap chosen at
    full strength, the rest of the track half as strong. Its colours off: one colour."""
    from openexpress.gui import activity_colours

    activity_colours.set_colours(False)
    data = lapped_walk()
    win, page = page_of(monkeypatch, data)
    present(win)
    try:
        shown = map_of(page)
        for marker in ends_of(shown):
            marker.set_visible(False)
        viewport = shown.map.get_viewport()

        def red_at(sample):
            x, y = viewport.location_to_widget_coords(shown.map, sample.latitude,
                                                      sample.longitude)
            return colours_of(shown.map)[round(y)][round(x)][0]

        in_first, in_second = data.samples[200], data.samples[600]  # in laps 1 and 2
        until(lambda: red_at(in_first) > 200)
        assert red_at(in_first) == pytest.approx(0xe5, abs=12)
        assert red_at(in_second) == pytest.approx(0xe5, abs=12)
        lap_rows(page)[1].emit("activated")
        until(lambda: 60 < red_at(in_first) < 170)  # drawn anew once the window's loop turns
        assert 60 < red_at(in_first) < 170  # faded
        assert red_at(in_second) == pytest.approx(0xe5, abs=12)
    finally:
        win.toplevel.destroy()


def rgb_of(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def test_the_track_coloured_by_the_chart_s_series_with_its_legend(monkeypatch):
    """By heart rate when the page opens (the chart's first series): the ends of the scale the
    activity's 5th and 95th percentile, said under the map with their unit; another series on
    the chart, the track and the legend follow it."""
    from openexpress.gui import activity_colours

    win, page = page_of(monkeypatch, lapped_walk())  # 100 bpm the first km, 115 the last
    shown = map_of(page)
    legend = shown.legend
    assert legend.get_parent() is shown and order_of(legend) < order_of(shown.note)
    assert legend.get_visible() and legend.switch.get_active()
    assert legend.name.get_label() == "Track colour: heart rate"
    assert (legend.low.get_label(), legend.high.get_label()) == ("100 bpm", "115 bpm")
    colours = shown.path.colours
    assert len(colours) == len(shown.path.times)
    assert colours[0] == activity_colours.SCALE[0] and colours[-1] == activity_colours.SCALE[-1]
    assert shown.path.edge.to_string() == rgba(activity_colours.EDGE).to_string()
    chart_of(page).show("elevation")
    assert legend.name.get_label() == "Track colour: elevation"
    assert (legend.low.get_label(), legend.high.get_label()) == ("95 m", "105 m")
    assert shown.path.colours != colours


def test_the_track_s_colours_turned_off_are_kept_for_the_next_page(monkeypatch):
    """Off: the track in its one colour on its own edge, the legend but its switch hidden; so
    on the next page too, until turned on."""
    from openexpress.gui import activity_colours, activity_map
    from openexpress.gui.gui_settings import load_gui_settings

    _win, page = page_of(monkeypatch, lapped_walk())
    shown = map_of(page)
    shown.legend.switch.set_active(False)
    assert shown.path.colours is None
    assert shown.path.edge.to_string() == rgba(activity_map.TRACK_EDGE).to_string()
    assert not shown.legend.scale.get_visible() and shown.legend.switch.get_visible()
    assert load_gui_settings()[activity_colours.SETTING] is False
    _win, page = page_of(monkeypatch, lapped_walk())
    again = map_of(page)
    assert again.path.colours is None and not again.legend.switch.get_active()
    again.legend.switch.set_active(True)
    assert again.path.colours is not None and again.legend.scale.get_visible()


def test_the_coloured_track_on_the_map_and_a_chosen_lap_over_its_colours(monkeypatch):
    """Each place in the colour of its record; a lap chosen at full strength, the rest of the
    track half as strong in its colours."""
    from openexpress.gui import activity_colours

    data = lapped_walk()
    win, page = page_of(monkeypatch, data)
    present(win)
    try:
        shown = map_of(page)
        for marker in ends_of(shown):
            marker.set_visible(False)
        viewport = shown.map.get_viewport()

        def colour_at(sample):
            x, y = viewport.location_to_widget_coords(shown.map, sample.latitude,
                                                      sample.longitude)
            return colours_of(shown.map)[round(y)][round(x)]

        in_first, in_last = data.samples[200], data.samples[-50]  # 100 and 115 bpm
        low, high = (rgb_of(activity_colours.SCALE[i]) for i in (0, -1))
        until(lambda: colour_at(in_first)[2] > 150)
        assert colour_at(in_first) == pytest.approx(low, abs=12)
        assert colour_at(in_last) == pytest.approx(high, abs=12)
        lap_rows(page)[3].emit("activated")
        until(lambda: 60 < colour_at(in_first)[2] < 170)  # drawn anew once the loop turns
        assert 60 < colour_at(in_first)[2] < 170  # faded, still blue
        assert colour_at(in_last) == pytest.approx(high, abs=12)
    finally:
        win.toplevel.destroy()


def scrolled_of(widget):
    """The scrolled window of the page around the widget (a status page has one too)."""
    from gi.repository import Gtk

    return widget.get_ancestor(Gtk.ScrolledWindow)


@pytest.mark.parametrize("ctrl", [False, True])
def test_the_mouse_wheel_over_the_map_scrolls_the_page_ctrl_with_it_zooms(monkeypatch, ctrl):
    """Without Ctrl the page scrolls and the map says how to zoom it; with Ctrl the map
    (libshumate) takes the wheel and the page stays."""
    from openexpress.gui import activity_map

    monkeypatch.setattr(activity_map, "_with_ctrl", lambda controller: ctrl)
    win, page = page_of(monkeypatch, lapped_walk())
    present(win)
    try:
        shown = map_of(page)
        page_scroll = scrolled_of(shown).get_vadjustment()
        zoom = shown.map.get_viewport().get_zoom_level()
        assert page_scroll.get_upper() > page_scroll.get_page_size()  # the page scrolls
        taken = wheel_of(shown).emit("scroll", 0.0, 1.0)
        assert taken is not ctrl
        assert (page_scroll.get_value() > 0) is not ctrl
        assert shown.map.get_viewport().get_zoom_level() == zoom
        assert shown.hint.get_visible() is not ctrl
        if not ctrl:
            assert shown.hint.get_label() == "Ctrl + scroll to zoom"
    finally:
        win.toplevel.destroy()


def test_the_hint_of_ctrl_goes_away(monkeypatch):
    from openexpress.gui import activity_map

    monkeypatch.setattr(activity_map, "_with_ctrl", lambda controller: False)
    monkeypatch.setattr(activity_map, "HINT_S", 0.05)
    win, page = page_of(monkeypatch, lapped_walk())
    present(win)
    try:
        shown = map_of(page)
        wheel_of(shown).emit("scroll", 0.0, 1.0)
        assert shown.hint.get_visible()
        until(lambda: not shown.hint.get_visible(), 2)
        assert not shown.hint.get_visible()
    finally:
        win.toplevel.destroy()


def test_the_buttons_of_the_map_zoom_it_by_a_whole_level():
    from gi.repository import Gtk

    shown, toplevel = a_map()
    try:
        viewport = shown.map.get_viewport()
        buttons = [w for w in descendants(shown) if isinstance(w, Gtk.Button)
                   and w.get_tooltip_text() in ("Zoom In", "Zoom Out")]
        zoom_in, zoom_out = sorted(buttons, key=lambda b: b.get_tooltip_text() != "Zoom In")
        viewport.set_zoom_level(14.4)
        zoom = 14
        zoom_in.emit("clicked")
        until(lambda: viewport.get_zoom_level() == pytest.approx(zoom + 1), 2)
        assert viewport.get_zoom_level() == pytest.approx(zoom + 1)  # to the next whole level
        zoom_out.emit("clicked")
        until(lambda: viewport.get_zoom_level() == pytest.approx(zoom), 2)
        assert viewport.get_zoom_level() == pytest.approx(zoom)
    finally:
        toplevel.destroy()


# --- the look --------------------------------------------------------------------------------


def sport_dot(page):
    from gi.repository import Gtk

    found = [w for w in descendants(page) if isinstance(w, Gtk.Widget) and w.has_css_class("oe-sport-dot")]
    return found[0] if found else None


def tile_icons(page):
    from gi.repository import Gtk

    return [w for w in descendants(page) if isinstance(w, Gtk.Image) and w.get_ancestor(Gtk.FlowBoxChild)
            and w.get_ancestor(Gtk.FlowBoxChild).get_child().has_css_class("card")]


def test_a_dot_of_the_sport_s_colour_before_the_title_not_in_a_narrow_window(monkeypatch):
    """A walk: green, from the program's style; in a narrow window not shown (it would cut
    the start under the title)."""
    from gi.repository import Adw

    program_style()
    win, page = page_of(monkeypatch, lapped_walk())
    present(win)
    try:
        dot = sport_dot(page)
        assert dot is not None and dot.has_css_class("oe-sport-foot") and dot.get_mapped()
        assert dot.get_ancestor(Adw.HeaderBar) is not None
        turn(0.1)
        pixels = colours_of(dot)
        middle = pixels[len(pixels) // 2][len(pixels[0]) // 2]
        assert middle == pytest.approx((0x26, 0xa2, 0x69), abs=3)
        win.toplevel.set_default_size(360, 800)
        until(lambda: not dot.get_visible(), 2)
        assert not dot.get_visible()
    finally:
        win.toplevel.destroy()


def tile_icon(name: str) -> str:
    return f"io.github.tomaszbojanowski.OpenExpress-{name}-symbolic"


@pytest.mark.parametrize(("which", "group", "icons"), [
    ("walk", "foot", [tile_icon(n) for n in ("activity-distance", "activity-time",
                                             "activity-speed", "activity-heart-rate",
                                             "activity-elevation")]),
    ("workout", "training", [tile_icon(n) for n in ("activity-time", "activity-heart-rate",
                                                    "activity-heart-rate",
                                                    "activity-calories")])])
def test_the_tiles_have_the_icons_of_their_kind_in_the_sport_s_colour(monkeypatch, which, group,
                                                                      icons):
    """Over each tile's value; for the eye only (the value and its name say it)."""
    from gi.repository import Gtk

    from openexpress.gui.sports import COLOURS

    program_style()
    data = lapped_walk() if which == "walk" else fit_of(heart_only())
    win, page = page_of(monkeypatch, data, sport=data.sport)
    present(win)
    try:
        shown = tile_icons(page)
        assert [icon.get_icon_name() for icon in shown] == icons
        for icon in shown:
            assert icon.has_css_class(f"oe-sport-{group}")
            assert icon.get_color().equal(rgba(COLOURS[group]))
            assert icon.get_accessible_role() == Gtk.AccessibleRole.PRESENTATION
    finally:
        win.toplevel.destroy()


# --- heart rate zones ------------------------------------------------------------------------

ZONE_BOUNDS = (120, 140, 155, 165, 178, 190)  # made-up


def zoned(data, times=(2190, 300, 0, 0, 0, 0)):
    """The activity with heart rate zones as a device keeps them (made-up)."""
    import dataclasses

    from openexpress import fit

    return dataclasses.replace(data, zones=fit.HeartRateZones(
        times=times, bounds=ZONE_BOUNDS, method="threshold", max_heart_rate=190,
        threshold_heart_rate=165))


def zone_section(page):
    from openexpress.gui.activity_zones import ZoneList

    found = [w for w in descendants(page) if isinstance(w, ZoneList)]
    return found[0] if found else None


def test_the_heart_rate_zones_under_the_laps(monkeypatch):
    """Zone 5 on top, then 4 to 1 and below zone 1: each with its bounds, its time and share,
    and a bar of its share in the zone's colour; under the title how the device worked out the
    bounds."""
    from openexpress.gui.activity_zones import ZONE_COLOURS

    _win, page = page_of(monkeypatch, zoned(lapped_walk()))
    shown = zone_section(page)
    assert shown is not None and order_of(shown) > order_of(lap_list(page))
    texts = labels(page)
    assert "Heart Rate Zones" in texts
    assert "Bounds from the device: % of lactate threshold (165 bpm)" in texts
    from openexpress.gui.activity_zones import ZoneBar

    texts = [row_texts(r) for r in shown.rows]
    assert [name for name, _span, _share in texts] == [
        "Zone 5", "Zone 4", "Zone 3", "Zone 2", "Zone 1", "Below Zone 1"]
    assert [span for _name, span, _share in texts] == [
        "above 177 bpm", "165–177 bpm", "155–164 bpm", "140–154 bpm", "120–139 bpm",
        "below 120 bpm"]
    assert texts[4][2] == "5:00 · 12%" and texts[5][2] == "36:30 · 88%"
    bars = [bar_of(r, ZoneBar) for r in shown.rows]
    assert [round(b.share, 2) for b in bars] == [0, 0, 0, 0, 0.12, 0.88]
    assert [b.colour for b in bars] == list(reversed(ZONE_COLOURS))


def test_each_zone_s_colour_reads_on_the_cards_of_both_styles():
    """A contrast of 3:1 and a margin (a twentieth) with the light card and the dark one, as a
    bar needs."""
    from openexpress.gui.activity_zones import ZONE_COLOURS

    cards = {"light": "#ffffff", "dark": "#343437"}  # the dark: white at 8 % over #222226
    weakest = min(contrast(colour, card) for colour in ZONE_COLOURS for card in cards.values())
    assert weakest >= 3.15


def test_no_zones_without_them_in_the_file(monkeypatch):
    _win, page = page_of(monkeypatch, lapped_walk())
    assert zone_section(page) is None and "Heart Rate Zones" not in labels(page)


def with_effect(data, aerobic=1.6, anaerobic=0.0, load=28.4):
    """The activity with its training effect and load as a device keeps them (made-up)."""
    import dataclasses

    return dataclasses.replace(data, aerobic_effect=aerobic, anaerobic_effect=anaerobic,
                               training_load=load)


def effect_section(page):
    from openexpress.gui.activity_effect import EffectList

    found = [w for w in descendants(page) if isinstance(w, EffectList)]
    return found[0] if found else None


def test_the_training_effect_under_the_zones(monkeypatch):
    """Aerobic and anaerobic, each its value with its word and a bar over 0-5 in the accent
    colour; the training load as a whole number, no bar."""
    from openexpress.gui.activity_colours import accent_colour

    _win, page = page_of(monkeypatch, with_effect(zoned(lapped_walk()), aerobic=2.4,
                                                  anaerobic=1.2))
    shown = effect_section(page)
    assert shown is not None and order_of(shown) > order_of(zone_section(page))
    assert "Training Effect" in labels(page)
    from openexpress.gui.activity_effect import EffectBar

    assert [row_texts(r) for r in shown.rows] == [
        ["Aerobic", "2.4\u00a0· Maintaining"], ["Anaerobic", "1.2\u00a0· Minor benefit"],
        ["Training Load", "28"]]
    bars = [bar_of(r, EffectBar) for r in shown.rows]
    assert [round(b.share, 2) for b in bars[:2]] == [0.48, 0.24] and bars[2] is None
    assert bars[0].fill().to_string() == accent_colour().to_string()


def test_a_row_of_the_training_effect_not_in_the_file_is_left_out(monkeypatch):
    _win, page = page_of(monkeypatch, with_effect(lapped_walk(), aerobic=None, load=None))
    assert [row_texts(r)[0] for r in effect_section(page).rows] == ["Anaerobic"]


def test_no_training_effect_without_it_in_the_file(monkeypatch):
    _win, page = page_of(monkeypatch, zoned(lapped_walk()))
    assert effect_section(page) is None and "Training Effect" not in labels(page)


def plateaus():
    """Ten minutes of heart rate: 110 bpm, then 150 bpm (made-up), a record a second."""
    from openexpress import fit

    begin = START + FIT_EPOCH
    samples = tuple(fit.Sample(begin + s, heart_rate=110 if s < 300 else 150) for s in range(600))
    return fit.ActivityData(fit.FileState.WHOLE, start=begin, sport=TRAINING, timer_time=600.0,
                            avg_heart_rate=130, max_heart_rate=150, calories=80,
                            samples=samples)


def test_the_bands_of_the_zones_behind_the_chart_of_heart_rate(monkeypatch):
    """Faint, in the zones' colours, for zones 1 to 5 only: none below zone 1."""
    from openexpress.gui import activity_chart

    win, page = page_of(monkeypatch, zoned(plateaus()), sport=TRAINING)
    present(win)
    try:
        area = chart_of(page).area
        assert area.zones == ZONE_BOUNDS
        turn(0.2)
        plot = activity_chart._plot(area, area.get_width(), area.get_height())
        pixels = colours_of(area, "white")
        x = round(plot.x(150))  # the line at 110 bpm there
        zone1, zone2, below = (pixels[round(plot.y(v))][x] for v in (130, 147, 115))
        assert zone1[2] > zone1[0] + 8  # blue
        assert zone2[1] > zone2[0] + 8  # green
        assert below == (255, 255, 255)
    finally:
        win.toplevel.destroy()


def test_the_bar_of_the_chart_s_switch_has_no_background_under_the_pointer(monkeypatch):
    """Only the buttons have one: beside them, under the pointer, the page as above the bar."""
    from gi.repository import Gtk

    program_style()
    win, page = page_of(monkeypatch, lapped_walk())
    present(win)
    try:
        top = chart_of(page).get_first_child()
        child = top.get_first_child()
        child.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
        turn(0.2)
        last = child.get_child().get_last_child()  # the last of the switch's buttons
        _ok, place = last.compute_bounds(win.toplevel)
        _ok, bar = top.compute_bounds(win.toplevel)
        x = round(place.get_x() + place.get_width() + 40)
        pixels = colours_of(win.toplevel)
        beside = pixels[round(place.get_y() + place.get_height() / 2)][x]
        above = pixels[round(bar.get_y()) - 3][x]
        assert beside == above
    finally:
        win.toplevel.destroy()


# --- the details -----------------------------------------------------------------------------


def detail_section(page):
    from openexpress.gui.activity_details import DetailsList

    found = [w for w in descendants(page) if isinstance(w, DetailsList)]
    return found[0] if found else None


def with_details(data):
    """The activity with details as a device keeps them (made-up): its elapsed time with a
    pause of five minutes, its descent, minimum heart rate and strides."""
    import dataclasses

    return dataclasses.replace(data, elapsed_time=data.timer_time + 300, descent=12,
                               min_heart_rate=86, cycles=1300.0)


def test_the_details_closed_at_the_end_of_the_page(monkeypatch):
    """The last section, under the training effect, closed; when opened its rows in their
    order, each its name and its value. The moving time is worked out from the records: the
    walk's last record is two seconds before the end of its timer time."""
    data = with_details(with_effect(zoned(lapped_walk())))
    _win, page = page_of(monkeypatch, data)
    shown = detail_section(page)
    assert shown is not None and order_of(shown) > order_of(effect_section(page))
    assert shown.get_next_sibling() is None
    assert shown.expander.get_title() == "Details" and not shown.expander.get_expanded()
    assert [(r.get_title(), r.value.get_label()) for r in shown.rows] == [
        ("Elapsed Time", "48:20"), ("Moving Time", "43:18"), ("Total Descent", "12 m"),
        ("Average Moving Pace", "13:19 /km"), ("Steps", "2600"), ("Heart Rate", "86–120 bpm")]
    assert not any(seen(r, page) for r in shown.rows)
    shown.expander.set_expanded(True)
    assert all(seen(r, page) for r in shown.rows)
    assert [r.value.get_label() for r in shown.rows[:2]] == ["48:20", "43:18"]


def test_no_details_without_them_in_the_file(monkeypatch):
    """A workout whose file gives only what its tiles show."""
    _win, page = page_of(monkeypatch, plateaus(), sport=TRAINING)
    assert detail_section(page) is None and "Details" not in labels(page)


@pytest.mark.parametrize("dark", [False, True])
def test_the_cursor_on_the_map_is_the_colour_of_the_chart_s(dark):
    """The marker of the chart's cursor on the map is drawn in the accent colour of the chart's
    cursor and line, in either style."""
    import cairo
    from gi.repository import Adw

    from openexpress.gui import activity_chart, activity_map

    style = Adw.StyleManager.get_default()
    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    try:
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 20, 20)
        activity_map._draw_cursor(None, cairo.Context(surface), 20, 20)
        surface.flush()
        data, stride = surface.get_data(), surface.get_stride()
        blue, green, red = data[10 * stride + 40:10 * stride + 43]
        accent = activity_chart.accent_colour()  # the chart's own
        off = max(abs(drawn - c * 255)
                  for drawn, c in zip((red, green, blue), (accent.red, accent.green, accent.blue)))
        assert off <= 1  # cairo rounds a colour its own way
    finally:
        style.set_color_scheme(Adw.ColorScheme.DEFAULT)


@pytest.mark.parametrize("dark", [False, True])
def test_without_the_accent_colours_a_line_is_in_the_blue_of_the_style(dark, monkeypatch):
    """Before libadwaita 1.6 (no accent colours) the chart's line is in libadwaita's blue for a
    line on the window of the style: lighter in the dark style, and at 3:1 at least from the
    window's background in either."""
    from types import SimpleNamespace

    from gi.repository import Adw

    from openexpress.gui import activity_colours

    style = Adw.StyleManager.get_default()
    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    try:
        monkeypatch.setattr(activity_colours, "Adw", SimpleNamespace(StyleManager=Adw.StyleManager))
        line = activity_colours.accent_colour()
        other = activity_colours.LINE_BLUE[not dark]
    finally:
        style.set_color_scheme(Adw.ColorScheme.DEFAULT)
    ours = "#" + "".join(f"{round(c * 255):02x}" for c in (line.red, line.green, line.blue))
    window = "#222226" if dark else "#fafafb"  # the window's background
    lighter_in_the_dark = (luminance(ours) > luminance(other)) == dark
    least = contrast(ours, window)
    assert lighter_in_the_dark
    assert least >= 3
