"""The shared fakes of the tests (tests/fakes.py) do what the program does where it matters:
RunNow runs a task as the window's run_task does, at once (review of 24.10.2026, "Co
ujednolicić" 7: 36 fakes of run_task in 14 files, with 5 ways of handling an error)."""

import threading

import pytest
from fakes import RunNow


class Failed(Exception):
    pass


def raising(error):
    """A work that fails with `error`."""
    def work():
        raise error
    return work


def test_the_work_runs_on_this_thread_and_done_gets_its_result():
    run, got, threads = RunNow(), [], []
    run("Copying…", lambda: threads.append(threading.current_thread()) or 42, got.append,
        device=False, in_page=True)
    assert got == [42] and threads == [threading.current_thread()]
    assert run.titles == ["Copying…"]
    (call,) = run.calls
    assert (call.device, call.in_page, call.button) == (False, True, None)


def test_with_a_thread_the_work_runs_in_another_and_done_on_this_one():
    run, where = RunNow(thread=True), []
    run("Copying…", lambda: where.append(threading.current_thread()) or 1,
        lambda result: where.append(threading.current_thread()))
    assert where[0] is not threading.current_thread()
    assert where[1] is threading.current_thread()


def test_an_error_of_done_is_not_given_to_failed():
    """The window calls done outside any try: an error there is not the task's."""
    failed = []

    def done(result):
        raise Failed("in done")

    with pytest.raises(Failed, match="in done"):
        RunNow()("Copying…", lambda: 1, done, failed.append)
    assert failed == []


def test_an_error_of_the_work_goes_to_failed():
    failed = []
    RunNow()("Copying…", raising(Failed("gone")), None,
             lambda e: failed.append(str(e)) or True)
    assert failed == ["gone"]


@pytest.mark.parametrize("taken", [None, False])
def test_an_error_failed_does_not_take_is_raised_by_default(taken):
    failed = None if taken is None else (lambda e: False)
    with pytest.raises(Failed, match="gone"):
        RunNow()("Copying…", raising(Failed("gone")), None, failed)


def test_an_error_failed_does_not_take_can_be_recorded_or_shown():
    run = RunNow(error="record")
    run("Copying…", raising(Failed("gone")), None, lambda e: False)
    assert run.errors == ["gone"]
    shown = []
    RunNow(error=lambda title, text: shown.append((title, text)))(
        "Copying…", raising(Failed()))
    assert shown == [("Copying…", "Failed")]  # an error without words: its class, as the window


def test_the_hooks_come_before_the_work_and_between_it_and_done():
    order = []
    run = RunNow(on_call=lambda call: order.append(("call", call.title)),
                 before_done=lambda: order.append("before done"))
    run("Copying…", lambda: order.append("work"), lambda result: order.append("done"))
    assert order == [("call", "Copying…"), "work", "before done", "done"]


@pytest.mark.parametrize("fails", [False, True])
def test_a_work_that_writes_to_the_device_is_noted_also_when_it_failed(fails):
    from openexpress.gui.app import writes_to_device

    def work():
        if fails:
            raise Failed("half written")

    run = RunNow(error="record")
    run("Writing…", writes_to_device(work), lambda result: None)
    run("Reading…", lambda: None, lambda result: None)
    assert run.written == ["Writing…"]


@pytest.mark.needs_display
@pytest.mark.parametrize("fails", [False, True])
def test_the_button_spins_during_the_work_and_is_back_before_done(fails):
    from openexpress.gui.app import Gtk

    button, seen = Gtk.Button(label="Install"), []

    def work():
        seen.append(("work", button.get_sensitive(), button.get_label()))
        if fails:
            raise Failed("refused")

    def after(_value):
        seen.append(("after", button.get_sensitive(), button.get_label()))
        return True

    RunNow()("Installing…", work, after, after, button=button)
    assert seen == [("work", False, None), ("after", True, "Install")]


# --- window_methods: the window's methods on a stand-in, wherever the window has them -----------

def test_a_stand_in_gets_the_methods_of_the_mixins_too():
    """A stand-in gets a method from the mixin that has it (moved out of app.py in the
    split) as well as one MainWindow defines itself."""
    import types

    from fakes import window_methods

    from openexpress.gui.app import MainWindow
    from openexpress.gui.device_page import DevicePage
    from openexpress.gui.maps_page import MapsPage
    from openexpress.gui.window_tasks import TaskRunner

    win = window_methods(types.SimpleNamespace(), ["show_maps", "_load", "_own_map_subtitle",
                                                   "in_background", "_update_actions",
                                                   "_ownership_title"])
    assert win._update_actions.__func__ is MainWindow.__dict__["_update_actions"]
    assert win._ownership_title is MainWindow.__dict__["_ownership_title"].__func__
    assert win.show_maps.__func__ is vars(MapsPage)["show_maps"]
    assert win.show_maps.__self__ is win and win._load.__func__ is vars(DevicePage)["_load"]
    # Static methods stay unbound, as on the window.
    assert win._own_map_subtitle is vars(MapsPage)["_own_map_subtitle"].__func__
    assert win.in_background is vars(TaskRunner)["in_background"].__func__


@pytest.mark.parametrize("name", ["_no_such_method", "present"])  # unknown; only GTK's
def test_a_name_the_window_does_not_have_fails_with_that_name(name):
    import types

    from fakes import window_methods

    with pytest.raises(AssertionError, match=f"MainWindow has no method {name} "):
        window_methods(types.SimpleNamespace(), ["_load", name])


def test_no_stand_in_picks_methods_from_the_window_class_alone():
    """vars(MainWindow) (or its __dict__) has only what app.py defines: a method moved to a
    mixin would leave such a stand-in without a word. window_methods walks the MRO."""
    import re
    from pathlib import Path

    own = re.compile(r"vars\((?:app\.)?MainWindow\)|MainWindow\.__dict__")
    here = Path(__file__).resolve()
    found = [f"{path.name}:{number}" for path in sorted(here.parent.glob("*.py")) if path != here
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if own.search(line)]
    assert found == [], f"stand-ins built from MainWindow's own class: {found}"


# --- replace_in_window: never a module skipped without a word ------------------------------


# Where each name is held: a module given another object, and one that keeps its own.
PLACES = {"Adw": ("report_dialog", "apps_page"),  # gui.app holds it
          "space_text": ("maps_page", "window_tasks")}  # gui.app does not


def test_a_name_held_as_another_object_somewhere_fails():
    """A module of the window that holds the name as another object would keep it, and the
    code there would never see the replacement: replace_in_window says which, and replaces
    nothing (it used to skip such a module without a word). The names are written out, so
    that guard 3 (test_dead_patches) sees them."""
    import importlib

    from fakes import replace_in_window

    for name in ("Adw", "space_text"):
        other, unchanged = PLACES[name]
        module = importlib.import_module(f"openexpress.gui.{other}")
        setting, replacing = pytest.MonkeyPatch(), pytest.MonkeyPatch()
        try:
            setting.setattr(module, name, object())  # not the object of the others
            with pytest.raises(AssertionError, match=rf"openexpress\.gui\.{other}"):
                replace_in_window(replacing, name, "a replacement")
            kept = vars(importlib.import_module(f"openexpress.gui.{unchanged}"))[name]
            assert kept != "a replacement", name  # nothing replaced
        finally:
            replacing.undo()
            setting.undo()
