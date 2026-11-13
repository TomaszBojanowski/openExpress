"""Tests what the device page says: rows Apps and Music like Maps (counts once their pages have
read them), the Backup row, a remembered device's last sync and account details; on the Maps
and Apps pages, the map update type Garmin gives unsupported maps and the app folders the
device refused. All devices, backups and accounts are made up."""

import types
from datetime import datetime

import pytest
from fakes import device_session, record_sync
from fakes import window as stand_in

from openexpress import i18n, sync

NB = "\u00a0"
UNIT = 3000000001


def _page(device=None, **session):
    """The device page as the window builds it, with the window's calls that leave it
    recorded in `win.went` (select_section)."""
    from fakes import page_window
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    win, section = page_window("watch")
    device = device or types.SimpleNamespace(
        description="fēnix 8 - 47mm, Solar", software_version_str="23.39",
        part_number="006-B4532-00", unit_id=UNIT,
        targets=lambda datatype: ([types.SimpleNamespace(path="Music", extension="mp3")]
                                  if datatype == "MEDIA_TRACK" else []))
    fields = dict(device=device, storage=None, logged_in=False, ownership_error=None,
                  account_devices=None, mass_storage=False, space=(None, None),
                  client=types.SimpleNamespace(auth=types.SimpleNamespace(display="x")))
    fields.update(session)
    win.session = device_session(**fields)
    win._account, win.update_state, win.update_count, win.map_state = None, None, 0, None
    win._update_check_session, win.went = None, []
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync", "_render_map_state", "backup", "logout",
                 "_show_primary_training"):
        setattr(win, name, lambda *a, **kw: None)
    win.select_section = win.went.append
    win._auto_sync_row = lambda: Adw.ActionRow(title="Syncs each time a device is connected, even when the window is closed")
    for name in ("show_overview", "_set_update_state", "_render_update_state",
                 "_highlight_update"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.show_overview()
    return win, section


def _rows(section):
    from gi.repository import Adw

    found = {}
    for g in section.groups:
        stack = [g]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Adw.ActionRow):
                found[widget.get_title()] = widget
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return found


# --- the rows "Apps" and "Music" --------------------------------------------------------------


@pytest.mark.needs_display
def test_the_rows_apps_and_music_go_to_their_pages_as_maps_does():
    win, section = _page()
    rows = _rows(section)
    for title, key in (("Apps", "apps"), ("Music", "music")):
        row = rows[title]
        assert row.get_activatable() and not row.get_subtitle()
        row.emit("activated")
        assert win.went[-1] == key
    group = rows["Apps"].get_ancestor(_preferences_group())
    assert group is rows["Music"].get_ancestor(_preferences_group())
    assert group.get_title() == "Apps and Music"


def _preferences_group():
    from gi.repository import Adw

    return Adw.PreferencesGroup


@pytest.mark.needs_display
def test_no_rows_for_a_drive_and_no_music_row_for_a_device_without_music():
    _win, section = _page(mass_storage=True)
    assert "Apps" not in _rows(section) and "Music" not in _rows(section)
    silent = types.SimpleNamespace(description="Edge 1050", software_version_str="33.13",
                                   part_number="006-B4440-00", unit_id=UNIT,
                                   targets=lambda datatype: [])
    _win, section = _page(device=silent)
    assert "Apps" in _rows(section) and "Music" not in _rows(section)


DRIVE_SYNC_NOTE = ("A device connected as a USB drive is synced with the Sync button only, "
                   "never in the background")


def _groups(section):
    return {g.get_title(): g for g in section.groups}


@pytest.mark.needs_display
def test_a_drive_that_may_sync_has_the_sync_group_without_the_background_row():
    """As over MTP, with a line on how it is synced in place of the row of the background
    sync (the background service never syncs a drive)."""
    _win, section = _page(mass_storage=True, drive_refusal=None)
    groups = _groups(section)
    assert "USB Drive" not in groups
    assert groups["Sync With Garmin Connect"].get_description() == DRIVE_SYNC_NOTE
    assert "Syncs each time a device is connected, even when the window is closed" not in _rows(section)
    _win, section = _page()  # a device over MTP: the row, no line
    assert not _groups(section)["Sync With Garmin Connect"].get_description()
    assert "Syncs each time a device is connected, even when the window is closed" in _rows(section)


