"""The page Device Status checked through its states in the real window, before a trial on a
device: made-up devices shaped like a watch, a bike computer and a test drive, in the narrowest
window and a wide one, in English, Polish and German. tools/status-review.py runs it and gathers a
picture of every state.

The states: connected; every crash and update shown (Show All); after Copy Crash Logs; after
another file written to the device; after Refresh on the page; after Free Up Space; after the file
of an update removed; not connected, its view and its page from the records, also once its view is
drawn again under it; a device without records; plugged in while its page from the records is
shown; another device opened in the same window; and the page chosen while the window still reads
the device's error logs. In each: nothing said by Adwaita, GTK or GLib as WARNING or CRITICAL; the
window no wider than 360 px at its narrowest; the kinds of file with System as many bytes as the
used space; the legend one column; no subtitle narrower than 160 px; no card or row without a
title; each colour dot level with the first line of its name, in the legend and in Category
Details; the error logs read and the summary the same on the page and in its row; no file of the
folder of dumps that is no dump shown, counted or removed; the window, the command line and the
device log telling the same numbers; a dump that fits the limit of the copies alone but not the
room the others left removed without a copy, and the question saying why; nothing that needs the
device on the page from the records, which stays the view of its device. All devices, files and
sizes are made up."""

import contextlib
import io
import json
import os
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import DEVICE_XML
from fakes import descendants, gtk_complaints, make_client, program_style

from openexpress.status import SHOWN_EVENTS

pytestmark = [
    pytest.mark.needs_display,
    pytest.mark.filterwarnings("ignore:'asyncio.*:DeprecationWarning"),
]

# A folder: a picture of every state is written there (tools/status-review.py).
SHOTS = os.environ.get("OPENEXPRESS_REVIEW_SHOTS")
NARROWEST = 360
WIDE = 1000
FONT = "Adwaita Sans 11"
DECORATION = "appmenu:minimize,maximize,close"
SUBTITLE_AT_LEAST = 160
CAPACITY = 1_000_000_000
SYSTEM = 200_000_000  # what the device's own files take besides those listed
COPY_LIMIT = 100_000  # the copies before a removal: a dump larger than this goes without one
NOW = time.time()

# A file of the folder of dumps that is no dump: never shown, counted or removed.
FOREIGN = ("Garmin/Debug/dump/notes.txt", 50, 1)
# The shapes: files (path, size, age in days), the updates the device did not install (path,
# name, version), whether a test drive, the ages (days) of the crashes its error log holds.
SHAPES = {
    "watch": types.SimpleNamespace(
        unit=3000000101, name="Made-up Watch", drive=False, files=[
            ("Garmin/Activity/A0001.FIT", 4_000, 1),
            ("Garmin/Debug/dump/RAM.zip", 9_100, 1),
            *[(f"Garmin/Debug/dump/RAM_bak{n}.zip", 8_980, 1 + n * 5) for n in range(1, 6)],
            FOREIGN],
        updates=[], crash=(2, 3, 6, 9, 13, 20)),
    "bike": types.SimpleNamespace(
        unit=3000000102, name="Made-up Bike Computer", drive=False, files=[
            ("Garmin/Activity/A0002.FIT", 6_000, 2),
            ("Garmin/Debug/dump/ramdumpC2.zip", 13_300, 3),
            ("Garmin/Debug/dump/ramdump1.zip", 45_800, 60),
            ("Garmin/Debug/dump/ramdump3.zip", 29_500, 61),
            *[(f"Garmin/Debug/ramdump{n}.log", 202_500, 600 + n) for n in range(1, 4)],
            ("Garmin/Debug/ramdump_err1.txt", 40, 600),
            ("Garmin/RemoteSW/006-D0000-00.GCD", 4_100, 5), FOREIGN],
        updates=[("Garmin/RemoteSW/006-D0000-00.GCD", "GPS software", "6.20")], crash=()),
    "drive": types.SimpleNamespace(
        unit=3000000103, name="Made-up Test Drive", drive=True, files=[
            ("Garmin/Debug/dump/RAM.zip", 9_100, 2),
            ("Garmin/Debug/dump/RAM_bak1.zip", 8_980, 15),
            ("Garmin/Debug/dump/RAM_bak2.zip", 8_980, 26),
            # Each fits the copies' limit alone; together with those above they do not: the
            # older one goes without a copy.
            ("Garmin/Debug/dump/RAM_bak3.zip", 60_000, 30),
            ("Garmin/Debug/dump/RAM_bak4.zip", 60_000, 31),
            ("Garmin/Debug/ramdump1.log", 120_000, 640), FOREIGN,
            ("Garmin/Debug/ramdump_err1.txt", 40, 640),
            ("Garmin/SQL/MapRegionGrouping.db", 5_857, 1),
            ("Garmin/RemoteSW/gmaptz.img", 5_827, 1)],
        updates=[("Garmin/SQL/MapRegionGrouping.db", "Map Region Grouping file", "2.31"),
                 ("Garmin/RemoteSW/gmaptz.img", "Time Zone Map", "43.00")], crash=()),
}
OTHER = {"watch": "bike", "bike": "drive", "drive": "watch"}  # opened next in the same window


