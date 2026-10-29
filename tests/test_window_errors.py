"""An exception the window does not expect goes to the log as ERROR with its traceback, as on
the command line: a report with the log then tells where it
was. A page whose drawing fails says so and loads anew when chosen again, instead of staying
on "loading" for good. All data is made up."""

import sys
import types

import pytest
from fakes import RunNow, window_methods
from fakes import window as stand_in


def _gui_log(log_file):
    from openexpress import applog

    applog.setup("gui")
    return log_file


def _lines(log_file):
    return log_file.read_text(encoding="utf-8").splitlines()


def test_an_exception_in_a_callback_of_the_window_is_logged_with_its_traceback(log_file,
                                                                              monkeypatch):
    """Raised in a callback of GLib (a handler, an idle): PyGObject hands it to
    sys.excepthook, which the window sets; stderr still gets it."""
    from gi.repository import GLib

    from openexpress.gui import app

    told = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: told.append(exc[0]))
    _gui_log(log_file)
    app.install_excepthook()

    def broken():
        raise RuntimeError("made-up failure")

    GLib.idle_add(broken)
    context = GLib.main_context_default()
    while context.iteration(False):
        pass
    text = "\n".join(_lines(log_file))
    # Written by log_unexpected, under its own module.
    assert ("ERROR openexpress.gui.window_tasks: unexpected error in the window: "
            "RuntimeError") in text
    assert "Traceback" in text and "made-up failure" in text
    assert told == [RuntimeError]  # and on to stderr too


def test_ctrl_c_is_not_an_error_of_the_window(log_file, monkeypatch):
    from openexpress.gui import app

    told = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: told.append(exc[0]))
    _gui_log(log_file)
    app.install_excepthook()
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert told == [KeyboardInterrupt]
    assert not any(" ERROR " in line for line in _lines(log_file))


def _window(sections):
    """What _load and load_section need of the window; a task runs at once."""

    win = stand_in(
        sections=sections, session=types.SimpleNamespace(mass_storage=False), busy=False,
        _retry_sources={}, _action_buttons=[],
        run_task=RunNow())
    for name in ("load_updates", "load_maps", "load_music"):
        setattr(win, name, lambda: pytest.fail("another page"))
    window_methods(win, ["_load"])
    return win


@pytest.mark.needs_display
def test_a_page_that_cannot_be_drawn_says_so_and_loads_anew(log_file, monkeypatch):
    from openexpress.gui.app import MainWindow
    from openexpress.gui.device_page import Section

    monkeypatch.setattr(sys, "excepthook", lambda *exc: None)  # stderr stays quiet here
    _gui_log(log_file)
    sec = Section("apps", "Apps", "application-x-addon-symbolic", "Reading the apps…")
    win = _window({"apps": sec})
    shown = []

    def broken(result):
        raise RuntimeError("made-up failure")

    win.load_apps = lambda: win._load("apps", "Reading the apps…", lambda: "the apps", broken)
    MainWindow.load_section(win, "apps")
    assert sec.stack.get_visible_child_name() == "error" and sec.loaded is False
    assert sec.error.get_title() == "Something went wrong"
    assert sec.error.get_description() == ("This page could not be shown. Try again; if it "
                                           "happens again, report the problem with the log "
                                           "attached.")
    text = "\n".join(_lines(log_file))
    assert ("ERROR openexpress.gui.window_tasks: unexpected error showing the page apps: "
            "RuntimeError") in text
    assert "Traceback" in text and "made-up failure" in text

    # Chosen again (or Try again): read and drawn anew.
    win.load_apps = lambda: win._load("apps", "Reading the apps…", lambda: "the apps",
                                      shown.append)
    MainWindow.load_section(win, "apps")
    assert shown == ["the apps"]
    assert sec.stack.get_visible_child_name() == "content" and sec.loaded is True
