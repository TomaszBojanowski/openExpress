"""One language for what the window says of a device: the state of the updates, the version
("Software X"), the free space, the USB mode ("USB drive", "MTP"), the reason a device cannot
be used with the account (only in the banner), "Last seen" and a version known only from the
account. All data here is made up."""

import re
import time
import types
from datetime import datetime
from pathlib import Path

import pytest
from fakes import window as stand_in
from fakes import window_methods
from test_updates_system import (
    BUNDLE_ENTRY,
    FONT_ENTRY,
    _entry,
    _items,
    _overview,
    _staged_items,
    _updates_page,
)

from openexpress import i18n, updates

NB = "\u00a0"


def _row_text(items, drive=False):
    """What the row "Device software" of the device page says of these updates."""
    win, MainWindow = _overview(drive=drive)
    MainWindow._set_update_state(win, updates.pending(items), updates.staged(items))
    return win.update_row.get_subtitle()


# --- the state of the updates in one text --------------------------------------------------


OTHER = _entry("006-D0002-00", "Test Map Data", "Etyp", "Garmin\\SID", "DA00200.sid", b"sid")


@pytest.mark.needs_display
@pytest.mark.parametrize("entries, words", [
    ([BUNDLE_ENTRY], "Version 23.40 available"),
    ([FONT_ENTRY, BUNDLE_ENTRY], "2 updates available"),
    ([FONT_ENTRY], "Update available: Test Font 23.40"),
    ([FONT_ENTRY, OTHER], "2 updates available"),
    ([], "Software is up to date"),
])
def test_the_updates_page_says_the_state_in_the_words_of_the_device_row(entries, words):
    items = _items(*entries)
    _win, status, _groups, _buttons = _updates_page(items)
    assert status["texts"][0][0] == words
    assert _row_text(_items(*entries)) == words


@pytest.mark.needs_display
@pytest.mark.parametrize("drive, words", [
    (False, "Update ready: disconnect the device to install it"),
    (True, "Update ready: eject the device to install it"),
])
def test_an_update_ready_is_said_alike_on_the_row_and_the_updates_page(drive, words):
    """The title says what to do; the line under it the versions only (not the same words
    again)."""
    _win, status, _groups, _buttons = _updates_page(_staged_items(), drive=drive)
    title, versions = status["texts"][0][0], status["texts"][1][0]
    assert title == words == _row_text(_staged_items(), drive=drive)
    assert "install it" not in versions and "23.39" in versions


@pytest.mark.needs_display
@pytest.mark.parametrize("entries", [[], [FONT_ENTRY, OTHER]])
def test_the_updates_page_says_software_x_as_everywhere(entries):
    """Under the state: "Software 23.39", as under the name of the device, not "<model> ·
    version 23.39"."""
    _win, status, _groups, _buttons = _updates_page(_items(*entries))
    assert status["texts"][1][0] == "Software 23.39"


def test_the_words_of_the_update_state():
    from openexpress.gui import words

    only = types.SimpleNamespace(is_system=True, version="33.13", title="Bundle")
    assert words.update_state_text("available", 1, only) == "Version 33.13 available"
    assert words.update_state_text("current") == "Software is up to date"
    assert words.update_state_text("failed") == "Could not check for updates"
    assert words.update_state_text(None) == "Checking…"
    assert words.update_state_text("ready", mass_storage=True) == (
        "Update ready: eject the device to install it")


# --- the free space in one caption ---------------------------------------------------------


def test_the_free_space_in_one_caption():
    from openexpress.gui import words

    assert words.space_text(2_100_000_000, 32_000_000_000) == f"2.1{NB}GB free of 32.0{NB}GB"
    assert words.space_text(2_100_000_000) == f"2.1{NB}GB free"
    assert words.space_text(None) == "Unknown"
    assert words.space_text(None, 32_000_000_000) == "Unknown"
    assert words.space_text(None, reading=True) == "Reading…"


def _storage_row(space):
    from gi.repository import Adw, Gtk

    from openexpress.gui.window_tasks import render_space

    win = stand_in(storage_row=Adw.ActionRow(), storage_bar=Gtk.LevelBar(),
                   session=types.SimpleNamespace(space=space))
    render_space(win)
    return win.storage_row.get_subtitle()


