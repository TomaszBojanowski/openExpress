"""A device that hides a folder of its top level from MTP (a car navigator's .System): what
openExpress cannot see it does not take for missing, and it writes nothing there.

The made-up device (device_root) has no .System at all, as ordinary MTP shows a navigator, and
visible Voice and Vehicle folders. Garmin's update server offers its software and a time-zone
map in .System, a voice and a vehicle it does not have (add-ons: not installed, optional), and a
new part in a new folder of its GARMIN folder (recommended, not installed yet); the universal map
service lists a map whose files are in .System/Map and one in the visible Garmin/Maps. All parts,
names and values are made up."""

import hashlib

import pytest
from fakes import maps_window, window_methods
from fakes import window as stand_in

from openexpress import download, maps, updates
from openexpress.cli import main
from openexpress.connect import GarminClient, lower_keys
from openexpress.device.storage import LocalStorage

SYSTEM = b"made-up navigator software"
NEW_PART = b"made-up new part"


def _entry(part, name, path, filename, data, severity="Recommended", installed=True,
           primary=False, locale=None):
    """One offered item, the keys as the server sends them."""
    return {
        "partNumber": part, "productName": name, "dataType": "Firmware",
        "displayVersion": "9.00", "severity": severity, "isPrimaryFirmware": primary,
        "locale": locale, "releaseState": "live", "releaseNotes": [],
        "deliverable": {"downloadUrl": "https://example.org/" + filename,
                        "md5": hashlib.md5(data).hexdigest().upper(),
                        "sizeInBytes": len(data)},
        "installation": {"path": path, "fileName": filename, "isReinstall": False,
                         "isProductInstalled": installed, "isRestartRequired": False,
                         "order": 0},
    }


def server_answer():
    return {"updates": [
        _entry("006-B0000-00", "Made-up Drive", ".System", "B000000.update", SYSTEM,
               primary=True),
        _entry("006-D9001-00", "Time zone map", ".System", "gmaptz.img", b"tz"),
        _entry("006-D9002-00", "English Made-up Voice", "Voice", "English_Made_Up.vpm", b"v",
               severity="Optional", installed=False, locale="en_US"),
        _entry("006-D9003-00", "Made-up Vehicle", "Vehicle", "Made_Up.srf", b"car",
               severity="Optional", installed=False),
        _entry("006-D9004-00", "New Part", "Garmin\\NewPart", "new.bin", NEW_PART,
               installed=False),
    ]}


@pytest.fixture
def navigator(device_root):
    """The made-up device with the visible folders of a navigator."""
    (device_root / "Voice").mkdir()
    (device_root / "Vehicle").mkdir()
    return device_root


def _offered(root):
    from openexpress import service

    storage = LocalStorage(root)
    device = service.read_device(storage, found_now=False)
    return storage, updates.offered(storage, lower_keys(server_answer()), device,
                                    forget_installed=False)


def _by_part(items):
    return {u.part_number: u for u in items}


# --- what is seen and what is not -------------------------------------------------------------


def test_an_update_in_a_top_folder_not_seen_is_not_taken_for_missing(navigator):
    _storage, items = _offered(navigator)
    by = _by_part(items)
    assert by["006-B0000-00"].hidden_folder == ".System"
    assert by["006-D9001-00"].hidden_folder == ".System"
    # A new folder inside a folder that is seen is made for the write.
    assert by["006-D9004-00"].hidden_folder is None
    assert [u.part_number for u in updates.installable(updates.pending(items))] == [
        "006-D9004-00"]


def test_optional_items_the_device_does_not_have_are_add_ons_not_updates(navigator):
    _storage, items = _offered(navigator)
    by = _by_part(items)
    assert by["006-D9002-00"].is_add_on and by["006-D9003-00"].is_add_on
    # Recommended and not installed yet: a new part to install, as Garmin Express installs it.
    assert not by["006-D9004-00"].is_add_on
    # The device's own software is never an add-on.
    assert not by["006-B0000-00"].is_add_on
    assert [u.part_number for u in updates.pending(items)] == [
        "006-B0000-00", "006-D9001-00", "006-D9004-00"]
    assert [u.part_number for u in updates.add_ons(items)] == ["006-D9002-00", "006-D9003-00"]


def test_without_the_servers_word_an_item_is_no_add_on():
    item = updates.parse_updates(lower_keys({"updates": [
        {**_entry("006-D9002-00", "Voice", "Voice", "x.vpm", b"v", severity="Optional"),
         "installation": {"path": "Voice", "fileName": "x.vpm", "isReinstall": False}}]}))[0]
    assert not item.is_add_on


