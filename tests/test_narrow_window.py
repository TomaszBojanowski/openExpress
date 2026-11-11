"""The window at its narrowest (MIN_WINDOW: 360 px), side panel folded: every page of the
device view and the start page fit, in English and in the longer Polish.

The real window, on the tests' hidden display, holds stand-ins: an Edge 1050 with a beta
update, long component names, maps, apps and music. Nothing asks a device or a server."""

import re
import time
import types
import uuid
from datetime import datetime, timedelta

import pytest
from fakes import (
    Waits,
    descendants,
    device_session,
    gpx_file,
    gpx_route,
    gpx_track,
    gpx_waypoint,
    program_style,
    record_sync,
    start_section,
)

NARROWEST = 360  # the width of gui_settings.MIN_WINDOW
FONT = "Adwaita Sans 11"  # GNOME's: the widths are measured in it
# The widest common title bar: three buttons of the window (as on Fedora's GNOME, which the
# window was measured on); GNOME's own default has only "close".
DECORATION = "appmenu:minimize,maximize,close"
UNIT = 3000000001

pytestmark = [
    pytest.mark.needs_display,
    # PyGObject's own (gi/events.py) while the test turns the GLib main loop; not our code.
    pytest.mark.filterwarnings("ignore:'asyncio.*:DeprecationWarning"),
]


def _device(description, version, part):
    from openexpress import music

    media = {*music.KINDS.values(), music.PLAYLIST}
    return types.SimpleNamespace(
        description=description, software_version_str=version, part_number=part,
        unit_id=UNIT, targets=lambda datatype: (
            [types.SimpleNamespace(path="Music", extension="mp3")] if datatype in media
            else []))


def _session(device, drive=False):
    storage = types.SimpleNamespace(free_space=lambda: 52_140_000_000,
                                    capacity=lambda: 64_000_000_000)
    client = types.SimpleNamespace(auth=types.SimpleNamespace(display="name@example.com"),
                                   consumer=object(), http=None)
    # On the account, its primary wearable: the row "Primary Wearable" is measured too
    # (in Polish its title is long).
    account = [{"unitid": device.unit_id, "primaryactivitytrackersettingcapable": True,
                "primaryactivitytrackerindicator": True}]
    return device_session(device=device, storage=storage, client=client, logged_in=True,
                          ownership_error=None, account_devices=account,
                          mass_storage=drive, diagnostics=_device_errors())


def _device_errors():
    """A crash the device logged lately, its hidden crash logs and five memory dumps."""
    from datetime import datetime

    from openexpress import diagnostics

    crash = diagnostics.ErrorEntry("RTL Trap", datetime(2026, 10, 20, 22, 24, 6), "0x003",
                                   "0x10000abc", "2301", "err_log.bak")
    return diagnostics.AtOpen([crash], [("Garmin/Debug/dump/RAM.zip", 8_090_553)] * 5, True)


def _status_facts():
    """The page Device Status at its fullest: crashes of the same error and updates, more than
    it shows at first (Show All as its last row in the narrowest window), every kind of file,
    older dumps and an update the device did not install, its copies and its hidden crash
    logs."""
    from datetime import datetime, timedelta

    from openexpress import status
    from openexpress.crashes import Crash

    now = datetime(2026, 10, 21, 12, 0)
    crashes = [Crash(now - timedelta(days=d), software="23.39", error="0x003",
                     program_counter="0x10000abc", screen="2886,2500,2502", errnum="0x1a2b3c4d")
               for d in (2, 9, 21, 25, 40, 60)]
    when = [(now - timedelta(days=d)).astimezone().isoformat(timespec="seconds")
            for d in (150, 30, 1)]
    lines = [{"time": t, "software": v} for t, v in zip(when, ("23.37", "23.38", "23.39"),
                                                          strict=True)]
    update = _update("Oprogramowanie systemu GPS z bardzo długą nazwą", "6.20", "GARMIN/GPS.GCD",
                     4_100_000, on_device=True)
    return status.Facts(
        free=12_400_000_000, capacity=31_100_000_000,
        categories={"maps": 17_200_000_000, "activities": 52_000_000, "music": 410_000_000,
                    "apps": 12_000_000, "courses": 305_000, "updates": 9_100_000,
                    "logs": 61_000_000, "system": 930_000_000},
        counted_at=now - timedelta(minutes=10), crashes=crashes, errors_known=True,
        dumps=[("GARMIN/Debug/dump/RAM.zip", 9_000_000, 1_700_000_300.0),
               ("GARMIN/Debug/dump/RAM_bak1.zip", 8_000_000, 1_700_000_200.0)],
        events=status.events(crashes, lines, (now - timedelta(days=200)).timestamp()),
        hidden_logs=True,
        copies=(12, 3_100_000, now - timedelta(hours=3)), not_installed=[update],
        has_diagnostics=True)


def _update(name, version, path, size, beta=False, system=False, reinstall=False,
            on_device=False):
    from openexpress import updates

    return updates.SoftwareUpdate(
        part_number="006-V4440-00" if system else "006-D0000-00", name=name, version=version,
        severity="Recommended", is_primary=system, is_reinstall=reinstall,
        restart_required=False, device_path=path, url="", md5=None, size=size, order=0,
        on_device=on_device, release_state="public_beta_phased_rollout" if beta else "live")


def _updates(copied: bool):
    """A beta of the system software (to install, or copied already) with long names of
    components around it."""
    from datetime import datetime

    items = [
        _update("Edge 1050 Software Bundle", "33.13", "Garmin/GUPDATE.GSP", 97_700_000,
                beta=True, system=True, on_device=copied),
        _update("Map Region Grouping file", "2.31", "Garmin/MapRegionGrouping.db", 212_000),
        _update("Wi-Fi, Bluetooth and ANT+ connectivity firmware", "11.20",
                "Garmin/RemoteSW/GUPDATE_WIFI_BT_ANT.GCD", 1_820_000)]
    for name, path in (("Time Zone Map", "Garmin/RemoteSW/gmaptz.img"),
                       ("Garmin Golf Course Database Information Update",
                        "Garmin/RemoteSW/GolfCourses.img")):
        items.append(_update(name, "41.00", path, 683_000, reinstall=True))
    if copied:
        # For the file of the copied one: it waits, with the name of that one.
        items.append(_update("Instinct 2/2S GPS Software", "6.70", "Garmin/GUPDATE.GSP",
                             1_200_000))
    else:
        # Copied before and offered again, its file changed since: the longer of its notes.
        items[2].copied_at = datetime(2026, 10, 30, 22, 5).timestamp()
        items[2].changed_on_device = True
    return items


