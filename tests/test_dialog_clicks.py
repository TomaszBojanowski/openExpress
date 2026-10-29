"""The questions of the window answered as a
user answers them. Each dialog is shown in a real window and its button, found by its words,
is clicked: libadwaita then says "closed" before "response" (1.9), so an answer
that reads what "closed" lets go of fails here, where a test that only emits "response" would
pass. Every device, app and update here is made up."""

import types
import uuid

import pytest
from fakes import RunNow
from test_forget_window import _button_labelled, _turn_until

from openexpress import ciq, service

pytestmark = pytest.mark.needs_display

ESCAPE = "Escape"  # not a button: the dialog closed as Escape closes it (Adw.Dialog.close)


@pytest.fixture
def click(monkeypatch):
    """click(words): the newest dialog shown, its button with these words clicked (ESCAPE:
    closed as Escape closes it), the loop turned until it is gone (or `until`)."""
    from gi.repository import Adw, Gtk

    host = Gtk.Window()
    host.present()
    shown = []
    present = Adw.AlertDialog.present
    monkeypatch.setattr(Adw.AlertDialog, "present",
                        lambda self, parent: (shown.append(self), present(self, host)))

    def answer(words, until=None):
        dialog = shown[-1]
        assert _turn_until(dialog.get_mapped)
        if words == ESCAPE:
            dialog.close()
        else:
            button = _button_labelled(dialog, words)
            assert button is not None, f"no button {words!r}"
            button.emit("clicked")
        assert _turn_until(until or (lambda: not dialog.get_mapped()))
        return dialog

    answer.shown = shown
    yield answer
    for dialog in shown:
        dialog.force_close()
    host.destroy()


class Tasks:
    """run_task that only notes the titles of the tasks it is given."""

    def __init__(self):
        self.titles = []

    def __call__(self, title, work, done=None, failed=None, **_kw):
        self.titles.append(title)


def _window(**kw):
    values = {"session": types.SimpleNamespace(
        device=types.SimpleNamespace(unit_id=3000000001, description="Test watch",
                                     software_version_str="23.39"),
        storage=object(), client=object(), mass_storage=False, unit_info_answer=None),
        "run_task": Tasks(), "toasts": [], "load_section": lambda key, force=False: None}
    values.update(kw)
    win = types.SimpleNamespace(**values)
    win.toast = win.toasts.append
    return win


# --- apps ----------------------------------------------------------------------------------


@pytest.fixture
def app_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(ciq, "remove", lambda device, storage, app: calls.append("remove"))
    monkeypatch.setattr(service, "require_account_device",
                        lambda client, unit_id: calls.append("owner") or [])
    monkeypatch.setattr(ciq, "StoreApi", lambda client: types.SimpleNamespace(
        forget=lambda device, ids: calls.append("forget")))
    return calls


APP = types.SimpleNamespace(name="Face It", app_id=uuid.UUID(int=7), has_files=True,
                            direct_removal=False, sandboxed=False)


@pytest.mark.parametrize(("words", "done"), [
    ("Remove", ["remove"]), ("Remove and forget", ["owner", "remove", "forget"]),
    ("Cancel", []), (ESCAPE, [])])
def test_removing_an_app(click, app_calls, words, done):
    from openexpress.gui.app import MainWindow

    win = _window(run_task=RunNow())
    MainWindow.confirm_remove_app(win, APP)
    click(words, until=lambda: app_calls == done and not click.shown[-1].get_mapped())
    assert app_calls == done


@pytest.mark.parametrize(("words", "done"), [
    ("Remove from Account", ["owner", "forget"]), ("Cancel", []), (ESCAPE, [])])
def test_removing_an_app_from_the_account(click, app_calls, words, done):
    from openexpress.gui.app import MainWindow

    win = _window(run_task=RunNow(), _forget_text=MainWindow._forget_text)
    MainWindow.confirm_forget_app(win, types.SimpleNamespace(name="Face It",
                                                             app_id=uuid.UUID(int=7)))
    click(words, until=lambda: app_calls == done and not click.shown[-1].get_mapped())


