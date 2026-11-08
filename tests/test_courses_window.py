"""The Courses page of the window (gui/courses_page.py): the courses with the chip of where each
came from, the files waiting under the page's banner, the saved locations, the page without a
course, a device that makes no courses of GPX files, the title with its chip, and Add Course…:
the question (the course's name, what the file holds, its name on the device following the
name, what the device does), the copy and its toast. Made-up files on a folder taken as the
device; nothing reaches a device."""

import time
import types

import pytest
from fakes import (
    COURSE_UNIT,
    course_device,
    course_fit,
    descendants,
    gpx_file,
    gpx_route,
    gpx_track,
    gpx_waypoint,
    locations_fit,
    page_window,
)

from openexpress import courses
from openexpress.device.storage import LocalStorage

pytestmark = pytest.mark.needs_display


@pytest.fixture
def drive(tmp_path):
    root = tmp_path / "device"
    for folder in ("Garmin/NewFiles", "Garmin/Courses", "Garmin/Locations"):
        (root / folder).mkdir(parents=True)
    return root


def courses_window(drive, offered=True):
    from openexpress.gui.app import MainWindow

    win, section = page_window("courses")
    win.session = types.SimpleNamespace(
        device=course_device(gpx_folder="Garmin/NewFiles" if offered else None),
        storage=LocalStorage(drive), unit_info_answer=None)
    for name in ("load_courses", "show_courses", "choose_course", "add_course",
                 "_course_refused", "_remove_button", "ask_remove_course"):
        setattr(win, name, types.MethodType(getattr(MainWindow, name), win))
    win.get_width = lambda: 1000  # the window's
    win.loads = []
    win.load_section = lambda key, force=False: (win.loads.append(key), win.load_courses())
    win.progress = lambda text: None
    win.toasts, win.errors = [], []
    win.toast = win.toasts.append
    win.error = lambda title, text: win.errors.append((title, text))
    return win, section


def labels(widget) -> list[str]:
    from gi.repository import Gtk

    return [w.get_label() for w in descendants(widget)
            if isinstance(w, Gtk.Label) and w.get_visible() and w.get_label()]


def rows(group) -> list[list[str]]:
    from gi.repository import Gtk

    return [labels(r) for r in descendants(group) if isinstance(r, Gtk.ListBoxRow)
            and r.get_child() is not None and labels(r)]


def now() -> int:
    return int(time.time())


def test_the_courses_the_files_waiting_and_the_locations(drive, clock):
    (drive / "Garmin/Courses/Along.fit").write_bytes(course_fit(
        "Along the River", made=now(), product=courses.CONNECT_PRODUCT))
    # Made of a route: no ascent, which the row leaves out.
    (drive / "Garmin/Courses/Hand.fit").write_bytes(course_fit("By Hand", made=now(), ascent=None))
    (drive / "Garmin/NewFiles/Loop.gpx").write_text(gpx_file(gpx_track()))
    (drive / "Garmin/Locations/Locations.fit").write_bytes(locations_fit(14))
    win, section = courses_window(drive)
    win.load_courses()
    assert section.stack.get_visible_child_name() == "content"
    assert [g.get_title() for g in section.groups] == [
        "On the Device", "Waiting for the Device", "Saved Locations"]
    assert rows(section.groups[0]) == [
        ["Along the River", "Garmin Connect",
         "12.3\u00a0km\u00a0· ↑\u00a0321\u00a0m\u00a0· 2026-10-21"],
        ["By Hand", "12.3\u00a0km\u00a0· 2026-10-21"]]
    assert rows(section.groups[1]) == [["Morning Loop", "track, 1.4\u00a0km\u00a0· Loop.gpx"]]
    assert "14" in labels(section.groups[2])
    assert section.banner.get_revealed()
    assert section.banner.get_title() == (
        "1 file waiting\u00a0– unplug the device so it can import it")


