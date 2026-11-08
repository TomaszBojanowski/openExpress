"""The Activities page of the window: a row for each activity of the open device and of its
backups - its sport, then the date, time, distance, ascent and source in one caption - read 30
at a time with the steps of the shared bar, "Show N More" for the next ones, and what a file
the device was still writing, a damaged file, a file that is no FIT file, no activity and a
device that keeps none say. Only how the page looks: export is another step. Made-up files
only; a folder stands for the device."""

import logging
import os
import types
from datetime import UTC, datetime

import pytest
from conftest import DEVICE_XML
from fakes import descendants, fit_activity, page_window, window, window_methods

pytestmark = pytest.mark.needs_display

FIT_EPOCH = int(datetime(1989, 12, 31, tzinfo=UTC).timestamp())
SEP = " · "  # between the parts of a caption
RUNNING, CYCLING, TRAINING = 1, 2, 10
WARSAW = (624_000_000, 250_000_000)  # a made-up position, in semicircles
PAGE_METHODS = ["load_activities", "show_activities"]


def unix(*when) -> int:
    return int(datetime(*when).timestamp())


def summary(state="WHOLE", **fields):
    from openexpress import fit

    return fit.ActivitySummary(getattr(fit.FileState, state), **fields)


def read(name, found, source="device", backup=None):
    from openexpress import activities

    where = activities.Activity(activities.Source(source), f"GARMIN/Activity/{name}", name,
                                100, 1.0, backup)
    return activities.Read(where, found)


def made_up_list(*reads, more=0):
    """What DeviceActivities holds after a reading: the activities read, `more` left."""
    from openexpress import activities

    listing = [r.activity for r in reads] + [object()] * more
    return types.SimpleNamespace(
        read=list(reads), left=more, listing=listing,
        only_in_backups=sum(1 for r in reads if r.activity.source is activities.Source.BACKUP))


THE_WEEK = [
    read("run.fit", summary(start=unix(2026, 10, 30, 7, 0), sport=RUNNING, timer_time=3731.4,
                            distance=12_400.0, ascent=135, has_positions=True)),
    read("strength.fit", summary(start=unix(2026, 10, 29, 18, 0), sport=TRAINING,
                                 timer_time=2702.0)),
    read("writing.fit", summary("STILL_BEING_WRITTEN", created=unix(2026, 10, 28, 6, 0))),
    read("damaged.fit", summary("DAMAGED", start=unix(2026, 10, 27, 7, 0), sport=RUNNING,
                                timer_time=600.0, distance=2_000.0, ascent=10,
                                has_positions=True)),
    read("notes.fit", None),
    read("ride.fit", summary(start=unix(2025, 9, 14, 9, 0), sport=CYCLING, timer_time=13_280.0,
                             distance=88_100.0, ascent=960, has_positions=True),
         source="backup", backup=datetime(2025, 10, 2, 10, 0)),
]


def the_page():
    win, section = page_window("activities")
    window_methods(win, PAGE_METHODS)
    win.session = types.SimpleNamespace()
    return win, section


def rows_of(section):
    from gi.repository import Adw

    return [w for g in section.groups for w in descendants(g) if isinstance(w, Adw.ActionRow)]


def texts(row) -> tuple[str, str]:
    return row.get_title(), row.get_subtitle()


def export_button(row):
    from gi.repository import Gtk

    found = [w for w in descendants(row) if isinstance(w, Gtk.Button)
             and w.get_icon_name() == "document-save-as-symbolic"]
    return found[0] if found else None


def test_the_rows_and_their_words():
    win, section = the_page()
    win.show_activities(made_up_list(*THE_WEEK))
    rows = rows_of(section)
    assert texts(rows[0]) == ("Running", SEP.join([
        "2026-10-30 07:00", "1 h 02 min", "12.4 km", "↑ 135 m", "Device"]))
    assert texts(rows[1]) == ("Training", SEP.join(["2026-10-29 18:00", "45 min", "Device"]))
    assert texts(rows[5]) == ("Cycling", SEP.join([
        "2025-09-14 09:00", "3 h 41 min", "88.1 km", "↑ 960 m",
        "Backup of 2025-10-02"]))