def crash_entry(age_days: float) -> dict:
    """An entry of an error log as a watch logs it (the shape seen; every value made up)."""
    moment = datetime.fromtimestamp(NOW) - timedelta(days=age_days)
    return {"unit_id": "3999999990", "error_cause": "RTL Trap", "system_version": "23.39",
            "ms_since_epoch": str(int((moment - datetime(1970, 1, 1)).total_seconds() * 1000)),
            "proprietary_info": {"Time": moment.strftime("%Y-%m-%d %H:%M:%S"),
                                 "RTL Error Number": "0x003", "programCounter": "0x10000abc",
                                 "pageStack": "2301"}}


def make_device(root: Path, shape) -> None:
    (root / "Garmin").mkdir(parents=True)
    (root / "Garmin" / "GarminDevice.xml").write_text(
        DEVICE_XML.replace("<Id>1234567890</Id>", f"<Id>{shape.unit}</Id>")
        .replace("Test watch", shape.name))
    for path, size, age in shape.files:
        where = root / path
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(bytes([len(path) % 251]) * size)
        os.utime(where, (NOW - age * 86400, NOW - age * 86400))
    if shape.crash:
        (root / "Garmin" / "Debug" / "err_log.bak").write_text(
            json.dumps([crash_entry(age) for age in shape.crash]))


def session_of(root: Path, shape):
    """A session of the made-up device in `root`: over MTP, or a test drive."""
    from openexpress import service
    from openexpress.device.gio_storage import GarminUsb
    from openexpress.device.storage import LocalStorage, UmsStorage
    from openexpress.settings import Settings

    def used(storage) -> int:
        return sum(p.stat().st_size for p in storage.root.rglob("*") if p.is_file())

    class Space:
        """A made-up capacity; the free space what the files and the system leave."""

        def capacity(self):
            return CAPACITY

        def free_space(self):
            return CAPACITY - SYSTEM - used(self)

    class OverMtp(Space, LocalStorage):
        mass_storage = False

        def __init__(self, folder):
            super().__init__(folder)
            self.device_uri = f"mtp://made-up-{shape.unit % 1000}/"
            self.usb = types.SimpleNamespace(bus=1, address=shape.unit % 100, for_testing=False)

    class TestDrive(Space, UmsStorage):
        pass

    if shape.drive:
        usb = GarminUsb("5678", 1, shape.unit % 100, serial="MADE-UP-SERIAL-7", vendor="0781")
        storage = TestDrive(root, root.as_uri() + "/", usb=usb)
    else:
        storage = OverMtp(root)
    # Read as the program reads a device found now: remembered (devices.json) with it.
    device = service.read_device(storage, found_now=True)
    return service.DeviceSession(storage, device, Settings(), make_client(auth=False))


def not_installed(storage, device) -> list:
    """The updates the made-up device did not install whose files it still holds, each with the
    size and time of its file, as freeing.updates_not_installed gives them."""
    from openexpress import freeing, updates

    shape = next(s for s in SHAPES.values() if s.unit == device.unit_id)
    found = [updates.SoftwareUpdate(
        part_number="006-D0000-00", name=name, version=version, severity="Recommended",
        is_primary=False, is_reinstall=False, restart_required=False, device_path=path, url="",
        md5=None, size=(storage.root / path).stat().st_size, order=0, on_device=True)
        for path, name, version in shape.updates if (storage.root / path).exists()]
    for u in found:
        u.listed_file = freeing.listed_file(storage, u)
    return found


