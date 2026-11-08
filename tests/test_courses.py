"""Courses on the device and GPX files copied to it (openexpress/courses.py): which devices are
offered them, what is read of the device (its courses with their origin, the files waiting, the
saved locations), the plan and the copy of a file, the removal of a course or of a waiting file,
and the record of the copies. Made-up files on a folder taken as the device; no device."""

import json
import time

import pytest
from fakes import (
    COURSE_UNIT,
    course_fit,
    gpx_file,
    gpx_route,
    gpx_track,
    gpx_waypoint,
    locations_fit,
)
from fakes import course_device as device

from openexpress import applog, courses, paths, service
from openexpress.course_gpx import CourseFileError
from openexpress.courses import CourseError
from openexpress.device.storage import DeviceError, LocalStorage

UNIT = COURSE_UNIT


@pytest.fixture
def drive(tmp_path):
    root = tmp_path / "device"
    for folder in ("Garmin/NewFiles", "Garmin/Courses", "Garmin/Locations"):
        (root / folder).mkdir(parents=True)
    return root


def put(root, path: str, data: bytes | str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def a_gpx(tmp_path, body=None, name="morning-loop.gpx"):
    path = tmp_path / name
    path.write_text(gpx_file(gpx_track() if body is None else body), encoding="utf-8")
    return path


def now() -> int:
    return int(time.time())


# --- which devices -------------------------------------------------------------------------------


def test_courses_are_offered_where_gpx_files_go_to_garmin_newfiles():
    assert courses.offered(device())
    assert courses.offered(device(gpx_folder="GARMIN/NEWFILES/"))  # its case, its slashes
    assert not courses.offered(device(gpx_folder=None))


def test_a_device_whose_gpx_files_go_elsewhere_is_not_offered_and_the_log_says_where(log_file):
    applog.setup("cli")
    assert not courses.offered(device(gpx_folder="Garmin/GPX"))
    assert "GPX files go to Garmin/GPX on this device, not Garmin/NewFiles" in log_file.read_text()


def test_a_device_not_offered_has_nothing_read(drive, monkeypatch):
    st = LocalStorage(drive)
    monkeypatch.setattr(st, "listdir", lambda path: pytest.fail("the device was read"))
    seen = courses.overview(device(gpx_folder=None), st)
    assert (seen.folder, seen.courses, seen.waiting, seen.locations) == (None, [], [], None)


# --- the courses on the device -------------------------------------------------------------------


def test_each_course_with_what_its_file_says(drive, clock):
    put(drive, "Garmin/Courses/Morning_Loop.fit", course_fit(made=now() - 3600))
    put(drive, "Garmin/Courses/Straight.fit",
        course_fit("Straight Lines", made=now() - 7200, distance_cm=340_862, ascent=None))
    put(drive, "Garmin/Courses/notes.txt", "not a course")
    put(drive, "Garmin/Courses/Inside/Deeper.fit", course_fit("Deeper", made=now()))
    seen = courses.overview(device(), LocalStorage(drive))
    assert seen.folder == "Garmin/NewFiles" and seen.unreadable == ()
    assert [(c.filename, c.path, c.name, c.distance_m, c.ascent_m, c.made)
            for c in seen.courses] == [
        ("Morning_Loop.fit", "Garmin/Courses/Morning_Loop.fit", "Morning Loop", 12345.0, 321,
         now() - 3600),
        ("Straight.fit", "Garmin/Courses/Straight.fit", "Straight Lines", 3408.62, None,
         now() - 7200)]


def test_a_course_of_garmin_connect_and_one_of_nobody_known(drive, clock):
    put(drive, "Garmin/Courses/A.fit", course_fit("Along the River", made=now(),
                                                  product=courses.CONNECT_PRODUCT))
    put(drive, "Garmin/Courses/B.fit", course_fit("By Hand", made=now()))
    put(drive, "Garmin/Courses/C.fit", course_fit("Another Maker", made=now(), manufacturer=255,
                                                  product=courses.CONNECT_PRODUCT))
    seen = courses.overview(device(), LocalStorage(drive))
    assert [(c.name, c.origin) for c in seen.courses] == [
        ("Along the River", "Garmin Connect"), ("By Hand", None), ("Another Maker", None)]


def test_a_file_that_is_no_course_is_listed_with_nothing_said_of_it(drive):
    put(drive, "Garmin/Courses/Broken.fit", b"not a FIT file at all")
    (course,) = courses.overview(device(), LocalStorage(drive)).courses
    assert (course.name, course.distance_m, course.made, course.origin, course.title) == (
        None, None, None, None, "Broken.fit")


def test_only_the_start_of_a_course_is_read_when_its_lap_comes_first(drive, monkeypatch, clock):
    put(drive, "Garmin/Courses/Long.fit", course_fit(made=now(), points=50))
    put(drive, "Garmin/Courses/Late.fit", course_fit("Lap at the End", made=now(), points=50,
                                                     lap_after_points=True))
    monkeypatch.setattr(courses, "START", 200)
    st = LocalStorage(drive)
    whole = []
    real_read = st.read
    monkeypatch.setattr(st, "read", lambda path: whole.append(path) or real_read(path))
    seen = courses.overview(device(), st)
    assert whole == ["Garmin/Courses/Late.fit"]  # its lap was past the start
    assert [(c.name, c.distance_m) for c in seen.courses] == [
        ("Lap at the End", 12345.0), ("Morning Loop", 12345.0)]


def test_a_folder_that_cannot_be_read_is_said_and_the_rest_still_shows(drive):
    class NoCourses(LocalStorage):
        def _list(self, real_path):
            if real_path.endswith("Courses"):
                raise DeviceError("made-up failure")
            return super()._list(real_path)

    put(drive, "Garmin/NewFiles/Loop.gpx", gpx_file(gpx_track()))
    seen = courses.overview(device(), NoCourses(drive))
    assert seen.unreadable == ("courses",)
    assert [w.filename for w in seen.waiting] == ["Loop.gpx"]


# --- where a course came from: openExpress's record ----------------------------------------------


def copy_a_file(drive, tmp_path, body=None, name=None):
    st = LocalStorage(drive)
    plan = courses.plan_add(device(), st, a_gpx(tmp_path, body), name)
    courses.add(plan, device(), st)
    return plan


def test_a_course_the_device_made_of_a_copy_is_openexpress_s(drive, tmp_path, clock):
    copy_a_file(drive, tmp_path)
    put(drive, "Garmin/Courses/Morning_Loop.fit", course_fit(made=now() + 120))
    (course,) = courses.overview(device(), LocalStorage(drive)).courses
    assert course.origin == "openExpress"


@pytest.mark.parametrize("made, name, product", [
    (-3600, "Morning Loop", courses.DEVICE_PRODUCT),  # made an hour before the copy
    (120, "Evening Loop", courses.DEVICE_PRODUCT),  # another name
    (120, "Morning Loop", 12345),  # not made by the device of a file
])
def test_a_course_not_made_of_a_copy_is_not_openexpress_s(drive, tmp_path, clock, made, name,
                                                          product):
    copy_a_file(drive, tmp_path)
    put(drive, "Garmin/Courses/X.fit", course_fit(name, made=now() + made, product=product))
    (course,) = courses.overview(device(), LocalStorage(drive)).courses
    assert course.origin is None


def test_a_name_the_device_cut_still_tells_the_copy(drive, tmp_path, clock):
    copy_a_file(drive, tmp_path, gpx_track("A Long Way Around the Lake"))
    put(drive, "Garmin/Courses/A_Long_Way.fit", course_fit("A Long Way Arou", made=now() + 60))
    (course,) = courses.overview(device(), LocalStorage(drive)).courses
    assert course.origin == "openExpress"


def test_the_other_install_s_copies_count_and_are_never_written(drive, tmp_path, clock):
    other = paths.other_install_dir() / "devices" / str(UNIT) / "courses.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"copies": [
        {"file": "Hill.GPX", "courses": ["Up the Hill"], "size": 1, "md5": "0" * 32,
         "copied": now() - 600}]}), encoding="utf-8")
    before = other.read_bytes()
    put(drive, "Garmin/Courses/Hill.fit", course_fit("Up the Hill", made=now() - 60))
    (course,) = courses.overview(device(), LocalStorage(drive)).courses
    assert course.origin == "openExpress"
    copy_a_file(drive, tmp_path)
    courses.remove(courses.plan_remove(device(), LocalStorage(drive), course), device(),
                   LocalStorage(drive))
    assert other.read_bytes() == before


