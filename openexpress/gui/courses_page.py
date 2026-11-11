"""The Courses page of the window (experimental): the courses on the open device, each with where
it came from, the GPX files waiting for it under a banner of the page, how many saved locations it
keeps, and Add Course…: a GPX file chosen (through the portal in the Flatpak, so from any
folder), checked and planned, a question with the course's name to change, the copy; a row's
Remove: the course's file or the waiting file checked against the list, a question that says
what is lost, the removal. courses.py decides all of it; this page shows it and asks.
Has: nothing of its own in the window (the page reads the device each time it is shown).
CoursesPage is a mixin of MainWindow (gui/app.py), without __init__ of its own; what it uses of
the window (session, run_task, sections, toast, error, …) is the window's."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402 - imports must follow gi.require_version

from openexpress import courses  # noqa: E402
from openexpress.course_gpx import CourseFileError  # noqa: E402
from openexpress.gui import course_map  # noqa: E402
from openexpress.gui.device_page import device_name  # noqa: E402
from openexpress.gui.tasks import read_again_after_failure, writes_to_device  # noqa: E402
from openexpress.gui.window_tasks import DIALOG_MARGINS, WRAP_BOX, Answer, confirm  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    row_ascent_text,
    row_distance_text,
    space_text,
)
from openexpress.i18n import _, caption, human_size, ngettext, npgettext, pgettext  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

log = logging.getLogger(__name__)

# A trail of switchbacks, its start a filled disc (packaging/icons/courses.svg): the side
# panel's and the page's without a course.
PAGE_ICON = f"{APP_ID}-courses-symbolic"
# The width of the question's content: a file's name on one line beside its title (libadwaita
# gives a question 312 px at most); never more than the window leaves a dialog's content
# (DIALOG_MARGINS).
QUESTION_WIDTH = 480


# --- the words of the page -----------------------------------------------------------------------


def course_caption(course: courses.Course) -> str:
    """Under a course's name: its distance, ascent and the day its file was made: by the
    device, or by Garmin Connect when it sent the course, not the day the course was created
    there (each left out when its file does not say)."""
    parts = []
    if course.distance_m:
        parts.append(row_distance_text(course.distance_m))
    if course.ascent_m is not None:
        parts.append(row_ascent_text(course.ascent_m))
    if course.made is not None:
        parts.append(datetime.fromtimestamp(course.made).strftime(_("%Y-%m-%d")))
    return caption(*parts)


def _points(line) -> str:
    if line.kind == "track":
        # TRANSLATORS: the points of a track of a GPX file, on the Courses page (not its
        # waypoints)
        return npgettext("course file", "{count} track point", "{count} track points",
                         line.points).format(count=line.points)
    # TRANSLATORS: the points of a route of a GPX file, on the Courses page (not its waypoints)
    return npgettext("course file", "{count} route point", "{count} route points",
                     line.points).format(count=line.points)


def contents_text(gpx, short: bool = False) -> str:
    """What a GPX file holds, each kind of it ("track, 12.3 km, ↑ 321 m, 120 track points");
    `short`: a track by its distance alone (the row of a waiting file)."""
    tracks = [line for line in gpx.lines if line.kind == "track"]
    routes = [line for line in gpx.lines if line.kind == "route"]
    parts = []
    if len(tracks) == 1:
        (track,) = tracks
        # TRANSLATORS: what a GPX file holds, on the Courses page: one track
        words = [pgettext("course file", "track"), row_distance_text(track.distance_m)]
        if not short:
            if track.ascent_m is not None:
                words.append(row_ascent_text(track.ascent_m))
            words.append(_points(track))
        parts.append(", ".join(words))
    elif tracks:
        parts.append(npgettext("course file", "{count} track, {distance}",
                               "{count} tracks, {distance}", len(tracks)).format(
            count=len(tracks),
            distance=row_distance_text(sum(track.distance_m for track in tracks))))
    if len(routes) == 1:
        # TRANSLATORS: what a GPX file holds, on the Courses page: one route
        parts.append(", ".join([pgettext("course file", "route"), _points(routes[0])]))
    elif routes:
        parts.append(npgettext("course file", "{count} route", "{count} routes",
                               len(routes)).format(count=len(routes)))
    if gpx.waypoints:
        # TRANSLATORS: what a GPX file holds, on the Courses page: its waypoints, which the
        # device saves as saved locations
        parts.append(npgettext("course file", "{count} waypoint", "{count} waypoints",
                               gpx.waypoints).format(count=gpx.waypoints))
    return "; ".join(parts)


def waiting_banner(count: int) -> str:
    # TRANSLATORS: banner of the Courses page: GPX files copied to the device that it has not
    # imported yet (it does when it is unplugged)
    # (A hard space before the dash: a line never starts with it.)
    return ngettext("{count} file waiting\u00a0– unplug the device so it can import it",
                    "{count} files waiting\u00a0– unplug the device so it can import them",
                    count).format(count=count)


def remove_heading(name: str) -> str:
    # TRANSLATORS: heading of the confirmation before a course or a GPX file waiting for the device
    # is removed from it
    return _("Remove “{name}” From the Device?").format(name=name)


def removed_toast(name: str) -> str:
    return _("Removed {name}").format(name=name)


def copied_toast(name: str) -> str:
    # TRANSLATORS: toast after a GPX file was copied to the device; {name}: its course's name,
    # else the file's
    return _("Copied {name}; unplug the device so it can import the file").format(name=name)


def question_heading(plan) -> str:
    if not plan.gpx.courses:
        # TRANSLATORS: heading of the confirmation before a GPX file of waypoints only is copied
        return _("Copy the Waypoints to the Device?")
    # TRANSLATORS: heading of the confirmation before a GPX file is copied to the device
    return ngettext("Copy the Course to the Device?", "Copy the Courses to the Device?",
                    plan.gpx.courses)


def file_text(name: str, size: int) -> str:
    """A file's name and size on the device; the name may break after each "_" (a zero-width
    space), so that in a narrow window the name breaks, not the line before its size."""
    return caption(name.replace("_", "_\u200b"), human_size(size))


# --- the widgets of the page ---------------------------------------------------------------------


def _chip(words: str, accent: bool) -> Gtk.Label:
    return Gtk.Label(label=words, valign=Gtk.Align.CENTER,
                     css_classes=["oe-chip", "accent" if accent else "dimmed"])


def _row(title: str, subtitle: str, origin: str | None = None,
         remove: Gtk.Widget | None = None) -> Gtk.ListBoxRow:
    """A row of a course or of a waiting file: its name with the chip of where it came from
    beside it (on the next line when the window is too narrow), the caption under them, and its
    `remove` button at the end."""
    name = Gtk.Label(label=title, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
    if origin is None:
        head = name
    else:
        chip = _chip(origin, accent=origin == courses.ORIGIN_OPENEXPRESS)
        if WRAP_BOX is not None:
            head = WRAP_BOX(child_spacing=8, line_spacing=4, halign=Gtk.Align.START)
        else:  # libadwaita before 1.7
            head = Gtk.Box(spacing=8)
        head.append(name)
        head.append(chip)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                   margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
    text.append(head)
    if subtitle:
        text.append(Gtk.Label(label=subtitle, xalign=0, wrap=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR,
                              css_classes=["dim-label", "caption"]))
    line = Gtk.Box(spacing=6, margin_end=6)
    line.append(text)
    if remove is not None:
        line.append(remove)
    row = Gtk.ListBoxRow(activatable=False, child=line)
    row.update_property([Gtk.AccessibleProperty.LABEL],
                        [", ".join(p for p in (title, origin, subtitle) if p)])
    return row


def _empty(win, sec) -> None:
    """No course and no file waiting: the page's icon, that there is none, and Add Course…"""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER,
                  margin_top=48, margin_bottom=24)
    box.append(Gtk.Image(icon_name=PAGE_ICON, pixel_size=64, css_classes=["dim-label"]))
    box.append(Gtk.Label(label=_("No Courses on the Device"), wrap=True,
                         justify=Gtk.Justification.CENTER, css_classes=["title-2"]))
    box.append(Gtk.Label(
        label=_("Copy a GPX file with a track or a route\u00a0– the device turns it into a "
                "course"),
        wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=48))
    add = win._button(_("Add Course…"), win.choose_course)
    add.set_halign(Gtk.Align.CENTER)
    add.add_css_class("suggested-action")
    add.add_css_class("pill")
    add.set_margin_top(12)
    add.set_tooltip_text(add_tooltip())
    box.append(add)
    sec.group().add(box)


