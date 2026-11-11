"""The page Device Status (gui/status_page.py) and its facts (status.py): the summary on top,
the storage by kind of file, the crashes and updates, the space to free (only told, nothing
removed), the diagnostic files and the report; the device page's row that leads to it; a
device without diagnostic data, a drive, and what is not known told apart from none. All
devices, crashes and sizes are made up."""

import json
import os
import types
from datetime import datetime, timedelta

import pytest
from conftest import DEVICE_XML
from fakes import device_session

from openexpress import diagnostics, freeing, history, status
from openexpress.crashes import Crash
from openexpress.device.storage import DeviceNotResponding, LocalStorage
from openexpress.device.xmldevice import parse

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
        free=12_400 * MB, capacity=31_100 * MB, categories=dict(CATEGORIES),
        counted_at=NOW - timedelta(minutes=10), crashes=crashes, errors_known=True,
        dumps=[("GARMIN/Debug/dump/RAM.zip", 9 * MB, 1_700_000_300.0),
               ("GARMIN/Debug/dump/RAM_bak1.zip", 8 * MB, 1_700_000_200.0),
               ("GARMIN/Debug/dump/RAM_bak2.zip", 10 * MB, 1_700_000_100.0)],
        events=status.events(crashes, lines, FIRST_SEEN), hidden_logs=True,
        copies=(12, 3_100_000, NOW - timedelta(hours=3)), not_installed=[],
        has_diagnostics=True)
    for name, value in changes.items():
        setattr(made, name, value)
    if "dumps_plan" not in changes:  # as freeing.plan makes it from the dumps
        from openexpress import freeing

        made.dumps_plan = freeing.dump_plan(made.dumps)
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
    from openexpress import freeing, history, known_devices

    monkeypatch.setattr(freeing, "plan", lambda storage, device, client, fresh=False: None)
    unit = 3_000_000_123
    known_devices.remember(unit, "Made-up watch", "006-B0000-00", "21.05", "mtp", now=FIRST_SEEN)
    lines = [line(150, "21.05"), line(40, "21.12")]
    monkeypatch.setattr(history, "read",
                        lambda unit_id, test_drive=False: types.SimpleNamespace(lines=lines))

    def session(test_drive: bool):
        return device_session(device=types.SimpleNamespace(unit_id=unit),
                              storage=types.SimpleNamespace(test_drive=test_drive))

    (first,) = [e for e in status.read(session(False)).events if e.kind == "first"]
    assert (first.at, first.software) == (datetime.fromtimestamp(FIRST_SEEN), "21.05")
    assert [e.kind for e in status.read(session(True)).events] == ["update"]


def write_log(unit: int, lines: list[dict]) -> None:
    """The device log of a made-up device, as the program writes it."""
    import json

    from openexpress import history, paths

    paths.write_private(history.path(unit), "".join(json.dumps(item) + "\n" for item in lines))


KEPT_UNIT = 3_000_000_123


def test_the_facts_of_a_device_not_connected_come_from_its_records(clock):
    """The device log's last line (storage, dumps, whether the error logs were read), its
    crashes, the updates and the first connection; the copies; nothing asked of a device.
    Without records: none."""
    from openexpress import known_devices

    assert status.read_kept(KEPT_UNIT) is None
    known_devices.remember(KEPT_UNIT, "Made-up watch", "006-B0000-00", "21.05", "mtp",
                           now=FIRST_SEEN)
    crash_at = (NOW - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    write_log(KEPT_UNIT, [line(150, "21.05"), line(1, "21.18", crash={
        "kind": "RTL Trap", "at": crash_at, "error": "0x003", "pc": "0x10000abc",
        "screen": "2301"})])
    made = status.read_kept(KEPT_UNIT)
    assert (made.connected, made.free, made.capacity, made.errors_known) == (
        False, 12_400 * MB, 31_100 * MB, True)
    assert made.categories == CATEGORIES and made.counted_at == NOW - timedelta(days=1)
    assert made.dumps_at_last == (2, 18_300_000) and made.dumps is None
    assert [(c.error, c.sources) for c in made.crashes] == [("0x003", {"device log"})]
    assert [e.kind for e in made.events] == ["update", "crash", "first"]
    assert made.not_installed == [] and made.has_diagnostics


def test_what_the_records_do_not_tell_is_not_known(clock):
    """A line without the space, the dumps or the error logs: not known, never none."""
    write_log(KEPT_UNIT, [{"v": 1, "time": (NOW - timedelta(days=1)).astimezone().isoformat(),
                           "conn": "made-up", "via": "mtp", "software": "21.18",
                           "categories": {}}])
    made = status.read_kept(KEPT_UNIT)
    assert (made.free, made.capacity, made.dumps_at_last, made.errors_known) == (
        None, None, None, False)
    assert made.categories == {} and made.crashes == []
    # The error logs not read: the page says so ("Error Logs Not Read"), as its row does; it is
    # no page of a device without diagnostic data.
    assert made.has_diagnostics is True


def test_the_events_are_the_crashes_the_updates_and_the_first_connection():
    made = facts()
    kinds = [(e.kind, e.previous, e.software, e.same_before) for e in made.events]
    assert kinds == [("crash", None, "21.18", 2), ("crash", None, None, 1),
                     ("crash", None, None, 0), ("update", "21.12", "21.18", 0),
                     ("update", "21.05", "21.12", 0), ("first", None, "21.05", 0)]
    assert [e.at for e in made.events] == sorted((e.at for e in made.events), reverse=True)


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
    win.session = device_session(device=None, storage=None, **session)
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
        "", "Storage", "Crashes and Updates", "Free Up Space", "Diagnostic Files",
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


def made_up_update(path, name="GPS software", version="6.20", size=4_100_000):
    from openexpress import updates

    return updates.SoftwareUpdate(
        part_number="006-D0000-00", name=name, version=version, severity="Recommended",
        is_primary=False, is_reinstall=False, restart_required=False, device_path=path, url="",
        md5=None, size=size, order=0, on_device=True)


def questions(in_the_window):
    """The questions of the window (confirm), recorded; answered by running an answer."""
    asked = []
    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw: asked.append(
        types.SimpleNamespace(name=name, heading=heading, body=body, answers=answers)))
    return asked


def button_of(row):
    from fakes import descendants
    from gi.repository import Gtk

    found = [w for w in descendants(row) if isinstance(w, Gtk.Button)]
    return found[0] if found else None


def working(win, monkeypatch):
    """The page's window able to run a task on a made-up open device (counted again with
    made-up numbers after a removal: status.recount): its toasts in `win.toasts`, the pages it
    reads again in `win.reloads`."""
    from openexpress import status

    monkeypatch.setattr(status, "recount", lambda device, storage, removed=None: status.Recount(
        {}, 1_000_000, 10_000_000, NOW, dumps=[]))
    win.toasts, win.reloads = [], []
    win.toast = win.toasts.append
    win.load_section = lambda key, force=False: win.reloads.append(key)
    win.session.device = types.SimpleNamespace(unit_id=1234567890)
    win.session.storage = types.SimpleNamespace(test_drive=False)
    win.progress = lambda message: None
    win.sections["status"].loaded = True  # as the window loaded it (_load)
    return win


@pytest.mark.needs_display
def test_free_up_space_offers_a_button_for_each_file_it_may_remove(clock):
    """Copy and Remove for the dumps, Remove for an update file, both destructive; an update
    file in the main folder waits to be installed: no button, no size."""
    gps = made_up_update("GARMIN/RemoteSW/006-D0000-00.GCD")
    bundle = made_up_update("Garmin/GUPDATE.GSP", "Edge Software Bundle", "33.20", 97_700_000)
    _win, section = page(facts(not_installed=[gps, bundle]))
    group = section.groups[3]
    assert (group.get_title(), group.get_description()) == (
        "Free Up Space", "Activities, maps, music, courses and settings are never removed")
    older = titled(section, "Older Memory Dumps")
    # The size in the subtitle: the end of the row is the button's, as the mockup has it.
    assert older.get_subtitle() == ("2 files; the newest dump is not counted\u00a0· "
                                    "18.0\u00a0MB")
    # Short, so that a narrow window leaves the subtitle room; its question says Copy and
    # Remove, and so do its tooltip and its name for screen readers.
    assert button_of(older).get_label() == "Remove"
    assert button_of(older).get_tooltip_text() == "Copy and Remove"
    assert button_of(older).has_css_class("destructive-action")
    assert "18.0\u00a0MB" not in labels(older)
    # An update file: its name and version the title, what became of it the subtitle.
    update = titled(section, "GPS software 6.20")
    assert update.get_subtitle() == ("The device took the file in but did not install "
                                     "it\u00a0· 4.1\u00a0MB")
    assert button_of(update).get_label() == "Remove"
    assert button_of(update).has_css_class("destructive-action")
    waiting = titled(section, "Edge Software Bundle 33.20")
    assert waiting.get_subtitle() == ("Waits to be installed; removing the file would cancel "
                                      "the update")
    assert button_of(waiting) is None
    assert "97.7\u00a0MB" not in labels(waiting)


@pytest.mark.needs_display
def test_a_dump_whose_time_is_not_known_offers_nothing(clock):
    """The newest cannot be told."""
    dumps = [("GARMIN/Debug/dump/RAM.zip", 9 * MB, 1_700_000_300.0), ("GARMIN/Debug/dump/RAM_bak1.zip",
                                                           8 * MB, 0)]
    _win, section = page(facts(dumps=dumps))
    older = titled(section, "Older Memory Dumps")
    assert older.get_subtitle() == "Not known" and button_of(older) is None


GB_DUMPS = {
    # fēnix-like: all fit in the copies
    "all": [("GARMIN/Debug/dump/RAM.zip", 9_100_000, 1_700_000_900.0),
            *[(f"GARMIN/Debug/dump/RAM_bak{n}.zip", 8_980_000, 1_700_000_900.0 - n) for n in range(1, 6)]],
    # Edge-like: two zips fit, three old logs are too large
    "some": [("GARMIN/Debug/dump/ramdump2.zip", 13_300_000, 1_700_000_900.0),
             ("GARMIN/Debug/dump/ramdump1.zip", 45_800_000, 1_700_000_800.0),
             ("GARMIN/Debug/dump/ramdump3.zip", 29_500_000, 1_700_000_799.0),
             *[(f"GARMIN/Debug/ramdump{n}.log", 202_500_000, 1_700_000_100.0 - n) for n in range(1, 4)]],
    # none fits
    "none": [("GARMIN/Debug/dump/ramdump2.zip", 13_300_000, 1_700_000_900.0),
             *[(f"GARMIN/Debug/ramdump{n}.log", 202_500_000, 1_700_000_100.0 - n) for n in range(1, 4)]],
}


