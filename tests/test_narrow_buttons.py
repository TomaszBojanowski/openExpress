"""The label of a button beside the text of a row or of a group's header breaks into lines in the
folded window, so that the row's title and subtitle keep their room; unfolded it is one line, as
it always was. One place makes it so for every such button (window_tasks.wraps_when_folded):
the buttons of _button, "Clear All" in the preferences and "Show All" on the page Device Status.
A button keeps its size while its task runs and after it, folded or not."""

import pytest
from fakes import iterate as _iterate
from test_narrow_window import (  # noqa: F401 - the fixture
    DEVICE_PAGES,
    NARROWEST,
    _fill,
    narrow_window,
)

WIDE = 1280

pytestmark = pytest.mark.needs_display


def _widgets(root):
    todo = [root]
    while todo:
        widget = todo.pop()
        yield widget
        child = widget.get_first_child()
        while child is not None:
            todo.append(child)
            child = child.get_next_sibling()


def _labelled_buttons(root):
    """The buttons with a label (or the unseen copy of it while their task runs)."""
    from gi.repository import Gtk

    from openexpress.gui.window_tasks import _label_of

    return [w for w in _widgets(root) if isinstance(w, Gtk.Button) and _label_of(w) is not None]


def _beside_text(button) -> bool:
    from openexpress.gui.window_tasks import _beside_text as beside

    return beside(button)


def _shown(win, page: str) -> str:
    """`page` filled and in view (its buttons mapped), as the window shows it."""
    key = _fill(win, page)
    win.navigation.set_animate_transitions(False)
    if win.navigation.get_visible_page() is not win.device_view:
        win.navigation.push(win.device_view)
    win.select_section(key)
    _iterate(0.2)
    from gi.repository import Adw

    for widget in list(_widgets(win.sections[key].page)):
        if isinstance(widget, Adw.ExpanderRow):
            widget.set_expanded(True)  # as the user opens it: its rows come into view
    _iterate(0.6)  # laid out, then the labels decided and laid out again
    return key


def _reinstall_buttons(win):
    from openexpress.i18n import _

    _shown(win, "updates-older")
    found = [b for b in _labelled_buttons(win.sections["updates"].page)
             if b.get_label() == _("Reinstall") and _beside_text(b)]
    assert found
    return found


def _wrapped(button) -> tuple[bool, object]:
    from openexpress.gui.window_tasks import _label_of

    label = _label_of(button)
    return label.get_wrap(), label.get_justify()


def _lines(button) -> int:
    from openexpress.gui.window_tasks import _label_of

    return _label_of(button).get_layout().get_line_count()


@pytest.mark.parametrize("language", ["en", "pl"])
def test_a_rows_button_breaks_its_label_in_the_folded_window_only(narrow_window, language):
    """"Zainstaluj ponownie" breaks beside each component (it never fits beside its text at
    360 px), "Reinstall" is one word; unfolded, every label is one line, as GTK makes it."""
    from gi.repository import Gtk

    from openexpress.i18n import _

    folded = narrow_window(language, width=NARROWEST)
    for button in _reinstall_buttons(folded):
        assert button.get_label() == _("Reinstall")  # the label itself does not change
        if language == "pl":
            assert _wrapped(button) == (True, Gtk.Justification.CENTER) and _lines(button) == 2
        else:
            assert _lines(button) == 1
    wide = narrow_window(language, width=WIDE)
    for button in _reinstall_buttons(wide):
        assert _wrapped(button) == (False, Gtk.Justification.LEFT)


def test_the_label_follows_the_window_as_it_folds_and_unfolds(narrow_window):
    from gi.repository import Gtk

    win = narrow_window("pl", width=NARROWEST)
    buttons = _reinstall_buttons(win)
    win.split.set_collapsed(False)
    _iterate(0.3)
    assert {_wrapped(b) for b in buttons} == {(False, Gtk.Justification.LEFT)}
    win.split.set_collapsed(True)
    _iterate(0.3)
    assert {_wrapped(b) for b in buttons} == {(True, Gtk.Justification.CENTER)}


