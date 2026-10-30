"""The progress of software updates: each update by its name and how far, in bytes
("Downloading System · 1.2 MB of 3.4 MB", "Copying System to the device · …"); never the
name of a file on the device or on the server. All updates and devices are made up."""
from test_updates_maps import server, sw_response  # noqa: F401 - the fixture

from openexpress import i18n, updates
from openexpress.device.storage import LocalStorage
from openexpress.progress import Step


def _no_file_names(seen):
    return not [m for m in seen
                if "gupdate" in m.lower() or ".gcd" in m.lower() or "Garmin/" in m]


def _install(server, device_root, **kw):
    items = updates.parse_updates(sw_response(server))
    seen = []
    updates.install(items, LocalStorage(device_root), progress=seen.append, **kw)
    return items, seen


def test_each_update_is_downloaded_and_copied_by_its_name(server, device_root, xdg):
    items, seen = _install(server, device_root)
    steps = [m for m in seen if isinstance(m, Step)]
    assert steps == seen  # nothing but steps
    heads = list(dict.fromkeys(m.head for m in steps))
    # Downloaded in the server's order, copied the system last.
    assert heads == ["Downloading System", "Downloading Aux",
                     "Copying Aux to the device", "Copying System to the device"]
    for u in items:
        for verb in ("Downloading {}", "Copying {} to the device"):
            mine = [m for m in steps if m.head == verb.format(u.title)]
            assert mine and all(m.in_bytes and m.total == u.size for m in mine), (verb, u.title)
            assert mine[-1].last
    assert _no_file_names(seen)


def test_an_update_in_the_cache_is_told_as_downloaded(server, device_root, xdg):
    _install(server, device_root)
    items, seen = _install(server, device_root)  # the files come from the cache now
    down = [m for m in seen if m.head.startswith("Downloading")]
    assert [m.head for m in down] == ["Downloading System", "Downloading Aux"]
    assert all(m.last for m in down)


def test_an_update_of_unknown_size_says_its_size_once_it_is_there(server, device_root, xdg):
    items = updates.parse_updates(sw_response(server))
    items[1].size = None  # the server does not always say
    seen = []
    updates.install(items, LocalStorage(device_root), progress=seen.append)
    down = [m for m in seen if m.head == "Downloading Aux"]
    assert down[-1].last and down[-1].total == (device_root / "GARMIN/RemoteSW/aux.gcd").stat(
        ).st_size


def test_update_progress_in_polish(server, device_root, xdg):
    i18n.setup("pl")
    try:
        _items, seen = _install(server, device_root)
    finally:
        i18n.setup("en")
    heads = list(dict.fromkeys(m.head for m in seen))
    assert heads == ["Pobieranie System", "Pobieranie Aux",
                     "Kopiowanie Aux na urządzenie", "Kopiowanie System na urządzenie"]
    assert " z " in seen[-1]


def test_an_install_nobody_listens_to_tells_nothing(server, device_root, xdg):
    """No progress: the copy is told nothing (over MTP in the Flatpak, gio copy without -p)."""
    copied = []

    class Told(LocalStorage):
        def write_file(self, path, local, progress=None, **kw):
            copied.append(progress)
            return super().write_file(path, local, progress, **kw)

    items = updates.parse_updates(sw_response(server))
    updates.install(items, Told(device_root))
    assert copied == [None, None]
