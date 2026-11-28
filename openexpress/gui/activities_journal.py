"""The activity journal in the window (activity_journal.py keeps it): brought up to date in the
background once a device is opened, its progress told to the page; on the Activities page, its
card until it is answered (on top of the page) and its line at the very bottom (after a no, or
for a device on another account after the yes). After the yes the page shows what is new
(gui/activities_since.py), drawn again once the journal's reading is done.
Has: the line at the bottom of the Activities page (activities_note); whether the open
device's journal was read, is done and how far it got are in its session
(DeviceSession.journal_read, journal_done, journal_progress)."""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import activity_journal  # noqa: E402
from openexpress.gui.window_tasks import (  # noqa: E402
    not_saved_text,
    still_in_window,
    wrapping_row,
)
from openexpress.i18n import _  # noqa: E402

log = logging.getLogger(__name__)


def read_journal(win, again: bool = False) -> None:
    """The open device's activity journal brought up to date in the background, once a session
    (after its diagnostics are read: device_page.read_diagnostics), when it is kept: after the
    user's yes, never for a device on another Garmin Connect account. Before the yes and after
    a no nothing starts. `again`: also when it was read this session (the yes given again in
    the preferences: what a reading turned off left out). The Activities page shows its
    progress, and is drawn again once it is done (also when it failed)."""
    from openexpress.gui import activities_since

    s = win.session
    if s is None or not activity_journal.kept() or (s.journal_read and not again):
        return
    if activity_journal.on_another_account(s.ownership_error):
        return
    s.journal_read = True
    s.journal_done, s.journal_progress = False, None
    if again:
        journal_changed(win)  # its progress shown

    def told(done: int, of: int) -> None:  # in the reading's thread
        GLib.idle_add(lambda: (shown(done, of), False)[1])

    def shown(done: int, of: int) -> None:
        if win.session is s and not s.journal_done:
            s.journal_progress = (done, of)
            activities_since.progress(win, done, of)

    def work():
        return activity_journal.at_connection(s.device, s.storage, s.ownership_error,
                                              progress=told)

    def finished(_did_or_error) -> None:
        s.journal_done = True
        if win.session is s:
            journal_changed(win)

    win.in_background(work, finished, finished)


def _another_account(win) -> bool:
    return activity_journal.on_another_account(getattr(win.session, "ownership_error", None))


def card(win, sec) -> None:
    """The card on top of the Activities page while the journal is not answered (settings.json
    has no answer), for a device the journal would be kept of: what it keeps and two buttons,
    its only answers. Nothing is read or written for it before one is clicked."""
    if activity_journal.answered() is not None or _another_account(win):
        return
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    # TRANSLATORS: title of the card on top of the Activities page that asks whether to keep
    # an activity journal
    title = Gtk.Label(label=_("Keep an Activity Journal?"), wrap=True, xalign=0)
    title.add_css_class("title-4")
    box.append(title)
    # TRANSLATORS: text of the card about the activity journal; "the device's last connection":
    # the one before the device is plugged in now
    text = _("openExpress can keep a summary and the shape of each activity on this computer, "
             "so that the Activities page shows what is new since the device's last connection "
             "and keeps your activities after the device deletes old files. Nothing is sent "
             "anywhere. You can turn it off or delete it in Preferences.")
    box.append(Gtk.Label(label=text, wrap=True, xalign=0))
    # TRANSLATORS: button of the card about the activity journal: the no
    later = Gtk.Button(label=_("Not Now"))
    later.add_css_class("pill")
    later.connect("clicked", lambda _button: answer(win, False))
    # TRANSLATORS: button of the card about the activity journal: the yes
    keep = Gtk.Button(label=_("Keep a Journal"))
    keep.add_css_class("pill")
    keep.add_css_class("suggested-action")
    keep.connect("clicked", lambda _button: answer(win, True))
    # Side by side, the second below the first when the window is too narrow for both; at the
    # start, as the words above them.
    buttons = wrapping_row([later, keep], 12)
    buttons.set_halign(Gtk.Align.START)
    if hasattr(buttons, "set_align"):  # the wrapping box: its lines at the start too
        buttons.set_align(0.0)
    box.append(buttons)
    frame = Gtk.Box()
    frame.add_css_class("card")
    frame.append(box)
    sec.group().add(frame)


