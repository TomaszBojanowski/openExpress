import pytest
from conftest import DEVICE_XML, make_fit

from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import DeviceFile, parse
from openexpress.fit import FIT_EPOCH_OFFSET, FitError, parse_file_id


def test_parse_device_xml():
    dev = parse(DEVICE_XML.encode())
    assert dev.unit_id == 1234567890
    assert dev.software_version_str == "23.39"
    assert dev.description == "Test watch"
    assert [f.path for f in dev.sources("FIT_TYPE_4")] == ["Garmin/Activity"]
    assert [f.path for f in dev.targets("FIT_TYPE_4")] == ["Garmin/NewFiles"]
    assert dev.targets("IQWatchApps")[0].readable
    assert dev.update_files[0].filename == "GUPDATE.GCD"
    assert dev.update_files[0].version == "23.39"


def test_matches_is_case_insensitive_and_checks_extension():
    f = DeviceFile("FIT_TYPE_4", "OutputFromUnit", "Garmin/Activity", extension="FIT")
    assert f.matches("GARMIN/Activity/a.fit")
    assert f.matches("garmin/activity/sub/a.FIT")
    assert not f.matches("GARMIN/Activity/a.txt")
    assert not f.matches("GARMIN/Activityx/a.fit")
    named = DeviceFile("FITBinary", "InputOutput", "Garmin", basename="device", extension="FIT")
    assert named.matches("GARMIN/device.fit")
    assert not named.matches("GARMIN/other.fit")


def test_local_storage_resolves_case(device_root):
    st = LocalStorage(device_root)
    assert st.resolve("Garmin/activity") == "GARMIN/Activity"
    assert st.exists("garmin/garmindevice.xml")
    files = [p for p, _ in st.walk_files("Garmin/Activity")]
    assert "GARMIN/Activity/2026-10-01-10-00-00.fit" in files
    real = st.write("Garmin/NewFiles/x.FIT", b"abc")
    assert real == "GARMIN/NewFiles/x.FIT"
    real = st.write("Garmin/NewDir/y.FIT", b"abc")
    assert (device_root / "GARMIN" / "NewDir" / "y.FIT").read_bytes() == b"abc"


def test_fit_file_id():
    fid = parse_file_id(make_fit(1100000000))
    assert fid.file_type == 4
    assert fid.time_created == 1100000000
    assert fid.time_created_unix == 1100000000 + FIT_EPOCH_OFFSET
    with pytest.raises(FitError):
        parse_file_id(b"not a fit file at all")


def test_the_serial_number_of_a_fit_file_is_not_in_its_repr():
    """A FIT file names the serial number of the device that made it: kept as parsed, but
    never in the text of the record (a log line, an error, a debugger), code audit of
    25.10.2026. Made up."""
    from openexpress.fit import FileId

    fid = FileId(file_type=4, serial_number=3999999999)
    assert fid.serial_number == 3999999999
    assert "3999999999" not in repr(fid) and "serial" not in repr(fid)
