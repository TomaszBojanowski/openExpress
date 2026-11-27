"""Back from the device view, in the real window: from any page of its side panel (Updates,
Maps, Apps, Music), chosen on the device page or in the side panel, one click on the back arrow
leads to the start page ("Devices"). Folded (a narrow window) the page goes back to the side
panel first, then to the start page; a page chosen stays in front when the window folds. The
device is made up; no server is asked."""
import pytest
from fakes import descendants, found_now

pytestmark = pytest.mark.needs_display

PAGES = ["updates", "maps", "apps", "music"]
MUSIC = ("<DataType><Name>MEDIA_TRACK</Name><File><Location><Path>Music</Path>"
         "<FileExtension>MP3</FileExtension></Location>"
         "<TransferDirection>InputOutput</TransferDirection></File></DataType>")


def _turn(times=200):
    from gi.repository import GLib

    for _ in range(times):
        GLib.MainContext.default().iteration(False)


def _stack(win):
    return [page.get_tag() for page in win.navigation.get_navigation_stack()]


def _back(win):
    """A click on the back arrow in view (the side panel's; folded, the page's own first)."""
    from gi.repository import Gtk

    arrows = [w for w in descendants(win) if isinstance(w, Gtk.Button) and w.get_mapped()
              and w.get_action_name() == "navigation.pop"]
    assert arrows, "no back arrow in view"
    arrows[0].emit("clicked")
    _turn()


@pytest.fixture
def device_window(monkeypatch, device_root):
    """The real window with the made-up device open in its view; every server answers 404."""
    import requests

    from openexpress import connect, netstatus, service
    from openexpress.device.storage import LocalStorage
    from openexpress.gui import app

    class Http:
        def request(self, method, url, **kw):
            answer = requests.Response()
            answer.status_code, answer._content = 404, b""
            return answer

    monkeypatch.setattr(connect, "user_agent_session", lambda: Http())
    monkeypatch.setattr(app.MainWindow, "refresh_device", lambda self, cause=None: None)
    # The made-up watch with a folder for music too, so that its page has the row "Music".
    xml = device_root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("</MassStorageMode>", MUSIC + "</MassStorageMode>"))
    (device_root / "Music").mkdir()
    windows = []

    def make(width=1000):
        win = app.MainWindow(None)
        windows.append(win)
        win.set_default_size(width, 740)
        win.present()
        _turn()
        found_now(monkeypatch, LocalStorage(device_root))
        win.session = service.open_session(allow_ums=True)
        win.show_overview()
        win.navigation.push(win.device_view)
        _turn()
        return win

    yield make
    for win in windows:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()


def _row_of(win, key):
    return {"updates": win.update_row, "maps": win.map_row, "apps": win.apps_row,
            "music": win.music_row}[key]


@pytest.mark.parametrize("key", PAGES)
def test_one_click_back_from_a_page_chosen_on_the_device_page(device_window, key):
    win = device_window()
    assert not win.split.get_collapsed() and _stack(win) == ["start", "device"]
    _row_of(win, key).emit("activated")
    _turn()
    assert win.pages.get_visible_child_name() == key
    _back(win)
    assert _stack(win) == ["start"]
    assert win.navigation.get_visible_page() is win.start.page


@pytest.mark.parametrize("key", PAGES)
def test_one_click_back_from_a_page_chosen_in_the_side_panel(device_window, key):
    win = device_window()
    row = next(r for r in descendants(win.nav) if getattr(r, "key", None) == key)
    win.nav.select_row(row)
    row.emit("activate")
    _turn()
    assert win.pages.get_visible_child_name() == key
    _back(win)
    assert _stack(win) == ["start"]


def test_one_click_back_from_the_device_page_itself(device_window):
    win = device_window()
    _back(win)
    assert _stack(win) == ["start"]


@pytest.mark.parametrize("key", PAGES)
def test_folded_the_page_goes_back_to_the_side_panel_then_to_the_start_page(device_window,
                                                                          key):
    win = device_window(width=500)
    assert win.split.get_collapsed()
    _row_of(win, key).emit("activated")
    _turn()
    assert win.split.get_show_content() and win.pages.get_visible_child_name() == key
    _back(win)
    assert not win.split.get_show_content() and _stack(win) == ["start", "device"]
    _back(win)
    assert _stack(win) == ["start"]


def test_a_page_chosen_stays_in_front_when_the_window_folds(device_window):
    win = device_window()
    win.map_row.emit("activated")
    _turn()
    win.split.set_collapsed(True)  # as the window's breakpoint folds it
    _turn()
    assert win.split.get_show_content() and win.pages.get_visible_child_name() == "maps"


