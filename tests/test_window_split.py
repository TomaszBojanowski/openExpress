"""The window in several modules (review of 24.10.2026, area 7): the pages split out of
gui/app.py are mixins of MainWindow, and what the window says of itself stays as it was."""

import logging
import types


def test_a_task_of_a_mixin_of_the_window_is_logged_as_the_windows(caplog, in_the_window):
    """The line "task: MainWindow.add_music" of the log names the method that made the work
    of the task; a method of a mixin (MusicPage, MapsPage, …) is still MainWindow's there."""
    from openexpress.gui import app

    mixins = [c for c in app.MainWindow.__mro__[1:] if c.__module__.startswith("openexpress.")]
    assert mixins  # the window has at least one
    started = []
    in_the_window("threading", types.SimpleNamespace(
        Thread=lambda target, daemon: types.SimpleNamespace(start=lambda: started.append(1))))
    win = types.SimpleNamespace(busy=False, session=None, set_busy=lambda *a, **kw: None)
    for cls in [app.MainWindow, *mixins]:
        def work():
            pass

        work.__qualname__ = f"{cls.__name__}.a_task_of_the_page.<locals>.work"
        with caplog.at_level(logging.INFO, logger="openexpress.gui.app"):
            app.MainWindow.run_task(win, "Copying…", work)
    tasks = [r.getMessage() for r in caplog.records if r.getMessage().startswith("task: ")]
    assert tasks == ["task: MainWindow.a_task_of_the_page"] * (1 + len(mixins))
    assert len(started) == 1 + len(mixins)
