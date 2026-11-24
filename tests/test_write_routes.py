"""Every folder a server's answer writes to has a route, decided by the program's own rules: the
ordinary write (a folder the ordinary listing shows, or one made for the write), openExpress's
own session (a folder the device hides at its top level, or one it names for its components in
GarminDevice.xml inside a folder that is seen), or a refusal. None may reach the ordinary making
of a folder the device has and hides: that write fails ("Cannot create directory …").

Each target is routed by the rule of what writes it: a file of an update by the rule of the
updates (updates.place, the folders hidden inside a seen one, updates.installable), a file of a
map by the rule of the maps (maps.mark_waiting, maps.can_install: a folder of the top level the
listing does not show, where a map is refused outside the command line's trial, and a folder
hidden inside a seen one by the rule of the updates, where a map is always refused). Both
are asked twice: with openExpress's own session to be had, and without it (the Flatpak without
USB, a device not over MTP), where every hidden target is refused, never written the ordinary way.

The data: for each device, the folders it hides as a reading of its own session found them (with
what its GarminDevice.xml says of each), the files it hides in folders the ordinary listing shows,
and the folders and files the saved answers of Garmin's servers write to, by answer: one whose name says "map" is the maps answer, any other the update
server's (whose saved answer may carry the maps too, into the same folders). A made-up device in
tests/data/write_routes/ always; the real devices of testdata/write-routes/ when they are there
(made with the private tool of the readings)."""

import json
import posixpath
import types
from pathlib import Path

import pytest
from fakes import hidden_at, map_entry

from openexpress import hidden_folders, maps, updates
from openexpress.device.storage import LocalStorage

ROOT = Path(__file__).resolve().parent.parent
MADE_UP = sorted((ROOT / "tests" / "data" / "write_routes").glob("*.json"))
REAL = sorted((ROOT / "testdata" / "write-routes").glob("*.json"))

ORDINARY, OWN_SESSION, OWN_SESSION_TOP = "ordinary", "own session", "own session (top level)"
REFUSED = "refused"
UPDATES, MAPS = "updates", "maps"


def _kinds(sources: dict) -> list[str]:
    """What writes into a folder: the maps answer, the update server's answer, or both."""
    return sorted({MAPS if "map" in name.casefold() else UPDATES for name in sources})


def _update(folder: str) -> updates.SoftwareUpdate:
    return updates.SoftwareUpdate(
        part_number="006-D0000-00", name="Made-up Part", version="1.00", severity="Recommended",
        is_primary=False, is_reinstall=False, restart_required=False,
        device_path=f"{folder}/made_up.bin", url="https://example.org/made_up.bin",
        md5="0123456789ABCDEF0123456789ABCDEF", size=4, order=0)


def _device(root: Path, data: dict) -> tuple[LocalStorage, dict[str, str], types.SimpleNamespace]:
    """The device as the ordinary listing shows it (GARMIN and every target folder not hidden),
    the folders it hides inside a seen one (updates.hidden_inside), and what its GarminDevice.xml
    names for its components. A device without saved answers: the hidden folders its
    GarminDevice.xml names, where the server would write its components."""
    hidden = [h["folder"] for h in data["hidden"]]
    named = [h["folder"] for h in data["hidden"] if h["xml"] == "names the folder"]
    (root / "GARMIN").mkdir(parents=True, exist_ok=True)
    for folder in data["targets"] or named:
        if hidden_at(folder, hidden) is None:
            (root / folder).mkdir(parents=True, exist_ok=True)
    storage = LocalStorage(root)
    device = types.SimpleNamespace(update_files=[
        types.SimpleNamespace(path=f, filename="file") for f in named])
    return storage, updates.hidden_inside(storage, device), device