def test_the_day_the_device_made_a_course_and_the_chip_of_openexpress(drive, tmp_path, clock):
    st = LocalStorage(drive)
    plan = courses.plan_add(course_device(), st, _gpx(tmp_path, gpx_track("Hill")))
    courses.add(plan, course_device(), st)
    (drive / plan.path).unlink()  # the device took it
    (drive / "Garmin/Courses/Hill.fit").write_bytes(course_fit("Hill", made=now() + 60))
    win, section = courses_window(drive)
    win.load_courses()
    (row,) = rows(section.groups[0])
    assert row[:2] == ["Hill", "openExpress"]
    assert row[2].endswith("2026-10-21")  # the clock's day
    assert not section.banner.get_revealed()  # nothing waiting


def test_without_a_course_or_a_file_waiting_the_page_says_so(drive):
    win, section = courses_window(drive)
    win.load_courses()
    assert labels(section.page) == [
        "No Courses on the Device",
        "Copy a GPX file with a track or a route\u00a0– the device turns it into a course.",
        "Add Course…"]
    assert not section.banner.get_revealed()


def test_a_device_that_makes_no_courses_of_gpx_files(drive):
    win, section = courses_window(drive, offered=False)
    win.load_courses()
    assert section.stack.get_visible_child_name() == "info"
    assert section.info.get_description() == "This device does not make courses of GPX files"


def test_the_title_in_the_header_carries_the_chip():
    from openexpress.gui.device_page import Section, section_title

    assert labels(section_title(Section("courses", "Courses", "x", "…", chip="experimental"))) == [
        "Courses", "experimental"]
    assert section_title(Section("maps", "Maps", "x", "…")) is None


def _gpx(tmp_path, body, name="loop.gpx"):
    path = tmp_path / name
    path.write_text(gpx_file(body), encoding="utf-8")
    return path


@pytest.fixture
def asked(in_the_window):
    """The questions the page asks: (heading, body, answers, the widget below the text)."""
    questions = []

    def confirm(_parent, name, heading, body, *answers, extra=None, **_kw):
        questions.append(types.SimpleNamespace(name=name, heading=heading, body=body,
                                               answers=answers, extra=extra))

    in_the_window("confirm", confirm)
    return questions


def test_adding_a_course_asks_with_its_name_and_copies_it_under_the_name_left(drive, tmp_path,
                                                                             asked, clock):
    from gi.repository import Adw

    win, section = courses_window(drive)
    win.load_courses()
    win.add_course(_gpx(tmp_path, gpx_track()))
    (q,) = asked
    assert (q.name, q.heading, q.body) == ("course-add", "Copy the Course to the Device?",
                                           "Made-up Edge")
    words = labels(q.extra)
    at = words.index("Contents")
    assert words[at:at + 5] == ["Contents", "track, 1.4\u00a0km, ↑\u00a010\u00a0m, 3 track points",
                                "File on the Device", "Morning_\u200bLoop.GPX\u00a0· 340\u00a0B",
                                "Free After Copying"]
    assert words[-1] == "The device makes the course when you unplug the cable."
    (entry,) = [w for w in descendants(q.extra) if isinstance(w, Adw.EntryRow)]
    assert entry.get_text() == "Morning Loop"
    entry.set_text("Pętla nad jeziorem")
    assert "Petla_\u200bnad_\u200bjeziorem.GPX\u00a0· 347\u00a0B" in labels(q.extra)  # it follows the name
    (copy,) = q.answers
    assert copy.label == "Copy"
    copy.run()
    assert win.run_task.written == ["Copying the Course…"]
    written = drive / "Garmin/NewFiles/Petla_nad_jeziorem.GPX"
    assert b"<name>P\xc4\x99tla nad jeziorem</name>" in written.read_bytes()
    assert win.toasts == [
        "Copied “Pętla nad jeziorem”. Unplug the device so it can import the file."]
    assert win.loads == ["courses"]  # the page read again: the file waits now
    assert rows(section.groups[1])[0][0] == "Pętla nad jeziorem"
    assert [c["courses"] for c in courses.recorded(COURSE_UNIT)] == [["Pętla nad jeziorem"]]


def test_a_name_left_as_it_was_copies_the_file_as_it_is(drive, tmp_path, asked):
    win, _section = courses_window(drive)
    local = _gpx(tmp_path, gpx_track())
    win.add_course(local)
    asked[0].answers[0].run()
    assert (drive / "Garmin/NewFiles/Morning_Loop.GPX").read_bytes() == local.read_bytes()