def _maps():
    from openexpress.maps import MapFile, UniversalMap
    from openexpress.ownmaps import Overview, OwnMap

    def a_map(name, version, region, size, state, reinstall=True, waiting=None):
        return UniversalMap(name=name, map_type="TopoActive", state=state,
                            is_reinstall=reinstall, can_uninstall=state == 1, version=version,
                            region=region, identifier={},
                            files=[MapFile("Garmin/x.img", "u", None, size)],
                            files_to_remove=[], hosts=[], waiting=waiting)

    maps = [a_map("TopoActive Europe", "2025.20", "Europe", 8_200_000_000, 1),
            a_map("Garmin Cycle Map Europe", "2025.20", "Europe", 2_100_000_000, 1,
                  reinstall=False),
            a_map("TopoActive Middle America", "2025.20", "Americas", 1_300_000_000, 1,
                  waiting="install"),
            a_map("TopoActive Middle East and North Africa", "2025.20", "Africa",
                  3_400_000_000, 0),
            a_map("TopoActive Australia and New Zealand", "2025.20", "Oceania",
                  3_400_000_000, 0)]
    own = Overview(folder="Garmin", maps=[OwnMap("berlin.img", 41_200_000, "Berlin"),
                                          OwnMap("openfietsmap.img", 812_000_000,
                                                 "OpenFietsMap Benelux 2025")],
                   removable={"berlin.img"}, foreign={"openfietsmap.img"})
    return (maps, len(maps)), own


def _apps():
    from openexpress.ciq import (
        PRELOADED_PREFIX,
        STORE_APP_ID,
        InstalledApp,
        InstalledState,
        StoreApp,
    )

    def store(name, kind, version):
        return InstalledApp(app_id=uuid.uuid4(), app_type=kind, name=name, disabled=False,
                            version=version, filename="ABCDEF12", size=265_493)

    weather = store("Weather Forecast & Radar Widget Pro", 2, 41)
    built_in = [InstalledApp(app_id=uuid.UUID(f"{PRELOADED_PREFIX}-0000-0000-{i:012d}"),
                             app_type=8, name=name, disabled=False)
                for i, name in enumerate(("Running", "Open Water Swim", "Backcountry Snowboard"))]
    state = InstalledState(apps=[InstalledApp(app_id=STORE_APP_ID, app_type=1,
                                              name="Connect IQ Store", disabled=False),
                                 store("Spotify", 7, 73), weather, store("Face It", 3, 42),
                                 *built_in],
                           available_space=66_000_000, available_slots=27)
    account = [StoreApp(uuid.uuid4(), "Garmin Coach Training Plans Companion",
                        "Garmin Ltd or its subsidiaries", "watch-app", 238_504, 3, "3.0.2",
                        False)]
    # The update asks for new permissions: the longest line of an app.
    return state, account, [types.SimpleNamespace(app_id=weather.app_id, version_name="4.2.0",
                                                  permissions_changed=True)]


def _music():
    from openexpress.music import DeviceMedia

    return ([DeviceMedia("Music/Wykonawca z długą nazwą/Album/Utwór o długim tytule.mp3",
                         8_100_000, "track"),
             DeviceMedia("Podcasts/Podcast/Odcinek 12.mp3", 40_000_000, "podcast")],
            ["Music/Ulubione na trening biegowy.m3u8"])


def _activities():
    """What the Activities page shows: the longest Polish name of a sport, the longest state, a
    copy in a backup, and more to read (the button below the rows)."""
    from datetime import datetime

    from openexpress import activities, fit

    def read(name, found, source=activities.Source.DEVICE, backup=None):
        return activities.Read(activities.Activity(source, f"GARMIN/Activity/{name}", name, 1,
                                                   1.0, backup), found)

    start = int(datetime(2026, 12, 31, 23, 59).timestamp())
    reads = [
        read("ski.fit", fit.ActivitySummary(fit.FileState.DAMAGED, start=start, sport=13,
                                            timer_time=35_999.0, distance=123_456.0, ascent=9_999,
                                            has_positions=True),
             activities.Source.BACKUP, datetime(2026, 12, 31, 23, 59)),
        read("writing.fit", fit.ActivitySummary(fit.FileState.STILL_BEING_WRITTEN, created=start)),
        read("notes.fit", None),
    ]
    return types.SimpleNamespace(read=reads, left=30, listing=[r.activity for r in reads] * 11,
                                 only_in_backups=11)


def _tiles():
    """Tiles of devices connected, remembered and only on the account, with the words under
    their names as the window makes them."""
    from openexpress.devicelist import Tile
    from openexpress.gui.start_window import account_subtitle, choice_subtitle, remembered_subtitle
    from openexpress.known_devices import KnownDevice

    def tile(name, part, connected, last_seen=None, on_account=False):
        return Tile(name, part, "23.39", "mtp" if connected else None, connected, on_account,
                    False, last_seen, part, None, None)

    def connected(kind):
        return choice_subtitle(types.SimpleNamespace(
            found=types.SimpleNamespace(kind=kind), ok=True, software_version="23.39"))

    seen = time.time() - 86400
    venu = KnownDevice(UNIT, "Venu 2", "006-B3703-00", "19.05", "mtp", seen, seen)
    return [(tile("fēnix 8 - 47mm, Solar", "006-B4532-00", True), connected("mtp")),
            (tile("Edge 1050", "006-B4440-00", True), connected("mtp")),
            (tile("Varia RTL515", "006-B3592-03", True), connected("ums")),
            (tile("Venu 2", "006-B3703-00", False, seen), remembered_subtitle(venu)),
            (tile("epix (Gen 2)", "006-B3943-00", False, on_account=True),
             account_subtitle({"software_version": "27.18"}))]


EDGE = ("Edge 1050", "33.11", "006-B4440-00")
FENIX = ("fēnix 8 - 47mm, Solar", "23.39", "006-B4532-00")
INREACH = ("inReach Mini 2", "5.05", "006-B3968-00")


