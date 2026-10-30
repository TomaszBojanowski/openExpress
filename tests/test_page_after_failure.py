"""A task that changes the Maps, Apps or Music page reads it again when it ends, also after a
failure, since a part may be done (a map's files written but not its unlock file, one song of
two copied, the waiting app list gone); the error is said over the page as read. All data is
made up."""

import types
from pathlib import Path

import pytest
from fakes import RunNow
from fakes import window as stand_in
from test_ciq_music import FACE, PRELOADED, STORE_ID, iq_device, library  # noqa: F401
from test_in_bin_writes import Breaking
from test_updates_maps import FILES, _real_maps_page, maps_response, server  # noqa: F401
from test_window_device_calls import _map, _rows, _turn_until

from openexpress import ciq, maps, music, service
from openexpress.device.storage import DeviceError, LocalStorage

GONE = "Cannot write it: the device is gone"


# --- the Maps page of the real kind, with the window's own run_task -------------------------------

def _real_tasks(win) -> None:
    """run_task of the window for real: the work in a thread, its end on the window's loop
    (busy off, then done or failed, then the error when failed did not say it)."""
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    win.busy, win._waiting, win._refresh_pending = False, 0, False
    win.action_bar, win.progress_label = Gtk.Revealer(reveal_child=False), Gtk.Label()
    win.progress_bar, win.busy_spinner = Gtk.ProgressBar(), Gtk.Spinner()
    win.unplug_label, win.banner = Gtk.Label(), Adw.Banner()
    win._update_actions = lambda *a: None
    for name in ("run_task", "set_busy", "_stop_waiting", "_run_pending_refresh"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))


@pytest.fixture
def maps_page(server, device_root, monkeypatch):
    """The Maps page read once; the questions are kept (not shown), errors and toasts noted,
    and each reading of the page (load_maps) counted."""
    from gi.repository import Adw

    win, section, _asked = _real_maps_page([maps_response(server)], device_root)
    _real_tasks(win)
    win.errors, win.toasts, win.questions = [], [], []
    win.error = lambda title, text: win.errors.append((title, text))
    win.toast = win.toasts.append
    win.progress, win.fraction = (lambda text: None), (lambda done, total=None: None)
    client = win.session.client
    client.http, client.consumer = None, types.SimpleNamespace(url_salt="salt")
    client.activate_maps = lambda raw, idents: None
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda d, parent: win.questions.append(d))
    reads = []
    load_maps = win.load_maps
    win.load_maps = lambda: (reads.append("maps"), load_maps())
    win.load_section("maps")
    _turn_until(lambda: not win.busy)
    assert reads == ["maps"]
    return win, section, reads


def _install(win, name):
    """Install of the map `name` from its question, to the end of the task (and of what it
    starts)."""
    from openexpress.gui.app import MainWindow

    (m,) = [m for m in win.maps_cache if m.name == name]
    MainWindow._ask_install_map(win, m, m.size, 10**9, [])
    win.questions[-1].emit("response", "ok")
    _turn_until(lambda: not win.busy and win.errors)


def _on_the_device(section) -> dict:
    (group,) = [g for g in section.groups if g.get_title() == "On the device"]
    return {r.get_title(): r.get_subtitle() for r in _rows(group)}


@pytest.mark.needs_display
@pytest.mark.filterwarnings("ignore:'asyncio.*:DeprecationWarning")
def test_a_map_whose_unlock_file_failed_is_shown_waiting_to_be_loaded(maps_page, monkeypatch):
    """Its files are written, its unlock file is not (after 6 tries): the page is read once
    more and shows the map on the device, to be loaded after unplugging; the error keeps
    the words of the window, and nothing else is said."""
    win, section, reads = maps_page
    error = maps.UnlockNotWritten("Garmin/J.gma", "Cannot write Garmin/J.gma: the device is gone")

    def install(m, all_maps, storage, **kw):
        storage.write("Garmin/J.img", FILES["/maps/topo.img"])
        raise error

    monkeypatch.setattr(maps, "install", install)
    assert "TopoActive Japan" not in _on_the_device(section)
    _install(win, "TopoActive Japan")
    assert reads == ["maps", "maps"]
    assert win.errors == [("Installing TopoActive Japan…", error.window_text)]
    assert win.toasts == []  # not "Another operation is in progress"
    subtitle = _on_the_device(section)["TopoActive Japan"]
    assert subtitle.endswith("\u00a0· will be loaded after the device is unplugged")