# --- the files waiting and the saved locations --------------------------------------------------


def test_the_gpx_files_waiting_with_what_they_hold(drive):
    put(drive, "Garmin/NewFiles/Morning_Loop.GPX", gpx_file(gpx_track()))
    put(drive, "Garmin/NewFiles/broken.gpx", "<not a gpx file")
    put(drive, "Garmin/NewFiles/Course.fit", b"a course of Garmin Connect waiting")
    seen = courses.overview(device(), LocalStorage(drive))
    assert [(w.filename, w.size > 0, w.course, w.title) for w in seen.waiting] == [
        ("broken.gpx", True, None, "broken.gpx"),
        ("Morning_Loop.GPX", True, "Morning Loop", "Morning Loop")]
    assert seen.waiting[1].gpx.lines[0].points == 3


@pytest.mark.parametrize("files, expected, unreadable", [
    ({}, 0, ()),
    ({"Locations.fit": locations_fit(0)}, 0, ()),
    ({"Locations.fit": locations_fit(3), "notes.txt": b"x"}, 3, ()),
    ({"Locations.fit": locations_fit(3, cut=60)}, None, ("locations",)),  # not whole
])
def test_the_saved_locations_are_counted(drive, files, expected, unreadable):
    for name, data in files.items():
        put(drive, f"Garmin/Locations/{name}", data)
    seen = courses.overview(device(), LocalStorage(drive))
    assert (seen.locations, seen.unreadable) == (expected, unreadable)


