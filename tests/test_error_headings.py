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
# The pages whose every task writes to the device or reads it for the user: each names what
# did not happen.
PAGES = ("activities_page", "maps_page", "apps_page", "music_page", "courses_page")


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


@pytest.mark.parametrize("module", PAGES)
def test_every_task_of_a_page_names_what_did_not_happen(module):
    """A new task of these pages comes with its heading ("<Thing> Not <Done>")."""
    tasks = [c for c in _calls(module) if _called(c) == "run_task"]
    without = [c.lineno for c in tasks
               if not any(k.arg == "failed_title" for k in c.keywords)]
    assert tasks and without == [], f"{module}: run_task without failed_title at {without}"


def test_apps_queued_in_garmin_connect_are_information_not_an_error():
    calls = [c for c in _calls("apps_page") if c.args]
    errors = [_words(c.args[0]) for c in calls if _called(c) == "error"]
    told = [_words(c.args[2]) for c in calls if _called(c) == "inform" and len(c.args) > 2]
    assert "Queued in Garmin Connect" not in errors and "Queued in Garmin Connect" in told