@pytest.mark.needs_display
@pytest.mark.parametrize(("reason", "error"), [("no-upload-types", ""),
                                               ("connect-disabled", ""),
                                               ("no-unit-info", "made-up: no network")])
def test_a_drive_that_is_not_synced_says_why_in_place_of_the_sync(reason, error):
    from openexpress import service

    _win, section = _page(mass_storage=True, drive_refusal=(reason, error))
    groups = _groups(section)
    assert "Sync With Garmin Connect" not in groups
    assert groups["USB Drive"].get_description() == service.drive_sync_refusal_text(reason,
                                                                                    error)


@pytest.mark.needs_display
def test_a_drive_has_the_backup_row_too():
    """A backup only reads the files of the device: a drive is backed up as over MTP."""
    _win, section = _page(mass_storage=True)
    assert "Backup" in _rows(section)


@pytest.mark.needs_display
def test_once_their_pages_have_read_them_the_rows_say_how_many():
    """Never a read of the device of their own: the counts of the Apps and Music pages, as
    those pages found them; a page drawn again keeps them, another device has none."""
    from openexpress.gui import music_page
    from openexpress.gui.apps_page import note_apps_count

    win, section = _page()
    note_apps_count(win, 4)
    music_page.note_music_count(win, 3, 12_000_000)
    rows = _rows(section)
    assert rows["Apps"].get_subtitle() == "4 Connect IQ apps"
    assert rows["Music"].get_subtitle() == f"3 files{NB}· 12.0{NB}MB"
    win.show_overview()
    rows = _rows(section)
    assert rows["Apps"].get_subtitle() == "4 Connect IQ apps"
    win.apps_count = win.music_count = None  # what _clear_device_pages does
    win.show_overview()
    assert not _rows(section)["Apps"].get_subtitle()


def test_the_apps_and_music_pages_give_their_counts():
    """show_apps and show_music give the counts to the device page (the apps of the group "On
    the device"; all the files of the media)."""
    from pathlib import Path

    apps = Path("openexpress/gui/apps_page.py").read_text()
    music = Path("openexpress/gui/music_page.py").read_text()
    assert "note_apps_count(self, len(state.store_apps))" in apps
    assert "note_music_count(self, len(items), sum(i.size for i in items))" in music
    from openexpress.gui.music_page import note_music_count

    assert note_music_count is not None  # the device page uses the page's own


def test_the_counts_in_polish():
    from openexpress.gui.apps_page import apps_count_text

    i18n.setup("pl")
    try:
        said = [apps_count_text(n) for n in (1, 2, 5)]
    finally:
        i18n.setup("en")
    assert said == ["1 aplikacja Connect IQ", "2 aplikacje Connect IQ", "5 aplikacji Connect IQ"]


# --- the backup -------------------------------------------------------------------------------


def _backup(backups, stamp, unit=UNIT):
    folder = backups / str(unit) / stamp
    folder.mkdir(parents=True)
    return folder


@pytest.mark.needs_display
def test_the_backup_row_says_when_and_how_many(backups):
    from openexpress.backup import STAMP

    _backup(backups, "2026-10-12 (17.37.37)")
    _backup(backups, "2026-10-20 (09.05.00)")
    _backup(backups, "GARMIN-APPS 2026-10-21 (10.00.00)")  # a copy of the app list
    _backup(backups, ".partial-2026-10-22 (10.00.00)")  # cut short
    _backup(backups, "2026-10-24 (08.00.00)", unit=3000000002)  # another device's
    _win, section = _page()
    row = _rows(section)["Backup"]
    newest = i18n.full_time(datetime.strptime("2026-10-20 (09.05.00)", STAMP))
    assert row.get_subtitle() == f"Last on {newest}{NB}· 2 backups"
    assert row.get_tooltip_text() == str(backups)  # the folder, never the device's own