def iterate(seconds: float = 0.05) -> None:
    from gi.repository import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)


def wait(until, what: str, seconds: float = 15) -> None:
    from gi.repository import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if until():
            iterate(0.1)  # what follows it drawn too
            return
        GLib.MainContext.default().iteration(False)
    raise AssertionError(f"not reached: {what}")


@pytest.fixture
def review(monkeypatch, tmp_path, in_the_window):
    """The real window as the program builds it, in the language and width asked for; devices
    "plugged in" (the detection gives their session), questions answered as a click does."""
    from gi.repository import Gtk

    from openexpress import freeing, i18n
    from openexpress.gui import detection, window_tasks
    from openexpress.gui.app import MainWindow

    families = {f.get_name() for f in Gtk.Label().get_pango_context().get_font_map()
                .list_families()}
    if FONT.rsplit(" ", 1)[0] not in families:
        pytest.skip(f"the widths are measured in GNOME's font, {FONT}, not installed here")
    for name in ("_start", "_want_account", "_show_device_picture", "_check_updates_quietly",
                 "_check_maps_quietly", "_load_app_icons", "_fetch_account", "_on_mount_added",
                 "_on_mount_removed", "_remember_geometry"):
        monkeypatch.setattr(MainWindow, name, lambda self, *a, **kw: None)
    monkeypatch.setattr(MainWindow, "_poll_usb", lambda self, clock=None: False)
    monkeypatch.setattr(MainWindow, "_background_enabled", lambda self: False)
    monkeypatch.setattr(freeing, "REMOVED_LIMIT", COPY_LIMIT)
    monkeypatch.setattr(freeing, "updates_not_installed",
                        lambda client, storage, device, fresh=False: (
                            not_installed(storage, device), None))
    plugged = {}
    monkeypatch.setattr(detection, "detect_devices", lambda current_uri=None: plugged["now"])
    asked = []
    real_confirm = window_tasks.confirm

    def confirm(parent, name, heading, body, *answers, **kw):
        dialog = real_confirm(parent, name, heading, body, *answers, **kw)
        asked.append(dialog)
        return dialog

    in_the_window("confirm", confirm)
    settings = Gtk.Settings.get_default()
    changed = {"gtk-font-name": FONT, "gtk-xft-dpi": 96 * 1024,
               "gtk-decoration-layout": DECORATION}
    windows = []

    def make(language: str, width: int):
        i18n.setup(language)
        for name, value in changed.items():
            settings.set_property(name, value)
        program_style()
        win = MainWindow(None)
        windows.append(win)
        win.set_default_size(width, 900)
        win.present()
        wait(lambda: win.split.get_collapsed() == (width == NARROWEST), "the window's width")
        return types.SimpleNamespace(win=win, plugged=plugged, asked=asked, language=language,
                                     width=width, root=tmp_path)

    try:
        yield make
    finally:
        for win in windows:
            win.destroy()
        for name in changed:
            settings.reset_property(name)
        i18n.setup("en")


# --- what the window does, as the user does it -----------------------------------------------


def plug(r, shape_name: str):
    """The made-up device plugged in (made once in this review), detected and opened."""
    shape = SHAPES[shape_name]
    root = r.root / shape_name
    if not root.exists():
        make_device(root, shape)
    s = session_of(root, shape)
    r.plugged["now"] = s
    r.win.refresh_device()
    wait(lambda: r.win.session is s and not r.win.busy, f"{shape_name} opened")
    wait(lambda: r.win.navigation.get_visible_page() is r.win.device_view, "its view")
    return s


def status_page(r) -> None:
    """Device Status chosen, read as the window reads it."""
    sec = r.win.sections["status"]
    r.win.select_section("status")
    wait(lambda: sec.loaded and not r.win.busy and sec.stack.get_visible_child_name() in
         ("content", "info"), "the page Device Status")