def test_a_device_that_declares_no_locations_has_none_known(drive):
    assert courses.overview(device(locations=None), LocalStorage(drive)).locations is None


# --- the plan of a copy --------------------------------------------------------------------------


def test_the_plan_names_the_file_after_the_course_as_the_device_declares_it(drive, tmp_path):
    local = a_gpx(tmp_path)
    plan = courses.plan_add(device(), LocalStorage(drive), local)
    assert (plan.course, plan.name, plan.path, plan.same_waiting) == (
        "Morning Loop", "Morning_Loop.GPX", "Garmin/NewFiles/Morning_Loop.GPX", None)
    assert plan.data == local.read_bytes() and plan.size == local.stat().st_size
    assert courses.plan_add(device(extension="gpx"), LocalStorage(drive), local).name == (
        "Morning_Loop.gpx")
    assert not (drive / "Garmin/NewFiles/Morning_Loop.GPX").exists()  # nothing written


def test_a_name_in_the_folder_is_never_taken(drive, tmp_path):
    put(drive, "Garmin/NewFiles/morning_loop.gpx", "another file")
    plan = courses.plan_add(device(), LocalStorage(drive), a_gpx(tmp_path))
    assert plan.name == "Morning_Loop_2.GPX" and plan.same_waiting is None


def test_the_same_file_waiting_already_is_said(drive, tmp_path):
    local = a_gpx(tmp_path)
    put(drive, "Garmin/NewFiles/Copied_Before.GPX", local.read_bytes())
    plan = courses.plan_add(device(), LocalStorage(drive), local)
    assert plan.same_waiting == "Copied_Before.GPX" and plan.name == "Morning_Loop.GPX"


def test_a_name_entered_is_written_into_the_copy_alone(drive, tmp_path):
    local = a_gpx(tmp_path)
    plan = courses.plan_add(device(), LocalStorage(drive), local, "Pętla nad jeziorem")
    assert (plan.course, plan.name) == ("Pętla nad jeziorem", "Petla_nad_jeziorem.GPX")
    original = local.read_bytes()
    assert plan.data == original.replace(b"Morning Loop", "Pętla nad jeziorem".encode())
    assert plan.courses == ["Pętla nad jeziorem"]


def test_a_track_without_a_name_gets_the_file_s_in_the_copy(drive, tmp_path):
    plan = courses.plan_add(device(), LocalStorage(drive), a_gpx(tmp_path, gpx_track(None)))
    assert plan.course == "morning-loop" and b"<name>morning-loop</name>" in plan.data