@pytest.mark.needs_display
@pytest.mark.parametrize(("dumps", "body", "answer"), [
    ("all", ("5 memory dumps (44.9\u00a0MB) will be copied to this computer and then removed "
             "from the device. The newest dump stays on the device."), "Copy and Remove"),
    ("some", ("5 memory dumps (682.8\u00a0MB) will be removed from the device: 2 (75.3\u00a0MB) "
              "after a copy on this computer, 3 (607.5\u00a0MB) without a copy, as the copies "
              "would not fit in 100\u00a0MB. The newest dump stays on the device."),
     "Copy and Remove"),
    ("none", ("3 memory dumps (607.5\u00a0MB) will be removed from the device without a copy, "
              "as the copies would not fit in 100\u00a0MB. The newest dump stays on the device."),
     "Remove")])
def test_the_question_names_what_goes_and_what_is_not_copied(clock, in_the_window, dumps,
                                                              body, answer):
    asked = questions(in_the_window)
    _win, section = page(facts(dumps=GB_DUMPS[dumps]))
    button_of(titled(section, "Older Memory Dumps")).emit("clicked")
    (q,) = asked
    assert (q.heading, q.body) == ("Remove Older Memory Dumps?", body)
    assert [(a.label, a.look) for a in q.answers] == [(answer, "destructive")]


@pytest.mark.needs_display
def test_the_question_in_polish(clock, in_the_window):
    from openexpress import i18n

    asked = questions(in_the_window)
    i18n.setup("pl")
    try:
        _win, section = page(facts(dumps=GB_DUMPS["some"]))
        button_of(titled(section, "Starsze zrzuty pamięci")).emit("clicked")
    finally:
        i18n.setup("en")
    (q,) = asked
    assert q.heading == "Usunąć starsze zrzuty pamięci?"
    assert q.body == ("Z\u00a0urządzenia zostanie usuniętych 5 zrzutów pamięci (682,8\u00a0MB): "
                      "2 (75,3\u00a0MB) po skopiowaniu na ten komputer, 3 (607,5\u00a0MB) bez "
                      "kopii, bo kopie nie zmieściłyby się w\u00a0100\u00a0MB. Najnowszy zrzut "
                      "zostaje na urządzeniu.")
    assert [a.label for a in q.answers] == ["Skopiuj i\u00a0usuń"]


@pytest.mark.needs_display
def test_copy_and_remove_runs_a_task_and_reads_the_page_again(clock, in_the_window,
                                                              monkeypatch):
    from openexpress import freeing

    asked = questions(in_the_window)
    win, section = page()
    working(win, monkeypatch)
    called = []

    def remove_dumps(storage, unit_id, asked_plan, progress=None, freed=None):
        called.append((asked_plan, unit_id, storage))
        freed.removed += [(p, s) for p, s, _t in asked_plan.older]
        return freed

    monkeypatch.setattr(freeing, "remove_dumps", remove_dumps)
    button_of(titled(section, "Older Memory Dumps")).emit("clicked")
    asked[0].answers[0].run()
    (plan, unit, storage), = called
    # The question's own plan goes to the removal: the newest named, the older ones as listed.
    assert plan == freeing.dump_plan(facts().dumps)
    assert [p for p, _s, _t in plan.older] == ["GARMIN/Debug/dump/RAM_bak1.zip",
                                               "GARMIN/Debug/dump/RAM_bak2.zip"]
    assert plan.newest[0] == "GARMIN/Debug/dump/RAM.zip"
    assert (unit, storage) == (1234567890, win.session.storage)
    assert win.toasts == ["2 memory dumps removed, 18.0\u00a0MB freed"]
    assert win.reloads == ["status"]


@pytest.mark.needs_display
def test_what_the_device_did_not_remove_is_told(clock, in_the_window, monkeypatch):
    from openexpress import freeing

    asked = questions(in_the_window)
    told = []
    in_the_window("inform", lambda parent, name, heading, body, **kw: told.append(
        (heading, body)))
    win, section = page()
    working(win, monkeypatch)
    monkeypatch.setattr(freeing, "remove_dumps", lambda storage, unit_id, older, **kw:
                        freeing.Freed(removed=[("GARMIN/Debug/dump/RAM_bak2.zip", 10 * MB)],
                                      kept=[("GARMIN/Debug/dump/RAM_bak1.zip",
                                             "Cannot delete RAM_bak1.zip: the device said no")]))
    button_of(titled(section, "Older Memory Dumps")).emit("clicked")
    asked[0].answers[0].run()
    assert told == [("Not All Files Removed",
                     ("The device did not remove 1 of the files; the others are removed.\n\n"
                      "RAM_bak1.zip: Cannot delete RAM_bak1.zip: the device said no"))]
    assert win.reloads == ["status"]  # the page shows what is left


@pytest.fixture
def drive_page(clock, in_the_window, tmp_path):
    """The page of a made-up watch connected as a test drive, in a window 1000 pixels wide:
    three dumps (the newest stays) and a course, the line of its connection counted ten
    minutes before. `remove()` runs Copy and Remove for real on it, and the page is read again
    as the window reads it."""
    import os

    from conftest import DEVICE_XML

    from openexpress import diagnostics, history
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.gui.app import MainWindow
    from openexpress.gui.status_page import read_page

    class Drive(LocalStorage):
        """1 MB of capacity; its free space what its files and the system (300 kB) leave."""

        test_drive = True

        def capacity(self):
            return 1_000_000

        def free_space(self):
            files = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
            return 1_000_000 - 300_000 - files

    dump = tmp_path / "drive" / "GARMIN" / "Debug" / "dump"
    dump.mkdir(parents=True)
    (tmp_path / "drive" / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    for name, size, when in (("RAM.zip", 9_000, 300), ("RAM_bak1.zip", 8_000, 200),
                             ("RAM_bak2.zip", 10_000, 100)):
        (dump / name).write_bytes(b"d" * size)
        os.utime(dump / name, (1_700_000_000 + when, 1_700_000_000 + when))
    storage = Drive(tmp_path / "drive")
    device = parse(storage.read("GARMIN/GarminDevice.xml"))
    history.note_connection(device, storage, now=NOW - timedelta(minutes=10))
    # What the window read of it when it opened it: no error log, the dumps listed.
    at_open = diagnostics.AtOpen(entries=[], hidden_logs=False, dumps=[
        (f"GARMIN/Debug/dump/{p.name}", p.stat().st_size) for p in dump.iterdir()])
    session = device_session(device=device, storage=storage, diagnostics=at_open,
                             space=None)
    asked = questions(in_the_window)
    win, section = page(read_page(session))  # as the window reads it, the space too
    win.session = session
    win.toasts = []
    win.toast = win.toasts.append
    win.progress = lambda message: None
    win.show_status = lambda made: MainWindow.show_status(win, made)
    win.load_section = lambda key, force=False: MainWindow.load_status(win)
    section.loaded = True
    win.toplevel.set_default_size(1000, 900)
    win.toplevel.present()
    shown(0.3)

    def remove():
        button_of(titled(section, "Older Memory Dumps")).emit("clicked")
        asked[-1].answers[0].run()
        shown(0.3)

    yield types.SimpleNamespace(win=win, section=section, storage=storage, remove=remove)
    win.toplevel.destroy()


def shown(seconds: float) -> None:
    """The window drawn: its main loop run for `seconds`."""
    import time

    from gi.repository import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)


def legend_columns(section) -> int:
    """How many columns the legend of the storage has as drawn: the places its names start at."""
    from fakes import descendants
    from gi.repository import Gtk

    from openexpress.gui.status_page import kind_names

    names = set(kind_names().values())
    card = next(w for w in descendants(section.groups[1]) if w.has_css_class("oe-status-storage"))
    starts = set()
    for label in descendants(card):
        if isinstance(label, Gtk.Label) and label.get_label() in names:
            _found, bounds = label.compute_bounds(card)
            starts.add(round(bounds.get_x()))
    return len(starts)


@pytest.fixture
def removed_on_a_drive(drive_page):
    """The page after Copy and Remove ran for real on the made-up drive."""
    drive_page.remove()
    return drive_page.win, drive_page.section, drive_page.storage


@pytest.mark.needs_display
def test_the_legend_has_system_and_one_column_before_and_after_a_removal(drive_page):
    """The count after a removal gives the kinds the count of the connection gives (the rest
    of the used space as System too), and the legend stays one column at any width."""
    before = labels(drive_page.section.groups[1])
    assert "System" in before and legend_columns(drive_page.section) == 1
    drive_page.remove()
    after = labels(drive_page.section.groups[1])
    assert "System" in after and legend_columns(drive_page.section) == 1
    assert [n for n in after if n in KIND_NAMES] == [n for n in before if n in KIND_NAMES]


KIND_NAMES = ("Maps", "Activities", "Music and Audio", "Apps", "Courses and Locations",
              "Updates and Satellite Data", "Logs and Memory Dumps", "System", "Free")


@pytest.mark.needs_display
def test_after_a_removal_the_storage_is_counted_again(removed_on_a_drive):
    """Not the count of the connection: the kinds of file and the free space counted again,
    and the subtitle says when."""
    from openexpress.i18n import human_size

    _win, section, storage = removed_on_a_drive
    legend = labels(section.groups[1])
    assert legend[legend.index("Logs and Memory Dumps") + 1] == human_size(9_000)
    assert f"{human_size(1_000_000 - storage.free_space())} used" in legend
    assert titled(section, "Category Details").get_subtitle() == (
        "Counted again after a change, just now")


@pytest.mark.needs_display
def test_after_a_removal_its_copies_count_on_this_computer(removed_on_a_drive):
    win, section, _storage = removed_on_a_drive
    assert win.toasts == ["2 memory dumps removed, 18.0\u00a0kB freed"]
    assert titled(section, "Copies on This Computer").get_subtitle() == (
        "1 copy\u00a0· 18.0\u00a0kB\u00a0· the last just now")