def test_what_a_file_still_being_written_a_damaged_and_an_unreadable_one_say():
    win, section = the_page()
    win.show_activities(made_up_list(*THE_WEEK))
    rows = rows_of(section)
    assert texts(rows[2]) == ("Activity", SEP.join([
        "2026-10-28 06:00", "Still being written: unplug the device and plug it in again"]))
    assert texts(rows[3])[1].endswith(f"{SEP}Device{SEP}Damaged file")
    assert texts(rows[4]) == ("Unreadable File", "Device")


def test_the_button_to_export_is_there_only_for_what_can_be_exported():
    win, section = the_page()
    win.show_activities(made_up_list(*THE_WEEK))
    run, strength, writing, damaged, unreadable, ride = (export_button(r) for r in rows_of(section))
    assert run.get_sensitive() and run.get_tooltip_text() == "Export as GPX…"
    assert ride.get_sensitive() and damaged.get_sensitive()
    assert not strength.get_sensitive()
    assert strength.get_tooltip_text() == "No GPS positions in this activity"
    assert writing is None and unreadable is None


def test_the_page_says_how_many_and_how_many_only_in_backups():
    win, section = the_page()
    win.show_activities(made_up_list(*THE_WEEK))
    assert section.groups[0].get_description() == f"6 activities{SEP}1 only in backups"


