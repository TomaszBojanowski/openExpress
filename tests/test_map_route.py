"""Which map service Garmin serves a device through (mapUpdateType of unit-info): the
universal one, which is the only one asked, serves a device filed as Universal, or as
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


def test_cli_maps_with_path_asks_the_universal_service_without_unit_info(device_root, monkeypatch):
    """A folder given with --path (maybe a copy): the universal service, without unit-info."""
    code, asked = _cli_maps(device_root, monkeypatch, {"mapUpdateType": "LegacyRmu"},
                            "--path", str(Path(device_root)))
    assert code == 0 and asked == ["universal"]


# --- unit-info once for each opening of a device -------------------------------------------

def _session(client):
    """A session as the window holds one: the client and the device it opened."""
    import types

    return types.SimpleNamespace(client=client, device=device(PPNM))


def test_unit_info_is_asked_once_a_session_and_again_when_forgotten():
    from openexpress import service

    client = Client({"mapupdatetype": "UniversalDetect"})
    s = _session(client)
    assert service.unit_info(s) == service.unit_info(s) == {"mapupdatetype": "UniversalDetect"}
    assert len(client.asked) == 1  # the second answer is the session's
    service.forget_unit_info(s)  # "Check Again" on the Maps page
    service.unit_info(s)
    assert len(client.asked) == 2
    service.unit_info(_session(client))  # another opening of the device: a new session
    assert len(client.asked) == 3


def test_a_failed_unit_info_is_not_kept():
    from openexpress import service

    client = Client(ApiError("Device info: network error (ConnectionError)"))
    s = _session(client)
    for _ in range(2):
        with pytest.raises(ApiError):
            service.unit_info(s)
    assert len(client.asked) == 2  # each one asked: nothing was kept of the failure


def test_two_threads_of_the_window_ask_unit_info_once():
    """The "Maps" row and the picture ask in the background at the same time: one request."""
    import threading
    import time

    from openexpress import service

    class Slow(Client):
        def unit_info(self, unit_id, part_number):
            time.sleep(0.2)
            return super().unit_info(unit_id, part_number)

    client = Slow({"mapupdatetype": "Universal"})
    s = _session(client)
    threads = [threading.Thread(target=service.unit_info, args=(s,)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(client.asked) == 1


def test_the_picture_and_the_map_check_share_the_answer_of_the_session():
    from openexpress import service
    from openexpress.gui import app

    client = Client({"mapupdatetype": "Universal", "graphicurl": "https://example.com/d.png"})
    s = _session(client)
    assert app.picture_from_unit_info(s.client, s.device, ask=lambda: service.unit_info(s)) == (
        "https://example.com/d.png")
    assert maps.check(s.client, s.device, ask=lambda: service.unit_info(s)) == ([], 0)
    assert client.asked == [("unit-info", UNIT, PART), ("universal",)]