@pytest.mark.needs_display
def test_a_removal_that_fails_reads_the_page_again(clock, in_the_window, monkeypatch):
    """A removal that fails before anything is removed: the error is said and the page shows
    the device as it is (one cut short after a removal: test_a_removal_cut_short_is_told_with_
    what_it_did)."""
    from fakes import RunNow

    from openexpress import freeing
    from openexpress.device.storage import DeviceError

    asked = questions(in_the_window)
    win, section = page()
    working(win, monkeypatch)
    win.run_task = RunNow(error="record")

    def fails(storage, unit_id, asked_plan, progress=None, freed=None):
        # The device stops before the first removal: nothing is told as removed.
        raise DeviceError("Cannot delete RAM_bak2.zip")

    monkeypatch.setattr(freeing, "remove_dumps", fails)
    button_of(titled(section, "Older Memory Dumps")).emit("clicked")
    asked[0].answers[0].run()
    assert win.run_task.errors and win.reloads == ["status"]


@pytest.mark.needs_display
def test_removing_an_update_file_offers_the_update_again(clock, in_the_window, monkeypatch):
    from openexpress import freeing

    asked = questions(in_the_window)
    gps = made_up_update("GARMIN/RemoteSW/006-D0000-00.GCD")
    win, section = page(facts(not_installed=[gps]))
    working(win, monkeypatch)
    monkeypatch.setattr(freeing, "remove_update",
                        lambda storage, u, client, device, freed=None: freeing.Freed(
                            removed=[(u.device_path, u.size)]))
    button_of(titled(section, "GPS software 6.20")).emit("clicked")
    (q,) = asked
    assert (q.heading, q.body) == (
        "Remove the Update File?",
        ("The device took in GPS software 6.20 (4.1\u00a0MB) but did not install it. The file "
         "will be removed from the device; the Updates page will offer the update again."))
    q.answers[0].run()
    assert win.toasts == ["Update file removed, 4.1\u00a0MB freed"]
    assert not gps.on_device  # the Updates page offers it again
    assert win.reloads == ["status"]


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
    drive = page(facts(hidden_logs=False))[1]
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
@pytest.mark.parametrize(("changes", "said"), [
    ({}, ("device status: warning; crashes in the last 30 days: 3; 12_400_000_000 B free of "
          "31_100_000_000 B; to free: 2 older dumps (18_000_000 B), 0 update files; update "
          "files checked")),
    ({"errors_known": False, "crashes": [], "updates_why": "no connection"},
     ("device status: unknown (error logs not read); crashes in the last 30 days: 0; "
      "12_400_000_000 B free of 31_100_000_000 B; to free: 2 older dumps (18_000_000 B), 0 "
      "update files; update files not checked (no connection)")),
    ({"dumps": None, "free": None},
     ("device status: warning; crashes in the last 30 days: 3; ? free of 31_100_000_000 B; to "
      "free: older dumps not known, 0 update files; update files checked"))])
def test_what_the_page_shows_is_said_in_the_log(clock, caplog, changes, said):
    """What the page read and how it sums it up, in one line; why it is not known when not."""
    caplog.set_level("INFO", logger="openexpress.gui.status_page")
    page(facts(**changes))
    assert [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("device status:")] == [said]


@pytest.mark.needs_display
def test_the_page_of_a_device_not_connected_has_nothing_that_needs_it(clock):
    """From the records: no Free Up Space, no copy of crash logs or dumps, no report; the dumps
    as the last connection counted them; the kinds of file lead nowhere."""
    from fakes import descendants
    from gi.repository import Gtk

    made = facts(connected=False, dumps=None, dumps_at_last=(6, 54 * MB), hidden_logs=True,
                 not_installed=[made_up_update("GARMIN/RemoteSW/006-D0000-00.GCD")])
    _win, section = page(made)
    assert [g.get_title() for g in section.groups] == [
        "", "Storage", "Crashes and Updates", "Diagnostic Files"]
    shown = {b.get_label() for b in descendants(section.stack) if isinstance(b, Gtk.Button)}
    assert not shown & {"Copy and Remove", "Remove", "Copy", "Copy Crash Logs",
                        "Copy Memory Dumps", "Report…"}
    assert titled(section, "Memory Dumps").get_subtitle() == (
        "6 dumps\u00a0· 54.0\u00a0MB\u00a0· at the last connection")
    assert titled(section, "Category Details").get_subtitle() == (
        "Counted at the last connection, 10 min ago")
    assert not titled(section, "Maps").get_activatable()


@pytest.mark.needs_display
def test_the_records_counted_again_after_a_removal_say_so(clock):
    """Not connected, from a device log whose line was put right after a removal: the subtitle
    tells that count, not the connection's."""
    _win, section = page(facts(connected=False, recounted=True, dumps=None,
                               dumps_at_last=(1, 9 * MB)))
    assert titled(section, "Category Details").get_subtitle() == (
        "Counted again after a change, 10 min ago")


@pytest.mark.needs_display
def test_the_dumps_of_a_device_not_connected_not_counted_are_not_known(clock):
    _win, section = page(facts(connected=False, dumps=None, dumps_at_last=None))
    assert titled(section, "Memory Dumps").get_subtitle() == "Not known"


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

    from openexpress.diagnostics import AtOpen, ErrorEntry
    from openexpress.gui.device_page import status_row

    chosen = []
    entries = [ErrorEntry("RTL Trap", c.at, c.error, c.program_counter, c.screen, "err_log.txt")
               for c in facts().crashes]
    found = AtOpen(entries, [], False, error_logs=1)
    win = window(session=device_session(diagnostics=found, device=None, storage=None,
                                        space=(12_400 * MB, 31_100 * MB)),
                 select_section=chosen.append)
    group = Adw.PreferencesGroup()
    status_row(win, group)
    row = win.status_row
    assert (row.get_title(), row.get_subtitle()) == ("Device Status",
                                                     "3 Crashes in the Last 30 Days")
    row.emit("activated")
    assert chosen == ["status"]


# --- a device not connected: its view leads to the page from the records ----------------------


def crash_line(days_ago_crash: float, days: float = 1) -> dict:
    """A line of the device log whose error logs held a crash `days_ago_crash` days back."""
    at = (NOW - timedelta(days=days_ago_crash)).strftime("%Y-%m-%d %H:%M:%S")
    return line(days, "21.18", crash={"kind": "RTL Trap", "at": at, "error": "0x003",
                                      "pc": "0x10000abc", "screen": "2301"})


@pytest.mark.needs_display
def test_the_view_of_a_device_not_connected_leads_to_its_status_from_the_records(clock):
    """A row Device Status under Tools, its subtitle the summary of the records and when they
    are from; it opens the page, with a banner that says so."""
    from fakes import descendants, remembered_device, remembered_window
    from gi.repository import Adw

    known = remembered_device()
    write_log(known.unit_id, [crash_line(2)])
    win = remembered_window()
    win._show_remembered(known)
    row = win.remembered.status_row
    assert row.get_visible() and row.get_activatable()
    assert row.get_subtitle() == "1 Crash in the Last 30 Days\u00a0· as of 2026-10-20 12:00"
    row.emit("activated")
    shown = win.pushed[-1]
    assert shown.get_tag() == "kept-status" and win.kept_status.page is shown
    (banner,) = [w for w in descendants(shown) if isinstance(w, Adw.Banner)]
    assert banner.get_title() == ("Device not connected\u00a0– as of 2026-10-20 12:00; connect "
                                  "it to free up space or copy its logs")


@pytest.mark.needs_display
def test_a_device_without_records_has_no_status_row(clock):
    from fakes import remembered_device, remembered_window

    win = remembered_window()
    win._show_remembered(remembered_device())
    assert not win.remembered.status_row.get_visible()


def test_the_status_of_a_device_not_connected_counts_as_its_view_for_navigation():
    """Plugged in while its page is shown, the device's view takes its place as from the
    remembered view (navigation): the page is not the device view."""
    from fakes import window

    from openexpress.gui.detection import visible_view
    from openexpress.gui.navigation import Page

    kept = object()
    win = window(start=types.SimpleNamespace(page=object()),
                 remembered=types.SimpleNamespace(page=object()),
                 navigation=types.SimpleNamespace(get_visible_page=lambda: kept),
                 _remembered_unit=1234567890, _remembered_from_account=False)
    win.kept_status = types.SimpleNamespace(page=kept)
    view = visible_view(win)
    assert (view.page, view.shown_unit_id) == (Page.REMEMBERED, 1234567890)


# --- the reading of the error logs at the opening, and the page ------------------------------


@pytest.mark.needs_display
def test_the_reading_is_in_the_session_before_anyone_learns_it_is_done(monkeypatch):
    """Whoever waits for the reading (a page, a task that writes) finds its results in the
    session the moment it learns that the reading is done."""
    import threading
    import time

    from fakes import window
    from gi.repository import GLib

    from openexpress import diagnostics
    from openexpress.diagnostics import AtOpen, ErrorEntry
    from openexpress.gui import device_page
    from openexpress.gui.window_tasks import TaskRunner

    crash_entry = ErrorEntry("RTL Trap", datetime(2026, 10, 20, 10, 0), "0x003")

    def at_open(device, storage, now=None):
        time.sleep(0.01)
        return AtOpen(entries=[crash_entry], dumps=[], hidden_logs=False)

    monkeypatch.setattr(diagnostics, "at_open", at_open)
    for n in range(20):
        s = device_session(device=types.SimpleNamespace(unit_id=3_000_000_000 + n),
                           storage=None, space=None)
        win = window(session=s, in_background=TaskRunner.in_background)
        device_page.read_diagnostics(win)
        seen = []

        def wait(s=s, seen=seen):
            s.diagnostics_reading.wait(5)
            seen.append(getattr(s, "diagnostics", None))

        waiter = threading.Thread(target=wait)
        waiter.start()
        end = time.monotonic() + 5
        while waiter.is_alive() and time.monotonic() < end:
            GLib.MainContext.default().iteration(False)
        waiter.join()
        assert seen and seen[0] is not None and seen[0].errors_known


