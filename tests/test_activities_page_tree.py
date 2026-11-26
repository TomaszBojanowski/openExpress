"""The widget tree of the Activities page in each of its states (types, texts, CSS classes,
order), against a pattern the page drew before the activity journal existed
(tests/data/activities_page_tree.json). Not answered and after a no, the journal adds its card
or its line and nothing else: nothing of the page as it was may change. After the yes the page
shows what is new since the connection before this one, against a pattern of its own
(tests/data/activities_since_tree.json), and Show All pushes the page as it was. Drawn a second
time in the window it was drawn in (plugged in again, Try Again, the answer changed, another
device, more rows shown, the journal's reading done, new activities), the page is the page a
fresh window draws.
Made-up files only; a folder stands for the device. To draw a pattern again (only on the
commit it comes from, e.g. after an update of libadwaita changed its inner widgets):
OPENEXPRESS_WRITE_PAGE_TREE=1 python3 -m pytest -q -p no:cacheprovider tests/test_activities_page_tree.py
(the page as it was), OPENEXPRESS_WRITE_SINCE_TREE=1 the same (what is new).
"""

import json
import math
import os
import time
import types
from datetime import datetime
from pathlib import Path

import pytest
from conftest import DEVICE_XML
from fakes import (
    FIT_EPOCH,
    FitData,
    FitDefinition,
    descendants,
    fit_activity,
    fit_file,
    page_window,
    window_methods,
)

pytestmark = [pytest.mark.needs_display, pytest.mark.journal_answer]

PATTERN = Path(__file__).parent / "data" / "activities_page_tree.json"
WRITE = "OPENEXPRESS_WRITE_PAGE_TREE"
SINCE_PATTERN = Path(__file__).parent / "data" / "activities_since_tree.json"
WRITE_SINCE = "OPENEXPRESS_WRITE_SINCE_TREE"
PAGE_METHODS = ["load_activities", "show_activities"]
WARSAW = (624_000_000, 250_000_000)  # a made-up position, in semicircles
UNIT_ID = 1234567890  # the made-up watch of DEVICE_XML
EDGE_ID = 2345678901  # a made-up bike computer
NB = "\u00a0"
STATES = ("loading", "error", "keeps-none", "none", "rows", "more", "only-in-backups",
          "drive", "other-account", "logged-out")


SEEN = datetime(2026, 10, 30, 12, 0)  # the made-up clock: a Friday after A_WEEK
BEFORE = datetime(2026, 10, 27, 9, 0)  # the connection before this one: the 29th is new


def unix(*when) -> int:
    return int(datetime(*when).timestamp())


@pytest.fixture(autouse=True)
def on_a_friday(clock):
    """The window's clock (the words "Since …" and the times told against now) at SEEN."""
    clock.current = SEEN
    return clock


def keep_journal(session, before: datetime | None = BEFORE) -> None:
    """The session's device read into its journal as the window reads it at this connection,
    the reading done; a journal that is not there yet starts with a connection before this one
    at `before` (None: none, the journal's first reading)."""
    from openexpress import activity_journal, history, paths

    where = activity_journal.path(session.device.unit_id)
    if not where.exists() and before is not None:
        where.parent.mkdir(parents=True, exist_ok=True)
        line = activity_journal.connection(before, 0, None, None, "the connection before")
        where.write_text(json.dumps(line) + "\n")
        where.chmod(0o600)
    activity_journal.add(session.device, session.storage, paths.default_backup_dir(), now=SEEN,
                         conn=history.connection_key(session.device, session.storage))
    session.journal_done = True


def kept() -> bool:
    from openexpress import activity_journal

    return activity_journal.answered() is True


