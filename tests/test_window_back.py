"""Back from the device view, in the real window: from any page of its side panel (Updates,
Maps, Apps, Music), chosen on the device page or in the side panel, one click on the back arrow
leads to the start page ("Devices"). Folded (a narrow window) the page goes back to the side
panel first, then to the start page; a page chosen stays in front when the window folds. The
device is made up; no server is asked."""

import pytest

pytestmark = pytest.mark.needs_display

PAGES = ["updates", "maps", "apps", "music"]
MUSIC = ("<DataType><Name>MEDIA_TRACK</Name><File><Location><Path>Music</Path>"
         "<FileExtension>MP3</FileExtension></Location>"
         "<TransferDirection>InputOutput</TransferDirection></File></DataType>")


def _all(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _all(child)
        child = child.get_next_sibling()


def _turn(times=200):
    from gi.repository import GLib

    for _ in range(times):
        GLib.MainContext.default().iteration(False)


def _stack(win):
    return [page.get_tag() for page in win.navigation.get_navigation_stack()]


def _back(win):
    """A click on the back arrow in view (the side panel's; folded, the page's own first)."""
    from gi.repository import Gtk

    arrows = [w for w in _all(win) if isinstance(w, Gtk.Button) and w.get_mapped()
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
        win.session = service.open_session(storage=LocalStorage(device_root))
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
    row = next(r for r in _all(win.nav) if getattr(r, "key", None) == key)
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