@pytest.mark.needs_display
@pytest.mark.filterwarnings("ignore:'asyncio.*:DeprecationWarning")
def test_a_map_whose_download_failed_reads_the_page_again_too(maps_page, monkeypatch):
    """Nothing was written: the page read again shows the map not installed."""
    win, section, reads = maps_page

    def install(m, all_maps, storage, **kw):
        raise maps.MapError("Download failed: the server is gone")

    monkeypatch.setattr(maps, "install", install)
    _install(win, "TopoActive Japan")
    assert reads == ["maps", "maps"]
    assert win.errors == [("Installing TopoActive Japan…", "Download failed: the server is gone")]
    assert "TopoActive Japan" not in _on_the_device(section)


# --- every such task of the three pages, in a window of stand-ins -----------------------------

class Answering:
    """Adw.AlertDialog: answered at once with `answer`, nothing shown."""
    answer = "ok"

    def __init__(self, **kw):
        pass

    def connect(self, signal, handler):
        if signal == "response":
            handler(self, Answering.answer)

    def get_response_enabled(self, response):
        return True

    def __getattr__(self, name):
        return lambda *a, **kw: None


class Switch:
    """The switch of an app row, turned to `active`; what the window sets back is kept."""

    def __init__(self, active):
        self.active = active

    def get_active(self):
        return self.active

    def set_active(self, value):
        self.active = value

    def handler_block(self, handler_id):
        pass

    handler_unblock = handler_block


SESSION = dict(device=types.SimpleNamespace(raw=b"xml", unit_id=3000000001), storage=None,
               client=types.SimpleNamespace(http=None,
                                            consumer=types.SimpleNamespace(url_salt="salt")))


