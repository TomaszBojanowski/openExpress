"""The progress of maps: the name of the map and how far ("Downloading East · 1.2 MB of 3.4 MB",
"Copying East to the device · …", "Removing East · 1 of 3"); never the name of a file on the
device or on the server. All maps, devices and answers are made up."""
import base64

from test_ownmaps import device, map_folder, my_map  # noqa: F401 - fixtures
from test_updates_maps import maps_response, server  # noqa: F401 - the fixture

from openexpress import maps, ownmaps
from openexpress.device.storage import LocalStorage
from openexpress.progress import Step


def _unlock(ident):
    return {"unlocks": [{"filename": "Garmin/E2.gma", "gma": base64.b64encode(b"UNLOCK").decode()}]}


def _no_file_names(seen):
    return not [m for m in seen if ".img" in m.lower() or ".gma" in m.lower() or "Garmin/" in m]


def test_a_map_is_downloaded_and_copied_by_its_name(server, device_root, xdg):
    all_maps = maps.parse_maps(maps_response(server))
    east = all_maps[1]
    seen = []
    maps.install(east, all_maps, LocalStorage(device_root), _unlock, progress=seen.append)
    steps = [m for m in seen if isinstance(m, Step)]
    heads = list(dict.fromkeys(m.head for m in steps))
    # The device of the fixture keeps two files the map replaces.
    assert heads == [f"Downloading {east.name}", "Removing old map files",
                     f"Copying {east.name} to the device"]
    down = [m for m in steps if m.head.startswith("Downloading")]
    copy = [m for m in steps if m.head.startswith("Copying")]
    assert all(m.in_bytes and m.total == east.size for m in down) and down[-1].last
    assert all(m.in_bytes and m.total == sum(f.size for f in east.files) for m in copy)
    assert copy[-1].last
    assert "Activating the map on the Garmin server" in seen
    assert _no_file_names(seen)


def test_a_map_is_removed_by_its_name(server, device_root, xdg):
    all_maps = maps.parse_maps(maps_response(server))
    east = all_maps[1]
    st = LocalStorage(device_root)
    for path in maps.removal_list(all_maps, east, rewriting=False):  # the map's files there
        (device_root / path).parent.mkdir(parents=True, exist_ok=True)
        (device_root / path).write_bytes(b"map")
    seen = []
    removed = maps.uninstall(east, all_maps, st, progress=seen.append)
    assert removed  # the files of the map were there
    assert [str(m) for m in seen] == [f"Removing {east.name} · {n} of {len(removed)}"
                                      for n in range(1, len(removed) + 1)]


def test_an_own_map_is_copied_by_its_title(map_folder, my_map):
    st = LocalStorage(map_folder)
    dev = device()
    dev.unit_id = 4000000003  # made up
    plan = ownmaps.plan_add(dev, st, [], my_map, "mine.img")
    seen = []
    ownmaps.add(plan, dev, st, progress=seen.append)
    assert seen and all(isinstance(m, Step) and m.in_bytes for m in seen)
    assert {m.head for m in seen} == {f"Copying {plan.title} to the device"}
    assert seen[-1].last and _no_file_names(seen)