@pytest.mark.needs_display
def test_without_a_backup_the_row_says_so(backups):
    _win, section = _page()
    row = _rows(section)["Backup"]
    assert row.get_subtitle() == "No backup yet"
    assert row.get_tooltip_text() == str(backups)


def test_a_backup_made_says_so_at_once(backups):
    """The task that made it: the row says the new one."""
    from openexpress.gui.device_page import show_backups

    said = []
    win = stand_in(backup_row=types.SimpleNamespace(
        set_subtitle=said.append, set_tooltip_text=lambda text: None),
        session=device_session(device=types.SimpleNamespace(unit_id=UNIT)))
    _backup(backups, "2026-10-12 (17.37.37)")
    show_backups(win)
    assert said[-1].endswith(f"{NB}· 1 backup")


def test_the_backup_row_counts_only_the_backups_openexpress_made(backups, tmp_path):
    """Only folders named as a backup is named: not a symbolic link to one, a file with such a
    name, a copy of the app list or another folder; the newest of them says when."""
    from openexpress.gui.device_page import backups_text

    _backup(backups, "2026-10-12 (17.37.37)")
    _backup(backups, "2026-10-13 (09.00.00)")
    unit = backups / str(UNIT)
    elsewhere = tmp_path / "elsewhere" / "2026-10-20 (10.00.00)"
    elsewhere.mkdir(parents=True)
    (unit / "2026-10-20 (10.00.00)").symlink_to(elsewhere, target_is_directory=True)
    (unit / "2026-10-21 (10.00.00)").write_text("a file")
    (unit / "GARMIN-APPS 2026-10-22 (10.00.00)").mkdir()
    (unit / "2026-10-23").mkdir()
    assert backups_text(UNIT) == f"Last on 2026-10-13 09:00{NB}· 2 backups"
    assert backups_text(3000000099) == backups_text(None) == "No backup yet"


def test_the_backup_words_in_polish(backups):
    from openexpress.gui.device_page import backups_text

    for stamp in ("2026-10-12 (17.37.37)", "2026-10-13 (17.37.37)"):
        _backup(backups, stamp)
    i18n.setup("pl")
    try:
        said = backups_text(UNIT)
        none = backups_text(3000000099)
    finally:
        i18n.setup("en")
    assert said.startswith("Ostatnia: ") and said.endswith(f"{NB}· 2 kopie")
    assert none == "Jeszcze bez kopii"


# --- the map update type ----------------------------------------------------------------------


def test_maps_not_supported_carries_the_type_garmin_gives():
    from openexpress import maps

    # A device with maps (NMaps) that Garmin serves another way than the universal service.
    device = types.SimpleNamespace(unit_id=UNIT, part_number="006-B0000-00",
                                   datatypes=lambda: ["NMaps"])
    with pytest.raises(maps.MapsNotSupported) as raised:
        maps.check(None, device, ask=lambda: {"mapupdatetype": "Gemini"})
    assert raised.value.map_update_type == "Gemini"
    with pytest.raises(maps.MapsNotSupported) as raised:
        maps.check(None, device, ask=dict)
    assert raised.value.map_update_type is None
    with pytest.raises(maps.MapsNotSupported) as raised:
        maps.check(None, device, ask=lambda: {"mapupdatetype": 7})
    assert raised.value.map_update_type is None  # not a word: not shown


def _maps_page(map_update_type):
    from fakes import page_window

    from openexpress.gui.app import MainWindow

    win, section = page_window("maps")
    win.session = device_session(device=types.SimpleNamespace(unit_id=UNIT))
    win._set_map_state = lambda result, no_maps=False: None
    win.show_own_maps = lambda own: None
    MainWindow.show_maps(win, None, None, map_update_type=map_update_type)
    return section


def _labels(widget):
    from gi.repository import Gtk

    found = [widget.get_label()] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child is not None:
        found += _labels(child)
        child = child.get_next_sibling()
    return found