def on_device(tmp_path, count, xml=DEVICE_XML):
    """A folder as a device with `count` made-up runs: its session."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse

    root = tmp_path / "device"
    (root / "GARMIN" / "Activity").mkdir(parents=True)
    (root / "GARMIN" / "GarminDevice.xml").write_text(xml)
    for n in range(count):
        path = root / "GARMIN" / "Activity" / f"2026-09-{n:02}.fit"
        start = unix(2026, 9, 1) + n * 3_600 - FIT_EPOCH
        path.write_bytes(fit_activity(start, records=2, position=WARSAW))
        os.utime(path, (1_000 + n, 1_000 + n))
    storage = LocalStorage(root)
    return types.SimpleNamespace(device=parse(storage.read("GARMIN/GarminDevice.xml")),
                                 storage=storage)


def test_thirty_then_show_more(tmp_path):
    from gi.repository import Gtk

    win, section = the_page()
    win.session = on_device(tmp_path, 32)
    win.progress = lambda _step: None
    win.load_activities()
    assert len(rows_of(section)) == 30
    more = [w for w in descendants(section.page) if isinstance(w, Gtk.Button)
            and w.get_label() == "Show 2 More"]
    assert len(more) == 1
    more[0].emit("clicked")
    assert len(rows_of(section)) == 32
    assert not [w for w in descendants(section.page) if isinstance(w, Gtk.Button)
                and (w.get_label() or "").startswith("Show")]
    assert win.run_task.titles == ["Reading Activities…", "Reading Activities…"]


def test_the_steps_of_the_reading_go_to_the_shared_bar(tmp_path):
    win, section = the_page()
    win.session = on_device(tmp_path, 2)
    said = []
    win.progress = said.append
    win.load_activities()
    assert said == ["Reading activities · 1 of 2", "Reading activities · 2 of 2"]
    assert win.run_task.calls[0].in_page  # the page shows it loads; the bar its steps


def test_no_activity_yet(tmp_path):
    win, section = the_page()
    win.session = on_device(tmp_path, 0)
    win.progress = lambda _step: None
    win.load_activities()
    assert rows_of(section) == []
    words = [w.get_label() for w in descendants(section.page) if hasattr(w, "get_label")]
    assert "No Activities" in words and "There are none on the device or in its backups" in words


def test_a_device_that_keeps_no_activities_reads_nothing(tmp_path):
    win, section = the_page()
    win.session = on_device(tmp_path, 1, DEVICE_XML.replace("<Name>FIT_TYPE_4</Name>",
                                                            "<Name>FIT_TYPE_999</Name>"))
    win.load_activities()
    assert win.run_task.calls == []
    assert section.stack.get_visible_child_name() == "info"
    assert section.info.get_description() == "This device does not keep activities"


def test_the_window_starts_with_no_activities_kept():
    win = window()
    assert (win.activities, win.activities_list, win.activities_more) == (None, None, None)


def test_the_page_logs_under_its_own_name(caplog):
    win, _section = the_page()
    with caplog.at_level(logging.INFO, logger="openexpress.gui.activities_page"):
        win.show_activities(made_up_list(*THE_WEEK, more=4))
    assert [r.getMessage() for r in caplog.records
            if r.name == "openexpress.gui.activities_page"] == [
        "activities page: 6 shown, 4 more to read"]


# --- exporting from the page ---------------------------------------------------------------


def a_dialog_that_saves_into(folder):
    """A stand-in for Gtk.FileDialog: "saves" at once, the file under the name it suggests in
    `folder`; what it was asked is kept on the class."""
    from gi.repository import Gio

    class FileDialog:
        made = []

        def __init__(self, **properties):
            self.properties = properties
            FileDialog.made.append(self)

        def set_initial_folder(self, folder):
            self.properties["initial_folder"] = folder

        def save(self, parent, cancellable, callback):
            callback(self, None)

        def save_finish(self, result):
            return Gio.File.new_for_path(str(folder / self.properties["initial_name"]))

    return FileDialog


def gtk_with(dialog):
    """GTK as the window has it, with `dialog` as its FileDialog."""
    from gi.repository import Gtk

    class GtkWithDialog:
        FileDialog = dialog

        def __getattr__(self, name):
            return getattr(Gtk, name)

    return GtkWithDialog()


def test_the_export_button_asks_where_then_a_task_writes_the_gpx(tmp_path, in_the_window):
    import xml.etree.ElementTree as ET

    dialog = a_dialog_that_saves_into(tmp_path)
    in_the_window("Gtk", gtk_with(dialog))
    win, section = the_page()
    win.session = on_device(tmp_path, 1)
    win.progress = lambda _step: None
    said = []
    win.toast = said.append
    win.load_activities()
    button = export_button(rows_of(section)[0])
    button.emit("clicked")
    [asked] = dialog.made
    name = "2026-09-01 00.00 Running.gpx"
    assert asked.properties["initial_name"] == name
    assert win.run_task.titles == ["Reading Activities…", "Exporting as GPX…"]
    assert win.run_task.calls[1].button is button
    root = ET.fromstring((tmp_path / name).read_bytes())
    assert root.get("creator") == "openExpress"
    assert said == [f"Saved as {name}"]


def test_an_insensitive_export_button_starts_nothing(tmp_path, in_the_window):
    dialog = a_dialog_that_saves_into(tmp_path)
    in_the_window("Gtk", gtk_with(dialog))
    win, section = the_page()
    win.show_activities(made_up_list(*THE_WEEK))
    strength = export_button(rows_of(section)[1])
    assert not strength.get_sensitive()
    strength.emit("clicked")
    assert dialog.made == [] and win.run_task.calls == []


def test_while_another_task_runs_the_export_asks_nothing(tmp_path, in_the_window):
    dialog = a_dialog_that_saves_into(tmp_path)
    in_the_window("Gtk", gtk_with(dialog))
    win, section = the_page()
    said = []
    win.toast = said.append
    win.show_activities(made_up_list(*THE_WEEK))
    win.busy = True
    export_button(rows_of(section)[0]).emit("clicked")
    assert dialog.made == [] and win.run_task.calls == []
    assert said == ["Another operation is in progress"]


def test_the_window_s_tasks_never_turn_on_an_insensitive_export_button(monkeypatch):
    """Every other task turns the page's buttons off and on again: an export button follows,
    one that cannot export stays off."""
    from openexpress import netstatus
    from openexpress.gui import app

    monkeypatch.setattr(app.MainWindow, "refresh_device", lambda self, cause=None: None)
    win = app.MainWindow(None)
    try:
        win.session = types.SimpleNamespace()
        win.show_activities(made_up_list(*THE_WEEK))
        buttons = [export_button(r) for r in rows_of(win.sections["activities"])]
        win.session = None  # the page as drawn; the window's actions ask nothing of a device
        win.set_busy(True)
        assert not any(b.get_sensitive() for b in buttons if b is not None)
        win.set_busy(False)
        assert [None if b is None else b.get_sensitive() for b in buttons] == [
            True, False, None, True, None, True]
    finally:
        netstatus.STATUS.remove_listener(win._net_listener)
        win.destroy()
