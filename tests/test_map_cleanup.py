"""Point 8 of package 3, stage 1: the cleanup rules of the map service ("directoriesToRemove":
a folder, a pattern of file names, a description). Garmin Express deletes every file that
matches, as a regular expression, anywhere in the name. openExpress only reads them, says
them in the log and shows, before a map is installed, updated or removed, which files a safe
reading of them would take: whole names only, only in a folder of the maps of the answer,
only with the extension of a map file, never a file of a map of the answer, a map of your own
or a map file the server does not know. Nothing is deleted. Every rule and file is made up."""

import json
import logging
import types

import pytest
from test_ownmaps import img_header
from test_updates_maps import maps_response

from openexpress import maps as mp
from openexpress import ownmaps, paths
from openexpress.device.storage import DeviceError, LocalStorage
from openexpress.device.xmldevice import parse

BASE = "https://maps.example"
HEADER = "Old files Garmin asks to remove:"
NOT_YET = "openExpress does not remove these files yet."


def rule(file_match, path="Garmin", external=None, description="old unlocks"):
    return {"description": description, "path": path, "externalpath": external,
            "filematch": file_match}


def answer(*rules):
    data = maps_response(BASE)
    data["directoriestoremove"] = list(rules)
    return json.loads(json.dumps(data))


@pytest.fixture
def device(device_root):
    """The made-up device with old files in its GARMIN folder."""
    g = device_root / "GARMIN"
    for name in ("OLD1.GMA", "MYOLD1.gma", "OLD1.gma.bak", "W.img", "junk.img"):
        (g / name).write_bytes(b"old")
    (g / "gmapsupp.img").write_bytes(img_header(b"A map loaded by hand"))
    (g / "sub").mkdir()
    (g / "sub" / "OLD2.gma").write_bytes(b"old")
    st = LocalStorage(device_root)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def plan(storage, device, *rules):
    maps, _listed = mp.read_maps(answer(*rules))
    return [f.path for f in mp.cleanup_plan(storage, device, maps)]


def test_the_cleanup_rules_of_the_answer_are_read():
    maps, _listed = mp.read_maps(answer(rule("OLD*.gma")))
    assert maps and all(m.cleanup_rules == (mp.CleanupRule("Garmin", None, "OLD*.gma",
                                                           "old unlocks"),) for m in maps)
    plain, _listed = mp.read_maps(answer())
    assert all(m.cleanup_rules == () for m in plain)


def test_a_rule_matches_whole_names_in_its_folder_only(device):
    """Garmin Express would take MYOLD1.gma and OLD1.gma.bak too (any part of the name)."""
    st, dev = device
    assert plan(st, dev, rule("OLD*.gma")) == ["GARMIN/OLD1.GMA"]


def test_a_rule_never_lists_maps_of_garmin_or_your_own(device):
    st, dev = device
    # W.img is a map of the answer; gmapsupp.img a map the server does not know (it has the
    # header of one); GarminDevice.xml is never a map file.
    assert plan(st, dev, rule("*.img"), rule("*.xml")) == ["GARMIN/junk.img"]
    # A map of your own that this installation copied: never either.
    folder = paths.device_dir(dev.unit_id)
    folder.mkdir(parents=True)
    (folder / ownmaps.STATE_NAME).write_text(json.dumps({"maps": [{"filename": "junk.img"}]}))
    assert plan(st, dev, rule("*.img")) == []


@pytest.mark.parametrize(("bad", "why"), [
    (rule("OLD?.gma"), "pattern"), (rule("OLD[1].gma"), "pattern"), (rule("*"), "pattern"),
    (rule(""), "pattern"), (rule("OLD*.gma", path=""), "folder"),
    (rule("OLD*.gma", path="/"), "folder"), (rule("OLD*.gma", path="../x"), "folder"),
    (rule("OLD*.gma", path="GARMIN/Activity"), "folder"),
    (rule("OLD*.gma", path=None, external="Garmin"), "memory card")])
def test_rules_with_other_characters_or_folders_are_skipped(device, bad, why, caplog):
    st, dev = device
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert plan(st, dev, bad) == []
    assert f"maps: a cleanup rule was skipped ({why})" in [r.getMessage() for r in caplog.records]


def test_a_rule_with_a_folder_on_the_memory_card_too_keeps_its_own_folder(device):
    st, dev = device
    assert plan(st, dev, rule("OLD*.gma", external="SD/Garmin")) == ["GARMIN/OLD1.GMA"]


def test_an_unreadable_folder_gives_no_plan(device, caplog):
    st, dev = device

    def broken(path):
        raise DeviceError("gone")

    st.listdir = broken
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert plan(st, dev, rule("OLD*.gma")) == []
    assert any("cleanup rules could not be checked (DeviceError)" in r.getMessage()
               for r in caplog.records)