@pytest.mark.needs_display
def test_the_page_waits_for_the_reading_without_a_task_then_reads(clock):
    """Chosen while the error logs are read (the first seconds of an opening, or Refresh on the
    page): the window stays free, the page says it reads; once the reading is done, the page is
    read with the error logs."""
    import threading

    from openexpress.diagnostics import AtOpen, ErrorEntry
    from openexpress.gui.app import MainWindow
    from openexpress.gui.device_page import status_changed

    win, section = page()
    win.session.diagnostics = None
    win.session.diagnostics_reading = threading.Event()
    win.session.device = types.SimpleNamespace(unit_id=None)
    win.load_section = lambda key, force=False: MainWindow.load_status(win)
    win.show_status = types.MethodType(MainWindow.show_status, win)
    MainWindow.load_status(win)
    assert win.run_task.calls == [] and not win.busy
    assert win.session.page_waits and section.stack.get_visible_child_name() == "loading"
    entry_ = ErrorEntry("RTL Trap", datetime(2026, 10, 20, 10, 0), "0x003")
    win.session.diagnostics = AtOpen(entries=[entry_], dumps=[],
                                     hidden_logs=False)
    win.session.diagnostics_reading.set()
    status_changed(win)
    assert len(win.run_task.calls) == 1 and not win.session.page_waits
    assert win.status_facts.errors_known and len(win.status_facts.crashes) == 1


@pytest.mark.needs_display
def test_after_copy_crash_logs_the_row_and_the_page_count_the_boot_logs_crashes(clock,
                                                                                monkeypatch):
    from fakes import RunNow

    from openexpress import diagnostics
    from openexpress.diagnostics import AtOpen, Copied
    from openexpress.gui import status_page

    unit = 3_000_000_321
    s = device_session(device=types.SimpleNamespace(unit_id=unit), storage=None,
                       space=None, client=None,
                       diagnostics=AtOpen(entries=[], dumps=[],
                                          hidden_logs=True))

    def copy_crash_logs(device, storage, progress=None):
        where = diagnostics.folder(unit)
        files, made = {}, Copied()
        boot = b"[10/20/26 09:15:00 123]: RTL Trap detected, errnum: 0x6e5d8003\n"
        diagnostics._keep_file(where, files, made, diagnostics.CRASH_LOGS[0], boot, None)
        diagnostics._save(where, "crash logs", files, {}, datetime(2026, 10, 21, 11, 0), "21.18")
        made.saved = True
        return made

    monkeypatch.setattr(diagnostics, "copy_crash_logs", copy_crash_logs)
    reloads, toasts = [], []
    from fakes import window

    win = window(session=s, run_task=RunNow(), toast=toasts.append, progress=None,
                 sections={"status": types.SimpleNamespace(loaded=True)},
                 load_section=lambda key, force=False: reloads.append(key))
    status_page.copy_crash_logs(win)
    (crash,) = status.told(s).crashes
    assert crash.errnum == "0x6e5d8003"
    assert reloads == ["status"]


@pytest.mark.needs_display
def test_after_another_write_the_page_is_read_again(monkeypatch, in_the_window):
    """A map, a song or an app written in the same session: the space read again, then the page
    Device Status, when shown, with it."""
    from fakes import window

    from openexpress.gui import window_tasks
    from openexpress.gui.window_tasks import TaskRunner

    monkeypatch.setattr(TaskRunner, "in_background",
                        staticmethod(lambda work, done, failed=None: done(work())))
    in_the_window("read_space", lambda storage, capacity: (5, 10))
    in_the_window("render_space", lambda win: None)
    reloads = []
    s = device_session(space=(9, 10), storage=None)
    win = window(session=s, sections={"status": types.SimpleNamespace(loaded=True)},
                 load_section=lambda key, force=False: reloads.append(key))
    window_tasks.refresh_space(win, s)
    assert s.space == (5, 10) and reloads == ["status"]


@pytest.mark.needs_display
def test_after_another_write_the_device_is_counted_again(monkeypatch, in_the_window):
    """What a task wrote (writes_to_device): the kinds of file, the space and the dumps counted
    again in the background, then the page read with them: its kinds and its free space of one
    count."""
    from fakes import window

    from openexpress import status
    from openexpress.gui import window_tasks
    from openexpress.gui.window_tasks import TaskRunner

    monkeypatch.setattr(TaskRunner, "in_background",
                        staticmethod(lambda work, done, failed=None: done(work())))
    counted = status.Recount({"maps": 4}, 5, 10, datetime(2026, 10, 30, 12, 0), dumps=[])
    monkeypatch.setattr(status, "recount", lambda device, storage: counted)
    in_the_window("render_space", lambda win: None)
    reloads = []
    s = device_session(space=(9, 10), storage=None, device=None, diagnostics=None)
    win = window(session=s, sections={"status": types.SimpleNamespace(loaded=True)},
                 load_section=lambda key, force=False: reloads.append(key))
    window_tasks.count_again(win, s)
    assert s.recount is counted and s.space == (5, 10) and reloads == ["status"]


@pytest.mark.needs_display
def test_a_removal_cut_short_is_told_with_what_it_did(monkeypatch):
    """What comes after the first removal (the device stopping, a count that fails) never loses
    what was done; before it, the failure goes on as it is."""
    from openexpress import freeing, status
    from openexpress.device.storage import DeviceError, DeviceNotResponding
    from openexpress.gui import status_page

    s = device_session(device=object(), storage=object())

    def full(device, storage, removed=None):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(status, "recount", full)
    freed, counted = status_page._then_counted(s, lambda freed: freeing.Freed(
        removed=[("GARMIN/Debug/dump/a.zip", 1)], copy_failed=["GARMIN/Debug/dump/b.zip"]))()
    assert freed.removed and freed.copy_failed and counted is None
    assert freed.stopped == "the device could not be read after the removal"

    def stops(freed):
        freed.removed.append(("GARMIN/Debug/dump/a.zip", 1))
        raise DeviceNotResponding("stopped")

    freed, _counted = status_page._then_counted(s, stops)()
    assert freed.removed and freed.stopped == "the device stopped responding"

    def refused(freed):
        raise DeviceError("Cannot delete a.zip")

    with pytest.raises(DeviceError):
        status_page._then_counted(s, refused)()
    with pytest.raises(freeing.ListChanged):
        status_page._then_counted(s, lambda freed: (_ for _ in ()).throw(
            freeing.ListChanged("changed")))()


@pytest.mark.needs_display
def test_a_removal_of_a_list_that_changed_says_why_and_reads_the_page(clock, in_the_window,
                                                                      monkeypatch):
    from fakes import RunNow

    from openexpress import freeing

    asked = questions(in_the_window)
    win, section = page()
    working(win, monkeypatch)
    win.run_task = RunNow(error="record")

    def changed(storage, unit_id, plan, progress=None, freed=None):
        raise freeing.ListChanged("The files on the device changed since the question; nothing "
                                  "was removed. Look at the list again.")

    monkeypatch.setattr(freeing, "remove_dumps", changed)
    button_of(titled(section, "Older Memory Dumps")).emit("clicked")
    asked[0].answers[0].run()
    assert win.reloads == ["status"] and win.toasts == []
    assert any("nothing was removed" in e for e in win.run_task.errors)


# --- the page of a device not connected -------------------------------------------------------


def _write_kept_log(unit, now, versions=("21.18",)):
    from openexpress import history, paths

    at = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    lines = [{"v": 1, "time": (now - timedelta(days=len(versions) - n)).astimezone().isoformat(
                  timespec="seconds"),
              "conn": f"made-up-{n}", "via": "mtp", "software": version,
              "categories": {"maps": 1000}, "capacity": 10_000, "free": 5_000,
              "dumps": {"count": 1, "bytes": 10},
              "crash": {"kind": "RTL Trap", "at": at, "error": "0x003", "pc": "0x1",
                        "screen": "1"}} for n, version in enumerate(versions)]
    paths.write_private(history.path(unit), "".join(json.dumps(x) + "\n" for x in lines))


@pytest.mark.needs_display
def test_the_page_from_the_records_stays_the_view_of_its_device_when_redrawn(clock):
    """The account's list comes (or Refresh) while that page is shown: the view under it is
    drawn again, and the page is still that device's view: plugging the device in opens it."""
    from fakes import remembered_device, remembered_window

    from openexpress.gui.detection import visible_view
    from openexpress.gui.navigation import Cause, Page, Seen, ShowDevice, navigate_after

    known = remembered_device()
    _write_kept_log(known.unit_id, clock.current)
    win = remembered_window()
    win.start = types.SimpleNamespace(page=object())
    win.navigation.get_visible_page = lambda: win.pushed[-1]
    win._show_remembered(known)
    win.remembered.status_row.emit("activated")
    kept_page = win.pushed[-1]
    tile = types.SimpleNamespace(image_part_number="", image_url=None, category="watch",
                                 description="Test watch", image_source=None)
    win._start_entries = [(tile, known)]
    win._redraw_remembered()
    assert win.kept_status.page is kept_page
    view = visible_view(win)
    plugged = Seen(frozenset({"a"}), "a", frozenset({known.unit_id}), known.unit_id)
    assert view.page is Page.REMEMBERED and win._on_remembered_page()
    assert isinstance(navigate_after(Cause.PLUGGED, view, Seen(), plugged), ShowDevice)


@pytest.mark.needs_display
@pytest.mark.parametrize("narrow", [False, True], ids=["wide", "narrow"])
def test_the_page_from_the_records_is_released_once_closed(clock, narrow):
    """With Show All (more than five crashes and updates), as without."""
    import gc

    from fakes import remembered_device, remembered_window
    from gi.repository import GLib

    known = remembered_device()
    _write_kept_log(known.unit_id, clock.current, [f"1.{n}" for n in range(9)])
    win = remembered_window()
    win.split = types.SimpleNamespace(get_collapsed=lambda: narrow)
    win._action_buttons = []
    win._show_remembered(known)
    win.remembered.status_row.emit("activated")
    page_ = win.pushed.pop()
    assert len(win.kept_status.status_history_rows) >= 5
    gone = []
    page_.weak_ref(lambda: gone.append(True))
    del page_
    win.kept_status = None  # another device's view
    win._action_buttons.clear()
    for _ in range(3):
        gc.collect()
        while GLib.main_context_default().iteration(False):
            pass
    assert gone == [True]