def test_a_route_is_warned_of(drive, tmp_path, asked):
    from gi.repository import Gtk

    win, _section = courses_window(drive)
    win.add_course(_gpx(tmp_path, gpx_route()))
    (q,) = asked
    (warning,) = [w for w in descendants(q.extra) if isinstance(w, Gtk.Label)
                  and w.has_css_class("warning")]
    assert warning.get_label().startswith("Garmin devices join the points of a route with "
                                          "straight lines, not roads.")
    assert warning.has_css_class("oe-note")
    assert "route, 3 route points" in labels(q.extra)


def test_waypoints_only_take_no_name(drive, tmp_path, asked):
    from gi.repository import Adw

    win, _section = courses_window(drive)
    win.add_course(_gpx(tmp_path, gpx_waypoint() + gpx_waypoint("Shelter", 52.1, 13.1),
                        "points.gpx"))
    (q,) = asked
    assert q.heading == "Copy the Waypoints to the Device?"
    assert not [w for w in descendants(q.extra) if isinstance(w, Adw.EntryRow)]
    assert labels(q.extra)[-1] == "Garmin devices save waypoints as saved locations."
    assert "2 waypoints" in labels(q.extra)


def test_several_tracks_ask_for_the_courses(drive, tmp_path, asked):
    win, _section = courses_window(drive)
    win.add_course(_gpx(tmp_path, gpx_track("First") + gpx_track("Second")))
    assert asked[0].heading == "Copy the Courses to the Device?"
    assert "The device makes 2 courses when you unplug the cable." in labels(asked[0].extra)


def test_a_file_that_is_not_copied_is_said_and_nothing_is_asked(drive, tmp_path, asked):
    win, _section = courses_window(drive)
    bad = tmp_path / "bad.gpx"
    bad.write_text("<not a gpx file")
    win.add_course(bad)
    assert asked == [] and win.errors == [("Course Not Added", "bad.gpx is not a GPX file.")]
    assert list((drive / "Garmin/NewFiles").iterdir()) == []


def test_the_chip_is_drawn_only_beside_the_whole_title():
    from gi.repository import Gtk

    from openexpress.gui.device_page import Section, section_title

    title = section_title(Section("courses", "Courses", "x", "…", chip="experimental"))
    holder = Gtk.Window(child=title)  # never shown
    title.measure(Gtk.Orientation.HORIZONTAL, -1)
    title.allocate(400, 40, -1, None)
    chip = title.get_last_child()
    assert chip.get_child_visible()
    assert chip.compute_point(title, _point(0, 0))[1].x > title.get_first_child().get_width()
    title.allocate(60, 40, -1, None)  # the narrowest window's header
    assert not chip.get_child_visible()
    holder.destroy()


def _point(x, y):
    from gi.repository import Graphene

    return Graphene.Point().init(x, y)


def test_saved_locations_that_could_not_be_counted(drive):
    (drive / "Garmin/Locations/Locations.fit").write_bytes(locations_fit(3, cut=60))
    (drive / "Garmin/NewFiles/Loop.gpx").write_text(gpx_file(gpx_track()))
    win, section = courses_window(drive)
    win.load_courses()
    words = labels(section.groups[-1])
    assert "Waypoints from GPX files and locations saved on the device" in words
    assert "Could not be counted" in words and "14" not in words


# --- removing ------------------------------------------------------------------------------------


@pytest.fixture
def told(monkeypatch):
    """What the widgets told a screen reader (update_property): (widget, {property: value})."""
    from gi.repository import Gtk

    said = []
    original = Gtk.Accessible.update_property

    def update_property(self, props, values):
        said.append((self, dict(zip(props, values, strict=True))))
        return original(self, props, values)

    monkeypatch.setattr(Gtk.Widget, "update_property", update_property, raising=False)
    return said


def trash_buttons(widget):
    from gi.repository import Gtk

    return [b for b in descendants(widget)
            if isinstance(b, Gtk.Button) and b.get_icon_name() == "user-trash-symbolic"]


