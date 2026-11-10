"""The page Device Status (gui/status_page.py) and its facts (status.py): the summary on top,
the storage by kind of file, the crashes and updates, the space to free (only told, nothing
removed), the diagnostic files and the report; the device page's row that leads to it; a
device without diagnostic data, a drive, and what is not known told apart from none. All
devices, crashes and sizes are made up."""

import types
from datetime import datetime, timedelta

import pytest

from openexpress import status
from openexpress.crashes import Crash

NOW = datetime(2026, 10, 21, 12, 0)  # the made-up clock's (conftest.NOON)
MB, GB = 1_000_000, 1_000_000_000
CATEGORIES = {"maps": 17_200 * MB, "activities": 52 * MB, "music": 410 * MB, "apps": 12 * MB,
              "courses": 305_000, "updates": 9_100_000, "logs": 61 * MB, "system": 930 * MB}


def crash(days: float, errnum="0x1a2b", **fields) -> Crash:
    return Crash(NOW - timedelta(days=days), errnum=errnum, sources={"boot log"}, **fields)


def line(days: float, software: str, **extra) -> dict:
    """A line of the device log (history.jsonl) as the program writes it."""
    when = (NOW - timedelta(days=days)).astimezone().isoformat(timespec="seconds")
    return {"v": 1, "time": when, "conn": f"made-up-{days}", "via": "mtp", "software": software,
            "categories": dict(CATEGORIES), "capacity": 31_100 * MB, "free": 12_400 * MB,
            "dumps": {"count": 2, "bytes": 18_300_000}, "crash": None, **extra}


# When the made-up watch was first seen here: a moment before its device log's first line.
FIRST_SEEN = (NOW - timedelta(days=150, minutes=1)).timestamp()


def facts(**changes) -> status.Facts:
    """The facts of a made-up watch: three crashes of the same error in the last weeks, an
    update and its first connection, older dumps, its copies on this computer."""
    crashes = [crash(2, software="21.18", error="0x003", program_counter="0x10000abc",
                     screen="2301"), crash(9), crash(21)]
    lines = [line(150, "21.05"), line(40, "21.12"), line(25, "21.18"), line(0.01, "21.18")]
    made = status.Facts(
        via="mtp", free=12_400 * MB, capacity=31_100 * MB, categories=dict(CATEGORIES),
        counted_at=NOW - timedelta(minutes=10), crashes=crashes, errors_known=True,
        dumps=[("GARMIN/Debug/dump/RAM.zip", 9 * MB, 300.0),
               ("GARMIN/Debug/dump/RAM_bak1.zip", 8 * MB, 200.0),
               ("GARMIN/Debug/dump/RAM_bak2.zip", 10 * MB, 100.0)],
        events=status.events(crashes, lines, FIRST_SEEN), hidden_logs=True,
        copies=(12, 3_100_000, NOW - timedelta(hours=3)), not_installed=[],
        has_diagnostics=True)
    for name, value in changes.items():
        setattr(made, name, value)
    return made


# --- the facts --------------------------------------------------------------------------------


@pytest.mark.parametrize(("free", "capacity", "little"), [
    (400 * MB, 32 * GB, True), (600 * MB, 32 * GB, False),  # a big device: under 500 MB
    (1_500_000, 19 * MB, True), (2 * MB, 19 * MB, False),  # a small one: under 10 %
    (None, 32 * GB, None), (MB, None, None)])  # not known
def test_little_space_is_under_ten_percent_or_500_mb_whichever_is_smaller(free, capacity,
                                                                          little):
    assert status.little_space(free, capacity) is little


def test_the_first_connection_is_when_the_device_was_first_seen():
    """Not the device log's first line (the log may begin long after): when this computer
    first saw the device (devices.json); its version only when the log began then; none
    without that time."""
    lines = [line(1, "21.18"), line(0.01, "21.18")]
    long_ago = (NOW - timedelta(days=200)).timestamp()
    (first,) = [e for e in status.events([], lines, long_ago) if e.kind == "first"]
    assert (first.at, first.software) == (datetime.fromtimestamp(long_ago), None)
    began = (NOW - timedelta(days=1, minutes=5)).timestamp()
    (first,) = [e for e in status.events([], lines, began) if e.kind == "first"]
    assert first.software == "21.18"
    assert [e.kind for e in status.events([], lines, None)] == []


