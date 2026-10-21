"""The window as narrow as it gets (MIN_WINDOW: 360 px), with the side panel folded: every page
of the device view and the start page fit in it, in English and in Polish, which is longer.

The real window is built (never shown on the desktop: the tests have their own hidden display)
and filled with stand-ins: an Edge 1050 with a beta update, long names of components, a few
maps, apps and music. Nothing asks a device or a server: detection, loading and the requests
in the background are replaced by nothing. The width is measured in GNOME's font."""

import time
import types
import uuid

import pytest

NARROWEST = 360  # gui.app.MIN_WINDOW
FONT = "Adwaita Sans 11"
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
    return types.SimpleNamespace(device=device, storage=storage, client=client, logged_in=True,
                                 ownership_error=None, account_devices=[], mass_storage=drive)


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
                        False, False)]
    return state, account, [types.SimpleNamespace(app_id=weather.app_id, version_name="4.2.0")]


def _music():
    from openexpress.music import DeviceMedia

    return ([DeviceMedia("Music/Wykonawca z długą nazwą/Album/Utwór o długim tytule.mp3",
                         8_100_000, "track"),
             DeviceMedia("Podcasts/Podcast/Odcinek 12.mp3", 40_000_000, "podcast")],
            ["Music/Ulubione na trening biegowy.m3u8"])


def _tiles():
    from openexpress.devicelist import Tile

    def tile(name, part, connected, last_seen=None):
        return Tile(name, part, "23.39", "mtp" if connected else None, connected, False, False,
                    last_seen, part, None, None)

    return [(tile("fēnix 8 - 47mm, Solar", "006-B4532-00", True), "Over USB (MTP)"),
            (tile("Edge 1050", "006-B4440-00", True), "Over USB (MTP)"),
            (tile("Varia RTL515", "006-B3592-03", True), "USB drive"),
            (tile("Venu 2", "006-B3703-00", False, time.time() - 86400),
             "Software 19.05 · Last seen yesterday, 23:41")]


EDGE = ("Edge 1050", "33.11", "006-B4440-00")
FENIX = ("fēnix 8 - 47mm, Solar", "23.39", "006-B4532-00")
INREACH = ("inReach Mini 2", "5.05", "006-B3968-00")


def _fill(win, page: str) -> str:
    """Builds `page` of the device view as the window does; returns its section."""
    from openexpress import service
    from openexpress.gui.app import MainWindow

    drive = page.endswith("-drive")
    model = INREACH if drive else EDGE if page.startswith(("device", "updates")) else FENIX
    win.session = _session(_device(*model), drive=drive)
    win.update_state = "ready" if page == "device-update-copied" else "available"
    win.update_count = 1
    win.map_state, win.map_update_count = "available", 1
    service.record_sync(UNIT, uploaded=8, downloaded=4, skipped_known=3, errors=[], lines=[])
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
        MainWindow.show_maps(win, *_maps())
    elif key == "apps":
        MainWindow.show_apps(win, _apps())
    elif key == "music":
        MainWindow.show_music(win, _music())
    win.sections[key].show("content")
    win._update_actions()
    win._update_banner()
    return key


def _iterate(seconds):
    from openexpress.gui.app import GLib

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)


@pytest.fixture
def narrow_window(monkeypatch):
    """A function that gives the real window, folded (narrower than its breakpoint), in the
    language asked for; nothing is detected, loaded or asked. GTK's settings and the language
    are put back afterwards."""
    from openexpress import i18n
    from openexpress.gui.app import Gtk, MainWindow

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
    from openexpress.gui.app import Gtk

    return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]


DEVICE_PAGES = ["device", "device-update-copied", "updates-beta", "updates-beta-drive",
                "updates-beta-copied", "updates-beta-copied-drive", "maps", "maps-none", "apps",
                "music"]


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("page", DEVICE_PAGES)
def test_every_page_of_the_device_view_fits_the_narrowest_window(narrow_window, language,
                                                                  page):
    """The page with its title bar, the side panel folded: no wider than the window can get
    (wider, and libadwaita cuts it off). Measured 21.10.2026: the line of the software version
    ("· update ready to install"), the beta line of the Updates page and its row of buttons,
    and "No maps are available for this device." did not wrap."""
    win = narrow_window(language)
    key = _fill(win, page)
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section(key)
    _iterate(0.05)
    assert win.pages.get_visible_child_name() == key
    assert _min_width(win.device_view) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_start_page_fits_the_narrowest_window(narrow_window, language):
    win = narrow_window(language)
    win.start.show(_tiles(), False)
    _iterate(0.05)
    assert _min_width(win.start.page) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("page", [p for p in DEVICE_PAGES if p.startswith("updates")])
def test_with_an_older_libadwaita_the_updates_page_fits_too(narrow_window, monkeypatch,
                                                            language, page):
    """Without Adw.WrapBox (libadwaita before 1.7) a FlowBox lets the beta line and the row of
    buttons go on to the next line."""
    from openexpress.gui import app

    monkeypatch.setattr(app, "WRAP_BOX", None)
    win = narrow_window(language)
    _fill(win, page)
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("updates")
    _iterate(0.05)
    assert _min_width(win.device_view) <= NARROWEST
