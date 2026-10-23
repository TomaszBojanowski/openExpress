"""The cache of openExpress: how big it is, part by part (C1), and clearing it, all of it or
some parts (maps, updates, pictures, temp)."""

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
    """Known sizes, a known total: files in subfolders and unfinished downloads count, and
    tmp with the temporary files; a folder openExpress does not make and a symbolic link to
    a file outside do not."""
    from openexpress import cache as oe_cache

    fill(cache, {"maps/aaa": 1000, "maps/bbb.part": 500, "maps/sub/ccc": 250,
                 "images/device-006-B4532-00.png": 30,
                 "software/006-B4532-00/23.40/GUPDATE.GCD": 4000,
                 "tmp/other": 99, "unknown/file": 55})
    (cache / "transfer").mkdir()
    fill(tmp_path, {"outside/big": 7777})
    os.symlink(tmp_path / "outside" / "big", cache / "maps" / "link")
    use = oe_cache.usage()
    assert use.root == cache
    assert use.sizes == {"maps": 1750, "updates": 4000, "pictures": 30, "temp": 99}
    assert use.files == {"maps": 3, "updates": 1, "pictures": 1, "temp": 1}
    assert use.total == 5879


def test_no_cache_yet_is_nothing(cache):
    """No cache folder at all: every size 0, no error, and nothing is made."""
    from openexpress import cache as oe_cache

    use = oe_cache.usage()
    assert use.sizes == {"maps": 0, "updates": 0, "pictures": 0, "temp": 0}
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


def test_cache_command_lists_the_four_parts_and_the_total(cache, capsys):
    """`openexpress cache`: the folder, each of the four parts with its size, the total; exit
    code 0; nothing deleted."""
    from openexpress.cli import main

    fill(cache, {"maps/aaa": 1750, "images/p.png": 30, "software/s/1/f": 4000})
    assert main(["cache"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Cache: {cache}",
        "  Maps: 1.8 kB",
        "  Software updates: 4.0 kB",
        "  Pictures and icons: 30 B",
        "  Temporary files: 0 B",
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
                 "transfer/openexpress-staged": 20, "tmp/other": 99, "notes.txt": 7,
                 "unknown/file": 55})
    fill(tmp_path, {"outside/big": 7777})
    os.symlink(tmp_path / "outside" / "big", cache / "maps" / "link")
    return {"unknown": cache / "unknown" / "file", "notes": cache / "notes.txt",
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
        "  Software updates: 4.0 kB",
        "  Pictures and icons: 30 B",
        "  Temporary files: 119 B",
        "  Total: 5.9 kB",
        "Dry run: nothing was deleted. Add --apply to delete it."]
    assert sorted(p for p in cache.rglob("*")) == before


