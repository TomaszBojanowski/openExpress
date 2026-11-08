"""The Activities page of the window: the activities of the open device and of its backups,
the newest 30 first and 30 more at a time on request (activities.py lists and reads them; the
words of a row are in gui/words.py). Nothing is written to the device; the button of a row
exports its activity as a GPX file where the user chooses, and a row opens the page of its
activity (gui/activity_page.py).
Has: the activities of the open device (activities) and the groups of the page that grow as
more are read (activities_list, activities_more).
ActivitiesPage is a mixin of MainWindow (gui/app.py), without __init__ of its own; what it uses
of the window (session, run_task, progress, sections, …) is the window's."""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import applog, paths  # noqa: E402
from openexpress.activities import Source  # noqa: E402
from openexpress.errors import UserFacingError  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    activities_count_text,
    activity_caption,
    activity_file_error,
    activity_title,
)
from openexpress.i18n import _, ngettext  # noqa: E402

log = logging.getLogger(__name__)

PAGE_ICON = f"{paths.APP_ID}-activities-symbolic"  # a runner


def _read(found, count: int, progress):
    """In the task's thread: the activity files listed again and the newest `count` read (what
    was read before is kept and not read again)."""
    found.list_files()
    found.read_next(count, progress)
    return found


class ExportFailed(UserFacingError):
    """An activity that could not be exported, in words for the user (never a file's name or
    path: the window's log writes them)."""


def export_button(win, read) -> Gtk.Button | None:
    """The button that exports an activity as GPX: one that can is one of the window's buttons
    (off while a task runs); one without positions is off for good, its tooltip says why, and
    it starts nothing. None for a file the device was still writing or that is no FIT file."""
    from openexpress import fit

    found = read.summary
    if found is None or found.state is fit.FileState.STILL_BEING_WRITTEN:
        return None
    words = _("Export as GPX…")
    button = Gtk.Button(icon_name="document-save-as-symbolic", valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.update_property([Gtk.AccessibleProperty.LABEL], [words])
    if not found.has_positions:
        button.set_sensitive(False)
        button.set_tooltip_text(_("No GPS positions in this activity"))
        return button
    button.set_tooltip_text(words)
    button.connect("clicked", lambda clicked: _export(win, read, clicked))
    button.set_sensitive(not win.busy)
    win._action_buttons.append(button)
    return button


def activity_row(win, read) -> Adw.ActionRow:
    """The row of an activity (activities.Read): its sport as the title, the details in one
    caption that wraps, the export button; the row of a FIT file opens the page of its
    activity (an arrow at its end)."""
    row = Adw.ActionRow(title=GLib.markup_escape_text(activity_title(read)),
                        subtitle=GLib.markup_escape_text(activity_caption(read)))
    button = export_button(win, read)
    if button is not None:
        row.add_suffix(button)
    if read.summary is not None:
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.set_activatable(True)
        row.connect("activated", lambda _row: _open(win, read))
    return row


def _open(win, read) -> None:
    from openexpress.gui.activity_page import open_activity  # it imports this module

    open_activity(win, read)


def _export(win, read, button: Gtk.Button) -> None:
    """Asks where to save the activity's GPX file (the file dialog suggests its name and the
    GPX folder), then writes it in a task on `button`, and says where in a toast."""
    from openexpress.activities import export_name

    if win.busy:
        win.toast(_("Another operation is in progress"))
        return
    found = win.activities
    name = export_name(read.summary, read.activity)
    dialog = Gtk.FileDialog(title=_("Export as GPX"), initial_name=f"{name}.gpx")
    folder = paths.default_gpx_dir()
    if folder.is_dir():
        dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))

    def chosen(dlg, result) -> None:
        try:
            target = dlg.save_finish(result)
        except GLib.Error:
            return  # not chosen
        if win.activities is not found:
            return  # another device opened meanwhile
        win.run_task(_("Exporting as GPX…"), lambda: _write_gpx(found, read, name, target),
                     lambda written: _saved(win, *written), button=button,
                     device=read.activity.source is Source.DEVICE)

    dialog.save(win, None, chosen)


