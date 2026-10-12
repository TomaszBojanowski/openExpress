import os
import struct
from pathlib import Path

import pytest

# Tests check English messages regardless of the system language.
os.environ["OPENEXPRESS_LANG"] = "en"
from openexpress import i18n  # noqa: E402

i18n.setup("en")

DEVICE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Device xmlns="http://www.garmin.com/xmlschemas/GarminDevice/v2">
 <Model><PartNumber>006-B0000-00</PartNumber><SoftwareVersion>2339</SoftwareVersion>
  <Description>Test watch</Description></Model>
 <Id>1234567890</Id>
 <MassStorageMode>
  <DataType><Name>FIT_TYPE_4</Name>
   <File><Location><Path>Garmin/Activity</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
   <File><Location><Path>Garmin/NewFiles</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File>
  </DataType>
  <DataType><Name>FIT_TYPE_32</Name>
   <File><Location><Path>Garmin/Monitor</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
  </DataType>
  <DataType><Name>FIT_TYPE_2</Name>
   <File><Location><Path>Garmin/Settings</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
  </DataType>
  <DataType><Name>FIT_TYPE_6</Name>
   <File><Location><Path>Garmin/NewFiles</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File>
  </DataType>
  <DataType><Name>IQWatchApps</Name>
   <File><Location><Path>GARMIN/APPS/MEDIA</Path><FileExtension>PRG</FileExtension></Location>
    <TransferDirection>InputOutput</TransferDirection></File>
  </DataType>
  <UpdateFile><PartNumber>006-B0000-10</PartNumber><Version><Major>23</Major><Minor>39</Minor></Version>
   <Path>Garmin</Path><FileName>GUPDATE.GCD</FileName></UpdateFile>
 </MassStorageMode>
</Device>
"""


def make_fit(time_created: int, file_type: int = 4) -> bytes:
    """Simplest valid FIT file with a file_id message."""
    definition = bytes([0x40, 0, 0]) + struct.pack("<H", 0) + bytes([2, 0, 1, 0x00, 4, 4, 0x86])
    data = bytes([0x00, file_type]) + struct.pack("<I", time_created)
    body = definition + data
    header = struct.pack("<BBHI4sH", 14, 0x20, 2132, len(body), b".FIT", 0)
    return header + body + b"\x00\x00"


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch) -> Path:
    """No test writes to the real log file; the log set up by a test is closed after it."""
    from openexpress import applog

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv(applog.ENV, raising=False)
    monkeypatch.setattr(applog, "_hidden", {})
    monkeypatch.setattr(applog, "_media", {})
    yield tmp_path / "state" / "openexpress" / applog.LOG_NAME
    applog.close()


@pytest.fixture(autouse=True)
def backups(tmp_path, monkeypatch) -> Path:
    """No test writes to the real backup folder: copies of the app list go here."""
    from openexpress import paths

    folder = tmp_path / "Backups"
    monkeypatch.setattr(paths, "default_backup_dir", lambda: folder)
    return folder


@pytest.fixture
def device_root(tmp_path) -> Path:
    root = tmp_path / "device"
    g = root / "GARMIN"
    for d in ("Activity", "Monitor", "Settings", "NewFiles", "APPS/MEDIA"):
        (g / d).mkdir(parents=True)
    (g / "GarminDevice.xml").write_text(DEVICE_XML)
    (g / "Activity" / "2026-10-01-10-00-00.fit").write_bytes(make_fit(1100000000))
    (g / "Activity" / "2026-10-02-10-00-00.fit").write_bytes(make_fit(1100086400))
    (g / "Activity" / "notes.txt").write_text("not FIT")
    (g / "Monitor" / "M1.FIT").write_bytes(make_fit(1100000500, 32))
    (g / "Settings" / "Settings.fit").write_bytes(make_fit(1000, 2))
    return root
