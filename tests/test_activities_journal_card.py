"""The card of the activity journal on the Activities page and its line at the bottom: the card
is there until it is answered, and only its two buttons answer (the yes saved, the journal of
the open device read; the no saved, the line instead); leaving the page, drawing it again or
unplugging the device saves nothing; a save that fails leaves the card and says why; the line
stays at the very bottom when more rows come, and its button opens the preferences; drawn
again in one window (plugged in again, Try Again, the answer changed) the page is drawn whole.
Made-up files only; a folder stands for the device."""

import json
import logging
import os
import re
import types
from pathlib import Path

import pytest
from conftest import DEVICE_XML
from fakes import FIT_EPOCH, descendants, fit_activity, page_window, window_methods

pytestmark = [pytest.mark.needs_display, pytest.mark.journal_answer]

PAGE_METHODS = ["load_activities", "show_activities", "load_section"]


def the_page(tmp_path, count=3, ownership_error=None):
    """The Activities page of a folder as a watch with `count` runs, drawn: the stand-in and
    the page. The stand-in records the errors it says and the preferences it opens."""
    from datetime import datetime

    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    root = tmp_path / "device"
    (root / "GARMIN" / "Activity").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(DEVICE_XML)
    for n in range(count):
        path = root / "GARMIN" / "Activity" / f"2026-09-{n + 1:02}.fit"
        start = int(datetime(2026, 9, 1).timestamp()) + n * 3_600 - FIT_EPOCH
        path.write_bytes(fit_activity(start, records=2))
        os.utime(path, (1_000 + n, 1_000 + n))
    storage = LocalStorage(root)
    win, section = page_window("activities")
    window_methods(win, PAGE_METHODS)
    win.progress = lambda _step: None
    win.errors, win.preferences_opened = [], []
    win.error = lambda title, text, private=False: win.errors.append((title, text))
    win.show_preferences = lambda: win.preferences_opened.append(True)
    win.session = types.SimpleNamespace(device=parse(storage.read("GARMIN/GarminDevice.xml")),
                                        storage=storage, ownership_error=ownership_error,
                                        journal_read=False, mass_storage=False)
    win.load_activities()
    return win, section


def labels(section) -> list[str]:
    from gi.repository import Gtk

    return [w.get_label() for g in section.groups for w in descendants(g)
            if isinstance(w, Gtk.Label)]


def button(section, words: str):
    from gi.repository import Gtk

    found = [w for g in section.groups for w in descendants(g)
             if isinstance(w, Gtk.Button) and w.get_label() == words]
    assert len(found) == 1, words
    return found[0]


def answer_saved():
    from openexpress import paths

    return json.loads(paths.settings_file().read_text()).get("activity_journal", "none")


@pytest.fixture
def reading(in_the_window):
    """The readings of the journal the card starts (read_journal), recorded."""
    started = []
    in_the_window("read_journal", lambda win, again=False: started.append(win))
    return started


def test_fresh_data_of_the_program_without_the_answer_show_the_card(tmp_path, reading):
    """The tests' own no (conftest.journal_answered_no) is the tests' only: in the program no
    settings file, and one without the answer, are no answer: the card, never the line of a
    no."""
    from openexpress import activity_journal, paths

    assert not paths.settings_file().exists()
    assert activity_journal.answered() is None  # it makes the file, without the answer
    assert "activity_journal" not in json.loads(paths.settings_file().read_text())
    for _drawn in range(2):
        win, section = the_page(tmp_path / str(_drawn))
        assert "Keep an Activity Journal?" in labels(section)
        assert win.activities_note is None
        assert "An activity journal shows what is new since the last connection" not in labels(section)


def test_keep_a_journal_saves_the_yes_and_reads_the_device(tmp_path, reading, caplog):
    caplog.set_level(logging.INFO, logger="openexpress")
    win, section = the_page(tmp_path)
    assert "Keep an Activity Journal?" in labels(section) and reading == []
    button(section, "Keep a Journal").emit("clicked")
    assert answer_saved() is True
    assert "Keep an Activity Journal?" not in labels(section)
    assert reading == [win]
    assert "activity journal: on" in caplog.text


def test_not_now_saves_the_no_and_the_line_comes(tmp_path, reading, caplog):
    caplog.set_level(logging.INFO, logger="openexpress")
    win, section = the_page(tmp_path)
    button(section, "Not Now").emit("clicked")
    assert answer_saved() is False and reading == []
    assert "Keep an Activity Journal?" not in labels(section)
    assert section.groups[-1] is win.activities_note
    assert "An activity journal shows what is new since the last connection" in labels(section)
    assert "activity journal: off" in caplog.text


def test_until_a_button_is_clicked_nothing_is_saved(tmp_path, reading):
    from openexpress import paths

    win, section = the_page(tmp_path)
    win.load_activities()  # the page left and chosen again
    assert "Keep an Activity Journal?" in labels(section)
    win.session = None  # unplugged
    win.show_activities(win.activities)
    assert not paths.settings_file().exists() or answer_saved() == "none"
    assert reading == []


def test_a_save_that_fails_leaves_the_card_and_says_why(tmp_path, reading, monkeypatch):
    from openexpress.settings import Settings

    win, section = the_page(tmp_path)

    def refused(self):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Settings, "save", refused)
    button(section, "Keep a Journal").emit("clicked")
    assert [title for title, _text in win.errors] == ["Setting Not Saved"]
    assert "Keep an Activity Journal?" in labels(section) and reading == []


def test_the_line_stays_at_the_very_bottom_when_more_rows_come(tmp_path, reading):
    from openexpress.settings import Settings

    s = Settings.load()
    s.activity_journal = False
    s.save()
    win, section = the_page(tmp_path, count=31)
    assert section.groups[-1] is win.activities_note
    button(section, "Show 1 More").emit("clicked")
    assert win.activities_more is None
    assert section.groups[-1] is win.activities_note


