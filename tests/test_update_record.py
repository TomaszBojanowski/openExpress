"""What the last check of updates found, kept with the device (devices.json: the count, one
update's name, version and whether it is of the system, the software then): the device page
shows it from its first frame while the server's check runs and confirms or changes it; only
shown, never installed from. All devices and updates are made up."""

import time
import types

import pytest
from fakes import window as stand_in
from test_fixes_ui import page_window
from test_updates_system import _entry

from openexpress import known_devices, updates
from openexpress.connect import lower_keys
from openexpress.device.storage import LocalStorage

UNIT = 3000000001
NOW = time.time()
DAY = 86400.0


def _remembered(software="23.39"):
    known_devices.remember(UNIT, "fēnix 8 - 47mm, Solar", "006-B4532-00", software, "mtp",
                           now=NOW - 10 * DAY)


def _summary(**fields):
    values = dict(count=1, software_version="23.39", title="Map Region Grouping file",
                  version="2.31", is_system=False)
    values.update(fields)
    return known_devices.UpdateSummary(**values)


def _record(found=NOW - DAY, **fields):
    _remembered()
    known_devices.set_update_found(UNIT, True, now=found, summary=_summary(**fields))


def _answer(*entries):
    return {"updates": list(entries)}


def _component():
    entry = _entry("006-D9267-00", "Map Region Grouping file", "Firmware", "Garmin\\SQL",
                   "MapRegionGrouping.db", b"regions")
    entry["displayVersion"] = "2.31"
    return entry


# --- devices.json -----------------------------------------------------------------------------


def test_the_record_keeps_the_count_the_one_update_and_the_software_only():
    import json

    _record()
    entry = known_devices.load()[UNIT]
    assert entry.update_summary == _summary() and entry.update_found == NOW - DAY
    kept = json.loads(known_devices.path().read_text())["devices"][str(UNIT)]["tile_facts"]
    assert kept["update"]["summary"] == {"count": 1, "title": "Map Region Grouping file",
                                         "version": "2.31", "is_system": False,
                                         "software_version": "23.39"}
    known_devices.set_update_found(UNIT, True, now=NOW,
                                   summary=_summary(count=2, title=None, version=None,
                                                    is_system=None))
    kept = json.loads(known_devices.path().read_text())["devices"][str(UNIT)]["tile_facts"]
    assert kept["update"]["summary"] == {"count": 2, "software_version": "23.39"}
    known_devices.set_update_found(UNIT, False)  # a check that found none
    entry = known_devices.load()[UNIT]
    assert entry.update_found is None and entry.update_summary is None
    kept = json.loads(known_devices.path().read_text())["devices"][str(UNIT)]["tile_facts"]
    assert "found" not in kept.get("update", {}) and "summary" not in kept.get("update", {})


@pytest.mark.parametrize("damaged", [
    "not an object", [1], {"count": True, "software_version": "23.39"},
    {"count": 0, "software_version": "23.39"}, {"count": "1", "software_version": "23.39"},
    {"count": 1}, {"count": 1, "software_version": 23.39},
    {"count": 1, "software_version": "23.39", "title": 3, "version": "2.31", "is_system": False},
    {"count": 1, "software_version": "23.39", "title": "x", "version": "2.31", "is_system": "no"},
    {"count": 1, "software_version": "23.39", "title": "x"},  # without its version
])
def test_a_damaged_record_is_as_none_and_the_rest_of_the_entry_stays(damaged):
    import json

    _record()
    data = json.loads(known_devices.path().read_text())
    data["devices"][str(UNIT)]["tile_facts"]["update"]["summary"] = damaged
    known_devices.path().write_text(json.dumps(data))
    entry = known_devices.load()[UNIT]
    assert entry.update_summary is None
    assert entry.update_found == NOW - DAY and entry.description == "fēnix 8 - 47mm, Solar"