def test_the_first_connection_is_read_from_the_devices_remembered(monkeypatch):
    """The time of devices.json, not the log's first line; a test drive carries a real
    device's unit ID, so it has none."""
    from openexpress import history, known_devices

    unit = 3_000_000_123
    known_devices.remember(unit, "Made-up watch", "006-B0000-00", "21.05", "mtp", now=FIRST_SEEN)
    lines = [line(150, "21.05"), line(40, "21.12")]
    monkeypatch.setattr(history, "read",
                        lambda unit_id, test_drive=False: types.SimpleNamespace(lines=lines))

    def session(test_drive: bool):
        return types.SimpleNamespace(
            device=types.SimpleNamespace(unit_id=unit), diagnostics=None, crash_history=[],
            storage=types.SimpleNamespace(test_drive=test_drive), space=None)

    (first,) = [e for e in status.read(session(False)).events if e.kind == "first"]
    assert (first.at, first.software) == (datetime.fromtimestamp(FIRST_SEEN), "21.05")
    assert [e.kind for e in status.read(session(True)).events] == ["update"]


def test_the_events_are_the_crashes_the_updates_and_the_first_connection():
    made = facts()
    kinds = [(e.kind, e.previous, e.software, e.same_before) for e in made.events]
    assert kinds == [("crash", None, "21.18", 2), ("crash", None, None, 1),
                     ("crash", None, None, 0), ("update", "21.12", "21.18", 0),
                     ("update", "21.05", "21.12", 0), ("first", None, "21.05", 0)]
    assert [e.at for e in made.events] == sorted((e.at for e in made.events), reverse=True)


def test_the_older_dumps_are_all_but_the_newest():
    older = facts().older_dumps()
    assert [path.rsplit("/", 1)[1] for path, _size, _time in older] == ["RAM_bak1.zip",
                                                                         "RAM_bak2.zip"]
    assert facts(dumps=None).older_dumps() is None  # not known
    assert facts(dumps=[]).older_dumps() == []


# --- the summary ------------------------------------------------------------------------------


def summary(made, **changes):
    from openexpress.gui import words

    for name, value in changes.items():
        setattr(made, name, value)
    return words.status_summary(made.crashes, made.errors_known, made.free, made.capacity, NOW)


def test_the_summary_of_crashes_of_the_same_error():
    look, title, line_ = summary(facts())
    assert (look, title) == ("warning", "3 Crashes in the Last 30 Days")
    assert line_ == "Last one 2 days ago\u00a0· the same error each time\u00a0· 12.4\u00a0GB free"


def test_the_summary_of_one_crash_and_of_crashes_long_ago():
    one = summary(facts(), crashes=[crash(2)])
    assert one[1:] == ("1 Crash in the Last 30 Days", "Last one 2 days ago\u00a0· 12.4\u00a0GB free")
    assert summary(facts(), crashes=[crash(40)])[:2] == ("good", "No Problems Found")


def test_the_summary_of_little_space_and_of_both():
    assert summary(facts(), crashes=[], free=400 * MB, capacity=32 * GB) == (
        "warning", "Little Free Space", "400.0\u00a0MB free of 32.0\u00a0GB")
    both = summary(facts(), free=400 * MB, capacity=32 * GB)
    assert both[1] == "3 Crashes in the Last 30 Days" and both[2].endswith("400.0\u00a0MB free")


def test_the_summary_when_the_error_logs_were_not_read():
    """Not known is not none: crashes the copies keep still count; with none of them, the
    summary says the error logs were not read, not "No Problems Found"."""
    assert summary(facts(), crashes=[], errors_known=False)[:2] == ("unknown",
                                                                    "Error Logs Not Read")
    assert summary(facts(), errors_known=False)[1] == "3 Crashes in the Last 30 Days"


# --- the page ---------------------------------------------------------------------------------


def page(made=None, collapsed=False, **session):
    """The page built from `made` in a window that is never shown; the window's choices of a
    page noted in `win.chosen`."""
    from fakes import page_window

    from openexpress.gui.app import MainWindow

    win, section = page_window("status")
    win.sections.update({key: object() for key in ("maps", "music", "apps", "courses",
                                                   "activities")})
    win.session = types.SimpleNamespace(device=None, storage=None, **session)
    win.split = types.SimpleNamespace(get_collapsed=lambda: collapsed)
    win.chosen = []
    win.select_section = win.chosen.append
    win.show_report = lambda *_a: win.chosen.append("report")
    shown = MainWindow.show_status(win, made or facts())
    section.show(shown or "content")
    return win, section


def rows(section):
    from fakes import descendants
    from gi.repository import Adw

    return [w for w in descendants(section.stack) if isinstance(w, Adw.PreferencesRow)]


def titled(section, title):
    return next(r for r in rows(section) if r.get_title() == title)


def crash_rows(section):
    """The rows of the crashes (an expander row holds a row of its own with its title)."""
    from gi.repository import Adw

    return [r for r in rows(section) if isinstance(r, Adw.ExpanderRow) and r.get_title() == "Crash"]


def labels(widget):
    from fakes import descendants
    from gi.repository import Gtk

    return [w.get_label() for w in descendants(widget) if isinstance(w, Gtk.Label)]