def line(win, sec) -> None:
    """The line at the very bottom of the Activities page: after a no, that a journal shows
    what is new since the last connection, with the way to the preferences; after the yes, for
    a device on another Garmin Connect account, that it gets none."""
    win.activities_note = None
    said = activity_journal.answered()
    if said is False:
        # TRANSLATORS: line at the very bottom of the Activities page after the no to the
        # activity journal, beside the button Preferences
        text = _("An activity journal shows what is new since the last connection")
        preferences = True
    elif said is True and _another_account(win):
        # TRANSLATORS: line at the very bottom of the Activities page of a device on another
        # Garmin Connect account than the one signed in
        text, preferences = _("The activity journal is not kept for a device on another Garmin "
                              "Connect account"), False
    else:
        return
    box = Gtk.Box(spacing=12)
    words = Gtk.Label(label=text, wrap=True, xalign=0, hexpand=True)
    words.add_css_class("dim-label")
    box.append(words)
    if preferences:
        button = Gtk.Button(label=_("Preferences"), valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.connect("clicked", lambda _button: win.show_preferences())
        box.append(button)
    win.activities_note = sec.group()
    win.activities_note.add(box)


def line_last(win) -> None:
    """The line at the very bottom again, after a group was added below it (Show N More)."""
    sec = win.sections["activities"]
    note = win.activities_note
    if note is None or sec.groups[-1] is note:
        return
    sec.page.remove(note)
    sec.groups.remove(note)
    sec.page.add(note)
    sec.groups.append(note)


def answer(win, yes: bool) -> None:
    """The card's answer saved in settings.json; then the card goes for good, the page drawn
    again as the answer has it, and after the yes the open device's journal brought up to
    date. When it cannot be saved the card stays and the window says why."""
    from openexpress.settings import Settings, SetupError

    try:
        s = Settings.load()
        s.activity_journal = yes
        s.save()
    except (OSError, SetupError) as e:
        win.error(_("Setting Not Saved"), not_saved_text(e))
        return
    log.info("activity journal: %s", "on" if yes else "off")
    journal_changed(win)
    if yes:
        read_journal(win)


def synced(win) -> None:
    """A sync done (the window's, or one seen in last_sync.json): the page of what is new drawn
    again, for what it uploaded; the page as it was is left as it is."""
    from openexpress.gui import activities_since

    if activities_since.shown(win):
        journal_changed(win)


def journal_changed(win) -> None:
    """The Activities page drawn again as the journal's answer has it now (its card, its line,
    or what is new), when it shows the activities of the open device; nothing is read again,
    but the first activities of the page as it was when what is new was shown before (it read
    none of them), and the whole page when its reading had failed. The journal no longer kept,
    the page of an activity from the journal alone goes."""
    from openexpress import activities
    from openexpress.gui import activities_since, activity_page

    if activity_journal.answered() is not True:
        activity_page.leave_journal(win)  # an activity's page from the journal alone
    found, sec = win.activities, (win.sections or {}).get("activities")
    if win.session is None or sec is None:
        return
    if not sec.loaded and sec.stack.get_visible_child_name() in ("error", "content"):
        win.load_activities()  # its reading failed: read again, as a fresh window would
        return
    if found is None or not sec.loaded or found.storage is not win.session.storage:
        return
    if not activities_since.shown(win) and len(found.read) < min(activities.PAGE,
                                                                 len(found.listing)):
        win.load_activities()
        return
    sec.clear()
    win._action_buttons = still_in_window(win._action_buttons)
    win.show_activities(found)