def answer(r, row_title: str) -> None:
    """The button of the row clicked, its question answered "ok" (the destructive answer),
    the task run and the page read again."""
    from gi.repository import Adw, Gtk

    sec = r.win.sections["status"]
    row = next(w for w in descendants(sec.stack)
               if isinstance(w, Adw.ActionRow) and w.get_title() == row_title)
    (button,) = [w for w in descendants(row) if isinstance(w, Gtk.Button)]
    before = len(r.asked)
    button.emit("clicked")
    wait(lambda: len(r.asked) > before, "the question")
    dialog = r.asked[-1]
    shot(r, "question")
    facts = r.win.status_facts
    dialog.emit("response", "ok")
    dialog.force_close()
    wait(lambda: r.win.status_facts is not facts and not r.win.busy and
         sec.stack.get_visible_child_name() == "content", "the page read again after it")


def show_all(r, kept: bool = False) -> None:
    """Show All of the page: the button beside the description, or its last row (narrow)."""
    from gi.repository import Adw, Gtk

    from openexpress.i18n import _

    view = r.win.kept_status if kept else r.win
    group = view.status_history
    button = group.get_header_suffix()
    if isinstance(button, Gtk.Button):
        button.emit("clicked")
    else:
        row = next(w for w in view.status_history_rows
                   if isinstance(w, Adw.ActionRow) and w.get_title() == _("Show All"))
        row.emit("activated")
    iterate(0.2)
    assert view.status_show_all and len(view.status_history_rows) >= len(
        view.status_facts.events)


def copy_crash_logs(r, monkeypatch, s) -> None:
    """Copy Crash Logs, its boot log holding a crash the error logs do not (made up)."""
    from openexpress import diagnostics
    from openexpress.diagnostics import Copied
    from openexpress.gui import status_page

    def copied(device, storage, progress=None):
        where = diagnostics.folder(device.unit_id)
        moment = datetime.fromtimestamp(NOW) - timedelta(days=25, hours=5)
        boot = (f"[{moment:%m/%d/%y %H:%M:%S} 123]: RTL Trap detected, errnum: 0x6e5d8003\n"
                .encode())
        files, made = {}, Copied()
        diagnostics._keep_file(where, files, made, diagnostics.CRASH_LOGS[0], boot, None)
        diagnostics._save(where, "crash logs", files, {}, datetime.fromtimestamp(NOW),
                          device.software_version_str)
        made.saved = True
        return made

    monkeypatch.setattr(diagnostics, "copy_crash_logs", copied)
    facts = r.win.status_facts
    status_page.copy_crash_logs(r.win)
    sec = r.win.sections["status"]
    wait(lambda: r.win.status_facts is not facts and not r.win.busy
         and sec.stack.get_visible_child_name() == "content", "the page after Copy Crash Logs")
    assert len(r.win.status_facts.crashes) == len(facts.crashes) + 1, (
        "the boot log's crash not on the page after Copy Crash Logs")


def write_another_file(r, s) -> None:
    """Another file written to the device in the same session (as a map, a song, an app): the
    page is read again with the space of now."""
    from openexpress.gui.tasks import writes_to_device

    facts = r.win.status_facts
    where = s.storage.root / "Garmin" / "Activity" / "written.FIT"

    def write():
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(b"w" * 3_000)

    r.win.run_task("Writing", writes_to_device(write), lambda _result: None)
    sec = r.win.sections["status"]
    wait(lambda: r.win.status_facts is not facts and not r.win.busy
         and sec.stack.get_visible_child_name() == "content" and r.win.status_facts.free
         != facts.free, "the page with the space after another write")


def refresh_on_the_page(r, shape_name: str):
    """Refresh with the page shown: the device opened again, its page read once the error logs
    are read again."""
    shape = SHAPES[shape_name]
    s = session_of(r.root / shape_name, shape)
    r.plugged["now"] = s
    r.win.refresh_device()
    sec = r.win.sections["status"]
    wait(lambda: r.win.session is s and not r.win.busy and sec.loaded
         and sec.stack.get_visible_child_name() in ("content", "info"), "the page refreshed")
    return s