@pytest.mark.needs_display
def test_the_maps_page_names_the_map_update_type():
    section = _maps_page("Gemini")
    labels = [t for g in section.groups for t in _labels(g)]
    assert "Map Updates Not Supported Yet" in labels
    assert "Garmin's map update type for this device: Gemini" in labels
    section = _maps_page(None)
    assert not any("map update type" in t for g in section.groups for t in _labels(g))


def test_the_map_update_type_in_polish():
    from openexpress.gui import maps_page

    i18n.setup("pl")
    try:
        said = maps_page.map_update_type_text("Gemini")
    finally:
        i18n.setup("en")
    assert said == "Typ aktualizacji map tego urządzenia u\u00a0Garmina: Gemini"


# --- a folder the device refused --------------------------------------------------------------


def _ciq_device(software="23.39"):
    return types.SimpleNamespace(unit_id=UNIT, software_version_str=software)


def test_the_folders_the_device_refused_with_its_software_now():
    import json

    from openexpress import ciq, paths

    device = _ciq_device()
    assert ciq.refused_dirs(device) == []
    folder = paths.device_dir(UNIT)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ciq.json").write_text(json.dumps({"refused_dirs": {
        "garmin/apps/media": "23.39", "garmin/apps/settings": "23.38"}}))
    assert ciq.refused_dirs(device) == ["garmin/apps/media"]
    assert ciq.refused_dirs(_ciq_device("23.40")) == []  # asked again with other software


def test_the_apps_page_says_it_in_one_line():
    from openexpress.gui.apps_page import refused_dirs_text

    assert refused_dirs_text([]) is None
    assert refused_dirs_text(["garmin/apps/media"]) == (
        "The device did not let openExpress create garmin/apps/media with this software "
        "version: apps that go there stay queued in Garmin Connect.")
    i18n.setup("pl")
    try:
        said = refused_dirs_text(["garmin/apps/media", "garmin/apps/settings"])
    finally:
        i18n.setup("en")
    assert said == ("Urządzenie nie pozwoliło openExpress utworzyć garmin/apps/media, "
                    "garmin/apps/settings w\u00a0tej wersji oprogramowania: aplikacje, które tam "
                    "trafiają, czekają w\u00a0kolejce Garmin Connect.")


def test_show_apps_puts_the_line_into_the_group_on_the_device():
    from pathlib import Path

    source = Path("openexpress/gui/apps_page.py").read_text()
    assert "refused_dirs_text(ciq.refused_dirs(self.session.device))" in source


# --- the page of a remembered device ----------------------------------------------------------


@pytest.mark.needs_display
def test_the_page_of_a_remembered_device_has_the_details_of_its_last_sync():
    from fakes import remembered_device as _remembered
    from fakes import remembered_window as _window

    known = _remembered()
    sync.SyncState(known.unit_id).save()
    record_sync(known.unit_id, uploaded=1, downloaded=0, skipped_known=0,
                errors=["download: HTTP 404"], lines=[])
    win = _window()
    win._show_remembered(known)
    row = win.remembered.last_sync_row
    assert row.get_enable_expansion() and row.get_tooltip_text()
    details = [t for t in _labels(row) if t and "HTTP 404" in t]
    assert details  # the error, as on the page of a connected device


@pytest.mark.needs_display
def test_a_remembered_device_never_synced_has_no_details():
    from fakes import remembered_device as _remembered
    from fakes import remembered_window as _window

    win = _window()
    win._show_remembered(_remembered())
    row = win.remembered.last_sync_row
    assert row.get_subtitle() == "Never synced" and not row.get_enable_expansion()
    assert row.get_tooltip_text() == "Never synced by this installation of openExpress"
    win._show_remembered(_remembered())  # drawn again: the details once, none here
    assert not row.get_enable_expansion()


@pytest.mark.needs_display
def test_the_details_are_drawn_once_however_often_the_page_is():
    from fakes import remembered_device as _remembered
    from fakes import remembered_window as _window

    known = _remembered()
    sync.SyncState(known.unit_id).save()
    record_sync(known.unit_id, uploaded=1, downloaded=0, skipped_known=0,
                errors=["download: HTTP 404"], lines=[])
    win = _window()
    for _n in range(3):
        win._show_remembered(known)
    row = win.remembered.last_sync_row
    assert len([t for t in _labels(row) if t and "HTTP 404" in t]) == 1