def _fill(win, page: str) -> str:
    """Builds `page` of the device view as the window does; returns its section."""
    from openexpress import sync
    from openexpress.gui.app import MainWindow

    drive = page.endswith("-drive")
    model = INREACH if drive else EDGE if page.startswith(("device", "updates")) else FENIX
    win.session = _session(_device(*model), drive=drive)
    if page == "device-refused-drive":
        # Not synced, with the longest of the reasons in place of the sync.
        win.session.drive_refusal = ("no-unit-info",
                                     "Device info: network error (made-up connection refused)")
    if page == "device-off-account":
        # Not on the account, no picture from unit-info: Get Picture From Account offered.
        from openexpress.connect import NotAccountDevice

        win.session.ownership_error = NotAccountDevice("not on the account")
        win.account_picture_offer = win.session
    win.update_state = "ready" if page == "device-update-copied" else "available"
    win.update_count = 1
    # The one update to install: the longest name of a component ("Device Software").
    win.update_only = _updates(copied=False)[2]
    win.map_state, win.map_update_count = "available", 1
    sync.SyncState(UNIT).save()  # synced here before
    record_sync(UNIT, uploaded=8, downloaded=4, skipped_known=3, errors=[], lines=[])
    for section in win.sections.values():
        section.clear()  # a page built before in this window goes
    key = {"device": "watch"}.get(page.split("-")[0], page.split("-")[0])
    if key == "watch":
        MainWindow.show_overview(win)
    elif key == "updates":
        MainWindow.show_updates(win, _updates(copied="copied" in page), beta=True)
    elif page == "maps-none":
        MainWindow.show_maps(win, ([], 0), None)
    elif key == "maps":
        # With the row of the space, as the page has it once the maps are read.
        storage = win.session.storage
        MainWindow.show_maps(win, *_maps(), space=(storage.free_space(), storage.capacity()))
    elif key == "apps":
        MainWindow.show_apps(win, _apps())
    elif key == "music":
        MainWindow.show_music(win, _music(), free=win.session.storage.free_space())
    elif key == "activities":
        MainWindow.show_activities(win, _activities())
    elif key == "courses":
        MainWindow.show_courses(win, _courses(empty=page == "courses-empty",
                                              uncounted=page == "courses-uncounted"))
    elif key == "status":
        MainWindow.show_status(win, _status_facts())
    win.sections[key].show("content")
    win._update_actions()
    win._update_banner()
    return key


def _waiting_gpx():
    """A file waiting for the device: a long track's name, a route and waypoints."""
    from pathlib import Path

    from openexpress import course_gpx

    data = gpx_file(gpx_track("Trójmiejski Park Krajobrazowy dookoła") + gpx_route()
                    + gpx_waypoint() + gpx_waypoint("Schronisko"))
    return course_gpx.parse(data.encode(), Path("Trojmiejski_Park_Krajobrazowy.gpx"))


def _courses(empty: bool, uncounted: bool = False):
    """Courses with long Polish names and both chips, one whose file says nothing, a file
    waiting and the saved locations (`uncounted`: they could not be counted); or none at
    all."""
    from openexpress import courses

    if empty:
        return courses.Overview("Garmin/NewFiles", [], [], 0)
    made = 1_761_000_000
    folder = "GARMIN/Courses"
    found = [
        courses.Course("Petla.fit", f"{folder}/Petla.fit", 5000,
                       "Pętla wokół Kamiennej Góry przez Przełęcz Kowarską", 154_800.0, 2_120,
                       made, courses.ORIGIN_OPENEXPRESS),
        courses.Course("Sopot.fit", f"{folder}/Sopot.fit", 5000,
                       "Sopot – Wejherowo przez Trójmiejski Park Krajobrazowy", 41_800.0, 310,
                       made, courses.ORIGIN_CONNECT),
        courses.Course("Bez_nazwy_kursu_zapisany_przez_urzadzenie.fit",
                       f"{folder}/Bez_nazwy_kursu_zapisany_przez_urzadzenie.fit", 5000, None,
                       None, None, None, None)]
    waiting = [courses.Waiting("Trojmiejski_Park_Krajobrazowy.GPX",
                               "GARMIN/NewFiles/Trojmiejski_Park_Krajobrazowy.GPX", 900,
                               _waiting_gpx())]
    if uncounted:
        return courses.Overview("Garmin/NewFiles", found, waiting, None, ("locations",))
    return courses.Overview("Garmin/NewFiles", found, waiting, 1234)


def _iterate(seconds):
    from gi.repository import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)


@pytest.fixture
def narrow_window(monkeypatch):
    """A function that gives the real window, folded (narrower than its breakpoint), in the
    language asked for; nothing is detected, loaded or asked. GTK's settings and the language
    are put back afterwards."""
    from gi.repository import Gtk

    from openexpress import i18n
    from openexpress.gui.app import MainWindow

    families = {f.get_name() for f in Gtk.Label().get_pango_context().get_font_map()
                .list_families()}
    if FONT.rsplit(" ", 1)[0] not in families:
        pytest.skip(f"the widths are measured in GNOME's font, {FONT}, not installed here")
    for name in ("_start", "_want_account", "_show_device_picture", "_check_updates_quietly",
                 "_check_maps_quietly", "_load_app_icons", "_fetch_account", "_on_mount_added",
                 "_on_mount_removed", "_remember_geometry"):
        monkeypatch.setattr(MainWindow, name, lambda self, *a, **kw: None)
    monkeypatch.setattr(MainWindow, "load_section", lambda self, key, force=False: None)
    monkeypatch.setattr(MainWindow, "_poll_usb", lambda self, clock=None: False)
    monkeypatch.setattr(MainWindow, "_background_enabled", lambda self: False)
    this_device = types.SimpleNamespace(unit_id=UNIT, is_this_device=True, name="")
    monkeypatch.setattr(MainWindow, "_show_primary_training", lambda self, row: row.set_subtitle(
        MainWindow._primary_training_text(this_device)))
    settings = Gtk.Settings.get_default()
    changed = {"gtk-font-name": FONT, "gtk-xft-dpi": 96 * 1024,
               "gtk-decoration-layout": DECORATION}
    windows = []

    def make(language):
        i18n.setup(language)
        for name, value in changed.items():
            settings.set_property(name, value)
        program_style()
        win = MainWindow(None)
        windows.append(win)
        win.set_default_size(400, 800)
        win.present()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not win.split.get_collapsed():
            _iterate(0.01)
        assert win.split.get_collapsed(), "the window did not fold"
        return win

    try:
        yield make
    finally:
        for win in windows:
            win.destroy()
        for name in changed:
            settings.reset_property(name)
        i18n.setup("en")


def _min_width(widget) -> int:
    from gi.repository import Gtk

    return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]


DEVICE_PAGES = ["device", "device-update-copied", "device-off-account", "device-drive",
                "device-refused-drive", "updates-beta",
                "updates-beta-drive",
                "updates-beta-copied", "updates-beta-copied-drive", "maps", "maps-none", "apps",
                "music", "activities", "courses", "courses-empty",
                "courses-uncounted", "status"]


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("page", DEVICE_PAGES)
def test_every_page_of_the_device_view_fits_the_narrowest_window(narrow_window, language,
                                                                  page):
    """The page with its title bar, the side panel folded: no wider than the window can get
    (wider, and libadwaita cuts it off). What has to wrap for it: "No maps are available for
    this device.", the line of the software version ("· update ready to install"), the beta
    line of the Updates page and its row of buttons."""
    win = narrow_window(language)
    key = _fill(win, page)
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section(key)
    _iterate(0.05)
    assert win.pages.get_visible_child_name() == key
    assert _min_width(win.device_view) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_question_before_a_course_is_copied_fits_the_narrowest_window(narrow_window,
                                                                         language):
    """The question's content as wide as the narrowest window leaves it, the name of each of
    its lines on one line (its value wraps instead): a long course's name, its file's, a route
    and its warning."""
    from pathlib import Path

    from gi.repository import Gtk

    from openexpress import course_gpx, courses
    from openexpress.gui import courses_page

    narrow_window(language)  # the language and GNOME's font
    gpx = course_gpx.parse(gpx_file(gpx_route("Pętla wokół Kamiennej Góry przez Przełęcz "
                                              "Kowarską")).encode(), Path("petla.gpx"))
    plan = courses.AddPlan(gpx, gpx.lines[0].name, gpx.data,
                           "Petla_wokol_Kamiennej_Gory_przez_Przelecz_Kowarska.gpx",
                           "Garmin/NewFiles/Petla.gpx", 13_300_000_000)
    width = courses_page.question_width(NARROWEST)
    body = courses_page.question_body(plan, {}, width)
    holder = Gtk.Window(child=body)  # never shown
    assert _min_width(body) == width
    lines = [w for w in descendants(body) if isinstance(w, Gtk.Box)
             and w.get_orientation() == Gtk.Orientation.HORIZONTAL
             and isinstance(w.get_first_child(), Gtk.Label)]
    assert len(lines) == 3 and not any(line.get_first_child().get_wrap() for line in lines)
    holder.destroy()


