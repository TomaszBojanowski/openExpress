"""The tasks of the window and what its pages share. TaskRunner is a mixin of MainWindow
(gui/app.py), without __init__ of its own: a task in a thread with the bar at the bottom
(run_task, set_busy, progress, fraction; a wait of known length with Stop Waiting), optional
work in the background (in_background), toasts and error messages, the action buttons. The
functions: the widgets of a page (still_in_window, wrapping_row, trash_button, show_working,
row_subtitle), the free space of the device read again after a task that writes to it
(read_space, render_space, refresh_space), the bar at the bottom in the column of the pages
(bottom_bar, page_column), the name of a task in the log (task_name), the
collection of reference cycles after it (collect_garbage), an error nobody expected, in
the log (log_unexpected), the questions and messages of the window, which close as
Cancel (confirm, Answer, inform), one file of this computer chosen (choose_local_file), and
a switch of a setting saved or put back with why (switch_saved, not_saved_text).

Has: the task under way (busy, _waiting, _task_lets_close, _reporter) and the action buttons.
"""
from __future__ import annotations

import gc
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402 - imports must follow gi.require_version

from openexpress import applog  # noqa: E402
from openexpress.applog import error_text  # noqa: E402
from openexpress.errors import OrdinaryStop, UserFacingError  # noqa: E402
from openexpress.gui import stalled  # noqa: E402
from openexpress.gui.gui_settings import MIN_WINDOW  # noqa: E402
from openexpress.gui.tasks import WindowClosed  # noqa: E402
from openexpress.gui.words import space_text  # noqa: E402
from openexpress.i18n import _, ngettext  # noqa: E402
from openexpress.progress import Reporter, Step, TimedWait, Waiting  # noqa: E402
from openexpress.settings import SetupError  # noqa: E402

log = logging.getLogger(__name__)

WAIT_PULSE_MS = 150  # how often the bar moves while the program waits for a server
SHOWN_CHARS = 300  # of the words of a dialog in its line of the log
# What the window leaves a dialog's content: its own width less this, in px (measured with
# libadwaita 1.9). The widest content (a question's extra widget, the report's preview)
# never asks for more.
DIALOG_MARGINS = 108
# A short hex string standing alone (also after "_"): a serial number or an ID that
# applog.redact leaves when nothing registered it (from 16 digits on, it is redact's).
_SHORT_HEX = re.compile(r"(^|[^0-9A-Za-z])[0-9A-Fa-f]{8,15}([^0-9A-Za-z]|$)")
LONG_WAIT_S = 20  # after this, the label says that the wait can take a while


def still_in_window(buttons: list) -> list:
    """The action buttons that are still in a window: a page that is rebuilt leaves its old
    buttons behind, and the list must not keep them and what they hold."""
    return [b for b in buttons if b.get_root() is not None]


# Adw.WrapBox is in libadwaita 1.7; with an older one (the window works from 1.5) a FlowBox,
# which puts the widgets in columns across its lines: good for a row of buttons.
WRAP_BOX = getattr(Adw, "WrapBox", None)


def wrapping_row(widgets, spacing: int, **props) -> Gtk.Widget:
    """The widgets side by side and centred, `spacing` apart; those that do not fit the width
    of the window (MIN_WINDOW at the narrowest) go on to the next line, centred too, `spacing`
    below."""
    if WRAP_BOX is not None:
        row = WRAP_BOX(child_spacing=spacing, line_spacing=spacing, align=0.5,
                       halign=Gtk.Align.CENTER, **props)
        for widget in widgets:
            row.append(widget)
        return row
    row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                      column_spacing=spacing, row_spacing=spacing, min_children_per_line=1,
                      max_children_per_line=max(1, len(widgets)), halign=Gtk.Align.CENTER,
                      **props)
    row.add_css_class("oe-wrap")  # no room of its own around each widget (start.CSS)
    for widget in widgets:
        row.append(widget)
        place = widget.get_parent()  # the FlowBoxChild it is put in
        place.set_focusable(False)  # the widget itself takes the focus, as in a box
        place.set_halign(Gtk.Align.CENTER)
    return row


def wrapping_line(spacing: int, halign: Gtk.Align = Gtk.Align.START) -> Gtk.Widget:
    """A line of short widgets (a name and its chips) at `halign`, `spacing` apart: those that
    do not fit go on to the next line, 4 px below; with a libadwaita older than 1.7 one line (a
    box), whose labels get shorter where they can."""
    if WRAP_BOX is not None:
        return WRAP_BOX(child_spacing=spacing, line_spacing=4,
                        align=0.0 if halign == Gtk.Align.START else 0.5, halign=halign)
    return Gtk.Box(spacing=spacing, halign=halign)


def chip(words: str, style: str | None = None, **props) -> Gtk.Label:
    """A chip: `words` in the one shape of every chip of the window (the class oe-chip,
    start.CSS), in the colours of `style` ("accent", "warning", "dimmed"); none: no colour."""
    return Gtk.Label(label=words, css_classes=["oe-chip", *([style] if style else [])], **props)


# The margin at the sides of the content of a page (Adw.PreferencesPage): libadwaita's CSS
# gives its box 12 px; the bar at the bottom takes the same.
PAGE_SIDE = 12


def descendants(widget):
    """The widgets under `widget`, at any depth: its children, theirs, and so on, in order."""
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