def test_a_file_of_several_courses_keeps_its_names(drive, tmp_path):
    body = gpx_track("First") + gpx_track("Second")
    plan = courses.plan_add(device(), LocalStorage(drive), a_gpx(tmp_path, body))
    assert (plan.course, plan.name, plan.courses) == (None, "morning-loop.GPX",
                                                     ["First", "Second"])
    with pytest.raises(CourseError, match="makes 2 courses; only a file with one track"):
        courses.plan_add(device(), LocalStorage(drive), a_gpx(tmp_path, body), "One Name")


def test_a_file_of_waypoints_takes_no_course_name(drive, tmp_path):
    local = a_gpx(tmp_path, gpx_waypoint() + gpx_waypoint("Shelter", 52.1, 13.1), "points.gpx")
    plan = courses.plan_add(device(), LocalStorage(drive), local)
    assert (plan.course, plan.name, plan.courses) == (None, "points.GPX", [])
    with pytest.raises(CourseError, match="has only waypoints"):
        courses.plan_add(device(), LocalStorage(drive), local, "A Name")


def test_what_is_refused_before_the_question(drive, tmp_path, monkeypatch):
    local = a_gpx(tmp_path)
    with pytest.raises(CourseError, match="does not make courses of GPX files"):
        courses.plan_add(device(gpx_folder=None), LocalStorage(drive), local)
    with pytest.raises(CourseFileError, match="is not a GPX file"):
        courses.plan_add(device(), LocalStorage(drive), a_gpx(tmp_path, "<trk>", "bad.gpx"))
    st = LocalStorage(drive)
    monkeypatch.setattr(st, "free_space", lambda: 1000)
    with pytest.raises(CourseError, match="Not enough space"):
        courses.plan_add(device(), st, local)
    (drive / "Garmin/NewFiles").rmdir()
    with pytest.raises(CourseError, match="no folder Garmin/NewFiles"):
        courses.plan_add(device(), LocalStorage(drive), local)


# --- the copy ------------------------------------------------------------------------------------


def test_the_copy_is_written_and_recorded(drive, tmp_path, clock):
    plan = copy_a_file(drive, tmp_path, name="Lake")
    assert (drive / "Garmin/NewFiles/Lake.GPX").read_bytes() == plan.data
    assert courses.recorded(UNIT) == [{"file": "Lake.GPX", "courses": ["Lake"],
                                       "size": plan.size, "md5": plan.md5, "copied": now()}]


def test_a_file_that_appeared_since_the_plan_is_not_overwritten(drive, tmp_path):
    st = LocalStorage(drive)
    plan = courses.plan_add(device(), st, a_gpx(tmp_path))
    put(drive, plan.path, "someone else's")
    with pytest.raises(CourseError, match="already on the device; nothing is overwritten"):
        courses.add(plan, device(), st)
    assert (drive / plan.path).read_text() == "someone else's"
    assert courses.recorded(UNIT) == []


def test_no_copy_while_a_sync_holds_the_lock(drive, tmp_path):
    st = LocalStorage(drive)
    plan = courses.plan_add(device(), st, a_gpx(tmp_path))
    with service.sync_lock(), pytest.raises(service.SyncBusy):
        courses.add(plan, device(), st)
    assert not (drive / plan.path).exists() and courses.recorded(UNIT) == []


def test_a_failed_copy_leaves_nothing(drive, tmp_path):
    class Breaks(LocalStorage):
        """Writes a part of the file, then fails, as a copy over MTP can; and cleans up after
        it as the storage over MTP does."""

        def _write(self, real_path, data):
            (self.root / real_path).write_bytes(data[:10])
            raise DeviceError("made-up failure")

        def _discard_partial(self, real_path):
            (self.root / real_path).unlink(missing_ok=True)

    st = Breaks(drive)
    plan = courses.plan_add(device(), st, a_gpx(tmp_path))
    with pytest.raises(CourseError, match="Writing Garmin/NewFiles/Morning_Loop.GPX failed"):
        courses.add(plan, device(), st)
    assert not (drive / plan.path).exists() and courses.recorded(UNIT) == []


def test_the_names_stay_out_of_the_log(drive, tmp_path, log_file):
    applog.setup("cli")
    copy_a_file(drive, tmp_path, name="Secret Hill")
    courses.overview(device(), LocalStorage(drive))
    log = log_file.read_text()
    assert "a GPX file copied" in log and "Secret" not in log and "morning-loop" not in log


# --- removing ------------------------------------------------------------------------------------


