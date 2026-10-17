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
    path, why = app.download_picture(http, app.PICTURE_HOST + "p.png", "device-x.png")
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