def page_column(child) -> Adw.Clamp:
    """`child` in the column of a page (Adw.PreferencesPage) at every width of the window:
    the clamp of that page's own width (read from one, so that it follows libadwaita)."""
    page_clamp = next((w for w in descendants(Adw.PreferencesPage())
                       if isinstance(w, Adw.Clamp)), None)
    column = Adw.Clamp(child=child, maximum_size=600, tightening_threshold=400)
    if page_clamp is not None:
        column.set_maximum_size(page_clamp.get_maximum_size())
        column.set_tightening_threshold(page_clamp.get_tightening_threshold())
        column.set_unit(page_clamp.get_unit())
    return column


def bottom_bar(indicator, words, button) -> Gtk.Revealer:
    """The bar at the bottom of a device's view, in the column of the pages above it: the
    spinner (`indicator`), the words and the bar of the task (`words`) and Stop Waiting
    (`button`), each where the groups of a page begin and end. Shown by set_reveal_child."""
    row = Gtk.Box(spacing=6, margin_start=PAGE_SIDE, margin_end=PAGE_SIDE, margin_top=6,
                  margin_bottom=6)
    row.append(indicator)
    row.append(words)
    row.append(button)
    return Gtk.Revealer(child=page_column(row), reveal_child=False,
                        transition_type=Gtk.RevealerTransitionType.SLIDE_UP)


def progress_reporter(win) -> Reporter:
    """The window's one Reporter (progress.Reporter): the words of a step at most once a
    second (a step held is shown by a timer of its own), the bar more often; on the GUI
    thread, whichever thread tells it."""
    def later(seconds, fn):
        def once() -> bool:
            fn()
            return False

        GLib.timeout_add(max(1, round(seconds * 1000)), once)

    return Reporter(lambda message: GLib.idle_add(win._show_progress, message),
                    bar=lambda done, total: win.fraction(done, total), later=later)


def show_label(win, text: str, about: str = "") -> None:
    """The words of the bar at the bottom, and its tooltip (none without `about`)."""
    win.progress_label.set_text(text)
    win.progress_label.set_tooltip_text(about or None)


def show_stop_waiting(win, visible: bool) -> None:
    """Stop Waiting on the bar at the bottom, shown only while TrueUp waits."""
    button = win.stop_wait_button
    if button is not None:
        button.set_visible(visible)


def stop_waiting(win) -> None:
    """Stop Waiting: the wait for TrueUp ends at its next step; the sync then ends as usual
    and writes nothing of TrueUp."""
    stop = getattr(win.session, "stop_waiting", None)
    if stop is not None:
        stop.set()
        log.info("TrueUp: the wait stopped by the user")
    show_stop_waiting(win, False)


def trash_button(tooltip: str, cb) -> Gtk.Button:
    """An icon that removes: its tooltip says what ("Remove {name}"), for a screen reader
    too."""
    b = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    b.add_css_class("flat")
    b.connect("clicked", lambda *_a: cb())
    return b


# A task started from a button: the button spins at once, and the bar at the bottom with the
# words of the task comes only when the task takes longer than this; a quick one (a fast
# device) shows no text that flashes and is gone.
TEXT_AFTER_MS = 500


# Marks a button of wraps_when_folded, and a row or a group whose text such a button stands
# beside, watched for changes of that text (style classes nothing draws).
FOLDING = "folding-label"
WATCHED = "folding-text"


def _label_of(button: Gtk.Button) -> Gtk.Label | None:
    """The button's label, or its unseen copy under the spinner of show_working; None for a
    button with an icon or another child."""
    child = button.get_child()
    if isinstance(child, Gtk.Overlay):
        child = child.get_child()
    return child if isinstance(child, Gtk.Label) else None


def _beside_text(button: Gtk.Button) -> bool:
    """Whether the button is a suffix of a row, or of a group's header: what it would squeeze."""
    return _holder(button) is not None


def _holder(button: Gtk.Button):
    """(the row or the group, the box with the text and the button side by side, the box of
    the text) for a button beside the text of a row or of a group's header; None otherwise."""
    row = button.get_ancestor(Adw.ActionRow)
    if row is not None:
        title = next((w for w in _descendants(row)
                      if isinstance(w, Gtk.Label) and w.has_css_class("title")), None)
        text = title.get_parent() if title is not None else None
        side = text.get_parent() if text is not None else None
        return (row, side, text) if side is not None else None
    group = button.get_ancestor(Adw.PreferencesGroup)
    suffix = group.get_header_suffix() if group is not None else None
    if suffix is None or not (button == suffix or button.is_ancestor(suffix)):
        return None
    side = suffix.get_parent()
    while side is not None and not isinstance(side, Gtk.Box):
        side = side.get_parent()
    text = side.get_first_child() if side is not None else None
    return (group, side, text) if text is not None and text != suffix else None


def _descendants(widget: Gtk.Widget):
    todo = [widget]
    while todo:
        found = todo.pop()
        yield found
        child = found.get_first_child()
        while child is not None:
            todo.append(child)
            child = child.get_next_sibling()