@pytest.mark.needs_display
def test_a_removal_that_ends_after_the_device_went_is_told_and_reads_nothing(clock,
                                                                            in_the_window):
    """Unplugged, or another device opened, before the removal's result came: what was removed
    is still told (one message, with what the device refused), and no page is read again for
    the device that went."""
    from openexpress import freeing
    from openexpress.gui import status_page

    told = []
    in_the_window("inform", lambda *a, **kw: told.append(a))
    win, _section = page()
    win.toasts, win.reloads = [], []
    win.toast = win.toasts.append
    win.load_section = lambda key, force=False: win.reloads.append(key)
    old = win.session
    win.session = device_session(device=None, storage=None)
    status_page._freed(win, old, freeing.Freed(removed=[("GARMIN/Debug/dump/a.zip", 1)],
                                               kept=[("GARMIN/Debug/dump/b.zip", "kept")]),
                       "1 removed")
    assert win.toasts == [] and win.reloads == []
    (heading, body) = next((a[2], a[3]) for a in told)
    assert heading == "Removal Not Finished"
    assert body.startswith("1 removed. Then the device was unplugged.")
    assert "b.zip: kept" in body


@pytest.mark.needs_display
def test_the_records_of_a_device_read_after_another_was_shown_are_left(clock):
    from fakes import remembered_device, remembered_window

    first, second = remembered_device(), remembered_device(part="006-B0000-01")
    second.unit_id = first.unit_id + 1
    _write_kept_log(first.unit_id, clock.current)
    win = remembered_window()
    later = []
    win.in_background = lambda work, done, failed=None: later.append((work, done))
    win._show_remembered(first)
    win._remembered_entry = second  # another device's view meanwhile
    for work, done in later:
        done(work())
    assert win.kept_status is None
    assert not win.remembered.status_row.get_visible()


# --- after a removal and after the reading: the window's other parts --------------------------


@pytest.mark.needs_display
def test_after_a_removal_the_row_device_storage_tells_the_space_of_now(clock, in_the_window,
                                                                       monkeypatch):
    from openexpress import freeing, status
    from openexpress.gui import status_page

    drawn, read_again = [], []
    in_the_window("render_space", lambda win: drawn.append(win.session.space))
    in_the_window("refresh_space", lambda win, s: read_again.append(s))
    win, _section = page()
    working(win, monkeypatch)
    drawn.clear()  # the page drew it when it was shown
    s = win.session
    counted = status.Recount({}, 9_000, 10_000, clock.current, dumps=[])
    status_page._freed(win, s, freeing.Freed(removed=[("GARMIN/Debug/dump/a.zip", 1)]), "1 removed",
                       counted)
    assert drawn == [(9_000, 10_000)] and read_again == []
    status_page._freed(win, s, freeing.Freed(removed=[("GARMIN/Debug/dump/b.zip", 1)]), "1 removed")
    assert read_again == [s]  # not counted again: the free space read again at least


@pytest.mark.needs_display
def test_a_page_that_waited_is_not_read_for_another_device(clock):
    import threading

    from openexpress.diagnostics import AtOpen
    from openexpress.gui.app import MainWindow
    from openexpress.gui.device_page import status_changed

    win, section = page()
    win.session = device_session(device=types.SimpleNamespace(unit_id=None),
                                 storage=None, diagnostics=None,
                                 diagnostics_reading=threading.Event())
    MainWindow.load_status(win)
    assert win.session.page_waits
    done = threading.Event()
    done.set()
    win.session = device_session(  # unplugged; another device, its page never chosen
        device=types.SimpleNamespace(unit_id=None), storage=None, diagnostics_reading=done,
        diagnostics=AtOpen(entries=[], dumps=[], hidden_logs=False))
    section.loaded = False
    loads = []
    win.load_section = lambda key, force=False: loads.append(key)
    status_changed(win)
    assert loads == []


@pytest.mark.needs_display
def test_the_page_not_shown_is_read_when_chosen_not_after_each_change(clock):
    from openexpress.gui.device_page import status_changed

    win, section = page()
    section.loaded = True
    loads = []
    win.load_section = lambda key, force=False: loads.append(key)
    win.pages = types.SimpleNamespace(get_visible_child_name=lambda: "maps")
    status_changed(win)
    assert loads == [] and section.loaded is False
    win.pages = types.SimpleNamespace(get_visible_child_name=lambda: "status")
    section.loaded = True
    status_changed(win)
    assert loads == ["status"]


# --- a question answered late, a count begun before a removal, the words of a failed copy -----


