"""While a task works with the device: what the window and the command line say, and widgets
freed on the GTK thread."""
import types

import pytest
from fakes import (
    Widget,
    device_session,
    window_methods,
)
from fakes import window as stand_in

# --- "Do not unplug the device" while a task works with the device ----------------------------

def _busy_window(plugged=True):

    win = stand_in(
        session=device_session() if plugged else None, busy=False, action_bar=Widget(), progress_label=Widget(),
        progress_bar=Widget(), unplug_label=Widget(), banner=Widget(), _action_buttons=[],
        _update_actions=lambda: None, _run_pending_refresh=lambda: None, toasts=[], shown=[],
        _waiting=0, busy_spinner=Widget())
    win.toast = win.toasts.append
    window_methods(win, ["_stop_waiting", "set_busy", "run_task"])
    return win


def _run_now(monkeypatch):
    """Tasks run at once instead of in a thread; what the label showed meanwhile is kept."""
    import threading

    from gi.repository import GLib

    class Now:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Thread", Now)
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *args: fn(*args))


def test_window_says_not_to_unplug_while_a_task_works_with_the_device(monkeypatch):
    _run_now(monkeypatch)
    win = _busy_window()
    during = []
    win.run_task("Syncing…", lambda: during.append((win.busy, win.unplug_label.visible,
                                                    win.action_bar.visible)))
    assert during == [(True, True, True)]  # shown for as long as the task runs
    assert (win.busy, win.unplug_label.visible, win.action_bar.visible) == (False, False, False)


def test_warning_goes_away_after_a_failed_task_too(monkeypatch):
    _run_now(monkeypatch)
    win = _busy_window()
    seen = []

    def fail():
        seen.append(win.unplug_label.visible)
        raise RuntimeError("the device went away")

    win.run_task("Copying Music…", fail, on_error=lambda e: True)
    assert seen == [True] and win.unplug_label.visible is False and win.busy is False


@pytest.mark.parametrize("plugged, device", [(True, False), (False, True)])
def test_no_warning_for_a_task_that_does_not_touch_a_device(monkeypatch, plugged, device):
    _run_now(monkeypatch)
    win = _busy_window(plugged)  # e.g. logging in, or no device is open
    during = []
    win.run_task("Signing In…", lambda: during.append(win.unplug_label.visible), device=device)
    assert during == [False] and win.action_bar.visible is False


@pytest.mark.parametrize("argv, expected", [
    (["sync", "--apply"], True), (["backup", "--apply"], True),
    (["apps", "install", "X", "--apply"], True), (["music", "add", "x.mp3", "--apply"], True),
    (["maps", "update", "--apply"], True), (["updates", "--install", "P", "--apply"], True),
    (["sync"], False), (["apps", "install", "X"], False),      # dry runs
    (["primary", "set", "--apply"], False), (["info"], False),  # account only / read in a moment
])
def test_cli_says_not_to_unplug_only_when_it_really_works_on_the_device(monkeypatch, capsys,
                                                                       argv, expected):
    from openexpress import cli
    from openexpress.device import gio_storage

    monkeypatch.setattr(gio_storage, "open_device_storage",
                        lambda uri=None, wait=False, notify=None: "storage")
    args = cli.build_parser().parse_args(argv)
    assert cli.open_storage(args) == "storage"
    out = capsys.readouterr()
    line = "Do not unplug the device until the operation is finished.\n"
    assert out.out == "" and out.err == (line if expected else "")


def test_cli_does_not_warn_for_a_folder_instead_of_a_device(tmp_path, capsys):
    from openexpress import cli

    args = cli.build_parser().parse_args(["--path", str(tmp_path), "sync", "--apply"])
    cli.open_storage(args)
    assert capsys.readouterr().err == ""


# --- widgets are destroyed on the GTK thread ------------------------------------------------