def fold_label(button: Gtk.Button, broken: bool) -> None:
    """The label of a button of wraps_when_folded broken into lines, centred and no wider than
    its longest word asks (max-width-chars 1), or one line as GTK makes it. GtkBox measures a
    row's height giving each child the width it asks for with no height given, and allocates
    it the width it asks for at the row's height; a label that may break asks for less then, so
    with its natural width at one line the row was measured as tall as with the label unbroken
    and left that room empty around its text."""
    label = _label_of(button)
    if label is None:
        return
    if (label.get_wrap(), label.get_max_width_chars()) == (broken, 1 if broken else -1):
        return
    label.set_wrap(broken)
    label.set_max_width_chars(1 if broken else -1)
    label.set_justify(Gtk.Justification.CENTER if broken else Gtk.Justification.LEFT)


def _folded(widget: Gtk.Widget) -> bool:
    split = getattr(widget.get_root(), "split", None)
    return bool(split is not None and split.get_collapsed())


def _one_line_width(button: Gtk.Button) -> int:
    """The button's width with its label on one line, as GTK makes it."""
    label = _label_of(button)
    plain = Gtk.Button(label=label.get_label() if label is not None else "",
                       use_underline=button.get_use_underline())
    for style in button.get_css_classes():
        plain.add_css_class(style)
    return plain.measure(Gtk.Orientation.HORIZONTAL, -1)[1]


def _taken(box: Gtk.Widget, button: Gtk.Button, text: Gtk.Widget) -> int:
    """What the children of `box` other than the text and the button take at least, with the
    spacing between them (in the box that holds the button, its neighbours too: a trash)."""
    taken = shown = 0
    child = box.get_first_child()
    while child is not None:
        if child.get_visible():
            shown += 1
            if child in (button, text):
                pass
            elif button.is_ancestor(child):
                taken += _taken(child, button, text)
            else:
                taken += child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        child = child.get_next_sibling()
    return taken + (box.get_spacing() * max(0, shown - 1) if isinstance(box, Gtk.Box) else 0)


def _fits_beside(button: Gtk.Button, side: Gtk.Widget, text: Gtk.Widget) -> bool:
    """Whether the text on one line (the wider of the title and the subtitle, or of a group's
    title and description) and the button with its label on one line fit side by side in the
    width the row would have in the narrowest window (MIN_WINDOW): its width now, less what the
    window has beyond the narrowest. Neither width depends on the label being broken, nor on
    the window's width, so the answer does not change as the window is made wider or
    narrower, and nothing is measured again in a loop."""
    root = button.get_root()
    width = side.get_width()
    if root is None or width <= 0:
        return True  # not laid out: decided once it is
    room = width - max(0, root.get_width() - MIN_WINDOW[0])
    needed = (text.measure(Gtk.Orientation.HORIZONTAL, -1)[1] + _one_line_width(button)
              + _taken(side, button, text))
    return needed <= room


def _decide(button: Gtk.Button) -> None:
    """The label of a shown button of wraps_when_folded broken or not, from the layout of the
    row it stands in (wraps_when_folded)."""
    if not button.get_mapped():
        return
    holder = _holder(button)
    broken = (holder is not None and _folded(button)
              and not _fits_beside(button, holder[1], holder[2]))
    fold_label(button, broken)


def _after_layout(button: Gtk.Button) -> None:
    """_decide once the next frame is drawn: the widths it reads are those of its layout."""
    clock = button.get_frame_clock()
    if clock is None:
        return
    handler = None

    def drawn(frame_clock) -> None:
        frame_clock.disconnect(handler)
        _decide(button)

    handler = clock.connect("after-paint", drawn)


def _refold(button: Gtk.Button) -> None:
    """Unfolded, or not beside a text: one line at once. Folded beside a text: decided once the
    row is laid out."""
    if _folded(button) and _beside_text(button):
        _after_layout(button)
    else:
        fold_label(button, False)


def _refold_all(owner) -> None:
    """The text of a row or a group changed: its buttons of wraps_when_folded decided again."""
    for widget in _descendants(owner):
        if isinstance(widget, Gtk.Button) and widget.has_css_class(FOLDING):
            _refold(widget)


def _watch_text(button: Gtk.Button) -> None:
    """The row or the group the button stands beside watched, once, for changes of its text
    (a subtitle said once something is read, the account's row of the preferences)."""
    holder = _holder(button)
    if holder is None or holder[0].has_css_class(WATCHED):
        return
    owner = holder[0]
    owner.add_css_class(WATCHED)
    names = ("title", "subtitle") if isinstance(owner, Adw.ActionRow) else ("title", "description")
    for name in names:
        owner.connect(f"notify::{name}", lambda changed, _pspec: _refold_all(changed))


def wraps_when_folded(button: Gtk.Button) -> Gtk.Button:
    """The one place that lets the label of a button beside the text of a row or of a group's
    header break into lines in the folded window (620sp and narrower), for every such button:
    Adw.ActionRow gives its suffix only its smallest width, so a long label of one line squeezes
    the row's title and subtitle into a narrow column. It breaks only when, in the narrowest
    window, the text on one line and the label on one line would not fit side by side
    (_fits_beside); a short label beside a short text stays one line. Unfolded it is one line,
    as GTK makes it. Decided when the button is shown, when the window folds or unfolds
    (fold_buttons), when its label changes and when the text it stands beside changes; switches
    and buttons with only an icon are not passed here."""
    button.add_css_class(FOLDING)
    button.connect("map", _on_shown)
    button.connect("notify::label", lambda changed, _pspec: _refold(changed))
    return button