def test_a_course_is_removed_and_its_name_taken_off_the_record(drive, tmp_path, clock):
    copy_a_file(drive, tmp_path)
    (drive / "Garmin/NewFiles/Morning_Loop.GPX").unlink()  # the device made the course of it
    put(drive, "Garmin/Courses/Morning_Loop.fit", course_fit(made=now() + 60))
    st = LocalStorage(drive)
    (course,) = courses.overview(device(), st).courses
    plan = courses.plan_remove(device(), st, course)
    assert (plan.title, plan.waiting, plan.origin) == ("Morning Loop", False, "openExpress")
    courses.remove(plan, device(), st)
    assert not (drive / "Garmin/Courses/Morning_Loop.fit").exists()
    assert courses.recorded(UNIT) == []
    # A course of that name the device makes later of a file copied by hand is nobody's known.
    put(drive, "Garmin/Courses/Morning_Loop.fit", course_fit(made=now() + 3600))
    assert courses.overview(device(), st).courses[0].origin is None


def test_a_waiting_file_is_removed_with_its_record(drive, tmp_path):
    copy_a_file(drive, tmp_path)
    st = LocalStorage(drive)
    (waiting,) = courses.overview(device(), st).waiting
    plan = courses.plan_remove(device(), st, waiting)
    assert (plan.title, plan.waiting) == ("Morning Loop", True)
    courses.remove(plan, device(), st)
    assert not (drive / "Garmin/NewFiles/Morning_Loop.GPX").exists()
    assert courses.recorded(UNIT) == []


def test_a_file_changed_or_gone_since_the_list_is_not_removed(drive, clock):
    put(drive, "Garmin/Courses/A.fit", course_fit("A", made=now()))
    st = LocalStorage(drive)
    (course,) = courses.overview(device(), st).courses
    put(drive, "Garmin/Courses/A.fit", course_fit("A", made=now(), points=9))
    with pytest.raises(CourseError, match="has changed since the list was shown"):
        courses.plan_remove(device(), st, course)
    (course,) = courses.overview(device(), st).courses
    plan = courses.plan_remove(device(), st, course)
    put(drive, "Garmin/Courses/A.fit", course_fit("A", made=now(), points=20))
    with pytest.raises(CourseError, match="has changed since the list was shown"):
        courses.remove(plan, device(), st)
    assert (drive / "Garmin/Courses/A.fit").exists()
    (drive / "Garmin/Courses/A.fit").unlink()
    with pytest.raises(CourseError, match="no longer on the device"):
        courses.plan_remove(device(), st, course)


@pytest.mark.parametrize("shown", [
    courses.Course("../Locations/Locations.fit", "", 10, None, None, None, None, None),
    courses.Course("Locations.fit", "Garmin/Locations/Locations.fit", 10, None, None, None,
                   None, None),  # looked for in the courses folder only
    courses.Course("notes.txt", "Garmin/Courses/notes.txt", 3, None, None, None, None, None),
    courses.Course(".fit", "Garmin/Courses/.fit", 3, None, None, None, None, None),
    courses.Waiting("Course.fit", "Garmin/NewFiles/Course.fit", 3, None),
])
def test_only_a_course_s_file_or_a_waiting_gpx_file_is_ever_removed(drive, shown):
    put(drive, "Garmin/Locations/Locations.fit", locations_fit(2))
    put(drive, "Garmin/Courses/notes.txt", "abc")
    put(drive, "Garmin/Courses/.fit", "abc")
    put(drive, "Garmin/NewFiles/Course.fit", "abc")
    before = sorted(p.relative_to(drive) for p in drive.rglob("*"))
    with pytest.raises(CourseError, match="is not the file of a course|no longer on the device"):
        courses.plan_remove(device(), LocalStorage(drive), shown)
    assert sorted(p.relative_to(drive) for p in drive.rglob("*")) == before


def test_no_removal_while_a_sync_holds_the_lock(drive, clock):
    put(drive, "Garmin/Courses/A.fit", course_fit("A", made=now()))
    st = LocalStorage(drive)
    (course,) = courses.overview(device(), st).courses
    plan = courses.plan_remove(device(), st, course)
    with service.sync_lock(), pytest.raises(service.SyncBusy):
        courses.remove(plan, device(), st)
    assert (drive / "Garmin/Courses/A.fit").exists()