@pytest.mark.needs_display
def test_the_page_has_the_groups_of_the_design(clock):
    _win, section = page()
    assert section.stack.get_visible_child_name() == "content"
    assert [g.get_title() for g in section.groups] == [
        "", "Storage", "Crashes and Updates", "Space to Free", "Diagnostic Files",
        "Report a Problem"]
    summary_labels = labels(section.groups[0])
    assert "3 Crashes in the Last 30 Days" in summary_labels


@pytest.mark.needs_display
def test_the_legend_has_each_kind_with_its_size_at_the_end_of_its_line(clock):
    from openexpress.i18n import human_size

    _win, section = page()
    shown = labels(section.groups[1])
    for name, size in (("Maps", CATEGORIES["maps"]), ("Logs and Memory Dumps", CATEGORIES["logs"]),
                       ("System", CATEGORIES["system"]), ("Free", 12_400 * MB)):
        assert shown[shown.index(name) + 1] == human_size(size)


@pytest.mark.needs_display
def test_the_kinds_go_from_the_largest_to_the_smallest_with_free_last(clock):
    """In the order of the bar: the legend and Category Details alike."""
    _win, section = page()
    legend = labels(section.groups[1])
    names = ["Maps", "System", "Music and Audio", "Logs and Memory Dumps", "Activities", "Apps",
             "Updates and Satellite Data", "Courses and Locations"]
    assert [n for n in legend if n in [*names, "Free"]][:len(names) + 1] == [*names, "Free"]
    details = [r.get_title() for r in rows(section) if r.get_title() in names
               and r.get_subtitle()]
    assert details == names


@pytest.mark.needs_display
@pytest.mark.parametrize("free", [12_400 * MB, 0])
def test_a_kind_of_0_b_is_left_out_and_free_always_stays(clock, free):
    """A USB drive holds none of most kinds: neither the legend nor Category Details lists
    them. Free stays, at 0 B too."""
    from openexpress.i18n import human_size

    sizes = {**CATEGORIES, "music": 0, "apps": 0, "courses": 0, "updates": 0}
    _win, section = page(facts(categories=sizes, free=free))
    left_out = ["Music and Audio", "Apps", "Courses and Locations",
                "Updates and Satellite Data"]
    legend = labels(section.groups[1])
    assert [name for name in left_out if name in legend] == []
    assert legend[legend.index("Free") + 1] == human_size(free)
    assert [r.get_title() for r in rows(section) if r.get_title() in left_out] == []
    assert "Maps" in [r.get_title() for r in rows(section)]


@pytest.mark.needs_display
def test_the_kinds_with_a_page_lead_to_it(clock):
    win, section = page()
    details = titled(section, "Category Details")
    assert details.get_subtitle() == "Counted when the device was connected, 10 min ago"
    titled(section, "Maps").emit("activated")
    titled(section, "Music and Audio").emit("activated")
    assert win.chosen == ["maps", "music"]
    assert not titled(section, "Logs and Memory Dumps").get_activatable()


@pytest.mark.needs_display
def test_the_crashes_and_updates_with_relative_dates_and_the_error_inside(clock):
    _win, section = page()
    newest = titled(section, "Crash")
    assert newest.get_subtitle() == "2 days ago\u00a0· software 21.18\u00a0· the same error as the 2 before"
    inside = titled(section, "Error Details")
    assert inside.get_subtitle() == ("error 0x003\u00a0· at 0x10000abc\u00a0· screen 2301\u00a0· boot log "
                                     "error 0x1a2b")
    updated = [r.get_subtitle() for r in rows(section) if r.get_title() == "Software Updated"]
    assert updated == ["2026-09-26 12:00\u00a0· 21.12\u00a0→\u00a021.18", "2026-09-11 12:00\u00a0· 21.05\u00a0→\u00a021.12"]


@pytest.mark.needs_display
@pytest.mark.parametrize("collapsed", [False, True], ids=["wide", "narrow"])
def test_show_all_is_beside_the_description_or_the_last_row(clock, collapsed):
    more = [crash(d) for d in (2, 9, 21, 60, 70, 80, 90)]
    win, section = page(facts(crashes=more, events=status.events(more, [], None)),
                        collapsed=collapsed)
    group = section.groups[2]
    assert len(crash_rows(section)) == status.SHOWN_EVENTS
    from gi.repository import Gtk

    suffix = group.get_header_suffix()
    if collapsed:
        assert suffix is None
        last = titled(section, "Show All")
        last.emit("activated")
    else:
        assert isinstance(suffix, Gtk.Button) and suffix.get_label() == "Show All"
        suffix.emit("clicked")
    assert len(crash_rows(section)) == 7


def bike_computer() -> status.Facts:
    """A made-up bike computer: no crash, one event (its first connection)."""
    return facts(crashes=[], events=status.events(
        [], [line(20, "31.10")], (NOW - timedelta(days=20, minutes=1)).timestamp()))