def _window(monkeypatch, session=None):
    """The window as these tasks use it. run_task runs at once in the window's order: the
    work, then done, or failed and the error when failed did not say it. Each reading of a
    page is noted in `reads`; `fails` is a work that fails, and unplugs the device first
    when `unplug` is set."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(Adw, "AlertDialog", Answering)
    Answering.answer = "ok"
    win = stand_in(
        session=session or types.SimpleNamespace(**SESSION), busy=False, errors=[],
        toasts=[], reads=[], unplug=False, maps_cache=[], _updated_apps=set(),
        progress=lambda text: None, fraction=lambda done, total=None: None,
        _forget_text=MainWindow._forget_text, _copy_summary=MainWindow._copy_summary,
        _playlist_summary=lambda plan, in_flatpak=False: "1 track")
    win.toast = win.toasts.append
    win.error = lambda title, text: win.errors.append((title, text))
    win.load_section = lambda key, force=False: win.reads.append((key, force))

    run_task = RunNow(error=lambda title, text: win.errors.append((title, text)))

    def fails(*args, **kwargs):
        if win.unplug:
            win.session = types.SimpleNamespace()  # another device opened meanwhile
        raise DeviceError(GONE)

    win.run_task, win.fails = run_task, fails
    win._install_apps = lambda apps, allowed=(): MainWindow._install_apps(win, apps, allowed)
    return win


APP = types.SimpleNamespace(name="Face It", app_id=FACE, app_type=4, has_files=True,
                            direct_removal=False, sandboxed=False, disabled=True,
                            permissions_changed=False)


def _account(monkeypatch, store=None):
    monkeypatch.setattr(service, "require_account_device", lambda client, unit_id, fresh=False: [])
    monkeypatch.setattr(ciq, "StoreApi", lambda client: store)


def restore_order(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(ciq, "restore_order", win.fails)
    MainWindow.restore_order(win, types.SimpleNamespace(state=None))
    return "apps", "Restoring the order…"


def install_apps(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    _account(monkeypatch)
    monkeypatch.setattr(ciq, "install_apps", win.fails)
    MainWindow.install_apps(win, [APP])
    return "apps", "Installing apps…"


def remove_app(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(ciq, "remove", win.fails)
    MainWindow.confirm_remove_app(win, APP)
    return "apps", "Removing Face It…"


def remove_and_forget(win, monkeypatch):
    """Deleted from the device, then the account says no: the page showed it still there."""
    from openexpress.gui.app import MainWindow

    Answering.answer = "forget"
    _account(monkeypatch, types.SimpleNamespace(forget=win.fails))
    monkeypatch.setattr(ciq, "remove", lambda device, storage, app: ["GARMIN/APPS/MEDIA/X.PRG"])
    MainWindow.confirm_remove_app(win, APP)
    return "apps", "Removing Face It…"


def forget_app(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    Answering.answer = "forget"
    _account(monkeypatch, types.SimpleNamespace(forget=win.fails))
    MainWindow.confirm_forget_app(win, APP)
    return "apps", "Removing Face It…"


def switch_app(win, monkeypatch):
    """The switch is set back to off, and the page is read again."""
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(ciq, "set_enabled", win.fails)
    row = Switch(active=True)  # turned on; the app is off
    MainWindow.toggle_app(win, APP, row, 0)
    assert row.active is False
    return "apps", "Saving…"


def copy_music(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    plan = music.CopyPlan(to_copy=[(types.SimpleNamespace(size=100), "Music/one.mp3")])
    monkeypatch.setattr(music, "scan", lambda paths, device, forced_kind=None: [])
    monkeypatch.setattr(music, "plan_copy", lambda tracks, device, storage: plan)
    monkeypatch.setattr(music, "copy", win.fails)
    MainWindow.add_music(win, [Path("one.mp3")], "track")
    return "music", "Copying music…"


def add_playlist(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    plan = types.SimpleNamespace(name="Morning run", device_paths=["Music/one.mp3"])
    monkeypatch.setattr(music, "plan_playlist", lambda path, device, storage: plan)
    monkeypatch.setattr(music, "add_playlist", win.fails)
    MainWindow.add_playlist(win, Path("Morning run.m3u8"))
    return "music", "Copying music…"


def remove_music(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(music, "remove", win.fails)
    MainWindow.remove_music(win, ["Music/one.mp3"])
    return "music", "Removing…"


def install_map(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    m = _map(1000)
    win.maps_cache = [m]
    monkeypatch.setattr(maps, "install", win.fails)
    MainWindow._ask_install_map(win, m, 1000, 10**9, [])
    return "maps", "Installing Test Map…"


def remove_map(win, monkeypatch):
    from openexpress.gui.app import MainWindow

    m = _map(1000)
    win.maps_cache = [m]
    monkeypatch.setattr(maps, "uninstall", win.fails)
    MainWindow.confirm_remove(win, m)
    return "maps", "Removing Test Map…"


CASES = [restore_order, install_apps, remove_app, remove_and_forget, forget_app, switch_app,
         copy_music, add_playlist, remove_music, install_map, remove_map]


@pytest.mark.parametrize("start", CASES, ids=[c.__name__ for c in CASES])
def test_a_task_that_failed_reads_its_page_again(start, monkeypatch):
    """The page is read once, as after a task that went well, and the error is said."""
    win = _window(monkeypatch)
    key, title = start(win, monkeypatch)
    assert win.reads == [(key, True)]
    assert win.errors == [(title, GONE)]


@pytest.mark.parametrize("start", CASES, ids=[c.__name__ for c in CASES])
def test_a_task_whose_device_is_gone_meanwhile_reads_no_page(start, monkeypatch):
    """Unplugged, or another device opened, during the task: the window reads the pages of
    what is open on its own; the task reads none. The error is still said."""
    win = _window(monkeypatch)
    win.unplug = True
    key, title = start(win, monkeypatch)
    assert win.reads == []
    assert win.errors == [(title, GONE)]


# --- what the page read again finds on the device --------------------------------------------------

def test_a_switch_whose_waiting_list_is_lost_reads_the_apps_page_again(iq_device, monkeypatch):
    """The list that waited (an earlier switch) cannot be written back either, so it is
    deleted: the page read again finds that nothing waits any more (not the earlier switch
    as waiting for the device)."""
    from openexpress.gui.app import MainWindow

    st, dev = iq_device
    ciq.set_enabled(dev, st, PRELOADED, True)  # waits for the device
    assert ciq.read_installed(dev, st).waiting
    win = _window(monkeypatch, types.SimpleNamespace(device=dev,
                                                     storage=Breaking(st.root, breaks=2)))
    win.load_section = lambda key, force=False: win.reads.append(
        (key, bool(ciq.read_installed(dev, st).waiting)))
    store = ciq.read_installed(dev, st).find(STORE_ID)
    MainWindow.toggle_app(win, store, Switch(active=False), 0)  # turned off
    assert win.reads == [("apps", False)]
    ((title, text),) = win.errors
    assert title == "Saving…" and "are lost. Make them again." in text


class SecondFails(LocalStorage):
    """A device that goes on the second file copied to it."""

    def __init__(self, root):
        super().__init__(root)
        self.copies = 0

    def _write_file(self, real_path, local, progress=None):
        self.copies += 1
        if self.copies == 2:
            raise DeviceError(GONE)
        super()._write_file(real_path, local, progress)


def test_music_copied_in_part_reads_the_music_page_again(iq_device, library, monkeypatch):
    """One song of two is copied: the page read again lists it."""
    from openexpress.gui.app import MainWindow

    st, dev = iq_device
    win = _window(monkeypatch, types.SimpleNamespace(device=dev, storage=SecondFails(st.root)))
    win.load_section = lambda key, force=False: win.reads.append(
        (key, [Path(i.path).name for i in music.list_device(dev, st)]))
    songs = [library.parent / "one.mp3", library.parent / "sub" / "two.mp3"]
    MainWindow.add_music(win, songs, "track")
    assert win.reads == [("music", ["one.mp3"])]
    assert win.errors == [("Copying music…", GONE)]