def test_clear_with_apply_empties_the_folders_of_the_four_parts(cache, tmp_path, capsys):
    """--apply: what is in the folders of the four parts goes; the folders themselves,
    cache.lock and all else stay (a link is removed as a link, the file it points to stays).
    It says how much was freed."""
    from openexpress.cli import main

    stay = filled(cache, tmp_path)
    assert main(["cache", "--clear", "--apply"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "Freed 5.9 kB." and "  Total: 5.9 kB" in out
    for name in ("maps", "images", "software", "transfer", "tmp"):
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


# --- some parts from the command line: `cache --clear --only maps,updates [--apply]` ------------


def _log_lines(log_file) -> list[str]:
    """The lines of the log after "cache: ", as the command line wrote them."""
    text = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    return [line.split(" cache: ", 1)[1] for line in text.splitlines() if " cache: " in line]


def test_clear_some_parts_without_apply_says_their_sizes(cache, tmp_path, capsys, log_file):
    """Dry run of two parts: their sizes and their total, then the dry run line; nothing is
    deleted; the log has one line for each of them."""
    from openexpress.cli import main

    filled(cache, tmp_path)
    before = sorted(p for p in cache.rglob("*"))
    assert main(["cache", "--clear", "--only", "maps,updates"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Cache: {cache}",
        "  Maps: 1.8 kB",
        "  Software updates: 4.0 kB",
        "  Total: 5.8 kB",
        "Dry run: nothing was deleted. Add --apply to delete it."]
    assert sorted(p for p in cache.rglob("*")) == before
    assert _log_lines(log_file) == ["maps: 1_750 B in 3 files, dry run: nothing deleted",
                                    "updates: 4_000 B in 1 file, dry run: nothing deleted"]


def test_clear_some_parts_with_apply(cache, tmp_path, capsys, log_file):
    """--apply with the parts named in any order and in two --only: only theirs go, the
    others stay; it says how much was freed, and the log one line for each part."""
    from openexpress.cli import main

    filled(cache, tmp_path)
    assert main(["cache", "--clear", "--only", "temp", "--only", " pictures ", "--apply"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Cache: {cache}",
        "  Pictures and icons: 30 B",
        "  Temporary files: 119 B",
        "  Total: 149 B",
        "Freed 149 B."]
    left = _left(cache)
    assert left["images"] == left["transfer"] == left["tmp"] == []
    assert left["maps"] and left["software"]
    assert _log_lines(log_file) == ["pictures: cleared, 30 B freed (1 file)",
                                    "temp: cleared, 119 B freed (2 files)"]


def test_the_sizes_of_some_parts(cache, tmp_path, capsys, log_file):
    """--only without --clear: the sizes of those parts alone."""
    from openexpress.cli import main

    filled(cache, tmp_path)
    assert main(["cache", "--only", "pictures"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"Cache: {cache}", "  Pictures and icons: 30 B", "  Total: 30 B"]
    assert _log_lines(log_file) == ["pictures: 30 B in 1 file"]


@pytest.mark.parametrize("only", ["maps,images", "", ", ,"])
def test_a_part_that_is_not_one_is_an_error_with_the_list(cache, tmp_path, capsys, only):
    """A name that is no part (or none named): exit code 1, the parts there are, nothing
    deleted, even with --apply."""
    from openexpress.cli import main

    filled(cache, tmp_path)
    before = sorted(p for p in cache.rglob("*"))
    assert main(["cache", "--clear", "--only", only, "--apply"]) == 1
    captured = capsys.readouterr()
    unknown = "images" if "images" in only else ""
    assert captured.err.strip() == (
        (f"Error: Not a part of the cache: {unknown}. " if unknown
         else "Error: No part of the cache named. ")
        + "The parts are: maps, updates, pictures, temp.")
    assert captured.out == ""
    assert sorted(p for p in cache.rglob("*")) == before


def test_the_help_names_the_parts(capsys):
    from openexpress.cli import main

    with pytest.raises(SystemExit):
        main(["cache", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--only PARTS" in help_text
    assert "maps, updates, pictures, temp" in help_text


# --- clearing some parts of the cache: cache.clear(only=…) ---------------------------------------

# The folders of each part, as the user knows them; and a file in each, of a known size.
PARTS = {"maps": ["maps"], "updates": ["software"], "pictures": ["images"],
         "temp": ["transfer", "tmp"]}
SIZES = {"maps": 1750, "updates": 4000, "pictures": 30, "temp": 119}


def _left(cache) -> dict[str, list[str]]:
    """What is still in each folder of the cache, as paths below it."""
    return {name: sorted(str(p.relative_to(cache / name)) for p in (cache / name).rglob("*"))
            for name in ("maps", "software", "images", "transfer", "tmp")
            if (cache / name).is_dir() and not (cache / name).is_symlink()}


@pytest.mark.parametrize("part", list(PARTS))
def test_one_part_is_cleared_and_the_others_stay(cache, tmp_path, part):
    """Only the folders of that part are emptied (the folders stay); every other part,
    cache.lock and what openExpress does not keep stay as they were. What was there is
    returned: its total is what was freed."""
    from openexpress import cache as oe_cache

    stay = filled(cache, tmp_path)
    before = _left(cache)
    freed = oe_cache.clear(only=[part])
    assert freed.sizes == {part: SIZES[part]} and freed.total == SIZES[part]
    after = _left(cache)
    for name, files in before.items():
        assert after[name] == ([] if name in PARTS[part] else files), name
    assert (cache / "cache.lock").exists()
    assert all(p.exists() for p in stay.values())
    assert oe_cache.usage().sizes == {k: 0 if k == part else v for k, v in SIZES.items()}


def test_some_parts_at_once_and_all_by_default(cache, tmp_path):
    """Two parts at once (in any order, named twice): both, in the order of the parts; no
    parts named: all four."""
    from openexpress import cache as oe_cache

    filled(cache, tmp_path)
    freed = oe_cache.clear(only=["temp", "maps", "temp"])
    assert list(freed.sizes) == ["maps", "temp"] and freed.total == 1750 + 119
    assert oe_cache.usage().sizes == {"maps": 0, "updates": 4000, "pictures": 30, "temp": 0}
    freed = oe_cache.clear()
    assert list(freed.sizes) == ["maps", "updates", "pictures", "temp"]
    assert freed.total == 4030 and oe_cache.usage().total == 0
    assert (cache / "cache.lock").exists()


def test_a_part_that_does_not_exist_is_refused(cache, tmp_path):
    """A name that is no part of the cache: nothing is deleted."""
    from openexpress import cache as oe_cache

    filled(cache, tmp_path)
    before = _left(cache)
    with pytest.raises(ValueError, match="'images'"):
        oe_cache.clear(only=["pictures", "images"])
    assert _left(cache) == before


@pytest.mark.parametrize("part", list(PARTS))
def test_a_part_is_not_cleared_while_the_cache_is_in_use(cache, tmp_path, part):
    """One lock for the whole cache: while anything uses it (a download, an install, a file
    on its way to the device), no part is cleared, not even the pictures."""
    from openexpress import cache as oe_cache

    filled(cache, tmp_path)
    before = _left(cache)
    with oe_cache.using(), pytest.raises(oe_cache.CacheBusy):
        oe_cache.clear(only=[part])
    assert _left(cache) == before


@pytest.mark.parametrize("part", list(PARTS))
def test_a_folder_of_a_part_that_is_a_link_is_left_alone(cache, tmp_path, part):
    """Every folder of the part is a link to a folder elsewhere: neither the link nor what is
    behind it is touched, and the part counts as nothing."""
    from openexpress import cache as oe_cache

    cache.mkdir(parents=True)
    for name in PARTS[part]:
        fill(tmp_path, {f"elsewhere-{name}/file": 5000})
        os.symlink(tmp_path / f"elsewhere-{name}", cache / name)
    assert oe_cache.usage().sizes[part] == 0
    assert oe_cache.clear(only=[part]).total == 0
    for name in PARTS[part]:
        assert (cache / name).is_symlink()
        assert (tmp_path / f"elsewhere-{name}" / "file").exists()


@pytest.mark.parametrize("part", list(PARTS))
def test_a_link_inside_a_part_goes_as_a_link(cache, tmp_path, part):
    """A link in a folder of the part, to a file and to a folder outside: the links go,
    what they point to stays."""
    from openexpress import cache as oe_cache

    fill(tmp_path, {"outside/file": 10, "outside-dir/file": 20})
    folder = cache / PARTS[part][-1]
    folder.mkdir(parents=True)
    os.symlink(tmp_path / "outside" / "file", folder / "to-a-file")
    os.symlink(tmp_path / "outside-dir", folder / "to-a-folder")
    oe_cache.clear(only=[part])
    assert list(folder.iterdir()) == []
    assert (tmp_path / "outside" / "file").exists()
    assert (tmp_path / "outside-dir" / "file").exists()


def test_the_lock_is_never_deleted(cache, tmp_path):
    """cache.lock stays through every clearing, of each part and of all; a file of that name
    inside a part is a file of the part (only the cache's own lock is kept)."""
    from openexpress import cache as oe_cache

    filled(cache, tmp_path)
    fill(cache, {"tmp/cache.lock": 3})
    for part in [*PARTS, None]:
        oe_cache.clear(only=None if part is None else [part])
        assert (cache / "cache.lock").is_file()
    assert not (cache / "tmp" / "cache.lock").exists()


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