# --- the device opened again: always on its device page -----------------------------------


def _on_device_page(win):
    return (_stack(win) == ["start", "device"] and win.pages.get_visible_child_name() == "watch"
            and win.content_page.get_title() == win.sections["watch"].title)


@pytest.mark.parametrize("key", PAGES)
def test_the_tile_of_the_open_device_opens_its_device_page(device_window, key):
    from openexpress.gui.start_window import OPEN

    win = device_window()
    _row_of(win, key).emit("activated")
    _turn()
    _back(win)
    assert _stack(win) == ["start"]
    index = next(i for i, (_tile, target) in enumerate(win._start_entries) if target is OPEN)
    win._open_tile(index)
    _turn()
    assert _on_device_page(win)


@pytest.mark.parametrize("key", PAGES)
def test_the_device_plugged_in_again_opens_on_its_device_page(device_window, device_root, key):
    from openexpress import service

    win = device_window()
    _row_of(win, key).emit("activated")
    _turn()
    win.session = None  # unplugged: the window goes back to the start page
    win.navigation.pop_to_page(win.start.page)
    _turn()
    win.session = service.open_session(allow_ums=True)  # plugged in again
    win.show_device()
    _turn()
    assert _on_device_page(win)


# --- the page of an activity ----------------------------------------------------------------


def _activity_page(win, device_root):
    """The Activities page of the made-up device with a hike of its own, and the hike's row
    clicked: its page, its file read at once (the window's tasks run at once)."""
    from fakes import FitData, FitDefinition, RunNow, fit_file
    from gi.repository import Adw

    session = FitDefinition(2, 18, ((2, 0x86), (5, 0x00), (7, 0x86), (8, 0x86), (9, 0x86),
                                    (124, 0x86), (16, 0x02), (22, 0x84)))
    hike = fit_file(session, FitData(2, (1_100_200_000, 17, 14_400_000, 13_800_000,
                                         1_240_000, 1000, 128, 740)))
    (device_root / "GARMIN" / "Activity" / "2026-10-03-10-00-00.fit").write_bytes(hike)
    win.run_task = RunNow()
    win.select_section("activities")
    _turn()
    rows = [r for r in descendants(win.sections["activities"].page)
            if isinstance(r, Adw.ActionRow) and r.get_title() == "Hiking"]
    assert len(rows) == 1 and rows[0].get_activatable()
    win.navigation.set_animate_transitions(False)  # the arrow in view is then the page's own
    rows[0].emit("activated")
    _turn()
    assert _stack(win) == ["start", "device", "activity"]
    return win.navigation.get_visible_page()


@pytest.mark.parametrize("width", [1000, 500])
def test_back_from_the_page_of_an_activity_to_the_activities_page(device_window, device_root,
                                                                  width):
    """Folded too: the arrow goes back to the list, in front of the side panel."""
    from gi.repository import Gtk

    win = device_window(width=width)
    page = _activity_page(win, device_root)
    assert {"12.40\u00a0km", "Distance", "740\u00a0m"} <= {w.get_text() for w in descendants(page)
                                                          if isinstance(w, Gtk.Label)}
    _back(win)
    assert _stack(win) == ["start", "device"]
    assert win.pages.get_visible_child_name() == "activities"
    assert win.split.get_show_content() == win.split.get_collapsed()


@pytest.mark.parametrize("action", ["refresh", "sync"])
def test_refresh_and_sync_from_the_keyboard_go_back_to_the_device_view_first(
        device_window, device_root, monkeypatch, action):
    """Their task's bar is in the device view: they start there."""
    from openexpress.gui import app

    started = []
    monkeypatch.setattr(app.MainWindow, {"refresh": "_on_refresh", "sync": "start_sync"}[action],
                        lambda self: started.append(_stack(self)))
    win = device_window()
    win._sync_blocked = lambda: ""  # signed in, as far as the action knows
    win._update_actions()
    _activity_page(win, device_root)
    win.activate_action(f"win.{action}", None)
    assert started == [["start", "device"]]
    assert win.pages.get_visible_child_name() == "activities"


def test_the_page_of_an_activity_goes_with_the_pages_of_the_device_view(device_window,
                                                                      device_root):
    """The device drawn anew (opened again, Refresh): the page of an activity read from it
    before goes, the device view stays on its Activities page."""
    win = device_window()
    _activity_page(win, device_root)
    win._clear_device_pages()
    _turn()
    assert _stack(win) == ["start", "device"]
    assert win.pages.get_visible_child_name() == "activities"