@pytest.mark.parametrize("language", ["en", "pl"])
def test_an_update_to_install_on_the_device_page_fits_the_narrowest_window(narrow_window,
                                                                          language):
    """The chip under the version and the name of the one update in the row "Device
    software": the page fits the narrowest window, the chip whole."""
    from gi.repository import Gtk

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "device")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("watch")
    _iterate(0.3)
    assert win.update_chip.get_visible()
    assert "Wi-Fi, Bluetooth and ANT+ connectivity firmware 11.20" in (
        win.update_row.get_subtitle())
    assert win.device_view.get_width() <= NARROWEST
    assert _min_width(win.device_view) <= NARROWEST
    natural = win.update_chip.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
    # The whole chip, its padding too (get_width() is the width inside the padding).
    ok, bounds = win.update_chip.compute_bounds(win)
    assert ok and bounds.get_width() >= natural


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_row_of_the_primary_wearable_fits_the_narrowest_window(narrow_window, language):
    """Its title is long in Polish ("Podstawowe urządzenie typu wearable"); it wraps."""
    from gi.repository import Adw

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "device")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("watch")
    _iterate(0.3)
    title = {"en": "Primary Wearable", "pl": "Podstawowe urządzenie typu wearable"}[language]
    assert title in [w.get_title() for w in descendants(win.device_view)
                     if isinstance(w, Adw.ActionRow)]
    assert _min_width(win.device_view) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_bar_with_stop_waiting_fits_the_narrowest_window(narrow_window, language):
    """The bar at the bottom while TrueUp waits, with Stop Waiting."""
    import threading

    from gi.repository import Gtk

    from openexpress.progress import TimedWait

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "device")
    win.session.stop_waiting = threading.Event()
    win.set_busy(True, "Sync", device=True)
    win._show_progress(TimedWait("Waiting 3 min 20 s for Garmin Connect to prepare TrueUp (the "
                                 "daily totals of all your devices)…", seconds=200, done=0.3))
    _iterate(0.1)
    assert win.stop_wait_button.get_visible()
    assert win.action_bar.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= NARROWEST


def _starts(label, anywhere=False) -> set[int]:
    """Where the lines of a label begin, but the first (offsets of its characters): as it is
    laid out, or `anywhere` it may be broken (its text laid out a pixel wide)."""
    from gi.repository import Pango

    layout = label.get_layout()
    if anywhere:
        layout = layout.copy()
        layout.set_width(1)
        layout.set_wrap(Pango.WrapMode.WORD)
    text, lines, starts = label.get_label().encode(), layout.get_iter(), set()
    while lines.next_line():
        starts.add(len(text[:lines.get_index()].decode()))
    return starts


HIKING, CYCLING = 17, 2  # FIT sports: a pace, a speed
LONG_DETAILS = 15  # the rows of details of _long_hike; a ride has no steps


def _long_hike(sport=HIKING):
    """A hike (or a ride) with all five tiles at their longest (two by two in a narrow window)
    and the chart of three series (its switch: the longest names), its row a copy in a backup
    with positions (the GPX button in its header bar)."""
    from datetime import datetime

    from openexpress import activities, fit

    start = int(datetime(2026, 12, 31, 23, 59).timestamp())
    samples = tuple(fit.Sample(start + s, distance=s * 0.2, speed=0.2, heart_rate=200,
                               altitude=4000 + s, latitude=51.5 + s * 1e-7,
                               longitude=-0.17 + s * 1e-7) for s in range(0, 359_999, 60))
    zones = fit.HeartRateZones(times=(359_999,) * 6, bounds=(219, 229, 239, 249, 254, 255),
                               method="threshold", max_heart_rate=255,
                               threshold_heart_rate=249)
    data = fit.ActivityData(fit.FileState.DAMAGED, start=start, sport=sport,
                            timer_time=359_999.0, distance=123_456.0, avg_speed=0.2,
                            avg_heart_rate=200, max_heart_rate=220, calories=9_999,
                            ascent=9_999, samples=samples, zones=zones, aerobic_effect=4.9,
                            anaerobic_effect=1.9, training_load=9_999.0,
                            elapsed_time=999_999.0, descent=9_999, min_altitude=-400.0,
                            max_altitude=8_848.0, max_speed=0.2, avg_cadence=255.0,
                            max_cadence=255.0, cycles=61_728.0, min_heart_rate=30,
                            avg_temperature=-40.0, min_temperature=-40.0,
                            max_temperature=-40.0, avg_respiration=60.0, min_respiration=10.0,
                            max_respiration=60.0, active_time=359_000.0, rest_time=999.0)
    read = activities.Read(
        activities.Activity(activities.Source.BACKUP, "/backup/hike.fit", "hike.fit", 1, 1.0,
                            datetime(2026, 12, 31, 23, 59)),
        fit.ActivitySummary(fit.FileState.DAMAGED, start=start, sport=sport, has_positions=True))
    return read, data


def _zone_bounds_break_between_words(zones) -> None:
    """Each zone's bounds on one line, or broken between words: never inside a word."""
    from gi.repository import Gtk

    for row in zones.rows:
        _name, span, _share = [w for w in descendants(row) if isinstance(w, Gtk.Label)]
        bounds = span.get_label()
        spaces = {i + 1 for i, c in enumerate(bounds) if c == " "}
        dashes = {i + 1 for i, c in enumerate(bounds) if c == "–"}
        assert _starts(span) <= spaces, bounds  # a range on one line
        # Pango breaks after a dash whatever it is told; nowhere else in a word.
        assert _starts(span, anywhere=True) <= spaces | dashes, bounds


