"""A map whose files go to a folder the device hides inside a folder the listing shows: by the
rule of the updates (updates.hidden_inside: a folder its GarminDevice.xml names for one of its
components, whose top folder is seen, and which the listing does not show). The ordinary write
cannot make such a folder ("Cannot create directory …"), and maps are not written through
openExpress's own session there: the map is refused before anything is downloaded or changes on
the device, in the words of a map whose folder the device hides at the top level. A device that
shows the folder, and a drive (which hides nothing), install it as any map. All devices, maps
and answers here are made up."""

import pytest
from fakes import REGION_COMPONENT, in_language, map_entry, xml_with

from openexpress import download, maps
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse

NAME = "Made-up Region"
# The file of the map, in Garmin/RegionMaps: the device names that folder for its own files
# (REGION_COMPONENT).
REGION_MAP = "Garmin/RegionMaps/D0000001A.img"
REFUSED = ("Map “Made-up Region” cannot be installed: the device hides the folder Garmin/RegionMaps "
           "from the file manager, and openExpress does not install maps there yet.")
REFUSED_PL = ("Nie można zainstalować mapy „Made-up Region”: urządzenie ukrywa folder "
              "Garmin/RegionMaps przed menedżerem plików, a\u00a0openExpress jeszcze nie instaluje tam "
              "map.")
# The line under the map on the Maps page: the device hides the folder, not openExpress.
REASON = {"en": ("The device hides the folder Garmin/RegionMaps from the file manager; openExpress "
                 "does not install maps there yet"),
          "pl": ("Urządzenie ukrywa folder Garmin/RegionMaps przed menedżerem plików; openExpress "
                 "jeszcze nie instaluje tam map")}


@pytest.fixture
def watch(device_root):
    """The made-up device, its GarminDevice.xml naming Garmin/RegionMaps for a component; the
    folder is not listed: the device hides it."""
    xml_with(device_root, REGION_COMPONENT)
    assert not (device_root / "GARMIN" / "RegionMaps").exists()
    return device_root


def _device(root):
    return parse((root / "GARMIN" / "GarminDevice.xml").read_bytes())


def _maps(storage, root, *entries):
    found, _count = maps.read_maps({"maps": list(entries) or [map_entry(NAME, REGION_MAP)]})
    maps.mark_waiting(storage, found, _device(root))
    return found


def _tree(root):
    return sorted((str(p.relative_to(root)), p.is_dir() or p.stat().st_size)
                  for p in root.rglob("*"))


def test_a_map_into_a_folder_hidden_inside_a_seen_one_is_refused_before_anything(watch,
                                                                                 monkeypatch):
    monkeypatch.setattr(download, "fetch", lambda *a, **kw: pytest.fail("downloaded"))
    before = _tree(watch)
    storage = LocalStorage(watch)
    (m,) = _maps(storage, watch)
    assert m.hidden_inside == "Garmin/RegionMaps"
    assert not maps.can_install(m) and not maps.can_install(m, hidden_trial=True)
    assert maps.cannot_install_reason(m) == REASON["en"]
    for trial in (False, True):
        with pytest.raises(maps.MapError) as refused:
            maps.install(m, [m], storage, activate=lambda identifier: pytest.fail("activated"),
                         hidden_trial=trial)
        assert str(refused.value) == REFUSED
    assert _tree(watch) == before


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_line_under_the_map_says_the_device_hides_the_folder(watch, language):
    """Not the words of .System ("openExpress does not install maps into the folder …"): the
    device hides this folder inside one the file manager shows."""
    (m,) = _maps(LocalStorage(watch), watch)
    with in_language(language):
        said = maps.cannot_install_reason(m)
    assert said == REASON[language]


def test_the_refusal_in_polish(watch):
    (m,) = _maps(LocalStorage(watch), watch)
    with in_language("pl"):
        said = maps.cannot_install_message(m)
    assert said == REFUSED_PL


def test_the_command_line_refuses_it_before_it_lists_anything(watch, monkeypatch, capsys):
    from openexpress import cli
    from openexpress.cli import main

    monkeypatch.setattr(cli, "open_storage", lambda args: LocalStorage(watch))
    monkeypatch.setattr(maps, "check", lambda client, dev, ask=None, country=None:
                        maps.read_maps({"maps": [map_entry(NAME, REGION_MAP)]}))
    monkeypatch.setattr(download, "fetch", lambda *a, **kw: pytest.fail("downloaded"))
    before = _tree(watch)
    assert main(["maps", "install", NAME, "--apply"]) == 1
    out, err = capsys.readouterr()
    assert "Installing map" not in out
    assert f"Error: {REFUSED}" in err.splitlines()
    assert _tree(watch) == before


def test_a_map_into_that_folder_the_device_shows_is_installable(watch):
    (watch / "GARMIN" / "RegionMaps").mkdir()
    (m,) = _maps(LocalStorage(watch), watch)
    assert m.hidden_inside is None and maps.can_install(m)


def test_a_map_into_a_folder_the_device_names_for_nothing_is_installable(watch):
    """A folder not named for a component may only not be there yet: made for the write."""
    (m,) = _maps(LocalStorage(watch), watch, map_entry(NAME, "Garmin/NewMaps/D0000001A.img"))
    assert m.hidden_inside is None and maps.can_install(m)