# --- the command line ----------------------------------------------------------------------------

COURSE_TYPES = """<DataType><Name>GPSData</Name>
   <File><Location><Path>Garmin/NewFiles</Path><FileExtension>GPX</FileExtension></Location>
    <TransferDirection>InputToUnit</TransferDirection></File></DataType>
  <DataType><Name>FIT_TYPE_6</Name>
   <File><Location><Path>Garmin/Courses</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
  <DataType><Name>FIT_TYPE_8</Name>
   <File><Location><Path>Garmin/Locations</Path><FileExtension>FIT</FileExtension></Location>
    <TransferDirection>OutputFromUnit</TransferDirection></File></DataType>
  <UpdateFile>"""


@pytest.fixture
def courses_cli(device_root):
    """The made-up device of the tests' folder, declaring GPX files, courses and locations."""
    xml = device_root / "GARMIN" / "GarminDevice.xml"
    xml.write_text(xml.read_text().replace("<UpdateFile>", COURSE_TYPES, 1))
    for folder in ("Courses", "Locations"):
        (device_root / "GARMIN" / folder).mkdir()
    return device_root


def run(root, *argv):
    from openexpress.cli import main

    return main(["--path", str(root), "courses", *argv])


def test_the_list_of_courses_files_waiting_and_locations(courses_cli, tmp_path, clock, capsys):
    g = courses_cli / "GARMIN"
    (g / "Courses" / "Along.fit").write_bytes(course_fit(
        "Along the River", made=now(), product=courses.CONNECT_PRODUCT))
    (g / "Courses" / "Broken.fit").write_bytes(b"not a course")
    (g / "NewFiles" / "Two.gpx").write_text(gpx_file(gpx_track("First") + gpx_track("Second")
                                                     + gpx_waypoint()))
    (g / "Locations" / "Locations.fit").write_bytes(locations_fit(2))
    assert run(courses_cli) == 0
    assert capsys.readouterr().out.splitlines() == [
        "2 courses on the device:",
        "  Along the River · Garmin Connect · 12.35 km · ↑ 321 m · 2026-10-21",
        "  Broken.fit",
        "1 GPX file waiting; unplug the device so it can import it:",
        "  Two.gpx · 2 tracks, 2.74 km; 1 waypoint",
        "Saved locations on the device: 2"]


def test_no_course_and_no_locations_file(courses_cli, capsys):
    assert run(courses_cli) == 0
    assert capsys.readouterr().out.splitlines() == [
        "No courses on the device.", "Saved locations on the device: 0"]


def test_a_device_that_makes_no_courses_of_gpx_files_is_said(device_root, capsys):
    assert run(device_root) == 1
    assert "This device does not make courses of GPX files." in capsys.readouterr().err


