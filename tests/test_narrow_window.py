"""The window as narrow as it gets (MIN_WINDOW: 360 px), with the side panel folded: every page
of the device view and the start page fit in it, in English and in Polish, which is longer.

The real window is built (never shown on the desktop: the tests have their own hidden display)
and filled with stand-ins: an Edge 1050 with a beta update, long names of components, a few
maps, apps and music. Nothing asks a device or a server: detection, loading and the requests
in the background are replaced by nothing. The width is measured in GNOME's font."""

import re
import time
import types
import uuid

import pytest
from fakes import record_sync

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
    # On the account, its primary wearable: the row "Primary wearable" is measured too
    # (analysis 14, stage 4; in Polish its title is long).
    account = [{"unitid": device.unit_id, "primaryactivitytrackersettingcapable": True,
                "primaryactivitytrackerindicator": True}]
    return types.SimpleNamespace(device=device, storage=storage, client=client, logged_in=True,
                                 ownership_error=None, account_devices=account,
                                 mass_storage=drive, diagnostics=_device_errors())


def _device_errors():
    """A crash the device logged lately, its hidden crash logs and five memory dumps: the row
    "Device error log" with both buttons (analysis 20)."""
    from datetime import datetime

    from openexpress import diagnostics

    crash = diagnostics.ErrorEntry("RTL Trap", datetime(2026, 10, 20, 22, 24, 6), "0x003",
                                   "0x10000abc", "2301", "err_log.bak")
    return diagnostics.AtOpen([crash], crash, [("Garmin/Debug/dump/RAM.zip", 8_090_553)] * 5,
                              True)


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
    # The update asks for new permissions: the longest line of an app (point 5).
    return state, account, [types.SimpleNamespace(app_id=weather.app_id, version_name="4.2.0",
                                                  permissions_changed=True)]


def _music():
    from openexpress.music import DeviceMedia

    return ([DeviceMedia("Music/Wykonawca z długą nazwą/Album/Utwór o długim tytule.mp3",
                         8_100_000, "track"),
             DeviceMedia("Podcasts/Podcast/Odcinek 12.mp3", 40_000_000, "podcast")],
            ["Music/Ulubione na trening biegowy.m3u8"])


def _tiles():
    """Tiles of devices connected, remembered and only on the account, with the words under
    their names as the window makes them."""
    from openexpress.devicelist import Tile
    from openexpress.gui import app
    from openexpress.known_devices import KnownDevice

    def tile(name, part, connected, last_seen=None, on_account=False):
        return Tile(name, part, "23.39", "mtp" if connected else None, connected, on_account,
                    False, last_seen, part, None, None)

    def connected(kind):
        return app.choice_subtitle(types.SimpleNamespace(
            found=types.SimpleNamespace(kind=kind), ok=True, software_version="23.39"))

    seen = time.time() - 86400
    venu = KnownDevice(UNIT, "Venu 2", "006-B3703-00", "19.05", "mtp", seen, seen)
    return [(tile("fēnix 8 - 47mm, Solar", "006-B4532-00", True), connected("mtp")),
            (tile("Edge 1050", "006-B4440-00", True), connected("mtp")),
            (tile("Varia RTL515", "006-B3592-03", True), connected("ums")),
            (tile("Venu 2", "006-B3703-00", False, seen), app.remembered_subtitle(venu)),
            (tile("epix (Gen 2)", "006-B3943-00", False, on_account=True),
             app.account_subtitle({"software_version": "27.18"}))]


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
    win.update_state = "ready" if page == "device-update-copied" else "available"
    win.update_count = 1
    # The one update to install: the longest name of a component ("Device software").
    win.update_only = _updates(copied=False)[2]
    win.map_state, win.map_update_count = "available", 1
    sync.SyncState(UNIT).save()  # synced here before (analysis 14, a3)
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
        _program_style()
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


_styled: set = set()  # the displays that have the program's own style


