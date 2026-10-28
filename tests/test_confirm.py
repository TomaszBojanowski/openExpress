"""The one question and the one message of the window (gui/window_tasks.py: confirm, inform).
A question closed in any way is cancelled and runs nothing; an answer runs only its own
work. The dialogs are shown in a real window, their buttons clicked and Escape is
Adw.Dialog.close(): libadwaita then says "closed", and "response" after it, once (1.9, seen
on Xvfb 28.10.2026). A "closed" said by a test alone gives the close response as well, so
the tests do not say it themselves."""

import logging

import pytest
from test_forget_window import _button_labelled, _turn_until

pytestmark = pytest.mark.needs_display

LOGGER = "openexpress.gui"


@pytest.fixture
def host(monkeypatch):
    """A real window the dialogs are shown over; host.shown: those shown, newest last;
    host.held_when_shown: what `held` of their parent was as each was shown."""
    import types

    from gi.repository import Adw, Gtk

    window = Gtk.Window()
    window.present()
    parent = types.SimpleNamespace(held=None)
    shown, held = [], []
    present = Adw.AlertDialog.present

    def show(self, p):
        shown.append(self)
        held.append(getattr(p, "held", "-"))
        present(self, window)

    monkeypatch.setattr(Adw.AlertDialog, "present", show)
    yield types.SimpleNamespace(parent=parent, shown=shown, held_when_shown=held)
    for d in shown:
        d.force_close()
    window.destroy()


def click(dialog, words):
    assert _turn_until(dialog.get_mapped)
    _button_labelled(dialog, words).emit("clicked")
    assert _turn_until(lambda: not dialog.get_mapped())


def escape(dialog):
    assert _turn_until(dialog.get_mapped)
    dialog.close()
    assert _turn_until(lambda: not dialog.get_mapped())


def question(parent, ran, **kw):
    from openexpress.gui.window_tasks import Answer, confirm

    return confirm(parent, "test-question", "Do it?", "It does it.",
                   Answer("one", "One", lambda: ran.append("one")),
                   Answer("two", "Two", lambda: ran.append("two"), look="destructive"), **kw)


def dialog_lines(caplog):
    return [m for m in caplog.messages if m.startswith("dialog ")]


@pytest.mark.parametrize("closed_by", ["Escape", "Cancel"])
def test_a_question_escaped_or_cancelled_runs_nothing(host, caplog, closed_by):
    ran = []
    with caplog.at_level(logging.INFO, logger=LOGGER):
        d = question(host.parent, ran)
        escape(d) if closed_by == "Escape" else click(d, "Cancel")
    assert ran == []
    assert d.get_close_response() == "cancel"
    assert dialog_lines(caplog) == ["dialog test-question: cancelled"]


@pytest.mark.parametrize(("words", "chosen"), [("One", "one"), ("Two", "two")])
def test_an_answer_runs_only_its_own(host, caplog, words, chosen):
    ran = []
    with caplog.at_level(logging.INFO, logger=LOGGER):
        click(question(host.parent, ran), words)
    assert ran == [chosen]
    assert dialog_lines(caplog) == [f"dialog test-question: {chosen}"]


@pytest.mark.parametrize("response", ["cancel", "close", "other"])
def test_any_other_response_runs_nothing(host, response):
    ran = []
    question(host.parent, ran).emit("response", response)
    assert ran == []


def test_an_answer_not_enabled_runs_nothing_until_it_is(host):
    from openexpress.gui.window_tasks import Answer, confirm

    ran = []
    d = confirm(host.parent, "test-question", "Do it?", "",
                Answer("go", "Go", lambda: ran.append("go"), enabled=False))
    assert d.get_response_enabled("go") is False
    d.emit("response", "go")
    assert ran == []
    d.set_response_enabled("go", True)
    click(d, "Go")
    assert ran == ["go"]


def test_the_buttons_as_asked(host):
    from gi.repository import Adw, Gtk

    extra = Gtk.Label(label="below")
    d = question(host.parent, [], cancel="Later", default="two", extra=extra, wide=True)
    assert d.get_response_label("cancel") == "Later"
    assert d.get_response_label("one") == "One"
    assert d.get_response_appearance("one") == Adw.ResponseAppearance.DEFAULT
    assert d.get_response_appearance("two") == Adw.ResponseAppearance.DESTRUCTIVE
    assert d.get_default_response() == "two"
    assert d.get_extra_child() is extra
    if hasattr(d, "get_prefer_wide_layout"):
        assert d.get_prefer_wide_layout() is True
    plain = question(host.parent, [])
    assert plain.get_response_label("cancel") == "Cancel"
    assert plain.get_default_response() is None


@pytest.mark.parametrize("answers", [[], ["cancel"], ["one", "one"]])
def test_a_question_without_sound_answers_is_refused(host, answers):
    from openexpress.gui.window_tasks import Answer, confirm

    with pytest.raises(ValueError):
        confirm(host.parent, "test-question", "Do it?", "",
                *(Answer(a, a, lambda: None) for a in answers))
    assert host.shown == []


def test_a_default_that_is_no_answer_is_refused(host):
    with pytest.raises(ValueError):
        question(host.parent, [], default="three")
    assert host.shown == []


def test_the_window_holds_the_question_while_it_is_open(host):
    from openexpress.gui.window_tasks import Answer, confirm

    parent, seen = host.parent, {}
    first = confirm(parent, "test-question", "Do it?", "",
                    Answer("go", "Go", lambda: seen.update(held=parent.held)), keep_as="held")
    assert host.held_when_shown == [first]  # held before it is shown
    assert parent.held is first
    click(first, "Go")
    assert parent.held is None and seen == {"held": None}  # let go before the answer
    second = question(parent, [], keep_as="held")
    third = question(parent, [], keep_as="held")
    escape(second)  # an older one closing does not let go of the newer one
    assert parent.held is third


@pytest.mark.parametrize("closed_by", ["Escape", "OK"])
def test_a_message_runs_then_however_it_is_closed(host, caplog, closed_by):
    from openexpress.gui.window_tasks import inform

    after = []
    with caplog.at_level(logging.INFO, logger=LOGGER):
        d = inform(host.parent, "test-message", "Done", "It is done.",
                   then=lambda: after.append("then"))
        assert d.get_close_response() == "ok"
        assert d.get_response_label("ok") == "OK"
        escape(d) if closed_by == "Escape" else click(d, "OK")
    assert after == ["then"]
    assert dialog_lines(caplog) == ["dialog test-message: ok"]


def test_a_message_with_words_of_its_own(host):
    from openexpress.gui.window_tasks import inform

    d = inform(host.parent, "test-message", "Done", "", label="Close")
    assert d.get_response_label("ok") == "Close"
    click(d, "Close")