class _Watch:
    """Builds a made-up watch over MTP on this computer's disk, at a place on USB, with three
    dumps, and a session of it as the window leaves it once the error logs are read."""

    @staticmethod
    def lay_out(root):
        import os

        from conftest import DEVICE_XML

        dump = root / "GARMIN" / "Debug" / "dump"
        dump.mkdir(parents=True)
        (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
        for name, size, when in (("RAM.zip", 900, 300), ("RAM_bak1.zip", 800, 200),
                                 ("RAM_bak2.zip", 1_000, 100)):
            (dump / name).write_bytes(b"d" * size)
            os.utime(dump / name, (1_700_000_000 + when, 1_700_000_000 + when))

    @staticmethod
    def storage(root, address=5, base=None):
        from openexpress.device.storage import LocalStorage

        class Watch(base or LocalStorage):
            mass_storage = False
            device_uri = "mtp://made-up/"

            def capacity(self):
                return 10_000_000

            def free_space(self):
                return 10_000_000 - sum(p.stat().st_size for p in self.root.rglob("*")
                                        if p.is_file())

            def usb_blocked(self):
                return False

            def exclusive_ptp(self):
                return None

        st = Watch(root)
        st.usb = types.SimpleNamespace(bus=1, address=address, serial="MADE-UP")
        return st

    @staticmethod
    def session(st):
        from openexpress import diagnostics, history
        from openexpress.device.xmldevice import parse

        dev = parse(st.read("GARMIN/GarminDevice.xml"))
        found = diagnostics.AtOpen(entries=[], hidden_logs=False, dumps=[
            (p, e.size) for p, e in diagnostics.dump_files(st)])
        history.note_connection(dev, st, found=found)
        return device_session(device=dev, storage=st, diagnostics=found,
                              diagnostics_reading=None, space=(st.free_space(),
                                                               st.capacity()),
                              client=None, recount=None)

    @staticmethod
    def window(s):
        from fakes import RunNow, window

        win = window(session=s, run_task=RunNow(error="record"),
                     sections={"status": types.SimpleNamespace(loaded=True)})
        win.toasts, win.reloads = [], []
        win.toast = win.toasts.append
        win.load_section = lambda key, force=False: win.reloads.append(key)
        win.progress = lambda message: None
        return win


@pytest.mark.needs_display
def test_a_question_answered_after_the_device_came_back_removes_nothing(clock, in_the_window,
                                                                        tmp_path):
    """The device restarted, or was plugged in again, while the question was open: the answer
    removes nothing, says why, and the page of the device as it is now is read."""
    from openexpress import freeing
    from openexpress.gui import status_page

    asked = questions(in_the_window)
    told = []
    in_the_window("inform", lambda parent, name, heading, body, **kw: told.append(heading))
    root = tmp_path / "watch"
    _Watch.lay_out(root)
    s1 = _Watch.session(_Watch.storage(root, address=5))
    win = _Watch.window(s1)
    status_page.confirm_dumps(win, freeing.dump_plan(freeing.plan(s1.storage, s1.device,
                                                                  None).dumps))
    win.session = _Watch.session(_Watch.storage(root, address=6))
    asked[0].answers[0].run()
    assert len(list((root / "GARMIN/Debug/dump").iterdir())) == 3
    assert told == ["Nothing Removed"] and win.reloads == ["status"]


@pytest.mark.needs_display
def test_a_question_answered_after_the_device_was_unplugged_removes_nothing(clock, in_the_window,
                                                                           tmp_path):
    """Unplugged while the question was open: the answer removes nothing and says why."""
    from openexpress import freeing
    from openexpress.gui import status_page

    asked = questions(in_the_window)
    told = []
    in_the_window("inform", lambda parent, name, heading, body, **kw: told.append(heading))
    root = tmp_path / "watch"
    _Watch.lay_out(root)
    s = _Watch.session(_Watch.storage(root, address=5))
    win = _Watch.window(s)
    status_page.confirm_dumps(win, freeing.dump_plan(freeing.plan(s.storage, s.device,
                                                                  None).dumps))
    win.session = None
    asked[0].answers[0].run()
    assert len(list((root / "GARMIN/Debug/dump").iterdir())) == 3
    assert told == ["Nothing Removed"]


@pytest.mark.needs_display
def test_a_count_begun_before_a_removal_never_undoes_its_count(clock, in_the_window, tmp_path,
                                                               monkeypatch):
    """A count after another write lists the device, then a removal goes to its end and counts
    it again, then the first count ends: the device log's line and the session keep the
    removal's count."""
    import threading

    from openexpress import freeing, history
    from openexpress.device.storage import LocalStorage
    from openexpress.gui import status_page, window_tasks
    from openexpress.gui.window_tasks import TaskRunner

    asked = questions(in_the_window)
    in_the_window("render_space", lambda win: None)
    root = tmp_path / "watch"
    _Watch.lay_out(root)
    listed, go = threading.Event(), threading.Event()
    seen = []

    class Slow(LocalStorage):
        """The count stops after its own listing of the dumps (its second of that folder,
        after the kinds of file)."""

        def _list(self, real_path):
            out = super()._list(real_path)
            if threading.current_thread().name == "count" and real_path.endswith("Debug/dump"):
                seen.append(real_path)
                if len(seen) == 2:
                    listed.set()
                    go.wait(10)
            return out

    st = _Watch.storage(root, base=Slow)
    s = _Watch.session(st)
    win = _Watch.window(s)
    threads = []

    def in_background(fn, on_done, on_error=None):
        def run():
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001 - as the window's runner
                if on_error:
                    on_error(e)
                return
            on_done(result)

        threads.append(threading.Thread(target=run, name="count"))
        threads[-1].start()

    monkeypatch.setattr(TaskRunner, "in_background", staticmethod(in_background))
    window_tasks.count_again(win, s)
    assert listed.wait(5)
    clock.current += timedelta(seconds=5)
    status_page.confirm_dumps(win, freeing.dump_plan(freeing.plan(st, s.device, None).dumps))
    asked[0].answers[0].run()
    assert sorted(p.name for p in (root / "GARMIN/Debug/dump").iterdir()) == ["RAM.zip"]
    go.set()
    for t in threads:
        t.join(10)
    assert history.read(1234567890).lines[-1]["dumps"]["count"] == 1
    assert len(s.recount.dumps) == 1


def test_after_a_write_the_device_is_counted_once_and_tasks_wait_for_it(monkeypatch):
    from openexpress.gui import window_tasks
    from openexpress.gui.window_tasks import TaskRunner

    later = []
    monkeypatch.setattr(TaskRunner, "in_background",
                        staticmethod(lambda work, done, failed=None: later.append(work)))
    s = device_session(device=None, storage=None)
    window_tasks.count_again(types.SimpleNamespace(session=s), s)
    assert not s.counting.is_set()
    counted = status.Recount({"maps": 4}, 5, 10, NOW, dumps=[])
    monkeypatch.setattr(status, "recount", lambda device, storage: counted)
    later[0]()
    # In the session before a task that waited for it goes on.
    assert s.counting.is_set() and s.recount is counted


@pytest.mark.needs_display
def test_a_copy_that_failed_is_not_said_to_be_the_device_s_refusal(clock, in_the_window):
    from openexpress import freeing
    from openexpress.gui import status_page

    told = []
    in_the_window("inform", lambda parent, name, heading, body, **kw: told.append(
        (heading, body)))
    s = device_session(device=None, storage=None)
    win = _Watch.window(s)
    status_page._freed(win, s, freeing.Freed(copy_failed=["GARMIN/Debug/dump/RAM_bak1.zip"]),
                       "0 memory dumps removed, 0 B freed")
    assert told == [("Not All Files Removed",
                     ("1 memory dump was not removed, as its copy on this computer failed.\n\n"
                      "RAM_bak1.zip"))]


@pytest.mark.needs_display
def test_the_page_from_the_records_is_released_once_closed_with_back(clock):
    import gc

    from fakes import remembered_device, remembered_window
    from gi.repository import GLib

    from openexpress.gui.app import MainWindow

    known = remembered_device()
    _write_kept_log(known.unit_id, clock.current, [f"1.{n}" for n in range(9)])
    win = remembered_window()
    win.split = types.SimpleNamespace(get_collapsed=lambda: False)
    win._action_buttons = []
    win._show_remembered(known)
    win.start = types.SimpleNamespace(page=None)
    win._render_start = lambda: None
    win.remembered.status_row.emit("activated")
    page_ = win.pushed.pop()
    MainWindow._on_popped(win, None, page_)  # Back: the remembered view shown again
    assert win.kept_status is not None
    gone = []
    page_.weak_ref(lambda: gone.append(True))
    del page_
    win._action_buttons.clear()
    for _ in range(3):
        gc.collect()
        while GLib.main_context_default().iteration(False):
            pass
    assert gone == [True]


@pytest.mark.needs_display
def test_a_page_that_waited_is_read_when_chosen_not_while_another_is_shown(clock):
    import threading

    from fakes import page_window

    from openexpress import diagnostics
    from openexpress.gui.app import MainWindow
    from openexpress.gui.device_page import status_changed

    win, _section = page_window("status")
    s = device_session(device=types.SimpleNamespace(unit_id=None), storage=None,
                       diagnostics=None, diagnostics_reading=threading.Event())
    win.session = s
    MainWindow.load_status(win)
    assert s.page_waits
    win.pages = types.SimpleNamespace(get_visible_child_name=lambda: "maps")
    s.diagnostics = diagnostics.AtOpen(entries=[], dumps=[], hidden_logs=False)
    s.diagnostics_reading.set()
    loads = []
    win.load_section = lambda key, force=False: loads.append(key)
    status_changed(win)
    assert loads == [] and win.sections["status"].loaded is False


@pytest.mark.needs_display
def test_the_page_chosen_during_another_task_shows_what_needs_no_device(clock, monkeypatch):
    """Another task (a sync) runs: the page shows at once what needs nothing of the
    device, under a banner that says what waits, and asks the device nothing; once the task ends, the
    page is read in full, without the banner."""
    import threading
    import time

    from fakes import page_window
    from gi.repository import GLib

    from openexpress import diagnostics, freeing
    from openexpress.gui.app import MainWindow

    planned = []
    monkeypatch.setattr(freeing, "plan", lambda storage, device, client: planned.append(1) or
                        freeing.Plan(dumps=None, dumps_plan=None, updates=[], waiting=[],
                                     why=None))
    win, section = page_window("status")
    reading = threading.Event()
    reading.set()  # the error logs were read at the opening, before the task began
    win.session = device_session(
        device=types.SimpleNamespace(unit_id=None), storage=object(), space=(9 * MB, 10 * MB),
        diagnostics=diagnostics.AtOpen(entries=[], dumps=[], hidden_logs=False, error_logs=1),
        diagnostics_reading=reading)
    win.show_status = types.MethodType(MainWindow.show_status, win)
    win.load_section = lambda key, force=False: MainWindow.load_status(win)
    win.busy = True  # the sync's task
    MainWindow.load_status(win)
    groups = [g.get_title() for g in section.groups]
    assert section.stack.get_visible_child_name() == "content" and section.loaded
    assert (win.run_task.calls, planned) == ([], [])
    assert "Free Up Space" not in groups and "Storage" in groups
    assert section.banner.get_revealed()
    assert section.banner.get_title() == ("Another task is running\u00a0– free up space or "
                                          "copy diagnostic files when it ends")

    win.busy = False  # the task ended
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not planned:
        GLib.main_context_default().iteration(False)
        time.sleep(0.01)
    assert planned == [1] and len(win.run_task.calls) == 1
    assert "Free Up Space" in [g.get_title() for g in section.groups]
    assert not section.banner.get_revealed()


# --- a removal the window cannot count again, the count after a write, the question --------


UNIT = 1234567890  # the Id of DEVICE_XML
# A type of the made-up watch for the files of its updates, so that they count as updates.
UPDATE_TYPE = """
  <DataType><Name>UpdateMadeUp</Name>
   <File><Location><Path>Garmin/RemoteSW</Path><FileExtension>GCD</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
"""


class StopsAfterARemoval(LocalStorage):
    """A made-up watch of 1 MB that stops responding once a file was removed from it: it cannot
    be counted again."""

    removed = False

    def capacity(self):
        return 1_000_000

    def free_space(self):
        if self.removed:
            raise DeviceNotResponding("Cannot read the free space: no answer")
        return 1_000_000 - sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def _delete(self, real_path):
        super()._delete(real_path)
        self.removed = True


def stopping_watch(tmp_path, files):
    """The watch with `files` (path: size, made at times one second apart), its connection
    written to its device log."""
    from openexpress import history

    root = tmp_path / "device"
    (root / "GARMIN").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(
        DEVICE_XML.replace("</MassStorageMode>", UPDATE_TYPE + "</MassStorageMode>"))
    for n, (path, size) in enumerate(files.items()):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"d" * size)
        os.utime(root / path, (1_700_000_000 + n, 1_700_000_000 + n))
    st = StopsAfterARemoval(root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    assert history.note_connection(dev, st)
    return st, dev


def removing_window(st, dev, in_the_window):
    """A stand-in window with the watch open: its tasks run at once, its questions answered with
    their first answer, what it tells afterwards and the free space it reads again left out."""
    from fakes import RunNow, window

    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw:
                  answers[0].run())
    in_the_window("inform", lambda *a, **kw: None)
    in_the_window("refresh_space", lambda win, s: None)
    session = device_session(device=dev, storage=st, diagnostics=None, space=None,
                             client=None)
    return window(session=session, run_task=RunNow(), toast=lambda text: None, progress=None)


def task_window(session, monkeypatch, **more):
    """A stand-in window that runs a task as the window does (run_task), its threads and main
    loop at once (fakes.tasks_at_once)."""
    from fakes import tasks_at_once, window, window_methods

    tasks_at_once(monkeypatch)
    win = window(session=session, _run_pending_refresh=lambda: None,
                 set_busy=lambda busy, *a, **kw: setattr(win, "busy", busy), **more)
    return window_methods(win, ["run_task"])


@pytest.mark.needs_display
def test_dumps_removed_from_a_device_that_then_stops_are_taken_off_its_records(tmp_path, clock,
                                                                             in_the_window):
    """Not counted again after Copy and Remove (the device stopped): its device log is put right
    by what was removed, so that its page from the records does not tell the dumps that are
    gone."""
    from openexpress import diagnostics, freeing, status
    from openexpress.gui import status_page

    st, dev = stopping_watch(tmp_path, {"GARMIN/Debug/dump/RAM_bak2.zip": 10_000,
                                        "GARMIN/Debug/dump/RAM_bak1.zip": 8_000,
                                        "GARMIN/Debug/dump/RAM.zip": 9_000})
    win = removing_window(st, dev, in_the_window)
    plan = freeing.dump_plan([(p, e.size, e.mtime) for p, e in diagnostics.dump_files(st)])
    status_page.confirm_dumps(win, plan)
    kept = status.read_kept(UNIT)
    assert kept.dumps_at_last == (1, 9_000) and kept.recounted


@pytest.mark.needs_display
def test_an_update_file_removed_from_a_device_that_then_stops_is_taken_off_its_updates(
        tmp_path, clock, in_the_window, monkeypatch):
    """Not counted again after the removal of an update file: its size goes off the updates of
    the device log, never off its logs and memory dumps."""
    from openexpress import freeing, status
    from openexpress.gui import status_page

    st, dev = stopping_watch(tmp_path, {"GARMIN/Debug/dump/RAM.zip": 9_000,
                                        "GARMIN/RemoteSW/GPS.GCD": 40_000})
    before = status.read_kept(UNIT)
    assert (before.categories["updates"], before.categories["logs"]) == (40_000, 9_000)
    gps = made_up_update("GARMIN/RemoteSW/GPS.GCD", size=40_000)
    gps.listed_file = freeing.listed_file(st, gps)
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: ([gps], None))
    status_page.confirm_update(removing_window(st, dev, in_the_window), gps)
    kept = status.read_kept(UNIT)
    assert (kept.categories["updates"], kept.categories["logs"]) == (0, 9_000)
    assert kept.dumps_at_last == (1, 9_000)


