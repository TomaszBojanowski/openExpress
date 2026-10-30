"""The tiles of the start page: one list made of the devices connected now, the
devices this installation remembers and the devices of the Garmin account. Data in, data out:
no GTK, no network. All unit IDs here are made up."""

import dataclasses
import json
import logging
from pathlib import Path

import pytest

from openexpress.device.gio_storage import NOT_MOUNTED, DeviceSummary, FoundDevice

EDGE, FENIX, VARIA, VENU = 3000000001, 3000000002, 3000000003, 3000000004
UNIT_IDS = (EDGE, FENIX, VARIA, VENU)
DAY = 86400.0
NOW = 1_800_000_000.0

# The fields the start page reads from GET device-service/deviceregistration/devices, as in the
# masked answer (testdata/konto-urzadzenia/), with made-up unit IDs and names.
ACCOUNT_ANSWER = [
    {"unitId": EDGE, "deviceId": EDGE, "displayName": "DEVICE 1",
     "productDisplayName": "Edge 1050", "partNumber": "006-B4440-00",
     "currentFirmwareVersion": "33.12", "primaryActivityTrackerIndicator": False,
     "deviceCategories": ["FITNESS", "DEDICATED_BIKE_COMPUTER"],
     "imageUrl": "https://res.garmin.com/en/products/010-02890-01/V/c1_01_md.png"},
    {"unitId": FENIX, "deviceId": FENIX, "displayName": "DEVICE 2",
     "productDisplayName": "fenix 8 - 47mm, Solar", "partNumber": "006-B4532-00",
     "currentFirmwareVersion": "23.39", "primaryActivityTrackerIndicator": True,
     "deviceCategories": ["FITNESS", "WELLNESS", "GOLF", "OUTDOOR"],
     "imageUrl": "https://res.garmin.com/en/products/010-02906-11/v/c1_01_md.png"},
]
ACCOUNT_FILE = (Path(__file__).parent.parent / "testdata" / "konto-urzadzenia"
                / "deviceregistration-devices.masked.json")


def account(answer=ACCOUNT_ANSWER):
    """The list as GarminClient.account_devices() gives it: keys in lower case."""
    from openexpress.connect import lower_keys

    return lower_keys(answer)


def connected(uri, description="", part="", version="", unit_id=None, kind="mtp",
              reason=None):
    return DeviceSummary(FoundDevice(uri, "x", True, kind=kind), description, part, version,
                         unit_id, reason)


def fenix_connected(version="23.40"):
    return connected("mtp://fenix/", "fēnix 8", "006-B4532-00", version, FENIX)


def remembered(*entries):
    from openexpress.known_devices import KnownDevice

    return {e.unit_id: e for e in (KnownDevice(*fields) for fields in entries)}


def with_facts(known, answer):
    """These remembered devices with what the window writes of them (tile_facts) once the
    account's list `answer` is read with a login saved: what it says of each one, and what
    is known of their sync."""
    import time

    from fakes import login

    from openexpress import tile_facts

    facts = tile_facts.account_facts(answer, known, time.time(), login())
    return {unit: dataclasses.replace(k, account=facts[unit], synced=tile_facts.synced_fact(unit))
            for unit, k in known.items()}


FENIX_SEEN = (FENIX, "fēnix 8 (remembered)", "006-B4532-00", "23.38", "mtp", NOW - 9 * DAY,
              NOW - 2 * DAY)
EDGE_SEEN = (EDGE, "Edge 1050 (remembered)", "006-B4440-00", "33.11", "mtp", NOW - 9 * DAY,
             NOW - DAY)
VARIA_SEEN = (VARIA, "Varia RTL515", "006-B3592-03", "3.36", "ums", NOW - 9 * DAY,
              NOW - 3 * DAY)