@pytest.mark.parametrize("width", [NARROWEST, WIDE])
@pytest.mark.parametrize("language", ["en", "pl"])
def test_a_button_keeps_its_size_while_its_task_runs_and_after(narrow_window, language, width):
    """Normal, working (the spinner over the label's unseen copy), and put back: the same size,
    and the same breaking of its label."""
    from openexpress.gui.window_tasks import show_working

    win = narrow_window(language, width=width)
    button = _reinstall_buttons(win)[0]
    sizes = [(button.get_width(), button.get_height())]
    wrapped = [_wrapped(button)]
    restore = show_working(button)
    _iterate(0.2)
    sizes.append((button.get_width(), button.get_height()))
    wrapped.append(_wrapped(button))
    restore(True)
    _iterate(0.2)
    sizes.append((button.get_width(), button.get_height()))
    wrapped.append(_wrapped(button))
    assert len(set(sizes)) == 1 and sizes[0][0] > 0
    assert len(set(wrapped)) == 1
    if language == "pl":  # "Zainstaluj ponownie" breaks in the folded window only
        assert wrapped[0][0] is (width == NARROWEST)


def test_a_window_unfolded_while_a_task_runs_gives_back_a_label_of_one_line(narrow_window):
    from gi.repository import Gtk

    from openexpress.gui.window_tasks import show_working

    win = narrow_window("pl", width=NARROWEST)
    button = _reinstall_buttons(win)[0]
    restore = show_working(button)
    win.split.set_collapsed(False)
    _iterate(0.1)
    assert _wrapped(button) == (False, Gtk.Justification.LEFT)  # the unseen copy
    restore(True)
    assert _wrapped(button) == (False, Gtk.Justification.LEFT)


@pytest.mark.parametrize("language", ["en", "pl"])
def test_unfolded_a_buttons_label_is_as_gtk_makes_it(narrow_window, language):
    """The wide window is unchanged: the label of each such button has the wrapping and the
    justification of a plain button's, and the same width."""
    from gi.repository import Gtk

    from openexpress.gui.window_tasks import _label_of

    win = narrow_window(language, width=WIDE)
    for button in _reinstall_buttons(win):
        plain = Gtk.Button(label=button.get_label())
        mine, theirs = _label_of(button), plain.get_child()
        assert (mine.get_wrap(), mine.get_justify()) == (theirs.get_wrap(), theirs.get_justify())
        assert button.measure(Gtk.Orientation.HORIZONTAL, -1)[:2] == plain.measure(
            Gtk.Orientation.HORIZONTAL, -1)[:2]


def test_every_labelled_button_beside_a_rows_text_folds(narrow_window):
    """On every page of the device view: each button with a label in a row or in a group's
    header goes through the one place, and nothing else does (a switch, a button with only an
    icon)."""
    from gi.repository import Gtk

    from openexpress.gui.window_tasks import FOLDING

    win = narrow_window("pl")
    seen = 0
    for page in DEVICE_PAGES:
        key = _fill(win, page)
        _iterate(0.05)
        section = win.sections[key].page
        for button in _labelled_buttons(section):
            if _beside_text(button):
                assert button.has_css_class(FOLDING), (page, button.get_label())
                seen += 1
        for widget in _widgets(section):
            if widget.has_css_class(FOLDING):
                assert isinstance(widget, Gtk.Button) and widget.get_label(), page
    assert seen > 20


def test_clear_all_folds_and_keeps_its_own_rule(narrow_window):
    """"Clear All" of the preferences: through the one place, not among the buttons a task
    makes insensitive, and insensitive until the cache is counted."""
    from openexpress.gui.app import MainWindow
    from openexpress.gui.window_tasks import FOLDING
    from openexpress.i18n import _

    win = narrow_window("pl", width=NARROWEST)
    MainWindow.show_preferences(win)
    _iterate(0.6)
    button = win._storage.everything[1]
    assert button.get_label() == _("Clear All") and button.has_css_class(FOLDING)
    assert button not in win._action_buttons
    assert _wrapped(button)[0] is True
    win._preferences.force_close()
    _iterate(0.1)


def test_show_all_folds_and_stays_out_of_the_buttons_of_tasks(narrow_window):
    """"Show All" in the header of "Crashes and Updates": only in the unfolded window (folded,
    a row of its own takes its place); through the one place, one line there."""
    from openexpress.gui.window_tasks import FOLDING
    from openexpress.i18n import _

    win = narrow_window("en", width=WIDE)
    _shown(win, "status")
    buttons = [b for b in _labelled_buttons(win.sections["status"].page)
               if b.get_label() == _("Show All")]
    assert len(buttons) == 1 and buttons[0].has_css_class(FOLDING)
    assert buttons[0] not in win._action_buttons and _wrapped(buttons[0])[0] is False


