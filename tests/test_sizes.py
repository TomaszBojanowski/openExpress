"""Sizes as the user reads them: powers of 1000 with the units B, kB, MB and GB, like GNOME
Files, and readable sizes instead of raw byte counts in messages."""

import base64

import pytest
from test_sync import UPLOAD_CONFIG, FakeClient, _setup
from test_updates_maps import maps_response, server, sw_response  # noqa: F401 - the fixture

from openexpress import i18n, maps, music, sync, updates
from openexpress.device.storage import LocalStorage
from openexpress.i18n import human_size


@pytest.fixture
def polish():
    i18n.setup("pl")
    yield
    i18n.setup("en")


VALUES = (None, 0, 1, 999, 1000, 1500, 2048, 699392, 999_949, 999_950, 1_000_000,
          64 * 1024 * 1024, 999_999_999, 6_657_199_308, 2_500_000_000_000)


def test_sizes_use_powers_of_1000_like_gnome_files():
    assert [human_size(n) for n in VALUES] == [
        "?", "0 B", "1 B", "999 B", "1.0 kB", "1.5 kB", "2.0 kB", "699.4 kB", "999.9 kB",
        "1.0 MB",  # 999.95 kB is not written as "1000.0 kB"
        "1.0 MB", "67.1 MB", "1.0 GB", "6.7 GB", "2500.0 GB"]


def test_sizes_in_polish_only_differ_in_the_separator(polish):
    assert [human_size(n) for n in VALUES] == [
        "?", "0 B", "1 B", "999 B", "1,0 kB", "1,5 kB", "2,0 kB", "699,4 kB", "999,9 kB",
        "1,0 MB", "1,0 MB", "67,1 MB", "1,0 GB", "6,7 GB", "2500,0 GB"]


def test_commands_and_window_use_the_same_function():
    from openexpress import cli
    from openexpress.gui import app

    assert cli._human is human_size and app._human is human_size


def uploading(device_root, tmp_path):
    (device_root / "GARMIN/Activity/2026-10-03-10-00-00.fit").write_bytes(b"x" * 1500)
    st, dev, state = _setup(device_root, tmp_path)
    plan = sync.plan_uploads(dev, st, UPLOAD_CONFIG, state)
    lines = []
    sync.run_uploads(plan, dev, st, FakeClient(), state, progress=lines.append,
                     wait_for_processing=False)
    return [line for line in lines if "2026-10-03" in line]


def test_upload_progress_shows_a_readable_size(device_root, tmp_path):
    assert uploading(device_root, tmp_path) == [
        "Uploading FIT_TYPE_4/2026-10-03-10-00-00.fit (1.5 kB)"]


def test_upload_progress_in_polish(polish, device_root, tmp_path):
    assert uploading(device_root, tmp_path) == [
        "Wysyłanie FIT_TYPE_4/2026-10-03-10-00-00.fit (1,5 kB)"]


def test_update_download_progress_shows_a_readable_size(server, device_root, xdg):
    items = updates.parse_updates(sw_response(server))
    items[1].size = None  # the server does not always say
    lines = []
    updates.install(items, LocalStorage(device_root), progress=lines.append)
    downloads = [line for line in lines if line.startswith("Downloading ")]
    assert downloads == [
        f"Downloading System {items[0].version} ({human_size(items[0].size)})",
        f"Downloading Aux {items[1].version} (?)"]


def test_map_download_progress_shows_a_readable_size(server, device_root, xdg):
    all_maps = maps.parse_maps(maps_response(server))
    east = all_maps[1]
    lines = []
    maps.install(east, all_maps, LocalStorage(device_root),
                 lambda ident: {"unlocks": [{"filename": "Garmin/E2.gma",
                                             "gma": base64.b64encode(b"UNLOCK").decode()}]},
                 progress=lines.append)
    downloads = [line for line in lines if line.startswith("Downloading ")]
    assert downloads == [f"Downloading {f.device_path} ({human_size(f.size)})"
                         for f in east.files]
    assert downloads  # every file, each with its own size


def not_enough_space(server, device_root, tmp_path, monkeypatch):
    """The three places that refuse to write when the device is too full."""
    monkeypatch.setattr(LocalStorage, "free_space", lambda self: 1_200_000)
    st = LocalStorage(device_root)
    said = []
    with pytest.raises(updates.UpdateError) as error:
        updates.install(updates.parse_updates(sw_response(server)), st)
    said.append(str(error.value))
    all_maps = maps.parse_maps(maps_response(server))
    with pytest.raises(maps.MapError) as error:
        maps.install(all_maps[1], all_maps, st, lambda ident: {})
    said.append(str(error.value))
    monkeypatch.setattr(music.CopyPlan, "size", property(lambda self: 2_500_000))
    with pytest.raises(music.MusicError) as error:
        music.copy(music.CopyPlan(), st)
    said.append(str(error.value))
    return said


def test_not_enough_space_is_said_with_readable_sizes(server, device_root, xdg, tmp_path,
                                                      monkeypatch):
    said = not_enough_space(server, device_root, tmp_path, monkeypatch)
    for text in said:
        assert text.startswith("Not enough space on the device: ")
        assert text.endswith(" needed, 1.2 MB free") and "1200000" not in text
    assert said[2] == "Not enough space on the device: 2.5 MB needed, 1.2 MB free"


def test_not_enough_space_in_polish(polish, server, device_root, xdg, tmp_path,
                                    monkeypatch):
    said = not_enough_space(server, device_root, tmp_path, monkeypatch)
    assert said[2] == "Za mało miejsca na urządzeniu: potrzeba 2,5 MB, wolne 1,2 MB"
    assert all(text.endswith(", wolne 1,2 MB") for text in said)
