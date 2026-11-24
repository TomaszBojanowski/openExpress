"""A Garmin device seen as a USB drive (UmsStorage, as the window and the command line find it)
hides nothing from its listing: a folder its GarminDevice.xml names for one of its components,
or a folder of the top level a map or an own map goes to, that is not on the drive is only not
there yet. No such folder is taken for one the device hides, and no USB session of openExpress's
own is asked of a drive. All devices, files and answers here are made up."""

import types

import pytest
from fakes import hidden_folder_waits, map_entry, offered_update, xml_with

from openexpress import download, hidden_folders, maps, ownmaps, service, updates
from openexpress.connect import lower_keys
from openexpress.device import gio_storage
from openexpress.device.storage import DeviceError, LocalStorage, UmsStorage

TEXT = b"made-up language file"
SOFTWARE = b"made-up watch software"
TEXT_PART, SOFTWARE_PART = "006-D0004-00", "006-B0000-00"
# A component in Garmin/Text, a folder the drive does not have yet.
TEXT_COMPONENT = """  <UpdateFile><PartNumber>006-D0004-00</PartNumber><Version><Major>3</Major>
   <Minor>10</Minor></Version><Path>Garmin/Text</Path><FileName>made_up.ln4</FileName>
  </UpdateFile>
"""
WHERE = "file:///run/media/someone/GARMIN/"


ANSWER = {"updates": [
    offered_update(SOFTWARE_PART, "Made-up Watch", "Garmin", "GUPDATE.GCD", SOFTWARE,
                   version="3.20", primary=True),
    offered_update(TEXT_PART, "Made-up Language", "GARMIN\\TEXT", "made_up.ln4", TEXT,
                   version="3.20"),
]}


@pytest.fixture
def drive(device_root):
    xml_with(device_root, TEXT_COMPONENT)
    assert not (device_root / "GARMIN" / "Text").exists()
    return device_root


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    content = {"made_up.ln4": TEXT, "GUPDATE.GCD": SOFTWARE}

    def fetch(urls, dest, size, md5, session=None, progress=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content[dest.name])
        return dest

    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    monkeypatch.setattr(download, "fetch", fetch)


@pytest.fixture
def waits(monkeypatch):
    """The waits for a USB session of openExpress's own, recorded instead of slept."""
    return hidden_folder_waits(monkeypatch)


PLACES = {"its place on USB not known": None,
          "its place on USB known": gio_storage.GarminUsb("4b11", 3, 14)}


def _offered(storage):
    device = service.read_device(storage, found_now=False)
    return {u.part_number: u for u in updates.offered(storage, lower_keys(ANSWER), device,
                                                      forget_installed=False)}


@pytest.mark.parametrize("place", list(PLACES))
def test_an_update_into_a_named_folder_not_on_the_drive_yet_is_written(drive, downloads, waits,
                                                                       place):
    storage = UmsStorage(drive, WHERE, PLACES[place])
    assert not hidden_folders.session_possible(storage)
    u = _offered(storage)[TEXT_PART]
    assert (u.hidden_inside, u.folder_writable, u.refused_inside) == (None, False, False)
    report = updates.install([u], storage)
    assert [w.part_number for w in report.written] == [TEXT_PART] and report.failed == []
    assert (drive / "GARMIN" / "TEXT" / "made_up.ln4").read_bytes() == TEXT
    assert waits == []  # no session waited for


def test_install_all_on_a_drive_goes_on_after_a_file_refused(drive, downloads, monkeypatch):
    """One file the drive refuses does not stop the others: the drive is still there."""
    real = LocalStorage.write_file_checked

    def write(self, path, *a, **kw):
        if path.casefold().endswith("made_up.ln4"):
            raise DeviceError("made-up refusal")
        return real(self, path, *a, **kw)

    monkeypatch.setattr(LocalStorage, "write_file_checked", write)
    storage = UmsStorage(drive, WHERE, PLACES["its place on USB known"])
    by = _offered(storage)
    report = updates.install([by[TEXT_PART], by[SOFTWARE_PART]], storage)
    assert [w.part_number for w in report.written] == [SOFTWARE_PART]
    assert [(f.part_number, why) for f, why in report.failed] == [(TEXT_PART, "made-up refusal")]
    assert (drive / "GARMIN" / "GUPDATE.GCD").read_bytes() == SOFTWARE


def test_a_drive_whose_top_level_cannot_be_listed_is_gone(drive, monkeypatch):
    """Install All stops then (the drive was unplugged), as for any device."""
    storage = UmsStorage(drive, WHERE, PLACES["its place on USB known"])
    assert not updates._gone(storage)

    def gone(self, path):
        raise DeviceError("made-up: no such device")

    monkeypatch.setattr(UmsStorage, "listdir", gone)
    assert updates._gone(storage)


def test_a_map_into_a_top_folder_not_on_the_drive_yet_is_not_taken_for_hidden(drive):
    found, _count = maps.read_maps({"maps": [map_entry("Made-up Topo", "Map/D0000001A.img",
                                                       state=1)]})
    storage = UmsStorage(drive, WHERE, PLACES["its place on USB known"])
    maps.mark_waiting(storage, found)
    assert found[0].hidden_folder is None and maps.can_install(found[0])


def test_a_map_into_a_named_folder_not_on_the_drive_yet_is_not_taken_for_hidden(drive):
    """Garmin/Text, named for a component and not on the drive: not hidden inside GARMIN."""
    from openexpress.device.xmldevice import parse

    found, _count = maps.read_maps({"maps": [map_entry("Made-up Topo", "Garmin/Text/D0000001A.img",
                                                       state=1)]})
    storage = UmsStorage(drive, WHERE, PLACES["its place on USB known"])
    maps.mark_waiting(storage, found, parse((drive / "GARMIN" / "GarminDevice.xml").read_bytes()))
    assert found[0].hidden_inside is None and maps.can_install(found[0])


def test_an_own_map_into_a_top_folder_not_on_the_drive_yet_is_not_refused(drive):
    device = types.SimpleNamespace(targets=lambda datatype: [types.SimpleNamespace(path="Map/")])
    storage = UmsStorage(drive, WHERE, PLACES["its place on USB known"])
    assert ownmaps.hidden_folder(device, storage) is None