@pytest.mark.needs_display
def test_a_removal_from_the_page_counts_the_device_once(clock, in_the_window, monkeypatch):
    """The removal counts the device again itself: the window does not count it a second time
    after the task (over MTP a count lists every folder of the device)."""
    from openexpress import freeing, status
    from openexpress.gui import status_page

    counts = []

    def recount(device, storage, **kw):
        counts.append(device)
        return status.Recount({}, 5_000, 10_000, clock.current, dumps=[])

    def remove_dumps(storage, unit_id, plan, progress=None, freed=None):
        freed.removed += [(p, s) for p, s, _t in plan.older]
        return freed

    monkeypatch.setattr(status, "recount", recount)
    monkeypatch.setattr(freeing, "remove_dumps", remove_dumps)
    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw:
                  answers[0].run())
    session = device_session(device=types.SimpleNamespace(unit_id=UNIT),
                             storage=types.SimpleNamespace(test_drive=False),
                             space=(1_000, 10_000), diagnostics=None)
    win = task_window(session, monkeypatch, toast=lambda text: None,
                      progress=None)
    status_page.confirm_dumps(win, freeing.dump_plan([
        ("GARMIN/Debug/dump/RAM.zip", 90, 1_700_000_400),
        ("GARMIN/Debug/dump/RAM_bak1.zip", 300, 1_700_000_300)]))
    assert len(counts) == 1


@pytest.mark.needs_display
def test_after_a_write_that_cannot_be_counted_again_the_free_space_is_read_again(monkeypatch,
                                                                                in_the_window):
    """A task that wrote to the device whose count fails: the free space alone is read again, so
    that the row Device Storage does not tell the space from before the write."""
    from openexpress import status
    from openexpress.device.storage import DeviceError
    from openexpress.gui.tasks import writes_to_device

    def fails(device, storage, **kw):
        raise DeviceError("Cannot list GARMIN: the device is busy")

    monkeypatch.setattr(status, "recount", fails)
    in_the_window("read_space", lambda storage, capacity: (5, 10))
    session = device_session(space=(9, 10), storage=None, device=None, diagnostics=None)
    win = task_window(session, monkeypatch)
    win.run_task("Copying…", writes_to_device(lambda: None))
    assert session.space == (5, 10)


@pytest.mark.needs_display
def test_the_question_names_the_size_of_the_file_on_the_device(in_the_window):
    """What the removal frees is the size of the file on the device, which may differ from the
    update server's: the question names the file's."""
    from fakes import window

    from openexpress.gui import status_page

    asked = []
    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw:
                  asked.append(body))
    gps = made_up_update("GARMIN/RemoteSW/006-D0000-00.GCD", size=4_100_000)
    gps.listed_file = (3_900_000, 1_700_000_000.0)
    status_page.confirm_update(window(session=device_session()), gps)
    (body,) = asked
    assert "GPS software 6.20 (3.9 MB)" in body


# --- the row and the page from one count, a removal told once ---------------------------------


class _FreeWatch(LocalStorage):
    def __init__(self, root, free=4_000_000):
        super().__init__(root)
        self.device_uri = "mtp://made-up/"
        self.usb = types.SimpleNamespace(bus=1, address=5, serial="MADE-UP")
        self.mass_storage = False
        self.free = free

    def capacity(self):
        return 10_000_000_000

    def free_space(self):
        return self.free

    def usb_blocked(self):
        return False

    def exclusive_ptp(self):
        return None


def make_free_watch(tmp_path, free=4_000_000):
    root = tmp_path / "device"
    (root / "GARMIN" / "Debug").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    st = _FreeWatch(root, free)
    return st, parse(st.read("GARMIN/GarminDevice.xml"))


def row_subtitle_for(session):
    from fakes import window
    from gi.repository import Adw

    from openexpress.gui.device_page import status_row

    win = window(session=session, select_section=lambda key: None)
    status_row(win, Adw.PreferencesGroup())
    return win.status_row.get_subtitle()


@pytest.mark.needs_display
def test_the_row_and_the_page_tell_the_same_summary_when_the_free_space_was_not_read(
        tmp_path, clock):
    """The free space not read at the opening: the page reads it, and the row of the device
    page says the same as the page's summary then."""
    from openexpress.gui import words
    from openexpress.gui.status_page import read_page

    st, dev = make_free_watch(tmp_path, free=100 * MB)  # under 500 MB of 10 GB: little
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    s = device_session(device=dev, storage=st, diagnostics=found,
                       diagnostics_reading=None, space=(None, 10_000_000_000),
                       client=None)
    made = read_page(s)
    page_title = words.status_summary(made.crashes, made.errors_known, made.free,
                                      made.capacity, datetime.now())[1]
    row = row_subtitle_for(s)
    assert (page_title, row) == ("Little Free Space", "Little Free Space"), (page_title, row)


@pytest.mark.needs_display
def test_after_a_failed_reading_the_row_tells_what_the_page_tells(tmp_path, clock):
    """After a failed reading of the error logs, the row of the device page says what the page's
    summary says (the crashes kept on this computer)."""
    import threading

    from openexpress.gui import words

    st, dev = make_free_watch(tmp_path)
    at = (clock.current - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    where = history.path(UNIT)
    where.parent.mkdir(parents=True, exist_ok=True)
    when = (clock.current - timedelta(days=1)).astimezone().isoformat(timespec="seconds")
    where.write_text(json.dumps({
        "v": 1, "time": when, "conn": "earlier", "via": "mtp", "software": "23.39",
        "categories": {}, "crash": {"kind": "RTL Trap", "at": at, "error": "0x003",
                                    "pc": "0x1", "screen": None},
        "crashes": [{"kind": "RTL Trap", "at": at, "error": "0x003", "pc": "0x1",
                     "screen": None}]}) + "\n")
    done = threading.Event()
    done.set()
    s = device_session(device=dev, storage=st, diagnostics=None,
                       diagnostics_reading=done, space=(4_000_000, 10_000_000_000),
                       client=None)
    made = status.read(s)
    page_title = words.status_summary(made.crashes, made.errors_known, made.free,
                                      made.capacity, datetime.now())[1]
    row = row_subtitle_for(s)
    assert page_title == "1 Crash in the Last 30 Days"
    assert row == page_title, f"row says {row!r}, the page {page_title!r}"


@pytest.mark.needs_display
@pytest.mark.parametrize("free", [9_000_000_000, 4_000_000])
def test_the_row_of_a_device_without_diagnostic_data_says_what_its_page_says(tmp_path,
                                                                               clock, free):
    """A device without diagnostic data: its page and the row of the device page both say No
    Diagnostic Data, whatever its free space."""
    st, dev = make_free_watch(tmp_path, free=free)
    st.device_uri = ""  # no hidden crash logs to copy (a bike computer that shows them)
    found = diagnostics.at_open(dev, st)
    history.note_connection(dev, st, found=found)
    s = device_session(device=dev, storage=st, diagnostics=found,
                       diagnostics_reading=None, space=(free, 10_000_000_000),
                       client=None)
    made = status.read(s)
    assert made.has_diagnostics is False  # the page: No Diagnostic Data
    row = row_subtitle_for(s)
    assert row == "No Diagnostic Data", f"the row says {row!r}"


@pytest.mark.needs_display
def test_the_page_reads_the_space_of_now(tmp_path, clock):
    """The page reads the free space when it is read: after the command line removed a file and
    put this connection's line right, the page tells the space of now, not the window's own
    count."""
    st, dev = make_free_watch(tmp_path)
    history.note_connection(dev, st)
    t1 = clock.current
    s = device_session(device=dev, storage=st, diagnostics=diagnostics.AtOpen([], [], False),
                       space=(4_000_000, 10_000_000_000),
                       recount=status.Recount({"logs": 5}, 4_000_000, 10_000_000_000, t1, []))
    clock.current += timedelta(minutes=5)
    st.free = 9_000_000  # the command line removed a file
    history.note_counted(dev, st, clock.current)  # as the command line puts the line right
    line = history.connection_line(dev, st)
    assert line["free"] == 9_000_000
    from openexpress.gui import status_page

    made = status_page.read_page(s)
    assert made.free == 9_000_000, f"the page shows {made.free}, the device log {line['free']}"


@pytest.mark.needs_display
def test_a_removal_that_ended_and_was_counted_is_not_told_as_not_finished(clock,
                                                                            in_the_window):
    """A removal that went to its end and was counted, the device unplugged only then, is told as
    done."""
    from fakes import window

    from openexpress import freeing
    from openexpress.gui import status_page

    told, toasts = [], []
    in_the_window("inform", lambda parent, name, heading, body, **kw: told.append(heading))
    old = device_session(device=None, storage=None)
    win = window(session=device_session(device=None, storage=None), toast=toasts.append)
    counted = status.Recount({}, 9_000, 10_000, clock.current, dumps=[])
    status_page._freed(win, old, freeing.Freed(removed=[("GARMIN/Debug/dump/a.zip", 1)]),
                       "1 memory dump removed, 1 B freed", counted)
    assert told == [] and toasts == ["1 memory dump removed, 1 B freed"], (told, toasts)


@pytest.mark.needs_display
def test_a_task_that_waited_for_the_count_finds_it_in_the_session(tmp_path, clock):
    """A task that waited for the count after a write finds that count in the session."""
    from fakes import window

    from openexpress.gui import window_tasks

    st, dev = make_free_watch(tmp_path)
    history.note_connection(dev, st)
    s = device_session(device=dev, storage=st, recount=None, space=(1, 10),
                       diagnostics=None)
    win = window(session=s, storage_row=None, storage_bar=None, sections={})
    window_tasks.count_again(win, s)
    window_tasks.wait_for_the_count(s)
    assert s.counting.is_set()
    assert s.recount is not None, "the count is done, but not in the session for the task"


@pytest.mark.needs_display
def test_a_removal_that_removed_nothing_says_so(clock, in_the_window):
    """A removal that found nothing left to remove (removed meanwhile by something else) says so."""
    from fakes import window

    from openexpress.gui import status_page

    told, toasts = [], []
    in_the_window("inform", lambda parent, name, heading, body, **kw: told.append(heading))
    s = device_session(device=None, storage=None)
    win = window(session=s, toast=toasts.append, sections={})
    status_page._freed(win, s, freeing.Freed(), "Update file removed, 0 B freed", None)
    assert told or toasts, "nothing told after the removal"


@pytest.mark.needs_display
def test_a_removal_not_counted_reads_the_page_once(clock, in_the_window, monkeypatch):
    """After a removal whose count failed, the page is read once, with the free space read again."""
    from fakes import window

    from openexpress.gui import device_page, status_page, window_tasks

    reads = []
    s = device_session(device=None, storage=types.SimpleNamespace(
        free_space=lambda: 5, capacity=lambda: 10), space=(1, 10))
    win = window(session=s, toast=lambda text: None, storage_row=None, storage_bar=None,
                 sections={"status": types.SimpleNamespace(loaded=True)},
                 pages=types.SimpleNamespace(get_visible_child_name=lambda: "status"),
                 load_section=lambda key, force=False: reads.append(key))
    monkeypatch.setattr(window_tasks.TaskRunner, "in_background",
                        staticmethod(lambda fn, done, failed=None: done(fn())))
    monkeypatch.setattr(device_page, "status_row", lambda win, group=None: None)
    status_page._freed(win, s, freeing.Freed(removed=[("GARMIN/Debug/dump/a.zip", 4)]),
                       "1 memory dump removed, 4 B freed", None)
    assert s.space == (5, 10)
    assert reads == ["status"], f"the page read {len(reads)} times"


# --- a crash ahead of the clock, the Updates page after a removal ----------------------------


def test_a_crash_more_than_a_day_ahead_is_not_of_the_last_30_days():
    """A device clock set far ahead dates its crash in the future: not a crash of the last 30
    days (a day ahead still is: the device's zone may be ahead of this computer's)."""
    far_ahead, a_day_ahead, last_week = crash(-2), crash(-1), crash(7)
    found = status.recent([far_ahead, a_day_ahead, last_week], NOW)
    assert [c.at for c in found] == [a_day_ahead.at, last_week.at]


@pytest.mark.needs_display
def test_removing_an_update_file_has_the_updates_page_read_again(clock, in_the_window,
                                                                monkeypatch):
    """The Updates page offers the update again only when it is read anew: once the file is
    removed, it is marked to be read again."""
    from openexpress import freeing

    asked = questions(in_the_window)
    gps = made_up_update("GARMIN/RemoteSW/006-D0000-00.GCD")
    win, section = page(facts(not_installed=[gps]))
    working(win, monkeypatch)
    win.sections["updates"] = types.SimpleNamespace(loaded=True)
    monkeypatch.setattr(freeing, "remove_update",
                        lambda storage, u, client, device, freed=None: freeing.Freed(
                            removed=[(u.device_path, u.size)]))
    button_of(titled(section, "GPS software 6.20")).emit("clicked")
    asked[0].answers[0].run()
    assert win.sections["updates"].loaded is False


# --- a task after a write waits for the count, a closed session let go, a refused task -------


NOW = datetime(2026, 10, 21, 12, 0)


@pytest.mark.needs_display
def test_a_task_started_as_a_write_ends_waits_for_the_count(monkeypatch, in_the_window):
    """A task started when a write ends (a page read again) waits for the count after that write."""
    from fakes import tasks_at_once, window, window_methods

    from openexpress.gui.tasks import writes_to_device

    tasks_at_once(monkeypatch)
    order = []

    def recount(device, storage, removed=None):
        order.append("count")
        return status.Recount({}, 5, 10, NOW, dumps=[])

    monkeypatch.setattr(status, "recount", recount)
    in_the_window("render_space", lambda win: None)
    s = device_session(device=None, storage=None, diagnostics=None, space=(9, 10))
    win = window(session=s, _run_pending_refresh=lambda: None, sections={},
                 toast=lambda *a: None)
    win.set_busy = lambda busy, *a, **kw: setattr(win, "busy", busy)
    win = window_methods(win, ["run_task"])
    win.run_task("Removing…", writes_to_device(lambda: order.append("write")),
                 lambda _r: win.run_task("Reading the page…",
                                         lambda: order.append("page reads the device"),
                                         device=False))
    assert order == ["write", "count", "page reads the device"], order


@pytest.mark.needs_display
def test_with_real_threads_the_page_task_waits_for_the_count(monkeypatch, in_the_window):
    """With the window's real threads and main loop, the page task started when a write ends never
    asks the device while the count goes on."""
    import threading
    import time

    from fakes import window, window_methods
    from gi.repository import GLib

    from openexpress.gui.tasks import writes_to_device

    finished = []

    def recount(device, storage, removed=None):
        time.sleep(0.2)
        finished.append(True)
        return status.Recount({}, 5, 10, NOW, dumps=[])

    monkeypatch.setattr(status, "recount", recount)
    in_the_window("render_space", lambda win: None)
    overlapped = []
    for _n in range(10):
        s = device_session(device=None, storage=None, diagnostics=None, space=(9, 10))
        win = window(session=s, _run_pending_refresh=lambda: None, sections={},
                     toast=lambda *a: None)
        win.set_busy = lambda busy, *a, w=win, **kw: setattr(w, "busy", busy)
        win = window_methods(win, ["run_task"])
        done = threading.Event()
        before = len(finished)

        def page(s=s, done=done, before=before):
            overlapped.append(len(finished) == before)  # the count of this write not done yet
            done.set()

        win.run_task("Removing…", writes_to_device(lambda: None),
                     lambda _r, win=win, page=page: win.run_task("Reading…", page, device=False))
        ctx = GLib.MainContext.default()
        end = time.monotonic() + 5
        while not done.is_set() and time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.001)
        # let the count finish
        end = time.monotonic() + 1
        while time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.005)
    print("asked the device during the count:", sum(overlapped), "of", len(overlapped))
    assert not any(overlapped), overlapped


