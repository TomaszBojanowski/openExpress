"""The window in several modules: the pages in modules of their own beside gui/app.py are
mixins of MainWindow, and the log names their tasks as MainWindow's."""

import logging
import types

from fakes import window as stand_in


def test_a_task_of_a_mixin_of_the_window_is_logged_as_the_windows(caplog, in_the_window):
    """The line "task: MainWindow.add_music" of the log names the method that made the work
    of the task; a method of a mixin (MusicPage, MapsPage, …) is still MainWindow's there."""
    from openexpress.gui import app

    mixins = [c for c in app.MainWindow.__mro__[1:] if c.__module__.startswith("openexpress.")]
    assert mixins  # the window has at least one
    started = []
    in_the_window("threading", types.SimpleNamespace(
        Thread=lambda target, daemon: types.SimpleNamespace(start=lambda: started.append(1))))
    win = stand_in(busy=False, session=None, set_busy=lambda *a, **kw: None)
    for cls in [app.MainWindow, *mixins]:
        def work():
            pass

        work.__qualname__ = f"{cls.__name__}.a_task_of_the_page.<locals>.work"
        with caplog.at_level(logging.INFO, logger="openexpress.gui"):
            app.MainWindow.run_task(win, "Copying…", work)
    tasks = [r.getMessage() for r in caplog.records if r.getMessage().startswith("task: ")]
    assert tasks == ["task: MainWindow.a_task_of_the_page"] * (1 + len(mixins))
    assert len(started) == 1 + len(mixins)


def test_each_module_of_the_window_logs_under_its_own_name():
    """Every getLogger of openexpress/gui/*.py takes __name__: a line of the log names the
    module that wrote it, not the window's module."""
    import ast
    from pathlib import Path

    import openexpress.gui as gui

    named = []
    for path in sorted(Path(gui.__file__).parent.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "getLogger"
                    and not (len(node.args) == 1 and isinstance(node.args[0], ast.Name)
                             and node.args[0].id == "__name__")):
                named.append(f"{path.name}:{node.lineno}")
    assert named == []
