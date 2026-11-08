"""Which map service Garmin serves a device through (mapUpdateType of unit-info): the
universal one, which is the only one asked, serves a device filed as Universal, or as
UniversalDetect while it declares the data type PreProgrammedNamedMaps. A device served
another way (Gemini, LegacyRmu, Unknown, none given) gets "not supported yet" and no map
check, unless every data type it declares in its GarminDevice.xml is known to be no maps: then
it has none (a Venu 2, a Varia). A data type not known may be maps under another name, so in
doubt it is "not supported yet". All data here is made up."""
import logging
from datetime import timedelta
from pathlib import Path

import pytest

from openexpress import maps
from openexpress.connect import ApiError, lower_keys
from openexpress.device.xmldevice import parse

PPNM = "PreProgrammedNamedMaps"
UNIT, PART = 3000000061, "006-B9999-00"
REFUSAL = "Map updates for this device are not supported yet."
NO_MAPS = "This device has no maps."
# Data types that are no maps, as a watch without maps (a Venu 2) declares them.
NO_MAP_DATATYPES = (
    "ErrorShutdownReports", "ExpressRequest", "FITBinary", "FIT_TYPE_2", "FIT_TYPE_4",
    "FIT_TYPE_72", "GPSData", "IQAppsConfiguration", "IQWatchFaces", "IQWidgets",
    "MEDIA_AUDIOBOOK", "MEDIA_TRACK", "TranslatedText", "UHCConfig", "ULFConfig", "ULFLogs",
    "WiFiConfiguration", "WELLNESS_TYPE_1", "BACKUP_PRIMARY", "ACTIVITY_GCPD", "CustomPOI")


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


@pytest.mark.parametrize("info, datatypes, outcome", [
    ({"mapupdatetype": "Universal"}, (), "universal"),
    ({"mapupdatetype": "Universal"}, (PPNM,), "universal"),
    ({"mapupdatetype": "UniversalDetect"}, (PPNM,), "universal"),  # the fēnix 8, the Edge 1050
    ({"mapupdatetype": "UniversalDetect"}, (), "none"),            # a Varia
    ({"mapupdatetype": "UniversalDetect"}, ("FIT_TYPE_4",), "none"),  # a Venu 2
    ({"mapupdatetype": "UniversalDetect"}, ("NMaps",), "unsupported"),
    ({"mapupdatetype": "UniversalDetect"}, ("Maps",), "unsupported"),
    ({"mapupdatetype": "UniversalDetect"}, ("SupplementalMapsTopo",), "unsupported"),
    # maps under other names, or a data type not known: in doubt, not "no maps"
    ({"mapupdatetype": "UniversalDetect"}, ("BaseMaps",), "unsupported"),
    ({"mapupdatetype": "UniversalDetect"}, ("BirdsEye",), "unsupported"),
    ({"mapupdatetype": "UniversalDetect"}, ("PreProgrammedDEMMaps",), "unsupported"),
    ({"mapupdatetype": "Gemini"}, ("PedestrianMaps",), "unsupported"),
    ({"mapupdatetype": "UniversalDetect"}, ("FIT_TYPE_4", "TopoActiveInstaSearch"), "unsupported"),
    ({"mapupdatetype": "UniversalDetect"}, ("FIT_TYPE_4", "MadeUpCharts"), "unsupported"),
    ({"mapupdatetype": "UniversalDetect"}, NO_MAP_DATATYPES, "none"),  # all a Venu 2 declares
    # the diagnostics and the satellite data the program knows of are no maps either
    ({"mapupdatetype": "UniversalDetect"},
     ("FIT_TYPE_4", "KPI", "RAMDump", "BLELogs", "IQErrorReports", "EphemerisMadeUp"), "none"),
    ({"mapupdatetype": "Gemini"}, ("ManifestGmf",), "unsupported"),  # a zūmo
    ({"mapupdatetype": "Gemini"}, ("CustomPOI",), "none"),  # points of interest are no maps
    ({"mapupdatetype": "Gemini"}, (PPNM,), "unsupported"),
    ({"mapupdatetype": "LegacyRmu"}, (PPNM,), "unsupported"),
    ({"mapupdatetype": "Unknown"}, (PPNM,), "unsupported"),
    ({}, (PPNM,), "unsupported"),                                  # no field
    ({}, (), "none"),
    ({"mapupdatetype": None}, (PPNM,), "unsupported"),
    ({"mapupdatetype": ""}, (PPNM,), "unsupported"),
])
def test_only_a_device_the_universal_service_serves_is_asked_for_its_maps(
        info, datatypes, outcome, caplog):
    client = Client(info)
    universal = outcome == "universal"
    with caplog.at_level(logging.INFO, logger="openexpress"):
        if universal:
            assert maps.check(client, device(*datatypes)) == ([], 0)
        else:
            with pytest.raises(maps.MapsNotSupported) as refused:
                maps.check(client, device(*datatypes))
            assert str(refused.value) == (NO_MAPS if outcome == "none" else REFUSAL)
            assert isinstance(refused.value, maps.DeviceHasNoMaps) is (outcome == "none")
    assert client.asked == [("unit-info", UNIT, PART)] + ([("universal",)] if universal else [])
    (line,) = _route_lines(caplog)  # one line: the way the check went
    assert ("the universal map service's answer" in line) is universal
    assert ("the device declares no maps" in line) is (outcome == "none")
    assert PART in line and str(UNIT) not in caplog.text


