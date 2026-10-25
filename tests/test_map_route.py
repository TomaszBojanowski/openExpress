"""Which map service Garmin serves a device through (mapUpdateType of unit-info, 25.10.2026):
the universal one, which is the only one asked, serves a device filed as Universal, or as
UniversalDetect while it declares the data type PreProgrammedNamedMaps. A device served
another way (Gemini, LegacyRmu, Unknown, none given) gets "not supported yet" and no map
check. All data here is made up."""

import logging
from pathlib import Path

import pytest

from openexpress import maps
from openexpress.connect import ApiError, lower_keys
from openexpress.device.xmldevice import parse

PPNM = "PreProgrammedNamedMaps"
UNIT, PART = 3000000061, "006-B9999-00"
REFUSAL = "Map updates for this device aren't supported yet."


def device(*datatypes):
    """A made-up device that declares these data types, each with a folder."""
    files = "".join(
        f"<DataType><Name>{name}</Name><File><Location><Path>Garmin/{name}</Path>"
        "<FileExtension>IMG</FileExtension></Location>"
        "<TransferDirection>InputToUnit</TransferDirection></File></DataType>"
        for name in datatypes)
    return parse(f"""<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
 <Model><PartNumber>{PART}</PartNumber><SoftwareVersion>100</SoftwareVersion>
  <Description>Made-up device</Description></Model>
 <Id>{UNIT}</Id>
 <MassStorageMode>{files}</MassStorageMode>
</Device>
""".encode())


class Client:
    """unit-info gives `info` (an exception is raised); the universal service lists no map."""

    def __init__(self, info):
        self.info, self.asked = info, []

    def unit_info(self, unit_id, part_number):
        self.asked.append(("unit-info", unit_id, part_number))
        if isinstance(self.info, Exception):
            raise self.info
        return self.info

    def universal_maps(self, device_xml, reinstall=False):
        self.asked.append(("universal",))
        return lower_keys({"maps": []})


def _route_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("maps: map update type")]


@pytest.mark.parametrize("info, datatypes, universal", [
    ({"mapupdatetype": "Universal"}, (), True),
    ({"mapupdatetype": "Universal"}, (PPNM,), True),
    ({"mapupdatetype": "UniversalDetect"}, (PPNM,), True),   # the fēnix 8 and the Edge 1050
    ({"mapupdatetype": "UniversalDetect"}, (), False),
    ({"mapupdatetype": "UniversalDetect"}, ("FIT_TYPE_4",), False),
    ({"mapupdatetype": "Gemini"}, (PPNM,), False),
    ({"mapupdatetype": "LegacyRmu"}, (PPNM,), False),
    ({"mapupdatetype": "Unknown"}, (PPNM,), False),
    ({}, (PPNM,), False),                                     # no field
    ({"mapupdatetype": None}, (PPNM,), False),
    ({"mapupdatetype": ""}, (PPNM,), False),
])
def test_only_a_device_the_universal_service_serves_is_asked_for_its_maps(
        info, datatypes, universal, caplog):
    client = Client(info)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        if universal:
            assert maps.check(client, device(*datatypes)) == ([], 0)
        else:
            with pytest.raises(maps.MapsNotSupported) as refused:
                maps.check(client, device(*datatypes))
            assert str(refused.value) == REFUSAL
    assert client.asked == [("unit-info", UNIT, PART)] + ([("universal",)] if universal else [])
    (line,) = _route_lines(caplog)  # one line: the way the check went
    assert ("asking the universal map service" in line) is universal
    assert PART in line and str(UNIT) not in caplog.text


def test_a_failed_unit_info_is_a_failed_map_check_not_a_guess():
    """Without the answer the program does not know which service serves the device: the
    check fails as when the map server fails, and the universal service is not asked."""
    client = Client(ApiError("Device info: network error (ConnectionError)"))
    with pytest.raises(ApiError):
        maps.check(client, device(PPNM))
    assert client.asked == [("unit-info", UNIT, PART)]


def test_the_window_says_it_without_a_full_stop():
    assert maps.not_supported_text() == "Map updates for this device aren't supported yet"


# --- the command line ---------------------------------------------------------------------

def _cli_maps(device_root, monkeypatch, info, *argv):
    """`openexpress maps` on the made-up device of the tests (found as a device, not given
    with --path, unless `argv` says so); unit-info gives `info`."""
    from openexpress import cli
    from openexpress.connect import GarminClient
    from openexpress.device.storage import LocalStorage

    asked = []
    found = cli.open_storage
    monkeypatch.setattr(cli, "open_storage",
                        lambda args: found(args) if args.path else LocalStorage(device_root))

    def unit_info(self, unit_id, part_number):
        asked.append("unit-info")
        return lower_keys(info)

    monkeypatch.setattr(GarminClient, "unit_info", unit_info)
    monkeypatch.setattr(GarminClient, "universal_maps", lambda self, device_xml, reinstall=False: (
        asked.append("universal"), lower_keys({"maps": []}))[1])
    return cli.main([*argv, "maps"]), asked


def test_cli_maps_refuses_a_device_the_universal_service_does_not_serve(
        device_root, monkeypatch, capsys):
    code, asked = _cli_maps(device_root, monkeypatch, {"mapUpdateType": "LegacyRmu"})
    assert code == 1 and asked == ["unit-info"]
    assert capsys.readouterr().err.strip() == "Error: " + REFUSAL


def test_cli_maps_asks_the_universal_service_for_a_device_it_serves(device_root, monkeypatch):
    code, asked = _cli_maps(device_root, monkeypatch, {"mapUpdateType": "Universal"})
    assert code == 0 and asked == ["unit-info", "universal"]


def test_cli_maps_with_path_asks_as_before(device_root, monkeypatch):
    """A folder given with --path (maybe a copy): the universal service, without unit-info."""
    code, asked = _cli_maps(device_root, monkeypatch, {"mapUpdateType": "LegacyRmu"},
                            "--path", str(Path(device_root)))
    assert code == 0 and asked == ["universal"]
