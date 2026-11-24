"""The window's own logic without a device: sections, refreshes, its size and Ctrl+Q."""

import types

import pytest
from fakes import window as stand_in
from fakes import window_methods


class _Sec:
    def __init__(self):
        self.shown, self.errors, self.loaded = [], [], False

    def show(self, name):
        self.shown.append(name)

    def show_error(self, text):
        self.errors.append(text)

    def clear(self):
        pass


def _fake_window(session):

    win = stand_in(session=session, busy=False, sections={"maps": _Sec()},
                   _retry_sources={}, tasks=[])
    win.run_task = (lambda title, work, done, failed, device=True, in_page=False:
                    win.tasks.append((done, failed)))
    window_methods(win, ["_load"])
    return win


def test_section_load_ignores_results_for_a_disconnected_watch():
    shown = []
    win = _fake_window(session="watch-1")
    win._load("maps", "t", lambda: 1, shown.append)
    done, failed = win.tasks[0]
    win.session = None  # unplugged during the load
    done(["map"])
    assert shown == [] and not win.sections["maps"].loaded
    assert failed(RuntimeError("x")) is True and win.sections["maps"].errors == []
    win.session = "watch-2"  # replugged: a result for the old watch is still ignored
    done(["map"])
    assert shown == []


def test_section_load_keeps_one_retry(monkeypatch):
    from gi.repository import GLib

    added, removed = [], []
    monkeypatch.setattr(GLib, "timeout_add", lambda ms, cb: added.append(cb) or len(added))
    monkeypatch.setattr(GLib, "source_remove", lambda sid: removed.append(sid))
    win = _fake_window(session="watch-1")
    win.busy = True
    win._load("maps", "t", lambda: 1, lambda r: None)
    win._load("maps", "t", lambda: 1, lambda r: None)
    assert removed == [1] and win._retry_sources == {"maps": 2}
    win.session = None
    assert added[-1]() is False and win.tasks == []


def test_pending_refresh_runs_when_idle():
    from openexpress.gui.app import MainWindow

    calls = []
    win = stand_in(_refresh_pending=True, busy=True,
                   refresh_device=lambda: calls.append(1))
    MainWindow._run_pending_refresh(win)
    assert calls == [] and win._refresh_pending
    win.busy = False
    MainWindow._run_pending_refresh(win)
    assert calls == [1] and not win._refresh_pending


def test_window_geometry(xdg):
    from openexpress.gui.gui_settings import (
        DEFAULT_WINDOW,
        load_gui_settings,
        save_gui_settings,
        save_window_geometry,
        window_geometry,
    )

    assert window_geometry({}) == (*DEFAULT_WINDOW, False)
    assert window_geometry({"window": "bad"}) == (*DEFAULT_WINDOW, False)
    assert window_geometry({"window": {"width": 1400, "height": 900, "maximized": True}}) == \
        (1400, 900, True)
    # Too small, too large, wrong types, "maximized" must be a real boolean
    assert window_geometry({"window": {"width": 10, "height": 99999, "maximized": 1}}) == \
        (360, 10000, False)
    assert window_geometry({"window": {"width": True, "height": "800"}}) == (*DEFAULT_WINDOW, False)

    save_gui_settings({"background": True})
    save_window_geometry(1200, 800, False)
    data = load_gui_settings()
    assert data["background"] is True  # other options are kept
    assert window_geometry(data) == (1200, 800, False)


@pytest.mark.parametrize("maximized", [False, True])
def test_ctrl_q_remembers_the_window_size_as_closing_does(xdg, maximized):
    """Ctrl+Q (app.quit) ends the program without the window's close-request: the size is
    saved there too, as when the window is closed."""
    from openexpress.gui.app import App
    from openexpress.gui.gui_settings import load_gui_settings, window_geometry

    win = stand_in(busy=False, get_default_size=lambda: (1100, 700),
                   is_maximized=lambda: maximized)
    window_methods(win, ["_remember_geometry"])
    quits = []
    app = types.SimpleNamespace(main_window=lambda create=True: win,
                                quit=lambda: quits.append(1))
    App.request_quit(app)
    assert quits == [1]
    assert window_geometry(load_gui_settings()) == (1100, 700, maximized)


def test_ctrl_q_during_a_task_still_asks_the_window(xdg):
    """While a task runs, Ctrl+Q closes the window, which refuses and explains, as a close
    does then; nothing quits and nothing is saved here."""
    from openexpress.gui.app import App
    from openexpress.gui.gui_settings import load_gui_settings

    closed, quits = [], []
    win = stand_in(busy=True, close=lambda: closed.append(1))
    app = types.SimpleNamespace(main_window=lambda create=True: win,
                                quit=lambda: quits.append(1))
    App.request_quit(app)
    assert closed == [1] and quits == [] and "window" not in load_gui_settings()