@pytest.mark.parametrize("tiles", [False, True])  # the map before the yes, and after
@pytest.mark.parametrize("sport", [HIKING, CYCLING])
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_page_of_an_activity_fits_the_narrowest_window(narrow_window, monkeypatch, language,
                                                           sport, tiles):
    """Its tiles two by two, the damaged file said under them, the map with its note and
    button (or its attribution), the chart with its switch and the value at its cursor, its
    kilometres, its heart rate zones and training effect, its details open (their longest
    values), its sport and start in the header bar with the GPX button."""
    from fakes import RunNow
    from gi.repository import Adw, Gtk

    from openexpress.gui import activity_map, activity_page
    from openexpress.gui.activity_chart import ActivityChart
    from openexpress.gui.activity_details import DetailsList
    from openexpress.gui.activity_effect import EffectList
    from openexpress.gui.activity_laps import LapList
    from openexpress.gui.activity_zones import ZoneList
    from openexpress.gui.words import activity_start as read_start

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "activities")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("activities")
    read, data = _long_hike(sport)
    monkeypatch.setattr(activity_page, "_data", lambda found, activity: data)
    monkeypatch.setattr(activity_map, "_fetch",
                        lambda url, etag: activity_map.Fetched(404, None, None, 0))
    activity_map.set_tiles(tiles)
    win.run_task = RunNow()
    activity_page.open_activity(win, read)
    _iterate(0.3)
    page = win.navigation.get_visible_page()
    assert page.get_tag() == "activity"
    shown = [w.get_label() for w in descendants(page) if isinstance(w, Gtk.Label)]
    assert {"99:59:59", "9999 m"} <= set(shown) and len(
        [w for w in descendants(page) if isinstance(w, Gtk.FlowBoxChild)
         and w.get_child().has_css_class("card")]) == 5
    assert len([w for w in descendants(page) if isinstance(w, Gtk.ToggleButton)]) == 3
    (chart,) = [w for w in descendants(page) if isinstance(w, ActivityChart)]
    chart.area.place(180_000)
    assert chart.value_label.get_label()
    _iterate(0.1)
    value = chart.value_label.compute_bounds(chart)[1]
    switch = next(w for w in descendants(chart) if isinstance(w, Gtk.ToggleButton)).compute_bounds(
        chart)[1]
    assert value.get_y() >= switch.get_y() + switch.get_height()  # under the switch, narrow
    (shown,) = [w for w in descendants(page) if isinstance(w, activity_map.ActivityMap)]
    assert shown.legend.get_visible() and shown.legend.scale.get_visible()  # the track's colours
    assert shown.attribution.get_visible() is tiles
    assert shown.note.get_visible() is not tiles
    assert [w for w in descendants(page) if isinstance(w, LapList)]  # its kilometres: no laps kept
    (zones,) = [w for w in descendants(page) if isinstance(w, ZoneList)]  # the longest words
    _zone_bounds_break_between_words(zones)
    assert [w for w in descendants(page) if isinstance(w, EffectList)]  # the scale's longest words
    (details,) = [w for w in descendants(page) if isinstance(w, DetailsList)]  # open, every row
    details.expander.set_expanded(True)
    assert len(details.rows) == LONG_DETAILS - (sport == CYCLING)
    _iterate(0.2)
    (dot,) = [w for w in descendants(page) if w.has_css_class("oe-sport-dot")]
    assert not dot.get_visible()  # not in a narrow window: the start under the title stays whole
    (start,) = [w for w in descendants(page) if isinstance(w, Gtk.Label) and w.get_mapped()
                and w.get_ancestor(Adw.WindowTitle) and w.get_label() == read_start(read)]
    assert not start.get_layout().is_ellipsized()
    assert _min_width(page) <= NARROWEST
    assert page.get_width() <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_page_device_status_opened_fits_the_narrowest_window(narrow_window, language):
    """The page with its newest crash and its details open, the kinds of file listed, Show All
    as the last row of the crashes and updates."""
    from gi.repository import Adw

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "status")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("status")
    expanders = [w for w in descendants(win.sections["status"].stack)
                 if isinstance(w, Adw.ExpanderRow)]
    for row in expanders:
        row.set_expanded(True)
    _iterate(0.3)
    assert win.status_history.get_header_suffix() is None
    assert win.status_history_rows[-1].get_title() == {"en": "Show All",
                                                       "pl": "Pokaż wszystko"}[language]
    assert _min_width(win.device_view) <= NARROWEST


def _lines_of(legend) -> list:
    """The lines of the legend of the storage, one kind each."""
    lines, child = [], legend.get_first_child()
    while child is not None:
        lines.append(child)
        child = child.get_next_sibling()
    return lines


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_names_of_the_legend_wrap_only_in_a_narrow_window(narrow_window, language):
    """In a wide window no kind's name wraps (the longest ones: "Updates and Satellite Data",
    "Aktualizacje i dane satelitarne"), its lines all as tall; in the narrowest one a name may."""
    from gi.repository import Gtk

    from openexpress.gui.status_page import kind_names

    win = narrow_window(language)
    win.set_default_size(1000, 800)
    _fill(win, "status")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("status")
    _iterate(0.3)
    names = set(kind_names().values())
    legend = next(w for w in descendants(win.sections["status"].stack)
                  if w.has_css_class("oe-status-legend"))
    shown = [w for w in descendants(legend) if isinstance(w, Gtk.Label) and w.get_label() in names]
    assert shown and all(w.get_layout().get_line_count() == 1 for w in shown)
    heights = {line.get_height() for line in _lines_of(legend)}
    assert len(heights) == 1


def test_a_name_of_the_legend_that_wraps_makes_only_its_line_taller(narrow_window):
    """The narrowest window in Polish, the updates as large as a bike computer's: "Aktualizacje
    i dane satelitarne" wraps; its line alone is taller, the others keep the height of one,
    and no room is left under the last one (Free)."""
    from dataclasses import replace

    from gi.repository import Gtk

    from openexpress.gui.app import MainWindow
    from openexpress.gui.status_page import kind_names

    win = narrow_window("pl")
    win.set_default_size(NARROWEST, 800)
    _fill(win, "status")
    made = _status_facts()
    win.sections["status"].clear()
    MainWindow.show_status(win, replace(made, categories={**made.categories,
                                                          "updates": 682_800_000}))
    win.sections["status"].show("content")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("status")
    _iterate(0.3)
    names = set(kind_names().values())
    legend = next(w for w in descendants(win.sections["status"].stack)
                  if w.has_css_class("oe-status-legend"))
    lines = _lines_of(legend)

    def name(line):
        return next(w for w in descendants(line)
                    if isinstance(w, Gtk.Label) and w.get_label() in names)

    wrapped = [line for line in lines if name(line).get_layout().get_line_count() > 1]
    assert [name(line).get_label() for line in wrapped] == ["Aktualizacje i\u00a0dane satelitarne"]
    one_line = {line.get_height() for line in lines if line not in wrapped}
    assert len(one_line) == 1 and wrapped[0].get_height() > max(one_line)
    _found, last = lines[-1].compute_bounds(legend)
    assert legend.get_height() - (last.get_y() + last.get_height()) < 1


