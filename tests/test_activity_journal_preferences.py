"""The activity journal in the preferences and in Forget Device: the group "Activity Journal"
right before "Activity Maps" with its switch (on is the yes, the real state shown: off until
answered) and Delete… (every device's journal, after a question; off while there is none);
a change that cannot be saved puts the switch back; turned off, a toast says what it holds
stays. Forget Device says, with its count, that the device's journal goes too.
Made-up files only."""

import json
import types
from datetime import datetime

import pytest
from fakes import descendants, window

pytestmark = [pytest.mark.needs_display, pytest.mark.journal_answer]

UNIT = 1234567890
SEEN = datetime(2026, 11, 25, 18, 0)


def answer(value) -> None:
    from openexpress.settings import Settings

    s = Settings.load()
    s.activity_journal = value
    s.save()


def answer_saved():
    from openexpress import paths

    return json.loads(paths.settings_file().read_text()).get("activity_journal", "none")


def journal_with(unit: int, count: int, twice: bool = False) -> None:
    """A journal of `unit` with `count` made-up activities; `twice`: each line written twice,
    as an older version could write it."""
    from openexpress import activity_journal

    where = activity_journal.path(unit)
    where.parent.mkdir(parents=True, exist_ok=True)
    with where.open("a") as f:
        for n in range(count):
            line = json.dumps({"v": 1, "kind": "activity", "key": f"{n}-1",
                               "file": {"name": f"{n}.fit", "size": 1}, "from": "device",
                               "start": 1_800_000_000 + n}) + "\n"
            f.write(line * (2 if twice else 1))


def the_group(**changes):
    """The group of the preferences, in a stand-in window that records its errors and
    toasts."""
    from openexpress.gui.preferences import journal_group

    win = window(errors=[], toasts=[], **changes)
    win.error = lambda title, text, private=False: win.errors.append((title, text))
    win.toast = lambda text, on_preferences=False: win.toasts.append(text)
    return win, journal_group(win)


def rows(group):
    from gi.repository import Adw

    return [w for w in descendants(group) if isinstance(w, Adw.PreferencesRow)]


def delete_button(group):
    from gi.repository import Gtk

    (button,) = [w for w in descendants(group)
                 if isinstance(w, Gtk.Button) and w.get_label() == "Delete…"]
    return button


def test_the_group_and_its_rows():
    from gi.repository import Adw

    _win, group = the_group()
    assert group.get_title() == "Activity Journal"
    switch, delete = rows(group)
    assert isinstance(switch, Adw.SwitchRow)
    assert switch.get_title() == "Keep an Activity Journal"
    assert switch.get_subtitle() == ("A summary and the shape of each activity stay on this "
                                     "computer, also after the device deletes its file")
    assert not switch.get_active()  # not answered: off, the real state
    assert delete.get_title() == "Delete the Activity Journal"
    assert "destructive-action" in delete_button(group).get_css_classes()


def test_it_comes_right_before_the_maps():
    """In the preferences: the journal's group added right before Activity Maps."""
    import inspect

    from openexpress.gui import preferences

    lines = [line.strip() for line in
             inspect.getsource(preferences.Preferences.show_preferences).splitlines()]
    assert lines.index("page.add(journal_group(self))") == \
        lines.index("page.add(maps_group(self))") - 1


@pytest.mark.parametrize("value,shown", [(None, False), (False, False), (True, True)])
def test_the_switch_shows_the_answer(value, shown):
    answer(value)
    _win, group = the_group()
    assert rows(group)[0].get_active() is shown


def test_turned_on_it_is_the_yes(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="openexpress")
    win, group = the_group()
    rows(group)[0].set_active(True)
    assert answer_saved() is True and win.errors == [] and win.toasts == []
    assert "activity journal: on" in caplog.text


def test_turned_off_what_it_holds_stays_and_a_toast_says_so():
    answer(True)
    journal_with(UNIT, 3)
    win, group = the_group()
    rows(group)[0].set_active(False)
    assert answer_saved() is False
    assert win.toasts == ["Activity journal turned off; what it holds stays until you delete it"]
    from openexpress import activity_journal

    assert activity_journal.count(activity_journal.path(UNIT)) == 3