def _gtk():
    """GTK 4; the tests that use it are marked needs_display (skipped without a display)."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    return Gtk


def _find(widget, kind):
    if isinstance(widget, kind):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find(child, kind)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _text(buffer) -> str:
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)


@pytest.mark.needs_display
def test_report_preview_does_not_keep_itself_alive(log_file):
    """The "attach the log" switch does not refer to itself through its own handler, or
    the preview would outlive the dialog until Python next collected reference cycles."""
    import gc
    import weakref

    from openexpress.gui.app import MainWindow

    Gtk = _gtk()
    log_file.parent.mkdir(parents=True)
    log_file.write_text("a line of the log\n")
    gc.collect()
    gc.disable()
    try:
        box, buffer = MainWindow._report_view("### Device\n")
        switch, view = _find(box, Gtk.CheckButton), _find(box, Gtk.TextView)
        assert _text(buffer) == "### Device\n" and not switch.get_active()  # off by default
        switch.set_active(True)
        assert _text(buffer) == (
            "### Device\n\n### Log (last 1 lines)\n```\na line of the log\n```\n")
        switch.set_active(False)
        assert _text(buffer) == "### Device\n"

        alive = [weakref.ref(switch), weakref.ref(view), weakref.ref(box)]
        del box, buffer, switch, view
        # Freed at once, by reference counting alone: nothing is left for a later collection.
        assert [ref() for ref in alive] == [None, None, None]
    finally:
        gc.enable()


def test_window_collects_cycles_itself_on_the_gtk_thread(monkeypatch):
    import gc
    import threading

    from gi.repository import GLib

    from openexpress.gui import app
    from openexpress.gui.window_tasks import collect_garbage

    calls = []
    monkeypatch.setattr(gc, "disable", lambda: calls.append("automatic collection off"))
    monkeypatch.setattr(GLib, "timeout_add_seconds", lambda s, fn: calls.append((s, fn)))
    app.collect_garbage_on_the_gtk_thread()
    assert calls == ["automatic collection off", (app.GC_INTERVAL_S, collect_garbage)]
    assert collect_garbage() is True  # the timer keeps running

    # After every task, successful or not, in the thread that ends it (the GTK thread).
    _run_now(monkeypatch)
    where = []
    monkeypatch.setattr(gc, "collect", lambda: where.append(threading.current_thread()))
    win = _busy_window()
    win.run_task("Syncing…", lambda: None)
    win.run_task("Syncing…", lambda: 1 / 0, on_error=lambda e: True)
    assert where == [threading.main_thread()] * 2


@pytest.mark.needs_display
def test_updates_page_with_several_updates(monkeypatch):
    """The page as it was when the window crashed: three updates, one of them with a list of
    changes, and a component that can be reinstalled."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow
    from openexpress.updates import SoftwareUpdate

    _gtk()

    def update(part, name, version, reinstall=False, changes=(), datatype="Firmware",
               severity="Recommended"):
        return SoftwareUpdate(part, name, version, severity, False, reinstall, False,
                              f"Garmin/SID/{part}.sid", "https://example.org/x", None, 2048, 0,
                              changes=list(changes), datatype=datatype)

    items = [update("006-D0001-00", "Etyp", "2.70", changes=["Better <maps> & more", "Fixes"],
                    datatype="Etyp"),
             update("006-D0002-00", "Voice <pack>", "2.40", severity="Critical"),
             update("006-D0003-00", "Thing", "2.60", datatype="NewKind", severity="Optional"),
             update("006-D0004-00", "Sensor Hub", "9.10", reinstall=True),
             update("006-D0005-META", "Metadata", "1.00"),
             update("006-D0006-00", "Etyp", "1.10", datatype="Etyp")]
    items[-1].on_device = True  # offered by the server, but the device has this very file

    class Group:
        def __init__(self, title):
            self.title, self.rows = title, []

        def add(self, row):
            self.rows.append(row)

    class Section:
        groups = []

        def group(self, title="", description=""):
            self.groups.append(Group(title))
            return self.groups[-1]

    states = []
    win = stand_in(
        session=device_session(device=types.SimpleNamespace(
            description="Test watch", software_version_str="23.39")),
        sections={"updates": Section()}, busy=False, _action_buttons=[],
        _set_update_state=lambda new, staged=(), not_installed=(): states.append(len(new)), confirm_update=lambda u: None,
        load_section=lambda *a, **kw: None)
    window_methods(win, ["_update_row", "_updates_status", "_button"])
    MainWindow.show_updates(win, items)

    status, available, advanced = win.sections["updates"].groups  # the status on top
    assert states == [3] and win.updates_cache == items
    assert available.title == "Available Updates"
    assert [(r.get_title(), r.get_subtitle()) for r in available.rows] == [
        # The server only repeats the type: a readable name. Another "Map support file" (the
        # Etyp below, a reinstall) has the same title, so both keep their file's name; the
        # other titles differ and leave it out.
        ("Map support file", "2.70\u00a0· 2.0\u00a0kB\u00a0· Recommended\u00a0· 006-D0001-00.sid"),
        ("Voice &lt;pack&gt;", "2.40\u00a0· 2.0\u00a0kB\u00a0· Critical"),
        ("Thing", "2.60\u00a0· 2.0\u00a0kB\u00a0· Optional")]  # unknown values stay raw
    assert advanced.title == "Advanced" and isinstance(advanced.rows[0], Adw.ExpanderRow)
    # The file that is already on the device is listed with the reinstalls, not as an update.
    assert advanced.rows[0].get_subtitle().startswith("2 components already on the device")
    # Three "Install", two "Reinstall", "Install All" and "Check Again" in the status.
    assert len(win._action_buttons) == 7