@pytest.mark.parametrize("language", ["en", "pl", "de"])
def test_the_copy_buttons_leave_their_subtitles_room(narrow_window, language):
    """In the narrowest window the buttons of Crash Logs and Memory Dumps say only "Copy" (their
    full names for screen readers and in their tooltips): their subtitles keep the room of a
    few words a line, not one or two."""
    from gi.repository import Adw, Gtk

    from openexpress.gui.window_tasks import row_subtitle
    from openexpress.i18n import _

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "status")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("status")
    _iterate(0.3)
    rows = [w for w in descendants(win.sections["status"].stack)
            if isinstance(w, Adw.ActionRow) and w.get_title() in (_("Crash Logs"),
                                                                  _("Memory Dumps"))]
    assert len(rows) == 2
    for row in rows:
        assert row_subtitle(row).get_width() >= 160, (row.get_title(),
                                                      row_subtitle(row).get_width())
        (button,) = [w for w in descendants(row) if isinstance(w, Gtk.Button)]
        assert button.get_tooltip_text() and button.get_tooltip_text() != button.get_label()


@pytest.mark.parametrize("why", ["logged-out", "failed"])
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_sentence_about_the_account_fits_the_narrowest_window(narrow_window, language,
                                                                  why):
    """The sentence under the tiles wraps."""
    from openexpress.gui.start_window import show_account_note

    win = narrow_window(language)
    win.start.show(_tiles(), False)
    win._account_why = why
    show_account_note(win, _tiles())
    _iterate(0.05)
    assert win.start.account_note.get_visible()
    assert _min_width(win.start.page) <= NARROWEST


@pytest.mark.parametrize("view", ["grid", "list"])
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_start_page_in_the_view_chosen_fits_the_narrowest_window(narrow_window, language,
                                                                     view):
    """Nine connected (a list when automatic), three remembered, three on the account,
    all in the view chosen: the page fits the narrowest window."""
    win = narrow_window(language)
    win.start.view = view
    win.start.show(_tiles() * 3, False)
    _iterate(0.05)
    kinds = {type(item).__name__ for item in win.start.tiles()}
    assert kinds == ({"DeviceRow"} if view == "list" else {"FlowBoxChild"})
    assert _min_width(win.start.page) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_menu_of_the_start_page_fits_the_narrowest_window(narrow_window, language):
    """Open, with the row of the view's buttons on top: no wider than the window, the three
    buttons side by side."""
    from gi.repository import Gtk

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    button = next(w for w in descendants(win.start.header) if isinstance(w, Gtk.MenuButton))
    popover = button.get_popover()
    button.popup()
    _iterate(0.1)
    try:
        assert popover.measure(Gtk.Orientation.HORIZONTAL, -1)[1] <= NARROWEST
        buttons = [w for w in descendants(popover) if isinstance(w, Gtk.ToggleButton)]
        assert len(buttons) == 3
        assert len({b.compute_bounds(popover)[1].get_y() for b in buttons}) == 1
    finally:
        button.popdown()
        _iterate(0.05)


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_start_page_fits_the_narrowest_window(narrow_window, language):
    win = narrow_window(language)
    win.start.show(_tiles(), False)
    _iterate(0.05)
    assert _min_width(win.start.page) <= NARROWEST


def _texts(widget):
    """The texts of all labels under `widget` (the rows of an expander too, folded or not)."""
    from gi.repository import Gtk

    if isinstance(widget, Gtk.Label) and widget.get_text():
        yield widget.get_text()
    child = widget.get_first_child()
    while child is not None:
        yield from _texts(child)
        child = child.get_next_sibling()


SIZE_WITH_A_SPACE = re.compile(r"\d (B|kB|MB|GB)\b")


def _breaks_badly(texts) -> list[str]:
    """The texts that may break where they should not: a size between its number and its
    unit, a separator "·" before it (a plain space before the separator)."""
    return [t for t in texts if SIZE_WITH_A_SPACE.search(t) or " ·" in t]


@pytest.mark.parametrize("language", ["en", "pl", "de"])
def test_the_view_of_a_remembered_device_fits_the_narrowest_window(narrow_window, language):
    """The whole view: its title bar (Sync as an icon, as on the device view: its words would
    make it 375 px wide in Polish, 398 px in German) and its content, with the button "Forget
    Device" at the bottom."""
    from openexpress import known_devices

    known = known_devices.remember(UNIT, "Edge 1050", "006-B4440-00", "33.13", "mtp", now=1.0)
    win = narrow_window(language)
    win._fill_remembered(known)
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.remembered.page)
    _iterate(0.05)
    assert win.remembered.forget_group.get_visible()
    assert _min_width(win.remembered.page.get_child()) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("page", ["start", *DEVICE_PAGES])
def test_captions_break_only_where_they_should(narrow_window, language, page):
    """The window at 360 px: no "4,7 / GB", and no separator at the start of a line.
    A size keeps its number and unit together, and "·" stays with the part before it (a hard
    space before it, a plain one after)."""
    win = narrow_window(language)
    if page == "start":
        win.start.show(_tiles(), False)
        root = win.start.page
    else:
        key = _fill(win, page)
        win.navigation.set_animate_transitions(False)
        win.navigation.push(win.device_view)
        win.select_section(key)
        root = win.device_view
    texts = list(_texts(root))
    # The pages without a caption of several parts.
    assert any("\u00a0·" in t for t in texts) or page in (
        "maps-none", "music", "courses-empty", "device-off-account", "device-refused-drive")
    assert _breaks_badly(texts) == []


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_line_of_the_last_sync_keeps_its_separators_too(language):
    """When the last sync was and its numbers, joined in the command line's words (service):
    in the window "·" stays with the part before it."""
    from openexpress import i18n
    from openexpress.gui.device_page import last_sync_words

    i18n.setup(language)
    try:
        line = last_sync_words({"time": time.time() - 3 * 86400, "uploaded": 6,
                                    "downloaded": 7, "new_files": {"FIT_TYPE_4": 8}})
    finally:
        i18n.setup("en")
    assert line.count("\u00a0· ") == 2 and _breaks_badly([line]) == []


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("page", [p for p in DEVICE_PAGES if p.startswith("updates")])
def test_with_an_older_libadwaita_the_updates_page_fits_too(narrow_window,
                                                            language, page, in_the_window):
    """Without Adw.WrapBox (libadwaita before 1.7) a FlowBox lets the beta line and the row of
    buttons go on to the next line."""

    in_the_window("WRAP_BOX", None)
    win = narrow_window(language)
    _fill(win, page)
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("updates")
    _iterate(0.05)
    assert _min_width(win.device_view) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_preferences_with_the_storage_group_fit_the_narrowest_window(narrow_window,
                                                                         monkeypatch, language):
    """The preferences of the real window, the parts of the cache counted with sizes of all
    kinds (gigabytes of maps, an empty part): no wider than the narrowest window."""
    from gi.repository import Adw

    from openexpress import cache

    sizes = {"maps": 6_657_384_448, "updates": 98_298_723, "pictures": 306_702, "temp": 0}
    files = {"maps": 128, "updates": 2, "pictures": 7, "temp": 0}
    monkeypatch.setattr(cache, "usage", lambda only=None: cache.CacheUsage(
        cache.paths.cache_dir(), dict(sizes), dict(files)))
    win = narrow_window(language)
    win.show_preferences()
    deadline = time.monotonic() + 5
    while win._storage.usage is None and time.monotonic() < deadline:
        _iterate(0.01)
    _iterate(0.05)
    dialog = win._preferences
    assert isinstance(dialog, Adw.PreferencesDialog)
    # The dialog itself measures nothing (it lays out a sheet): its page is what must fit.
    everything, _button = win._storage.everything
    (page,) = [w for w in descendants(dialog) if isinstance(w, Adw.PreferencesPage)
               and everything.is_ancestor(w)]
    texts = list(_texts(page))
    assert any(t.startswith(("6.7\u00a0GB", "6,7\u00a0GB")) for t in texts)
    assert ("Clear All" if language == "en" else "Wyczyść wszystko") in texts
    assert _min_width(page) <= NARROWEST
    dialog.close()


