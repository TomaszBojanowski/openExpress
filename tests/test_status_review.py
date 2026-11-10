"""The page Device Status checked through its states in the real window, before a trial on a
device: made-up devices shaped like a watch, a bike computer and a test drive, in the narrowest
window and a wide one, in English, Polish and German. tools/status-review.py runs it and gathers a
picture of every state.

The states: connected; after Free Up Space; after the file of an update removed; not connected,
its view and its page from the records; a device without records; plugged in while its page from
the records is shown; another device opened in the same window. In each: nothing said by
Adwaita, GTK or GLib as WARNING or CRITICAL; the window no wider than 360 px at its narrowest;
the kinds of file with System as many bytes as the used space; the legend one column; no
subtitle narrower than 160 px; the window, the command line and the device log telling the same
numbers; nothing that needs the device on the page from the records. All devices, files and
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

# The shapes: files (path, size, age in days), the updates the device did not install (path,
# name, version), whether a test drive.
SHAPES = {
    "watch": types.SimpleNamespace(
        unit=3000000101, name="Made-up Watch", drive=False, files=[
            ("Garmin/Activity/A0001.FIT", 4_000, 1),
            ("Garmin/Debug/dump/RAM.zip", 9_100, 1),
            *[(f"Garmin/Debug/dump/RAM_bak{n}.zip", 8_980, 1 + n * 5) for n in range(1, 6)]],
        updates=[], crash=True),
    "bike": types.SimpleNamespace(
        unit=3000000102, name="Made-up Bike Computer", drive=False, files=[
            ("Garmin/Activity/A0002.FIT", 6_000, 2),
            ("Garmin/Debug/dump/ramdumpC2.zip", 13_300, 3),
            ("Garmin/Debug/dump/ramdump1.zip", 45_800, 60),
            ("Garmin/Debug/dump/ramdump3.zip", 29_500, 61),
            *[(f"Garmin/Debug/ramdump{n}.log", 202_500, 600 + n) for n in range(1, 4)],
            ("Garmin/Debug/ramdump_err1.txt", 40, 600),
            ("Garmin/RemoteSW/006-D0000-00.GCD", 4_100, 5)],
        updates=[("Garmin/RemoteSW/006-D0000-00.GCD", "GPS software", "6.20")], crash=False),
    "drive": types.SimpleNamespace(
        unit=3000000103, name="Made-up Test Drive", drive=True, files=[
            ("Garmin/Debug/dump/RAM.zip", 9_100, 2),
            ("Garmin/Debug/dump/RAM_bak1.zip", 8_980, 15),
            ("Garmin/Debug/dump/RAM_bak2.zip", 8_980, 26),
            ("Garmin/Debug/ramdump1.log", 120_000, 640),
            ("Garmin/Debug/ramdump_err1.txt", 40, 640),
            ("Garmin/SQL/MapRegionGrouping.db", 5_857, 1),
            ("Garmin/RemoteSW/gmaptz.img", 5_827, 1)],
        updates=[("Garmin/SQL/MapRegionGrouping.db", "Map Region Grouping file", "2.31"),
                 ("Garmin/RemoteSW/gmaptz.img", "Time Zone Map", "43.00")], crash=False),
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
        (root / "Garmin" / "Debug" / "err_log.bak").write_text(json.dumps([crash_entry(3)]))


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
    """The updates the made-up device did not install whose files it still holds."""
    from openexpress import updates

    shape = next(s for s in SHAPES.values() if s.unit == device.unit_id)
    return [updates.SoftwareUpdate(
        part_number="006-D0000-00", name=name, version=version, severity="Recommended",
        is_primary=False, is_reinstall=False, restart_required=False, device_path=path, url="",
        md5=None, size=(storage.root / path).stat().st_size, order=0, on_device=True)
        for path, name, version in shape.updates if (storage.root / path).exists()]


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


def check(r, said, state: str, facts=None) -> None:
    """The hard conditions of the review in this state."""
    from gi.repository import Adw, Gtk

    from openexpress.gui.window_tasks import row_subtitle

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
    for row in (w for w in descendants(page) if isinstance(w, Adw.ActionRow)):
        label = row_subtitle(row)
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
    from openexpress import cli, history

    facts = r.win.status_facts
    older = facts.older_dumps() or []
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
def test_the_page_device_status_through_its_states(review, shape, width, language):
    from openexpress import known_devices

    with gtk_complaints(("Adwaita", "Gtk", "GLib", "GLib-GObject")) as said:
        r = review(language, width)
        r.shape = shape
        s = plug(r, shape)
        status_page(r)
        check(r, said, "connected", r.win.status_facts)
        agree(r, s)
        from openexpress.i18n import _

        if r.win.status_facts.older_dumps():
            answer(r, _("Older Memory Dumps"))
            check(r, said, "after-free-up-space", r.win.status_facts)
            agree(r, s)
        for _path, name, version in SHAPES[shape].updates[:1]:
            answer(r, _("{name} {version}").format(name=name, version=version))
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
            kept = r.win.kept_status.facts
            assert kept.dumps_at_last == (len(r.win.status_facts.dumps),
                                          sum(z for _p, z, _t in r.win.status_facts.dumps)), (
                "the records are not those of the device after the removal")
            said_count = _("Counted again after a removal, {when}").split("{when}")[0]
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
