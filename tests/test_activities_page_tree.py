"""The widget tree of the Activities page in each of its states (types, texts, CSS classes,
order), against a pattern the page drew before the activity journal existed
(tests/data/activities_page_tree.json). Whatever the journal adds to the page is checked
against this pattern: nothing of the page as it was may change.
Made-up files only; a folder stands for the device. To draw the pattern again (only on the
commit it comes from, e.g. after an update of libadwaita changed its inner widgets):
OPENEXPRESS_WRITE_PAGE_TREE=1 python3 -m pytest -q -p no:cacheprovider tests/test_activities_page_tree.py
"""

import json
import os
import time
import types
from datetime import datetime
from pathlib import Path

import pytest
from conftest import DEVICE_XML
from fakes import FIT_EPOCH, fit_activity, page_window, window_methods

pytestmark = pytest.mark.needs_display

PATTERN = Path(__file__).parent / "data" / "activities_page_tree.json"
WRITE = "OPENEXPRESS_WRITE_PAGE_TREE"
PAGE_METHODS = ["load_activities", "show_activities"]
WARSAW = (624_000_000, 250_000_000)  # a made-up position, in semicircles
UNIT_ID = 1234567890  # the made-up watch of DEVICE_XML
STATES = ("loading", "error", "keeps-none", "none", "rows", "more", "only-in-backups",
          "drive", "other-account", "logged-out")


def unix(*when) -> int:
    return int(datetime(*when).timestamp())


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
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    activity = folder / "GARMIN" / "Activity"
    activity.mkdir(parents=True)
    (folder / "GARMIN" / "GarminDevice.xml").write_text(xml)
    for n, (name, data) in enumerate(files.items()):
        (activity / name).write_bytes(data)
        os.utime(activity / name, (1_000 + n, 1_000 + n))
    storage = LocalStorage(folder)
    return types.SimpleNamespace(device=parse(storage.read("GARMIN/GarminDevice.xml")),
                                 storage=storage, logged_in=True, ownership_error=None,
                                 mass_storage=False)


def write_backup(backups: Path, files: dict[str, bytes]) -> None:
    """A backup of the made-up watch, made on 2 October 2025, with these activity files."""
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
    files = {"none": {}, "only-in-backups": {},
             "more": {f"2026-09-{n:02}-07-00-00.fit": run(1) for n in range(1, 32)}}
    xml = (DEVICE_XML.replace("<Name>FIT_TYPE_4</Name>", "<Name>FIT_TYPE_999</Name>")
           if state == "keeps-none" else DEVICE_XML)
    win.session = write_device(folder, files.get(state, A_WEEK), xml)
    if state in ("rows", "only-in-backups"):
        write_backup(backups, IN_A_BACKUP)
    if state == "drive":
        win.session.mass_storage = True
    if state == "other-account":
        from openexpress.connect import OwnershipError

        win.session.ownership_error = OwnershipError("made up: on another account")
    if state == "logged-out":
        win.session.logged_in = False
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


@pytest.fixture
def drawn(tmp_path, backups, monkeypatch, in_warsaw):
    """Every state of the page drawn: {state: its tree}."""
    found = {}
    for state in STATES:
        _win, section = draw(state, tmp_path / state, backups, monkeypatch)
        found[state] = page_tree(section)
        if state in ("rows", "only-in-backups"):
            import shutil

            shutil.rmtree(backups)
    return found


def test_each_state_of_the_page_is_the_pattern(drawn):
    if os.environ.get(WRITE):
        PATTERN.write_text(json.dumps(drawn, indent=1, ensure_ascii=False) + "\n",
                           encoding="utf-8")
        pytest.fail(f"pattern written ({WRITE}), nothing compared")
    pattern = json.loads(PATTERN.read_text(encoding="utf-8"))
    assert sorted(pattern) == sorted(STATES)
    for state in STATES:
        assert drawn[state] == pattern[state], state


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