def _tiles_with_chips():
    """Tiles with the longest pairs of chips: an update with a device off the account, with a
    drive, with activities to sync, with a device never synced, with the primary device and
    with a device synced; the primary device with a device synced; an update copied with a
    device never synced, and with a drive."""
    from openexpress.devicelist import Tile

    found = time.time()

    def tile(name, kind, on_account=True, primary=False, update=True, ready=False, **sync):
        return Tile(name, "006-B4440-00", "33.13", kind, True, on_account, primary, None,
                    "006-B4440-00", None, None, update_found=found if update else None,
                    update_ready=found if ready else None, account_known=True, **sync)

    return [(tile("Edge 1050", "mtp", on_account=False), "Software 33.13"),
            (tile("Varia RTL515", "ums"), "Software 3.80"),
            (tile("Edge 1050", "mtp", to_sync=12, synced_at=found), "Software 33.13"),
            (tile("Edge 1050", "mtp", never_synced=True), "Software 33.13"),
            (tile("fēnix 8 - 47mm, Solar", "mtp", primary=True), "Software 23.39"),
            (tile("Edge 1050", "mtp", to_sync=0, synced_at=found), "Software 33.13"),
            (tile("fēnix 8 - 47mm, Solar", "mtp", primary=True, update=False, to_sync=0,
                  synced_at=found), "Software 23.39"),
            (tile("Edge 1050", "mtp", ready=True, never_synced=True), "Software 33.13"),
            (tile("Varia RTL515", "ums", ready=True), "Software 3.80")]


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_longest_chips_fit_the_narrowest_window_whole(narrow_window, language):
    """Two chips on a tile, the longest pairs in either language: the page fits the
    narrowest window, and in it no chip is cut short (they may go on two lines)."""
    from gi.repository import Gtk

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    win.start.show(_tiles_with_chips(), False)
    _iterate(0.3)
    assert win.start.page.get_width() <= NARROWEST
    assert _min_width(win.start.page) <= NARROWEST
    chips = []
    stack = [win.start.page]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label) and widget.has_css_class("oe-chip"):
            chips.append(widget)
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    assert len(chips) == 18
    assert [c.get_label() for c in chips if c.get_layout().is_ellipsized()] == []


@pytest.mark.parametrize("wrap_box", [True, False])
@pytest.mark.parametrize("language", ["en", "pl"])
def test_a_list_with_the_longest_chips_fits_the_narrowest_window(narrow_window, monkeypatch,
                                                                 language, wrap_box):
    """Nine devices connected (a list) with the longest pairs of chips: the page fits the
    narrowest window and every chip stays inside its row; with Adw.WrapBox (libadwaita 1.7)
    no chip is cut short, before it they may get shorter."""
    from openexpress.gui import start

    if not wrap_box:
        monkeypatch.setattr(start, "Adw", _WithoutWrapBox())
    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    win.start.show(_tiles_with_chips(), False)
    _iterate(0.3)
    rows = win.start.tiles()
    connected_section = start_section(win.start, "connected")
    assert len(rows) == 9 and all(row.get_parent() is connected_section.list for row in rows)
    assert win.start.page.get_width() <= NARROWEST
    assert _min_width(win.start.page) <= NARROWEST
    chips = [(row, chip) for row in rows for chip in _chip_labels(row)]
    assert len(chips) == 18
    for row, chip in chips:
        ok, bounds = chip.compute_bounds(row)
        assert ok and bounds.get_x() >= 0 and bounds.get_y() >= 0
        assert bounds.get_x() + bounds.get_width() <= row.get_width() + 0.5
        assert bounds.get_y() + bounds.get_height() <= row.get_height() + 0.5
    if wrap_box:
        assert [c.get_label() for _r, c in chips if c.get_layout().is_ellipsized()] == []


# The longest pair of chips in either language: 271 px on one line in one measurement,
# 309 px in GNOME's font; more than a tile has at 700 and 1000 px. Its update is one copied
# ("Unplug to install"), not one found.
LONGEST_PAIR = ("Odłącz, aby zainstalować", "Nie zsynchronizowano")


def _tiles_per_line(page) -> int:
    from gi.repository import Graphene

    tops = [round(tile.compute_point(page.page, Graphene.Point())[1].y)
            for tile in page.tiles()]
    return tops.count(tops[0])


def _chip_labels(widget) -> list:
    from gi.repository import Gtk

    found = []
    for child in descendants(widget):
        if isinstance(child, Gtk.Label) and child.has_css_class("oe-chip") and child.get_label():
            found.append(child)
    return found


class _WithoutWrapBox:
    """libadwaita before 1.7, for the start page: everything but Adw.WrapBox."""

    def __getattr__(self, name):
        from gi.repository import Adw

        if name == "WrapBox":
            raise AttributeError(name)
        return getattr(Adw, name)