def unplug(r) -> None:
    from openexpress.gui.connected import close_as_unplugged

    close_as_unplugged(r.win)
    wait(lambda: r.win.navigation.get_visible_page() is r.win.start.page, "the start page")


def remembered(r, unit: int) -> None:
    """Its tile on the start page chosen: the view of the device not connected."""
    from openexpress import known_devices

    r.win._show_remembered(known_devices.load()[unit])
    wait(lambda: r.win.navigation.get_visible_page() is r.win.remembered.page and
         r.win.remembered.status_row.get_visible() == (r.win.kept_status is not None),
         "the remembered view")
    iterate(0.3)


def kept_page(r) -> None:
    r.win.remembered.status_row.emit("activated")
    wait(lambda: r.win.kept_status is not None and r.win.kept_status.page is not None and
         r.win.navigation.get_visible_page() is r.win.kept_status.page, "the page from records")


# --- what every state must hold ---------------------------------------------------------------


def shown_page(r):
    """The page shown: the status page of the device view, the remembered view or the page
    from the records."""
    visible = r.win.navigation.get_visible_page()
    if visible is r.win.device_view:
        return r.win.sections["status"].stack
    return visible.get_child()


def first_line_middle(label, inside) -> float:
    """How far below the top of `inside` the middle of the first line of `label` is."""
    from gi.repository import Graphene

    _ink, line = label.get_layout().get_line_readonly(0).get_pixel_extents()
    _ok, point = label.compute_point(inside, Graphene.Point().init(0, line.height / 2))
    return point.y


def drawn_middle(widget) -> float | None:
    """The middle, from the widget's top, of what it draws: the rows of its pixels that are
    not transparent (a colour dot draws nothing else)."""
    from gi.repository import Gdk, Graphene, Gtk

    w, h = widget.get_width(), widget.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return None
    texture = widget.get_native().get_renderer().render_texture(
        node, Graphene.Rect().init(0, 0, w, h))
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    raw = data.get_data()
    painted = [y for y in range(h) if any(raw[y * stride + x * 4 + 3] > 128 for x in range(w))]
    return (painted[0] + painted[-1] + 1) / 2 if painted else None


def check_titles(page, where: str) -> None:
    """No card or row without a title: a row's title, a card's words."""
    from gi.repository import Adw, Gtk

    for row in descendants(page):
        if isinstance(row, Adw.PreferencesRow) and row.get_mapped():
            assert row.get_title(), f"{where}: a row without a title"
        if row.has_css_class("card") and row.get_mapped():
            words = [w.get_label() for w in descendants(row)
                     if isinstance(w, Gtk.Label) and w.get_mapped() and w.get_label()]
            assert words, f"{where}: a card without words"


def check_dots(page, where: str) -> None:
    """Each colour dot level with the first line of its name: in the legend the label beside
    it, in Category Details the title of its row."""
    from gi.repository import Adw, Gtk

    from openexpress.gui.window_tasks import row_label

    for dot in descendants(page):
        if not isinstance(dot, Gtk.DrawingArea) or not dot.get_mapped():
            continue
        row = dot.get_ancestor(Adw.ActionRow)
        if row is not None:
            name = row_label(row, "title")
        elif dot.get_parent() is not None and dot.get_parent().get_parent() is not None \
                and dot.get_parent().get_parent().has_css_class("oe-status-legend"):
            name = dot.get_next_sibling()
        else:
            continue  # the bar
        middle = drawn_middle(dot)
        assert middle is not None, f"{where}: a dot draws nothing"
        level = first_line_middle(name, dot)
        assert abs(middle - level) <= 1.5, (
            f"{where}: the dot of {name.get_label()!r} at {middle:.1f}, its name at {level:.1f}")


