"""The tasks of the window and what its pages share. TaskRunner is a mixin of MainWindow
(gui/app.py), without __init__ of its own: a task in a thread with the bar at the bottom
(run_task, set_busy, progress, fraction; a wait of known length with Stop Waiting), optional
work in the background (in_background), toasts and error messages, the action buttons. The
functions: the widgets of a page (still_in_window, wrapping_row, trash_button, show_working,
row_subtitle), the free space of the device read again after a task that writes to it
(read_space, render_space, refresh_space), the name of a task in the log (task_name), the
collection of reference cycles after it (collect_garbage), an error nobody expected, in
the log (log_unexpected), and the questions and messages of the window, which close as
Cancel (confirm, Answer, inform).

Has: the task under way (busy, _waiting, _task_lets_close, _reporter) and the action buttons.
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.errors import OrdinaryStop, UserFacingError  # noqa: E402
from openexpress.gui.tasks import WindowClosed  # noqa: E402
from openexpress.gui.words import space_text  # noqa: E402
from openexpress.i18n import _, ngettext  # noqa: E402
from openexpress.progress import Reporter, Step, TimedWait, Waiting  # noqa: E402

log = logging.getLogger(__name__)

WAIT_PULSE_MS = 150  # how often the bar moves while the program waits for a server
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
    b = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.add_css_class("flat")
    b.connect("clicked", lambda *_a: cb())
    return b


# A task started from a button: the button spins at once, and the bar at the bottom with the
# words of the task comes only when the task takes longer than this; a quick one (a fast
# device) shows no text that flashes and is gone.
TEXT_AFTER_MS = 500


def show_working(button: Gtk.Button):
    """The button spins instead of showing its label or icon and cannot be clicked; returns
    the function that puts it back (clickable or not). The label or icon stays under the
    spinner, unseen, so the button keeps its size and nothing beside it moves."""
    label, icon = button.get_label(), button.get_icon_name()
    if label is None and icon is None:  # neither: it only goes grey
        button.set_sensitive(False)
        return button.set_sensitive
    ghost = (Gtk.Label(label=label, use_underline=button.get_use_underline(), opacity=0)
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
            button.set_label(label)
        else:
            button.set_icon_name(icon)
        button.set_sensitive(sensitive)

    return restore


# How long a task that writes to the device waits for its diagnostics at most.
DIAGNOSTICS_WAIT_S = 120


def wait_for_the_diagnostics(session, fn) -> None:
    """A work that writes to the device (writes_to_device) waits, in its thread, until the
    device's diagnostics are read (device_page.read_diagnostics, in the background after its
    page is shown), so that their copy is of the device before any change; any other work
    goes on at once."""
    reading = getattr(session, "diagnostics_reading", None)
    if reading is None or reading.is_set() or not getattr(fn, "writes_to_device", False):
        return
    log.info("task: waits until the diagnostics of the device are read")
    if not reading.wait(DIAGNOSTICS_WAIT_S):
        log.info("task: the diagnostics still not read after %d s, it goes on",
                 DIAGNOSTICS_WAIT_S)


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
    """The subtitle (and the bar) of "Device storage" from the space the session holds, in
    the words of every page (space_text): "Reading…" until it is read, "Unknown" when the
    device does not say. Nothing is asked of the device."""
    row, bar = win.storage_row, win.storage_bar
    s = win.session
    if row is None or s is None:
        return
    space = getattr(s, "space", None)
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
    capacity only while it is not known), then the subtitle of "Device storage" alone put
    right. An answer that comes after the device was unplugged, or another one opened, is
    dropped."""
    if s is None:
        return
    known = getattr(s, "space", None)
    capacity = known[1] if known is not None else None

    def got(space):
        if win.session is not s:
            log.info("device storage: read after the device was closed, not shown")
            return
        s.space = space
        render_space(win)

    TaskRunner.in_background(lambda: read_space(s.storage, capacity), got)


