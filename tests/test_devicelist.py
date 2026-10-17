"""The tiles of the start page (D5, T1): one list made of the devices connected now, the
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
# masked answer of 17.10.2026 (testdata/konto-urzadzenia/), with made-up unit IDs and names.
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

    tiles = devicelist.tiles([fenix_connected()], remembered(FENIX_SEEN, EDGE_SEEN), account(),
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

    (fenix, _edge) = devicelist.tiles([fenix_connected("23.40")], remembered(FENIX_SEEN),
                                      account(), logged_in=True)
    assert dataclasses.asdict(fenix) == {
        "description": "fēnix 8", "part_number": "006-B4532-00", "software_version": "23.40",
        "kind": "mtp", "connected": True, "on_account": True, "primary": True,
        "last_seen": NOW - 2 * DAY, "image_part_number": "006-B4532-00",
        "image_url": "https://res.garmin.com/en/products/010-02906-11/v/c1_01_md.png",
        "category": None}


def test_fields_of_a_device_not_connected_come_from_the_memory_first():
    """Not connected: name and version as remembered, before the account's."""
    from openexpress import devicelist

    edge = by_name(devicelist.tiles([], remembered(EDGE_SEEN), account(),
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
        "category": "cycling-computer"}


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


def test_connected_first_the_primary_first_then_by_last_seen():
    """Connected first, the account's primary device first among them; then the rest, seen
    last first; a device only on the account (never seen) at the end."""
    from openexpress import devicelist

    varia_now = connected("file:///run/media/u/GARMIN/", "Varia RTL515", "006-B3592-03",
                          "3.36", VARIA, kind="ums")
    venu = (VENU, "Venu 2", "006-B3703-00", "19.05", "mtp", NOW - 9 * DAY, NOW - 4 * DAY)
    older_edge = (EDGE, "Edge 1050 (remembered)", "006-B4440-00", "33.11", "mtp",
                  NOW - 9 * DAY, NOW - 6 * DAY)
    tiles = devicelist.tiles([varia_now, fenix_connected()], remembered(venu), account(),
                             logged_in=True)
    assert [t.description for t in tiles] == ["fēnix 8", "Varia RTL515", "Venu 2", "Edge 1050"]
    tiles = devicelist.tiles([], remembered(venu, older_edge, VARIA_SEEN), [], logged_in=False)
    assert [t.description for t in tiles] == [
        "Varia RTL515", "Venu 2", "Edge 1050 (remembered)"]


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
    list (only on the account; None before T4)."""
    from openexpress import devicelist

    drive = connected("file:///run/media/u/GARMIN/", "Varia RTL515", "006-B3592-03", "3.36",
                      VARIA, kind="ums")
    mtp = connected("mtp://varia/", "Varia RTL515", "006-B3592-03", "3.36", VARIA)
    known = remembered(EDGE_SEEN)
    entries = devicelist.entries([drive, mtp, fenix_connected()], known, account(),
                                 logged_in=True)
    assert [t.description for t, _ in entries] == [
        "fēnix 8", "Varia RTL515", "Edge 1050 (remembered)"]
    (_, fenix), (_, varia), (_, edge) = entries
    assert fenix.found.uri == "mtp://fenix/" and varia is mtp and edge is known[EDGE]
    answer = account()
    only_on_account = devicelist.entries([], {}, answer, logged_in=True)
    assert [source for _, source in only_on_account] == answer  # T4: the account's entries
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
    """T4: connected, remembered (not connected), only on the account; a device in two
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
    """T4: the source of a tile only on the account is its entry of the account's list (for
    the window to show it); the tile itself still holds no unit ID."""
    from openexpress import devicelist

    answer = account(ACCOUNT_WITH_A_THIRD)
    entries = devicelist.entries([fenix_connected()], remembered(EDGE_SEEN), answer,
                                 logged_in=True)
    tile, source = entries[-1]
    assert tile.description == "Forerunner 965" and source is answer[2]
    assert str(FORERUNNER) not in repr(tile)


# --- the kind of a device (T5.1) -----------------------------------------------------------------


def test_the_kind_of_a_device_comes_from_garmin_only():
    """T5.1, from what Garmin says only: a cycling computer by the account's category
    DEDICATED_BIKE_COMPUTER, a watch by the account's hasOpticalHeartRate, a bike accessory as
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
                     "Index S2": None, "Varia RTL515": "bike-accessory", "Venu 2": None}
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


# --- grid or list, by the number in a section (T6a) ---------------------------------------------


def test_a_section_is_a_grid_up_to_six_then_a_list():
    """T6a: up to six devices in a section, tiles in a grid; seven or more, a list; none,
    no section. Counted for each section on its own."""
    from openexpress import devicelist

    assert devicelist.layout(0) is None
    assert [devicelist.layout(n) for n in (1, 2, 6)] == [devicelist.GRID] * 3
    assert [devicelist.layout(n) for n in (7, 8, 30)] == [devicelist.LIST] * 3