def _saved_locations(sec, seen) -> None:
    """The group Saved Locations, when the device holds a locations file (Device Status counts
    it under Courses and Locations and leads to this page), also with no course; none without
    such a file."""
    if not seen.locations_file and "locations" not in seen.unreadable:
        return
    # TRANSLATORS: group of the Courses page: the device's saved locations
    g = sec.group(pgettext("courses", "Saved Locations"))
    # TRANSLATORS: the row of the saved locations kept on the device
    row = Adw.ActionRow(title=pgettext("saved locations", "On the Device"),
                        subtitle=_("Waypoints from GPX files and locations saved on the device"))
    row.add_suffix(Gtk.Label(
        label=(str(seen.locations) if seen.locations is not None
               # TRANSLATORS: in place of how many saved locations the device keeps, when its
               # locations file does not read whole
               else pgettext("saved locations", "Could not be counted")),
        wrap=True, justify=Gtk.Justification.RIGHT, css_classes=["dim-label"]))
    g.add(row)


def add_tooltip() -> str:
    # TRANSLATORS: tooltip of Add Course… on the Courses page
    return _("Copy a GPX file to the device (experimental)")


class _FactLayout(Gtk.LayoutManager):
    """A line of the question: its name and its value side by side when both fit whole on one
    line; else the value under the name, in the whole width. The name is never broken, so each
    line takes the room its own name needs."""

    __gtype_name__ = "OpenExpressFactLayout"
    SPACING = 16  # between the name and the value side by side
    GAP = 2  # between the name and the value under it

    def do_get_request_mode(self, widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def _side_by_side(self, name, value, width: int) -> bool:
        whole = (name.measure(Gtk.Orientation.HORIZONTAL, -1)[1] + self.SPACING
                 + value.measure(Gtk.Orientation.HORIZONTAL, -1)[1])
        return width < 0 or whole <= width

    def do_measure(self, widget, orientation, for_size):
        name = widget.get_first_child()
        value = name.get_next_sibling()
        if orientation == Gtk.Orientation.HORIZONTAL:
            n, v = name.measure(orientation, -1), value.measure(orientation, -1)
            return max(n[0], v[0]), n[1] + self.SPACING + v[1], -1, -1
        if self._side_by_side(name, value, for_size):
            n, v = name.measure(orientation, -1), value.measure(orientation, -1)
            return max(n[0], v[0]), max(n[1], v[1]), -1, -1
        n, v = name.measure(orientation, for_size), value.measure(orientation, for_size)
        return n[0] + self.GAP + v[0], n[1] + self.GAP + v[1], -1, -1

    def do_allocate(self, widget, width, height, baseline):
        name = widget.get_first_child()
        value = name.get_next_sibling()
        if self._side_by_side(name, value, width):
            named = name.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            name.allocate(named, height, -1, None)
            value.allocate(width - named - self.SPACING, height, -1, Gsk.Transform().translate(
                Graphene.Point().init(named + self.SPACING, 0)))
            return
        tall = name.measure(Gtk.Orientation.VERTICAL, width)[1]
        name.allocate(width, tall, -1, None)
        value.allocate(width, max(0, height - tall - self.GAP), -1, Gsk.Transform().translate(
            Graphene.Point().init(0, tall + self.GAP)))


def _fact(name: str, value: Gtk.Label) -> Gtk.Box:
    """A line of the question (_FactLayout): its name, never broken, and its value."""
    line = Gtk.Box()
    line.set_layout_manager(_FactLayout())
    line.append(Gtk.Label(label=name, xalign=0, valign=Gtk.Align.START,
                          css_classes=["dim-label"]))
    line.append(value)
    return line


def _value(text: str) -> Gtk.Label:
    return Gtk.Label(label=text, xalign=1, hexpand=True, wrap=True,
                     wrap_mode=Pango.WrapMode.WORD_CHAR, css_classes=["heading"])


def _note(text: str, warning: bool = False) -> Gtk.Label:
    note = Gtk.Label(label=text, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                     css_classes=["oe-note"])
    if warning:
        note.add_css_class("warning")
    return note


def question_width(window_width: int) -> int:
    return max(0, min(QUESTION_WIDTH, window_width - DIALOG_MARGINS))


def question_body(plan, entered: dict, width: int = 0) -> Gtk.Widget:
    """What the question before a copy shows under its heading: the file on a map (with
    libshumate), the course's name to change (a file of one course), what the file holds, its
    name and size on the device (following the name), the space left, and what the device does
    with it. A name typed goes into `entered` (the answer takes it from there, never from the
    widget). `width`: its own (none: the dialog's)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    if width:
        box.set_size_request(width, -1)
    if course_map.available():
        box.append(course_map.CourseMap(plan.gpx))
    file_value = _value(file_text(plan.name, plan.size))
    if plan.course is not None:
        names = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        # TRANSLATORS: the entry of the confirmation before a GPX file is copied: the name the
        # device gives the course
        entry = Adw.EntryRow(title=pgettext("course", "Course Name"), text=plan.course)
        entry.connect("changed", lambda e: entered.__setitem__("name", e.get_text()))
        # The file's name on the device follows the course's (a binding: no handler holds
        # a widget).
        entry.bind_property("text", file_value, "label", GObject.BindingFlags.DEFAULT,
                            lambda _binding, text: file_text(*plan.file_for(text)))
        names.append(entry)
        box.append(names)
    facts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    # TRANSLATORS: a line of the confirmation before a GPX file is copied: what the file holds
    facts.append(_fact(pgettext("course", "Contents"), _value(contents_text(plan.gpx))))
    # TRANSLATORS: a line of the confirmation before a GPX file is copied: its name and size there
    facts.append(_fact(pgettext("course", "File on the Device"), file_value))
    free = None if plan.free is None else max(0, plan.free - plan.size)
    # TRANSLATORS: a line of the confirmation before a GPX file is copied: the space left after it
    facts.append(_fact(pgettext("course", "Free After Copying"),
                       _value(human_size(free) if free is not None else space_text(None))))
    box.append(facts)
    if plan.same_waiting is not None:
        box.append(_note(_("The same file is already waiting on the device as {name}.").format(
            name=plan.same_waiting)))
    for said, warning in courses.what_the_device_does(plan.gpx):
        box.append(_note(said, warning))
    return box


def _copy(s, path: Path, name: str | None, progress):
    """The copy under the name the question was left with: planned again with the device (its
    folder and space as they are now), then written. The plan written."""
    plan = courses.plan_add(s.device, s.storage, path, name)
    courses.add(plan, s.device, s.storage, progress=progress)
    return plan


class CoursesPage:
    """The Courses page and its tasks."""

    def load_courses(self) -> None:
        s = self.session
        sec = self.sections["courses"]
        if not courses.offered(s.device):
            # As a page that does not apply to the device: information, nothing read.
            sec.clear()
            sec.info.set_icon_name(PAGE_ICON)
            sec.info.set_title(sec.title)
            sec.info.set_description(GLib.markup_escape_text(
                # TRANSLATORS: the Courses page of a device that does not take GPX files
                _("This device does not make courses of GPX files")))
            sec.show("info")
            return
        self._load("courses", _("Reading Courses…"),
                   lambda: courses.overview(s.device, s.storage), self.show_courses)

    def show_courses(self, seen: courses.Overview) -> None:
        """The courses read, the files waiting (and the banner), the saved locations; without a
        course or a file waiting, that there is none and Add Course…"""
        if self.session is None:
            return
        sec = self.sections["courses"]
        if seen.waiting:
            sec.banner.set_title(waiting_banner(len(seen.waiting)))
            sec.banner.set_revealed(True)
        if not seen.courses and not seen.waiting and not seen.unreadable:
            _empty(self, sec)
            _saved_locations(sec, seen)
            log.info("courses page: none on the device")
            return
        # TRANSLATORS: group of the Courses page: the courses the device keeps
        g = sec.group(pgettext("courses", "On the Device"))
        add = self._button(_("Add Course…"), self.choose_course)
        add.set_tooltip_text(add_tooltip())
        g.set_header_suffix(add)
        if "courses" in seen.unreadable:
            # TRANSLATORS: a row of the Courses page when the courses could not be read
            g.add(Adw.ActionRow(title=_("Courses"),
                                subtitle=_("Could not be read from the device")))
        elif not seen.courses:
            g.add(Adw.ActionRow(title=_("No Courses on the Device")))
        for course in seen.courses:
            g.add(_row(course.title, course_caption(course), course.origin,
                       self._remove_button(course)))
        if seen.waiting or "waiting" in seen.unreadable:
            # TRANSLATORS: group of the Courses page: GPX files the device has not imported yet
            g = sec.group(pgettext("courses", "Waiting for the Device"))
            if "waiting" in seen.unreadable:
                # TRANSLATORS: a row of the Courses page when the GPX files waiting for the
                # device could not be read
                g.add(Adw.ActionRow(title=_("Files Waiting"),
                                    subtitle=_("Could not be read from the device")))
            for waiting in seen.waiting:
                parts = [contents_text(waiting.gpx, short=True)] if waiting.gpx else []
                if waiting.title != waiting.filename:
                    parts.append(waiting.filename)
                if waiting.gpx is None:
                    parts.append(human_size(waiting.size))
                g.add(_row(waiting.title, caption(*parts),
                           remove=self._remove_button(waiting)))
        _saved_locations(sec, seen)
        log.info("courses page: %d courses, %d files waiting", len(seen.courses),
                 len(seen.waiting))

    def choose_course(self) -> None:
        """Add Course…: a GPX file chosen through the file chooser (the portal in the Flatpak:
        any folder), then checked and asked about (add_course)."""
        dialog = Gtk.FileDialog(title=_("Choose a GPX File"))
        only_gpx = Gtk.FileFilter(name=_("GPX files (*.gpx)"))
        only_gpx.add_pattern("*.gpx")
        only_gpx.add_pattern("*.GPX")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(only_gpx)
        dialog.set_filters(filters)

        def chosen(dlg, res):
            try:
                path = dlg.open_finish(res).get_path()
            except GLib.Error:
                return  # cancelled
            if path is None:
                self.toast(_("Choose a file stored on this computer"))
                return
            self.add_course(Path(path))

        dialog.open(self, None, chosen)

    def _course_refused(self, e, title: str | None = None) -> bool:
        """A refusal of courses.py or course_gpx.py over the page, under `title` (by default,
        that a file was not copied); True when it was one."""
        if isinstance(e, CourseFileError | courses.CourseError):
            # TRANSLATORS: title of the message why a GPX file was not copied
            self.error(title or _("Course Not Added"), str(e))
            return True
        return False

    def _remove_button(self, shown) -> Gtk.Button:
        """The trash button of a row: its tooltip names what it removes, for a screen reader
        too."""
        return self._trash(_("Remove {name}").format(name=shown.title),
                           lambda: self.ask_remove_course(shown))

    def ask_remove_course(self, shown) -> None:
        """Remove: the file checked against what the list showed (courses.plan_remove), the
        question with what is lost, then the removal through the core, as `courses remove`
        does. A refusal or a failure is said, and the page is read again."""
        s = self.session

        def refused(e):
            # TRANSLATORS: title of the message why a course or a waiting file was not removed
            return self._course_refused(e, _("Course Not Removed"))

        def removed(plan):
            self.toast(removed_toast(plan.title))
            self.load_section("courses", force=True)

        def remove(plan):
            self.run_task(_("Removing {name}…").format(name=plan.title),
                          writes_to_device(lambda: courses.remove(plan, s.device, s.storage)
                                           or plan),
                          removed, read_again_after_failure(self, "courses", refused))

        def ask(plan):
            confirm(self, "course-remove", remove_heading(plan.title), courses.removal_note(plan),
                    Answer("remove", _("Remove"), lambda: remove(plan), look="destructive"),
                    default="cancel", private=True)

        self.run_task(_("Checking the File…"),
                      lambda: courses.plan_remove(s.device, s.storage, shown), ask,
                      read_again_after_failure(self, "courses", refused))

    def add_course(self, path: Path) -> None:
        """The file checked and planned with the device, then the question; Copy copies it
        under the name the question was left with."""
        s = self.session

        def copied(plan):
            self.toast(copied_toast(plan.course or plan.gpx.path.name))
            self.load_section("courses", force=True)

        def copy(plan, entered):
            name = entered["name"]
            if name is not None and name.strip() == (plan.course or ""):
                name = None  # unchanged: the file as it is
            self.run_task(
                _("Copying the Course…"),
                writes_to_device(lambda: _copy(s, path, name, self.progress)),
                copied, read_again_after_failure(self, "courses", self._course_refused))

        def ask(plan):
            entered = {"name": None}
            confirm(self, "course-add", question_heading(plan), device_name(s),
                    Answer("copy", _("Copy"), lambda: copy(plan, entered), look="suggested"),
                    default="copy", wide=True, private=True,
                    extra=question_body(plan, entered, question_width(self.get_width())))

        self.run_task(_("Checking the File…"),
                      lambda: courses.plan_add(s.device, s.storage, path), ask,
                      self._course_refused)