VENU_SEEN = (VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", NOW - 9 * DAY, NOW - 5 * DAY)


def by_name(tiles):
    return {t.description: t for t in tiles}


# --- merging by unit ID ------------------------------------------------------------------------


def test_one_device_in_all_three_sources_is_one_tile():
    """The fēnix connected, remembered and on the account: one tile, connected and on the
    account; the Edge, remembered and on the account: one tile as well."""
    from openexpress import devicelist

    tiles = devicelist.tiles([fenix_connected()],
                             with_facts(remembered(FENIX_SEEN, EDGE_SEEN), account()), account(),
                             logged_in=True)
    assert len(tiles) == 2
    fenix, edge = tiles
    assert (fenix.description, fenix.connected, fenix.on_account) == ("fēnix 8", True, True)
    assert (edge.description, edge.connected, edge.on_account) == (
        "Edge 1050 (remembered)", False, True)


def test_one_device_in_two_sources_is_one_tile():
    """Connected and remembered (not on the account): one tile; remembered and on the account:
    one tile; connected and on the account (never remembered): one tile."""
    from openexpress import devicelist

    assert len(devicelist.tiles([fenix_connected()], remembered(FENIX_SEEN), [],
                                logged_in=True)) == 1
    assert len(devicelist.tiles([], remembered(EDGE_SEEN), account(), logged_in=True)) == 2
    assert len(devicelist.tiles([fenix_connected()], {}, account(), logged_in=True)) == 2


def test_the_part_number_never_merges():
    """Two fēnix 8 of the same model (one connected, one remembered with another unit ID):
    two tiles."""
    from openexpress import devicelist

    other = (3000000009, "fēnix 8", "006-B4532-00", "23.39", "mtp", NOW - DAY, NOW - DAY)
    tiles = devicelist.tiles([fenix_connected()], remembered(other), [], logged_in=False)
    assert len(tiles) == 2
    assert [t.connected for t in tiles] == [True, False]


def test_a_connected_device_without_an_id_has_a_tile_of_its_own():
    """Connected without <Id> (unit ID None) or without details (not mounted): each its own
    tile, never merged with each other nor with a remembered fēnix of the same model."""
    from openexpress import devicelist

    no_id = connected("mtp://a/", "fēnix 8", "006-B4532-00", "23.39", None)
    not_mounted = connected("mtp://b/", reason=NOT_MOUNTED)
    tiles = devicelist.tiles([no_id, not_mounted], remembered(FENIX_SEEN), [], logged_in=False)
    assert len(tiles) == 3
    first, second, third = tiles
    assert (first.description, first.connected, first.last_seen) == ("fēnix 8", True, None)
    assert (second.description, second.connected) == ("", True)
    assert (third.description, third.connected) == ("fēnix 8 (remembered)", False)


def test_one_device_seen_both_ways_is_one_tile():
    """A device connected over MTP and as a drive at once (the same unit ID): one tile, the
    MTP one."""
    from openexpress import devicelist

    drive = connected("file:///run/media/u/GARMIN/", "Varia RTL515", "006-B3592-03", "3.36",
                      VARIA, kind="ums")
    mtp = connected("mtp://varia/", "Varia RTL515", "006-B3592-03", "3.36", VARIA)
    (tile,) = devicelist.tiles([drive, mtp], {}, [], logged_in=False)
    assert tile.kind == "mtp" and tile.connected


# --- which source gives which field ------------------------------------------------------------


def test_fields_come_from_the_connected_device_first():
    """Name and version: the connected device's GarminDevice.xml before the memory and the
    account; on_account, primary, the picture address and the category only from the account;
    when it was seen only from the memory."""
    from openexpress import devicelist

    (fenix, _edge) = devicelist.tiles([fenix_connected("23.40")],
                                      with_facts(remembered(FENIX_SEEN), account()),
                                      account(), logged_in=True)
    assert dataclasses.asdict(fenix) == {
        "description": "fēnix 8", "part_number": "006-B4532-00", "software_version": "23.40",
        "kind": "mtp", "connected": True, "on_account": True, "primary": True,
        "last_seen": NOW - 2 * DAY, "image_part_number": "006-B4532-00",
        "image_url": "https://res.garmin.com/en/products/010-02906-11/v/c1_01_md.png",
        "category": None, "image_source": "the account", "update_found": None,
        # Never synced here (no state.json): said from the first frame.
        "update_ready": None, "account_known": True, "to_sync": None, "never_synced": True,
        "synced_at": None}


def test_fields_of_a_device_not_connected_come_from_the_memory_first():
    """Not connected: name and version as remembered, before the account's."""
    from openexpress import devicelist

    edge = by_name(devicelist.tiles([], with_facts(remembered(EDGE_SEEN), account()), account(),
                                    logged_in=True))["Edge 1050 (remembered)"]
    assert (edge.software_version, edge.kind, edge.last_seen) == ("33.11", "mtp", NOW - DAY)
    assert (edge.on_account, edge.primary, edge.category) == (True, False, "cycling-computer")
    assert edge.image_url.endswith("/010-02890-01/V/c1_01_md.png")


def test_a_device_only_on_the_account():
    """Never seen here: the account's name and version (as Garmin Connect knows it), no kind,
    no date."""
    from openexpress import devicelist

    edge = by_name(devicelist.tiles([], {}, account(), logged_in=True))["Edge 1050"]
    assert dataclasses.asdict(edge) == {
        "description": "Edge 1050", "part_number": "006-B4440-00", "software_version": "33.12",
        "kind": None, "connected": False, "on_account": True, "primary": False,
        "last_seen": None, "image_part_number": "006-B4440-00",
        "image_url": "https://res.garmin.com/en/products/010-02890-01/V/c1_01_md.png",
        "category": "cycling-computer", "image_source": "the account", "update_found": None,
        "update_ready": None, "account_known": True, "to_sync": None, "never_synced": False,
        "synced_at": None}


def test_a_device_not_on_the_account_has_no_account_fields():
    """The Varia (never on the account) and the Venu 2 (another account): on_account False,
    no picture address, not primary, whatever the account says of the others."""
    from openexpress import devicelist

    varia_now = connected("file:///run/media/u/GARMIN/", "Varia RTL515", "006-B3592-03",
                          "3.36", VARIA, kind="ums")
    tiles = by_name(devicelist.tiles([varia_now], remembered(VARIA_SEEN, VENU_SEEN), account(),
                                     logged_in=True))
    for name in ("Varia RTL515", "Venu 2"):
        t = tiles[name]
        assert (t.on_account, t.primary, t.image_url, t.category) == (False, False, None, None)
    assert tiles["Varia RTL515"].connected and tiles["Varia RTL515"].kind == "ums"


# --- order -------------------------------------------------------------------------------------


def test_connected_first_the_primary_first_then_by_name():
    """Connected first, the account's primary device first among them; then the remembered
    ones, then those only on the account, each section by name."""
    from openexpress import devicelist

    varia_now = connected("file:///run/media/u/GARMIN/", "Varia RTL515", "006-B3592-03",
                          "3.36", VARIA, kind="ums")
    venu = (VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", NOW - 9 * DAY, NOW - 4 * DAY)
    older_edge = (EDGE, "Edge 1050 (remembered)", "006-B4440-00", "33.11", "mtp",
                  NOW - 9 * DAY, NOW - 6 * DAY)
    # The fēnix connected: seen by the detection, remembered with what the account says.
    tiles = devicelist.tiles([varia_now, fenix_connected()],
                             with_facts(remembered(venu, FENIX_SEEN), account()), account(),
                             logged_in=True)
    assert [t.description for t in tiles] == ["fēnix 8", "Varia RTL515", "Venu 2", "Edge 1050"]
    tiles = devicelist.tiles([], remembered(venu, older_edge, VARIA_SEEN), [], logged_in=False)
    assert [t.description for t in tiles] == [
        "Edge 1050 (remembered)", "Varia RTL515", "Venu 2"]


def test_devices_not_connected_are_in_name_order_not_by_when_they_were_seen():
    """A device seen later does not move ahead: the order of the tiles stays the same from one
    plug-in to the next ("last seen" is only in the subtitle)."""
    from openexpress import devicelist

    venu_later = (VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", NOW - 9 * DAY, NOW - DAY)
    edge_earlier = (EDGE, "edge 1050", "006-B4440-00", "33.11", "mtp", NOW - 9 * DAY,
                    NOW - 6 * DAY)
    tiles = devicelist.tiles([], remembered(venu_later, edge_earlier), [], logged_in=False)
    assert [t.description for t in tiles] == ["edge 1050", "Venu 2"]  # casefold


def test_the_same_name_is_ordered_by_part_number():
    from openexpress import devicelist

    second = (VENU, "Garmin device", "006-B0002-00", "1.00", "mtp", NOW - 9 * DAY, NOW - DAY)
    first = (EDGE, "Garmin device", "006-B0001-00", "1.00", "mtp", NOW - 9 * DAY, NOW - 6 * DAY)
    for order in ((second, first), (first, second)):
        tiles = devicelist.tiles([], remembered(*order), [], logged_in=False)
        assert [t.part_number for t in tiles] == ["006-B0001-00", "006-B0002-00"]


def test_the_account_section_coming_later_does_not_reorder_the_others():
    """Each section in name order, the sections one after the other (the window counts a
    click over the whole list): the devices only on the account after the remembered ones,
    even when their names come first."""
    from openexpress import devicelist

    venu = (VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", NOW - 9 * DAY, NOW - DAY)
    known = remembered(venu, VARIA_SEEN)
    without = devicelist.tiles([fenix_connected()], known, [], logged_in=False)
    with_account = devicelist.tiles([fenix_connected()], known, account(ACCOUNT_WITH_A_THIRD),
                                    logged_in=True)
    assert [t.description for t in without] == ["fēnix 8", "Varia RTL515", "Venu 2"]
    assert [(t.description, devicelist.section(t)) for t in with_account] == [
        ("fēnix 8", devicelist.CONNECTED),
        ("Varia RTL515", devicelist.REMEMBERED), ("Venu 2", devicelist.REMEMBERED),
        ("Edge 1050", devicelist.ON_ACCOUNT), ("Forerunner 965", devicelist.ON_ACCOUNT)]


# --- not logged in -----------------------------------------------------------------------------


def test_without_logging_in_there_is_no_tile_from_the_account():
    """Not logged in: an account list passed by mistake is not used; no tile only from it, no
    account field on the others."""
    from openexpress import devicelist

    tiles = devicelist.tiles([fenix_connected()], remembered(EDGE_SEEN), account(),
                             logged_in=False)
    assert [t.description for t in tiles] == ["fēnix 8", "Edge 1050 (remembered)"]
    assert not any(t.on_account or t.primary or t.image_url or t.category for t in tiles)


def test_nothing_at_all_is_no_tile():
    from openexpress import devicelist

    assert devicelist.tiles([], {}, [], logged_in=True) == []
    assert devicelist.tiles([], {}, None, logged_in=False) == []


# --- the unit ID -------------------------------------------------------------------------------


def test_no_unit_id_in_a_tile_nor_in_the_log(caplog):
    """No field of a tile holds a unit ID (nor does its repr); the log neither."""
    from openexpress import devicelist

    varia_now = connected("file:///run/media/u/GARMIN/", "Varia RTL515", "006-B3592-03",
                          "3.36", VARIA, kind="ums")
    with caplog.at_level(logging.DEBUG, logger="openexpress"):
        tiles = devicelist.tiles([fenix_connected(), varia_now],
                                 remembered(EDGE_SEEN, VENU_SEEN), account(), logged_in=True)
    assert len(tiles) == 4
    shown = [repr(t) for t in tiles] + [str(v) for t in tiles
                                        for v in dataclasses.asdict(t).values()]
    for unit_id in UNIT_IDS:
        assert not any(str(unit_id) in text for text in shown)
        assert str(unit_id) not in caplog.text
    assert "start page: 4 tiles" in caplog.text


@pytest.mark.skipif(not ACCOUNT_FILE.exists(), reason="private test data")
def test_the_masked_account_answer():
    """The masked answer itself (all 319 fields of each device): two tiles, the fēnix primary,
    no unit ID in them."""
    from openexpress import devicelist

    answer = json.loads(ACCOUNT_FILE.read_text())
    tiles = devicelist.tiles([], {}, account(answer), logged_in=True)
    assert [(t.description, t.part_number, t.primary, t.on_account) for t in tiles] == [
        ("Edge 1050", "006-B4440-00", False, True),
        ("fenix 8 - 47mm, Solar", "006-B4532-00", True, True)]
    ids = {str(d["unitId"]) for d in answer}
    assert not any(i in repr(t) for t in tiles for i in ids)


def test_each_tile_comes_with_what_it_was_made_of():
    """entries(): each tile with its source, kept outside the tile: the connected device's
    summary (to open it), else what is remembered of it, else its entry of the account's
    list (only on the account)."""
    from openexpress import devicelist

    drive = connected("file:///run/media/u/GARMIN/", "Varia RTL515", "006-B3592-03", "3.36",
                      VARIA, kind="ums")
    mtp = connected("mtp://varia/", "Varia RTL515", "006-B3592-03", "3.36", VARIA)
    known = with_facts(remembered(EDGE_SEEN, FENIX_SEEN), account())
    entries = devicelist.entries([drive, mtp, fenix_connected()], known, account(),
                                 logged_in=True)
    assert [t.description for t, _ in entries] == [
        "fēnix 8", "Varia RTL515", "Edge 1050 (remembered)"]
    (_, fenix), (_, varia), (_, edge) = entries
    assert fenix.found.uri == "mtp://fenix/" and varia is mtp and edge is known[EDGE]
    answer = account()
    only_on_account = devicelist.entries([], {}, answer, logged_in=True)
    assert [source for _, source in only_on_account] == answer  # the account's entries
    assert [t for t, _ in entries] == devicelist.tiles(
        [drive, mtp, fenix_connected()], known, account(), logged_in=True)


FORERUNNER = 3000000005
ACCOUNT_WITH_A_THIRD = [*ACCOUNT_ANSWER, {
    "unitId": FORERUNNER, "deviceId": FORERUNNER, "displayName": "DEVICE 3",
    "productDisplayName": "Forerunner 965", "partNumber": "006-B4315-00",
    "currentFirmwareVersion": "22.26", "primaryActivityTrackerIndicator": False,
    "deviceCategories": ["FITNESS", "WELLNESS"],
    "imageUrl": "https://res.garmin.com/en/products/010-02809-10/v/c1_01_md.png"}]


def test_each_tile_has_its_section():
    """Connected, remembered (not connected), only on the account; a device in two
    sources is in the more present one: connected before remembered before the account."""
    from openexpress import devicelist

    tiles = devicelist.tiles([fenix_connected()], remembered(EDGE_SEEN, VENU_SEEN),
                             account(ACCOUNT_WITH_A_THIRD), logged_in=True)
    assert [(t.description, devicelist.section(t)) for t in tiles] == [
        ("fēnix 8", devicelist.CONNECTED),
        ("Edge 1050 (remembered)", devicelist.REMEMBERED),
        ("Venu 2", devicelist.REMEMBERED),
        ("Forerunner 965", devicelist.ON_ACCOUNT)]
    forerunner = tiles[-1]
    assert (forerunner.on_account, forerunner.software_version, forerunner.last_seen) == (
        True, "22.26", None)
    assert [devicelist.section(t) for t in devicelist.tiles(
        [fenix_connected()], remembered(EDGE_SEEN), account(ACCOUNT_WITH_A_THIRD),
        logged_in=False)] == [devicelist.CONNECTED, devicelist.REMEMBERED]


def test_a_tile_only_on_the_account_comes_with_its_entry():
    """The source of a tile only on the account is its entry of the account's list (for
    the window to show it); the tile itself still holds no unit ID."""
    from openexpress import devicelist

    answer = account(ACCOUNT_WITH_A_THIRD)
    entries = devicelist.entries([fenix_connected()], remembered(EDGE_SEEN), answer,
                                 logged_in=True)
    tile, source = entries[-1]
    assert tile.description == "Forerunner 965" and source is answer[2]
    assert str(FORERUNNER) not in repr(tile)


# --- the kind of a device ------------------------------------------------------------------------


def test_the_kind_of_a_device_comes_from_garmin_only():
    """From what Garmin says only: a cycling computer by the account's category
    DEDICATED_BIKE_COMPUTER, a watch by the account's hasOpticalHeartRate, a wellness device
    by its only category WELLNESS (a scale: test_accessory_icons.py), a bike accessory as
    remembered (from unit-info's segmenttype); anything else none (the neutral icon). Never
    by the name: a remembered "Edge 1050" not on the account stays without a kind."""
    from openexpress import devicelist
    from openexpress.known_devices import KnownDevice

    answer = account([{**ACCOUNT_ANSWER[0]}, {**ACCOUNT_ANSWER[1], "hasOpticalHeartRate": True},
                      {"unitId": FORERUNNER, "productDisplayName": "Index S2",
                       "partNumber": "006-B0000-01", "hasOpticalHeartRate": False,
                       "deviceCategories": ["WELLNESS"]}])
    varia = KnownDevice(VARIA, "Varia RTL515", "006-B3592-03", "3.36", "ums", 1.0, 2.0,
                        category="bike-accessory")
    venu = KnownDevice(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", 1.0, 3.0)
    kinds = {t.description: t.category for t in devicelist.tiles(
        [], {VARIA: varia, VENU: venu}, answer, logged_in=True)}
    assert kinds == {"Edge 1050": "cycling-computer", "fenix 8 - 47mm, Solar": "watch",
                     "Index S2": "wellness", "Varia RTL515": "bike-accessory", "Venu 2": None}
    edge_by_name = KnownDevice(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", 1.0, 4.0)
    (tile,) = devicelist.tiles([], {EDGE: edge_by_name}, None, logged_in=False)
    assert tile.category is None


def test_the_account_tells_the_kind_before_what_is_remembered():
    """The account's word on a device counts first; what is remembered serves without it
    (offline, logged out)."""
    from openexpress import devicelist
    from openexpress.known_devices import KnownDevice

    edge = KnownDevice(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", 1.0, 4.0,
                       category="watch")
    (tile, _fenix) = devicelist.tiles([], {EDGE: edge}, account(), logged_in=True)
    assert tile.category == "cycling-computer"
    (tile,) = devicelist.tiles([], {EDGE: edge}, None, logged_in=False)
    assert tile.category == "watch"


def test_the_account_list_teaches_the_kind_of_remembered_devices(tmp_path):
    """The kinds the account tells are kept in devices.json for the devices remembered, so
    that they are known offline; never for a device that is not remembered."""
    from openexpress import devicelist, known_devices

    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=1.0)
    devicelist.remember_kinds(account([{**ACCOUNT_ANSWER[0]},
                                       {**ACCOUNT_ANSWER[1], "hasOpticalHeartRate": True}]))
    known = known_devices.load()
    assert set(known) == {EDGE} and known[EDGE].category == "cycling-computer"


# --- handhelds (inReach, GPSMAP…): their kind from the model name, for the icon only ----------

INREACH = {"unitId": 3000000005, "deviceId": 3000000005, "displayName": "DEVICE 5",
           "productDisplayName": "inReach Mini 2", "partNumber": "006-B3968-00",
           "currentFirmwareVersion": "5.05", "deviceCategories": ["OUTDOOR"],
           "hasOpticalHeartRate": False}


@pytest.mark.parametrize("name", ["inReach Mini 2", "INREACH Messenger", "GPSMAP 67i",
                                  "Montana 700i", "eTrex 32x", "Oregon 750t"])
def test_a_handheld_is_told_by_its_model_name(name):
    from openexpress import devicelist

    assert devicelist.kind_from_name(name) == "handheld"


def test_an_inreach_without_a_picture_gets_the_icon_of_a_handheld_and_nothing_else():
    """inReach Mini 2: Garmin tells no kind of it, its name does, for the icon only: the
    tile's kind stays what Garmin says (none), nothing is kept in devices.json. A name that
    tells nothing keeps the neutral icon."""
    from openexpress import devicelist, known_devices
    from openexpress.gui.start import APP_ID, DEVICE_ICON, kind_icon

    tiles = devicelist.tiles([], {}, account([INREACH, {**INREACH, "unitId": VENU,
                                                        "productDisplayName": "Index S2",
                                                        "partNumber": "006-B0000-00"}]), True)
    inreach, scale = _tile_of(tiles, "006-B3968-00"), _tile_of(tiles, "006-B0000-00")
    assert (inreach.category, devicelist.icon_kind(inreach)) == (None, "handheld")
    assert kind_icon(devicelist.icon_kind(inreach)) == f"{APP_ID}-handheld-symbolic"
    assert kind_icon(devicelist.icon_kind(scale)) == DEVICE_ICON
    assert "handheld" not in known_devices.CATEGORIES  # never a kind Garmin tells, never kept
    known_devices.remember(3000000005, "inReach Mini 2", "006-B3968-00", "5.05", "ums", now=1.0)
    devicelist.remember_kinds(account([INREACH]))
    assert known_devices.load()[3000000005].category is None


def test_the_open_inreach_gets_the_icon_of_a_handheld_too():
    """The device view before its picture: the same kind as its tile."""
    import types

    from openexpress.gui.device_page import open_device_kind

    s = types.SimpleNamespace(mass_storage=True, device=types.SimpleNamespace(
        unit_id=3000000005, description="inReach Mini 2", part_number="006-B3968-00"))
    assert open_device_kind(s, None) == "handheld"


@pytest.fixture
def kinds_told(monkeypatch):
    """What Garmin said of the kinds of models, as this process has heard it: none yet."""
    from openexpress import devicelist

    monkeypatch.setattr(devicelist, "_kind_evidence", {})
    monkeypatch.setattr(devicelist, "_kind_told", set())
    monkeypatch.setattr(devicelist, "_map_type_told", set())


def _no_kind_lines(caplog):
    return [r.getMessage() for r in caplog.records if "tells no kind" in r.getMessage()]


def test_a_device_garmin_tells_no_kind_of_is_logged_with_what_garmin_said(kinds_told, caplog):
    """So that a report tells the signal Garmin gives for such a device (an inReach): the
    account's categories and unit-info's segment, with the part number only (no unit ID),
    once for each new piece; a device whose kind Garmin tells is not named."""
    from openexpress import devicelist

    with caplog.at_level(logging.INFO, logger="openexpress"):
        devicelist.remember_kinds(account([ACCOUNT_ANSWER[0], INREACH]))
        devicelist.remember_kinds(account([ACCOUNT_ANSWER[0], INREACH]))  # read again
        devicelist.note_kind_evidence("006-B3968-00", segment="Outdoor",  # unit-info, later
                                      connect_support="Enabled")
    assert _no_kind_lines(caplog) == [
        ("devices: Garmin tells no kind of 006-B3968-00 (account categories: OUTDOOR; "
         "unit-info segment: not asked, connect support: not asked)"),
        ("devices: Garmin tells no kind of 006-B3968-00 (account categories: OUTDOOR; "
         "unit-info segment: Outdoor, connect support: Enabled)")]
    assert "3000000005" not in caplog.text


def test_unit_info_alone_is_logged_too_and_a_bike_accessory_not(kinds_told, caplog):
    """A device off the account: what unit-info says (here the window's note_segment, after
    an update or for a picture); a bike accessory is a kind Garmin tells."""
    import types

    from openexpress.gui.pictures import note_segment

    venu = types.SimpleNamespace(unit_id=VENU, part_number="006-B3703-00")
    varia = types.SimpleNamespace(unit_id=VARIA, part_number="006-B3592-03")
    with caplog.at_level(logging.INFO, logger="openexpress"):
        note_segment(venu, {"segmenttype": "Fitness", "serialnumber": "SECRET123"})
        note_segment(venu, {"segmenttype": "Fitness", "connectsupport": "Enabled"})
        note_segment(varia, {"segmenttype": "BikeAccessory"})
    assert _no_kind_lines(caplog) == [
        ("devices: Garmin tells no kind of 006-B3703-00 (account categories: not known; "
         "unit-info segment: Fitness, connect support: none given)"),
        ("devices: Garmin tells no kind of 006-B3703-00 (account categories: not known; "
         "unit-info segment: Fitness, connect support: Enabled)")]
    assert "SECRET123" not in caplog.text and str(VENU) not in caplog.text


def test_the_map_update_type_unit_info_tells_is_logged_once_per_model(kinds_told, caplog):
    """The log names the map update type unit-info gives a model (a nüvi 52LMT gets its software
    update but no map), so a report tells whether the universal map service serves it: the part
    number only, no unit ID; once per model and value; a value given as a number, or none, is
    said too."""
    import types

    from openexpress.gui.pictures import note_segment

    nuvi = types.SimpleNamespace(unit_id=3000000052, part_number="006-B1234-00")  # made up
    venu = types.SimpleNamespace(unit_id=VENU, part_number="006-B3703-00")
    edge = types.SimpleNamespace(unit_id=EDGE, part_number="006-B4440-00")
    with caplog.at_level(logging.INFO, logger="openexpress"):
        note_segment(nuvi, {"segmenttype": "Automotive", "mapupdatetype": "LegacyRmu",
                                "serialnumber": "SECRET123"})
        note_segment(nuvi, {"segmenttype": "Automotive", "mapupdatetype": "LegacyRmu"})
        note_segment(venu, {"segmenttype": "Fitness"})
        note_segment(edge, {"segmenttype": "Fitness", "mapupdatetype": 4})
    tail = " (openExpress asks the universal map service only)"
    assert [r.getMessage() for r in caplog.records if "map update type" in r.getMessage()] == [
        "maps: unit-info gives the map update type LegacyRmu for 006-B1234-00" + tail,
        "maps: unit-info gives the map update type none given for 006-B3703-00" + tail,
        "maps: unit-info gives the map update type 4 for 006-B4440-00" + tail]
    assert "SECRET123" not in caplog.text and "3000000052" not in caplog.text


# --- the address of a tile's picture: the account's, else the one remembered ------------------

VENU_PICTURE = "https://res.garmin.com/en/products/010-02430-11/g/cf-lg.png"
S3_PICTURE = "https://s3.amazonaws.com/garmin-bucket/edge.png?X-Amz-Signature=secret"


def _tile_of(tiles, part):
    (tile,) = [t for t in tiles if t.part_number == part]
    return tile


def test_a_tile_takes_the_remembered_address_when_the_account_gives_none():
    """Venu 2 is off the account: its tile takes the address remembered with it (from
    unit-info, when it was last connected), and says so; a device on the account takes the
    account's address, which is the newer word."""
    from openexpress import devicelist, known_devices

    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", now=1.0)
    known_devices.set_image_url(VENU, VENU_PICTURE)
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=1.0)
    known_devices.set_image_url(EDGE, "https://res.garmin.com/en/products/older.png")
    tiles = devicelist.tiles([], known_devices.load(), account(), logged_in=True)
    venu, edge = _tile_of(tiles, "006-B3703-00"), _tile_of(tiles, "006-B4440-00")
    assert (venu.image_url, venu.image_source) == (VENU_PICTURE, "the remembered address")
    assert (edge.image_url, edge.image_source) == (ACCOUNT_ANSWER[0]["imageUrl"],
                                                    "the account")
    tiles = devicelist.tiles([], known_devices.load(), None, logged_in=False)  # offline
    assert _tile_of(tiles, "006-B4440-00").image_source == "the remembered address"


def test_an_account_address_that_is_not_allowed_gives_way_to_the_remembered_one():
    """The account gives an address on S3, which is not downloaded: the remembered one
    is taken instead; with none remembered, the account's stays, so that the log says why
    there is no picture."""
    from openexpress import devicelist, known_devices

    answer = account([{**ACCOUNT_ANSWER[0], "imageUrl": S3_PICTURE}])
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=1.0)
    tile = _tile_of(devicelist.tiles([], known_devices.load(), answer, True), "006-B4440-00")
    assert (tile.image_url, tile.image_source) == (S3_PICTURE, "the account")
    known_devices.set_image_url(EDGE, VENU_PICTURE)
    tile = _tile_of(devicelist.tiles([], known_devices.load(), answer, True), "006-B4440-00")
    assert (tile.image_url, tile.image_source) == (VENU_PICTURE, "the remembered address")


def test_an_account_address_over_plain_http_is_still_the_accounts():
    """Asked over HTTPS like every download, it may be downloaded: the account's stays."""
    from openexpress import devicelist, known_devices

    http_url = "http://res.garmin.com/en/products/010-02890-01/V/c1_01_md.png"
    answer = account([{**ACCOUNT_ANSWER[0], "imageUrl": http_url}])
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=1.0)
    known_devices.set_image_url(EDGE, VENU_PICTURE)
    tile = _tile_of(devicelist.tiles([], known_devices.load(), answer, True), "006-B4440-00")
    assert (tile.image_url, tile.image_source) == (http_url, "the account")


def test_the_account_list_keeps_the_picture_addresses_of_remembered_devices():
    """Only of devices remembered here, and only an address that is allowed."""
    from openexpress import devicelist, known_devices

    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.11", "mtp", now=1.0)
    devicelist.remember_picture_addresses(account())
    known = known_devices.load()
    assert set(known) == {EDGE} and known[EDGE].image_url == ACCOUNT_ANSWER[0]["imageUrl"]
    devicelist.remember_picture_addresses(account([{**ACCOUNT_ANSWER[0],
                                                    "imageUrl": S3_PICTURE}]))
    assert known_devices.load()[EDGE].image_url == ACCOUNT_ANSWER[0]["imageUrl"]


# --- the icon's kind from the model name, as a last resort --------------------------------------


@pytest.mark.parametrize("name, kind", [
    ("Venu 2", "watch"), ("fēnix 8 - 47mm, Solar", "watch"), ("FENIX 7", "watch"),
    ("vívoactive 5", "watch"), ("Forerunner 965", "watch"), ("Approach S70", "watch"),
    ("Instinct 3", "watch"), ("MARQ Gen 2", "watch"), ("Lily 2", "watch"),
    ("Edge 1050", "cycling-computer"), ("Edge", "cycling-computer"),
    ("Varia RTL515", "bike-accessory"),
    ("Approach Z82", None), ("Venusaur", None), ("Index S2", None), ("Test watch", None),
    ("", None), (None, None),
])
def test_the_kind_guessed_from_the_start_of_the_model_name(name, kind):
    from openexpress import devicelist

    assert devicelist.kind_from_name(name) == kind


def test_the_icon_of_a_tile_falls_back_to_the_name_and_the_kind_stays_unknown():
    """Venu 2 without a word from Garmin: a watch's icon; the tile's kind - what the program
    reads - stays unknown."""
    from openexpress import devicelist

    (tile,) = devicelist.tiles([], remembered(VENU_SEEN), None, logged_in=False)
    assert tile.category is None
    assert devicelist.icon_kind(tile) == "watch"
    (other,) = devicelist.tiles([], remembered(
        (EDGE, "Index S2", "006-B0000-01", "1.00", "mtp", NOW - 9 * DAY, NOW - DAY)), None,
        logged_in=False)
    assert devicelist.icon_kind(other) is None  # the neutral icon


def test_what_garmin_says_wins_over_the_name():
    from openexpress import devicelist
    from openexpress.known_devices import KnownDevice

    edge_a_watch = KnownDevice(EDGE, "Edge", "006-B4440-00", "1.00", "mtp", 1.0, 4.0,
                               category="watch")
    (tile,) = devicelist.tiles([], {EDGE: edge_a_watch}, None, logged_in=False)
    assert devicelist.icon_kind(tile) == "watch"
    (fenix,) = devicelist.tiles([], {}, account([{**ACCOUNT_ANSWER[1],
                                                  "productDisplayName": "Edge 1050",
                                                  "hasOpticalHeartRate": True}]),
                                logged_in=True)
    assert devicelist.icon_kind(fenix) == "watch"


def test_a_kind_from_the_name_is_never_kept_in_devices_json():
    from openexpress import devicelist, known_devices

    known_devices.remember(VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", now=1.0)
    tiles = devicelist.tiles([], known_devices.load(), account(), logged_in=True)
    assert [devicelist.icon_kind(t) for t in tiles if t.description == "Venu 2"] == ["watch"]
    devicelist.remember_kinds(account())
    assert known_devices.load()[VENU].category is None


# --- grid or list, by the number in a section ---------------------------------------------------


def test_a_section_is_a_grid_up_to_six_then_a_list():
    """Up to six devices in a section, tiles in a grid; seven or more, a list; none,
    no section. Counted for each section on its own."""
    from openexpress import devicelist

    assert devicelist.layout(0) is None
    assert [devicelist.layout(n) for n in (1, 2, 6)] == [devicelist.GRID] * 3
    assert [devicelist.layout(n) for n in (7, 8, 30)] == [devicelist.LIST] * 3


def test_the_view_chosen_lays_out_every_section_alike():
    """The views "grid" and "list" whatever the number; "auto" (and a view not known) by the
    number, as without a view; no device, no section, in every view."""
    from openexpress import devicelist

    grid, listed, auto = devicelist.GRID, devicelist.LIST, devicelist.AUTO
    assert [devicelist.layout(n, grid) for n in (1, 7, 30)] == [grid] * 3
    assert [devicelist.layout(n, listed) for n in (1, 6, 7)] == [listed] * 3
    assert [devicelist.layout(n, auto) for n in (6, 7)] == [grid, listed]
    assert [devicelist.layout(n, "tiles") for n in (6, 7)] == [grid, listed]
    assert {devicelist.layout(0, view) for view in (auto, grid, listed)} == {None}