@pytest.mark.parametrize(("words", "restored"), [("Restore the Order", 1), ("Cancel", 0)])
def test_restoring_the_order_of_the_apps(click, monkeypatch, words, restored):
    from openexpress.gui.app import MainWindow

    copies = []
    monkeypatch.setattr(ciq, "copy_title", lambda copy: "2026-10-26 (22.22.08)")
    win = _window(_order_summary=lambda plan, note: "3 apps moved",
                  restore_order=copies.append)
    copy = object()
    MainWindow.confirm_order(win, copy, object(), "")
    click(words, until=lambda: len(copies) == restored and not click.shown[-1].get_mapped())
    assert copies == [copy] * restored


@pytest.mark.parametrize(("words", "installed"), [
    ("Allow", [(["Plain", "Face It"], {uuid.UUID(int=8)})]),
    ("Skip These Apps", [(["Plain"], set())]), ("Cancel", [])])
def test_allowing_the_new_permissions_of_an_app(click, words, installed):
    from openexpress.gui.app import MainWindow

    plain = types.SimpleNamespace(name="Plain", app_id=uuid.UUID(int=9), version_name="1.0",
                                  permissions_changed=False, permissions=())
    asking = types.SimpleNamespace(name="Face It", app_id=uuid.UUID(int=8), version_name="2.0",
                                   permissions_changed=True, permissions=("Positioning",))
    got = []
    win = _window(_install_apps=lambda apps, allowed=(): got.append(
        ([a.name for a in apps], set(allowed))))
    MainWindow.confirm_permissions(win, [plain, asking], [asking])
    click(words, until=lambda: got == installed and not click.shown[-1].get_mapped())


# --- updates -------------------------------------------------------------------------------


def _update():
    from openexpress import updates

    return updates.SoftwareUpdate(
        part_number="006-D0000-00", name="Made-up file", version="2.00",
        severity="Recommended", is_primary=False, is_reinstall=False, restart_required=False,
        device_path="Garmin/NewFiles/X.fit", url="", md5=None, size=100, order=0,
        on_device=False, release_state="live")


@pytest.mark.parametrize(("words", "tasks"), [
    ("Accept and install", ["Installing update…"]), ("Cancel", [])])
def test_installing_an_update_of_a_device_that_loses_its_data(click, words, tasks):
    from openexpress.gui.app import MainWindow

    u = _update()
    win = _window(updates_cache=[u])
    win.session.unit_info_answer = {"segmenttype": "Wellness"}  # the data-loss warning too
    MainWindow.confirm_update(win, u)
    click(words, until=lambda: win.run_task.titles == tasks
          and not click.shown[-1].get_mapped())


@pytest.mark.parametrize(("words", "ejected"), [("Eject", 1), ("Later", 0)])
def test_ejecting_after_an_update_was_saved(click, words, ejected):
    from openexpress.gui.app import MainWindow

    asked = []
    win = _window(eject_device=lambda s, bike_accessory=False: asked.append(s))
    MainWindow.ask_to_eject(win, win.session)
    click(words, until=lambda: len(asked) == ejected and not click.shown[-1].get_mapped())


def test_trying_again_to_eject(click, in_the_window):
    from openexpress.device.storage import DeviceError
    from openexpress.gui.app import MainWindow

    tries = []

    def refuse(storage):
        tries.append(1)
        raise DeviceError("The system refused to eject it.")

    in_the_window("eject", refuse)
    win = _window(run_task=RunNow())
    win.eject_device = lambda s, bike_accessory=False, button=None: MainWindow.eject_device(
        win, s, bike_accessory, button)
    win.eject_device(win.session)
    assert tries == [1]
    click("Try Again", until=lambda: len(tries) == 2)
    click("Close")
    assert len(tries) == 2


# --- the device page -----------------------------------------------------------------------