@pytest.mark.needs_display
def test_the_device_page_says_reading_then_unknown():
    assert _storage_row(None) == "Reading…"
    assert _storage_row((None, None)) == "Unknown"
    assert _storage_row((2_100_000_000, None)) == f"2.1{NB}GB free"


@pytest.mark.needs_display
@pytest.mark.parametrize("free, said", [(None, "Unknown"), (5_000_000_000, f"5.0{NB}GB free")])
def test_the_music_page_says_the_space_in_the_same_words(free, said):
    from fakes import page_window


    win, section = page_window("music")
    dev = types.SimpleNamespace(targets=lambda datatype: [], unit_id=None)
    win.session = types.SimpleNamespace(device=dev, storage=None)
    win.choose_music = win.choose_playlist = win.remove_music = lambda *a, **kw: None
    window_methods(win, ["show_music"])
    win.show_music(([], []), free=free)
    (add,) = [g for g in section.groups if g.get_title() == "Add to the device"]
    assert add.get_description() == said


@pytest.mark.parametrize("slots, space, said", [
    (3, 1_200_000, f"3 free slots{NB}· 1.2{NB}MB free"),
    (None, 1_200_000, f"1.2{NB}MB free"),
    (2, None, f"2 free slots{NB}· Unknown"),
])
def test_the_apps_page_says_the_space_in_the_same_words(slots, space, said):
    from openexpress.gui.app import MainWindow

    state = types.SimpleNamespace(available_slots=slots, available_space=space)
    assert MainWindow._apps_space(state) == said


@pytest.mark.needs_display
def test_the_question_before_a_map_says_the_space_in_the_same_words(monkeypatch):
    from gi.repository import Adw

    from openexpress.gui import app

    asked = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: asked.append(self))
    win = stand_in(session=types.SimpleNamespace(), run_task=None)
    m = types.SimpleNamespace(size=1_000_000, files=["a"], eula_urls=[], installed=False,
                              name="Test Map")
    app.MainWindow._ask_install_map(win, m, 1_500_000, None, [])
    app.MainWindow._ask_install_map(win, m, 1_500_000, 2_100_000_000, [])
    bodies = [d.get_body().splitlines()[1] for d in asked]
    assert bodies == [f"Space on the device: 1.5{NB}MB (Unknown)",
                      f"Space on the device: 1.5{NB}MB (2.1{NB}GB free)"]


# --- the USB mode -------------------------------------------------------------------------


def test_mass_storage_mode_is_named_only_in_the_sentence_that_explains_it():
    """Every text for the user that names the mode: "USB drive" (and "MTP"); "mass storage
    mode" only in the explanation under the title of the device page's group."""
    pot = (Path(__file__).resolve().parents[1] / "po" / "openexpress.pot").read_text(
        encoding="utf-8")
    msgids = ["".join(re.findall(r'"(.*)"', block)) for block in
              re.findall(r'^msgid ((?:".*"\n)+)', pot, re.MULTILINE)]
    named = [m for m in msgids if "mass storage" in m.casefold()]
    assert len(named) == 1 and named[0].startswith("The device is connected in mass storage mode")


def test_devices_names_the_usb_mode_with_the_same_pair(capsys, monkeypatch, tmp_path):
    from openexpress import known_devices
    from openexpress.cli import main

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        known_devices.remember(3000000001, "fēnix 8", "006-B4532-00", "23.39", "mtp", now=1.0)
        known_devices.remember(3000000003, "Varia RTL515", "006-B3592-03", "3.36", "ums",
                               now=2.0)
        assert main(["devices"]) == 0
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
    out = capsys.readouterr().out.splitlines()
    assert " · USB drive · " in out[1] and " · MTP · " in out[2]