def _on_shown(button: Gtk.Button) -> None:
    _watch_text(button)
    _refold(button)


def fold_buttons(win) -> None:
    """The window folded or unfolded: every button of wraps_when_folded in it, and in a dialog
    open in it, decided again."""
    _refold_all(win)


def show_working(button: Gtk.Button):
    """The button spins instead of showing its label or icon and cannot be clicked; returns
    the function that puts it back (clickable or not). The label or icon stays under the
    spinner, unseen, so the button keeps its size and nothing beside it moves: the label's copy
    breaks into lines as the label did (wraps_when_folded), and the label put back does again."""
    label, icon = button.get_label(), button.get_icon_name()
    if label is None and icon is None:  # neither: it only goes grey
        button.set_sensitive(False)
        return button.set_sensitive
    shown = _label_of(button)
    ghost = (Gtk.Label(label=label, use_underline=button.get_use_underline(), opacity=0,
                       wrap=shown is not None and shown.get_wrap(),
                       max_width_chars=shown.get_max_width_chars() if shown else -1,
                       justify=shown.get_justify() if shown else Gtk.Justification.LEFT)
             if label is not None else Gtk.Image(icon_name=icon, opacity=0))
    spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
    spinner.set_halign(Gtk.Align.CENTER)
    spinner.set_valign(Gtk.Align.CENTER)
    overlay = Gtk.Overlay(child=ghost)
    overlay.add_overlay(spinner)
    button.set_child(overlay)
    button.set_sensitive(False)

    def restore(sensitive: bool) -> None:
        if label is not None:
            broken = ghost.get_wrap()  # as the label was, or as the window is now
            button.set_label(label)
            if button.has_css_class(FOLDING):
                fold_label(button, broken)
        else:
            button.set_icon_name(icon)
        button.set_sensitive(sensitive)

    return restore


# How long a task waits at most for the reading of the error logs at the opening and for the
# count after a write.
DEVICE_WAIT_S = 120


def diagnostics_state(session) -> str:
    """Where the reading of the open device's diagnostics is (device_page.read_diagnostics):
    "none" (not started), "reading", or "read" (done, also when it failed)."""
    reading = getattr(session, "diagnostics_reading", None)
    if getattr(session, "diagnostics", None) is not None or (
            reading is not None and reading.is_set()):
        return "read"
    return "none" if reading is None else "reading"


def error_heading(failed_title: str | None) -> str:
    """The heading of the error of a task: its own (`failed_title`, "<Thing> Not <Done>": what
    did not happen), else a general one; never the words of its progress ("Saving…")."""
    # TRANSLATORS: heading of an error of a task that has no heading of its own
    return failed_title or _("Something Went Wrong")


def failed_text(e: BaseException) -> str:
    """The words of the error dialog of a task: those of the error, and for a fault of
    openExpress (applog.is_fault) how to report it."""
    if not applog.is_fault(e):
        return error_text(e)
    # TRANSLATORS: under the words of an error nobody foresaw, in the window; {item}: the item
    # of the main menu, "Report a Device or Problem…" in your language (put your language's
    # quotation marks around it); "Attach the last log" is the check box of that dialog
    note = _("This is a bug in openExpress. To report it, choose “{item}” in the main menu "
             "and attach the last log.").format(item=_("Report a Device or Problem…"))
    return f"{error_text(e)}\n\n{note}"


def not_saved_text(e: Exception) -> str:
    """Why a setting was not saved: the system's words for a file it could not write
    (applog.system_words), else the words of applog.error_text."""
    return applog.system_words(e) or error_text(e)


def switch_saved(win, row, handler, saved: bool, save) -> bool:
    """A switch of a setting turned (its handler `handler` of notify::active): `save(on)` with
    what it shows now, unless that is `saved` already: the switch put back after a change that
    was not saved, which a row may tell again though its handler is blocked, so nothing loops.
    A change that cannot be saved puts the switch back and says why (Setting Not Saved). True:
    the change is saved."""
    on = row.get_active()
    if on == saved:
        return False
    try:
        save(on)
    except (OSError, SetupError) as e:
        row.handler_block_by_func(handler)
        row.set_active(not on)
        row.handler_unblock_by_func(handler)
        # TRANSLATORS: heading of the error when a switch of a setting could not be saved (in
        # the preferences, on the device page, under the map of an activity)
        win.error(_("Setting Not Saved"), not_saved_text(e))
        return False
    return True


def wait_for_the_diagnostics(session, fn) -> None:
    """A work that writes to the device (writes_to_device) waits, in its thread, until the
    device's diagnostics are read (device_page.read_diagnostics, in the background after its
    page is shown), so that their copy is of the device before any change; so does one that
    takes the device from the file manager for a session of its own
    (leaves_the_file_manager), which the reading would not survive; any other work goes on at
    once."""
    if diagnostics_state(session) != "reading" or not (
            getattr(fn, "writes_to_device", False)
            or getattr(fn, "leaves_the_file_manager", False)):
        return
    log.info("task: waits until the diagnostics of the device are read")
    if not session.diagnostics_reading.wait(DEVICE_WAIT_S):
        log.info("task: the diagnostics still not read after %d s, it goes on",
                 DEVICE_WAIT_S)