@pytest.mark.needs_display
def test_the_page_of_a_remembered_device_says_what_the_account_says():
    """The account's list in memory: the primary wearable, read only; without the list, or
    for a device that cannot be one, no group."""
    from fakes import remembered_device as _remembered
    from fakes import remembered_window as _window

    known = _remembered()
    account = [{"unitid": known.unit_id, "primaryactivitytrackersettingcapable": True,
                "primaryactivitytrackerindicator": False},
               {"unitid": 3000000004, "productdisplayname": "Venu 2",
                "primaryactivitytrackerindicator": True}]
    win = _window()
    win._account = account
    win._show_remembered(known)
    page = win.remembered
    assert page.account_group.get_visible()
    assert [(r.get_title(), r.get_subtitle()) for r in page.account_rows] == [
        ("Primary Wearable", "Venu 2")]
    win._account = None
    win._show_remembered(known)
    assert not page.account_group.get_visible()
    win._account = [dict(account[0], primaryactivitytrackersettingcapable=False), account[1]]
    win._show_remembered(known)
    assert not page.account_group.get_visible()


@pytest.mark.needs_display
def test_a_device_only_on_the_account_says_it_too():
    from fakes import remembered_window as _window

    entry = {"unitid": 3000000004, "productdisplayname": "Venu 2",
             "primaryactivitytrackersettingcapable": True,
             "primaryactivitytrackerindicator": True}
    win = _window()
    win._account = [entry]
    win._show_account(entry)
    assert [(r.get_title(), r.get_subtitle()) for r in win.remembered.account_rows] == [
        ("Primary Wearable", "This device")]


# --- the row "Satellite Data" -------------------------------------------------------------------


def _satellite_row(win):
    return win.satellite_row if win.satellite_row.get_visible() else None


@pytest.mark.needs_display
def test_a_device_without_the_satellite_data_file_has_no_row(clock):
    win, _section = _page(satellite=None)
    assert _satellite_row(win) is None


@pytest.mark.needs_display
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_satellite_data_row_says_off_updated_and_turned_off(clock, language):
    from datetime import timedelta

    from openexpress import satellite

    i18n.setup(language)
    try:
        written = (clock.current - timedelta(days=1)).timestamp()
        said = {}
        for name, state in (
                ("off", satellite.State()),
                ("updated", satellite.State(on=True, written_at=written, written_sha="0" * 64)),
                ("stopped", satellite.State(stopped=True))):
            win, _section = _page(satellite=state)
            row = _satellite_row(win)
            said[name] = (row.get_title(), row.get_active(), row.get_subtitle())
    finally:
        i18n.setup("en")
    at = datetime.fromtimestamp(written).strftime("%H:%M")
    if language == "en":
        assert said == {
            "off": ("Satellite Data", False, f"Experimental{NB}· when on, each sync updates it"),
            "updated": ("Satellite Data", True, f"Experimental{NB}· updated yesterday, {at}"),
            "stopped": ("Satellite Data", False,
                        "Turned off: the device rejected the last file (CPE_BAD.bin)")}
    else:
        assert said == {
            "off": ("Dane satelitarne", False,
                    f"Eksperymentalne{NB}· po włączeniu aktualizuje je każda synchronizacja"),
            "updated": ("Dane satelitarne", True,
                        f"Eksperymentalne{NB}· zaktualizowane wczoraj, {at}"),
            "stopped": ("Dane satelitarne", False,
                        "Wyłączone: urządzenie odrzuciło ostatni plik (CPE_BAD.bin)")}


