"""The cache of openExpress: how big it is, folder by folder (C1). Nothing is deleted."""

import os
from pathlib import Path

import pytest


def fill(root: Path, files: dict[str, int]) -> None:
    """Files of these sizes below `root` (made-up content)."""
    for name, size in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)


@pytest.fixture
def cache(tmp_path) -> Path:
    """The cache folder of these tests: XDG_CACHE_HOME is tmp_path/cache (conftest)."""
    from openexpress import paths

    root = paths.cache_dir()
    assert root == tmp_path / "cache" / "openexpress"
    return root


def test_sizes_of_the_cache_folders(cache, tmp_path):
    """Known sizes, a known total: files in subfolders and unfinished downloads count; a
    folder openExpress does not make (e.g. tmp) and a symbolic link to a file outside do
    not."""
    from openexpress import cache as oe_cache

    fill(cache, {"maps/aaa": 1000, "maps/bbb.part": 500, "maps/sub/ccc": 250,
                 "images/device-006-B4532-00.png": 30,
                 "software/006-B4532-00/23.40/GUPDATE.GCD": 4000,
                 "tmp/other": 99})
    (cache / "transfer").mkdir()
    fill(tmp_path, {"outside/big": 7777})
    os.symlink(tmp_path / "outside" / "big", cache / "maps" / "link")
    use = oe_cache.usage()
    assert use.root == cache
    assert use.sizes == {"maps": 1750, "images": 30, "software": 4000, "transfer": 0}
    assert use.total == 5780


def test_no_cache_yet_is_nothing(cache):
    """No cache folder at all: every size 0, no error, and nothing is made."""
    from openexpress import cache as oe_cache

    use = oe_cache.usage()
    assert use.sizes == {"maps": 0, "images": 0, "software": 0, "transfer": 0}
    assert use.total == 0 and not cache.exists()


def test_a_folder_that_is_a_link_is_not_followed(cache, tmp_path):
    """A cache folder replaced by a symbolic link (e.g. maps on another disk) is not walked:
    what is counted is only what openExpress keeps in its own folder."""
    from openexpress import cache as oe_cache

    fill(tmp_path, {"elsewhere/map": 5000})
    cache.mkdir(parents=True)
    os.symlink(tmp_path / "elsewhere", cache / "maps")
    assert oe_cache.usage().sizes["maps"] == 0


def test_the_flatpak_cache_is_where_xdg_cache_home_says(tmp_path, monkeypatch):
    """In the Flatpak XDG_CACHE_HOME is ~/.var/app/<id>/cache: the same function finds the
    cache of that installation; each one counts its own."""
    from openexpress import cache as oe_cache

    sandbox = tmp_path / ".var" / "app" / "io.github.tomaszbojanowski.OpenExpress" / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(sandbox))
    fill(sandbox / "openexpress", {"maps/m": 123})
    use = oe_cache.usage()
    assert use.root == sandbox / "openexpress" and use.sizes["maps"] == 123


