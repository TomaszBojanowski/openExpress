"""Whether a watch connected as a USB drive is synced (only its files uploaded): its
GarminDevice.xml has to declare a data type that goes from the device, and Garmin's server has
to say that Garmin Connect serves it (unit-info "connectSupport" other than "Disabled", as the
kept answers hold it). Nothing is asked of a server here. All devices are made up."""

import pytest
from fakes import RTL515_XML

from openexpress import service
from openexpress.device.xmldevice import parse


def _device(*files):
    """A made-up GarminDevice.xml with these (data type, folder, direction) entries."""
    types = "".join(
        f"<DataType><Name>{name}</Name><File><Location><Path>{folder}</Path>"
        f"<FileExtension>FIT</FileExtension></Location>"
        f"<TransferDirection>{direction}</TransferDirection></File></DataType>"
        for name, folder, direction in files)
    return parse(f"""<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
 <Model><PartNumber>006-B0000-00</PartNumber><SoftwareVersion>1210</SoftwareVersion>
  <Description>Made-up Forerunner</Description></Model>
 <Id>3000000071</Id>
 <MassStorageMode>{types}</MassStorageMode>
</Device>""".encode())


# Like a Forerunner 55: activities and monitoring go from it, courses and workouts to it.
FORERUNNER_LIKE = _device(("FIT_TYPE_4", "Garmin/Activity", "OutputFromUnit"),
                          ("FIT_TYPE_32", "Garmin/Monitor", "OutputFromUnit"),
                          ("FIT_TYPE_6", "Garmin/NewFiles", "InputToUnit"),
                          ("FIT_TYPE_5", "Garmin/NewFiles", "InputToUnit"))
VARIA = parse(RTL515_XML.encode())  # no data types at all
SERVED = {"connectsupport": "Required", "devicemodel": "Forerunner 55"}


def test_a_watch_like_the_forerunner_55_is_synced():
    assert service.drive_sync_refusal(FORERUNNER_LIKE, SERVED) is None


@pytest.mark.parametrize("unit_info", [SERVED, {"connectsupport": "Disabled"}, None])
def test_a_varia_is_not_synced_whatever_the_server_says(unit_info):
    """Its GarminDevice.xml declares no data type: nothing to upload, also when Garmin Connect
    would take its data (the types decide first)."""
    assert service.drive_sync_refusal(VARIA, unit_info) == service.NO_UPLOAD_TYPES


def test_a_drive_whose_files_only_go_to_it_is_not_synced():
    only_to_it = _device(("FIT_TYPE_6", "Garmin/NewFiles", "InputToUnit"))
    assert service.drive_sync_refusal(only_to_it, SERVED) == service.NO_UPLOAD_TYPES


@pytest.mark.parametrize("word", ["Disabled", "disabled", "DISABLED"])
def test_a_drive_that_garmin_connect_does_not_serve_is_not_synced(word):
    """As unit-info says of an inReach Mini 2, which also declares activities."""
    info = {**SERVED, "connectsupport": word}
    assert service.drive_sync_refusal(FORERUNNER_LIKE, info) == service.CONNECT_DISABLED


@pytest.mark.parametrize("unit_info", [None, {}, {"devicemodel": "Forerunner 55"},
                                       {"connectsupport": ""}, {"connectsupport": None},
                                       {"connectsupport": 1}, ["Required"]])
def test_without_a_word_on_connect_support_it_is_not_synced(unit_info):
    """No answer of unit-info, or one that says nothing usable of it: not synced."""
    assert service.drive_sync_refusal(FORERUNNER_LIKE, unit_info) == service.CONNECT_NOT_KNOWN


def test_the_reasons_are_three_different_ones():
    reasons = {service.NO_UPLOAD_TYPES, service.CONNECT_DISABLED, service.CONNECT_NOT_KNOWN}
    assert len(reasons) == 3 and None not in reasons