def test_the_line_s_button_opens_the_preferences(tmp_path, reading):
    from openexpress.settings import Settings

    s = Settings.load()
    s.activity_journal = False
    s.save()
    win, section = the_page(tmp_path)
    button(section, "Preferences").emit("clicked")
    assert win.preferences_opened == [True]


# --- the page drawn again in one window ------------------------------------------------------
# Plugged in again, Try Again, another device, a changed answer: the page is drawn anew in the
# same window, and nothing of the drawing before it (its line, its groups) may stand in its way.

def answer(value) -> None:
    from openexpress.settings import Settings

    s = Settings.load()
    s.activity_journal = value
    s.save()


LINE = ["An activity journal shows what is new since the last connection", "Preferences"]
OTHER_ACCOUNT = ["The activity journal is not kept for a device on another Garmin Connect account"]


def texts_of(group) -> list[str]:
    """The words a group of the page shows, in order."""
    from gi.repository import Gtk

    return [w.get_label() for w in descendants(group)
            if isinstance(w, Gtk.Label) and w.get_label()]


def unexpected(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]


def test_the_no_drawn_again_with_more_than_a_page(tmp_path, reading, caplog):
    answer(False)
    win, section = the_page(tmp_path, count=31)
    win.load_activities()  # plugged in again, or the page chosen again
    assert unexpected(caplog) == []
    assert section.stack.get_visible_child_name() == "content"
    assert section.groups[-1] is win.activities_note
    assert texts_of(win.activities_note) == LINE
    button(section, "Show 1 More")


def test_the_switch_turned_off_and_on_again_with_the_page_open(tmp_path, reading, caplog):
    """True, false, true again in the preferences while the Activities page of more than a page
    of activities is shown: drawn again each time; after the no the page as it was, its first
    activities read and the line at the bottom; after the yes what is new, while the journal
    reads the device again."""
    from gi.repository import Adw

    from openexpress.gui.preferences import journal_group

    answer(True)
    win, section = the_page(tmp_path, count=31)
    win.toast = lambda text, on_preferences=False: None
    (switch,) = [w for w in descendants(journal_group(win)) if isinstance(w, Adw.SwitchRow)]
    switch.set_active(False)
    assert section.groups[-1] is win.activities_note and texts_of(win.activities_note) == LINE
    button(section, "Show 1 More")
    switch.set_active(True)
    assert unexpected(caplog) == [] and win.errors == []
    assert win.activities_note is None
    assert all(texts_of(g) != LINE for g in section.groups)
    assert [texts_of(g) for g in section.groups] == [["Reading the activities of this device…"]]
    assert reading == [win]


def test_the_line_of_another_account_drawn_again(tmp_path, reading, caplog):
    from openexpress.connect import NotAccountDevice

    answer(True)
    win, section = the_page(tmp_path, count=31,
                            ownership_error=NotAccountDevice("made up: on another account"))
    win.load_activities()
    assert unexpected(caplog) == []
    assert section.groups[-1] is win.activities_note
    assert texts_of(win.activities_note) == OTHER_ACCOUNT


def test_show_more_after_the_page_was_drawn_again(tmp_path, reading, caplog):
    answer(False)
    win, section = the_page(tmp_path, count=32)
    win.load_activities()
    button(section, "Show 2 More").emit("clicked")
    assert unexpected(caplog) == []
    assert win.activities_more is None
    assert section.groups[-1] is win.activities_note


def test_try_again_after_the_page_failed_draws_it_anew(tmp_path, reading, caplog, in_the_window):
    """A drawing that fails once (an error of the window's own, injected) leaves nothing of it
    to the next: Try Again draws the whole page, its line at the very bottom."""
    from openexpress.gui import activities_page

    answer(False)
    win, section = the_page(tmp_path, count=31)
    plain, failed = activities_page.activity_row, []

    def once(win, read):
        if not failed:
            failed.append(True)
            raise RuntimeError("made up: a row that cannot be drawn")
        return plain(win, read)
    in_the_window("activity_row", once)
    win.load_activities()
    assert section.stack.get_visible_child_name() == "error"
    assert len(unexpected(caplog)) == 1
    win.load_section("activities", force=True)  # Try Again
    assert len(unexpected(caplog)) == 1
    assert section.stack.get_visible_child_name() == "content"
    assert section.groups[-1] is win.activities_note and texts_of(win.activities_note) == LINE
    button(section, "Show 1 More")


# The modules of the Activities page and of an activity's page, and the words they share.
PAGES = ("activities_journal.py", "activities_since.py", "activities_page.py", "activity_page.py",
         "activity_overview.py", "activity_zones.py", "activity_chart.py", "activity_laps.py",
         "activity_details.py", "activity_colours.py", "activity_map.py", "words.py")
NOT_YET = re.compile(r"\b(weeks?|years?|calendar|records)\b", re.IGNORECASE)


def test_no_text_of_the_activities_promises_what_is_not_there():
    """The pages of the activities show what is new since the connection before this one and
    each activity, nothing of weeks or a year: none of their texts promises weeks, a year,
    records or a calendar."""
    template = (Path(__file__).resolve().parent.parent / "po" / "openexpress.pot").read_text(
        encoding="utf-8")
    promised = []
    for block in template.split("\n\n"):
        where = " ".join(re.findall(r"^#: (.*)$", block, re.MULTILINE))
        if "msgid " not in block or not any(f"gui/{page}" in where for page in PAGES):
            continue
        said = "".join(re.findall(r'"(.*)"', block[block.index("msgid "):].split("msgstr")[0]))
        if NOT_YET.search(said):
            promised.append(said)
    assert promised == []