def _marked(storage: LocalStorage, u: updates.SoftwareUpdate, inside: dict[str, str],
            listing: hidden_folders.Listing, session: bool) -> None:
    """`u` as updates.mark_present marks it on this device: `inside`, its folders hidden inside
    a seen one (updates.hidden_inside); `listing`, what openExpress's own session read on this
    connection (hidden_folders.kept); `session`: whether that session can be had."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(hidden_folders, "kept", lambda _storage: listing)
        updates.mark_present(storage, [u], inside=inside, writable=session)


def _update_route(folder: str, storage: LocalStorage, inside: dict[str, str],
                  session: bool) -> str:
    """As updates.mark_present and _install route it: a top folder not seen read through the
    session when it can be had; a folder hidden inside a seen one written through it."""
    u = _update(folder)
    listing = hidden_folders.Listing()
    if session:
        listing.folders[folder.casefold()] = (folder, [])
    else:
        listing.state = hidden_folders.NO_SESSION
    _marked(storage, u, inside, listing, session)
    if not updates.installable([u]):
        return REFUSED
    if u.hidden_folder is not None:
        return OWN_SESSION_TOP
    return OWN_SESSION if u.hidden_inside is not None else ORDINARY


def _map_route(folder: str, storage: LocalStorage, device) -> str:
    """As maps.mark_waiting and _install route it: written the ordinary way, or refused (a
    folder of the top level the listing does not show, or one hidden inside a seen one)."""
    found, _count = maps.read_maps({"maps": [map_entry("Made-up Map", f"{folder}/D0000001A.img",
                                                       part="M")]})
    maps.mark_waiting(storage, found, device)
    return ORDINARY if maps.can_install(found[0]) else REFUSED


def routes(data: dict, root: Path, session: bool = True) -> dict[str, dict[str, str]]:
    """Each target folder: its route for each kind of answer that writes there."""
    storage, inside, device = _device(root, data)
    targets = data["targets"] or {f.path: {"GarminDevice.xml": 1} for f in device.update_files}
    out = {}
    for folder, sources in targets.items():
        out[folder] = {kind: (_map_route(folder, storage, device) if kind == MAPS
                              else _update_route(folder, storage, inside, session))
                       for kind in _kinds(sources)}
    return out


def _wrong(data: dict, root: Path, session: bool = True) -> list[str]:
    hidden = [h["folder"] for h in data["hidden"]]
    return [f"{folder} ({kind}): the ordinary write, into {at}, which the device hides"
            for folder, by_kind in routes(data, root, session).items()
            if (at := hidden_at(folder, hidden)) is not None
            for kind, route in by_kind.items() if route == ORDINARY]


@pytest.mark.parametrize("path", MADE_UP, ids=[p.stem for p in MADE_UP])
@pytest.mark.parametrize("session", [True, False], ids=["session", "no session"])
def test_the_made_up_devices_have_a_route_for_every_folder(path, tmp_path, session):
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = data["expected" if session else "expected_without_session"]
    assert routes(data, tmp_path, session) == expected


def test_a_hidden_folder_the_device_does_not_name_is_caught(tmp_path):
    """The guard itself: a target in a hidden folder that GarminDevice.xml does not name would
    take the ordinary write, and the test says so."""
    data = {"hidden": [{"folder": "GARMIN/Secret", "xml": "names its folder"}],
            "targets": {"GARMIN/Secret": {"made-up answer": 1}, "GARMIN/SID": {"made-up": 2}}}
    assert _wrong(data, tmp_path) == [
        "GARMIN/Secret (updates): the ordinary write, into GARMIN/Secret, which the device hides"]


@pytest.mark.parametrize("session", [True, False], ids=["session", "no session"])
def test_a_map_into_a_folder_hidden_inside_a_seen_one_is_refused(tmp_path, session):
    """A folder the device hides inside a seen one, which its GarminDevice.xml names: a map is
    refused there, as the maps are not written through openExpress's own session."""
    data = {"hidden": [{"folder": "GARMIN/Maps", "xml": "names the folder"}],
            "targets": {"GARMIN/Maps": {"made-up maps answer": 1}}}
    assert routes(data, tmp_path, session) == {"GARMIN/Maps": {MAPS: REFUSED}}
    assert _wrong(data, tmp_path, session) == []


