"""A file that two sources of a data type name (GarminDevice.xml spells one folder two ways) is
taken once, as the first source names it: listed with the activities, counted, planned for
upload and backed up once. All devices and files are made up."""

import pytest
from conftest import DEVICE_XML, make_fit
from fakes import UPLOAD_SETTINGS

from openexpress import activities, backup, sync
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

ACTIVITY_FOLDER = """<File><Location><Path>Garmin/Activity</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>"""
# The activity folder named again in capitals, and ECG recordings in a folder named twice.
TWICE = ACTIVITY_FOLDER + """
   <File><Location><Path>GARMIN/ACTIVITY</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>"""
ECG = """  <DataType><Name>FIT_TYPE_61</Name>
   <File><Location><Path>Garmin/ECG</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
   <File><Location><Path>GARMIN/ecg</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File>
  </DataType>
"""
ACTIVITIES = ["2026-10-01-10-00-00.fit", "2026-10-02-10-00-00.fit"]


class Spelled(LocalStorage):
    """Names each file below a folder as the folder was asked for, not as it is on the disk:
    the paths of two spellings differ in their letter case only."""

    def walk_files(self, path):
        real = self.resolve(path)
        return [(path.strip("/") + p[len(real):], e) for p, e in super().walk_files(path)]


@pytest.fixture
def twice(device_root):
    xml = device_root / "GARMIN" / "GarminDevice.xml"
    assert DEVICE_XML.count(ACTIVITY_FOLDER) == 1
    xml.write_text(DEVICE_XML.replace(ACTIVITY_FOLDER, TWICE).replace(
        "</MassStorageMode>", ECG + "</MassStorageMode>"))
    (device_root / "GARMIN" / "ECG").mkdir()
    for n in (1, 2, 3):
        (device_root / "GARMIN" / "ECG" / f"ECG{n}.FIT").write_bytes(make_fit(1100000000 + n))
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    assert len(dev.sources("FIT_TYPE_4")) == len(dev.sources("FIT_TYPE_61")) == 2
    return device_root, dev


@pytest.mark.parametrize("storage", [LocalStorage, Spelled])
def test_the_activities_of_the_device_are_listed_once(twice, storage):
    root, dev = twice
    found = activities.DeviceActivities(dev, storage(root), None)._on_device()
    folder = "GARMIN/Activity" if storage is LocalStorage else "Garmin/Activity"
    assert [a.path for a in found] == [f"{folder}/{n}" for n in reversed(ACTIVITIES)]


@pytest.mark.parametrize("storage", [LocalStorage, Spelled])
def test_the_files_of_the_sources_are_counted_once(twice, storage):
    root, dev = twice
    assert sync._count_files(storage(root), dev.sources("FIT_TYPE_61")) == 3
    plan = sync.plan_uploads(dev, storage(root), {"datatypes": [
        {"datatypename": "FIT_TYPE_61", "uploadlocations": [{"url": "https://up.example/ecg"}]}]},
        sync.SyncState(dev.unit_id))
    assert plan.ecg_recordings == 3 and plan.candidates == []


@pytest.mark.parametrize("storage", [LocalStorage, Spelled])
def test_the_activities_to_sync_are_counted_once(twice, storage):
    root, dev = twice
    sync.SyncState(dev.unit_id).save()
    counted = sync._activities_to_sync(dev, storage(root))
    assert counted.listed == frozenset(ACTIVITIES) and counted.count == 2


@pytest.mark.parametrize("storage", [LocalStorage, Spelled])
def test_an_upload_is_planned_once_for_each_file(twice, storage):
    root, dev = twice
    plan = sync.plan_uploads(dev, storage(root), UPLOAD_SETTINGS, sync.SyncState(dev.unit_id),
                             only_types={"FIT_TYPE_4"})
    folder = "GARMIN/Activity" if storage is LocalStorage else "Garmin/Activity"
    assert [(c.name, c.device_path) for c in plan.candidates] == [
        (n, f"{folder}/{n}") for n in ACTIVITIES]


def test_a_backup_copies_each_file_once(twice, tmp_path):
    root, dev = twice
    steps = []
    done = backup.run_backup(dev, LocalStorage(root), tmp_path / "bk", keep=1,
                             progress=steps.append)
    copied = sorted(str(p.relative_to(done.path)) for p in done.path.rglob("*") if p.is_file())
    assert copied == ["FIT_TYPE_2/Settings.fit", "FIT_TYPE_32/M1.FIT",
                      *(f"FIT_TYPE_4/{n}" for n in ACTIVITIES),
                      *(f"FIT_TYPE_61/ECG{n}.FIT" for n in (1, 2, 3)), "GarminDevice.xml"]
    assert done.files == len(copied) == len(steps)