@pytest.mark.parametrize("page", ["updates-beta", "updates-beta-copied", "activities",
                                  "courses-empty"])
def test_a_button_not_beside_a_rows_text_keeps_its_label_on_one_line(narrow_window, page):
    """The buttons of _button that stand on their own (those of the page Updates in their row
    under the state, "Show {count} More", "Add Course…"): a wrapping row or a box gives them
    their whole width, so their label stays one line in the folded window too."""
    from openexpress.gui.window_tasks import _label_of

    win = narrow_window("pl")
    key = _shown(win, page)
    others = [b for b in _labelled_buttons(win.sections[key].page)
              if not _beside_text(b) and b.get_mapped()]
    assert others, page
    for button in others:
        assert _label_of(button).get_layout().get_line_count() == 1, (page, button.get_label())


def _needed(row) -> int:
    """The height the row's text and its button ask for at the widths they have: the taller."""
    from gi.repository import Gtk

    title = next(w for w in _widgets(row) if isinstance(w, Gtk.Label) and w.has_css_class("title"))
    text = title.get_parent()  # the title and the subtitle
    button = _labelled_buttons(row)[0]
    _ok, bounds = button.compute_bounds(button.get_parent())
    return max(text.measure(Gtk.Orientation.VERTICAL, text.get_width())[1],
               button.measure(Gtk.Orientation.VERTICAL, int(bounds.get_width()))[1])


def _usual_margins(row) -> int:
    """What a row adds around its text: one with a title and a subtitle and nothing beside them,
    in the same list."""
    from gi.repository import Adw, Gtk

    plain = Adw.ActionRow(title="Made-up", subtitle="made-up")
    listbox = row.get_parent()
    listbox.append(plain)
    _iterate(0.2)
    title = next(w for w in _widgets(plain) if isinstance(w, Gtk.Label) and w.has_css_class("title"))
    text = title.get_parent()
    margins = plain.get_height() - text.measure(Gtk.Orientation.VERTICAL, text.get_width())[1]
    listbox.remove(plain)
    return margins


def test_a_row_is_as_tall_as_what_it_shows(narrow_window):
    """360 px in Polish, the row with the longest title ("Emoji Font, Twemoji Extended…"): with
    its button's label broken it is lower than with the label on one line, and no taller than
    the taller of its text and its button with a row's usual margins. GtkBox measured it with
    the label's width at one line and allocated it the broken one: the room in between stayed
    empty around the text."""
    from gi.repository import Adw

    from openexpress.gui.window_tasks import fold_label

    win = narrow_window("pl")
    key = _shown(win, "updates-older")
    row = next(w for w in _widgets(win.sections[key].page)
               if isinstance(w, Adw.ActionRow) and "Emoji" in (w.get_title() or ""))
    button = _labelled_buttons(row)[0]
    after = row.get_height()
    assert after <= _needed(row) + _usual_margins(row)
    fold_label(button, False)  # the label on one line, as without the folding
    _iterate(0.3)
    before = row.get_height()
    fold_label(button, True)
    _iterate(0.3)
    assert after < before and row.get_height() == after


@pytest.mark.parametrize("language", ["en", "pl"])
def test_no_row_or_header_with_a_broken_label_leaves_room_empty(narrow_window, language):
    """At 360 px, each row or group's header with a button beside its text gets no more height
    than the least it needs at its width (the rows of the components, the header and the rows
    of the apps, "Clear All" of the preferences): nothing left empty around its text."""
    from gi.repository import Adw, Gtk

    from openexpress.gui.app import MainWindow

    def holder(button):
        row = button.get_ancestor(Adw.ActionRow)
        if row is not None:
            return row
        box = button.get_parent()
        while not isinstance(box, Gtk.Box):
            box = box.get_parent()
        return box  # the header of the group

    def given_and_needed(box) -> tuple[int, int]:
        _ok, bounds = box.compute_bounds(box.get_parent())
        return (round(bounds.get_height()),
                box.measure(Gtk.Orientation.VERTICAL, round(bounds.get_width()))[0])

    win = narrow_window(language)
    checked = []
    for page in ("updates-older", "apps"):
        root = win.sections[_shown(win, page)].page
        checked += [(page, b, holder(b)) for b in _labelled_buttons(root)
                    if _beside_text(b) and b.get_mapped()]
        for page_, button, box in checked:
            if page_ == page:
                given, needed = given_and_needed(box)
                assert given <= needed, (page, button.get_label(), given, needed)
    MainWindow.show_preferences(win)
    _iterate(0.5)
    given, needed = given_and_needed(win._storage.everything[0])
    assert given <= needed, ("Clear All", given, needed)
    assert len(checked) >= 6
    win._preferences.force_close()
    _iterate(0.1)