def test_a_failed_unit_info_is_a_failed_map_check_not_a_guess():
    """Without the answer the program does not know which service serves the device: the
    check fails as when the map server fails, and the universal service is not asked."""
    client = Client(ApiError("Device info: network error (ConnectionError)"))
    with pytest.raises(ApiError):
        maps.check(client, device(PPNM))
    assert client.asked == [("unit-info", UNIT, PART)]


def test_the_window_says_it_without_a_full_stop():
    assert maps.not_supported_text() == "Map updates for this device are not supported yet"
    assert maps.no_maps_on_device_text() == "This device has no maps"


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


def _with_maps(device_root):
    """The test device, declaring a data type of maps (NMaps) too."""
    xml = device_root / "GARMIN" / "GarminDevice.xml"
    text = xml.read_text()
    assert text.count("</MassStorageMode>") == 1
    xml.write_text(text.replace("</MassStorageMode>", (
        "<DataType><Name>NMaps</Name><File><Location><Path>Garmin/Maps</Path>"
        "<FileExtension>IMG</FileExtension></Location>"
        "<TransferDirection>InputToUnit</TransferDirection></File></DataType>"
        "</MassStorageMode>")))


def test_cli_maps_refuses_a_device_the_universal_service_does_not_serve(
        device_root, monkeypatch, capsys):
    _with_maps(device_root)
    code, asked = _cli_maps(device_root, monkeypatch, {"mapUpdateType": "LegacyRmu"})
    assert code == 1 and asked == ["unit-info"]
    assert capsys.readouterr().err.strip() == "Error: " + REFUSAL


def test_cli_maps_says_a_device_without_maps_has_none(device_root, monkeypatch, capsys):
    """The test device declares no data type of maps (as a Venu 2)."""
    code, asked = _cli_maps(device_root, monkeypatch, {"mapUpdateType": "UniversalDetect"})
    assert code == 1 and asked == ["unit-info"]
    assert capsys.readouterr().err.strip() == "Error: " + NO_MAPS


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

    from fakes import UpdateServer

    from openexpress import service
    from openexpress.connect import GarminClient
    from openexpress.settings import Settings

    class Slow(UpdateServer):
        def request(self, method, url, **kw):
            time.sleep(0.2)
            return super().request(method, url, **kw)

    server = Slow({"mapUpdateType": "Universal"})
    s = _session(GarminClient(Settings(), None, None, server))
    threads = [threading.Thread(target=service.unit_info, args=(s,)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(server.sent) == 1


def test_the_picture_and_the_map_check_share_the_answer_of_the_session():
    from openexpress import service
    from openexpress.gui.pictures import picture_from_unit_info

    client = Client({"mapupdatetype": "Universal", "graphicurl": "https://example.com/d.png"})
    s = _session(client)
    assert picture_from_unit_info(s.client, s.device, ask=lambda: service.unit_info(s)) == (
        "https://example.com/d.png")
    assert maps.check(s.client, s.device, ask=lambda: service.unit_info(s)) == ([], 0)
    assert client.asked == [("unit-info", UNIT, PART), ("universal",)]


def test_the_line_tells_where_the_answer_came_from(caplog, monkeypatch, clock):
    """The device page and its Maps page share one answer: the second line says it came from
    memory, not that the service was asked."""
    from fakes import UpdateServer

    from openexpress.connect import GarminClient
    from openexpress.settings import Settings

    server = UpdateServer({"maps": []})
    client = GarminClient(Settings(), None, None, server)
    with caplog.at_level(logging.INFO, logger="openexpress"):
        maps.check(client, device(PPNM), ask=lambda: {"mapupdatetype": "Universal"})
        clock.current += timedelta(seconds=85)
        maps.check(client, device(PPNM), ask=lambda: {"mapupdatetype": "Universal"})
    first, second = _route_lines(caplog)
    assert first.endswith("the universal map service's answer, asked now")
    assert second.endswith("the universal map service's answer, from memory, 85 s old")
    assert len(server.sent) == 1 and "asking" not in caplog.text