@pytest.mark.parametrize(("words", "changed"), [("Set", 1), ("Cancel", 0)])
def test_making_the_device_the_primary_training_device(click, words, changed):
    from openexpress.gui.app import MainWindow

    calls = []
    win = _window(_primary_training_text=MainWindow._primary_training_text,
                  set_primary_training=lambda button=None: calls.append(button))
    MainWindow.confirm_primary_training(win, types.SimpleNamespace(unit_id=None))
    click(words, until=lambda: len(calls) == changed and not click.shown[-1].get_mapped())


@pytest.mark.parametrize(("words", "started"), [
    ("New data only", ["baseline"]), ("Upload everything", ["all"]), ("Cancel", []),
    (ESCAPE, [])])
def test_choosing_the_first_sync(click, words, started):
    from openexpress.gui.app import MainWindow

    syncs = []
    win = _window(start_sync=syncs.append)
    MainWindow.ask_first_sync(win)
    click(words, until=lambda: syncs == started and not click.shown[-1].get_mapped())


# --- the preferences -----------------------------------------------------------------------


@pytest.mark.parametrize(("words", "cleared"), [("Clear", [["maps"]]), ("Cancel", [])])
def test_clearing_a_part_of_the_cache(click, words, cleared, in_the_window):
    from openexpress.gui import app

    got = []
    in_the_window("clear_cache", lambda win, names, button: got.append(names))
    win = _window(_storage=types.SimpleNamespace(parts={"maps": (None, object())},
                                                 everything=(None, object())))
    app.ask_to_clear(win, "Clear the downloaded maps?", "They take 1 GB.", ["maps"])
    click(words, until=lambda: got == cleared and not click.shown[-1].get_mapped())


# --- the report ----------------------------------------------------------------------------


@pytest.mark.parametrize("attached", [True, False])
def test_open_on_github_keeps_the_report_open_only_with_the_log(click, device_root, attached):
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.gui.app import MainWindow

    storage = LocalStorage(device_root)
    session = types.SimpleNamespace(device=parse(storage.read("GARMIN/GarminDevice.xml")),
                                    storage=storage, logged_in=False, space=(None, None))
    opened = []
    win = _window(session=session, _report_view=MainWindow._report_view,
                  _open_report_form=lambda *a: opened.append(a) or attached)
    MainWindow.show_report(win)
    dialog = click.shown[-1]
    if attached:  # it stays to say where the log is, and Escape closes it then
        click("Open on GitHub", until=lambda: bool(opened))
        assert _turn_until(lambda: False, 0.3) is False and dialog.get_mapped()
        dialog.close()
        assert _turn_until(lambda: not dialog.get_mapped())
    else:
        click("Open on GitHub")
        assert opened


# --- the window ----------------------------------------------------------------------------


def test_closing_while_a_task_runs_asks_to_wait(click):
    from openexpress.gui.app import MainWindow

    win = _window(busy=True, _close_dialog=None, _task_lets_close=False)
    assert MainWindow._on_close_request(win, None) is True  # the window stays
    assert win._close_dialog is not None
    click("OK", until=lambda: win._close_dialog is None and not click.shown[-1].get_mapped())
    assert MainWindow._on_close_request(win, None) is True  # asked again, as before


# --- Escape in every dialog of the window -------------------------------------------------------
#
# Each dialog is opened by the window's own code (OPENERS: its name in the log, as confirm() and
# inform() are given it) and closed as Escape closes it. A question then does nothing at all; a
# message runs only its follow-up, as after OK (AFTER_ESCAPE). Anything a dialog could do is
# noted in `effects`: tasks, syncs, removals, the next question. A new dialog without a case
# here fails test_every_dialog_has_an_escape_case.

INFORMS = {"close-busy", "order-none-differs", "ejected", "error"}
AFTER_ESCAPE = {"ejected": [("error", "Finish the update")]}  # a bike accessory's last step


def _escape_window(ctx, **kw):
    return _window(run_task=RunNow(on_call=lambda call: ctx.effects.append(("task", call.title))),
                   **kw)


def _note(ctx, what):
    return lambda *_a, **_kw: ctx.effects.append(what)


