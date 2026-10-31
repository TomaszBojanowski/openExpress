"""Tests what the device page says: rows Apps and Music like Maps (counts once their pages have
read them), the Backup row, a remembered device's last sync and account details; on the Maps
and Apps pages, the map update type Garmin gives unsupported maps and the app folders the
device refused. All devices, backups and accounts are made up."""

import types
from datetime import datetime

import pytest
from fakes import record_sync
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
    win.session = types.SimpleNamespace(**fields)
    win._account, win.update_state, win.update_count, win.map_state = None, None, 0, None
    win._update_check_session, win.went = None, []
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync", "_render_map_state", "backup", "logout",
                 "_show_primary_training"):
        setattr(win, name, lambda *a, **kw: None)
    win.select_section = win.went.append
    win._auto_sync_row = lambda: Adw.ActionRow(title="Sync automatically when connected")
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
    assert group.get_title() == "Apps and music"


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
                   "never in the background.")


def _groups(section):
    return {g.get_title(): g for g in section.groups}


@pytest.mark.needs_display
def test_a_drive_that_may_sync_has_the_sync_group_without_the_background_row():
    """As over MTP, with a line on how it is synced in place of the row of the background
    sync (the background service never syncs a drive)."""
    _win, section = _page(mass_storage=True, drive_refusal=None)
    groups = _groups(section)
    assert "USB drive" not in groups
    assert groups["Sync with Garmin Connect"].get_description() == DRIVE_SYNC_NOTE
    assert "Sync automatically when connected" not in _rows(section)
    _win, section = _page()  # a device over MTP: the row, no line
    assert not _groups(section)["Sync with Garmin Connect"].get_description()
    assert "Sync automatically when connected" in _rows(section)


@pytest.mark.needs_display
@pytest.mark.parametrize(("reason", "error"), [("no-upload-types", ""),
                                               ("connect-disabled", ""),
                                               ("no-unit-info", "made-up: no network")])
def test_a_drive_that_is_not_synced_says_why_in_place_of_the_sync(reason, error):
    from openexpress import service

    _win, section = _page(mass_storage=True, drive_refusal=(reason, error))
    groups = _groups(section)
    assert "Sync with Garmin Connect" not in groups
    assert groups["USB drive"].get_description() == service.drive_sync_refusal_text(reason,
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
        session=types.SimpleNamespace(device=types.SimpleNamespace(unit_id=UNIT)))
    _backup(backups, "2026-10-12 (17.37.37)")
    show_backups(win)
    assert said[-1].endswith(f"{NB}· 1 backup")


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

    device = types.SimpleNamespace(unit_id=UNIT, part_number="006-B0000-00",
                                   datatypes=list)
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
    win.session = types.SimpleNamespace(device=types.SimpleNamespace(unit_id=UNIT))
    win._set_map_state = lambda result: None
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
    assert "Map updates for this device aren't supported yet" in labels
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
        ("Primary wearable", "Venu 2")]
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
        ("Primary wearable", "This device")]
