"""The window asks the device nothing on its own thread (GTK's): over MTP each question is a
round trip through gvfs, and a few dozen of them froze the window for seconds (review of
22.10.2026, area 7). Here the thread of the test stands for GTK's: a task runs its work in
a thread of its own and `done` back here, as run_task does. All data is made up."""

import threading
import types

import pytest

from openexpress.device.storage import LocalStorage


class Counting(LocalStorage):
    """A storage that notes on which thread each question to the device is asked; it
    replaces a file as MTP does (the old one goes first), so a map's files are looked at."""

    replaces_atomically = False

    def __init__(self, root):
        super().__init__(root)
        self.on_gtk: list[str] = []
        self.elsewhere: list[str] = []
        self.gtk = threading.current_thread()

    def _note(self, what):
        (self.on_gtk if threading.current_thread() is self.gtk else self.elsewhere).append(what)

    def _list(self, real_path):
        self._note("list")
        return super()._list(real_path)

    def _read(self, real_path):
        self._note("read")
        return super()._read(real_path)

    def free_space(self):
        self._note("free_space")
        return super().free_space()

    def capacity(self):
        self._note("capacity")
        return super().capacity()


class Tasks:
    """run_task as the window has it: the work in a thread, then `done` (or `failed`) on the
    window's thread; `before_done` runs in between, as what may happen meanwhile."""

    def __init__(self):
        self.titles, self.errors, self.before_done = [], [], None

    def __call__(self, title, fn, done=None, failed=None, device=True, in_page=False,
                 button=None):
        self.titles.append(title)
        result, error = [], []

        def worker():
            try:
                result.append(fn())
            except Exception as e:  # noqa: BLE001
                error.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        if self.before_done is not None:
            self.before_done()
        if error:
            if not (failed and failed(error[0])):
                self.errors.append(str(error[0]))
        elif done:
            done(result[0])


class Question:
    """Adw.AlertDialog: what is asked is recorded, nothing is shown."""
    asked: list = []

    def __init__(self, **kw):
        Question.asked.append(kw)

    def __getattr__(self, name):
        return lambda *a, **kw: None


# --- step 1: the question before a map is installed --------------------------------------------

def _map(*sizes):
    from openexpress import maps

    files = [maps.MapFile(device_path=f"Garmin/Maps/Map{n}.img",
                          url=f"https://example.org/map{n}.img", md5=None, size=size)
             for n, size in enumerate(sizes)]
    return maps.UniversalMap(name="Test Map", map_type="TopoActive", state=0,
                             is_reinstall=False, can_uninstall=True, version="2026.11",
                             region="Test", identifier={}, files=files, files_to_remove=[],
                             hosts=[])


@pytest.fixture
def map_window(device_root, monkeypatch):
    from openexpress.gui import app

    (device_root / "Garmin/Maps").mkdir(parents=True, exist_ok=True)
    (device_root / "Garmin/Maps/Map0.img").write_bytes(b"x" * 300)  # an older one, replaced
    Question.asked = []
    monkeypatch.setattr(app.Adw, "AlertDialog", Question)
    storage = Counting(device_root)
    m = _map(1000, 2000)
    tasks = Tasks()
    win = types.SimpleNamespace(
        maps_cache=[m], run_task=tasks, tasks=tasks, errors=[],
        error=lambda title, body: win.errors.append(title),
        progress=lambda text: None, fraction=lambda done, total: None,
        session=types.SimpleNamespace(storage=storage, device=types.SimpleNamespace(raw=b""),
                                      client=types.SimpleNamespace(
                                          http=None,
                                          consumer=types.SimpleNamespace(url_salt="salt"))))
    win._ask_install_map = types.MethodType(app.MainWindow._ask_install_map, win) \
        if hasattr(app.MainWindow, "_ask_install_map") else None
    return win, m, storage


def test_the_space_for_a_map_is_checked_off_the_window_thread(map_window, device_root):
    """The same question as before, after a short "Checking the space…"."""
    from openexpress import maps as mp
    from openexpress.gui import app

    win, m, storage = map_window
    plain = LocalStorage(device_root)
    plain.replaces_atomically = False
    need = mp.space_needed(plain, m, [])
    free = plain.free_space()
    app.MainWindow.confirm_map(win, m)
    assert storage.on_gtk == [] and storage.elsewhere  # asked, but not on GTK's thread
    assert win.tasks.titles == ["Checking the space…"]
    (asked,) = Question.asked
    assert asked["heading"] == "Install Test Map?"
    assert asked["body"].startswith(
        "Download: 3.0\u00a0kB\n"
        f"Space on the device: {app._human(need)} ({app._human(free)} free)\nFiles: 2")