def _close_busy(ctx):
    from openexpress.gui.app import MainWindow

    win = _escape_window(ctx, busy=True, _close_dialog=None, _task_lets_close=False)
    MainWindow._on_close_request(win, None)


def _order(ctx, differs):
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(ciq, "read_installed", lambda device, storage: object())
    ctx.monkeypatch.setattr(ciq, "order_choices", lambda device, state: [("copy", "plan")])
    ctx.monkeypatch.setattr(ciq, "copy_note", lambda made: "")
    win = _escape_window(ctx, _order_blocked=lambda state: None,
                         _order_rows=lambda choices: [("2026-10-26", "1 app moved", differs)],
                         confirm_order=_note(ctx, "confirm_order"))
    MainWindow.choose_order(win)


def _order_restore(ctx):
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(ciq, "copy_title", lambda copy: "2026-10-26 (22.22.08)")
    win = _escape_window(ctx, _order_summary=lambda plan, note: "3 apps moved",
                         restore_order=_note(ctx, "restore_order"))
    MainWindow.confirm_order(win, object(), object(), "")


def _app_permissions(ctx):
    from openexpress.gui.app import MainWindow

    plain = types.SimpleNamespace(name="Plain", app_id=uuid.UUID(int=9), version_name="1.0",
                                  permissions_changed=False, permissions=())
    asking = types.SimpleNamespace(name="Face It", app_id=uuid.UUID(int=8), version_name="2.0",
                                   permissions_changed=True, permissions=("Positioning",))
    win = _escape_window(ctx, _install_apps=_note(ctx, "install_apps"))
    MainWindow.confirm_permissions(win, [plain, asking], [asking])


def _app_calls(ctx):
    ctx.monkeypatch.setattr(ciq, "remove", _note(ctx, "remove"))
    ctx.monkeypatch.setattr(service, "require_account_device", _note(ctx, "owner"))
    ctx.monkeypatch.setattr(ciq, "StoreApi", lambda client: types.SimpleNamespace(
        forget=_note(ctx, "forget")))


def _app_forget(ctx):
    from openexpress.gui.app import MainWindow

    _app_calls(ctx)
    win = _escape_window(ctx, _forget_text=MainWindow._forget_text)
    MainWindow.confirm_forget_app(win, types.SimpleNamespace(name="Face It",
                                                             app_id=uuid.UUID(int=7)))


def _app_remove(ctx):
    from openexpress.gui.app import MainWindow

    _app_calls(ctx)
    MainWindow.confirm_remove_app(_escape_window(ctx), APP)


def _primary_wearable(ctx):
    from openexpress.gui.device_page import confirm_primary_wearable

    ctx.in_the_window("set_primary_wearable", _note(ctx, "set_primary_wearable"))
    wearable = types.SimpleNamespace(set=True, is_this_device=False, name="Other watch")
    confirm_primary_wearable(_escape_window(ctx), wearable)


def _primary_training(ctx):
    from openexpress.gui.app import MainWindow

    win = _escape_window(ctx, _primary_training_text=MainWindow._primary_training_text,
                         set_primary_training=_note(ctx, "set_primary_training"))
    MainWindow.confirm_primary_training(win, types.SimpleNamespace(unit_id=None))


def _first_sync(ctx):
    from openexpress.gui.app import MainWindow

    MainWindow.ask_first_sync(_escape_window(ctx, start_sync=_note(ctx, "start_sync")))


def _forget_device(ctx):
    from test_forget_window import remembered, window

    remembered()
    win = window()
    win._forget_answered = _note(ctx, "forget_device")
    win.ask_forget()


def _maps_window(ctx, **kw):
    return _escape_window(ctx, maps_cache=[], map_cleanup=None, fraction=lambda *a: None,
                          _own_map_refused=lambda e, what: False, **kw)


def _own_map_add(ctx):
    from openexpress import ownmaps
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(ownmaps, "plan_add",
                            lambda device, storage, garmin_maps, path: types.SimpleNamespace())
    MainWindow.add_own_map(_maps_window(ctx, _add_map_text=lambda plan: "Copy it?"),
                           "/made/up/Sometown.img")


