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
    from openexpress import freeing, history, known_devices

    monkeypatch.setattr(freeing, "plan", lambda storage, device, client, fresh=False: None)
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
    """The page's window able to run a task on a made-up open device (whose kinds of file are
    not counted again: status.recount gives nothing): its toasts in `win.toasts`, the pages it
    reads again in `win.reloads`."""
    from openexpress import status

    monkeypatch.setattr(status, "recount", lambda device, storage: None)
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
    assert button_of(older).get_label() == "Copy and Remove"
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
    dumps = [("GARMIN/Debug/dump/RAM.zip", 9 * MB, 300.0), ("GARMIN/Debug/dump/RAM_bak1.zip",
                                                           8 * MB, 0)]
    _win, section = page(facts(dumps=dumps))
    older = titled(section, "Older Memory Dumps")
    assert older.get_subtitle() == "Not known" and button_of(older) is None


GB_DUMPS = {
    # fēnix-like: all fit in the copies
    "all": [("GARMIN/Debug/dump/RAM.zip", 9_100_000, 900.0),
            *[(f"GARMIN/Debug/dump/RAM_bak{n}.zip", 8_980_000, 900.0 - n) for n in range(1, 6)]],
    # Edge-like: two zips fit, three old logs are too large
    "some": [("GARMIN/Debug/dump/ramdump2.zip", 13_300_000, 900.0),
             ("GARMIN/Debug/dump/ramdump1.zip", 45_800_000, 800.0),
             ("GARMIN/Debug/dump/ramdump3.zip", 29_500_000, 799.0),
             *[(f"GARMIN/Debug/ramdump{n}.log", 202_500_000, 100.0 - n) for n in range(1, 4)]],
    # none fits
    "none": [("GARMIN/Debug/dump/ramdump2.zip", 13_300_000, 900.0),
             *[(f"GARMIN/Debug/ramdump{n}.log", 202_500_000, 100.0 - n) for n in range(1, 4)]],
}


@pytest.mark.needs_display
@pytest.mark.parametrize(("dumps", "body", "answer"), [
    ("all", ("5 memory dumps (44.9\u00a0MB) will be copied to this computer and then removed "
             "from the device. The newest dump stays on the device."), "Copy and Remove"),
    ("some", ("5 memory dumps (682.8\u00a0MB) will be removed from the device: 2 (75.3\u00a0MB) "
              "after a copy on this computer, 3 (607.5\u00a0MB) without a copy, as they are too "
              "large. The newest dump stays on the device."), "Copy and Remove"),
    ("none", ("3 memory dumps (607.5\u00a0MB) will be removed from the device without a copy: "
              "they are too large to copy. The newest dump stays on the device."), "Remove")])
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
                      "kopii, bo są za duże. Najnowszy zrzut zostaje na urządzeniu.")
    assert [a.label for a in q.answers] == ["Skopiuj i\u00a0usuń"]


@pytest.mark.needs_display
def test_copy_and_remove_runs_a_task_and_reads_the_page_again(clock, in_the_window,
                                                              monkeypatch):
    from openexpress import freeing

    asked = questions(in_the_window)
    win, section = page()
    working(win, monkeypatch)
    called = []

    def remove_dumps(storage, unit_id, older, test_drive=False, progress=None):
        called.append(([p for p, _s, _t in older], unit_id, test_drive))
        return freeing.Freed(removed=[(p, s) for p, s, _t in older])

    monkeypatch.setattr(freeing, "remove_dumps", remove_dumps)
    button_of(titled(section, "Older Memory Dumps")).emit("clicked")
    asked[0].answers[0].run()
    assert called == [(["GARMIN/Debug/dump/RAM_bak1.zip", "GARMIN/Debug/dump/RAM_bak2.zip"],
                       1234567890, False)]
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

    from openexpress import diagnostics, history, status
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.gui.app import MainWindow

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
    at_open = diagnostics.AtOpen(entries=[], crash=None, hidden_logs=False, dumps=[
        (f"GARMIN/Debug/dump/{p.name}", p.stat().st_size) for p in dump.iterdir()])
    session = types.SimpleNamespace(device=device, storage=storage, diagnostics=at_open,
                                    crash_history=[], space=None)
    asked = questions(in_the_window)
    win, section = page(status.read(session))
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
        "Counted again after a removal, just now")


@pytest.mark.needs_display
def test_after_a_removal_its_copies_count_on_this_computer(removed_on_a_drive):
    win, section, _storage = removed_on_a_drive
    assert win.toasts == ["2 memory dumps removed, 18.0\u00a0kB freed"]
    assert titled(section, "Copies on This Computer").get_subtitle() == (
        "1 copy\u00a0· 18.0\u00a0kB\u00a0· the last just now")


@pytest.mark.needs_display
def test_a_removal_that_fails_reads_the_page_again(clock, in_the_window, monkeypatch):
    """A part may be removed: the page shows what is left, and the error is said."""
    from fakes import RunNow

    from openexpress import freeing
    from openexpress.device.storage import DeviceError

    asked = questions(in_the_window)
    win, section = page()
    working(win, monkeypatch)
    win.run_task = RunNow(error="record")

    def fails(storage, unit_id, older, **kw):
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
    monkeypatch.setattr(freeing, "remove_update", lambda storage, u: freeing.Freed(
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
