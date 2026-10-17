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