def _own_map_remove(ctx):
    from openexpress.gui.app import MainWindow

    m = types.SimpleNamespace(path="Garmin/Sometown.img", size=1000, title="Sometown",
                              filename="Sometown.img")
    MainWindow.confirm_remove_own(_maps_window(ctx), m)


def _map_install(ctx):
    from openexpress.gui.app import MainWindow

    m = types.SimpleNamespace(size=1000, files=["Garmin/X.img"], eula_urls=[], installed=False,
                              name="TopoActive Made-up")
    MainWindow._ask_install_map(_maps_window(ctx), m, 1000, 5000, [])


def _map_remove(ctx):
    from openexpress import maps
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(maps, "removal_list",
                            lambda cache, m, rewriting=False: ["Garmin/X.img"])
    MainWindow.confirm_remove(_maps_window(ctx), types.SimpleNamespace(name="Made-up map"))


def _music_copy(ctx):
    from pathlib import Path

    from openexpress import music
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(music, "scan", lambda paths, device, forced_kind=None: [])
    ctx.monkeypatch.setattr(music, "plan_copy", lambda tracks, device, storage:
                            types.SimpleNamespace(to_copy=["one"]))
    win = _escape_window(ctx, _copy_summary=lambda plan: "1 file", fraction=lambda *a: None)
    MainWindow.add_music(win, [Path("/made/up/Song.mp3")], "track")


def _playlist_add(ctx):
    from openexpress import music
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(music, "plan_playlist", lambda path, device, storage:
                            types.SimpleNamespace(device_paths=["Music/Song.mp3"], name="Mine"))
    win = _escape_window(ctx, _playlist_summary=lambda plan, flatpak: "1 track",
                         fraction=lambda *a: None)
    MainWindow.add_playlist(win, "/made/up/Mine.m3u")


def _cache_clear(ctx):
    from openexpress.gui import app

    ctx.in_the_window("clear_cache", _note(ctx, "clear_cache"))
    win = _escape_window(ctx, _storage=types.SimpleNamespace(parts={"maps": (None, object())},
                                                             everything=(None, object())))
    app.ask_to_clear(win, "Clear the downloaded maps?", "They take 1 GB.", ["maps"])


def _update_install(ctx):
    from openexpress.gui.app import MainWindow

    u = _update()
    MainWindow.confirm_update(_escape_window(ctx, updates_cache=[u]), u)


def _eject_ask(ctx):
    from openexpress.gui.app import MainWindow

    win = _escape_window(ctx, eject_device=_note(ctx, "eject_device"))
    MainWindow.ask_to_eject(win, win.session)


def _eject_window(ctx, eject):
    from openexpress.gui.app import MainWindow

    ctx.in_the_window("eject", eject)
    win = _escape_window(ctx, error=lambda title, text: ctx.effects.append(("error", title)))
    win.eject_device = lambda s, bike_accessory=False, button=None: MainWindow.eject_device(
        win, s, bike_accessory, button)
    return win


def _ejected(ctx):
    win = _eject_window(ctx, lambda storage: None)
    win.eject_device(win.session, True)  # a bike accessory: its last step follows


def _eject_retry(ctx):
    from openexpress.device.storage import DeviceError

    def refuse(storage):
        raise DeviceError("The system refused to eject it.")

    win = _eject_window(ctx, refuse)
    win.eject_device(win.session)


def _error(ctx):
    from openexpress.gui.app import MainWindow

    MainWindow.error(_escape_window(ctx), "Something", "went wrong")


def _report(ctx):
    from openexpress.gui.app import MainWindow

    win = _escape_window(ctx, session=None, _report_view=MainWindow._report_view,
                         _open_report_form=_note(ctx, "open_report_form"))
    MainWindow.show_report(win)