def widget(w) -> dict:
    """One widget and all under it: its type, CSS classes, what it says and shows, whether it
    is visible and takes clicks."""
    from gi.repository import Adw, Gtk

    said: dict = {"type": w.__gtype__.name, "css": sorted(w.get_css_classes())}
    if not w.get_visible():
        said["hidden"] = True
    if not w.get_sensitive():
        said["insensitive"] = True
    if w.get_tooltip_text():
        said["tooltip"] = w.get_tooltip_text()
    if isinstance(w, Gtk.Label):
        said["label"] = w.get_label()
    elif isinstance(w, Gtk.Button):
        said["label"] = w.get_label()
        said["icon"] = w.get_icon_name()
    elif isinstance(w, Gtk.Image):
        said["icon"] = w.get_icon_name()
    if isinstance(w, Adw.PreferencesRow):
        said["title"] = w.get_title()
    if isinstance(w, Adw.ActionRow):
        said["subtitle"] = w.get_subtitle()
        said["activatable"] = w.get_activatable()
    if isinstance(w, Adw.PreferencesGroup):
        said["title"], said["description"] = w.get_title(), w.get_description()
    if isinstance(w, Adw.StatusPage):
        said["title"], said["description"] = w.get_title(), w.get_description()
        said["icon"] = w.get_icon_name()
    children, child = [], w.get_first_child()
    while child is not None:
        children.append(widget(child))
        child = child.get_next_sibling()
    if children:
        said["children"] = children
    return said


def page_tree(section) -> dict:
    """What the page shows: which of its views, the banner, and the groups of its content (the
    status page instead, when it shows one)."""
    shown = section.stack.get_visible_child_name()
    tree = {"shown": shown, "banner": section.banner.get_revealed()}
    if shown == "content":
        tree["groups"] = [widget(g) for g in section.groups]
    else:
        tree["status"] = widget(section.stack.get_visible_child())
    return tree


def write_device(folder: Path, files: dict[str, bytes], xml: str = DEVICE_XML):
    """A folder as a device with these activity files, the newest last: its session."""
    activity = folder / "GARMIN" / "Activity"
    activity.mkdir(parents=True)
    (folder / "GARMIN" / "GarminDevice.xml").write_text(xml)
    for n, (name, data) in enumerate(files.items()):
        (activity / name).write_bytes(data)
        os.utime(activity / name, (1_000 + n, 1_000 + n))
    return session_of(folder)


def session_of(folder: Path):
    """The session of a folder written as a device (write_device)."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    storage = LocalStorage(folder)
    return types.SimpleNamespace(device=parse(storage.read("GARMIN/GarminDevice.xml")),
                                 storage=storage, logged_in=True, ownership_error=None,
                                 mass_storage=False, journal_read=False)


def write_backup(backups: Path, files: dict[str, bytes]) -> None:
    """A backup of the made-up watch with these activity files."""
    from openexpress.backup import STAMP
    from openexpress.sync import ACTIVITY_TYPE

    folder = backups / str(UNIT_ID) / datetime(2025, 10, 2, 10, 0).strftime(STAMP) / ACTIVITY_TYPE
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)


def run(day: int, **options) -> bytes:
    return fit_activity(unix(2026, 10, day, 7, 0) - FIT_EPOCH, records=2, position=WARSAW,
                        **options)


A_WEEK = {
    "2026-10-24-07-00-00.fit": run(24),
    "2026-10-25-07-00-00.fit": fit_activity(unix(2026, 10, 25, 18, 0) - FIT_EPOCH, sport=10,
                                            distance_cm=None, ascent=None),
    "2026-10-26-07-00-00.fit": run(26, bad_crc=True),
    "notes.fit": b"no FIT file",
    "2026-10-28-07-00-00.fit": run(28, cut=40),
    "2026-10-29-07-00-00.fit": run(29),
}
MORE = {f"2026-09-{n:02}-07-00-00.fit": run(1) for n in range(1, 32)}  # a page and one more
IN_A_BACKUP = {"2025-09-14-09-00-00.fit": fit_activity(unix(2025, 9, 14, 9, 0) - FIT_EPOCH,
                                                       sport=2, records=2, position=WARSAW),
               "2026-10-24-07-00-00.fit": run(24)}


def draw(state: str, folder: Path, backups: Path, monkeypatch):
    """The page in `state`, drawn as the window draws it: the stand-in and the page."""
    from openexpress.device.storage import DeviceError

    win, section = page_window("activities")
    window_methods(win, PAGE_METHODS)
    win.progress = lambda _step: None
    if state == "loading":
        win.session = write_device(folder, A_WEEK)
        section.show("loading")
        return win, section
    files = {"none": {}, "only-in-backups": {}, "more": MORE}
    xml = (DEVICE_XML.replace("<Name>FIT_TYPE_4</Name>", "<Name>FIT_TYPE_999</Name>")
           if state == "keeps-none" else DEVICE_XML)
    win.session = write_device(folder, files.get(state, A_WEEK), xml)
    if state in ("rows", "only-in-backups"):
        write_backup(backups, IN_A_BACKUP)
    if state == "drive":
        win.session.mass_storage = True
    if state == "other-account":
        from openexpress.connect import NotAccountDevice

        win.session.ownership_error = NotAccountDevice("made up: on another account")
    if state == "logged-out":
        win.session.logged_in = False
    if kept():
        from openexpress import activity_journal

        activity_journal.path(UNIT_ID).unlink(missing_ok=True)  # another device's state
        keep_journal(win.session)
    if state == "error":
        def broken(*args, **kwargs):
            raise DeviceError("made up: the device stopped answering")
        monkeypatch.setattr(win.session.storage, "walk_files", broken)
    win.load_activities()
    return win, section


@pytest.fixture
def in_warsaw(monkeypatch):
    """The page's times in one zone wherever the tests run."""
    monkeypatch.setenv("TZ", "Europe/Warsaw")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def answer(value) -> None:
    """The answer to the activity journal as settings.json keeps it (None: not answered)."""
    from openexpress.settings import Settings

    s = Settings.load()
    s.activity_journal = value
    s.save()


