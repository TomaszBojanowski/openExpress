"""The heading of the error of a task: its own, "<Thing> Not <Done>" (the task gives it as
failed_title), else "Something Went Wrong"; never the words of its progress ("Saving…"). What
a task shows as information (apps queued in Garmin Connect) is no error. Made-up tasks and
words; nothing reaches a device or a server."""

import ast
import types
from pathlib import Path

import pytest
from fakes import replace_in_window
from fakes import window as stand_in

GUI = Path(__file__).resolve().parent.parent / "openexpress" / "gui"


def _at_once(monkeypatch):
    """The window's thread and main loop, run at once."""
    from gi.repository import GLib

    replace_in_window(monkeypatch, "threading", types.SimpleNamespace(
        Thread=lambda target, daemon: types.SimpleNamespace(start=target)))
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: fn(*args))


@pytest.mark.parametrize("failed_title, heading", [
    ("Map Not Added", "Map Not Added"), (None, "Something Went Wrong")])
def test_a_task_that_failed_says_its_own_heading_or_a_general_one(monkeypatch, failed_title,
                                                                   heading):
    from openexpress.gui import app

    _at_once(monkeypatch)
    told = []
    win = stand_in(busy=False, session=None, set_busy=lambda *a, **kw: None,
                   error=lambda title, text: told.append(title),
                   _run_pending_refresh=lambda: None)

    def work():
        raise RuntimeError("made-up failure")

    app.MainWindow.run_task(win, "Copying the Map…", work, failed_title=failed_title)
    assert told == [heading]


def _calls(module: str):
    """The calls in a module of the window."""
    tree = ast.parse((GUI / f"{module}.py").read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _words(node) -> str | None:
    """The English words of `_("…")`, also with .format(…) after it; None for anything else."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "format":
        node = node.func.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" \
            and node.args and isinstance(node.args[0], ast.Constant):
        return node.args[0].value
    return node.value if isinstance(node, ast.Constant) else None


def _called(call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def test_no_heading_of_an_error_is_the_words_of_a_progress():
    found = []
    for path in sorted(GUI.glob("*.py")):
        for call in _calls(path.stem):
            heading = None
            if _called(call) == "error" and call.args:
                heading = _words(call.args[0])
            elif _called(call) == "run_task":
                given = [k.value for k in call.keywords if k.arg == "failed_title"]
                heading = _words(given[0]) if given else None
            if heading is not None and heading.endswith("…"):
                found.append(f"{path.name}:{call.lineno} {heading}")
    assert found == []


def _calls_in(node, function):
    """(the function a run_task call is in, the call) under `node`."""
    for child in ast.iter_child_nodes(node):
        inner = child.name if isinstance(child, (ast.FunctionDef,
                                                 ast.AsyncFunctionDef)) else function
        if isinstance(child, ast.Call) and _called(child) == "run_task":
            yield function, child
        yield from _calls_in(child, inner)


def _tasks():
    """(module, the function a run_task call is in, the call) for every task of the window."""
    for path in sorted(GUI.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function, call in _calls_in(tree, None):
            yield path.stem, function, call


# The tasks that keep the general heading: they only read, or their own handler heads every
# error (an update not installed, a device not ejected).
GENERAL = {("activity_page", "open_activity"), ("detection", "refresh_device"),
           ("detection", "open_choice"), ("device_page", "_load"), ("hidden_reading", "_start"),
           ("updates_page", "install"), ("updates_page", "eject_device")}


def test_every_task_that_changes_something_names_what_did_not_happen():
    """A new task comes with its heading ("<Thing> Not <Done>"), or is named here for a reason."""
    without = sorted({(module, function) for module, function, call in _tasks()
                      if not any(k.arg == "failed_title" for k in call.keywords)})
    assert without == sorted(GENERAL)


def test_apps_queued_in_garmin_connect_are_information_not_an_error():
    calls = [c for c in _calls("apps_page") if c.args]
    errors = [_words(c.args[0]) for c in calls if _called(c) == "error"]
    told = [_words(c.args[2]) for c in calls if _called(c) == "inform" and len(c.args) > 2]
    assert "Queued in Garmin Connect" not in errors and "Queued in Garmin Connect" in told