def check_said(r, facts, page, where: str) -> None:
    """What the page says of the device: its error logs read (every made-up device's can be),
    its summary the one of its facts, and the same summary in its row as a sentence (the device
    page's, or the remembered view's); no file that is no dump among its dumps."""
    from gi.repository import GLib, Gtk

    from openexpress.gui import words

    assert facts.errors_known, f"{where}: the error logs said not read"
    summary = words.status_summary(facts.crashes, facts.errors_known, facts.free,
                                   facts.capacity, datetime.now())
    title = summary.title
    titles = [w.get_label() for w in descendants(page)
              if isinstance(w, Gtk.Label) and w.has_css_class("title-3")]
    assert title in titles, f"{where}: the summary {titles}, not {title!r}"
    row = r.win.status_row if facts.connected else r.win.remembered.status_row
    if row is not None and row.get_visible():
        assert (row.get_subtitle() or "").startswith(GLib.markup_escape_text(summary.row)), (
            f"{where}: the row says {row.get_subtitle()!r}, the page {title!r}")
    foreign = FOREIGN[0].rsplit("/", 1)[1]
    assert not any(path.endswith(foreign) for path, _s, _t in facts.dumps or ()), (
        f"{where}: a file that is no dump among the dumps")


def check(r, said, state: str, facts=None) -> None:
    """The hard conditions of the review in this state."""
    from gi.repository import Adw, Gtk

    from openexpress.gui.window_tasks import row_label

    where = f"{state} ({r.language}, {r.width} px)"
    assert said == [], f"{where}: {said}"
    if r.width == NARROWEST:  # a wide window lays out its side panel: it folds when narrower
        narrowest = r.win.get_content().measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        assert narrowest <= NARROWEST, f"{where}: the window needs {narrowest} px"
    page = shown_page(r)
    expanders = [w for w in descendants(page) if isinstance(w, Adw.ExpanderRow)]
    for expander in expanders:  # the details inside measured too
        expander.set_expanded(True)
    iterate(0.2)
    check_titles(page, where)
    check_dots(page, where)
    for row in (w for w in descendants(page) if isinstance(w, Adw.ActionRow)):
        label = row_label(row, "subtitle")
        if label is not None and label.get_label() and row.get_mapped():
            width = label.get_width()
            assert width >= SUBTITLE_AT_LEAST, f"{where}: {row.get_title()!r} subtitle {width}"
    legend = [w for w in descendants(page) if w.has_css_class("oe-status-legend")]
    for box in legend:
        starts = set()
        child = box.get_first_child()
        while child is not None:
            starts.add(round(child.compute_bounds(box)[1].get_x()))
            child = child.get_next_sibling()
        assert len(starts) == 1, f"{where}: the legend in {len(starts)} columns"
    if facts is not None:
        check_said(r, facts, page, where)
        assert "system" in facts.categories, f"{where}: no System"
        assert sum(facts.categories.values()) == facts.capacity - facts.free, (
            f"{where}: the kinds of file are not the used space")
    shot(r, state)
    for expander in expanders:
        expander.set_expanded(False)


def check_kept(r, state: str) -> None:
    """The page from the records: no button that needs the device."""
    from gi.repository import Gtk

    from openexpress.i18n import _, pgettext

    allowed = {pgettext("open the folder of the copies", "Open"), _("Show All")}
    labels = {b.get_label() for b in descendants(shown_page(r))
              if isinstance(b, Gtk.Button) and b.get_label() and b.get_mapped()}
    assert labels <= allowed, f"{state}: {labels - allowed}"


def agree(r, s) -> None:
    """The window, the command line and the device log tell the same numbers."""
    from openexpress import cli, freeing, history

    facts = r.win.status_facts
    older = freeing.older_dumps(facts.dumps) or []
    files = [u for u in facts.not_installed]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["--lang", "en", "diagnostics", "--path", str(s.storage.root),
                         "--free-space"]) == 0
    from openexpress import i18n

    i18n.setup(r.language)
    text = out.getvalue()
    if older:
        assert f"older memory dumps: {len(older)} " in text, text
    else:
        assert "older memory dumps" not in text, text
    assert text.count("update file not installed by the device") == len(files), text
    line = history.read(s.device.unit_id, bool(getattr(s.storage, "test_drive", False))).lines[-1]
    assert line["dumps"]["count"] == len(facts.dumps)
    assert line["dumps"]["bytes"] == sum(size for _p, size, _t in facts.dumps)
    assert line["free"] == facts.free and line["categories"] == facts.categories