def _write_gpx(found, read, name: str, target) -> tuple[str, int]:
    """In the task's thread: the activity's file read again and its GPX written to `target`
    (a Gio.File the file dialog gave, over a file there when the dialog said so)."""
    from openexpress import fit, gpx

    data = found.read_file(read.activity)
    if data is None:
        raise ExportFailed(activity_file_error())
    try:
        track = gpx.track(data, name=name)
    except fit.FitError as e:
        raise ExportFailed(_("This activity could not be exported: {error}").format(
            error=e)) from e
    try:
        target.replace_contents(track, None, False, Gio.FileCreateFlags.REPLACE_DESTINATION,
                                None)
    except GLib.Error as e:
        log.info("activities page: the GPX file could not be written (%s)", e.domain)
        raise ExportFailed(_("The GPX file could not be written there. Choose another "
                             "folder.")) from e
    return target.get_basename(), len(track)


def _saved(win, file_name: str, size: int) -> None:
    log.info("activities page: a GPX file written (%s)", applog.size(size))
    # TRANSLATORS: a toast after an activity was exported ("Saved as 2026-10-30 07.00 Running.gpx")
    win.toast(_("Saved as {name}").format(name=file_name))


def _no_activities(sec) -> None:
    """The page without any activity: the page's icon and why, in its content."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER,
                  margin_top=24, margin_bottom=24)
    box.append(Gtk.Image(icon_name=PAGE_ICON, pixel_size=64))
    # TRANSLATORS: title of the Activities page when there are none
    words = Gtk.Label(label=_("No Activities"), wrap=True, justify=Gtk.Justification.CENTER)
    words.add_css_class("title-2")
    box.append(words)
    box.append(Gtk.Label(label=_("There are none on the device or in its backups"), wrap=True,
                         justify=Gtk.Justification.CENTER, css_classes=["dim-label"]))
    sec.group().add(box)


class ActivitiesPage:
    """The Activities page and its tasks."""

    def load_activities(self) -> None:
        from openexpress import activities

        s = self.session
        sec = self.sections["activities"]
        if not activities.keeps_activities(s.device):
            # As a page that does not apply to the device: information, nothing read.
            sec.clear()
            sec.info.set_icon_name(PAGE_ICON)
            sec.info.set_title(sec.title)
            sec.info.set_description(GLib.markup_escape_text(
                _("This device does not keep activities")))
            sec.show("info")
            return
        found = self.activities
        if found is None or found.storage is not s.storage:
            found = activities.DeviceActivities(s.device, s.storage, paths.default_backup_dir())
        # Read again (Refresh): as many as were shown, what was read before kept.
        count = max(activities.PAGE, len(found.read))
        self._load("activities", _("Reading Activities…"),
                   lambda: _read(found, count, self.progress), self.show_activities)

    def show_activities(self, found) -> None:
        """The rows of the activities read (`found`: activities.DeviceActivities), the button
        for more when some are left."""
        if self.session is None:
            return
        self.activities = found
        sec = self.sections["activities"]
        self.activities_list = self.activities_more = None
        if not found.listing:
            _no_activities(sec)
            return
        self.activities_list = sec.group(description=GLib.markup_escape_text(
            activities_count_text(len(found.listing), found.only_in_backups)))
        for read in found.read:
            self.activities_list.add(activity_row(self, read))
        _show_more_button(self, found)
        log.info("activities page: %d shown, %d more to read", len(found.read), found.left)


def _show_more_button(win, found) -> None:
    """The button below the rows that reads the next activities, when some are left (the one
    before it goes)."""
    from openexpress import activities

    sec = win.sections["activities"]
    if win.activities_more is not None:
        sec.page.remove(win.activities_more)
        sec.groups.remove(win.activities_more)
        win.activities_more = None
    if not found.left:
        return
    count = min(activities.PAGE, found.left)
    # TRANSLATORS: button below the rows of the Activities page: read the next ones
    button = win._button(ngettext("Show {count} More", "Show {count} More", count).format(
        count=count), lambda clicked: _read_more(win, clicked), style="pill", with_button=True)
    button.set_halign(Gtk.Align.CENTER)
    win.activities_more = sec.group()
    win.activities_more.add(button)


def _read_more(win, button: Gtk.Button) -> None:
    """Reads the next activities, a task on `button`, and adds their rows."""
    found = win.activities
    if found is None:
        return

    def shown(read) -> None:
        if win.activities is not found or win.activities_list is None:
            return  # another device opened meanwhile
        for r in read:
            win.activities_list.add(activity_row(win, r))
        _show_more_button(win, found)
        log.info("activities page: %d shown, %d more to read", len(found.read), found.left)

    win.run_task(_("Reading Activities…"), lambda: found.read_next(progress=win.progress),
                 shown, button=button)