@pytest.mark.needs_display
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_satellite_data_row_of_a_watch_that_hides_gpe_and_gpes(clock, language):
    """A watch such as the fēnix 8: its own data current, ours written, the server's wait, and
    turned off when it did not take a file or the file disappeared twice."""
    from datetime import timedelta

    from openexpress import satellite

    files = {"GPE.bin": {"time": 1794500000}, "GPES.bin": {"time": 1794500000}}
    written = (clock.current - timedelta(days=1)).timestamp()
    until = (clock.current + timedelta(minutes=40)).timestamp()
    i18n.setup(language)
    try:
        said = {}
        for name, state in (
                ("off", satellite.State(kind=satellite.RXNETWORKS, files=files)),
                ("current", satellite.State(on=True, kind=satellite.RXNETWORKS, files=files,
                                            current=True)),
                ("updated", satellite.State(on=True, kind=satellite.RXNETWORKS, files=files,
                                            written_at=written)),
                ("wait", satellite.State(on=True, kind=satellite.RXNETWORKS, files=files,
                                         error=f"{satellite.WAIT}{int(until)}")),
                ("not taken", satellite.State(stopped=True, kind=satellite.RXNETWORKS,
                                              files=files, why=satellite.NOT_TAKEN)),
                ("gone twice", satellite.State(stopped=True, kind=satellite.RXNETWORKS,
                                               files=files, why=satellite.GONE_TWICE))):
            win, _section = _page(satellite=state)
            said[name] = _satellite_row(win).get_subtitle()
    finally:
        i18n.setup("en")
    at = datetime.fromtimestamp(written).strftime("%H:%M")
    wait = datetime.fromtimestamp(until).strftime("%H:%M")
    if language == "en":
        assert said == {
            "off": f"Experimental{NB}· when on, each sync updates it",
            "current": f"Experimental{NB}· the data on the device is current",
            "updated": f"Experimental{NB}· updated yesterday, {at}",
            "wait": f"Experimental{NB}· not updated: Garmin's server asks to wait until {wait}",
            "not taken": "Turned off: the device did not take the last file",
            "gone twice": "Turned off: the written file disappeared twice"}
    else:
        assert said == {
            "off": f"Eksperymentalne{NB}· po włączeniu aktualizuje je każda synchronizacja",
            "current": f"Eksperymentalne{NB}· dane na urządzeniu są aktualne",
            "updated": f"Eksperymentalne{NB}· zaktualizowane wczoraj, {at}",
            "wait": (f"Eksperymentalne{NB}· nie zaktualizowano: serwer Garmina prosi o{NB}przerwę"
                     f" do {wait}"),
            "not taken": "Wyłączone: urządzenie nie przyjęło ostatniego pliku",
            "gone twice": "Wyłączone: zapisany plik dwa razy zniknął"}


@pytest.mark.needs_display
def test_the_satellite_data_switch_turns_them_on_and_off_for_the_device(clock):
    from openexpress import satellite

    satellite.save(UNIT, satellite.State(stopped=True))
    win, _section = _page(satellite=satellite.load(UNIT))
    row = _satellite_row(win)
    row.set_active(True)  # turned on again after a rejection: the next sync tries once more
    assert satellite.load(UNIT) == satellite.State(on=True)
    assert row.get_subtitle() == f"Experimental{NB}· the next sync updates it"
    row.set_active(False)
    assert satellite.load(UNIT) == satellite.State()


def test_the_opening_reads_whether_the_device_has_the_file(tmp_path, clock):
    from fakes import made_up_cpe

    from openexpress import satellite
    from openexpress.device.storage import LocalStorage
    from openexpress.gui.device_page import read_satellite

    found = {}
    for name, files in (("like a forerunner", {"GARMIN/REMOTESW/CPE.BIN": made_up_cpe()}),
                        ("like an edge", {"GARMIN/RemoteSW/GPES.bin": b"made up"})):
        root = tmp_path / name
        for path, data in files.items():
            (root / path).parent.mkdir(parents=True)
            (root / path).write_bytes(data)
        unit = UNIT + len(found)
        s = types.SimpleNamespace(device=types.SimpleNamespace(unit_id=unit, part_number="x"),
                                  storage=LocalStorage(root), satellite=None)
        read_satellite(s)
        found[name] = s.satellite
    assert found == {"like a forerunner": satellite.State(), "like an edge": None}