# What the rule says at 360 px (window_tasks.wraps_when_folded), for each kind of place: the
# number of lines of the label in English and in Polish (2: broken, into two lines or more).
RULE = [
    ("apps", "Update All", 1, 2),               # the header "On the Device"
    ("courses", "Add Course…", 1, 1),           # a header with a short title
    ("maps", "Check Again", 1, 1),              # the header "On the Device"
    ("maps", "Add Map…", 2, 2),                 # "Own Maps": a long description
    ("music", "Add Playlist…", 1, 1),
    ("device-off-account", "Add to Account", 2, 2),
    ("device-off-account", "Get Picture From Account", 2, 2),
    ("device", "Sign Out", 1, 1),
    ("device-primary-other", "Set as Primary", 1, 2),
    ("preferences", "Clear All", 1, 2),         # "Entire Cache", a short text
    ("preferences", "Choose File…", 2, 2),      # the installer: a long subtitle
    ("preferences", "Choose Installer…", 2, 2), # the account, not signed in
    ("updates-older", "Reinstall", 1, 2),
]


def _found(win, page, msgid):
    from openexpress.gui.app import MainWindow
    from openexpress.i18n import _

    if page == "preferences":
        MainWindow.show_preferences(win)
        _iterate(0.8)
        root = win._preferences
    else:
        root = win.sections[_shown(win, page)].page
    found = [b for b in _labelled_buttons(root)
             if b.get_label() == _(msgid) and _beside_text(b) and b.get_mapped()]
    assert found, (page, msgid)
    return found


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize(("page", "msgid", "en", "pl"), RULE,
                         ids=[f"{page}:{msgid}" for page, msgid, _en, _pl in RULE])
def test_a_label_breaks_only_where_it_would_take_room_from_the_text(narrow_window, language,
                                                                     page, msgid, en, pl):
    """At 360 px a label breaks only where the text on one line and the label on one line
    would not fit side by side (the rule measured on every place: analysis of the change)."""
    win = narrow_window(language, width=NARROWEST)
    expected = en if language == "en" else pl
    for button in _found(win, page, msgid):
        lines = _lines(button)
        assert (lines == 1) if expected == 1 else (lines >= 2), (msgid, lines)
    if page == "preferences":
        win._preferences.force_close()
        _iterate(0.1)


def test_a_changed_text_decides_again(narrow_window):
    """A row's subtitle said once something is read (as "Entire Cache" once the cache is
    counted): "Clear All" breaks when the text no longer leaves it room, and comes back."""
    win = narrow_window("en", width=NARROWEST)
    button = _found(win, "preferences", "Clear All")[0]
    row = win._storage.everything[0]
    assert _lines(button) == 1
    row.set_subtitle("A made-up subtitle long enough to need the whole width of the row")
    _iterate(0.4)
    assert _lines(button) >= 2
    row.set_subtitle("0 B")
    _iterate(0.4)
    assert _lines(button) == 1
    win._preferences.force_close()
    _iterate(0.1)


def test_a_changed_label_decides_again(narrow_window):
    """A label set anew once shown (as the account's button of the preferences): decided
    again for the new label."""
    win = narrow_window("en", width=NARROWEST)
    button = _found(win, "preferences", "Clear All")[0]
    button.set_label("A Made-up Label Long Enough to Break")
    _iterate(0.4)
    assert _lines(button) >= 2
    button.set_label("Clear")
    _iterate(0.4)
    assert _lines(button) == 1
    win._preferences.force_close()
    _iterate(0.1)


def test_wider_than_the_narrowest_a_broken_label_stays_broken(narrow_window):
    """Decided as in the narrowest window, whatever the folded window's width: no jumping as
    it is made wider (the cost: room there is not used)."""
    win = narrow_window("pl", width=600)
    for button in _reinstall_buttons(win):
        assert _lines(button) == 2