def draw_all(folder: Path, backups: Path, monkeypatch) -> dict:
    """Every state of the page drawn: {state: its tree}."""
    import shutil

    found = {}
    for state in STATES:
        _win, section = draw(state, folder / state, backups, monkeypatch)
        found[state] = page_tree(section)
        if state in ("rows", "only-in-backups"):
            shutil.rmtree(backups)
    return found


def test_each_state_of_the_page_is_the_pattern(tmp_path, backups, monkeypatch, in_warsaw):
    """The page as it was, drawn as before the journal existed (not answered and after a no it
    is checked against this pattern below)."""
    if os.environ.get(WRITE):
        answer(False)
        drawn = draw_all(tmp_path, backups, monkeypatch)
        PATTERN.write_text(json.dumps(drawn, indent=1, ensure_ascii=False) + "\n",
                           encoding="utf-8")
        pytest.fail(f"pattern written ({WRITE}), nothing compared")
    pattern = json.loads(PATTERN.read_text(encoding="utf-8"))
    assert sorted(pattern) == sorted(STATES)


# --- the page of what is new: its own states ---------------------------------------------------

# A record with a position, the elevation, the distance; a session with its sport and sub sport,
# times, distance and ascent; the device's units (user profile: distance, elevation, speed).
RECORD = FitDefinition(1, 20, ((253, 0x86), (0, 0x85), (1, 0x85), (5, 0x86)))
SESSION = FitDefinition(2, 18, ((2, 0x86), (5, 0x00), (6, 0x00), (7, 0x86), (8, 0x86),
                                (9, 0x86), (22, 0x84)))
PROFILE = FitDefinition(3, 3, ((6, 0x00), (13, 0x00), (14, 0x00)))
LOOP = [(math.cos(n / 20 * math.tau) * 1.6, math.sin(n / 20 * math.tau)) for n in range(21)]
ZIGZAG = [(n * 0.4, (n % 2) * 0.6 + n * 0.05) for n in range(12)]
CURVE = [(n * 0.25, math.sin(n / 3) * 0.5) for n in range(24)]


def track(when: datetime, sport: int, sub_sport: int, shape, *, km: float, minutes: int,
          ascent: int | None, statute: bool = False) -> bytes:
    """A made-up activity whose track has `shape` (points in kilometres east and north of
    52°N 21°E), or none for an empty `shape`."""
    start = int(when.timestamp()) - FIT_EPOCH
    def semi(degrees):
        return round(degrees * 2**31 / 180)
    records = [FitData(1, (start + n * 60, semi(52 + y / 111), semi(21 + x / 68), n * 100))
               for n, (x, y) in enumerate(shape)] or [FitData(1, (start, 0x7FFFFFFF,
                                                                  0x7FFFFFFF, 0))]
    units = 1 if statute else 0
    return fit_file(FitDefinition(0, 0, ((0, 0x00), (4, 0x86))), FitData(0, (4, start)),
                    RECORD, *records, SESSION,
                    FitData(2, (start, sport, sub_sport, minutes * 60_000, minutes * 60_000,
                                round(km * 100_000), 0xFFFF if ascent is None else ascent)),
                    PROFILE, FitData(3, (units, units, units)))