def test_each_course_and_file_waiting_has_its_remove_button(drive, clock, told):
    from gi.repository import Gtk

    (drive / "Garmin/Courses/Hand.fit").write_bytes(course_fit("By Hand", made=now()))
    (drive / "Garmin/NewFiles/Loop.gpx").write_text(gpx_file(gpx_track()))
    win, section = courses_window(drive)
    win.load_courses()
    buttons = trash_buttons(section.page)
    assert [b.get_tooltip_text() for b in buttons] == ["Remove By Hand", "Remove Morning Loop"]
    assert [p[Gtk.AccessibleProperty.LABEL] for w, p in told if w in buttons] == [
        "Remove By Hand", "Remove Morning Loop"]


def _remove(section, title):
    """Its row's Remove… clicked."""
    from gi.repository import Gtk

    (row,) = [r for r in descendants(section.page)
              if isinstance(r, Gtk.ListBoxRow) and labels(r)[:1] == [title]]
    (button,) = trash_buttons(row)
    button.emit("clicked")


@pytest.mark.parametrize("kind, body", [
    ("connect", "The device keeps no other copy of it; Garmin Connect can send it again."),
    ("unknown", ("openExpress did not copy this course and it did not come from Garmin "
                 "Connect. The device keeps no other copy of it.")),
])
def test_removing_a_course_asks_then_removes_it(drive, clock, asked, kind, body):
    product = courses.CONNECT_PRODUCT if kind == "connect" else courses.DEVICE_PRODUCT
    course = drive / "Garmin/Courses/Along.fit"
    course.write_bytes(course_fit("Along the River", made=now(), product=product))
    win, section = courses_window(drive)
    win.load_courses()
    _remove(section, "Along the River")
    (q,) = asked
    assert (q.name, q.heading, q.body) == ("course-remove",
                                           "Remove “Along the River” From the Device?", body)
    (remove,) = q.answers
    assert (remove.label, remove.look) == ("Remove", "destructive")
    remove.run()
    assert not course.exists()
    assert win.run_task.written == ["Removing…"]
    assert win.toasts == ["Removed “Along the River” from the device."]
    assert win.loads == ["courses"]  # read again: none left
    assert labels(section.page)[0] == "No Courses on the Device"


def test_a_course_of_openexpress_is_said_so(drive, tmp_path, clock, asked):
    st = LocalStorage(drive)
    plan = courses.plan_add(course_device(), st, _gpx(tmp_path, gpx_track("Hill")))
    courses.add(plan, course_device(), st)
    (drive / plan.path).unlink()
    (drive / "Garmin/Courses/Hill.fit").write_bytes(course_fit("Hill", made=now() + 60))
    win, section = courses_window(drive)
    win.load_courses()
    _remove(section, "Hill")
    assert asked[0].body == "The device keeps no other copy of it."
    asked[0].answers[0].run()
    assert courses.recorded(COURSE_UNIT) == []  # its name taken off the record


def test_removing_a_file_waiting(drive, asked):
    waiting = drive / "Garmin/NewFiles/Loop.gpx"
    waiting.write_text(gpx_file(gpx_track()))
    win, section = courses_window(drive)
    win.load_courses()
    _remove(section, "Morning Loop")
    (q,) = asked
    assert (q.heading, q.body) == ("Remove “Morning Loop” From the Device?",
                                   "The device has not made a course of it yet.")
    q.answers[0].run()
    assert not waiting.exists() and win.toasts == ["Removed “Morning Loop” from the device."]


def test_cancel_removes_nothing(drive, clock, asked):
    course = drive / "Garmin/Courses/Hand.fit"
    course.write_bytes(course_fit("By Hand", made=now()))
    win, section = courses_window(drive)
    win.load_courses()
    _remove(section, "By Hand")
    assert len(asked) == 1  # the question, never answered: Cancel and Escape run nothing
    assert course.exists() and win.run_task.written == [] and win.toasts == []


def test_a_file_gone_before_the_question_is_said_and_the_list_read_again(drive, clock, asked):
    course = drive / "Garmin/Courses/Hand.fit"
    course.write_bytes(course_fit("By Hand", made=now()))
    win, section = courses_window(drive)
    win.load_courses()
    course.unlink()  # the device unplugged and the file removed elsewhere
    _remove(section, "By Hand")
    assert asked == []
    assert win.errors == [("Course Not Removed", "Hand.fit is no longer on the device.")]
    assert win.loads == ["courses"]
    empty = "Copy a GPX file with a track or a route\u00a0– the device turns it into a course."
    assert labels(section.page)[:2] == ["No Courses on the Device", empty]