def _device_page(**session):
    """The device page of a connected device as the window builds it, with what `session`
    says (a drive, the account); nothing is asked."""
    from fakes import page_window
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    win, section = page_window("watch")
    device = types.SimpleNamespace(description="fēnix 8 - 47mm, Solar",
                                   software_version_str="23.39", part_number="006-B4532-00",
                                   unit_id=None)
    fields = dict(device=device, storage=None, logged_in=False, ownership_error=None,
                  account_devices=None, mass_storage=False, space=(None, None),
                  client=types.SimpleNamespace(auth=types.SimpleNamespace(display="x")))
    fields.update(session)
    win.session = types.SimpleNamespace(**fields)
    win._account, win.update_state, win.update_count, win.map_state = None, None, 0, None
    win._update_check_session = None
    for name in ("_show_device_picture", "_check_updates_quietly", "_check_maps_quietly",
                 "_show_last_sync", "_render_map_state", "select_section", "backup", "logout"):
        setattr(win, name, lambda *a, **kw: None)
    win._auto_sync_row = lambda: Adw.ActionRow(title="Sync automatically when connected")
    for name in ("show_overview", "_set_update_state", "_render_update_state",
                 "_highlight_update"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.show_overview()
    return win, section


def _texts(section):
    """(title, subtitle) of every row of the page, and the title and description of every
    group."""
    from gi.repository import Adw

    found = []
    for g in section.groups:
        found.append((g.get_title(), g.get_description() or ""))
        stack = [g]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Adw.ActionRow):
                found.append((widget.get_title(), widget.get_subtitle() or ""))
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return found


@pytest.mark.needs_display
def test_the_device_page_of_a_drive_says_usb_drive():
    from openexpress.device.gio_storage import mass_storage_text

    _win, section = _device_page(mass_storage=True)
    texts = _texts(section)
    assert ("USB drive", mass_storage_text()) in texts
    assert not any(title == "Mass storage mode" for title, _sub in texts)


def test_the_sync_button_and_the_notice_of_a_drive_say_usb_drive():
    from openexpress.gui.app import MainWindow

    win = stand_in(session=types.SimpleNamespace(mass_storage=True))
    assert MainWindow._sync_blocked(win) == "USB drive"


# --- the reason a device cannot be used with the account, only in the banner -------------


@pytest.mark.needs_display
def test_the_reason_is_only_in_the_banner():
    from openexpress.service import NotAccountDevice

    error = NotAccountDevice("This device is not registered to the Garmin account of x.")
    _win, section = _device_page(logged_in=True, ownership_error=error)
    texts = _texts(section)
    (sync,) = [i for i, (title, _sub) in enumerate(texts) if title == "Sync with Garmin Connect"]
    assert texts[sync + 1] == ("Last sync", "Not available")
    assert not any(str(error) in sub or str(error) in title for title, sub in texts)


# --- "Last seen" told against now, the full date in the tooltip ----------------------------


@pytest.mark.needs_display
def test_the_page_of_a_remembered_device_tells_last_seen_against_now(clock):
    from datetime import timedelta

    from fakes import remembered_device as _remembered
    from fakes import remembered_window as _window

    from openexpress.i18n import full_time

    known = _remembered()
    known.last_seen = (clock.current - timedelta(days=3, minutes=1)).timestamp()
    win = _window()
    win._show_remembered(known)
    label = win.remembered.seen_label
    assert label.get_label() == "Last seen 3 days ago"
    # As on its tile.
    assert label.get_tooltip_text() == (
        f"Last seen {full_time(datetime.fromtimestamp(known.last_seen))}")


# --- a version known only from the account -------------------------------------------------


def test_a_version_known_only_from_the_account_says_so():
    from openexpress.gui.start_window import account_subtitle

    fields = {"software_version": "22.26"}
    assert account_subtitle(fields) == (
        f"Software 22.26 (Garmin Connect){NB}· Not seen on this computer")


@pytest.mark.needs_display
def test_the_page_of_a_device_only_on_the_account_says_where_its_version_comes_from():
    from fakes import remembered_window as _window

    win = _window()
    win._show_account({"unitid": 3000000002, "productdisplayname": "Edge 1050",
                       "currentfirmwareversion": "33.12"})
    assert win.remembered.version_label.get_label() == "Software 33.12 (Garmin Connect)"


# --- personal records as Garmin Connect names them --------------------------------------------


def test_personal_records_in_polish_as_garmin_connect_names_them():
    from openexpress import service

    i18n.setup("pl")
    try:
        assert service.type_name("FIT_TYPE_29") == "rekordy osobiste"
    finally:
        i18n.setup("en")