OPENERS = {
    "close-busy": _close_busy, "order-none-differs": lambda ctx: _order(ctx, False),
    "order-choose": lambda ctx: _order(ctx, True), "order-restore": _order_restore,
    "app-permissions": _app_permissions, "app-forget": _app_forget, "app-remove": _app_remove,
    "primary-wearable": _primary_wearable, "primary-training": _primary_training,
    "first-sync": _first_sync, "forget-device": _forget_device, "own-map-add": _own_map_add,
    "own-map-remove": _own_map_remove, "map-install": _map_install, "map-remove": _map_remove,
    "music-copy": _music_copy, "playlist-add": _playlist_add, "cache-clear": _cache_clear,
    "update-install": _update_install, "eject-ask": _eject_ask, "ejected": _ejected,
    "eject-retry": _eject_retry, "error": _error, "report": _report,
}


@pytest.mark.parametrize("name", sorted(OPENERS))
def test_escape_in_every_dialog_does_nothing(click, monkeypatch, in_the_window, caplog, name):
    import logging

    ctx = types.SimpleNamespace(effects=[], monkeypatch=monkeypatch, in_the_window=in_the_window)
    with caplog.at_level(logging.INFO, logger="openexpress.gui"):
        OPENERS[name](ctx)
        assert click.shown, f"{name}: no dialog shown"
        before, shown = list(ctx.effects), len(click.shown)
        caplog.clear()
        click(ESCAPE)
    assert ctx.effects[len(before):] == AFTER_ESCAPE.get(name, [])
    assert len(click.shown) == shown  # no other dialog
    said = [m for m in caplog.messages if m.startswith("dialog ")]
    if name == "report":  # its own dialog (it stays open for its answers): no line
        assert said == []
    else:
        assert said == [f"dialog {name}: {'ok' if name in INFORMS else 'cancelled'}"]


# --- the source of the window: every dialog through confirm() or inform() ----------------------

HELPERS = {("window_tasks.py", "confirm"), ("window_tasks.py", "inform")}
# The report stays open for Copy and for Open on GitHub with the log: no question of confirm()
# fits it. Its Escape is in test_escape_in_every_dialog_does_nothing.
REPORT = ("report_dialog.py", "show_report")


def _calls_in_the_window():
    """(file, enclosing function, call) of every call in openexpress/gui/*.py."""
    import ast
    from pathlib import Path

    import openexpress.gui as gui

    found = []

    def walk(file, node, function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and function is None:
                walk(file, child, child.name)
                continue
            if isinstance(child, ast.Call):
                found.append((file, function, child))
            walk(file, child, function)

    for path in sorted(Path(gui.__file__).parent.glob("*.py")):
        walk(path.name, ast.parse(path.read_text(encoding="utf-8")), None)
    return found


def _called(call) -> str:
    """The name called: `f` of f(…) and of x.f(…)."""
    return getattr(call.func, "attr", None) or getattr(call.func, "id", "")


def test_dialogs_are_made_and_answered_only_by_the_helpers():
    """Adw.AlertDialog(…), connect("response", …) and set_close_response only in confirm(),
    inform() and the report: a dialog built by hand elsewhere would close as it likes."""
    import ast

    places = set()
    for file, function, call in _calls_in_the_window():
        name = _called(call)
        answered = (name == "connect" and call.args and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == "response")
        if name in ("AlertDialog", "set_close_response") or answered:
            places.add((file, function))
    assert places <= HELPERS | {REPORT}, sorted(places - HELPERS - {REPORT})


def test_every_dialog_has_a_name_of_its_own_and_an_escape_case():
    """The names of confirm() and inform() are literal and none twice, and each has its case
    in OPENERS (with the report's): a new dialog comes with its Escape tested."""
    import ast

    names = []
    for file, function, call in _calls_in_the_window():
        if _called(call) in ("confirm", "inform") and (file, function) not in HELPERS:
            assert len(call.args) >= 2 and isinstance(call.args[1], ast.Constant), (
                f"{file}: a dialog without a literal name")
            names.append(call.args[1].value)
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)
    assert set(names) | {"report"} == set(OPENERS)