@pytest.mark.skipif(not REAL, reason="no readings of real devices in testdata/write-routes/")
@pytest.mark.parametrize("path", REAL, ids=[p.stem for p in REAL])
@pytest.mark.parametrize("session", [True, False], ids=["session", "no session"])
def test_no_folder_of_a_real_device_takes_the_ordinary_write_into_a_hidden_one(path, tmp_path,
                                                                              session):
    data = json.loads(path.read_text(encoding="utf-8"))
    wrong = _wrong(data, tmp_path, session)
    assert wrong == [], f"{data['device']} {data['software']}: " + "; ".join(wrong)


# --- files the device hides in a folder the ordinary listing shows ----------------------------
#
# A known behaviour, kept on purpose: such a file is not seen by the ordinary listing, and no rule
# looks for it, so an update writes it the ordinary way, as any file of a folder that is seen. A
# fēnix 8 with software 24.20 keeps GARMIN/SQL/MapRegionGrouping.db so; an ordinary write of that
# file over it replaced it, without an error and without a second, seen file of that name. It
# stays so until such files are written through openExpress's own session.


def file_routes(data: dict, root: Path) -> dict[str, str]:
    """Each file an update answer writes into a folder the ordinary listing shows: its route, as
    updates.mark_present and _install take it."""
    storage, inside, _ = _device(root, data)
    hidden = [h["folder"] for h in data["hidden"]]
    out = {}
    for path, answers in data.get("target_files", {}).items():
        folder = posixpath.dirname(path)
        if UPDATES not in _kinds(dict.fromkeys(answers)) or hidden_at(folder, hidden):
            continue  # a map's, or one in a hidden folder: the rule of the folders
        u = _update(folder)
        u.device_path = path
        _marked(storage, u, inside, hidden_folders.Listing(), session=True)
        if not updates.installable([u]):
            out[path] = REFUSED
        elif u.hidden_folder is not None or u.hidden_inside is not None:
            out[path] = OWN_SESSION
        else:
            out[path] = ORDINARY
    return out


@pytest.mark.parametrize("path", [*MADE_UP, *REAL], ids=[p.stem for p in [*MADE_UP, *REAL]])
def test_a_known_behaviour_a_file_hidden_in_a_seen_folder_takes_the_ordinary_write(path,
                                                                                    tmp_path):
    data = json.loads(path.read_text(encoding="utf-8"))
    hidden_files = {h.casefold() for h in data.get("hidden_files", [])}
    got = file_routes(data, tmp_path)
    assert {p: route for p, route in got.items()
            if p.casefold() in hidden_files and route != ORDINARY} == {}


@pytest.mark.parametrize("path", MADE_UP, ids=[p.stem for p in MADE_UP])
def test_the_made_up_device_s_files(path, tmp_path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert file_routes(data, tmp_path) == data["expected_files"]


@pytest.mark.parametrize("path", [*MADE_UP, *REAL], ids=[p.stem for p in [*MADE_UP, *REAL]])
def test_without_a_session_every_hidden_target_is_refused(path, tmp_path):
    """Where openExpress's own session cannot be had, nothing goes through it, and every target
    in a folder the device hides is refused."""
    data = json.loads(path.read_text(encoding="utf-8"))
    hidden = [h["folder"] for h in data["hidden"]]
    got = routes(data, tmp_path, session=False)
    assert {route for by_kind in got.values() for route in by_kind.values()} <= {ORDINARY,
                                                                                 REFUSED}
    assert {folder: by_kind for folder, by_kind in got.items()
            if hidden_at(folder, hidden) is not None
            and set(by_kind.values()) != {REFUSED}} == {}
