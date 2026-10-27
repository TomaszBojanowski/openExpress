"""openexpress status (the user, 27.10.2026): in one look, the login, the Garmin devices
connected (name, USB mode, whether they are on the account), the last sync of each and what
the last check of its updates found. Without --online nothing is asked of a server: only the
devices' GarminDevice.xml and what this installation keeps are read (the tests' guard against
the network fails a test that tries). Every device and account here is made up."""

import types

import pytest
from fakes import login, noted, record_sync

from openexpress import sync, tile_facts
from openexpress.cli import main
from openexpress.device.gio_storage import DeviceSummary, FoundDevice

FENIX, VARIA = 3000000011, 3000000012


def _fenix():
    found = FoundDevice("mtp://made-up/", "fenix", True, kind="mtp")
    return DeviceSummary(found, "fēnix 8 - 47mm, Solar", "006-B4532-00", "23.39", FENIX)


def _varia():
    found = FoundDevice("file:///run/media/made-up/GARMIN/", "GARMIN", True, kind="ums")
    return DeviceSummary(found, "Varia RTL515", "006-B3592-03", "3.36", VARIA)


def _connected(monkeypatch, summaries):
    from openexpress.device import gio_storage

    monkeypatch.setattr(gio_storage, "find_all_devices", lambda: [s.found for s in summaries])
    by_found = {id(s.found): s for s in summaries}
    monkeypatch.setattr(gio_storage, "probe", lambda found: by_found[id(found)])


def _checked(unit_id, version, updates=()):
    tile_facts.note_update_check(types.SimpleNamespace(unit_id=unit_id,
                                                       software_version_str=version),
                                 list(updates))


MAP_GROUPING = types.SimpleNamespace(title="Map Region Grouping file", version="2.31",
                                     is_system=False)


def test_status_says_what_is_known_without_asking_a_server(monkeypatch, capsys, clock):
    login()
    fenix, varia = _fenix(), _varia()
    _connected(monkeypatch, [fenix, varia])
    noted([fenix, varia], account=[{"unitid": FENIX}])
    sync.SyncState(FENIX).save()
    record_sync(FENIX, uploaded=2, downloaded=1, skipped_known=0, errors=[], lines=[])
    _checked(FENIX, "23.39", [MAP_GROUPING])
    _checked(VARIA, "3.36")
    assert main(["status"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Garmin Connect: logged in as made-up",
        "Connected: 2 devices",
        "fēnix 8 - 47mm, Solar · MTP · on your account",
        "  Last sync: just now · 2 new files, 2 uploaded · 1 received",
        "  Device software: Update available: Map Region Grouping file 2.31 · checked just now",
        "Varia RTL515 · USB drive",
        "  Device software: Software is up to date · checked just now"]


def test_status_without_a_login_or_a_record(monkeypatch, capsys, clock):
    login(saved=False)
    _connected(monkeypatch, [_fenix()])
    assert main(["status"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Garmin Connect: not logged in",
        "Connected: 1 device",
        "fēnix 8 - 47mm, Solar · MTP · account not known",
        "  Last sync: Never synced",
        "  Device software: not checked yet (openexpress status --online checks it)"]


def test_status_without_a_device(monkeypatch, capsys):
    login()
    _connected(monkeypatch, [])
    assert main(["status"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Garmin Connect: logged in as made-up", "No Garmin device is connected."]


def test_status_online_asks_the_account_and_the_update_server(monkeypatch, capsys, clock,
                                                              device_root):
    """--online: the account's device list once, and the update server for each device;
    nothing of their answers is kept."""
    from openexpress import cli
    from openexpress import updates as upd
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    login()
    fenix = _fenix()
    fenix.storage = LocalStorage(device_root)
    fenix.device = parse(fenix.storage.read("GARMIN/GarminDevice.xml"))
    _connected(monkeypatch, [fenix])
    asked = []
    client = types.SimpleNamespace(
        account_devices=lambda: asked.append("account") or [{"unitid": OTHER_UNIT}],
        software_updates=lambda raw: asked.append("updates") or "answer")
    monkeypatch.setattr(cli, "make_client", lambda settings, need_auth=False: client)
    monkeypatch.setattr(cli, "GarminClient", lambda settings: client)
    monkeypatch.setattr(upd, "parse_updates", lambda answer: [MAP_GROUPING_UPDATE])
    monkeypatch.setattr(upd, "mark_present", lambda storage, items, count: items)
    from openexpress import known_devices

    kept = known_devices.path()
    before = kept.read_bytes() if kept.exists() else None
    assert main(["status", "--online"]) == 0
    assert asked == ["account", "updates"]
    assert capsys.readouterr().out.splitlines()[2:] == [
        "fēnix 8 - 47mm, Solar · MTP · not on your account",
        "  Last sync: Never synced",
        "  Device software: Update available: Map Region Grouping file 2.31"]
    assert (kept.read_bytes() if kept.exists() else None) == before  # nothing of it kept


OTHER_UNIT = 3000000099
MAP_GROUPING_UPDATE = types.SimpleNamespace(
    title="Map Region Grouping file", version="2.31", is_system=False, is_reinstall=False,
    is_metadata=False, on_device=False, device_path="Garmin/SQL/MapRegionGrouping.db")


@pytest.mark.parametrize("argv", [["status", "--help"]])
def test_the_help_of_status(capsys, argv):
    with pytest.raises(SystemExit):
        main(argv)
    said = " ".join(capsys.readouterr().out.split())
    assert "--online" in said