def shot(r, state: str) -> None:
    """A picture of the window in this state, when the review gathers them."""
    if not SHOTS:
        return
    from gi.repository import Graphene, Gtk

    win = r.win
    iterate(0.2)
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    snap.scale(2, 2)
    _found, bounds = win.compute_bounds(win)
    snap.save()
    snap.translate(Graphene.Point().init(bounds.get_x(), bounds.get_y()))
    Gtk.WidgetPaintable.new(win).snapshot(snap, bounds.get_width(), bounds.get_height())
    snap.restore()
    texture = win.get_native().get_renderer().render_texture(
        snap.to_node(), Graphene.Rect().init(0, 0, w * 2, h * 2))
    folder = Path(SHOTS)
    folder.mkdir(parents=True, exist_ok=True)
    width = "narrow" if r.width == NARROWEST else "wide"
    shape = getattr(r, "shape", "")
    n = getattr(r, "taken", 0) + 1
    r.taken = n
    texture.save_to_png(str(folder / f"{shape}-{r.language}-{width}-{n:02d}-{state}.png"))


# --- the review -------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "pl", "de"])
@pytest.mark.parametrize("width", [NARROWEST, WIDE], ids=["360", "wide"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_the_page_device_status_through_its_states(review, shape, width, language,
                                                   monkeypatch):
    from openexpress import known_devices

    with gtk_complaints(("Adwaita", "Gtk", "GLib", "GLib-GObject")) as said:
        r = review(language, width)
        r.shape = shape
        s = plug(r, shape)
        status_page(r)
        check(r, said, "connected", r.win.status_facts)
        agree(r, s)
        from openexpress.i18n import _

        if len(r.win.status_facts.events) > SHOWN_EVENTS:
            show_all(r)
            check(r, said, "show-all", r.win.status_facts)
        if r.win.status_facts.hidden_logs:
            copy_crash_logs(r, monkeypatch, s)
            check(r, said, "after-copy-crash-logs", r.win.status_facts)
        write_another_file(r, s)
        check(r, said, "after-another-write", r.win.status_facts)
        s = refresh_on_the_page(r, shape)
        check(r, said, "after-refresh-on-the-page", r.win.status_facts)
        agree(r, s)

        plan = r.win.status_facts.dumps_plan
        if plan is not None and plan.older:
            from openexpress import freeing

            answer(r, _("Older Memory Dumps"))
            if any(size <= COPY_LIMIT for _p, size, _t in plan.without_copy):
                # One fits the limit alone, not the room the others left: the question says
                # the copies would not fit in it.
                assert freeing.limit_words() in r.asked[-1].get_body(), r.asked[-1].get_body()
            check(r, said, "after-free-up-space", r.win.status_facts)
            agree(r, s)
        assert (r.root / shape / FOREIGN[0]).exists(), "a file that is no dump was removed"
        for path, name, version in SHAPES[shape].updates[:1]:
            answer(r, _("{name} {version}").format(name=name, version=version))
            assert not (r.root / shape / path).exists(), "the update file was not removed"
            check(r, said, "after-update-file-removed", r.win.status_facts)
            agree(r, s)
        unplug(r)
        check(r, said, "start-page")
        if not SHAPES[shape].drive:  # a test drive's records are its own, not the device's
            remembered(r, SHAPES[shape].unit)
            assert r.win.remembered.status_row.get_visible()
            check(r, said, "remembered-view")
            kept_page(r)
            check(r, said, "from-the-records", r.win.kept_status.facts)
            check_kept(r, "from-the-records")
            if len(r.win.kept_status.facts.events) > SHOWN_EVENTS:
                show_all(r, kept=True)
            shown = r.win.kept_status.page
            r.win._redraw_remembered()  # the account's list came while the page is shown
            from openexpress.gui.detection import visible_view
            from openexpress.gui.navigation import Page

            assert r.win.kept_status.page is shown and visible_view(r.win).page is Page.REMEMBERED, (
                "the page from the records no longer the view of its device")
            check(r, said, "from-the-records-redrawn", r.win.kept_status.facts)
            kept = r.win.kept_status.facts
            assert kept.dumps_at_last == (len(r.win.status_facts.dumps),
                                          sum(z for _p, z, _t in r.win.status_facts.dumps)), (
                "the records are not those of the device after the removal")
            said_count = _("Counted again after a change, {when}").split("{when}")[0]
            assert kept.recounted and any(
                (w.get_subtitle() or "").startswith(said_count)
                for w in descendants(shown_page(r)) if hasattr(w, "get_subtitle")), (
                "the page from the records does not say it was counted after the removal")
            plug(r, shape)  # plugged in while its page from the records is shown
            status_page(r)
            check(r, said, "plugged-in-while-shown", r.win.status_facts)
            unplug(r)
        known_devices.remember(3000000199, "Made-up Old Watch", "006-B0000-00", "1.00", "mtp")
        remembered(r, 3000000199)
        assert not r.win.remembered.status_row.get_visible()
        check(r, said, "no-records")
        r.win.navigation.pop_to_page(r.win.start.page)
        wait(lambda: r.win.navigation.get_visible_page() is r.win.start.page, "the start page")
        other = plug(r, OTHER[shape])  # another device in the same window
        status_page(r)
        check(r, said, "another-device", r.win.status_facts)
        assert r.win.status_facts is not None
        agree(r, other)


@pytest.mark.parametrize("shape", list(SHAPES))
def test_the_page_chosen_while_the_error_logs_are_read(review, shape, monkeypatch):
    """Chosen at once, while the window still reads the error logs (seconds on a watch): the
    window stays free and the page says it reads; once read, the page has them."""
    import threading

    from openexpress import diagnostics

    real = diagnostics.at_open
    gate = threading.Event()

    def slow(device, storage, now=None):
        gate.wait(10)
        return real(device, storage, now)

    monkeypatch.setattr(diagnostics, "at_open", slow)
    with gtk_complaints(("Adwaita", "Gtk", "GLib", "GLib-GObject")) as said:
        r = review("en", WIDE)
        r.shape = shape
        s = plug(r, shape)
        sec = r.win.sections["status"]
        r.win.select_section("status")
        iterate(0.3)
        assert not r.win.busy and sec.stack.get_visible_child_name() == "loading"
        shot(r, "while-the-error-logs-are-read")
        gate.set()
        wait(lambda: sec.loaded and not r.win.busy
             and sec.stack.get_visible_child_name() in ("content", "info"), "the page")
        check(r, said, "after-the-reading", r.win.status_facts)
        agree(r, s)


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("width", [NARROWEST, WIDE], ids=["360", "wide"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_the_page_chosen_while_another_task_runs(review, shape, width, language):
    """Chosen while another task (a sync) runs: the page at once from what needs
    nothing of the device, under its banner, with no button that needs the device; once the task
    ends, the page read in full."""
    import threading

    from gi.repository import Gtk

    from openexpress.i18n import _, pgettext

    gate = threading.Event()
    with gtk_complaints(("Adwaita", "Gtk", "GLib", "GLib-GObject")) as said:
        r = review(language, width)
        r.shape = shape
        s = plug(r, shape)
        sec = r.win.sections["status"]
        r.win.run_task("Syncing…", lambda: gate.wait(10), lambda _result: None)
        wait(lambda: r.win.busy, "the task")
        r.win.select_section("status")
        wait(lambda: sec.loaded and sec.stack.get_visible_child_name() in ("content", "info"),
             "the page while the task runs")
        facts = r.win.status_facts
        assert facts.device_busy and r.win.busy
        if facts.has_diagnostics:
            assert sec.banner.get_revealed(), "no banner while the task runs"
        allowed = {pgettext("open the folder of the copies", "Open"), _("Show All"),
                   _("Report…")}
        labels = {b.get_label() for b in descendants(shown_page(r))
                  if isinstance(b, Gtk.Button) and b.get_label() and b.get_mapped()}
        assert labels <= allowed, f"a button that needs the device: {labels - allowed}"
        check(r, said, "while-another-task-runs", facts)
        gate.set()
        wait(lambda: not r.win.busy and r.win.status_facts is not facts and sec.loaded
             and sec.stack.get_visible_child_name() in ("content", "info"), "the page after it")
        assert not r.win.status_facts.device_busy and not sec.banner.get_revealed()
        check(r, said, "after-the-task", r.win.status_facts)
        agree(r, s)
