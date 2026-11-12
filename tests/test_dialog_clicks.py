"""The window's questions answered as a user answers them: each dialog is shown in a real
window and its button, found by its words, clicked. libadwaita 1.9 says "closed" before
"response", so an answer that reads what "closed" lets go of fails here, where emitting only
"response" would pass. Every device, app and update is made up."""

import types
import uuid

import pytest
from fakes import RunNow, device_session, software_update
from fakes import window as stand_in
from test_forget_window import _button_labelled, _turn_until

from openexpress import ciq, service
from openexpress.connect import OtherOwner

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
        device=types.SimpleNamespace(part_number="006-B0000-00", unit_id=3000000001, description="Test watch",
                                     software_version_str="23.39"),
        storage=object(), client=object(), mass_storage=False, unit_info_answer=None),
        "run_task": Tasks(), "toasts": [], "load_section": lambda key, force=False: None}
    values.update(kw)
    win = stand_in(**values)
    win.toast = win.toasts.append
    return win


# --- apps ----------------------------------------------------------------------------------


@pytest.fixture
def app_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(ciq, "remove", lambda device, storage, app: calls.append("remove"))
    monkeypatch.setattr(service, "require_account_device",
                        lambda client, unit_id, fresh=False, **_kw: calls.append("owner") or [])
    monkeypatch.setattr(ciq, "StoreApi", lambda client: types.SimpleNamespace(
        forget=lambda device, ids: calls.append("forget")))
    return calls


APP = types.SimpleNamespace(name="Face It", app_id=uuid.UUID(int=7), has_files=True,
                            direct_removal=False, sandboxed=False)


@pytest.mark.parametrize(("words", "done"), [
    ("Remove", ["remove"]), ("Remove From Device and Account", ["owner", "remove", "forget"]),
    ("Cancel", []), (ESCAPE, [])])
def test_removing_an_app(click, app_calls, words, done):
    from openexpress.gui.app import MainWindow

    win = _window(run_task=RunNow())
    MainWindow.confirm_remove_app(win, APP)
    click(words, until=lambda: app_calls == done and not click.shown[-1].get_mapped())
    assert app_calls == done


@pytest.mark.parametrize(("words", "done"), [
    ("Remove From Account", ["owner", "forget"]), ("Cancel", []), (ESCAPE, [])])
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
    ("Accept and Install", ["Installing Update…"]), ("Cancel", [])])
def test_installing_an_update_of_a_device_that_loses_its_data(click, words, tasks):
    from openexpress.gui.app import MainWindow

    u = _update()
    win = _window(updates_cache=[u])
    win.session.unit_info_answer = {"segmenttype": "Wellness"}  # the data-loss warning too
    MainWindow.confirm_update(win, u)
    click(words, until=lambda: win.run_task.titles == tasks
          and not click.shown[-1].get_mapped())


@pytest.mark.parametrize(("words", "ejected"), [("Eject", [True]), ("Later", [])])
def test_ejecting_after_a_sync_wrote_to_the_drive(click, words, ejected):
    from openexpress.gui.app import MainWindow

    asked = []
    win = _window(eject_device=lambda s, bike_accessory=False, button=None, files=False:
                  asked.append(files))
    MainWindow.ask_to_eject(win, win.session)
    assert click.shown[-1].get_heading() == "Files Saved on the Device"
    click(words, until=lambda: asked == ejected and not click.shown[-1].get_mapped())


def test_trying_again_to_eject_after_a_sync_says_the_files(click, in_the_window):
    from openexpress.device.storage import DeviceError
    from openexpress.gui.app import MainWindow

    tries = []

    def refuse(storage, **told):
        tries.append(1)
        raise DeviceError("The system refused to eject it.")

    in_the_window("eject", refuse)
    win = _window(run_task=RunNow())
    win.eject_device = (lambda s, bike_accessory=False, button=None, files=False:
                        MainWindow.eject_device(win, s, bike_accessory, button, files=files))
    win.eject_device(win.session, files=True)
    assert "may not see the files from Garmin Connect" in click.shown[-1].get_body()
    click("Try Again", until=lambda: len(tries) == 2)
    assert "may not see the files from Garmin Connect" in click.shown[-1].get_body()
    click("Close")