def test_a_record_is_used_while_recent_and_for_the_same_software():
    _record()
    entry = known_devices.load()[UNIT]
    assert known_devices.recorded_updates(entry, "23.39", now=NOW) == _summary()
    assert known_devices.recorded_updates(entry, "23.40", now=NOW) is None  # updated since
    assert known_devices.recorded_updates(entry, "23.39", now=NOW + 30 * DAY) is None
    _remembered()
    known_devices.set_update_found(UNIT, True, now=NOW)  # an older version: no summary
    entry = known_devices.load()[UNIT]
    assert known_devices.recorded_updates(entry, "23.39", now=NOW) is None


# --- the device page --------------------------------------------------------------------------


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _subtitle_classes(row):
    from openexpress.gui.app import row_subtitle

    return set(row_subtitle(row).get_css_classes())


def _page(device_root, answer, software="23.39"):
    """The device page of the made-up device as the window builds it; the check of updates
    waits in `later` (work, done, failed)."""
    from gi.repository import Adw

    from openexpress.gui.app import MainWindow

    win, section = page_window("watch")
    device = types.SimpleNamespace(description="fēnix 8 - 47mm, Solar",
                                   software_version_str=software, part_number="006-B4532-00",
                                   unit_id=UNIT, raw=b"<Device/>", update_files=None)
    asked = []

    def software_updates(raw, include_reinstalls=False):
        asked.append(raw)
        return lower_keys(answer)

    win.session = types.SimpleNamespace(
        device=device, storage=LocalStorage(device_root), logged_in=False, ownership_error=None,
        account_devices=None, mass_storage=False, space=(None, None),
        client=types.SimpleNamespace(software_updates=software_updates))
    win._account, win.update_state, win.update_count, win.update_only = None, None, 0, None
    win.map_state, win._update_check_session, win.updates_cache = None, None, None
    win.later, win.asked = [], asked
    win.in_background = lambda work, done, failed=None: win.later.append((work, done, failed))
    for name in ("_show_device_picture", "_check_maps_quietly", "_show_last_sync",
                 "_render_map_state", "select_section", "backup"):
        setattr(win, name, lambda *a, **kw: None)
    win._auto_sync_row = lambda: Adw.ActionRow(title="Sync automatically when connected")
    for name in ("show_overview", "_set_update_state", "_render_update_state",
                 "_highlight_update", "_check_updates_quietly"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.show_overview()
    return win, section


def _shown(win):
    """The chip under the version (shown or not), the subtitle of "Device software" and
    whether it is in the warning colour."""
    return (win.update_chip.get_visible(), win.update_row.get_subtitle(),
            "warning" in _subtitle_classes(win.update_row))


def _heights(win):
    from gi.repository import Gtk

    header = win.software_label.get_parent()
    return (header.measure(Gtk.Orientation.VERTICAL, 600)[1],
            win.update_row.measure(Gtk.Orientation.VERTICAL, 600)[1])


def _check(win, fails=None):
    (work, done, failed), = win.later
    win.later.clear()
    if fails is not None:
        failed(fails)
    else:
        done(work())


@pytest.mark.needs_display
def test_the_page_shows_what_the_last_check_found_from_its_first_frame(device_root):
    """Before the server answers: the chip and "Update available: …", in the warning colour;
    the check runs all the same."""
    _record()
    win, _section = _page(device_root, _answer(_component()))
    assert _shown(win) == (True, "Update available: Map Region Grouping file 2.31", True)
    assert len(win.later) == 1 and win.asked == []  # the check is under way


@pytest.mark.needs_display
def test_the_same_answer_moves_nothing_and_keeps_the_record(device_root):
    _record()
    win, _section = _page(device_root, _answer(_component()))
    before, chip = _heights(win), win.update_chip
    _check(win)
    assert win.asked == [b"<Device/>"]
    assert _shown(win) == (True, "Update available: Map Region Grouping file 2.31", True)
    assert _heights(win) == before and win.update_chip is chip
    entry = known_devices.load()[UNIT]
    assert entry.update_summary == _summary() and entry.update_found > NOW - DAY


@pytest.mark.needs_display
def test_another_answer_changes_the_page_and_the_record(device_root):
    """The server offers nothing now (installed on the watch meanwhile): no chip, "Software is
    up to date", and the record goes; the tile then has no chip either."""
    _record()
    win, _section = _page(device_root, _answer())
    _check(win)
    assert _shown(win) == (False, "Software is up to date", False)
    entry = known_devices.load()[UNIT]
    assert entry.update_found is None and entry.update_summary is None


@pytest.mark.needs_display
@pytest.mark.parametrize("software, found", [("23.40", NOW - DAY), ("23.39", NOW - 31 * DAY)],
                         ids=["other software", "older than 30 days"])
def test_a_record_for_other_software_or_too_old_is_not_shown(device_root, software, found):
    _record(found=found)
    win, _section = _page(device_root, _answer(_component()), software=software)
    assert _shown(win) == (False, "Checking…", False)
    assert len(win.later) == 1


@pytest.mark.needs_display
def test_a_failed_check_keeps_what_the_record_says(device_root):
    """And says when it was found (its words: test_update_states)."""
    from openexpress.connect import ApiError

    _record()
    win, _section = _page(device_root, _answer(_component()))
    _check(win, fails=ApiError("Software update check: HTTP 500", 500))
    chip, subtitle, warning = _shown(win)
    assert (chip, warning) == (True, True)
    assert subtitle.startswith("Update available: Map Region Grouping file 2.31\u00a0· checked ")


@pytest.mark.needs_display
def test_without_a_summary_the_page_says_checking_and_the_tile_keeps_its_chip(device_root):
    """An entry of an older version (update_found, no summary): "Checking…", then the answer;
    the tile's chip needs update_found alone."""
    from test_update_chips import _tile

    from openexpress.gui import start

    _remembered()
    known_devices.set_update_found(UNIT, True, now=NOW - DAY)
    win, _section = _page(device_root, _answer(_component()))
    assert _shown(win) == (False, "Checking…", False)
    _check(win)
    assert _shown(win) == (True, "Update available: Map Region Grouping file 2.31", True)
    found = known_devices.load()[UNIT].update_found
    assert [words for words, _s, _t in start.chips(_tile(update_found=found))] == [
        "Update available"]


@pytest.mark.needs_display
def test_the_record_is_never_installed_from(device_root, monkeypatch):
    """What the record says is only shown: the Updates page and its buttons have nothing to
    install until the server answers (updates_cache), the one update named is not an update
    that could be downloaded, and nothing is installed."""
    def refused(*_a, **_k):
        raise AssertionError("an update was installed")

    monkeypatch.setattr(updates, "install", refused)
    _record()
    win, _section = _page(device_root, _answer(_component()))
    assert win.update_state == "available" and win.updates_cache is None
    assert not isinstance(win.update_only, updates.SoftwareUpdate)
    _check(win)
    assert isinstance(win.update_only, updates.SoftwareUpdate)  # the server's, from now on


@pytest.mark.needs_display
def test_after_an_install_the_next_opening_shows_no_record_of_it(device_root):
    """The component copied by openExpress (the page shown again without asking the server):
    the record forgets it, and the device opened again starts from "Checking…"."""
    from test_updates_system import _Section

    from openexpress.gui.app import MainWindow

    _record()
    items = updates.mark_present(LocalStorage(device_root),
                                 updates.parse_updates(lower_keys(_answer(_component()))))
    win = stand_in(
        session=types.SimpleNamespace(device=types.SimpleNamespace(
            description="fēnix 8 - 47mm, Solar", software_version_str="23.39", unit_id=UNIT),
            mass_storage=False),
        sections={"updates": _Section()}, busy=False, _action_buttons=[],
        _update_check_session=None, _render_update_state=lambda: None,
        confirm_update=lambda u: None, eject_device=lambda s, button=None: None,
        load_section=lambda *a, **kw: None)
    for name in ("show_updates", "_show_installed", "_set_update_state", "_updates_status",
                 "_update_row", "_button"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.show_updates(items)
    assert known_devices.load()[UNIT].update_summary == _summary()
    win._show_installed(win.session, [items[0]])  # what the install's end does
    entry = known_devices.load()[UNIT]
    assert entry.update_found is None and entry.update_summary is None
    again, _section = _page(device_root, _answer())
    assert _shown(again) == (False, "Checking…", False)