def _program_style() -> None:
    """The program's own style (start.CSS: the chips, the tiles), as the application loads it
    before its window (App): measured without it, a chip had no padding (26.10.2026)."""
    from gi.repository import Gdk

    from openexpress.gui import start

    display = Gdk.Display.get_default()
    if display not in _styled:
        _styled.add(display)
        start.load_css(display)


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
def test_an_update_to_install_on_the_device_page_fits_the_narrowest_window(narrow_window,
                                                                          language):
    """The chip under the version and the name of the one update in the row "Device
    software" (24.10.2026): the page fits the narrowest window, the chip whole."""
    from openexpress.gui.app import Gtk

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
    """Analysis 14, stage 4: its title is long in Polish ("Podstawowe urządzenie typu
    wearable"); it wraps."""
    from openexpress.gui.app import Adw

    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "device")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("watch")
    _iterate(0.3)
    title = {"en": "Primary wearable", "pl": "Podstawowe urządzenie typu wearable"}[language]
    assert title in [w.get_title() for w in _descendants(win.device_view)
                     if isinstance(w, Adw.ActionRow)]
    assert _min_width(win.device_view) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_device_error_log_fits_the_narrowest_window(narrow_window, language):
    """Analysis 20: the row under "Backup", open, with its two buttons."""
    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    _fill(win, "device")
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.device_view)
    win.select_section("watch")
    win.device_error_row.set_expanded(True)
    _iterate(0.3)
    assert win.device_error_row.get_title() == {"en": "Device error log",
                                                 "pl": "Dziennik błędów urządzenia"}[language]
    assert _min_width(win.device_view) <= NARROWEST


@pytest.mark.parametrize("why", ["logged-out", "failed"])
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_sentence_about_the_account_fits_the_narrowest_window(narrow_window, language,
                                                                  why):
    """Analysis 14, c4 (25.10.2026): the sentence under the tiles wraps."""
    from openexpress.gui import app

    win = narrow_window(language)
    win.start.show(_tiles(), False)
    win._account_why = why
    app.show_account_note(win, _tiles())
    _iterate(0.05)
    assert win.start.account_note.get_visible()
    assert _min_width(win.start.page) <= NARROWEST


@pytest.mark.parametrize("view", ["grid", "list"])
@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_start_page_in_the_view_chosen_fits_the_narrowest_window(narrow_window, language,
                                                                     view):
    """T6b: nine connected (a list when automatic), three remembered, three on the account,
    all in the view chosen: the page fits the narrowest window."""
    win = narrow_window(language)
    win.start.view = view
    win.start.show(_tiles() * 3, False)
    _iterate(0.05)
    kinds = {type(item).__name__ for item in win.start.tiles()}
    assert kinds == ({"DeviceRow"} if view == "list" else {"FlowBoxChild"})
    assert _min_width(win.start.page) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_start_page_fits_the_narrowest_window(narrow_window, language):
    win = narrow_window(language)
    win.start.show(_tiles(), False)
    _iterate(0.05)
    assert _min_width(win.start.page) <= NARROWEST


