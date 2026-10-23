"""The dialogs of the window are released once closed: no handler of a signal holds its own
widget, the dialog or a sibling (the rule since 0.4.2; a cycle through GObject is one
Python's collector cannot see). Seen with weak references in the review of 22.10.2026. All
data is made up."""

import gc
import types

import pytest


def _finalized(obj) -> list:
    """A list that gets an entry when the GObject `obj` is finalized."""
    told = []
    obj.weak_ref(lambda: told.append(True))
    return told


def _settle():
    from gi.repository import GLib

    for _ in range(3):
        gc.collect()
        while GLib.main_context_default().iteration(False):
            pass


def _preferences_window():
    from openexpress.gui.app import MainWindow

    win = types.SimpleNamespace(busy=False, _action_buttons=[], _background_enabled=lambda: False,
                                set_background=lambda on, done=None: None, choose_dll=lambda: None,
                                error=lambda title, text: None, run_task=None, toast=None)
    for name in ("_button", "_cache_group", "_ask_clear_cache", "_account_group",
                 "_account_action"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    return win


@pytest.mark.needs_display
def test_the_preferences_are_released_once_closed(monkeypatch, xdg):
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    shown = []
    monkeypatch.setattr(Adw.PreferencesDialog, "present", lambda self, parent: shown.append(self))
    win = _preferences_window()
    MainWindow.show_preferences(win)
    dialog = shown.pop()
    gone = _finalized(dialog)
    dialog.emit("closed")
    del dialog
    win._action_buttons.clear()  # what the window drops as its pages are drawn again
    _settle()
    assert gone == [True]


@pytest.mark.needs_display
def test_the_preferences_and_the_question_of_the_cache_are_released(monkeypatch, xdg):
    """The storage group counted (in the background), a part cleared after its question:
    once closed, the preferences and the question are both released, and the count that
    comes back later finds nothing to draw in."""
    import time

    from gi.repository import Adw, GLib

    from openexpress import cache
    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    shown, questions = [], []
    monkeypatch.setattr(Adw.PreferencesDialog, "present", lambda self, parent: shown.append(self))
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: questions.append(self))
    maps = cache.paths.cache_dir() / "maps"
    maps.mkdir(parents=True)
    (maps / "m").write_bytes(b"x" * 100)
    win = _preferences_window()

    def run_task(title, work, done, failed=None, device=True, in_page=False, button=None):
        restore = app.show_working(button)
        result = work()
        restore(True)
        done(result)

    win.run_task = run_task
    MainWindow.show_preferences(win)
    dialog = shown.pop()
    deadline = time.monotonic() + 5
    while win._storage.usage is None and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
    (trash,) = [t for name, (_row, t) in win._storage.parts.items() if name == "maps"]
    trash.emit("clicked")
    question = questions.pop()
    question.emit("response", "clear")
    assert list(maps.iterdir()) == []
    del trash
    gone, asked_gone = _finalized(dialog), _finalized(question)
    question.emit("closed")
    dialog.emit("closed")
    del dialog, question
    win._action_buttons.clear()
    MainWindow.in_background(cache.usage, lambda use: app.show_cache_usage(win, use))
    _settle()
    time.sleep(0.05)
    _settle()
    assert gone == [True] and asked_gone == [True]
    assert getattr(win, "_storage", None) is None


def _presented_in_a_window(monkeypatch, kind):
    """`kind`.present puts the dialog in a real window, which alone holds it then (as the
    window of the program does): the test keeps no reference to it."""
    from gi.repository import Gtk

    host = Gtk.Window()
    present = kind.present
    monkeypatch.setattr(kind, "present", lambda self, parent: present(self, host))
    return host


@pytest.mark.needs_display
def test_the_language_changed_in_the_preferences_says_to_restart(monkeypatch, xdg):
    """The toast still comes on the dialog, which only its window holds."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    toasts, groups = [], []
    host = _presented_in_a_window(monkeypatch, Adw.PreferencesDialog)
    monkeypatch.setattr(Adw.PreferencesDialog, "add_toast",
                        lambda self, toast: toasts.append(toast.get_title()))
    add = Adw.PreferencesPage.add
    monkeypatch.setattr(Adw.PreferencesPage, "add",
                        lambda page, group: groups.append(group) or add(page, group))
    MainWindow.show_preferences(_preferences_window())
    (language,) = [g for g in groups if g.get_title() == "Language"]
    combo = next(w for w in _descendants(language) if isinstance(w, Adw.ComboRow))
    groups.clear()
    _settle()
    combo.set_selected(combo.get_selected() + 1)
    assert toasts == ["Restart the application to change the language"]
    host.destroy()


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def _order_window(monkeypatch, chosen):
    """The window of choose_order: the copies of the app list made up, the task run at
    once; what confirm_order gets is recorded in `chosen`."""
    from openexpress import ciq
    from openexpress.gui.app import MainWindow

    choice = ("a made-up copy", "a made-up plan")
    monkeypatch.setattr(ciq, "read_installed", lambda device, storage: "state")
    monkeypatch.setattr(ciq, "order_choices", lambda device, state: [choice])
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=None, storage=None),
        error=lambda title, text: pytest.fail(text),
        _order_blocked=lambda state: None,
        _order_rows=lambda choices: [("Copy of 12.10.2026", "213 apps", True)],
        confirm_order=lambda copy, plan, note: chosen.append((copy, plan)),
        run_task=lambda title, work, done, button=None: done(work()))
    win.choose_order = types.MethodType(MainWindow.choose_order, win)
    return win, choice


@pytest.mark.needs_display
def test_the_choice_of_a_copy_of_the_app_list_is_released_once_closed(monkeypatch):
    """Choosing a copy still enables Next and Next still goes on with it, with the dialog held
    by its window alone; closed, it is released."""
    import weakref

    from gi.repository import Adw, Gtk

    shown, chosen = [], []
    _presented_in_a_window(monkeypatch, Adw.AlertDialog)
    present = Adw.AlertDialog.present
    monkeypatch.setattr(Adw.AlertDialog, "present",
                        lambda self, parent: (shown.append(self), present(self, parent)))
    win, choice = _order_window(monkeypatch, chosen)
    win.choose_order()
    dialog = shown.pop()
    gone = _finalized(dialog)
    seen = weakref.ref(dialog)  # the test looks at it without holding it
    box = next(w for w in _descendants(dialog.get_extra_child()) if isinstance(w, Gtk.ListBox))
    del dialog
    _settle()
    box.select_row(box.get_row_at_index(0))
    assert seen().get_response_enabled("next")  # a copy chosen: Next
    seen().emit("response", "next")
    assert chosen == [choice]
    dialog = seen()
    dialog.emit("closed")  # what closing it says (after its animation, in the program)
    dialog.force_close()
    del dialog, box
    _settle()
    assert gone == [True]
