"""A device whose GarminDevice.xml names it in capitals only ("EPIX") is shown as Garmin names
its model: the field devicemodel of unit-info without ™ and ®, with " — " and " | " as ", ".
The name below is the one unit-info gave for an epix (Gen 2); every device here is made up."""

import types

from openexpress import devicelist, known_devices, service
from openexpress.device.gio_storage import DeviceSummary, FoundDevice

EPIX = "epix™ (Gen 2) — Sapphire Edition | 47 mm"
SHOWN = "epix (Gen 2), Sapphire Edition, 47 mm"
UNIT = 3000000009
PART = "006-B3943-00"


def test_the_name_as_garmin_names_it():
    assert known_devices.garmin_name(EPIX) == SHOWN
    assert known_devices.garmin_name("fēnix® 8 – 47 mm") == (
        "fēnix 8 – 47 mm")  # an en dash stays
    for nothing in (None, "", "   ", 7, "™", "x" * 81):
        assert known_devices.garmin_name(nothing) is None


def test_only_a_name_in_capitals_is_replaced():
    assert known_devices.shown_name("EPIX", SHOWN) == SHOWN
    assert known_devices.shown_name("VARIA RTL515", "Varia RTL515") == "Varia RTL515"
    assert known_devices.shown_name("Edge 1050", "Edge® 1050") == "Edge 1050"
    assert known_devices.shown_name("EPIX", None) == "EPIX"
    assert known_devices.shown_name("006", SHOWN) == "006"  # no letters: not in capitals


def _session(description: str):
    asked = []
    device = types.SimpleNamespace(unit_id=UNIT, part_number=PART, description=description)
    client = types.SimpleNamespace(unit_info=lambda unit_id, part: asked.append(part) or {
        "devicemodel": EPIX})
    return types.SimpleNamespace(device=device, client=client, unit_info_answer=None), asked


def test_unit_info_keeps_the_name_of_a_device_named_in_capitals():
    known_devices.remember(UNIT, "EPIX", PART, "27.18", "mtp", now=1.0)
    session, asked = _session("EPIX")
    service.unit_info(session)
    service.unit_info(session)
    assert asked == [PART]  # asked once: the session keeps the answer
    assert known_devices.load()[UNIT].model_name == SHOWN
    known_devices.remember(UNIT, "EPIX", PART, "27.18", "mtp", now=2.0)  # seen again
    entry = known_devices.load()[UNIT]
    assert entry.model_name == SHOWN and entry.shown_name == SHOWN


def test_a_device_named_otherwise_keeps_no_name_of_unit_info():
    known_devices.remember(UNIT, "epix", PART, "27.18", "mtp", now=1.0)
    session, _asked = _session("epix")
    service.unit_info(session)
    entry = known_devices.load()[UNIT]
    assert entry.model_name is None and entry.shown_name == "epix"


def test_its_tiles_show_it():
    known_devices.remember(UNIT, "EPIX", PART, "27.18", "mtp", now=1.0)
    known_devices.set_model_name(UNIT, SHOWN)
    found = FoundDevice("mtp://made-up/", "EPIX", True, kind="mtp")
    connected = DeviceSummary(found, "EPIX", PART, "27.18", UNIT)
    ((tile, _source),) = devicelist.entries([connected], known_devices.load(), None, False)
    assert tile.description == SHOWN
    ((tile, _source),) = devicelist.entries([], known_devices.load(), None, False)
    assert tile.description == SHOWN  # remembered, not connected


def test_the_device_page_shows_it_once_unit_info_has_it():
    from openexpress.gui.device_page import device_name, show_device_name

    known_devices.remember(UNIT, "EPIX", PART, "27.18", "mtp", now=1.0)
    session, _asked = _session("EPIX")
    assert device_name(session) == "EPIX"  # nothing known of it yet
    service.unit_info(session)
    assert device_name(session) == SHOWN
    labels = []
    win = types.SimpleNamespace(session=session, device_title=types.SimpleNamespace(
        set_label=labels.append))
    show_device_name(win, session)
    assert labels == [SHOWN]
    show_device_name(win, object())  # another device open meanwhile: nothing
    assert labels == [SHOWN]
    session.unit_info_answer = None  # a new opening: as remembered
    assert device_name(session) == SHOWN