def wait_for_the_count(session) -> None:
    """Any work of the device waits, in its thread, until the device's count after a write
    (count_again, in the background) is done: one thing asks the device at a time (Copy Crash
    Logs leaves the file manager for a moment), and a count begun before a removal never ends
    after it. No device open (`session` None): nothing to wait for."""
    counting = session.counting if session is not None else None
    if counting is None or counting.is_set():
        return
    log.info("task: waits until the device is counted again")
    if not counting.wait(DEVICE_WAIT_S):
        log.info("task: the device still not counted after %d s, it goes on", DEVICE_WAIT_S)


def read_space(storage, capacity: int | None = None) -> tuple[int | None, int | None]:
    """(free space, capacity) of the device, asked of it: never on the window's thread. The
    capacity is asked only when it is not given (it does not change). What cannot be asked
    (e.g. while a session of PTP holds the device) is not known, None: no error."""
    def ask(question: str):
        try:
            return getattr(storage, question)()
        except Exception as e:  # noqa: BLE001 - unknown, said as such on the device page
            log.info("device storage: %s not read (%s), not known", question,
                     type(e).__name__)
            return None

    free = ask("free_space")
    return free, capacity if capacity is not None else ask("capacity")


def render_space(win) -> None:
    """The subtitle (and the bar) of "Device Storage" from the space the session holds, in
    the words of every page (space_text): "Reading…" until it is read, "Unknown" when the
    device does not say. Nothing is asked of the device."""
    row, bar = win.storage_row, win.storage_bar
    s = win.session
    if row is None or s is None:
        return
    space = s.space  # as the page and the report have it
    if space is None:
        row.set_subtitle(space_text(None, reading=True))
        bar.set_visible(False)
        return
    free, total = space
    row.set_subtitle(space_text(free, total))
    if free is not None and total:
        bar.set_value(min(1.0, max(0.0, 1 - free / total)))
    bar.set_visible(bool(free is not None and total))


def refresh_space(win, s) -> None:
    """The free space of the device of session `s` read again in the background (its
    capacity only while it is not known), then the subtitle of "Device Storage" alone put
    right. An answer that comes after the device was unplugged, or another one opened, is
    dropped."""
    if s is None:
        return
    from openexpress import status

    capacity = status.known_capacity(s)

    def got(space):
        if win.session is not s:
            log.info("device storage: read after the device was closed, not shown")
            return
        s.space = space
        render_space(win)
        # The page Device Status, when shown, with the space and the files of now.
        from openexpress.gui.device_page import status_changed

        status_changed(win)

    win.in_background(lambda: read_space(s.storage, capacity), got)


def count_again(win, s) -> None:
    """After a work that wrote to the device: its kinds of file, its space and its memory dumps
    counted again in the background (status.recount, into the device log's line too), then the
    row "Device Storage" and the page Device Status shown with them (status.counted_again):
    the page tells the device as it is now, its kinds of file and its free space of one count.
    When that cannot be counted, the free space alone is read again (refresh_space). Any task of
    the device waits until it is done (wait_for_the_count)."""
    from openexpress import status

    counting = s.counting = threading.Event()

    def done(kept):
        if win.session is not s:
            log.info("device storage: counted after the device was closed, not shown")
            return
        if not kept:
            log.info("device storage: a later count is shown already, this one not")
            return
        render_space(win)
        from openexpress.gui.device_page import status_changed

        status_changed(win)

    def failed(e):
        log.info("device storage: not counted again (%s)", type(e).__name__)
        refresh_space(win, s)
        return True

    def count():
        # In the session before any task waiting for it goes on (wait_for_the_count); a device
        # unplugged meanwhile keeps what it had.
        try:
            counted = status.recount(s.device, s.storage)
            return win.session is s and status.counted_again(s, counted)
        finally:
            counting.set()

    win.in_background(count, done, failed)


def row_label(row, style: str) -> Gtk.Label | None:
    """The label of the title ("title") or of the subtitle ("subtitle") of an Adw.ActionRow,
    which libadwaita does not give: the one with that style class (so in 1.5 as in 1.9). None
    when there is none."""
    return next((w for w in descendants(row)
                 if isinstance(w, Gtk.Label) and w.has_css_class(style)), None)


def task_name(fn) -> str:
    """The name of a task in the log: the method that made its work ("MainWindow.add_music"),
    also when the method is in a mixin of the window (NetStatusPanel, MusicPage, …)."""
    name = fn.__qualname__.split(".<locals>")[0] if hasattr(fn, "__qualname__") else "?"
    owner, dot, method = name.partition(".")
    # The window is the class made directly of TaskRunner (MainWindow, gui/app.py).
    mixins = {c.__name__ for window in TaskRunner.__subclasses__()
              for c in window.__mro__[1:] if c.__module__.startswith("openexpress.")}
    return f"MainWindow.{method}" if dot and owner in mixins else name


def collect_garbage() -> bool:
    """Free reference cycles now. Always called on the GTK thread; True keeps a timer going."""
    gc.collect()
    return True


def log_unexpected(what: str, exc_info) -> None:
    """An exception the window did not expect: to the log as ERROR, with its traceback
    (filtered, as every line), as the command line writes one."""
    log.error("unexpected error %s: %s", what, exc_info[0].__name__, exc_info=exc_info)