def row_subtitle(row) -> Gtk.Label | None:
    """The label of the subtitle of an Adw.ActionRow, which libadwaita does not give: the one
    with the style class "subtitle" (so in 1.5 as in 1.9). None when there is none."""
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label) and child.has_css_class("subtitle"):
            return child
        found = row_subtitle(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


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


def confirm(parent, name: str, heading: str, body: str, *answers: Answer,
            cancel: str | None = None, default: str | None = None, extra=None,
            wide: bool = False, keep_as: str | None = None) -> Adw.AlertDialog:
    """Shows a question over `parent` and returns it: Cancel (labelled `cancel`), then
    `answers`. Closed in any way (Escape) it is cancelled; a chosen answer runs its `run`
    only while enabled. `default`: the answer of Enter; `extra`: a widget below the text;
    `keep_as`: see _keep. ValueError for a missing, repeated or "cancel" answer. Logs `name`
    and the answer's id, or "cancelled"."""
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
    d.present(parent)
    return d


def inform(parent, name: str, heading: str, body: str, *, label: str | None = None,
           then: Callable[[], object] | None = None) -> Adw.AlertDialog:
    """A message shown over `parent` with one button (`label`: its words, OK by default):
    nothing to cancel, so `then` runs once it is closed in any way, by the button or Escape.
    One line in the log: the dialog's `name`."""
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("ok", label if label is not None else _("OK"))
    d.set_close_response("ok")

    def response(_dialog, r):
        log.info("dialog %s: %s", name, r)
        if then is not None:
            then()

    d.connect("response", response)
    d.present(parent)
    return d


class TaskRunner:
    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))

    def error(self, title: str, text: str) -> None:
        inform(self, "error", title, text)

    def set_busy(self, busy: bool, text: str = "", device: bool = False,
                 in_page: bool = False) -> None:
        """A task starts or ends. Its indicator is the spinner until it tells its progress,
        then the bar (`_show_bar`, one way); none for a page that shows it loads (`in_page`)."""
        self._stop_waiting()
        self._reporter.reset()  # nothing of a task before is shown later
        self.busy = busy
        self.action_bar.set_revealed(busy)
        self.progress_label.set_text(text)
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
        self.progress_label.set_text(text)
        if isinstance(text, TimedWait):
            # Its length is known: the bar shows how much has passed, and it can be stopped.
            self.progress_bar.set_fraction(text.done)
            stop = getattr(self.session, "stop_waiting", None)
            show_stop_waiting(self, text.done < 1 and stop is not None and not stop.is_set())
            return False
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
                 in_page: bool = False, button: Gtk.Button | None = None) -> None:
        """Run `fn()` in a thread, then `on_done(result)` or `on_error(e)` on the GUI thread
        after the bar and `button` are restored; the error is shown unless `on_error`
        returns true. While busy: a toast only. `device`: False when the device is not touched
        (no "keep it plugged in"). `in_page`: as set_busy. `button`: spins at once, the bar
        with `title` after TEXT_AFTER_MS."""
        if self.busy:
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
                result = fn()
            except Exception as e:  # noqa: BLE001 - every failure of a task is shown
                GLib.idle_add(finish_error, e)
            else:
                GLib.idle_add(finish_ok, result)

        # A work that writes to the device (writes_to_device): once it ends, also when it
        # failed (a part may be written), the free space is read again in the background.
        def written() -> None:
            if getattr(fn, "writes_to_device", False) and started is not None \
                    and self.session is started:
                refresh_space(self, started)

        def finish_ok(result):
            ended()
            try:
                if on_done:
                    on_done(result)
            finally:
                written()
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
            try:
                if not (on_error and on_error(e)):
                    self.error(title, str(e) or e.__class__.__name__)
            finally:
                written()
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
        return b

    def _trash(self, tooltip: str, cb) -> Gtk.Button:
        b = trash_button(tooltip, cb)
        b.set_sensitive(not self.busy)
        self._action_buttons.append(b)
        return b

    @staticmethod
    def _removed_files(count: int) -> str:
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