def test_a_file_changed_after_the_question_is_not_removed(drive, clock, asked):
    course = drive / "Garmin/Courses/Hand.fit"
    course.write_bytes(course_fit("By Hand", made=now()))
    win, section = courses_window(drive)
    win.load_courses()
    _remove(section, "By Hand")
    course.write_bytes(course_fit("By Hand", made=now(), points=30))
    asked[0].answers[0].run()
    assert course.exists() and win.toasts == []
    said = "Hand.fit has changed since the list was shown, so it is not removed."
    assert win.errors == [("Course Not Removed", said)]
    assert win.loads == ["courses"]


# --- the map in the question ---------------------------------------------------------------------


def _file(body):
    from pathlib import Path

    from openexpress import course_gpx

    return course_gpx.parse(gpx_file(body).encode(), Path("made-up.gpx"))


def test_the_map_draws_each_track_and_route_their_starts_and_the_waypoints():
    from openexpress.gui import activity_map, course_map

    if not course_map.available():
        pytest.skip("libshumate is not installed")
    gpx = _file(gpx_track("First") + gpx_track("Second", ((52.1, 13.0, 1), (52.1, 13.1, 2)))
                + gpx_route() + gpx_waypoint() + gpx_waypoint("Far", 52.3, 13.4))
    shown = course_map.CourseMap(gpx)
    assert [dashed for dashed, _points in shown.path.lines] == [False, False, True]
    assert [len(points) for _dashed, points in shown.path.lines] == [3, 2, 3]
    markers = [w for w in descendants(shown.map) if isinstance(w, activity_map.Shumate.Marker)]
    starts = [m for m in markers if m.get_child().get_content_width() == course_map.END_SIZE]
    places = [m for m in markers if m.get_child().get_content_width() == course_map.PLACE_SIZE]
    assert len(starts) + len(places) == len(markers)
    assert [m.get_latitude() for m in starts] == [52.0, 52.1, 52.0]
    assert [(m.get_latitude(), m.get_longitude()) for m in places] == [(52.0, 13.0),
                                                                     (52.3, 13.4)]
    assert shown.box == (52.0, 13.0, 52.3, 13.4)  # all of the file, its waypoints too
    assert not shown.map.get_can_target()  # a look at the file: not dragged, not zoomed
    assert shown.tiles is None and not shown.attribution.get_visible()  # no yes given
    assert activity_map.MapView in type(shown).__mro__


def test_with_the_yes_the_tiles_and_their_attribution_and_without_a_network_the_note(
        monkeypatch):
    from openexpress.gui import activity_map, course_map

    if not activity_map.can_ask():
        pytest.skip("this libshumate cannot ask the tiles")
    activity_map.set_tiles(True)  # in the test's own settings
    monkeypatch.setattr(activity_map, "_network_monitor", lambda: types.SimpleNamespace(
        get_network_available=lambda: False))
    shown = course_map.CourseMap(_file(gpx_track()))
    assert shown.tiles is not None and shown.attribution.get_visible()
    assert shown.offline.get_visible()  # the drawing alone; the question does not wait


def test_the_question_shows_the_file_on_its_map(drive, tmp_path, asked):
    from openexpress.gui import course_map

    if not course_map.available():
        pytest.skip("libshumate is not installed")
    win, _section = courses_window(drive)
    win.add_course(_gpx(tmp_path, gpx_route()))
    # On top, over the course's name.
    assert isinstance(asked[0].extra.get_first_child(), course_map.CourseMap)


def test_the_page_without_a_course_shows_the_icon_of_the_side_panel(drive):
    from gi.repository import Gtk

    from openexpress.gui import courses_page

    win, section = courses_window(drive)
    win.load_courses()
    (icon,) = [w for w in descendants(section.page) if isinstance(w, Gtk.Image)]
    # The program's own icon (test_window_icons: drawn, made into shapes, in the Flatpak).
    assert icon.get_icon_name() == courses_page.PAGE_ICON == (
        "io.github.tomaszbojanowski.OpenExpress-courses-symbolic")