@dataclass(frozen=True)
class Answer:
    """A button of a question (confirm): its response id, never "cancel"; what it runs once
    chosen; its look ("suggested", "destructive" or None); whether it can be chosen yet."""

    id: str
    label: str
    run: Callable[[], object]
    look: str | None = None
    enabled: bool = True


LOOKS = {"suggested": Adw.ResponseAppearance.SUGGESTED,
         "destructive": Adw.ResponseAppearance.DESTRUCTIVE}


def _keep(parent, attribute: str, dialog) -> None:
    """`parent.<attribute>` holds the dialog while it is open (set before it is shown, let go
    once it is closed, unless the attribute holds another dialog by then)."""
    setattr(parent, attribute, dialog)

    def closed(shown):
        if getattr(parent, attribute) is shown:
            setattr(parent, attribute, None)

    dialog.connect("closed", closed)


def _one_line(text: str) -> str:
    """`text` for a line of the log: without what identifies the user (applog.redact, then
    short hex strings, twice, as two may stand side by side), its whitespace one space."""
    text = applog.redact(text)
    for _pass in range(2):
        text = _SHORT_HEX.sub(r"\1<hex>\2", text)
    return " ".join(text.split())


def _log_shown(name: str, heading: str, body: str, private: bool) -> None:
    """One line in the log as a dialog is shown: its name, then its heading and words, without
    what identifies the user (_one_line), the words cut at SHOWN_CHARS.
    `private`: words that carry the user's own names (files, maps, music, courses): the name
    alone."""
    if private:
        log.info("dialog %s shown", name)
        return
    title = _one_line(heading)
    words = _one_line(body)
    if len(words) > SHOWN_CHARS:
        words = words[:SHOWN_CHARS - 1] + "…"
    log.info("dialog %s shown: %s", name, f"{title} — {words}" if words else title)


def confirm(parent, name: str, heading: str, body: str, *answers: Answer,
            cancel: str | None = None, default: str | None = None, extra=None,
            wide: bool = False, keep_as: str | None = None,
            private: bool = False) -> Adw.AlertDialog:
    """Shows a question over `parent` and returns it: Cancel (labelled `cancel`), then
    `answers`. Closed in any way (Escape) it is cancelled; a chosen answer runs its `run`
    only while enabled. `default`: the answer of Enter; `extra`: a widget below the text;
    `keep_as`: see _keep. ValueError for a missing, repeated or "cancel" answer. Logs what it
    shows (_log_shown; `private`: only its name), then `name` and the answer's id, or
    "cancelled"."""
    ids = [a.id for a in answers]
    if not answers or "cancel" in ids or len(set(ids)) != len(ids):
        raise ValueError(f"dialog {name}: answers {ids}")
    if default is not None and default not in ("cancel", *ids):
        raise ValueError(f"dialog {name}: no answer {default}")
    d = Adw.AlertDialog(heading=heading, body=body)
    if wide and hasattr(d, "set_prefer_wide_layout"):
        d.set_prefer_wide_layout(True)
    d.add_response("cancel", cancel if cancel is not None else _("Cancel"))
    for a in answers:
        d.add_response(a.id, a.label)
        if a.look is not None:
            d.set_response_appearance(a.id, LOOKS[a.look])
        if not a.enabled:
            d.set_response_enabled(a.id, False)
    d.set_close_response("cancel")
    if default is not None:
        d.set_default_response(default)
    if extra is not None:
        d.set_extra_child(extra)
    runs = {a.id: a.run for a in answers}

    def response(dialog, r):
        run = runs.get(r) if r in runs and dialog.get_response_enabled(r) else None
        log.info("dialog %s: %s", name, r if run is not None else "cancelled")
        if run is not None:
            run()

    d.connect("response", response)
    if keep_as is not None:
        # libadwaita says "closed" before the answer (1.9), so an answer takes its data from
        # its own closure, never from this attribute.
        _keep(parent, keep_as, d)
    _log_shown(name, heading, body, private)
    d.present(parent)
    return d


def inform(parent, name: str, heading: str, body: str, *, label: str | None = None,
           then: Callable[[], object] | None = None, private: bool = False) -> Adw.AlertDialog:
    """A message shown over `parent` with one button (`label`: its words, OK by default):
    nothing to cancel, so `then` runs once it is closed in any way, by the button or Escape.
    In the log: what it shows (_log_shown; `private`: only its name), then its `name` and
    "ok"."""
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("ok", label if label is not None else _("OK"))
    d.set_close_response("ok")

    def response(_dialog, r):
        log.info("dialog %s: %s", name, r)
        if then is not None:
            then()

    d.connect("response", response)
    _log_shown(name, heading, body, private)
    d.present(parent)
    return d


def choose_local_file(win, title: str, filter_name: str, patterns: tuple[str, ...],
                      not_local: str, then: Callable[[Path], object]) -> None:
    """One file chosen through the file chooser (the portal in the Flatpak: any folder), only
    those of `patterns` shown, under `filter_name`; `then` gets its path. Cancelled: nothing;
    a file without a path on this computer: the toast `not_local`."""
    dialog = Gtk.FileDialog(title=title)
    only = Gtk.FileFilter(name=filter_name)
    for pattern in patterns:
        only.add_pattern(pattern)
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(only)
    dialog.set_filters(filters)

    def chosen(dlg, res):
        try:
            path = dlg.open_finish(res).get_path()
        except GLib.Error:
            return  # cancelled
        if path is None:
            win.toast(not_local)
            return
        then(Path(path))

    dialog.open(win, None, chosen)