# --- the install: nothing in a top folder not seen ----------------------------------------------


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    fetched = []

    def fetch(urls, dest, size, md5, session=None, progress=None):
        fetched.append(dest.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(NEW_PART if dest.name == "new.bin" else SYSTEM)
        return dest

    monkeypatch.setattr(download, "fetch", fetch)
    monkeypatch.setattr(updates, "cache_path", lambda u: tmp_path / "cache" / u.filename)
    return fetched


def test_the_install_into_a_top_folder_not_seen_is_refused_before_anything(navigator, downloads):
    storage, items = _offered(navigator)
    system = _by_part(items)["006-B0000-00"]
    with pytest.raises(updates.UpdateError) as refused:
        updates.install([system], storage)
    assert str(refused.value) == ("Not installed: Made-up Drive 9.00 goes to .System, which "
                                  "openExpress cannot see on the device.")
    assert downloads == []
    assert not any(p.name.casefold() == ".system" for p in navigator.iterdir())


def test_a_new_folder_inside_a_folder_seen_is_made_for_the_write(navigator, downloads):
    storage, items = _offered(navigator)
    report = updates.install([_by_part(items)["006-D9004-00"]], storage)
    assert [u.part_number for u in report.written] == ["006-D9004-00"]
    assert (navigator / "GARMIN" / "NewPart" / "new.bin").read_bytes() == NEW_PART


# --- the command line ---------------------------------------------------------------------------


@pytest.fixture
def offered_to(navigator, monkeypatch):
    answer = lower_keys(server_answer())
    monkeypatch.setattr(GarminClient, "software_updates",
                        lambda self, raw, include_reinstalls=False: answer)
    monkeypatch.setattr(GarminClient, "unit_info", lambda self, unit_id, part_number: {})
    return navigator


NOT_INSTALLED_SYSTEM = ("      Not installed: Made-up Drive 9.00 goes to .System, which openExpress "
                        "cannot see on the device.")
NOT_INSTALLED_TZ = ("      Not installed: Time zone map 9.00 goes to .System, which openExpress "
                    "cannot see on the device.")
NOTICE = ("openExpress cannot see the folder .System, where this device keeps its software, so it "
          "installs nothing there. Some devices, such as car navigators, hide it from the "
          "ordinary USB connection.")
ADD_ONS = ("2 add-ons not on the device are not offered: openExpress does not install add-ons "
           "yet.")


def test_the_list_says_what_cannot_be_installed_and_leaves_out_the_add_ons(offered_to, capsys):
    assert main(["--path", str(offered_to), "updates"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Available updates: 3" in out
    assert NOT_INSTALLED_SYSTEM in out and NOT_INSTALLED_TZ in out
    assert not any("006-D9002-00" in line or "006-D9003-00" in line for line in out)
    assert ADD_ONS in out and NOTICE in out


def test_install_all_writes_what_it_can_and_refuses_the_rest(offered_to, downloads, capsys):
    assert main(["--path", str(offered_to), "updates", "--install", "all", "--apply"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert NOT_INSTALLED_SYSTEM.strip() in [line.strip() for line in out]
    assert NOTICE in out
    assert downloads == ["new.bin"]  # nothing for .System, no add-on
    assert (offered_to / "GARMIN" / "NewPart" / "new.bin").read_bytes() == NEW_PART
    assert not any(p.name.casefold() == ".system" for p in offered_to.iterdir())


def test_the_software_alone_is_refused_with_why(offered_to, downloads, capsys):
    assert main(["--path", str(offered_to), "updates", "--install", "006-B0000-00",
                 "--apply"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert NOTICE in out and downloads == []


# --- the window -----------------------------------------------------------------------------------


class _Group:
    def __init__(self, title):
        self.title, self.rows = title, []

    def add(self, row):
        self.rows.append(row)


class _Section:
    def __init__(self):
        self.groups = []

    def group(self, title="", description=""):
        self.groups.append(_Group(title))
        return self.groups[-1]


def _updates_page(items):
    import types

    from openexpress.gui.app import MainWindow

    win = stand_in(
        session=types.SimpleNamespace(device=types.SimpleNamespace(
            description="Made-up Drive", software_version_str="8.50")),
        sections={"updates": _Section()}, busy=False, _action_buttons=[],
        _set_update_state=lambda new, staged=(), not_installed=(): None,
        confirm_update=lambda u: None, load_section=lambda *a, **kw: None)
    window_methods(win, ["_update_row", "_updates_status", "_button"])
    MainWindow.show_updates(win, items)
    return win.sections["updates"].groups


def _texts(widget):
    from gi.repository import Gtk

    from openexpress.gui.window_tasks import descendants

    return [w.get_label() for w in [widget, *descendants(widget)]
            if isinstance(w, (Gtk.Label, Gtk.Button)) and w.get_label()]


@pytest.mark.needs_display
def test_the_updates_page_installs_nothing_into_a_top_folder_not_seen(navigator):
    from gi.repository import Adw

    _storage, items = _offered(navigator)
    status, available, advanced = _updates_page(items)
    top = _texts(status.rows[0])
    # The device's software: no Install, and why.
    assert NOTICE in top and "Install" not in top and "Install All" not in top
    rows = {r.get_title(): r for r in available.rows}
    assert rows["Time zone map"].get_subtitle().endswith(
        "Cannot be installed: openExpress cannot see its folder .System on this device")
    assert "Install" not in _texts(rows["Time zone map"])
    assert "Install" in _texts(rows["New Part"])
    (add_ons,) = [r for r in advanced.rows if isinstance(r, Adw.ActionRow)
                  and r.get_title() == "Add-Ons"]
    assert add_ons.get_subtitle() == ("2 add-ons that are not on the device; openExpress does "
                                      "not install add-ons yet")


# --- maps -----------------------------------------------------------------------------------------


def _map(name, path):
    return {
        "displayname": name, "maptype": "CityNavigator", "installationstate": 1,
        "isreinstall": True, "canuninstall": True,
        "release": {"majorversion": 2026, "minorversion": 20},
        "downloadhosts": {"foregroundprimaryhost": "https://example.org/maps/",
                          "failoverhosts": []},
        "filestoremove": [{"filename": path}],
        "installoptions": [{"ispreferred": True, "identifier": {"regionpartnumber": name},
                            "files": [{"filename": path, "datatype": "X",
                                       "deliverableoptions": [{
                                           "type": 1, "url": "x.img", "md5": "AAAA",
                                           "sizeinbytes": 4}]}]}],
    }


def test_a_map_whose_folder_is_not_seen_cannot_be_read_not_removed(navigator):
    (navigator / "GARMIN" / "Maps").mkdir()
    found, _count = maps.read_maps({"maps": [
        _map("Made-up City Navigator", ".System/Map/D0000001A.img"),
        _map("Made-up Topo", "Garmin/Maps/T.img")]})
    maps.mark_waiting(LocalStorage(navigator), found)
    hidden, gone = found
    assert hidden.unseen and hidden.waiting is None and not hidden.update_available
    assert hidden.status == "cannot be read: openExpress cannot see the folder of its files"
    # Its folder is seen, its file is not there: removed.
    assert not gone.unseen and gone.waiting == maps.WAIT_REMOVE


def test_a_map_that_cannot_be_read_is_not_removed(navigator):
    found, _count = maps.read_maps({"maps": [
        _map("Made-up City Navigator", ".System/Map/D0000001A.img")]})
    storage = LocalStorage(navigator)
    maps.mark_waiting(storage, found)
    with pytest.raises(maps.MapError, match="cannot see the folder of its files"):
        maps.uninstall(found[0], found, storage)


@pytest.mark.needs_display
def test_the_maps_page_shows_a_map_that_cannot_be_read_without_buttons(navigator):
    from gi.repository import Gtk

    from openexpress.gui.app import MainWindow
    from openexpress.gui.window_tasks import descendants

    storage = LocalStorage(navigator)
    win, _reloads = maps_window(storage)
    found, count = maps.read_maps({"maps": [
        _map("Made-up City Navigator", ".System/Map/D0000001A.img")]})
    maps.mark_waiting(storage, found)  # as the window's check does before the page
    MainWindow.show_maps(win, (found, count))
    (on_device,) = win.sections["maps"].groups
    (row,) = [r for r in on_device.rows if r.get_title() == "Made-up City Navigator"]
    assert row.get_subtitle().endswith(
        "\nCannot be read: openExpress cannot see the folder of its files on this device")
    assert not [w for w in descendants(row) if isinstance(w, Gtk.Button)]


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_new_words_in_both_languages(language):
    from openexpress import i18n

    i18n.setup(language)
    try:
        u = updates.parse_updates(lower_keys(server_answer()))[0]
        u.hidden_folder = ".System"
        texts = [updates.not_installed_here_text(u), updates.hidden_software_text(".System"),
                 updates.add_ons_text(2)]
    finally:
        i18n.setup("en")
    expected = {
        "en": [("Not installed: Made-up Drive 9.00 goes to .System, which openExpress cannot see "
                "on the device."), NOTICE, ADD_ONS],
        "pl": [("Nie zainstalowano: Made-up Drive 9.00 trafia do folderu .System, którego "
                "openExpress nie widzi na urządzeniu."),
               ("openExpress nie widzi folderu .System, w\u00a0którym to urządzenie trzyma swoje "
                "oprogramowanie, więc niczego tam nie instaluje. Niektóre urządzenia, np. "
                "nawigacje samochodowe, ukrywają go przed zwykłym połączeniem USB."),
               ("Pominięto 2 dodatki, których nie ma na urządzeniu: openExpress jeszcze nie "
                "instaluje dodatków.")]}
    assert texts == expected[language]