@pytest.mark.parametrize("meanwhile", ["unplugged", "list read again without it"])
def test_nothing_is_asked_when_the_device_or_the_list_changed(map_window, meanwhile):
    from openexpress.gui import app

    win, m, _storage = map_window

    def change():
        if meanwhile == "unplugged":
            win.session = None
        else:
            win.maps_cache = []

    win.tasks.before_done = change
    app.MainWindow.confirm_map(win, m)
    assert Question.asked == [] and win.tasks.errors == []


def test_a_device_gone_during_the_check_says_nothing(map_window, monkeypatch):
    """Unplugged while its space was read: the window shows that it is gone, no error."""
    from openexpress.device.storage import DeviceError
    from openexpress.gui import app

    win, m, storage = map_window

    def gone():
        raise DeviceError("Cannot list Garmin/Maps: the device is gone")

    monkeypatch.setattr(storage, "free_space", gone)
    win.tasks.before_done = lambda: setattr(win, "session", None)
    app.MainWindow.confirm_map(win, m)
    assert Question.asked == [] and win.tasks.errors == []


def _bar_window(map_window, monkeypatch, delay):
    """The window as it runs a task for real (run_task and set_busy with their bar), for the
    map of map_window, whose free space takes `delay` seconds to read."""
    import time

    from gi.repository import Adw, Gtk

    from openexpress.gui import app

    win, m, storage = map_window
    real_free = storage.free_space

    def slow_free():
        time.sleep(delay)
        return real_free()

    monkeypatch.setattr(storage, "free_space", slow_free)
    win.busy, win._waiting, win._refresh_pending, win._action_buttons = False, 0, False, []
    win.action_bar, win.progress_label = Gtk.ActionBar(revealed=False), Gtk.Label()
    win.progress_bar, win.busy_spinner = Gtk.ProgressBar(), Gtk.Spinner()
    win.unplug_label, win.banner = Gtk.Label(), Adw.Banner()
    win._update_actions = win.toast = lambda *a: None
    for name in ("run_task", "set_busy", "_stop_waiting", "_run_pending_refresh",
                 "_ask_install_map"):
        setattr(win, name, types.MethodType(getattr(app.MainWindow, name), win))
    return win, m


def _spinning(widget) -> bool:
    from gi.repository import Adw, Gtk

    kinds = (Gtk.Spinner, Adw.Spinner) if hasattr(Adw, "Spinner") else (Gtk.Spinner,)
    child = widget.get_first_child()
    while child is not None:
        if (isinstance(child, kinds) and child.get_visible()) or _spinning(child):
            return True
        child = child.get_next_sibling()
    return False


def _until_asked(win, button):
    """Runs the window's loop until the question is asked; what it showed meanwhile:
    (the bar out, its text, the button clickable, a spinner in the button) each time."""
    import time

    from gi.repository import GLib

    seen = []
    deadline = time.monotonic() + 5
    while not Question.asked and time.monotonic() < deadline:
        seen.append((win.action_bar.get_revealed(), win.progress_label.get_text(),
                     button.get_sensitive(), _spinning(button)))
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    return seen


@pytest.mark.needs_display
@pytest.mark.parametrize(("delay", "text_shown"), [(0.1, False), (0.8, True)])
def test_the_button_spins_and_the_text_waits_half_a_second(map_window, monkeypatch, delay,
                                                           text_shown):
    """From the click the button of the map spins and cannot be clicked; "Checking the
    space…" at the bottom comes only when the check takes more than 500 ms, so on a fast
    device no text flashes. Then the button is as it was and the question is asked."""
    from gi.repository import Gtk

    from openexpress.gui import app

    win, m = _bar_window(map_window, monkeypatch, delay)
    button = Gtk.Button(label="Install")
    app.MainWindow.confirm_map(win, m, button)
    seen = _until_asked(win, button)
    (asked,) = Question.asked
    assert asked["heading"] == "Install Test Map?"
    assert seen and all(not clickable and spins for _out, _text, clickable, spins in seen)
    shown = [text for out, text, _clickable, _spins in seen if out]
    assert (shown != []) is text_shown
    assert set(shown) <= {"Checking the space…"}
    assert button.get_label() == "Install" and button.get_sensitive()
    assert not _spinning(button)
    assert not win.action_bar.get_revealed() and not win.busy