class TaskRunner:
    def toast(self, text: str, on_preferences: bool = False) -> None:
        """A short message: on a device's view above its bar at the bottom (where the next
        task may show already), elsewhere on the window; `on_preferences`: on the preferences
        while they are open (the window's own toasts are under them). Its words go on to a
        second and a third line rather than being cut (a name in them may be long)."""
        words = Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=3,
                          ellipsize=Pango.EllipsizeMode.END, justify=Gtk.Justification.CENTER,
                          css_classes=["heading"])
        toast = Adw.Toast(custom_title=words, timeout=5)
        if on_preferences and self._preferences is not None:
            self._preferences.add_toast(toast)
            return
        on_device = (self.navigation is not None and self.page_toasts is not None
                     and self.navigation.get_visible_page() is self.device_view)
        (self.page_toasts if on_device else self.toasts).add_toast(toast)

    def error(self, title: str, text: str, private: bool = False) -> None:
        """A message under `title`; `private`: its words carry the user's own names, so the
        log has the dialog's name alone (_log_shown)."""
        if self._telling_stall:
            log.info("window: no error dialog: the banner says the device stopped responding")
            return
        inform(self, "error", title, text, private=private)

    def set_busy(self, busy: bool, text: str = "", device: bool = False,
                 in_page: bool = False) -> None:
        """A task starts or ends. Its indicator is the spinner until it tells its progress,
        then the bar (`_show_bar`, one way); none for a page that shows it loads (`in_page`)."""
        self._stop_waiting()
        self._reporter.reset()  # nothing of a task before is shown later
        self.busy = busy
        self.action_bar.set_reveal_child(busy)
        show_label(self, text)
        self.unplug_label.set_visible(busy and device)
        self.progress_bar.set_fraction(0)
        self.progress_bar.set_visible(False)
        show_stop_waiting(self, False)
        self.busy_spinner.set_visible(busy and not in_page)
        self._action_buttons = still_in_window(self._action_buttons)
        for b in self._action_buttons:
            b.set_sensitive(not busy)
        self.banner.set_sensitive(not busy)
        self._update_actions()

    def progress(self, text: str) -> None:
        self._reporter(text)  # a step at most once a second (progress_reporter)

    def fraction(self, done: int, total: int | None) -> None:
        if total:
            GLib.idle_add(self._show_fraction, min(1.0, done / total))

    def _show_bar(self) -> None:
        """The task tells its progress: the bar instead of the spinner, until the task ends."""
        self.busy_spinner.set_visible(False)
        self.progress_bar.set_visible(True)

    def _show_progress(self, text: str) -> bool:
        """A step moves the bar once. A wait for a server (`Waiting`), whose length nobody
        knows, keeps it moving until the next step, and after a while the label says so."""
        self._stop_waiting()
        self._show_bar()
        if isinstance(text, TimedWait):
            # Its words short, for the narrowest window beside Stop Waiting; why in the tooltip.
            show_label(self, text.short, text.about)
            # Its length is known: the bar shows how much has passed, and it can be stopped.
            self.progress_bar.set_fraction(text.done)
            stop = getattr(self.session, "stop_waiting", None)
            show_stop_waiting(self, text.done < 1 and stop is not None and not stop.is_set())
            return False
        show_label(self, text)
        show_stop_waiting(self, False)
        if isinstance(text, Step) and text.fraction is not None:
            self.progress_bar.set_fraction(text.fraction)  # how far: "3 of 8"
            return False
        self.progress_bar.pulse()
        if isinstance(text, Waiting):
            until = time.monotonic() + LONG_WAIT_S
            told = []

            def pulse() -> bool:
                self.progress_bar.pulse()
                if not told and time.monotonic() >= until:
                    told.append(True)
                    self.progress_label.set_text(f"{text} {_('This can take a few minutes.')}")
                return True

            self._waiting = GLib.timeout_add(WAIT_PULSE_MS, pulse)
        return False

    def _show_fraction(self, fraction: float) -> bool:
        self._stop_waiting()
        self._show_bar()
        self.progress_bar.set_fraction(fraction)
        return False

    def _stop_waiting(self) -> None:
        if self._waiting:
            GLib.source_remove(self._waiting)
            self._waiting = 0

    def run_task(self, title: str, fn, on_done=None, on_error=None, device: bool = True,
                 in_page: bool = False, button: Gtk.Button | None = None,
                 failed_title: str | None = None) -> None:
        """Run `fn()` in a thread, then `on_done(result)` or `on_error(e)` on the GUI thread
        after the bar and `button` are restored; the error is shown unless `on_error`
        returns true, under `failed_title` (error_heading). While busy: a toast only.
        `device`: False when the task writes nothing to the device (no "keep it plugged in").
        `in_page`: as set_busy. `button`: spins at once, the bar with `title` after
        TEXT_AFTER_MS."""
        if self.busy:
            log.info("task %s: not started, another one is running", task_name(fn))
            self.toast(_("Another operation is in progress"))
            return
        # The window may close during it (_on_close_request).
        self._task_lets_close = bool(getattr(fn, "lets_the_window_close", False))
        has_device = device and self.session is not None
        started = self.session
        restore, reveal = None, [0]
        if button is None:
            self.set_busy(True, title, device=has_device, in_page=in_page)
        else:
            self.busy = True  # no other task meanwhile
            restore = show_working(button)

            def show_bar() -> bool:
                reveal[0] = 0
                self.set_busy(True, title, device=has_device, in_page=in_page)
                return False

            reveal[0] = GLib.timeout_add(TEXT_AFTER_MS, show_bar)
        log.info("task: %s", task_name(fn))

        def ended():
            if reveal[0]:
                GLib.source_remove(reveal[0])  # done before its words were due
                reveal[0] = 0
            collect_garbage()
            self._task_lets_close = False
            self.set_busy(False)
            if restore is not None:
                restore(True)

        def worker():
            try:
                wait_for_the_diagnostics(started, fn)
                wait_for_the_count(started)
                result = fn()
            except Exception as e:  # noqa: BLE001 - every failure of a task is shown
                GLib.idle_add(finish_error, e)
            else:
                GLib.idle_add(finish_ok, result)

        # A work that writes to the device (writes_to_device): once it ends, also when it
        # failed (a part may be written), the device is counted again in the background (a
        # work that counts it itself, as the removals of the page Device Status, is left).
        def written() -> None:
            if getattr(fn, "writes_to_device", False) and started is not None \
                    and self.session is started and not getattr(fn, "counts_again", False):
                count_again(self, started)

        # The count after a write begins before what the task's end starts (a page read again,
        # another task): those wait for it (wait_for_the_count).
        def finish_ok(result):
            ended()
            written()
            try:
                if on_done:
                    on_done(result)
            finally:
                self._run_pending_refresh()
            return False

        def finish_error(e):
            if isinstance(e, WindowClosed):  # stopped as the window closed: nothing to tell
                ended()
                return False
            if isinstance(e, OrdinaryStop):
                # Not a failure (the choice of the first sync, another sync running…): what it
                # waits for, in one line.
                log.info("task %s: %s", task_name(fn), e.log_reason)
            elif isinstance(e, UserFacingError):
                # A message for the user (the device not ready, a refusal, an answer of a
                # server): one line, as the user is told.
                log.info("task failed: %s: %s", type(e).__name__, e)
            else:
                # Nobody expected it: with its traceback, filtered as every line of the log,
                # so that a report from the window says where it failed.
                log.warning("task failed: %s: %s", type(e).__name__, e, exc_info=e)
            ended()
            written()
            try:
                with stalled.telling(self, e):  # a stall: the banner, no error dialog
                    if not (on_error and on_error(e)):
                        self.error(error_heading(failed_title), failed_text(e))
            finally:
                self._run_pending_refresh()
            return False

        # Not a daemon thread: the process must not exit while the watch is being written.
        threading.Thread(target=worker, daemon=False).start()

    @staticmethod
    def in_background(fn, on_done, on_error=None) -> None:
        """Run optional work (pictures, status checks) without blocking the window."""
        def worker():
            try:
                result = fn()
            except Exception as e:
                # Said in the log as run_task says it, at INFO: the work is optional.
                if isinstance(e, OrdinaryStop):
                    log.info("background work %s: %s", task_name(fn), e.log_reason)
                elif isinstance(e, UserFacingError):
                    log.info("background work %s failed: %s: %s", task_name(fn),
                             type(e).__name__, e)
                else:  # nobody expected it: with its traceback
                    log.info("background work %s failed: %s: %s", task_name(fn),
                             type(e).__name__, e, exc_info=e)
                if on_error is not None:
                    GLib.idle_add(lambda err=e: (on_error(err), False)[1])
                return
            GLib.idle_add(lambda: (on_done(result), False)[1])

        threading.Thread(target=worker, daemon=True).start()

    def _button(self, label: str, cb, style: str | None = None,
                with_button: bool = False) -> Gtk.Button:
        """An action button. `with_button`: `cb` gets the button clicked (the argument of the
        signal; the handler holds no button), e.g. to start its task from it (run_task)."""
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if style:
            b.add_css_class(style)
        if with_button:
            b.connect("clicked", lambda button: cb(button))
        else:
            b.connect("clicked", lambda *_: cb())
        b.set_sensitive(not self.busy)
        self._action_buttons.append(b)
        return wraps_when_folded(b)

    def _trash(self, tooltip: str, cb) -> Gtk.Button:
        b = trash_button(tooltip, cb)
        b.set_sensitive(not self.busy)
        self._action_buttons.append(b)
        return b

    @staticmethod
    def _removed_files(count: int) -> str:
        # TRANSLATORS: toast after music or map files were removed from the device
        return ngettext("Removed {count} file", "Removed {count} files", count).format(count=count)

    @staticmethod
    def _search_filter(entry: Gtk.SearchEntry, rows: list[tuple[Gtk.Widget, str]],
                       expander: Adw.ExpanderRow | None = None) -> None:
        def changed(e):
            text = e.get_text().casefold()
            for row, haystack in rows:
                row.set_visible(text in haystack)
            if expander is not None and text:
                expander.set_expanded(True)
        entry.connect("search-changed", changed)
