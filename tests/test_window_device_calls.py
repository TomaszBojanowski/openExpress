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

    def __call__(self, title, fn, done=None, failed=None, device=True, in_page=False):
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
        "Download: 3.0 kB\n"
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