@pytest.mark.parametrize("wrap_box", [True, False])
@pytest.mark.parametrize("width", [700, 1000])
def test_chips_never_change_how_many_tiles_share_a_line(narrow_window, monkeypatch, width,
                                                        wrap_box):
    """A tile asks for no more width than its chips need at least: with the longest pair (an
    update copied, a device never synced) as many tiles share a line as without chips, and
    the chips go on two lines, whole (libadwaita 1.7: Adw.WrapBox; before it, on one line,
    shorter)."""
    from openexpress.devicelist import Tile
    from openexpress.gui import start

    if not wrap_box:
        monkeypatch.setattr(start, "Adw", _WithoutWrapBox())
    win = narrow_window("pl")
    win.set_default_size(width, 800)
    per_line, widths = [], []
    for chips in ({}, {"update_ready": time.time(), "never_synced": True}):
        entries = [(Tile(name, "006-B4532-00", "23.39", "mtp", True, True, False, None,
                         "006-B4532-00", None, None, account_known=True, **chips),
                    "Wersja oprogramowania 23.39")
                   for name in ("fēnix 8 - 47mm, Solar", "Edge 1050", "Venu 2")]
        win.start.show(entries, False)
        _iterate(0.5)
        per_line.append(_tiles_per_line(win.start))
        widths.append(win.start.tiles()[0].get_width())
    chips = _chip_labels(win.start.tiles()[0])
    assert [c.get_label() for c in chips] == list(LONGEST_PAIR)
    room = widths[0] - 24  # a tile without chips, but its margins
    one_line = sum(c.measure(start.Gtk.Orientation.HORIZONTAL, -1)[1] for c in chips) + 6
    assert one_line > room  # the pair does not fit on one line of the tile
    # Asking for the pair's whole width, two tiles would share a line of 1000 px, one of 700 px.
    assert per_line[1] == per_line[0] and per_line[0] == (2 if width == 700 else 3)
    if wrap_box:
        assert [c.get_label() for c in chips if c.get_layout().is_ellipsized()] == []


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_wait_for_trueup_is_whole_in_the_narrowest_window(narrow_window, language):
    """The words of TrueUp's wait on the bar at the bottom, beside Stop Waiting, at the
    narrowest width: never cut short, also for the longest wait (4 min 59 s); why it waits
    is in the tooltip."""
    import threading

    from openexpress import trueup

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "device")
    # The device's view above the start page, folded: its page, not the side panel.
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.split.set_show_content(True)
    win.session.stop_waiting = threading.Event()
    win.set_busy(True, "Sync", device=True)
    seen = []
    trueup._wait(299, seen.append, Waits(), trueup.TrueUpResult())
    wait = seen[0]
    win._show_progress(wait)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (win.get_width() > NARROWEST
                                           or not win.stop_wait_button.get_width()):
        _iterate(0.01)
    _iterate(0.1)
    assert 0 < win.get_width() <= NARROWEST  # its frame aside, no wider than the narrowest
    label = win.progress_label
    assert win.stop_wait_button.get_visible() and label.get_text() == wait.short
    assert "4 min 59 s" in label.get_text()
    assert label.get_width() > 0 and not label.get_layout().is_ellipsized(), label.get_text()
    assert label.get_tooltip_text() == wait.about


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_banner_of_devices_that_stopped_responding_fits_the_narrowest_window(narrow_window,
                                                                                language):
    """Two devices named, the longest words: the banner on the start page and on the device
    view wraps, the pages no wider than the window."""
    from openexpress.device.gio_storage import GarminUsb
    from openexpress.gui import stalled

    win = narrow_window(language)
    win._stalled = [stalled.Stalled(GarminUsb("51b4", 3, 14), "fēnix 8 Pro – 47 mm, AMOLED"),
                    stalled.Stalled(GarminUsb("4e86", 3, 20), "Edge 1050")]
    stalled.show(win)
    _iterate(0.05)
    assert win.start.banner.get_revealed()
    assert _min_width(win.start.page) <= NARROWEST
    _fill(win, "device")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    _iterate(0.05)
    assert win.banner.get_revealed() and "Edge 1050" in win.banner.get_title()
    assert _min_width(win.device_view) <= NARROWEST


@pytest.mark.needs_display
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_notice_of_a_device_not_supported_fits_the_narrowest_window(narrow_window,
                                                                       monkeypatch, tmp_path,
                                                                       language):
    """On top of the start page and on a page of the device view (no device open), folded."""
    from fakes import garmin_on_usb

    from openexpress.device import gio_storage
    from openexpress.gui.app import MainWindow

    monkeypatch.setattr(gio_storage, "SYSFS_USB", tmp_path / "usb")
    garmin_on_usb(tmp_path / "usb", "1-4", "0b89", ("03",), product="Garmin USB inReach")
    from openexpress.gui.detection import notice_icon, start_notice

    win = narrow_window(language)
    words = MainWindow._usb_notice(gio_storage.UNSUPPORTED, 0)
    assert words[1].startswith("openExpress")
    icon = notice_icon(gio_storage.UNSUPPORTED)
    win.start.set_empty(*start_notice(gio_storage.UNSUPPORTED, words), icon=icon)
    win.start.show(_tiles(), True)
    for section in win.sections.values():
        section.set_empty(*words, icon=icon)
    _iterate(0.05)
    assert _min_width(win.start.page) <= NARROWEST
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    _iterate(0.05)
    assert _min_width(win.device_view) <= NARROWEST


@pytest.mark.needs_display
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_report_dialog_fits_the_narrowest_window(narrow_window, device_root, language):
    """Its preview of the report as wide as the window leaves a dialog's content (at most
    420 px): the dialog is no wider than the narrowest window."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.gui.window_tasks import DIALOG_MARGINS

    storage = LocalStorage(device_root)
    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    win.session = device_session(device=parse(storage.read("GARMIN/GarminDevice.xml")),
                                 storage=storage, logged_in=False, space=(None, None))
    _iterate(0.3)
    win.show_report()
    _iterate(0.3)
    dialog = win.get_visible_dialog()
    assert _min_width(dialog.get_extra_child()) <= NARROWEST - DIALOG_MARGINS
    dialog.force_close()


# --- the page from the records -----------------------------------------------------------------


pytestmark = [pytest.mark.needs_display,
              pytest.mark.filterwarnings("ignore:'asyncio.*:DeprecationWarning")]


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_page_from_the_records_fits_the_narrowest_window(narrow_window, language):
    from openexpress import status
    from openexpress.crashes import Crash
    from openexpress.gui import status_page

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    now = datetime.now()
    crashes = [Crash(now - timedelta(days=2), software="21.18", error="0x003",
                     program_counter="0x10000abc", screen="2301", sources={"device log"})]
    facts = status.Facts(
        free=12_400_000_000, capacity=31_100_000_000,
        categories={"maps": 17_200_000_000, "music": 410_000_000, "logs": 61_000_000,
                    "updates": 9_100_000, "courses": 305_000, "system": 930_000_000},
        counted_at=now - timedelta(days=3), crashes=crashes, errors_known=True, dumps=None,
        events=status.events(crashes, [], None), hidden_logs=False,
        copies=(12, 3_100_000, now - timedelta(hours=3)), connected=False,
        dumps_at_last=(6, 54_000_000), recounted=True)
    win.kept_status = status_page.KeptStatus(3000000001, facts)
    win.navigation.set_animate_transitions(False)
    status_page.show_kept_status(win)
    _iterate(0.3)
    width = _min_width(win.kept_status.page)
    assert width <= NARROWEST, width