@pytest.mark.needs_display
def test_the_button_comes_back_when_the_check_fails(map_window, monkeypatch):
    """A device gone during the check: no question, no error, the button as it was."""
    from gi.repository import Gtk

    from openexpress.device.storage import DeviceError
    from openexpress.gui import app

    win, m = _bar_window(map_window, monkeypatch, 0.05)
    storage = win.session.storage

    def gone():
        win.session = None  # the window notices the unplugging meanwhile
        raise DeviceError("Cannot list Garmin/Maps: the device is gone")

    monkeypatch.setattr(storage, "free_space", gone)
    button = Gtk.Button(label="Install")
    app.MainWindow.confirm_map(win, m, button)
    import time

    from gi.repository import GLib

    deadline = time.monotonic() + 2
    while win.busy and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    assert Question.asked == [] and win.errors == []
    assert button.get_label() == "Install" and button.get_sensitive()
    assert not _spinning(button)


@pytest.mark.needs_display
def test_the_install_button_of_the_maps_page_starts_the_check_from_itself(server, device_root):
    """Install and Update on the Maps page hand their own button to the check."""
    from gi.repository import Gtk

    from openexpress.gui import app

    win, section, _asked = _real_maps_page([maps_response(server)], device_root)
    started = []
    win.confirm_map = lambda m, button=None: started.append(button)
    win.load_section("maps")
    buttons = [b for g in section.groups for r in _rows(g) for b in _suffix_buttons(r)
               if b.get_label() in ("Install", "Update")]
    assert buttons
    for b in buttons:
        b.emit("clicked")
    assert started == buttons and all(isinstance(b, Gtk.Button) for b in started)
    assert app.TEXT_AFTER_MS == 500


def _suffix_buttons(row):
    from gi.repository import Gtk

    out, stack = [], [row]
    while stack:
        w = stack.pop()
        if isinstance(w, Gtk.Button):
            out.append(w)
        c = w.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    return out


# --- step 3: the question before the order of the apps is restored ---------------------------

from test_ciq_music import iq_device, order_device  # noqa: E402, F401 - the fixtures

NOTE = "Before any change, a copy of the app list of the device is made."


def test_the_question_of_the_order_asks_the_device_nothing(order_device, monkeypatch):
    from openexpress import ciq
    from openexpress.gui import app

    st, dev, backups = order_device
    Question.asked = []
    monkeypatch.setattr(app.Adw, "AlertDialog", Question)
    copy = ciq.load_app_list_copy(dev, backups, "03.04.05")
    plan = ciq.plan_order(ciq.read_installed(dev, st), copy.state)
    storage = Counting(st.root)
    win = types.SimpleNamespace(session=types.SimpleNamespace(device=dev, storage=storage),
                                _order_summary=app.MainWindow._order_summary,
                                restore_order=lambda copy: None)
    app.MainWindow.confirm_order(win, copy, plan, NOTE)
    assert storage.on_gtk == []
    (asked,) = Question.asked
    assert NOTE in asked["body"]


class Dialog:
    """Adw.AlertDialog as choose_order uses it: its list and its handlers are kept."""

    def __init__(self, **kw):
        self.kw, self.handlers, self.extra = kw, {}, None
        Dialog.made.append(self)

    def set_extra_child(self, widget):
        self.extra = widget

    def connect(self, signal, handler):
        self.handlers[signal] = handler

    def __getattr__(self, name):
        return lambda *a, **kw: None