@pytest.mark.needs_display
@pytest.mark.parametrize("device", ["bike computer", "watch"])
def test_folding_show_all_and_a_refresh_leave_gtk_nothing_to_complain_of(clock, device):
    """The device page's row Device Status and this page; wide, narrow, wide again, Show All,
    then both built anew, as Refresh or the next device opened in the window builds them:
    neither Adwaita nor GTK says a CRITICAL or a WARNING (a row removed from a group it is
    not in), and the row is in the new group only."""
    from fakes import descendants, gtk_complaints
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow
    from openexpress.gui.device_page import status_row
    from openexpress.gui.status_page import on_folded

    made = bike_computer() if device == "bike computer" else facts()
    folded = [False]

    def status_rows(group):
        return [w for w in descendants(group)
                if isinstance(w, Adw.ActionRow) and w.get_title() == "Device Status"]

    with gtk_complaints() as said:
        win, section = page(made)
        win.split = types.SimpleNamespace(get_collapsed=lambda: folded[0])
        first, again = Adw.PreferencesGroup(), Adw.PreferencesGroup()
        status_row(win, first)
        for folded[0] in (True, False):
            on_folded(win)
        suffix = win.status_history.get_header_suffix()
        if device == "watch":
            assert isinstance(suffix, Gtk.Button)
            suffix.emit("clicked")
        else:
            assert suffix is None  # one event: nothing more to show
        status_row(win, again)
        win._load("status", "Reading…", lambda: made, lambda f: MainWindow.show_status(win, f))
        for folded[0] in (True, False):
            on_folded(win)
    assert said == []
    assert (len(status_rows(first)), len(status_rows(again))) == (0, 1)


@pytest.mark.needs_display
def test_the_space_to_free_is_told_and_nothing_removes_it(clock):
    from fakes import descendants
    from gi.repository import Gtk

    _win, section = page()
    group = section.groups[3]
    older = titled(section, "Older Memory Dumps")
    assert older.get_subtitle() == "2 files; the newest dump is not counted"
    assert not [w for w in descendants(group) if isinstance(w, Gtk.Button)]


@pytest.mark.needs_display
def test_the_diagnostic_files_and_the_report(clock):
    from openexpress.settings import Settings

    settings = Settings.load()
    settings.diagnostics_copy = True  # the switch is the setting, as in the preferences
    settings.save()
    win, section = page()
    assert titled(section, "Keep Diagnostic Files").get_active()
    assert titled(section, "Memory Dumps").get_subtitle() == "3 dumps\u00a0· 27.0\u00a0MB\u00a0· encrypted"
    assert titled(section, "Crash Logs")  # the device hides them
    titled(section, "Report to openExpress")
    drive = page(facts(via="drive", hidden_logs=False))[1]
    assert "Crash Logs" not in [r.get_title() for r in rows(drive)]


@pytest.mark.needs_display
def test_not_known_is_told_apart_from_none(clock):
    _win, section = page(facts(dumps=None, crashes=[], errors_known=False,
                               events=status.events([], [line(30, "21.18")], None)))
    assert titled(section, "Memory Dumps").get_subtitle() == "Not known"
    assert titled(section, "Older Memory Dumps").get_subtitle() == "Not known"
    assert "Error Logs Not Read" in labels(section.groups[0])
    none = page(facts(dumps=[], crashes=[], events=[]))[1]
    assert titled(none, "Memory Dumps").get_subtitle() == "None"
    assert "Older Memory Dumps" not in [r.get_title() for r in rows(none)]


@pytest.mark.needs_display
def test_a_device_without_diagnostic_data_has_the_empty_page(clock):
    _win, section = page(facts(has_diagnostics=False, crashes=[], events=[], dumps=[],
                               hidden_logs=False, copies=(0, 0, None)))
    assert section.stack.get_visible_child_name() == "info"
    assert section.info.get_title() == "No Diagnostic Data"
    assert section.info.get_description() == ("This device keeps no error logs or memory dumps "
                                              "that openExpress can read")


# --- the device page ------------------------------------------------------------------------


@pytest.mark.needs_display
def test_the_device_page_leads_to_the_page_with_its_summary(clock):
    from fakes import window
    from gi.repository import Adw

    from openexpress.gui.device_page import status_row

    chosen = []
    found = types.SimpleNamespace(entries=[], errors_known=True)
    win = window(session=types.SimpleNamespace(diagnostics=found, crash_history=facts().crashes,
                                               space=(12_400 * MB, 31_100 * MB)),
                 select_section=chosen.append)
    group = Adw.PreferencesGroup()
    status_row(win, group)
    row = win.status_row
    assert (row.get_title(), row.get_subtitle()) == ("Device Status",
                                                     "3 Crashes in the Last 30 Days")
    row.emit("activated")
    assert chosen == ["status"]