def test_cache_command_lists_the_four_folders_and_the_total(cache, capsys):
    """`openexpress cache`: the folder, each of the four with its size, the total; exit
    code 0; nothing deleted."""
    from openexpress.cli import main

    fill(cache, {"maps/aaa": 1750, "images/p.png": 30, "software/s/1/f": 4000})
    assert main(["cache"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Cache: {cache}",
        "  Maps: 1.8 kB",
        "  Pictures: 30 B",
        "  Software updates: 4.0 kB",
        "  Transfer: 0 B",
        "  Total: 5.8 kB"]
    assert (cache / "maps" / "aaa").exists()


def test_cache_command_with_no_cache(cache, capsys):
    """No cache yet: zeros, exit code 0."""
    from openexpress.cli import main

    assert main(["cache"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "  Total: 0 B"


# --- the lock of the cache (C2, part 1): shared while it is used, exclusive to clear it -------


def in_use() -> bool:
    """Whether a clearing would be refused now (something holds the cache)."""
    from openexpress import cache as oe_cache

    try:
        with oe_cache.clearing():
            return False
    except oe_cache.CacheBusy:
        return True


def test_clearing_is_refused_while_the_cache_is_in_use(cache):
    """A download or an install holds the cache: a clearing is refused at once (it does not
    wait); once the cache is free it goes ahead."""
    from openexpress import cache as oe_cache

    with oe_cache.using():
        with pytest.raises(oe_cache.CacheBusy) as error, oe_cache.clearing():
            pytest.fail("cleared while in use")
        with oe_cache.using():  # two users at once are fine
            pass
    assert str(error.value) == ("The cache is in use (a download or an install is running). "
                                "Try again later.")
    with oe_cache.clearing():
        pass
    assert (cache / "cache.lock").exists()


def test_two_processes_do_not_get_in_each_others_way(cache, tmp_path):
    """Another process (e.g. the window while the command line clears) holds the cache: the
    clearing is refused until that process lets go."""
    import subprocess
    import sys

    from openexpress import cache as oe_cache

    script = tmp_path / "hold.py"
    script.write_text("import sys\nfrom openexpress import cache\n"
                      "with cache.using():\n    print('held', flush=True)\n    sys.stdin.readline()\n")
    env = {**os.environ, "PYTHONPATH": str(Path(oe_cache.__file__).parent.parent)}
    with subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True, env=env) as other:
        assert other.stdout.readline().strip() == "held"
        assert in_use()
        other.stdin.write("\n")
        other.stdin.flush()
        assert other.wait(timeout=20) == 0
    assert not in_use()


def test_a_download_waits_while_the_cache_is_being_cleared(cache):
    """No download starts during a clearing: it waits, and goes on once the clearing ends."""
    import threading

    from openexpress import cache as oe_cache

    entered = threading.Event()

    def download():
        with oe_cache.using():
            entered.set()

    with oe_cache.clearing():
        worker = threading.Thread(target=download)
        worker.start()
        assert not entered.wait(0.3)  # held back
    assert entered.wait(5)
    worker.join(5)


def test_installing_a_map_holds_the_cache(cache, monkeypatch):
    """From its download to the copy to the device, a map install holds the cache."""
    from openexpress import maps

    seen = []
    monkeypatch.setattr(maps, "_install", lambda *a, **kw: seen.append(in_use()))
    maps.install(None, [], None, None)
    assert seen == [True] and not in_use()


def test_installing_an_update_holds_the_cache(cache, monkeypatch):
    """The same for a software update."""
    from openexpress import updates

    seen = []
    monkeypatch.setattr(updates, "_install", lambda *a, **kw: seen.append(in_use()))
    updates.install([], None)
    assert seen == [True] and not in_use()


def test_saving_a_picture_holds_the_cache(cache, monkeypatch):
    """A picture or an icon is written to the cache while it is held (not the network part)."""
    import types

    from openexpress.gui import app

    seen, write = [], Path.write_bytes

    def write_bytes(self, data):
        seen.append(in_use())
        return write(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    png = app.PNG_SIGNATURE + b"picture"
    http = types.SimpleNamespace(get=lambda url, **kw: types.SimpleNamespace(
        ok=True, content=png))
    assert app.cached_download(http, "https://example/icon.png", "app-x.png") is not None

    class Stream:
        status_code, headers = 200, {"Content-Type": "image/png"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_content(self, size):
            return [png]

    http = types.SimpleNamespace(get=lambda url, **kw: Stream())
    path, why = app.download_picture(http, "https://res.garmin.com/p.png", "device-x.png")
    assert path is not None and why == ""
    assert seen == [True, True] and not in_use()


def test_staging_a_file_for_the_device_holds_the_cache(cache, monkeypatch):
    """In the Flatpak a file goes to the device through the cache (transfer): held from the
    staged copy until it is deleted."""
    from openexpress.device import gio_storage

    monkeypatch.setattr(gio_storage, "in_flatpak", lambda: True)
    seen = []
    st = object.__new__(gio_storage.GioStorage)
    st._copy_local = lambda local, real, progress, what: seen.append((in_use(), local.exists()))
    gio_storage.GioStorage._write(st, "GARMIN/NewFiles/x.fit", b"data")
    assert seen == [(True, True)] and not in_use()
    assert list((cache / "transfer").iterdir()) == []


# --- clearing the cache (C2, part 2): `openexpress cache --clear [--apply]` --------------------


def filled(cache, tmp_path) -> dict:
    """A cache with files in each folder, and around it what a clearing must never touch:
    a file and a folder openExpress does not make, a link in maps to a file outside, and
    that file. Returns the paths that must stay."""
    fill(cache, {"maps/aaa": 1000, "maps/bbb.part": 500, "maps/sub/ccc": 250,
                 "images/device-x.png": 30, "software/006/1/GUPDATE.GCD": 4000,
                 "transfer/openexpress-staged": 20, "tmp/other": 99, "notes.txt": 7})
    fill(tmp_path, {"outside/big": 7777})
    os.symlink(tmp_path / "outside" / "big", cache / "maps" / "link")
    return {"other": cache / "tmp" / "other", "notes": cache / "notes.txt",
            "outside": tmp_path / "outside" / "big"}


def test_clear_without_apply_deletes_nothing(cache, tmp_path, capsys):
    """Dry run: the same numbers as `cache`, then one line; every file is still there."""
    from openexpress.cli import main

    filled(cache, tmp_path)
    before = sorted(p for p in cache.rglob("*"))
    assert main(["cache", "--clear"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Cache: {cache}",
        "  Maps: 1.8 kB",
        "  Pictures: 30 B",
        "  Software updates: 4.0 kB",
        "  Transfer: 20 B",
        "  Total: 5.8 kB",
        "Dry run: nothing was deleted. Add --apply to delete it."]
    assert sorted(p for p in cache.rglob("*")) == before


def test_clear_with_apply_empties_the_four_folders(cache, tmp_path, capsys):
    """--apply: what is in the four folders goes; the folders themselves, cache.lock and all
    else stay (a link is removed as a link, the file it points to stays). It says how much
    was freed."""
    from openexpress.cli import main

    stay = filled(cache, tmp_path)
    assert main(["cache", "--clear", "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "Freed 5.8 kB." and "  Total: 5.8 kB" in out
    for name in ("maps", "images", "software", "transfer"):
        assert (cache / name).is_dir() and list((cache / name).iterdir()) == []
    assert (cache / "cache.lock").exists()
    assert all(p.exists() for p in stay.values())


def test_clear_while_the_cache_is_in_use_deletes_nothing(cache, tmp_path, capsys):
    """A download or an install holds the cache: the clearing is refused, exit code 1,
    every file is still there."""
    from openexpress import cache as oe_cache
    from openexpress.cli import main

    filled(cache, tmp_path)
    with oe_cache.using():  # makes cache.lock: the files are counted after that
        before = sorted(p for p in cache.rglob("*"))
        assert main(["cache", "--clear", "--apply"]) == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == ("Error: The cache is in use (a download or an install is "
                                    "running). Try again later.")
    assert "Freed" not in captured.out
    assert sorted(p for p in cache.rglob("*")) == before


def test_a_cache_folder_that_is_a_link_is_left_alone(cache, tmp_path):
    """maps is a link to a folder elsewhere: neither the link nor what it points to is
    deleted (only what openExpress keeps in its own folder is cleared)."""
    from openexpress import cache as oe_cache

    fill(tmp_path, {"elsewhere/map": 5000})
    fill(cache, {"images/p.png": 30})
    os.symlink(tmp_path / "elsewhere", cache / "maps")
    freed = oe_cache.clear()
    assert freed.total == 30
    assert (cache / "maps").is_symlink() and (tmp_path / "elsewhere" / "map").exists()
    assert list((cache / "images").iterdir()) == []


# --- the "Storage" group of the preferences (C3/C4) --------------------------------------------


@pytest.fixture
def prefs(cache, monkeypatch):
    """A stand-in for the window around the cache group: tasks run at once; dialogs are
    recorded instead of shown. Needs a display (widgets)."""
    import types

    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    dialogs, errors, toasts = [], [], []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: dialogs.append(self))

    def run_task(title, work, done, failed=None, device=True):
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - as the window's run_task: to `failed`
            if not (failed and failed(e)):
                errors.append(str(e))
        else:
            done(result)

    win = types.SimpleNamespace(busy=False, run_task=run_task, dialogs=dialogs, errors=errors,
                                toast=toasts.append, toasts=toasts,
                                error=lambda title, text: errors.append(text))
    for name in ("_cache_group", "_ask_clear_cache", "_render_cache_row"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    return win


def cache_row(group):
    """The "Cache" row of the group and its "Clear…" button."""
    from gi.repository import Adw, Gtk

    rows, buttons = [], []

    def walk(widget):
        if isinstance(widget, Adw.ActionRow):
            rows.append(widget)
        if isinstance(widget, Gtk.Button) and widget.get_label() == "Clear…":
            buttons.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(group)
    (row,), (button,) = rows, buttons
    return row, button


@pytest.mark.needs_display
@pytest.mark.parametrize("files, subtitle, can_clear", [
    ({"maps/m": 5000, "images/p.png": 30}, "5.0\u00a0kB, mostly downloaded maps", True),
    ({"images/p.png": 3000, "software/s/1/f": 1000}, "4.0\u00a0kB", True),
    ({}, "Empty", False),
])
def test_the_group_shows_how_big_the_cache_is(prefs, cache, files, subtitle, can_clear):
    """The row says how big the cache is (cache.usage(), as the command line), mostly maps
    or not; with nothing in it, "Empty" and the button is grey."""
    fill(cache, files)
    group = prefs._cache_group()
    assert group.get_title() == "Storage"
    row, button = cache_row(group)
    assert (row.get_title(), row.get_subtitle()) == ("Cache", subtitle)
    assert button.get_sensitive() is can_clear


@pytest.mark.needs_display
def test_clear_asks_first(prefs, cache):
    """"Clear…" opens a confirmation that names the maps and their size; Cancel is the
    default, Clear is destructive. Nothing is deleted yet."""
    from gi.repository import Adw

    fill(cache, {"maps/m": 5000, "images/p.png": 30})
    _row, button = cache_row(prefs._cache_group())
    button.emit("clicked")
    (confirm,) = prefs.dialogs
    assert confirm.get_heading() == "Clear the cache?"
    assert confirm.get_body() == ("This deletes the downloaded maps (5.0\u00a0kB) and the other "
                                  "files of the cache (30\u00a0B). The maps will have to be "
                                  "downloaded again.")
    assert confirm.get_default_response() == "cancel" and confirm.get_close_response() == "cancel"
    assert confirm.get_response_appearance("clear") == Adw.ResponseAppearance.DESTRUCTIVE
    assert (cache / "maps" / "m").exists()


@pytest.mark.needs_display
def test_confirming_clears_and_shows_the_new_size(prefs, cache):
    """Clear: the cache is cleared as by `cache --clear --apply`; the row says "Empty", the
    button is grey, and a toast says how much was freed."""
    fill(cache, {"maps/m": 5000, "images/p.png": 30})
    row, button = cache_row(prefs._cache_group())
    button.emit("clicked")
    prefs.dialogs[0].emit("response", "clear")
    assert list((cache / "maps").iterdir()) == [] and list((cache / "images").iterdir()) == []
    assert row.get_subtitle() == "Empty" and not button.get_sensitive()
    assert prefs.toasts == ["Freed 5.0\u00a0kB."] and prefs.errors == []


@pytest.mark.needs_display
def test_clearing_while_the_cache_is_in_use(prefs, cache):
    """A download or an install holds the cache: the message says so; nothing is deleted
    and the row still shows the size."""
    from openexpress import cache as oe_cache

    fill(cache, {"maps/m": 5000})
    row, button = cache_row(prefs._cache_group())
    button.emit("clicked")
    with oe_cache.using():
        prefs.dialogs[0].emit("response", "clear")
    assert prefs.errors == [("The cache is in use (a download or an install is running). "
                             "Try again later.")]
    assert (cache / "maps" / "m").exists() and row.get_subtitle().startswith("5.0\u00a0kB")


@pytest.mark.needs_display
def test_cancel_does_nothing(prefs, cache):
    """Cancel: nothing deleted, no task, nothing said."""
    fill(cache, {"maps/m": 5000})
    _row, button = cache_row(prefs._cache_group())
    button.emit("clicked")
    prefs.dialogs[0].emit("response", "cancel")
    assert (cache / "maps" / "m").exists() and prefs.errors == [] and prefs.toasts == []


@pytest.mark.needs_display
def test_preferences_have_the_storage_group(cache, monkeypatch):
    """The "Storage" group is in the preferences, after Sync, Language and Setup."""
    import types

    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    shown = []
    monkeypatch.setattr(Adw.PreferencesDialog, "present", lambda self, parent: shown.append(self))
    win = types.SimpleNamespace(busy=False, _action_buttons=[], _background_enabled=lambda: False,
                                set_background=lambda on, done=None: None, choose_dll=lambda: None,
                                error=lambda title, text: None, run_task=None, toast=None)
    for name in ("_button", "_cache_group", "_ask_clear_cache", "_render_cache_row",
                 "_account_group", "_account_action"):
        if hasattr(MainWindow, name):  # so that on old code the test fails on what it checks
            setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    titles, add = [], Adw.PreferencesPage.add
    monkeypatch.setattr(Adw.PreferencesPage, "add",
                        lambda page, group: titles.append(group.get_title()) or add(page, group))
    MainWindow.show_preferences(win)
    (dialog,) = shown
    assert titles == ["Garmin Connect account", "Sync with Garmin Connect", "Language", "Setup",
                      "Storage"]
    assert isinstance(dialog, Gtk.Widget)


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


@pytest.mark.needs_display
def test_the_preferences_show_the_account_and_log_in_or_out(monkeypatch):
    """The group "Garmin Connect account", first in the preferences: who is logged in with
    Log out, or "Not logged in" with Log In. The button goes the way of the device view
    (MainWindow.logout / login, so Q3 decides where the user goes), and the row follows a
    change of account while the preferences are open."""
    import types

    from gi.repository import Adw

    from openexpress.gui import app
    from openexpress.gui.app import MainWindow

    shown, calls = [], []
    monkeypatch.setattr(Adw.PreferencesDialog, "present", lambda self, parent: shown.append(self))
    client = types.SimpleNamespace(auth=types.SimpleNamespace(display="someone"))
    monkeypatch.setattr(app, "account_client", lambda: client)
    win = types.SimpleNamespace(busy=False, _action_buttons=[], _background_enabled=lambda: False,
                                set_background=lambda on, done=None: None, choose_dll=lambda: None,
                                error=lambda title, text: None, run_task=None, toast=None,
                                session=None, refresh_device=lambda **kw: None,
                                logout=lambda: calls.append("logout"),
                                login=lambda: calls.append("login"))
    for name in ("_button", "_cache_group", "_ask_clear_cache", "_render_cache_row",
                 "_account_group", "_account_action"):
        if hasattr(MainWindow, name):  # so that on old code the test fails on what it checks
            setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    groups, add = [], Adw.PreferencesPage.add
    monkeypatch.setattr(Adw.PreferencesPage, "add",
                        lambda page, group: groups.append(group) or add(page, group))
    MainWindow.show_preferences(win)
    assert groups[0].get_title() == "Garmin Connect account"
    (row,) = [w for w in _descendants(groups[0]) if isinstance(w, Adw.ActionRow)]
    (button,) = [w for w in _descendants(row) if isinstance(w, app.Gtk.Button)]
    assert (row.get_title(), button.get_label()) == ("someone", "Log out")
    button.emit("clicked")
    assert calls == ["logout"]
    client.auth = None  # logged out: the window tells of the change of account
    app.account_changed(win, app.Cause.LOGGED_OUT)
    assert (row.get_title(), button.get_label()) == ("Not logged in", "Log In")
    button.emit("clicked")
    assert calls == ["logout", "login"]
    shown[0].emit("closed")  # the preferences closed: the window keeps no widget of them
    assert getattr(win, "_account_row", None) is None
