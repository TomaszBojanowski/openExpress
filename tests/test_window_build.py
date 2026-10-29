"""The real window, built as the program builds it (MainWindow(None), no detection), has what
its parts wire together: its actions, the main menu and the start page's menu, the sections of
its side panel, the size saved when it closes, and the bar at the bottom (spinner, bar, fraction,
a wait of known length with Stop Waiting, then nothing)."""

import threading
import types

import pytest
from test_forget_window import _turn_until

pytestmark = pytest.mark.needs_display


@pytest.fixture
def window(monkeypatch):
    from openexpress import netstatus
    from openexpress.gui import app

    monkeypatch.setattr(app.MainWindow, "refresh_device", lambda self, cause=None: None)
    win = app.MainWindow(None)
    yield win
    netstatus.STATUS.remove_listener(win._net_listener)
    win.destroy()


def _all(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _all(child)
        child = child.get_next_sibling()


def _items(model):
    """The actions of a menu, sections included: (action, target or None)."""
    from gi.repository import Gio

    out = []
    for i in range(model.get_n_items()):
        section = model.get_item_link(i, Gio.MENU_LINK_SECTION)
        if section is not None:
            out += _items(section)
            continue
        action = model.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_ACTION, None)
        target = model.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_TARGET, None)
        out.append((action.get_string() if action else None,
                    target.get_string() if target else None))
    return out


def test_the_window_has_its_actions(window):
    assert sorted(window.list_actions()) == ["refresh", "start-view", "sync"]
    view = window.lookup_action("start-view")
    assert view.get_state().get_string() == "auto"
    assert window.lookup_action("refresh").get_enabled()


def test_the_main_menu_and_the_start_page_s_menu(window):
    from gi.repository import Adw, Gtk

    main = [("app.preferences", None), *([("app.shortcuts", None)]
                                          if hasattr(Adw, "ShortcutsDialog") else []),
            ("app.report", None), ("app.about", None)]
    menus = [_items(b.get_menu_model()) for b in [*_all(window), *_all(window.start.page)]
             if isinstance(b, Gtk.MenuButton) and b.get_menu_model() is not None]
    start = [("win.start-view", v) for v in ("auto", "grid", "list")] + main
    assert main in menus and start in menus


def test_the_side_panel_chooses_a_section(window):
    from gi.repository import Gtk

    keys = list(window.sections)
    assert keys == ["watch", "updates", "maps", "apps", "music"]
    rows = [r for r in _all(window.nav) if isinstance(r, Gtk.ListBoxRow)]
    assert [r.key for r in rows] == keys
    window.select_section("maps")
    assert window.pages.get_visible_child_name() == "maps"
    assert window.content_page.get_title() == window.sections["maps"].title


def test_closing_saves_the_size(monkeypatch):
    from openexpress import netstatus
    from openexpress.gui import app

    monkeypatch.setattr(app.MainWindow, "refresh_device", lambda self, cause=None: None)
    win = app.MainWindow(None)
    win.set_default_size(900, 700)
    win.present()  # a window never shown is not asked to close
    listener = win._net_listener
    win.close()
    netstatus.STATUS.remove_listener(listener)
    assert app.load_gui_settings()["window"] == {"width": 900, "height": 700,
                                                 "maximized": False}


def test_the_bar_at_the_bottom(window):
    from openexpress.progress import TimedWait

    window.set_busy(True, "Syncing…", device=True)
    assert window.action_bar.get_revealed() and window.busy
    assert window.busy_spinner.get_visible() and not window.progress_bar.get_visible()
    assert window.unplug_label.get_visible() and not window.stop_wait_button.get_visible()
    window.progress("Uploading activity")
    assert _turn_until(window.progress_bar.get_visible)
    assert not window.busy_spinner.get_visible()
    assert window.progress_label.get_text() == "Uploading activity"
    window.fraction(1, 4)
    assert _turn_until(lambda: window.progress_bar.get_fraction() == 0.25)
    # TrueUp waits for Garmin Connect: the bar fills and the wait can be stopped.
    window.session = types.SimpleNamespace(stop_waiting=threading.Event(), mass_storage=False,
                                           logged_in=False, ownership_error=None)
    window.progress(TimedWait("Waiting 3 min 20 s for Garmin Connect…", seconds=200,
                              done=0.4))
    assert _turn_until(window.stop_wait_button.get_visible)
    assert window.progress_bar.get_fraction() == 0.4
    window.stop_wait_button.emit("clicked")
    assert window.session.stop_waiting.is_set() and not window.stop_wait_button.get_visible()
    window.set_busy(False)
    assert not window.action_bar.get_revealed() and not window.busy
    assert not window.stop_wait_button.get_visible() and window._waiting == 0