def _texts(widget):
    """The texts of all labels under `widget` (the rows of an expander too, folded or not)."""
    from openexpress.gui.app import Gtk

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


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_view_of_a_remembered_device_fits_the_narrowest_window(narrow_window, language):
    """Its content, with the button "Forget Device…" at the bottom. Its title bar is not
    measured: with the words of Sync it is 375 px wide in Polish (25.10.2026, not fixed yet)."""
    from openexpress import known_devices

    known = known_devices.remember(UNIT, "Edge 1050", "006-B4440-00", "33.13", "mtp", now=1.0)
    win = narrow_window(language)
    win._fill_remembered(known)
    win.navigation.set_animate_transitions(False)
    win.navigation.push(win.remembered.page)
    _iterate(0.05)
    assert win.remembered.forget_group.get_visible()
    assert _min_width(win.remembered.page.get_child().get_content()) <= NARROWEST


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("page", ["start", *DEVICE_PAGES])
def test_captions_break_only_where_they_should(narrow_window, language, page):
    """The window at 360 px (21.10.2026): "4,7 / GB", and a separator that may start a line.
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
    assert any("\u00a0·" in t for t in texts) or page in ("maps-none", "music")
    assert _breaks_badly(texts) == []


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_line_of_the_last_sync_keeps_its_separators_too(language):
    """When the last sync was and its numbers, joined in the command line's words (service):
    in the window "·" stays with the part before it."""
    from openexpress import i18n
    from openexpress.gui import app

    i18n.setup(language)
    try:
        line = app.last_sync_words({"time": time.time() - 3 * 86400, "uploaded": 6,
                                    "downloaded": 7, "new_files": {"FIT_TYPE_4": 8}})
    finally:
        i18n.setup("en")
    assert line.count("\u00a0· ") == 2 and _breaks_badly([line]) == []


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


@pytest.mark.parametrize("language", ["en", "pl"])
def test_the_preferences_with_the_storage_group_fit_the_narrowest_window(narrow_window,
                                                                         monkeypatch, language):
    """The preferences of the real window, the parts of the cache counted with sizes of all
    kinds (gigabytes of maps, an empty part): no wider than the narrowest window."""
    from openexpress import cache
    from openexpress.gui.app import Adw

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
    (page,) = [w for w in _descendants(dialog) if isinstance(w, Adw.PreferencesPage)
               and everything.is_ancestor(w)]
    texts = list(_texts(page))
    assert any(t.startswith(("6.7\u00a0GB", "6,7\u00a0GB")) for t in texts)
    assert ("Clear All…" if language == "en" else "Wyczyść wszystko…") in texts
    assert _min_width(page) <= NARROWEST
    dialog.close()


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def _tiles_with_chips():
    """Tiles with the longest pairs of chips (PLAN T9): an update with a device off the
    account, with a drive, with activities to sync, with a device never synced, with the
    primary device and with a device synced; the primary device with a device synced; an
    update copied with a device never synced, and with a drive (25.10.2026)."""
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
    from openexpress.gui.app import Gtk

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
    no chip is cut short, before it they may get shorter (24.10.2026)."""
    from openexpress.gui import start

    if not wrap_box:
        monkeypatch.setattr(start, "Adw", _WithoutWrapBox())
    win = narrow_window(language)
    win.set_default_size(NARROWEST, 800)
    win.start.show(_tiles_with_chips(), False)
    _iterate(0.3)
    rows = win.start.tiles()
    assert len(rows) == 9 and all(row.get_parent() is win.start.list for row in rows)
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


# The longest pair of chips in either language (24.10.2026): 271 px on one line in the first
# measurement, 309 px in GNOME's font; more than a tile has at 700 and 1000 px. Since
# 25.10.2026 an update copied ("Unplug to install") instead of one found.
LONGEST_PAIR = ("Odłącz, aby zainstalować", "Nie synchronizowano")


def _tiles_per_line(page) -> int:
    from gi.repository import Graphene

    tops = [round(tile.compute_point(page.page, Graphene.Point())[1].y)
            for tile in page.tiles()]
    return tops.count(tops[0])


def _chip_labels(widget) -> list:
    from openexpress.gui.app import Gtk

    found = []
    for child in _descendants(widget):
        if isinstance(child, Gtk.Label) and child.has_css_class("oe-chip") and child.get_label():
            found.append(child)
    return found


class _WithoutWrapBox:
    """libadwaita before 1.7, for the start page: everything but Adw.WrapBox."""

    def __getattr__(self, name):
        from openexpress.gui.app import Adw

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
    shorter). Measured 24.10.2026: with the pair, two tiles shared a line of 1000 px instead
    of three, and one shared a line of 700 px instead of two."""
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
                    "Oprogramowanie 23.39")
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
    assert per_line[1] == per_line[0] and per_line[0] == (2 if width == 700 else 3)
    if wrap_box:
        assert [c.get_label() for c in chips if c.get_layout().is_ellipsized()] == []