SHAPES = {
    "2026-10-28-07-00-00.fit": track(datetime(2026, 10, 28, 7, 0), 1, 3, ZIGZAG, km=21.3,
                                     minutes=171, ascent=640),
    "2026-10-29-07-00-00.fit": track(datetime(2026, 10, 29, 7, 0), 2, 7, CURVE, km=24.8,
                                     minutes=55, ascent=212),
    "2026-10-29-17-00-00.fit": track(datetime(2026, 10, 29, 17, 0), 1, None, LOOP, km=6.9,
                                     minutes=38, ascent=41),
    "2026-10-30-06-00-00.fit": track(datetime(2026, 10, 30, 6, 0), 5, 17, [], km=1.5,
                                     minutes=40, ascent=None),
    "2026-10-30-07-00-00.fit": track(datetime(2026, 10, 30, 7, 0), 10, 20, [], km=0,
                                     minutes=45, ascent=None),
}
MANY = {f"2026-10-{d:02}-{h:02}-00-00.fit": track(datetime(2026, 10, d, h, 0), 1, None, LOOP,
                                                  km=5.0, minutes=30, ascent=20)
        for d in (28, 29) for h in (6, 8, 10, 12)}
SINCE_STATES = ("reading", "started", "many", "shapes", "uploads", "sync-error", "imperial",
                "gap")


def last_sync(errors: list[str]) -> None:
    """The made-up last sync of the watch, after the connection before this one: two uploaded."""
    from openexpress import service

    record = {"time": datetime(2026, 10, 29, 20, 0).timestamp(), "unit_id": UNIT_ID,
              "uploaded": 2, "downloaded": 0, "skipped_known": 0, "rejected": 0,
              "errors": errors, "lines": []}
    where = service.device_last_sync_path(UNIT_ID)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps(record))


def draw_since(state: str, folder: Path):
    """The page of what is new in one of its own states."""
    import shutil

    from openexpress import activity_journal, paths, sync

    win, section = page_window("activities")
    window_methods(win, PAGE_METHODS)
    win.progress = lambda _step: None
    shutil.rmtree(paths.device_dir(UNIT_ID), ignore_errors=True)  # another state's records
    files = {"many": MANY, "reading": A_WEEK, "started": A_WEEK, "gap": A_WEEK}.get(state, SHAPES)
    if state == "imperial":
        files = {name: track(datetime(2026, 10, 29, 7, 0), 1, 3, ZIGZAG, km=21.3, minutes=171,
                             ascent=640, statute=True) for name in list(SHAPES)[:1]}
    win.session = write_device(folder, files)
    if state == "reading":
        win.session.journal_progress = (3, 10)
    elif state == "gap":
        where = activity_journal.path(UNIT_ID)
        where.parent.mkdir(parents=True, exist_ok=True)
        line = activity_journal.connection(BEFORE, 9, unix(2026, 9, 1), unix(2026, 10, 1),
                                           "the connection before")
        where.write_text(json.dumps(line) + "\n")
        keep_journal(win.session)
    else:
        keep_journal(win.session, None if state == "started" else BEFORE)
    if state in ("uploads", "sync-error"):
        state_ = sync.SyncState(UNIT_ID)
        state_.mark_completed(sync.ACTIVITY_TYPE, "2026-10-28-07-00-00.fit")
        state_.keep_pending(sync.ACTIVITY_TYPE, "2026-10-29-07-00-00.fit", b"made up")
        state_.save()
        last_sync(["made up: Garmin Connect did not answer"] if state == "sync-error" else [])
    win.load_activities()
    return win, section


def draw_all_since(folder: Path) -> dict:
    return {state: page_tree(draw_since(state, folder / state)[1]) for state in SINCE_STATES}


