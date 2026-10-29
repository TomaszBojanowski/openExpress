"""The settings of the window (gui.json): each change reads the file again and changes only its
own keys, so the switch of the background sync keeps the start view chosen since the window
opened. The language of the interface is read and kept by one pair of functions (i18n), for
the window, the command line and the start."""

import json
import types

from fakes import window as stand_in


def _window():
    from openexpress.gui.gui_settings import load_gui_settings

    shown = []
    win = stand_in(gui_settings=load_gui_settings(), session=None,
                   toast=shown.append, error=lambda *a: shown.append(a),
                   start=types.SimpleNamespace(view=None),
                   _render_start=lambda: None)
    return win, shown


def test_the_background_switch_keeps_the_view_chosen_since_the_window_opened(monkeypatch):
    from openexpress import background
    from openexpress.gui import app
    from openexpress.gui.gui_settings import load_gui_settings, save_gui_settings
    from openexpress.gui.start_window import StartView

    save_gui_settings({"window": {"width": 900, "height": 700, "maximized": False}})
    win, _shown = _window()  # the window opens: its settings as they were
    StartView.choose(win, "list")
    monkeypatch.setattr(background, "set_enabled", lambda enabled, done: done(True, ""))
    app.MainWindow.set_background(win, True)
    # Opened again: the view, the size and the switch, all kept.
    assert StartView.saved() == "list"
    assert load_gui_settings() == {
        "window": {"width": 900, "height": 700, "maximized": False}, "start_view": "list",
        "background": True}
    assert win.gui_settings["background"] is True  # what the window knows now


def test_every_change_of_gui_json_keeps_the_other_keys():
    from openexpress.gui.gui_settings import (
        load_gui_settings,
        save_gui_settings,
        save_window_geometry,
        update_gui_settings,
    )
    from openexpress.gui.start_window import StartView

    save_gui_settings({"someone else's": 1})
    update_gui_settings(background=False)
    save_window_geometry(800, 600, True)
    StartView.save("grid")
    assert load_gui_settings() == {
        "someone else's": 1, "background": False, "start_view": "grid",
        "window": {"width": 800, "height": 600, "maximized": True}}


def test_one_way_for_the_language_of_the_interface(tmp_path):
    from openexpress import i18n, paths
    from openexpress.gui.preferences import load_ui_language, save_ui_language

    path = paths.config_dir() / "settings.json"
    paths.write_private(path, json.dumps({"environment": "production", "client_guid": "g"}))
    assert i18n.saved_ui_language() == ""
    i18n.save_ui_language("pl")
    assert json.loads(path.read_text()) == {"environment": "production", "client_guid": "g",
                                            "ui_language": "pl"}
    assert i18n.saved_ui_language() == "pl"
    assert load_ui_language is i18n.saved_ui_language
    assert save_ui_language is i18n.save_ui_language
    path.write_text("not json")
    assert i18n.saved_ui_language() == ""  # unreadable: the system's


def test_setup_keeps_the_language_of_the_interface(capsys):
    from openexpress import i18n
    from openexpress.cli import main

    i18n.save_ui_language("pl")
    assert main(["setup", "--environment", "production"]) == 0
    assert i18n.saved_ui_language() == "pl"
    try:
        assert main(["setup", "--ui-language", "system"]) == 0
        assert i18n.saved_ui_language() == ""
    finally:
        i18n.setup("en")
