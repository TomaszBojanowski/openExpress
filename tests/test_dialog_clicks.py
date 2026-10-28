"""Guard 4 of splitting gui/app.py (analysis 17): the questions of the window answered as a
user answers them. Each dialog is shown in a real window and its button, found by its words,
is clicked: libadwaita then says "closed" before "response" (1.9, 25.10.2026), so an answer
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
    win.session.unit_info_answer = {"segmenttype": "Wellness"}  # the warning of point 6 too
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