def test_the_rules_and_the_counts_are_in_the_log(device, caplog):
    st, dev = device
    with caplog.at_level(logging.INFO, logger="openexpress"):
        plan(st, dev, rule("OLD*.gma"), rule("OLD?.gma"))
    lines = [r.getMessage() for r in caplog.records if "cleanup" in r.getMessage()]
    assert ("maps: cleanup rule 1: folder 'Garmin', memory card None, files 'OLD*.gma', "
            "description 'old unlocks'") in lines
    assert "maps: 2 cleanup rules, 1 files match (1 rules skipped)" in lines
    assert not any("OLD1.GMA" in line for line in lines)  # the files of the device: never


def test_without_rules_nothing_is_read(device, caplog):
    st, dev = device

    def never(path):
        raise AssertionError("a folder was listed")

    st.listdir = never
    with caplog.at_level(logging.INFO, logger="openexpress"):
        assert plan(st, dev) == []
    assert "maps: 0 cleanup rules" in [r.getMessage() for r in caplog.records]


# --- the command line ------------------------------------------------------------------------


@pytest.fixture
def cli_maps(monkeypatch, device):
    from openexpress import cli

    st, dev = device

    def load(args, client, own=False):
        maps, listed = mp.read_maps(answer(rule("OLD*.gma")))
        return st, dev, maps, listed

    monkeypatch.setattr(cli, "_load_maps", load)
    return st


@pytest.mark.parametrize("apply", [False, True])
def test_the_command_line_lists_them_and_removes_nothing(cli_maps, apply, capsys, device_root):
    from openexpress.cli import main

    args = ["maps", "remove", "TopoActive West"] + (["--apply"] if apply else [])
    assert main(args) == 0
    out = capsys.readouterr().out.splitlines()
    start = out.index(HEADER)
    assert out[start + 1:start + 3] == ["  - GARMIN/OLD1.GMA", NOT_YET]
    assert (device_root / "GARMIN" / "OLD1.GMA").exists()


def test_the_command_line_says_nothing_without_a_rule(monkeypatch, device, capsys):
    from openexpress import cli
    from openexpress.cli import main

    st, dev = device
    monkeypatch.setattr(cli, "_load_maps",
                        lambda args, client, own=False: (st, dev, *mp.read_maps(answer())))
    assert main(["maps", "remove", "TopoActive West"]) == 0
    assert HEADER not in capsys.readouterr().out


# --- the window ------------------------------------------------------------------------------


def _questions(monkeypatch, device, ask):
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    st, dev = device
    maps, _listed = mp.read_maps(answer(rule("OLD*.gma")))
    shown = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: shown.append(self))
    win = types.SimpleNamespace(session=types.SimpleNamespace(storage=st, device=dev),
                                maps_cache=maps, map_cleanup=mp.cleanup_plan(st, dev, maps),
                                _removed_files=MainWindow._removed_files)
    ask(win, maps)
    (dialog,) = shown
    return dialog.get_body()


@pytest.mark.needs_display
def test_the_questions_before_a_map_name_the_old_files(monkeypatch, device):
    from openexpress.gui.app import MainWindow

    west = "TopoActive West"
    for ask in (lambda win, maps: MainWindow.confirm_remove(win, mp.find(maps, west)),
                lambda win, maps: MainWindow._ask_install_map(win, mp.find(maps, west), 100,
                                                              None, [])):
        body = _questions(monkeypatch, device, ask)
        assert body.endswith(f"\n\n{HEADER}\nGARMIN/OLD1.GMA\n{NOT_YET}")


@pytest.mark.needs_display
def test_the_page_keeps_the_plan_of_its_maps(device, monkeypatch):
    """The plan is made with the maps, in the work of the page, never on the window's
    thread."""
    from openexpress.gui.app import MainWindow

    st, dev = device
    works = []
    client = types.SimpleNamespace(unit_info=lambda unit_id, part_number: {
        "mapupdatetype": "Universal"}, universal_maps=lambda raw: answer(rule("OLD*.gma")))
    win = types.SimpleNamespace(session=types.SimpleNamespace(
        client=client, device=dev, storage=st, unit_info_answer=None),
        _load=lambda key, text, work, done, on_error=None: works.append((work, done)),
        show_maps=lambda *a, **kw: None)
    monkeypatch.setattr(mp, "universal_serves", lambda value, device: True)
    MainWindow.load_maps(win)
    ((work, done),) = works
    done(work())
    assert [f.path for f in win.map_cleanup] == ["GARMIN/OLD1.GMA"]


def test_the_words_in_polish():
    from openexpress import i18n

    i18n.setup("pl")
    try:
        lines = mp.cleanup_lines([mp.CleanupFile("GARMIN/OLD1.GMA", 3)])
    finally:
        i18n.setup("en")
    assert lines == ["Stare pliki, które Garmin zaleca usunąć:", "GARMIN/OLD1.GMA",
                     "openExpress na razie ich nie usuwa."]