@pytest.mark.needs_display
def test_the_choice_of_a_copy_passes_the_note_on(order_device, monkeypatch):
    """The note comes with the list read in the background (read_installed read the app
    list, so it is there): Next hands it to the question, which asks the device nothing."""
    from gi.repository import Gtk

    from openexpress import ciq
    from openexpress.gui import app

    st, dev, backups = order_device
    Dialog.made = []
    monkeypatch.setattr(app.Adw, "AlertDialog", Dialog)
    real = ciq.order_choices
    monkeypatch.setattr(ciq, "order_choices", lambda device, state: real(device, state, backups))
    storage = Counting(st.root)
    asked = []
    win = types.SimpleNamespace(
        session=types.SimpleNamespace(device=dev, storage=storage), run_task=Tasks(),
        error=lambda title, text: pytest.fail(text), _order_blocked=lambda state: None,
        _order_rows=app.MainWindow._order_rows,
        confirm_order=lambda copy, plan, note=None: asked.append(note))
    app.MainWindow.choose_order(win)
    (dialog,) = Dialog.made
    box = dialog.extra.get_child().get_child()
    assert isinstance(box, Gtk.ListBox)
    rows = [box.get_row_at_index(i) for i in range(20)]
    box.select_row(next(r for r in rows if r is not None and r.get_selectable()))
    dialog.handlers["response"](dialog, "next")
    assert asked == [NOTE]
    assert storage.on_gtk == [] and storage.elsewhere


# --- step 4: the Maps page ----------------------------------------------------------------------

from test_updates_maps import _real_maps_page, maps_response, server  # noqa: E402, F401


def _rows(group):
    from gi.repository import Adw

    out, child = [], group.get_first_child()
    stack = [child] if child is not None else []
    while stack:
        w = stack.pop()
        if isinstance(w, Adw.ActionRow):
            out.append(w)
        c = w.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    return out


@pytest.mark.needs_display
def test_the_maps_page_reads_the_space_with_the_maps(server, device_root):
    """The free space and the size of the device are read with the maps, in the task: the
    page shows them as before and asks the device nothing as it is drawn."""
    from openexpress.gui import app

    win, section, _asked = _real_maps_page([maps_response(server)], device_root)
    storage = Counting(device_root)
    win.session.storage = storage
    win.run_task = Tasks()
    plain = LocalStorage(device_root)
    title = f"{app._human(plain.free_space())} free of {app._human(plain.capacity())}"
    win.load_section("maps")
    assert storage.on_gtk == [] and "free_space" in storage.elsewhere
    (on_device,) = [g for g in section.groups if g.get_title() == "On the device"]
    assert title in [r.get_title() for r in _rows(on_device)]


# --- step 5: the Music page ---------------------------------------------------------------------

@pytest.mark.needs_display
def test_the_music_page_reads_the_free_space_with_the_music(iq_device):
    """The free space is read with the music on the device, in the task; the page shows it as
    before and asks the device nothing as it is drawn."""
    from test_fixes_ui import page_window

    from openexpress.gui import app

    st, dev = iq_device
    win, section = page_window("music")
    storage = Counting(st.root)
    win.session = types.SimpleNamespace(device=dev, storage=storage)
    win.run_task = Tasks()
    win.toast = lambda text: None
    win.choose_music = win.choose_playlist = win.remove_music = lambda *a, **kw: None
    for name in ("load_music", "show_music"):
        setattr(win, name, types.MethodType(getattr(app.MainWindow, name), win))
    free = app._human(LocalStorage(st.root).free_space())
    win.load_music()
    assert storage.on_gtk == [] and "free_space" in storage.elsewhere
    (add,) = [g for g in section.groups if g.get_title() == "Add to the device"]
    assert add.get_description() == f"Free space: {free}"


# --- step 6: the device page (show_overview) -----------------------------------------------------

FREE, SIZE = 52_140_000_000, 64_000_000_000


class Space(Counting):
    """A storage whose free space and size the test sets; each question is noted. `fail`: it
    cannot be asked (e.g. another process holds the device in a session of PTP); `wait`: the
    free space is answered once the event is set."""

    def __init__(self, root, free=FREE, size=SIZE, fail=False, wait=None):
        super().__init__(root)
        self.free, self.size, self.fail, self.wait = free, size, fail, wait

    def free_space(self):
        self._note("free_space")
        if self.wait is not None:
            self.wait.wait(5)
        if self.fail:
            from openexpress.device.storage import DeviceError

            raise DeviceError("Cannot read the free space: the device is busy")
        return self.free

    def capacity(self):
        self._note("capacity")
        if self.fail:
            from openexpress.device.storage import DeviceError

            raise DeviceError("Cannot read the size: the device is busy")
        return self.size


NOT_READ = object()