@pytest.mark.needs_display
def test_a_device_unplugged_while_its_page_waited_is_let_go(clock):
    """The page Device Status chosen while the error logs were read, then the device unplugged:
    the window keeps nothing of its session (its storage, device, client)."""
    import gc
    import threading
    import weakref

    from fakes import page_window

    from openexpress.gui import connected
    from openexpress.gui.app import MainWindow

    class Session:
        device = types.SimpleNamespace(unit_id=None)
        storage = types.SimpleNamespace(device_uri="mtp://made-up/")
        diagnostics = None

    win, _section = page_window("status")
    s = Session()
    s.diagnostics_reading = threading.Event()
    win.session = s
    MainWindow.load_status(win)
    assert s.page_waits
    win.show_no_device = lambda: None
    gone = weakref.ref(s)
    connected.close_as_unplugged(win)
    del s
    gc.collect()
    assert gone() is None


@pytest.mark.needs_display
def test_an_answer_while_another_task_runs_is_said_in_the_log(monkeypatch, in_the_window,
                                                             caplog):
    """An answer whose task cannot start while another runs is told (a toast) and said in the log."""
    from fakes import window, window_methods

    from openexpress import freeing
    from openexpress.gui import status_page

    plan = freeing.dump_plan([("GARMIN/Debug/dump/RAM.zip", 9, 1_700_000_300.0),
                              ("GARMIN/Debug/dump/RAM_bak1.zip", 8, 1_700_000_200.0)])
    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw:
                  answers[0].run())
    s = device_session(device=types.SimpleNamespace(unit_id=1234), storage=None)
    toasts = []
    win = window(session=s, busy=True, toast=toasts.append, progress=None)
    win = window_methods(win, ["run_task"])
    with caplog.at_level("INFO"):
        status_page.confirm_dumps(win, plan)
    said = [r.getMessage() for r in caplog.records]
    assert toasts == ["Another operation is in progress"]
    assert "task _then_counted: not started, another one is running" in said


# --- refusals told after a stop, no button without a removal, the count before the tasks -----


@pytest.mark.needs_display
def test_files_the_device_refused_are_told_also_when_it_then_stopped(clock, in_the_window,
                                                                     monkeypatch):
    """Nothing removed, but the device refused a file before it stopped responding: the
    refusal is told, not lost in the failure."""
    asked = questions(in_the_window)
    told = []
    in_the_window("inform", lambda parent, name, heading, body, **kw: told.append(
        (heading, body)))
    win, section = page()
    working(win, monkeypatch)
    win.run_task.error = "record"  # a task that fails: its error kept, not raised

    def refused_then_stopped(storage, unit_id, plan, progress=None, freed=None):
        freed.kept.append(("GARMIN/Debug/dump/RAM_bak1.zip",
                           "Cannot delete RAM_bak1.zip: the device said no"))
        raise DeviceNotResponding("no answer")

    monkeypatch.setattr(freeing, "remove_dumps", refused_then_stopped)
    button_of(titled(section, "Older Memory Dumps")).emit("clicked")
    asked[0].answers[0].run()
    assert told == [("Not All Files Removed",
                     ("The device did not remove the file.\n\n"
                      "RAM_bak1.zip: Cannot delete RAM_bak1.zip: the device said no"))]


@pytest.mark.needs_display
def test_older_dumps_that_may_not_be_removed_have_no_button(clock):
    """A device whose older dumps may not be removed (no unit ID: their copies would have no
    folder of the device): the row tells them, with no button that removes them."""
    _win, section = page(facts(dumps_plan=None))
    older = titled(section, "Older Memory Dumps")
    assert older.get_subtitle() == ("2 files; the newest dump is not counted · "
                                    "18.0 MB")
    assert button_of(older) is None


def test_the_tasks_waiting_for_the_count_go_on_only_once_it_is_in_the_session(monkeypatch):
    """A task that waited for the count after a write finds that count in the session: the
    waiting tasks are let go only after it is kept there."""
    from openexpress.gui import window_tasks
    from openexpress.gui.window_tasks import TaskRunner

    later = []
    monkeypatch.setattr(TaskRunner, "in_background",
                        staticmethod(lambda work, done, failed=None: later.append(work)))
    s = device_session(device=None, storage=None)
    window_tasks.count_again(types.SimpleNamespace(session=s), s)
    counted = status.Recount({"maps": 4}, 5, 10, NOW, dumps=[])
    monkeypatch.setattr(status, "recount", lambda device, storage: counted)
    real = status.counted_again
    let_go = []

    def kept(session, made):
        let_go.append(session.counting.is_set())
        return real(session, made)

    monkeypatch.setattr(status, "counted_again", kept)
    later[0]()
    assert let_go == [False], "the waiting tasks went on before the count was in the session"
    assert s.counting.is_set() and s.recount is counted


def test_a_crash_exactly_30_days_old_is_of_the_last_30_days():
    """The last 30 days hold a crash of exactly 30 days ago; a second older is not of them."""
    thirty_days, a_second_more = crash(30), crash(30 + 1 / 86_400)
    found = status.recent([thirty_days, a_second_more], NOW)
    assert [c.at for c in found] == [thirty_days.at]
