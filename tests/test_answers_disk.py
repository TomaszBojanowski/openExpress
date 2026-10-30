"""The kept answers on disk (answers.use_disk), for the next command and the next start of the
window: only the fields the program reads (the lists match what maps.py and updates.py read,
and an answer cut to them reads the same), never a name given to a device, a serial number,
unit-info's unit ID or unlock data; a command run again a moment later asks nothing; the cache
part "answers" counts and clears them. All answers and devices are made up."""
import json
import re
import stat
from pathlib import Path

import pytest
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


# --- the account's answers: Garmin Connect sends them with "no-cache, no-store, private" -------


def _on_disk(folder) -> dict:
    found = {}
    for p in folder.glob("*.json"):
        data = json.loads(p.read_text())
        found[data["kind"]] = data["value"]
    return found


def test_of_the_account_only_the_fields_read_go_to_disk(tmp_path):
    """Of the account's list and its primary device only what the program reads: no name, no
    serial number, no date, no profile of the user."""
    ANSWERS.disk = Disk(tmp_path / "answers")
    device = {"unitid": 3000000001, "deviceid": 4000000001, "partnumber": "006-X",
              "productdisplayname": "Made-up 1", "displayname": "Jan Kowalski's watch",
              "serialnumber": "SER00001", "registrationdate": "2025-01-01",
              "userprofilepk": 5000000001, "devicestatus": "active",
              "devicecategories": ["FITNESS"], "primaryactivitytrackerindicator": True}
    ANSWERS.ask(answers.ACCOUNT_DEVICES, ("prod", "login"), lambda: [device])
    ANSWERS.ask(answers.PRIMARY_TRAINING, ("prod", "login"), lambda: {
        "deviceid": 4000000001, "userprofilepk": 5000000001, "displayname": "Jan Kowalski",
        "devicename": "Jan's fenix"})
    kept = _on_disk(tmp_path / "answers")
    assert kept[answers.ACCOUNT_DEVICES] == [{
        "unitid": 3000000001, "deviceid": 4000000001, "partnumber": "006-X",
        "productdisplayname": "Made-up 1", "devicecategories": ["FITNESS"],
        "primaryactivitytrackerindicator": True}]
    assert kept[answers.PRIMARY_TRAINING] == {"deviceid": 4000000001}


def test_each_field_of_the_account_on_disk_is_read_by_the_program():
    """A field the program no longer reads leaves the list: nothing of the account is kept
    on disk without a use."""
    source = "\n".join(p.read_text() for p in (ROOT / "openexpress").rglob("*.py")
                       if p.name != "answers.py")
    for field in answers.ACCOUNT_FIELDS | answers.PRIMARY_FIELDS:
        assert re.search(rf"""["']{field}["']""", source), field


@pytest.mark.parametrize("made_before", [False, True])
def test_the_folder_and_its_files_are_the_owners_only(xdg, made_before):
    """The folder 0700 (also one made with the usual mode before), each file 0600."""
    answers.use_disk()
    folder = ANSWERS.disk.folder
    if made_before:
        folder.mkdir(parents=True)
        folder.chmod(0o755)
    ANSWERS.ask(answers.ACCOUNT_DEVICES, ("prod", "login"), lambda: [{"unitid": 3000000001}])
    ANSWERS.ask(answers.PRIMARY_TRAINING, ("prod", "login"), lambda: {"deviceid": 4000000001})
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    files = list(folder.iterdir())
    assert len(files) == 2
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)


@pytest.mark.parametrize("how", ["logout", "login"])
def test_a_logout_or_another_login_clears_the_account_on_disk(xdg, capsys, how):
    """What another process kept on disk too; the device's answers stay."""
    from openexpress.auth import store
    from openexpress.cli import main

    answers.use_disk()
    store.save(store.ConnectAuth("made-up-token", "made-up-secret"))
    ANSWERS.ask(answers.ACCOUNT_DEVICES, ("prod", store.stamp()),
                lambda: [{"unitid": 3000000001}])
    ANSWERS.ask(answers.PRIMARY_TRAINING, ("prod", store.stamp()),
                lambda: {"deviceid": 4000000001})
    ANSWERS.ask(answers.SOFTWARE_UPDATES, ("k",), lambda: {"updates": []})
    ANSWERS.clear()  # the logout comes from another process
    if how == "logout":
        assert main(["logout"]) == 0
    else:
        store.save(store.ConnectAuth("another-token", "another-secret"))
    assert set(_on_disk(ANSWERS.disk.folder)) == {answers.SOFTWARE_UPDATES}


def test_an_answer_got_while_the_cache_is_cleared_stays_in_memory(xdg):
    """Clearing the cache holds it alone (cache.clearing); an answer got meanwhile does not
    wait for it, nor go to the folder being emptied: it is kept in memory only."""
    import threading

    from openexpress import cache

    answers.use_disk()
    got = []
    with cache.clearing():
        asking = threading.Thread(target=lambda: got.append(ANSWERS.ask(
            answers.SOFTWARE_UPDATES, ("k",), lambda: {"updates": []})))
        asking.start()
        asking.join(5)
        assert not asking.is_alive(), "the answer waited for the clearing"
    assert got == [{"updates": []}]
    assert not list(ANSWERS.disk.folder.glob("*.json"))
    assert ANSWERS.ask(answers.SOFTWARE_UPDATES, ("k",), pytest.fail) == {"updates": []}


def test_answers_out_of_date_leave_the_disk_when_a_process_starts(xdg, clock):
    """An answer whose key no longer comes (another GarminDevice.xml, another login) would
    stay until the cache is cleared: the next process takes away those out of date, and
    files that are no answer."""
    from datetime import timedelta

    answers.use_disk()
    ANSWERS.ask(answers.SOFTWARE_UPDATES, ("old",), lambda: {"updates": []})
    clock.current += timedelta(seconds=answers.LIFETIMES[answers.SOFTWARE_UPDATES] + 1)
    ANSWERS.ask(answers.SOFTWARE_UPDATES, ("new",), lambda: {"updates": []})
    folder = ANSWERS.disk.folder
    (folder / "0123.json").write_text("[]")  # changed by hand
    (folder / "note.txt").write_text("not an answer")  # not ours: stays
    answers.use_disk()  # the next process
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 2 and "note.txt" in names
    ANSWERS.clear()
    assert ANSWERS.ask(answers.SOFTWARE_UPDATES, ("new",), pytest.fail) == {"updates": []}