def _overview_window(storage, dev, space=NOT_READ):
    """The device page of the real kind for a device in mass storage mode (no sync groups),
    the window around it with run_task and its bar as they are; `space`: what the session
    holds from its detection (NOT_READ: nothing)."""
    from gi.repository import Adw, Gtk
    from test_fixes_ui import page_window

    from openexpress.gui.app import MainWindow

    win, section = page_window("watch")
    s = types.SimpleNamespace(device=dev, storage=storage, logged_in=False, ownership_error=None,
                              account_devices=None, mass_storage=True)
    if space is not NOT_READ:
        s.space = space
    win.session, win.update_state, win.map_state, win.errors = s, None, None, []
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync", "_render_update_state", "_render_map_state",
                 "select_section", "backup", "set_background", "logout"):
        setattr(win, name, lambda *a, **kw: None)
    win._background_enabled = lambda: False
    win.error = lambda title, text: win.errors.append(text)
    win._waiting, win._refresh_pending = 0, False
    win.action_bar, win.progress_label = Gtk.ActionBar(revealed=False), Gtk.Label()
    win.progress_bar, win.busy_spinner = Gtk.ProgressBar(), Gtk.Spinner()
    win.unplug_label, win.banner = Gtk.Label(), Adw.Banner()
    win._update_actions = win.toast = lambda *a: None
    for name in ("show_overview", "run_task", "set_busy", "_stop_waiting",
                 "_run_pending_refresh"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    return win, section, s


def _storage_subtitle(section) -> str:
    from gi.repository import Adw

    stack = [section.page]
    while stack:
        w = stack.pop()
        if isinstance(w, Adw.ActionRow) and w.get_title() == "Device storage":
            return w.get_subtitle()
        c = w.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    raise AssertionError("no row Device storage")


def _turn_until(done, seconds=5) -> None:
    import time

    from gi.repository import GLib

    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


READ = "52.1\u00a0GB free of 64.0\u00a0GB"


@pytest.mark.needs_display
def test_the_device_page_asks_the_device_nothing(iq_device):
    """The free space and the size were read with the device (its detection): the page shows
    them and asks the device nothing, on the window's thread or elsewhere."""
    st, dev = iq_device
    storage = Space(st.root)
    win, section, _s = _overview_window(storage, dev, space=(FREE, SIZE))
    win.show_overview()
    assert storage.on_gtk == [] and storage.elsewhere == []
    assert _storage_subtitle(section) == READ


@pytest.mark.needs_display
def test_the_storage_row_says_dots_until_the_space_is_read(iq_device):
    """Not read yet: "…" (never "0 B" or "?"), then the space read in the background."""
    st, dev = iq_device
    storage = Space(st.root)
    win, section, s = _overview_window(storage, dev)
    win.show_overview()
    assert _storage_subtitle(section) == "…"
    _turn_until(lambda: _storage_subtitle(section) != "…")
    assert _storage_subtitle(section) == READ
    assert storage.on_gtk == [] and sorted(storage.elsewhere) == ["capacity", "free_space"]
    assert s.space == (FREE, SIZE)


@pytest.mark.needs_display
def test_after_a_task_that_writes_the_free_space_is_read_again(iq_device):
    """A task that writes to the device: once it ends, the free space alone is read again in
    the background (the size stays) and the subtitle of the row is put right; a task that
    only reads asks nothing more."""
    st, dev = iq_device
    storage = Space(st.root)
    win, section, s = _overview_window(storage, dev, space=(FREE, SIZE))
    win.show_overview()
    win.run_task("Reading…", lambda: None, lambda result: None)
    _turn_until(lambda: not win.busy)
    _turn_until(lambda: False, 0.1)
    assert storage.elsewhere == []

    from openexpress.gui import app

    def write():
        storage.free = 40_000_000_000

    win.run_task("Copying…", app.writes_to_device(write), lambda result: None)
    _turn_until(lambda: _storage_subtitle(section) != READ)
    assert _storage_subtitle(section) == "40.0\u00a0GB free of 64.0\u00a0GB"
    assert storage.on_gtk == [] and storage.elsewhere == ["free_space"]
    assert s.space == (40_000_000_000, SIZE)


@pytest.mark.needs_display
def test_a_space_read_after_the_device_was_unplugged_is_dropped(iq_device):
    """The device is unplugged while its free space is read: the answer that comes later
    changes nothing, says nothing."""
    import threading

    st, dev = iq_device
    answer = threading.Event()
    storage = Space(st.root, free=1, wait=answer)
    win, section, s = _overview_window(storage, dev, space=(FREE, SIZE))
    win.show_overview()
    from openexpress.gui import app

    win.run_task("Copying…", app.writes_to_device(lambda: None), lambda result: None)
    _turn_until(lambda: "free_space" in storage.elsewhere)
    win.session = None  # unplugged
    answer.set()
    _turn_until(lambda: False, 0.3)
    assert s.space == (FREE, SIZE) and _storage_subtitle(section) == READ
    assert win.errors == []


@pytest.mark.needs_display
def test_a_device_that_cannot_be_asked_has_an_unknown_space_and_no_error(iq_device):
    """E.g. during a session of PTP: the space is unknown ("?"), with no error in the
    window."""
    st, dev = iq_device
    storage = Space(st.root, fail=True)
    win, section, s = _overview_window(storage, dev)
    win.show_overview()
    _turn_until(lambda: _storage_subtitle(section) != "…")
    assert _storage_subtitle(section) == "? free"
    assert s.space == (None, None) and win.errors == []


def test_the_detection_and_the_choice_read_the_space_in_their_thread(iq_device, monkeypatch):
    """refresh_device and open_choice: the device found is opened with its free space and
    size read in the task's thread, never on the window's."""
    from openexpress.gui import app

    st, dev = iq_device
    storage = Space(st.root)
    session = types.SimpleNamespace(device=dev, storage=storage)
    works = []
    win = types.SimpleNamespace(
        busy=False, session=None, _asked=None, sections={"watch": types.SimpleNamespace(
            show=lambda key: None)},
        run_task=lambda title, fn, done=None, failed=None, **kw: works.append(fn))
    monkeypatch.setattr(app, "detect_devices", lambda current_uri=None: session)
    monkeypatch.setattr(app.service, "open_session", lambda found=None: session)
    monkeypatch.setattr(app.service, "check_session", lambda s: s)
    app.MainWindow.refresh_device(win)
    app.MainWindow.open_choice(win, object())
    assert len(works) == 2
    for work in works:
        vars(session).pop("space", None)
        result = []
        thread = threading.Thread(target=lambda work=work, result=result: result.append(work()))
        thread.start()
        thread.join(5)
        assert result == [session] and session.space == (FREE, SIZE)
    assert storage.on_gtk == [] and sorted(storage.elsewhere) == ["capacity", "capacity",
                                                                  "free_space", "free_space"]


# The tasks of the window that write to the device: after each, its free space is read again.
WRITING = {"Syncing…", "Installing update…", "Copying the map…", "Installing {name}…",
           "Removing {name}…", "Restoring the order…", "Installing apps…", "Saving…",
           "Copying music…", "Removing…"}


def test_every_task_that_writes_to_the_device_says_so():
    """Each run_task of the window whose task writes to the device has its work wrapped in
    writes_to_device, and only those (read in the source: the title of the task, its work
    and its keywords)."""
    import ast
    import inspect

    from openexpress.gui import app

    found = {}
    for node in ast.walk(ast.parse(inspect.getsource(app))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_task" and node.args):
            continue
        title = node.args[0]
        if (isinstance(title, ast.Call) and isinstance(title.func, ast.Attribute)
                and title.func.attr == "format"):
            title = title.func.value  # _("Removing {name}…").format(…)
        if not (isinstance(title, ast.Call) and getattr(title.func, "id", "") == "_"):
            continue  # a title given by the caller (_load): pages that read
        text = title.args[0].value
        work = node.args[1] if len(node.args) > 1 else None
        writes = (isinstance(work, ast.Call)
                  and getattr(work.func, "id", "") == "writes_to_device")
        # device=False: a task that does not touch the device (e.g. an app forgotten in the
        # account's list, "Removing {name}…" as well).
        device = not any(k.arg == "device" and getattr(k.value, "value", None) is False
                         for k in node.keywords)
        found.setdefault(text, set()).add((writes, device))
    assert set(found) >= WRITING
    for text, calls in found.items():
        for writes, device in calls:
            assert writes is (text in WRITING and device), text