def test_after_the_yes_each_state_is_its_pattern(tmp_path, backups, monkeypatch, in_warsaw):
    """After the yes, what is new in each state of the device, and in the states of its own
    (tests/data/activities_since_tree.json)."""
    answer(True)
    drawn = {**draw_all(tmp_path, backups, monkeypatch), **draw_all_since(tmp_path / "since")}
    if os.environ.get(WRITE_SINCE):
        SINCE_PATTERN.write_text(json.dumps(drawn, indent=1, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
        pytest.fail(f"pattern written ({WRITE_SINCE}), nothing compared")
    pattern = json.loads(SINCE_PATTERN.read_text(encoding="utf-8"))
    assert sorted(pattern) == sorted((*STATES, *SINCE_STATES))
    for state in (*STATES, *SINCE_STATES):
        assert drawn[state] == pattern[state], state


def texts(tree: dict) -> list[str]:
    """Every text of a tree, in order."""
    found = [tree[k] for k in ("title", "description", "label", "subtitle")
             if isinstance(tree.get(k), str) and tree[k]]
    if tree["type"] == "GtkButton":
        return found  # its label once, not again as the label inside it
    for child in tree.get("children", ()):
        found += texts(child)
    return found


CARD = ["Keep an Activity Journal?",
        ("openExpress can keep a summary and the shape of each activity on this computer, so "
         "that the Activities page shows your weeks and your year even after the device "
         "deletes old files. Nothing is sent anywhere. You can turn it off or delete it in "
         "Preferences."), "Not Now", "Keep a Journal"]
LINE = ["An activity journal shows your weeks and your year", "Preferences"]
OTHER_ACCOUNT = [("The activity journal is not kept for a device on another Garmin Connect "
                  "account")]
SHOWN_AS_BEFORE = ("loading", "error", "keeps-none")  # no content: nothing added


@pytest.mark.parametrize("value", [None, False])
def test_the_journal_adds_its_card_or_its_line_and_nothing_else(tmp_path, backups, monkeypatch,
                                                                in_warsaw, value):
    """Not answered: the card on top of the page (not for a device that keeps no activities
    nor one on another account); no: the line at the very bottom. Each state's page is
    otherwise the pattern: types, texts, CSS classes and order."""
    answer(value)
    drawn = draw_all(tmp_path, backups, monkeypatch)
    pattern = json.loads(PATTERN.read_text(encoding="utf-8"))
    for state in STATES:
        tree, before = drawn[state], pattern[state]
        if state in SHOWN_AS_BEFORE or (state == "other-account" and value is None):
            assert tree == before, state
            continue
        groups = tree["groups"]
        rest = {k: v for k, v in tree.items() if k != "groups"}
        assert rest == {k: v for k, v in before.items() if k != "groups"}, state
        if value is None:
            assert texts(groups[0]) == CARD, state
            assert groups[1:] == before["groups"], state
        else:
            assert groups[:-1] == before["groups"], state
            assert texts(groups[-1]) == LINE, state


def test_after_the_yes_what_is_new_and_the_page_as_it_was_where_nothing_is(tmp_path, backups,
                                                                           monkeypatch,
                                                                           in_warsaw):
    """After the yes the page shows what is new in each state with activities (their numbers,
    tiles, the earlier ones and Show All); the reading, the page of a device that keeps none
    and the error stay what they were; a device on another account has the page as it was with
    its own line at the very bottom; one without any activity says it has none."""
    answer(True)
    drawn = draw_all(tmp_path, backups, monkeypatch)
    pattern = json.loads(PATTERN.read_text(encoding="utf-8"))
    for state in ("loading", "keeps-none", "none"):
        assert drawn[state] == pattern[state], state
    other = drawn["other-account"]
    assert other["groups"][:-1] == pattern["other-account"]["groups"]
    assert texts(other["groups"][-1]) == OTHER_ACCOUNT
    for state in ("rows", "drive", "logged-out", "error"):
        said = texts({"type": "page", "children": drawn[state]["groups"]})
        assert "1 new activity" in said and "Running" in said, state
    more = texts({"type": "page", "children": drawn["more"]["groups"]})
    assert "No new activities" in more and "31 earlier activities on this device" in more
    assert "Show All" in texts({"type": "page", "children": drawn["rows"]["groups"]})
    error = texts({"type": "page", "children": drawn["error"]["groups"]})
    assert error[:2] == ["made up: the device stopped answering", "Try Again"]
    assert "Show All" not in error
    backup = texts({"type": "page", "children": drawn["only-in-backups"]["groups"]})
    assert "No new activities" in backup and "Latest" in backup


def test_the_pattern_shows_what_each_state_is():
    """The pattern is the page it says: rows, the reading, the error, the page of a device
    that keeps no activities."""
    pattern = json.loads(PATTERN.read_text(encoding="utf-8"))
    assert pattern["loading"]["shown"] == "loading"
    assert pattern["error"]["shown"] == "error"
    assert pattern["keeps-none"]["shown"] == "info"
    for state in ("none", "rows", "more", "only-in-backups", "drive", "other-account",
                  "logged-out"):
        assert pattern[state]["shown"] == "content", state
    rows = json.dumps(pattern["rows"], ensure_ascii=False)
    assert "Unreadable File" in rows and "Backup of" in rows and "Export as GPX…" in rows
    assert "Show 1 More" in json.dumps(pattern["more"], ensure_ascii=False)
    assert "No Activities" in json.dumps(pattern["none"], ensure_ascii=False)
    # The page is the same over USB as a drive, off the account and logged out (no backups).
    assert pattern["drive"] == pattern["other-account"] == pattern["logged-out"]


# --- drawn again in one window ---------------------------------------------------------------

def page_of(session):
    """A fresh window with the Activities page of `session`, drawn: the stand-in and the page."""
    win, section = page_window("activities")
    window_methods(win, [*PAGE_METHODS, "_clear_device_pages"])
    win.progress = lambda _step: None
    win._update_banner = win._update_actions = lambda: None
    win.session = session
    if kept():
        keep_journal(session)
    win.load_activities()
    return win, section


def click(section, words: str) -> None:
    from gi.repository import Gtk

    (found,) = [w for g in section.groups for w in descendants(g)
                if isinstance(w, Gtk.Button) and w.get_label() == words]
    found.emit("clicked")


def says(section, words: str) -> bool:
    return words in json.dumps(page_tree(section), ensure_ascii=False)


@pytest.mark.parametrize("value", [None, False, True])
def test_each_state_drawn_again_in_its_window_is_the_same(tmp_path, backups, monkeypatch,
                                                         in_warsaw, value):
    """Each state drawn a second time in the same window (plugged in again, Try Again, the page
    chosen again): the same tree. The reading itself ("loading") is never drawn twice."""
    import shutil

    answer(value)
    for state in STATES:
        win, section = draw(state, tmp_path / state, backups, monkeypatch)
        first = page_tree(section)
        if state != "loading":
            win.load_activities()
        assert page_tree(section) == first, state
        if state in ("rows", "only-in-backups"):
            shutil.rmtree(backups)


@pytest.mark.parametrize("value", [None, False, True])
def test_a_short_list_then_more_than_a_page(tmp_path, monkeypatch, in_warsaw, value):
    """A week of activities, then more than a page of them on the same device (Refresh): the
    page a fresh window draws, with its Show N More."""
    answer(value)
    folder = tmp_path / "device"
    win, section = page_of(write_device(folder, A_WEEK))
    for n, (name, data) in enumerate(MORE.items()):
        (folder / "GARMIN" / "Activity" / name).write_bytes(data)
        os.utime(folder / "GARMIN" / "Activity" / name, (2_000 + n, 2_000 + n))
    if kept():
        keep_journal(win.session)  # Refresh: the journal reads them too
    win.load_activities()
    _fresh, fresh = page_of(session_of(folder))
    assert page_tree(section) == page_tree(fresh)
    assert says(section, "36 earlier activities on this device" if value else "Show 7 More")


@pytest.mark.parametrize("value", [None, False, True])
def test_one_device_then_another_in_one_window(tmp_path, monkeypatch, in_warsaw, value):
    """A watch with more than a page of activities, unplugged, then a bike computer that is a
    USB drive, also with more than a page: its page is the one a fresh window draws."""
    answer(value)
    win, section = page_of(write_device(tmp_path / "watch", MORE))
    edge = write_device(tmp_path / "edge", {**A_WEEK, **MORE},
                        DEVICE_XML.replace(str(UNIT_ID), str(EDGE_ID)))
    edge.mass_storage = True
    win.session = edge
    win._clear_device_pages()  # what the window does for the device plugged in now
    if kept():
        keep_journal(edge)
    win.load_activities()
    again = session_of(tmp_path / "edge")
    again.mass_storage = True
    _fresh, fresh = page_of(again)
    assert page_tree(section) == page_tree(fresh)
    assert says(section, "36 earlier activities on this device" if value else "Show 7 More")


@pytest.mark.parametrize("first", [False, True])
def test_the_answer_changed_with_the_page_shown(tmp_path, backups, monkeypatch, in_warsaw,
                                                first):
    """No, yes, no, or yes, no, yes, with the page of each state shown (the preferences' switch
    and the card draw it again so; after the yes the journal's reading done draws it again):
    after each the page a fresh window draws."""
    import shutil

    from openexpress.gui.activities_journal import journal_changed

    for state in STATES:
        answer(first)
        shutil.rmtree(backups, ignore_errors=True)
        folder = tmp_path / state / "first"
        win, section = draw(state, folder, backups, monkeypatch)
        for n, value in enumerate((not first, first)):
            answer(value)
            journal_changed(win)
            if value and state != "loading":
                # The reading the yes starts, done (it reads the device's files; the page's
                # listing may fail all the same).
                from openexpress import activity_journal

                activity_journal.path(UNIT_ID).unlink(missing_ok=True)  # another state's
                keep_journal(session_of(folder))
                win.session.journal_done = True
                journal_changed(win)
            shutil.rmtree(backups, ignore_errors=True)
            _fresh, fresh = draw(state, tmp_path / state / str(n), backups, monkeypatch)
            assert page_tree(section) == page_tree(fresh), (state, value)


@pytest.mark.parametrize("value", [None, False])
def test_show_more_clicked_then_drawn_again(tmp_path, monkeypatch, in_warsaw, value):
    """Show N More clicked, then the page drawn again (plugged in again, Try Again): as many
    rows as were shown, the button for the rest and the line where they were."""
    answer(value)
    many = {f"2026-08-{n % 28 + 1:02}-{n // 28:02}-00-00.fit": run(1) for n in range(61)}
    win, section = page_of(write_device(tmp_path / "device", many))
    click(section, "Show 30 More")
    shown = page_tree(section)
    assert says(section, "Show 1 More")
    win.load_activities()
    assert page_tree(section) == shown


# --- the page of what is new, drawn again in one window ------------------------------------------

def test_each_state_of_what_is_new_drawn_again_is_the_same(tmp_path):
    """Each state of its own drawn a second time in the same window (plugged in again, the page
    chosen again): the same tree."""
    answer(True)
    for state in SINCE_STATES:
        win, section = draw_since(state, tmp_path / state)
        first = page_tree(section)
        win.load_activities()
        assert page_tree(section) == first, state


def test_the_reading_tells_its_progress_then_the_page_comes(tmp_path):
    """The journal's first reading: its progress on the page as it goes; once done, the page a
    fresh window draws."""
    from gi.repository import Gtk

    from openexpress.gui import activities_since
    from openexpress.gui.activities_journal import journal_changed

    answer(True)
    win, section = draw_since("reading", tmp_path / "device")
    (bar,) = [w for g in section.groups for w in descendants(g)
              if isinstance(w, Gtk.ProgressBar)]
    assert bar.get_fraction() == pytest.approx(0.3)
    activities_since.progress(win, 7, 10)
    assert bar.get_fraction() == pytest.approx(0.7)
    keep_journal(win.session)  # the reading done
    journal_changed(win)
    assert win.activities_progress is None
    _fresh, fresh = page_of(session_of(tmp_path / "device"))
    assert page_tree(section) == page_tree(fresh)
    assert says(section, "1 new activity")


def test_none_new_then_new_at_the_next_connection(tmp_path, monkeypatch):
    """No new activity since the connection before; at the next connection one more on the
    device, read into the journal: the page a fresh window draws, with it."""
    from openexpress import history

    answer(True)
    folder = tmp_path / "device"
    session = write_device(folder, A_WEEK)
    keep_journal(session, before=datetime(2026, 10, 29, 20, 0))
    win, section = page_of(session)
    assert says(section, "No new activities")
    monkeypatch.setattr(history, "connection_key", lambda device, storage: "the next one")
    (folder / "GARMIN" / "Activity" / "2026-10-30-13-00-00.fit").write_bytes(
        track(datetime(2026, 10, 30, 13, 0), 1, 3, ZIGZAG, km=12.0, minutes=70, ascent=300))
    again = session_of(folder)
    win.session = again
    win._clear_device_pages()
    keep_journal(again)
    win.load_activities()
    _fresh, fresh = page_of(session_of(folder))
    assert page_tree(section) == page_tree(fresh)
    assert says(section, "1 new activity") and says(section, "Trail running")


def navigation(win):
    """The window's navigation with the device view at its root, as the window has it."""
    from gi.repository import Adw, Gtk

    win.navigation = Adw.NavigationView()
    win.device_view = Adw.NavigationPage(title="device", child=Gtk.Box())
    win.navigation.add(win.device_view)
    win.toast = lambda *args, **kwargs: None


def test_show_all_pushes_the_page_as_it_was_and_back(tmp_path, backups, monkeypatch,
                                                      in_warsaw):
    """Show All: the page as it was (its pattern), above the page of what is new; back, the
    page of what is new as it was; drawn again, the same."""
    pattern = json.loads(PATTERN.read_text(encoding="utf-8"))
    answer(True)
    win, section = draw("rows", tmp_path / "rows", backups, monkeypatch)
    navigation(win)
    before = page_tree(section)
    click(section, "Show All")
    page = win.navigation.get_visible_page()
    assert page.get_tag() == "activities-all" and page.get_title() == "Activities"
    assert [widget(g) for g in win.activities_sheet.groups] == pattern["rows"]["groups"]
    win.navigation.pop()
    assert win.navigation.get_visible_page() is win.device_view
    assert page_tree(section) == before
    win.load_activities()
    assert page_tree(section) == before


def test_show_more_on_the_page_of_show_all(tmp_path):
    """More than two pages of activities: Show 30 More on the page Show All pushed adds them
    there; the page of what is new beneath it stays as it was, also drawn again."""
    from gi.repository import Gtk

    answer(True)
    many = {f"2026-08-{n % 28 + 1:02}-{n // 28:02}-00-00.fit": run(1) for n in range(61)}
    win, section = page_of(write_device(tmp_path / "device", many))
    navigation(win)
    before = page_tree(section)
    click(section, "Show All")
    sheet = win.activities_sheet
    (more,) = [w for g in sheet.groups for w in descendants(g)
               if isinstance(w, Gtk.Button) and w.get_label() == "Show 30 More"]
    more.emit("clicked")
    labels = [w.get_label() for g in sheet.groups for w in descendants(g)
              if isinstance(w, Gtk.Button) and w.get_label()]
    assert "Show 1 More" in labels
    assert page_tree(section) == before
    win.load_activities()
    assert page_tree(section) == before


def test_the_page_of_show_all_goes_with_the_device_view(tmp_path, backups, monkeypatch,
                                                       in_warsaw):
    """The device gone or its pages drawn anew: the page Show All pushed goes, as the page of
    an activity does."""
    from openexpress.gui import activity_page

    answer(True)
    win, section = draw("rows", tmp_path / "rows", backups, monkeypatch)
    navigation(win)
    click(section, "Show All")
    activity_page.leave(win)
    assert win.navigation.get_visible_page() is win.device_view


@pytest.mark.parametrize("value", [None, False, True])
def test_after_a_sync_only_the_page_of_what_is_new_is_drawn_again(tmp_path, value):
    """A sync done (the window's, or one seen in last_sync.json): the page of what is new is
    drawn again (what was uploaded); the page as it was is left as it is (its scroll, its rows
    shown)."""
    from openexpress.gui.activities_journal import synced

    answer(value)
    win, section = page_of(write_device(tmp_path / "device", A_WEEK))
    before = list(section.groups)
    synced(win)
    assert (list(section.groups) == before) is (value is not True)