def test_add_is_a_dry_run_without_apply(courses_cli, tmp_path, capsys):
    local = a_gpx(tmp_path, gpx_track() + gpx_route())
    assert run(courses_cli, "add", str(local)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Courses: Morning Loop, To the Lake"
    assert out[1].startswith("  morning-loop.gpx (") and out[1].endswith(
        "will be written to GARMIN/NewFiles/morning-loop.GPX")
    assert out[2:5] == [
        "  Contents: track, 1.37 km, ↑ 10 m, 3 track points; route, 3 route points",
        "  The device makes 2 courses when you unplug the cable.",
        ("  Garmin devices join the points of a route with straight lines, not roads. For a "
         "course along roads, export a track instead of a route.")]
    assert out[5].startswith("Free space on the device: ") and out[-1] == "Dry run. Add --apply."
    assert sorted(p.name for p in (courses_cli / "GARMIN" / "NewFiles").iterdir()) == []


def test_add_with_apply_copies_under_the_name_given(courses_cli, tmp_path, clock, capsys):
    local = a_gpx(tmp_path)
    assert run(courses_cli, "add", str(local), "--name", "Lake Loop", "--apply") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Course: Lake Loop"
    assert out[-2].startswith("Wrote GARMIN/NewFiles/Lake_Loop.GPX (")
    assert out[-1] == "Unplug the device so it can import the file."
    written = (courses_cli / "GARMIN" / "NewFiles" / "Lake_Loop.GPX").read_bytes()
    assert written == local.read_bytes().replace(b"Morning Loop", b"Lake Loop")
    assert [c["courses"] for c in courses.recorded(1234567890)] == [["Lake Loop"]]


def test_the_same_file_waiting_is_said_in_the_dry_run(courses_cli, tmp_path, capsys):
    local = a_gpx(tmp_path)
    (courses_cli / "GARMIN" / "NewFiles" / "Before.GPX").write_bytes(local.read_bytes())
    assert run(courses_cli, "add", str(local)) == 0
    assert ("  The same file is already waiting on the device as Before.GPX."
            in capsys.readouterr().out.splitlines())


@pytest.mark.parametrize("body, name, error", [
    (gpx_track("First") + gpx_track("Second"), "One",
     "makes 2 courses; only a file with one track or route takes a course name."),
    (gpx_waypoint(), "One", "has only waypoints"),
    ("<gpx", None, "is not a GPX file."),
])
def test_add_refuses_a_file_before_the_device_is_opened(courses_cli, tmp_path, monkeypatch,
                                                        capsys, body, name, error):
    from openexpress import cli

    monkeypatch.setattr(cli, "open_device", lambda args: pytest.fail("the device was opened"))
    local = tmp_path / "file.gpx"
    local.write_text(body if body.startswith("<gpx") else gpx_file(body))
    argv = ["add", str(local), *(["--name", name] if name else [])]
    assert run(courses_cli, *argv) == 1
    assert error in capsys.readouterr().err


def test_remove_a_course_by_a_part_of_its_name(courses_cli, clock, capsys):
    (courses_cli / "GARMIN" / "Courses" / "Along.fit").write_bytes(course_fit(
        "Along the River", made=now(), product=courses.CONNECT_PRODUCT))
    assert run(courses_cli, "remove", "river") == 0
    assert capsys.readouterr().out.splitlines() == [
        "Removing course: Along the River",
        f"  - GARMIN/Courses/Along.fit  ({human(courses_cli / 'GARMIN/Courses/Along.fit')})",
        "  The device keeps no other copy of it; Garmin Connect can send it again.",
        "Dry run. Add --apply."]
    assert run(courses_cli, "remove", "RIVER", "--apply") == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("Removed GARMIN/Courses/Along.fit")
    assert not (courses_cli / "GARMIN" / "Courses" / "Along.fit").exists()


def human(path) -> str:
    """The size of a file as the command line writes it (with a plain space)."""
    from openexpress.i18n import human_size

    return human_size(path.stat().st_size).replace("\xa0", " ")


def test_remove_a_waiting_file_and_one_of_unknown_origin(courses_cli, clock, capsys):
    (courses_cli / "GARMIN" / "NewFiles" / "Loop.gpx").write_text(gpx_file(gpx_track()))
    (courses_cli / "GARMIN" / "Courses" / "Hand.fit").write_bytes(course_fit("By Hand",
                                                                           made=now()))
    assert run(courses_cli, "remove", "morning loop") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Removing a file waiting for the device: Morning Loop"
    assert out[2] == "  The device has not made a course of it yet."
    assert run(courses_cli, "remove", "Hand.fit") == 0
    assert capsys.readouterr().out.splitlines()[2] == (
        "  openExpress did not copy this course and it did not come from Garmin Connect. "
        "The device keeps no other copy of it.")


def test_remove_names_several_matches_and_none_and_removes_nothing(courses_cli, clock, capsys):
    c = courses_cli / "GARMIN" / "Courses"
    c.joinpath("A.fit").write_bytes(course_fit("Loop North", made=now()))
    c.joinpath("B.fit").write_bytes(course_fit("Loop South", made=now()))
    assert run(courses_cli, "remove", "loop", "--apply") == 1
    assert ("Courses and files that match “loop”: Loop North (A.fit), Loop South (B.fit)"
            in capsys.readouterr().err)
    assert run(courses_cli, "remove", "east", "--apply") == 1
    assert "No course or waiting file is named “east”." in capsys.readouterr().err
    assert sorted(p.name for p in c.iterdir()) == ["A.fit", "B.fit"]


def test_courses_remove_matches_a_name_without_letter_case_or_diacritics():
    from openexpress import cli

    wanted = "cwiczenia lyzwy"
    assert cli._name_key("Ćwiczenia ŁYŻWY") == cli._name_key(wanted) == wanted
