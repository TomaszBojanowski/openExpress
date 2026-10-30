"""The kept answers on disk (answers.use_disk), for the next command and the next start of the
window: only the fields the program reads (the lists match what maps.py and updates.py read,
and an answer cut to them reads the same), never a name given to a device, a serial number,
unit-info's unit ID or unlock data; a command run again a moment later asks nothing; the cache
part "answers" counts and clears them. All answers and devices are made up."""
import json
import re
from pathlib import Path

from test_updates_maps import maps_response, sw_response

from openexpress import answers, maps, updates
from openexpress.answers import ANSWERS, DISK_FIELDS, Disk

ROOT = Path(__file__).resolve().parent.parent


def _keys_read(module: str) -> set[str]:
    source = (ROOT / "openexpress" / module).read_text()
    return (set(re.findall(r'''\.get\(\s*["']([a-z0-9_]+)["']''', source))
            | set(re.findall(r'''\[["']([a-z0-9_]+)["']\]''', source)))


def test_the_fields_on_disk_are_those_the_program_reads():
    """A key read by maps.py or updates.py and missing from its list would be lost for a
    command that takes the answer from disk: the lists follow the code."""
    not_of_the_answer = {
        "maps.py": {"gma", "unlocks", "mapupdatetype"},  # the activation's, unit-info's
        "updates.py": {"all", "segmenttype"},  # a filter of the request, unit-info's
    }
    assert _keys_read("maps.py") - not_of_the_answer["maps.py"] <= answers.MAP_FIELDS
    assert _keys_read("updates.py") - not_of_the_answer["updates.py"] <= answers.SOFTWARE_FIELDS
    assert "gma" not in answers.MAP_FIELDS and "displayname" not in answers.ACCOUNT_FIELDS


def test_an_answer_cut_to_its_fields_reads_the_same():
    from openexpress.connect import lower_keys

    base = "https://maps.example"
    full = lower_keys(maps_response(base))
    cut = answers._fields(full, answers.MAP_FIELDS)
    assert repr(maps.read_maps(cut)) == repr(maps.read_maps(full))
    full = lower_keys(sw_response(base))
    full["accesslevel"] = "public_beta"
    cut = answers._fields(full, answers.SOFTWARE_FIELDS)
    assert updates.parse_updates(cut) == updates.parse_updates(full)
    assert updates.beta_offered(cut) == updates.beta_offered(full)


def test_nothing_private_goes_to_disk(tmp_path):
    ANSWERS.disk = Disk(tmp_path / "answers")
    ANSWERS.ask(answers.ACCOUNT_DEVICES, ("prod", "login"), lambda: [
        {"unitid": 3000000001, "displayname": "Jan Kowalski's watch", "partnumber": "006-X",
         "registrationdate": "2025-01-01", "serialnumber": "SER00001"}])
    ANSWERS.ask(answers.UNIT_INFO, ("prod", 3000000001, "006-X"), lambda: {
        "unitid": 3000000001, "serialnumber": "SER00002", "mapupdatetype": "Universal"})
    ANSWERS.ask(answers.UNIVERSAL_MAPS, ("prod", "en-US", "xml", False), lambda: {
        "maps": [{"displayname": "TopoActive Made-up", "gma": "UNLOCK-DATA"}]})
    ANSWERS.ask(answers.PRIMARY_TRAINING, ("prod", "login"), lambda: {
        "deviceid": 3000000001, "token": "made-up-token"})
    text = " ".join(p.read_text() for p in (tmp_path / "answers").iterdir())
    for private in ("Jan Kowalski", "SER0000", "UNLOCK-DATA", "made-up-token",
                    "registrationdate", "serialnumber"):
        assert private not in text, private
    assert "TopoActive Made-up" in text  # a map's name is what the maps page shows
    assert len(list((tmp_path / "answers").iterdir())) == 4


def test_every_kind_on_disk_has_its_fields_and_the_country_stays_in_memory():
    assert set(DISK_FIELDS) == {answers.ACCOUNT_DEVICES, answers.PRIMARY_TRAINING,
                                answers.SOFTWARE_UPDATES, answers.UNIVERSAL_MAPS,
                                answers.UNIT_INFO}


def test_the_disk_is_in_the_cache_part_answers():
    from openexpress import cache, paths

    answers.use_disk()
    assert ANSWERS.disk.folder == paths.cache_dir() / cache.CATEGORIES["answers"][0]


def test_a_command_run_again_a_moment_later_asks_nothing(device_root, xdg, monkeypatch, capsys):
    """Each command is a process of its own: what the one before was told comes from disk."""
    from test_updates_maps import NO_MAPS, answer_with

    from openexpress.cli import main

    asked = answer_with(monkeypatch, body=json.dumps(NO_MAPS).encode())
    assert main(["--path", str(device_root), "maps"]) == 0
    ANSWERS.clear()  # the next process: nothing in memory
    assert main(["--path", str(device_root), "maps"]) == 0
    assert len(asked) == 1
    assert list((Path(xdg) / "cache" / "openexpress" / "answers").glob("*.json"))


def test_clearing_the_part_answers_forgets_them(xdg, capsys):
    from openexpress.cli import main

    answers.use_disk()
    ANSWERS.ask(answers.SOFTWARE_UPDATES, ("k",), lambda: {"updates": []})
    assert main(["cache"]) == 0
    assert "  Answers of Garmin's servers: " in capsys.readouterr().out
    assert main(["cache", "--clear", "--only", "answers", "--apply"]) == 0
    assert not list(ANSWERS.disk.folder.glob("*.json")) and ANSWERS._kept == {}