def test_trying_again_to_eject(click, in_the_window):
    from openexpress.device.storage import DeviceError
    from openexpress.gui.app import MainWindow

    tries = []

    def refuse(storage, **told):
        tries.append(1)
        raise DeviceError("The system refused to eject it.")

    in_the_window("eject", refuse)
    win = _window(run_task=RunNow())
    win.eject_device = (lambda s, bike_accessory=False, button=None, files=False:
                        MainWindow.eject_device(win, s, bike_accessory, button, files=files))
    win.eject_device(win.session)
    assert tries == [1]
    click("Try Again", until=lambda: len(tries) == 2)
    click("Close")
    assert len(tries) == 2


# --- the device page -----------------------------------------------------------------------


@pytest.mark.parametrize(("words", "changed"), [("Change", 1), ("Cancel", 0)])
def test_making_the_device_the_primary_training_device(click, words, changed):
    from openexpress.gui.app import MainWindow

    calls = []
    win = _window(_primary_training_text=MainWindow._primary_training_text,
                  set_primary_training=lambda button=None: calls.append(button))
    MainWindow.confirm_primary_training(win, types.SimpleNamespace(unit_id=None))
    click(words, until=lambda: len(calls) == changed and not click.shown[-1].get_mapped())


@pytest.mark.parametrize(("words", "started"), [
    ("Upload New Data Only", ["baseline"]), ("Upload Everything", ["all"]), ("Cancel", []),
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
    from openexpress.gui.preferences import ask_to_clear

    got = []
    in_the_window("clear_cache", lambda win, names, button: got.append(names))
    win = _window(_storage=types.SimpleNamespace(parts={"maps": (None, object())},
                                                 everything=(None, object())))
    ask_to_clear(win, "Clear the Downloaded Maps?", "They take 1 GB.", ["maps"])
    click(words, until=lambda: got == cleared and not click.shown[-1].get_mapped())


# --- the report ----------------------------------------------------------------------------


@pytest.mark.parametrize("attached", [True, False])
def test_open_on_github_keeps_the_report_open_only_with_the_log(click, device_root, attached):
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.gui.app import MainWindow

    storage = LocalStorage(device_root)
    session = device_session(device=parse(storage.read("GARMIN/GarminDevice.xml")),
                             storage=storage, logged_in=False, space=(None, None))
    opened = []
    win = _window(session=session, _report_view=MainWindow._report_view,
                  _open_report_form=lambda *a: opened.append(a) or attached,
                  get_width=lambda: 1000)
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
    assert MainWindow._on_close_request(win, None) is True  # asked again, as the first time


# --- Escape in every dialog of the window -------------------------------------------------------
#
# Each dialog is opened by the window's own code (OPENERS: its name in the log, as confirm() and
# inform() are given it) and closed as Escape closes it. A question then does nothing at all; a
# message runs only its follow-up, as after OK (AFTER_ESCAPE). Anything a dialog could do is
# noted in `effects`: tasks, syncs, removals, the next question. A new dialog without a case
# here fails test_every_dialog_has_an_escape_case.

INFORMS = {"close-busy", "order-none-differs", "ejected", "error", "backup-incomplete",
           "apps-queued", "add-to-account-refused", "added-not-listed", "added-not-checked",
           "update-done", "update-finish", "free-not-removed", "free-stopped",
           "free-device-changed"}
AFTER_ESCAPE = {}


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
                         _order_rows=lambda choices: [
                             ("2026-10-26", "1 app moved", choices[0])] if differs else [],
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


def _apps_queued(ctx):
    """Apps that only Garmin Connect on the phone installs: a message, not an error."""
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(service, "require_account_device", _note(ctx, "owner"))
    ctx.monkeypatch.setattr(ciq, "StoreApi", lambda client: object())
    ctx.monkeypatch.setattr(ciq, "install_apps", lambda *a, **kw: types.SimpleNamespace(
        needs_consent=[], updated_ids=[], failed=[], for_phone=["Face It"], installed=[],
        sandboxed=[], sandboxed_updates=[], no_app_list=False))
    win = _escape_window(ctx, progress=lambda message: None,
                         _for_phone_text=MainWindow._for_phone_text)
    MainWindow._install_apps(win, [types.SimpleNamespace(name="Face It")])


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


def _off_account_window(ctx):
    """A device off the account, logged in: Add to Account offered."""
    from openexpress.connect import NotAccountDevice

    win = _escape_window(ctx, refresh_device=_note(ctx, "refresh_device"))
    win.session.logged_in = True
    win.session.ownership_error = NotAccountDevice("not on the account")
    win.session.client = types.SimpleNamespace(auth=types.SimpleNamespace(display="tomek_b"))
    return win


def _add_to_account(ctx):
    from openexpress.gui.add_to_account import confirm_add_to_account

    confirm_add_to_account(_off_account_window(ctx))


def _added(ctx, listed=True, refusal=None):
    """Add to Account as Garmin answered it: the list after it (`listed`), or a refusal."""
    from openexpress.gui.add_to_account import add_to_account

    def add_device(client, device, apply=False):
        if refusal is not None:
            raise refusal
        return service.DeviceAddition(False, False, listed)

    ctx.monkeypatch.setattr(service, "add_device", add_device)
    add_to_account(_off_account_window(ctx))


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


def _course_add(ctx):
    """Add Course…: the file planned (a made-up track), then the question."""
    from pathlib import Path

    from fakes import gpx_file, gpx_track

    from openexpress import course_gpx, courses
    from openexpress.gui.app import MainWindow

    gpx = course_gpx.parse(gpx_file(gpx_track()).encode(), Path("loop.gpx"))
    plan = courses.AddPlan(gpx, "Morning Loop", gpx.data, "Morning_Loop.gpx",
                           "Garmin/NewFiles/Morning_Loop.gpx", 5_000_000)
    ctx.monkeypatch.setattr(courses, "plan_add", lambda device, storage, path, name=None: plan)
    MainWindow.add_course(_escape_window(ctx, get_width=lambda: 1000,
                                         _course_refused=lambda e: False),
                          Path("/made/up/loop.gpx"))


def _course_remove(ctx):
    """A course's Remove…: the file checked (made up), then the question."""
    from openexpress import courses
    from openexpress.gui.app import MainWindow

    shown = courses.Course("Hand.fit", "Garmin/Courses/Hand.fit", 100, "By Hand", None, None,
                           None, courses.ORIGIN_CONNECT)
    plan = courses.RemovePlan("Hand.fit", "Garmin/Courses/Hand.fit", 100, "By Hand", False,
                              courses.ORIGIN_CONNECT)
    ctx.monkeypatch.setattr(courses, "plan_remove", lambda device, storage, shown: plan)
    MainWindow.ask_remove_course(_escape_window(ctx, _course_refused=lambda e, title=None: False),
                                 shown)


def _own_map_remove(ctx):
    from openexpress.gui.app import MainWindow

    m = types.SimpleNamespace(path="Garmin/Sometown.img", size=1000, title="Sometown",
                              filename="Sometown.img")
    MainWindow.confirm_remove_own(_maps_window(ctx), m)


def _map_install(ctx):
    from openexpress.gui.app import MainWindow

    m = types.SimpleNamespace(size=1000, files=["Garmin/X.img"], eula_urls=[], installed=False,
                              name="TopoActive Made-up")
    MainWindow._ask_install_map(_maps_window(ctx), m, 1000, 1000, 5000, [])


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


def _music_remove(ctx):
    from openexpress.gui.app import MainWindow

    MainWindow.confirm_remove_music(_escape_window(ctx), "Music/Someone/Song.mp3", 3_000_000)


def _cache_clear(ctx):
    from openexpress.gui.preferences import ask_to_clear

    ctx.in_the_window("clear_cache", _note(ctx, "clear_cache"))
    win = _escape_window(ctx, _storage=types.SimpleNamespace(parts={"maps": (None, object())},
                                                             everything=(None, object())))
    ask_to_clear(win, "Clear the Downloaded Maps?", "They take 1 GB.", ["maps"])


def _update_install(ctx):
    from openexpress.gui.app import MainWindow

    u = _update()
    MainWindow.confirm_update(_escape_window(ctx, updates_cache=[u]), u)


def _update_install_all(ctx):
    import dataclasses

    from openexpress.gui.updates_page import confirm_install_all

    u = _update()
    v = dataclasses.replace(u, part_number="006-D0001-00", device_path="Garmin/NewFiles/Y.fit")
    confirm_install_all(_escape_window(ctx, updates_cache=[u, v]), [u, v])


def _eject_ask(ctx):
    from openexpress.gui.app import MainWindow

    win = _escape_window(ctx, eject_device=_note(ctx, "eject_device"))
    MainWindow.ask_to_eject(win, win.session)


def _eject_window(ctx, eject):
    from openexpress.gui.app import MainWindow

    ctx.in_the_window("eject", eject)
    win = _escape_window(ctx, error=lambda title, text: ctx.effects.append(("error", title)))
    win.eject_device = (lambda s, bike_accessory=False, button=None, files=False:
                        MainWindow.eject_device(win, s, bike_accessory, button, files=files))
    return win


def _ejected(ctx):
    win = _eject_window(ctx, lambda storage, **told: None)
    win.eject_device(win.session)  # a bike accessory's last step: test_a_bike_accessory_s_…


def _update_done(ctx):
    from openexpress.gui.updates_page import tell_update_done
    from openexpress.updates import InstallReport

    win = _escape_window(ctx)
    tell_update_done(win, win.session, InstallReport(written=[software_update()]))


def _update_finish(ctx):
    from openexpress.gui.updates_page import tell_update_finish

    tell_update_finish(_escape_window(ctx))


def _eject_retry(ctx):
    from openexpress.device.storage import DeviceError

    def refuse(storage, **told):
        raise DeviceError("The system refused to eject it.")

    win = _eject_window(ctx, refuse)
    win.eject_device(win.session)


def _error(ctx):
    from openexpress.gui.app import MainWindow

    MainWindow.error(_escape_window(ctx), "Something", "went wrong")


def _free_dumps(ctx):
    from openexpress import freeing
    from openexpress.gui.status_page import confirm_dumps

    confirm_dumps(_escape_window(ctx), freeing.dump_plan(
        [("GARMIN/Debug/dump/RAM.zip", 9_000_000, 1_700_000_300.0),
         ("GARMIN/Debug/dump/RAM_bak1.zip", 8_000_000, 1_700_000_200.0)]))


def _free_update(ctx):
    from openexpress import updates
    from openexpress.gui.status_page import confirm_remove_update

    u = updates.SoftwareUpdate(
        part_number="006-D0000-00", name="GPS software", version="6.20", severity="Recommended",
        is_primary=False, is_reinstall=False, restart_required=False,
        device_path="GARMIN/RemoteSW/006-D0000-00.GCD", url="", md5=None, size=4_100_000,
        order=0, on_device=True)
    confirm_remove_update(_escape_window(ctx), u)


def _free_not_removed(ctx):
    """After a removal the device refused in part."""
    from openexpress import freeing
    from openexpress.gui import status_page

    win = _escape_window(ctx)
    status_page._freed(win, win.session, freeing.Freed(
        removed=[("GARMIN/Debug/dump/RAM_bak2.zip", 9_000_000)],
        kept=[("GARMIN/Debug/dump/RAM_bak1.zip", "the device kept it")]), "1 removed")


def _free_stopped(ctx):
    """After a removal the device stopped responding (or could not be counted again)."""
    from openexpress import freeing
    from openexpress.gui import status_page

    win = _escape_window(ctx)
    status_page._freed(win, win.session, freeing.Freed(
        removed=[("GARMIN/Debug/dump/RAM_bak2.zip", 9_000_000)],
        stopped="the device stopped responding"), "1 removed")


def _free_device_changed(ctx):
    """A question of the page Device Status answered after the device was unplugged or opened
    again: nothing removed."""
    from openexpress.gui import status_page

    win = _escape_window(ctx)
    status_page._for_the_question(win, object(), lambda: ctx.effects.append(("removal",)))()


def _report(ctx):
    from openexpress.gui.app import MainWindow

    win = _escape_window(ctx, session=None, _report_view=MainWindow._report_view,
                         _open_report_form=_note(ctx, "open_report_form"),
                         get_width=lambda: 1000)
    MainWindow.show_report(win)


def _backup_incomplete(ctx):
    from pathlib import Path

    from openexpress import backup
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(backup, "run_backup", lambda device, storage, dest, progress=None:
                            backup.BackupResult(Path("/made/up/backups"), 3, 100, [],
                                                incomplete=1))
    MainWindow.backup(_escape_window(ctx, _closed=False, progress=lambda message: None,
                                     backup_row=None))


def _login_needs_installer(ctx):
    """Log In without the application key: the question offers the installer; Garmin's sign-in
    is never opened."""
    from openexpress.auth import sso
    from openexpress.gui.app import MainWindow

    ctx.monkeypatch.setattr(sso, "create_login_window", lambda *a, **kw: ctx.effects.append(
        ("sign-in",)) or types.SimpleNamespace(present=lambda: None))
    MainWindow.login(_escape_window(ctx, choose_dll=_note(ctx, "choose_dll")))


OPENERS = {
    "backup-incomplete": _backup_incomplete,
    "login-needs-installer": _login_needs_installer,
    "close-busy": _close_busy, "order-none-differs": lambda ctx: _order(ctx, False),
    "order-choose": lambda ctx: _order(ctx, True), "order-restore": _order_restore,
    "app-permissions": _app_permissions, "app-forget": _app_forget, "app-remove": _app_remove,
    "apps-queued": _apps_queued,
    "primary-wearable": _primary_wearable, "primary-training": _primary_training,
    "add-to-account": _add_to_account,
    "add-to-account-refused": lambda ctx: _added(ctx, refusal=OtherOwner("made up", False)),
    "added-not-listed": lambda ctx: _added(ctx, listed=False),
    "added-not-checked": lambda ctx: _added(ctx, listed=None),
    "first-sync": _first_sync, "forget-device": _forget_device, "own-map-add": _own_map_add,
    "course-add": _course_add, "course-remove": _course_remove,
    "own-map-remove": _own_map_remove, "map-install": _map_install, "map-remove": _map_remove,
    "music-copy": _music_copy, "playlist-add": _playlist_add, "music-remove": _music_remove,
    "cache-clear": _cache_clear,
    "update-install": _update_install, "update-install-all": _update_install_all,
    "eject-ask": _eject_ask, "ejected": _ejected,
    "eject-retry": _eject_retry, "error": _error, "report": _report,
    "update-done": _update_done, "update-finish": _update_finish,
    "free-dumps": _free_dumps, "free-update": _free_update, "free-not-removed": _free_not_removed,
    "free-stopped": _free_stopped, "free-device-changed": _free_device_changed,
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


def test_a_bike_accessory_s_last_step_follows_the_done_message(click, monkeypatch, in_the_window,
                                                             caplog):
    import logging

    ctx = types.SimpleNamespace(effects=[], monkeypatch=monkeypatch, in_the_window=in_the_window)
    win = _eject_window(ctx, lambda storage, **told: None)
    with caplog.at_level(logging.INFO, logger="openexpress.gui"):
        win.eject_device(win.session, True)
        click("OK", until=lambda: len(click.shown) == 2)
    assert click.shown[-1].get_heading() == "Finish the Update"
    said = [m for m in caplog.messages if m.startswith("dialog ")]
    assert said[-1].startswith("dialog update-finish shown: Finish the Update — ")


@pytest.mark.parametrize("private", [False, True])
def test_the_error_message_logs_its_words_unless_private(click, monkeypatch, in_the_window, caplog,
                                                          log_file, private):
    import logging
    from pathlib import Path

    from openexpress.gui.app import MainWindow

    ctx = types.SimpleNamespace(effects=[], monkeypatch=monkeypatch, in_the_window=in_the_window)
    with caplog.at_level(logging.INFO, logger="openexpress.gui"):
        MainWindow.error(_escape_window(ctx), "Cannot Read",
                         f"{Path.home()}/maps/x.img is unreadable.", private=private)
    said = [m for m in caplog.messages if m.startswith("dialog ")]
    assert said == (["dialog error shown"] if private
                    else ["dialog error shown: Cannot Read — <path>"])


# Their words carry names of the user's files, maps, music or courses: only the name of the
# dialog goes to the log. An error() whose words do, too (the music page's).
PRIVATE_DIALOGS = {"course-add", "course-remove", "own-map-add", "own-map-remove", "music-copy",
                   "playlist-add", "music-remove", "backup-incomplete"}
PRIVATE_ERRORS = {"music_page.py"}


def test_dialogs_with_the_user_s_names_are_private_in_the_log():
    import ast
    from pathlib import Path

    gui = Path(__file__).resolve().parent.parent / "openexpress" / "gui"
    seen, wrong = set(), []
    for path in sorted(gui.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            private = any(k.arg == "private" and isinstance(k.value, ast.Constant)
                          and k.value.value is True for k in node.keywords)
            if called in ("confirm", "inform"):
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                    wrong.append(f"{path.name}:{node.lineno}: the name of the dialog is no literal")
                    continue
                name = node.args[1].value
                seen.add(name)
                if private != (name in PRIVATE_DIALOGS):
                    wrong.append(f"{path.name}:{node.lineno}: {name} private={private}")
            elif called == "error" and private and path.name not in PRIVATE_ERRORS:
                wrong.append(f"{path.name}:{node.lineno}: error() private outside {PRIVATE_ERRORS}")
            elif called == "error" and not private and path.name in PRIVATE_ERRORS:
                wrong.append(f"{path.name}:{node.lineno}: error() not private")
    assert wrong == []
    assert seen >= PRIVATE_DIALOGS


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