def test_a_change_not_saved_puts_the_switch_back(monkeypatch):
    from openexpress.settings import Settings

    win, group = the_group()

    def refused(self):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Settings, "save", refused)
    switch = rows(group)[0]
    switch.set_active(True)
    assert not switch.get_active()
    assert [title for title, _text in win.errors] == ["Setting Not Saved"]


def test_delete_is_off_while_there_is_nothing_to_delete():
    _win, group = the_group()
    assert not delete_button(group).get_sensitive()


def test_delete_asks_then_deletes_every_device_s(in_the_window):
    from openexpress import activity_journal

    journal_with(UNIT, 3)
    journal_with(987654321, 2)
    asked = []
    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw:
                  asked.append((name, heading, body, answers, kw)))
    win, group = the_group()
    button = delete_button(group)
    assert button.get_sensitive()
    button.emit("clicked")
    ((name, heading, body, answers, kw),) = asked
    assert heading == "Delete the Activity Journal?"
    assert body == ("The journal of 5 activities is deleted from this computer. Activities the "
                    "devices no longer keep cannot be read again.")
    (delete,) = answers
    assert (delete.label, delete.look, kw["default"]) == ("Delete", "destructive", "cancel")
    assert len(activity_journal.journals()) == 2  # nothing before the answer
    delete.run()
    assert activity_journal.journals() == []
    assert win.toasts == ["Activity journal deleted"]
    assert not button.get_sensitive()


def test_a_journal_not_deleted_is_said(in_the_window, monkeypatch):
    from openexpress import activity_journal

    journal_with(UNIT, 1)
    asked = []
    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw:
                  asked.append(answers))
    win, group = the_group()
    delete_button(group).emit("clicked")

    def refused(found):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(activity_journal, "delete", refused)
    asked[0][0].run()
    assert [title for title, _text in win.errors] == ["Activity Journal Not Deleted"]


# --- Forget Device -----------------------------------------------------------------------------

def forget_window(asked):
    from openexpress import known_devices
    from openexpress.gui.forget_device import ForgetDevice

    known_devices.remember(UNIT, "fēnix 8", "006-B4532-00", "2420", "mtp")
    win = window(_remembered_unit=UNIT, _remembered_from_account=False, session=None,
                 connected=types.SimpleNamespace(devices=[]), errors=[])
    win.error = lambda title, text, private=False: win.errors.append((title, text))
    for name, member in vars(ForgetDevice).items():
        if callable(member):
            setattr(win, name, types.MethodType(member, win))
    return win


@pytest.fixture
def asked(monkeypatch):
    from gi.repository import Adw

    questions = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda self, parent: questions.append(self))
    return questions


def test_forget_device_says_its_journal_goes_too(asked):
    journal_with(UNIT, 3)
    forget_window(asked).ask_forget()
    (d,) = asked
    assert d.get_body().endswith("\n\nIts activity journal, 3 activities, is deleted too.")


def test_forget_device_without_a_journal_says_nothing_of_it(asked):
    forget_window(asked).ask_forget()
    (d,) = asked
    assert "activity journal" not in d.get_body()


# --- the counts are of activities (keys), not of lines ---------------------------------------

def test_the_question_counts_activities_not_lines(in_the_window):
    """A journal an older write left with each line twice: the question counts 3, not 6."""
    journal_with(UNIT, 3, twice=True)
    asked = []
    in_the_window("confirm", lambda parent, name, heading, body, *answers, **kw:
                  asked.append(body))
    _win, group = the_group()
    button = delete_button(group)
    assert button.get_sensitive()
    button.emit("clicked")
    assert asked == [("The journal of 3 activities is deleted from this computer. Activities "
                      "the devices no longer keep cannot be read again.")]


def test_forget_device_counts_activities_not_lines(asked):
    journal_with(UNIT, 3, twice=True)
    forget_window(asked).ask_forget()
    (d,) = asked
    assert d.get_body().endswith("\n\nIts activity journal, 3 activities, is deleted too.")


def test_deleting_counts_activities_not_lines():
    from openexpress import activity_journal

    journal_with(UNIT, 3, twice=True)
    assert activity_journal.delete(activity_journal.journals()) == 3
