"""openexpress graphical interface (GTK 4 + libadwaita)."""

from __future__ import annotations

import dataclasses
import gc
import logging
import os
import sys
import time
import weakref
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import (  # noqa: E402, F401 - paths also gui.app.paths for the tests
    devicelist,
    diagnostics,
    i18n,
    known_devices,
    last_sync,
    netstatus,
    paths,
    service,
    sync,
    tile_facts,
)
from openexpress.connect import NotAccountDevice  # noqa: E402
from openexpress.console import error as print_error  # noqa: E402
from openexpress.device.gio_storage import (  # noqa: E402
    ABSENT,
    DRIVE_OFF,
    MASS_STORAGE,
    NO_DEVICE_FILE,
    NOT_MOUNTED,
    PRESENT,
    STARTING,
    UNMOUNTED_DRIVE,
    USB_GAP_S,
    WAIT_FOR_DEVICE_S,
    DeviceSummary,
    FoundDevice,
    NotThereYet,
    SeveralDevices,
    distinct_devices,
    drive_notice_text,
    drive_off_text,
    drive_volume,
    drives_on_usb,
    drives_without_a_volume,
    ejected_by_us,
    ejected_drive_waits,
    ejected_text,
    find_all_devices,
    garmin_products_on_usb,
    garmin_ums,
    garmin_usb,
    held_elsewhere_text,
    mtp_missing,
    no_mtp_text,
    no_mtp_title,
    not_ready_text,
    note_drive_place,
    own_remount,
    probe,
    usb_state,
    usb_still_there,
)
from openexpress.gui import pictures, start  # noqa: E402
from openexpress.gui.apps_page import (  # noqa: E402, F401 - also gui.app.<name> for the tests
    AppsPage,
    apps_count_text,
    note_apps_count,
    refused_dirs_text,
)
from openexpress.gui.connected import Connected, open_address, open_unit, unit_ids, uris  # noqa: E402
from openexpress.gui.device_page import (  # noqa: E402, F401 - also gui.app.<name> for the tests
    DevicePage,
    Section,
    backups_text,
    copy_crash_logs,
    copy_dumps,
    count_open_activities,
    device_error_row,
    last_sync_subtitle,
    last_sync_words,
    open_device_kind,
    refresh_last_sync_subtitle,
    remember_sync_count,
    remember_update_found,
    show_backups,
    show_not_for_mass_storage,
)
from openexpress.gui.forget_device import ForgetDevice  # noqa: E402
from openexpress.gui.gui_settings import (  # noqa: E402, F401 - also gui.app.<name> for the tests
    DEFAULT_WINDOW,
    MIN_WINDOW,
    gui_settings_path,
    load_gui_settings,
    save_gui_settings,
    save_window_geometry,
    update_gui_settings,
    window_geometry,
)
from openexpress.gui.maps_page import MapsPage  # noqa: E402
from openexpress.gui.music_page import MusicPage, note_music_count  # noqa: E402, F401 - also gui.app.<name> for the tests
from openexpress.gui.navigation import (  # noqa: E402
    READ_THE_LIST,
    Cause,
    CheckOpen,
    Detect,
    Nothing,
    Page,
    ReadList,
    Seen,
    ShowStart,
    Stay,
    View,
    describe,
    navigate_after,
)
from openexpress.gui.navigation import plan as detection_plan  # noqa: E402
from openexpress.gui.netstatus_view import (  # noqa: E402, F401 - also gui.app.<name> for the tests
    DOT,
    NET_COLOUR,
    NET_COLOURS,
    NET_POPOVER_WIDTH,
    NetStatusPanel,
    _render_net_status_later,
    _render_net_status_on_show,
)
from openexpress.gui.pictures import (  # noqa: E402, F401 - also gui.app.<name> for the tests
    PNG_SIGNATURE,
    cached_download,
    cached_texture,
    device_picture_path,
    download_picture,
    load_texture,
    note_segment,
    picture_from_unit_info,
    picture_target,
    with_device_picture,
)
from openexpress.gui.preferences import (  # noqa: E402, F401 - also gui.app.<name> for the tests
    Preferences,
    account_changed,
    account_client,
    ask_clear_cache_part,
    ask_to_clear,
    diagnostics_group,
    load_ui_language,
    resolve_account,
    save_ui_language,
    show_cache_usage,
)
from openexpress.gui.report_dialog import ReportDialog  # noqa: E402
from openexpress.gui.start import RememberedPage, StartPage, kind_icon  # noqa: E402
from openexpress.gui.tasks import writes_to_device  # noqa: E402, F401 - also gui.app.<name> for the tests
from openexpress.gui.updates_page import (  # noqa: E402, F401 - also gui.app.<name> for the tests
    ARROW,
    UpdatesPage,
    update_check_failed,
)
from openexpress.gui.window_tasks import (  # noqa: E402, F401 - also gui.app.<name> for the tests
    TEXT_AFTER_MS,
    WRAP_BOX,
    TaskRunner,
    collect_garbage,
    log_unexpected,
    read_space,
    refresh_space,
    render_space,
    row_subtitle,
    show_working,
    still_in_window,
    stop_waiting,
    wrapping_row,
)
from openexpress.gui.words import (  # noqa: E402
    last_seen_text,
    last_seen_tooltip,
    never_synced_tooltip,
    primary_wearable_text,
    remembered_update_text,
    software_text,
)
from openexpress.i18n import N_, _, human_size, pgettext  # noqa: E402, F401 - human_size also gui.app.human_size for the tests
from openexpress.known_devices import KnownDevice  # noqa: E402

APP_ID = "io.github.tomaszbojanowski.OpenExpress"
USB_POLL_S = 2
# A drive may come a moment after its USB device (#19, analysis 18): a device in mass storage
# mode that the last list found without one is looked for again every DRIVE_RETRY_S seconds,
# at most DRIVE_RETRIES times.
DRIVE_RETRY_S = 4
DRIVE_RETRIES = 5
# A device this long on USB in its normal mode without being offered over MTP: the page adds
# that it may be plugged in again (gvfs did not mount an Edge 1050 restarted with its cable
# in; plugging it in again helped, 21.10.2026). Only the words change, not the detection.
REPLUG_HINT_S = 15
# The same words for a device this long on USB in its transitional mode (091e:0003): past the
# about 19 s of a fēnix 8 starting, so that a device starting as usual gets no such advice.
STARTING_REPLUG_HINT_S = 25
# TRANSLATORS: added to the page while a device on USB is not offered over MTP
REPLUG_HINT = N_("If the device isn't locked and isn't asking for anything, unplug the cable and "
                 "plug it back in.")
# TRANSLATORS: the same, after the sentence about a PIN, which names the device already
REPLUG_HINT_AFTER_PIN = N_("If it isn't locked and isn't asking for anything, unplug the cable "
                           "and plug it back in.")
# The captions of the window (a hard space before "·"): in i18n, for last_sync too.
SEPARATOR, caption = i18n.SEPARATOR, i18n.caption
log = logging.getLogger(__name__)


# How often the texts told against now ("2 min ago") are looked at again, while shown.
RELATIVE_REFRESH_S = 60


class StartView:
    """The section "View" of the menu of the start page (PLAN T6b, 24.10.2026): how its
    sections show their devices: automatic (devicelist.AUTO: a list from GRID_MAX + 1
    devices in a section, each section on its own), a grid or a list for all of them. Kept
    in gui.json (KEY); a change draws the start page again from what is known
    (_render_start), the devices are not asked. All of it here, to go with the start page
    when app.py is split further."""

    KEY = "start_view"
    ACTION = "start-view"  # win.start-view, whose state is the view

    @classmethod
    def saved(cls) -> str:
        """The view kept in gui.json; automatic without one, or with a value of another type
        or not known (the file is left as it is)."""
        view = load_gui_settings().get(cls.KEY)
        return view if isinstance(view, str) and view in devicelist.VIEWS else devicelist.AUTO

    @classmethod
    def save(cls, view: str) -> None:
        """Kept as the window's size is: gui.json read again, its other keys stay."""
        update_gui_settings(**{cls.KEY: view})

    @classmethod
    def menu(cls, main: Gio.Menu) -> Gio.Menu:
        """The menu of the start page: "View" with its three items, one of them chosen (the
        action's state), then the sections of the main menu."""
        views = Gio.Menu()
        # TRANSLATORS: items of the section "View" of the menu of the start page: how the
        # devices are shown (automatic: tiles, a list for more than six in a section)
        for view, words in ((devicelist.AUTO, pgettext("start view", "Automatic")),
                            (devicelist.GRID, pgettext("start view", "Grid")),
                            (devicelist.LIST, pgettext("start view", "List"))):
            views.append(words, f"win.{cls.ACTION}::{view}")
        menu = Gio.Menu()
        # TRANSLATORS: heading of a section of the menu of the start page
        menu.append_section(pgettext("start view", "View"), views)
        for i in range(main.get_n_items()):
            menu.append_item(Gio.MenuItem.new_from_model(main, i))
        return menu

    @classmethod
    def install(cls, win) -> None:
        """The window's action, its state the view kept, which the start page takes."""
        view = cls.saved()
        win.start.view = view
        action = Gio.SimpleAction.new_stateful(cls.ACTION, GLib.VariantType.new("s"),
                                               GLib.Variant.new_string(view))
        action.connect("change-state", lambda act, value: cls._changed(win, act, value))
        win.add_action(action)

    @classmethod
    def _changed(cls, win, action, value) -> None:
        view = value.get_string()
        if view not in devicelist.VIEWS or view == action.get_state().get_string():
            return
        action.set_state(value)
        cls.choose(win, view)

    @classmethod
    def choose(cls, win, view: str) -> None:
        """The view chosen: kept (a failure only in the log), the start page drawn in it."""
        log.info("start page: view %s", view)
        try:
            cls.save(view)
        except OSError as e:
            log.warning("start page: the view is not kept (%s)", type(e).__name__)
        win.start.view = view
        win._render_start()


def account_note(why: str | None) -> str | None:
    """The one sentence of the start page while the account's list is not known (analysis
    14, c4): not logged in ("logged-out": its tiles then say nothing of the account), or it
    could not be read ("failed": they say what was kept of it, tile_facts). None: known, or
    still asked."""
    if why == "logged-out":
        # TRANSLATORS: under the tiles of the start page
        return _("Not logged in to Garmin Connect: the tiles do not show what is on your "
                 "account or what there is to sync.")
    if why == "failed":
        # TRANSLATORS: under the tiles of the start page
        return _("Your Garmin Connect account could not be read: the tiles show only what "
                 "was known of it before.")
    return None


def show_account_note(win, entries) -> None:
    """The sentence of account_note under the tiles drawn; none without tiles."""
    win.start.set_account_note(account_note(getattr(win, "_account_why", None))
                               if entries else None)


@dataclass
class DeviceChoices:
    """Several Garmin devices are connected: their summaries, one per device, to choose
    from (what a detection gives instead of a session)."""
    summaries: list


def choice_subtitle(summary) -> str:
    """The words under a connected device: its version or why it has no details. Not how
    it is connected: a drive says it in its chip (start.chips), MTP is the usual way. Never
    its address: an MTP address carries the serial number."""
    if summary.ok:
        return _("Software {version}").format(version=summary.software_version)
    if summary.reason == NOT_MOUNTED:
        return _("Not mounted; choose it to open it")
    if summary.reason == NO_DEVICE_FILE:
        return _("No GarminDevice.xml found")
    return _("Could not be read")


def detect_devices(current_uri: str | None = None):
    """The window's detection: a session on the one device connected, as before, or
    DeviceChoices when several different ones are (MTP devices and Garmin drives alike;
    one device seen in both ways is one). The device open now (`current_uri`) is opened
    again when it is still there. The list asks nothing of the account."""
    found = find_all_devices()
    if len(found) > 1 or any(not f.mounted and f.kind == "ums" for f in found):
        # Several, or a drive the system has not mounted: a tile to choose (and mount).
        again = next((f for f in found if current_uri is not None
                      and f.uri.rstrip("/") == current_uri.rstrip("/")), None)
        if again is not None:
            return service.check_session(service.open_session(found=again))
        return _choose(found)
    try:
        return service.check_session(service.open_session(allow_ums=True))
    except SeveralDevices as e:
        if not e.devices:
            raise
        return _choose(e.devices)


def with_space(found):
    """A device opened for the window, with its free space and capacity read in the same
    thread (the device page and the report then ask it nothing); a list to choose from is
    passed on as it is."""
    if not isinstance(found, DeviceChoices):
        found.space = read_space(found.storage)
        # In the task that opens the device, so before any task can write to it: what it
        # logged as errors, and its diagnostic files copied when the copy is on (once per
        # connection; never a session of our own).
        try:
            found.diagnostics = diagnostics.at_open(found.device, found.storage)
        except Exception:  # an extra of the opening never fails it
            log.warning("diagnostics: the error logs were not read", exc_info=True)
    return found


def _choose(found: list):
    """The list of these devices, one entry each, without those gone since they were found;
    with one left, that one opened (D7); with none, no device."""
    summaries = distinct_devices([probe(f) for f in found])
    for s in summaries:
        if s.ok:  # its GarminDevice.xml was read: remembered before anything is chosen
            known_devices.seen(s.unit_id, s.description, s.part_number, s.software_version,
                               s.found.kind, **service.drive_keys(
                                   s.found.usb if s.found.kind == "ums" else None))
    left = _still_there(summaries)
    if len(left) > 1 or (left and left[0].found.kind == "ums" and not left[0].found.mounted):
        # A drive the system has not mounted is not mounted behind the user's back: its tile.
        return DeviceChoices(left)
    if left:  # also one device found in two ways: no choice to make
        return service.check_session(service.open_session(found=left[0].found))
    raise NotThereYet(not_ready_text(ABSENT))


def _still_there(summaries: list) -> list:
    """Without the entries that have no details and are no longer on USB: gone."""
    return [s for s in summaries
            if s.ok or s.found.usb is None or s.found.usb.bus is None
            or usb_still_there(s.found.usb)]


def connected_devices() -> list:
    """The devices connected now, one entry each, as the start page shows them while a
    device is open: GarminDevice.xml of each is read, nothing is opened or mounted and
    nothing is asked of the account."""
    return _still_there(distinct_devices([probe(f) for f in find_all_devices()]))


def without_facts(tile):
    """A tile without the facts of its chips (tile_facts.TILE_FIELDS): to tell whether only
    they changed."""
    return dataclasses.replace(tile, **tile_facts.TileFacts().tile_fields())


def session_summary(s) -> DeviceSummary:
    """The open device as an entry of the start page (never its address in a label)."""
    d = s.device
    kind = "ums" if getattr(s, "mass_storage", False) else "mtp"
    found = FoundDevice(str(getattr(s.storage, "device_uri", "") or ""), "", True, kind=kind)
    summary = DeviceSummary(found, d.description, d.part_number, d.software_version_str,
                            d.unit_id or None)
    summary.to_sync = getattr(s, "to_sync", None)  # as the thread that opened it counted
    return summary


# The target of the tile of the device open now: its view, without opening it again.
OPEN = "open"


def tile_picture(part: str, url: str | None, source: str | None = None):
    """(texture, where it came from) or (None, why not): the file in the cache by part
    number, else the address of the tile downloaded into it; `source` says whose address it
    is (devicelist: the account's, or the one remembered with the device)."""
    path = device_picture_path(part)
    if path.exists():
        texture = cached_texture(part)
        return (texture, "the cache") if texture is not None else (
            None, "the file is not a picture")
    if not url:
        return None, "not in the cache"
    path, why = download_picture(account_client().http, url, path.name)
    if path is None:
        return None, f"download refused: {why}"
    texture = load_texture(path)
    return (texture, source or devicelist.PICTURE_FROM_ACCOUNT) if texture is not None else (
        None, "the file is not a picture")


def last_sync_more(unit_id) -> tuple[str | None, list]:
    """The tooltip and the details of "Last sync" of a device not connected, as its page
    when it is: the full date and last_sync.describe's lines; "Never synced …" and none for
    a device never synced here."""
    if not unit_id or not sync.synced_here(unit_id):
        return never_synced_tooltip(), []
    data = service.load_last_sync(unit_id)
    if not data:
        return None, []
    told = last_sync.describe(data)
    return told.tooltip, told.details


def account_rows(win, unit_id) -> list[tuple[str, str]]:
    """What the account's list in memory says of a device not connected, read only (analysis
    14, b8): its primary wearable, for a device that can be one. Nothing is asked."""
    wearable = devicelist.primary_wearable(getattr(win, "_account", None), unit_id)
    if wearable is None:
        return []
    return [(_("Primary wearable"), primary_wearable_text(wearable))]


def show_remembered_more(win, unit_id) -> None:
    """The view of a device not connected: the details of its last sync and the rows of the
    account."""
    win.remembered.set_last_sync_details(*last_sync_more(unit_id))
    win.remembered.set_account_rows(account_rows(win, unit_id))


def remembered_texts(win, known) -> dict:
    """The texts of the view of a device remembered that are told against now: when it was
    last seen, its last sync, the last check of its updates."""
    now = datetime.now()  # the window's clock (the tests set it)
    return {"last_seen": last_seen_text(known.last_seen, now),
            "last_sync": win._last_sync_text(known.unit_id),
            "update": remembered_update_text(known, now)}


def account_subtitle(fields: dict) -> str:
    """A device only on the account: its version as Garmin Connect knows it (if it says)."""
    seen = _("Not seen on this computer")
    version = fields["software_version"]
    return caption(software_text(version, from_account=True), seen) if version else seen


def tile_subtitle(source) -> str:
    """The words under the name of a tile, by what it was made of (devicelist.entries)."""
    if isinstance(source, KnownDevice):
        return remembered_subtitle(source)
    if isinstance(source, dict):  # only on the account
        return account_subtitle(devicelist.account_fields(source))
    return choice_subtitle(source)


def remembered_subtitle(known, now: datetime | None = None) -> str:
    """A remembered device that is not connected: its version and when it was last seen,
    told against now as the last sync is ("Last seen 2 h ago"; in full in its tooltip)."""
    return caption(software_text(known.software_version),
                   last_seen_text(known.last_seen, now or datetime.now()))  # the window's clock


def _mount_address(mount) -> str:
    """The address of a mount for the log. An MTP address carries the serial number,
    which the log filter hides; any other address may carry a local path with the user
    name, which the filter does not hide, so only its scheme is written."""
    root = mount.get_root()
    uri = (root.get_uri() if root is not None else None) or ""
    if uri.startswith("mtp://"):
        return uri
    return uri.split(":", 1)[0] + ":" if ":" in uri else "unknown"


def _same_device(old, new) -> str:
    """For the log: how the detected device relates to the open one. Compared by the
    unit ID, which is never written."""
    if old is None:
        return "a device found"
    before = getattr(getattr(old, "device", None), "unit_id", None)
    now = getattr(getattr(new, "device", None), "unit_id", None)
    if before is not None and before == now:
        return "the same device as the open one"
    return "another device than the open one"


def _on_mount(storage, mount_uri: str) -> bool:
    """Whether the open storage is on this mount (full addresses, compared in memory)."""
    return str(getattr(storage, "label", "")).startswith(mount_uri.rstrip("/") + "/")


def _cause_of(reason: str | None) -> Cause:
    """Why a trigger of the timer came, when its caller did not say."""
    if reason == READ_THE_LIST:
        return Cause.UNPLUGGED_OTHER
    return Cause.USB_CHANGED if reason is None else Cause.PLUGGED


def _timed_cause(causes: list) -> Cause:
    """The cause of a detection by the timer, from those of its triggers: a mount first;
    never "another device unplugged" (that one only reads the list)."""
    for cause in (Cause.PLUGGED, Cause.UNPLUGGED_CHOICE, Cause.USB_CHANGED):
        if cause in causes:
            return cause
    return Cause.USB_CHANGED if causes else Cause.PLUGGED


def seen_open(s, connected) -> Seen:
    """What the window knows after a detection or a read of the list: the devices connected
    (`connected`, entries) with the open one among them."""
    address, unit = open_address(s), open_unit(s)
    return Seen(uris(connected) | ({address.rstrip("/")} if address else set()), address,
                unit_ids(connected) | ({unit} if unit else set()), unit)


def schedule_detection(win, delay_ms: int, mount_uri: str | None = None,
                       cause: Cause | None = None) -> None:
    """Look for the device after `delay_ms`: after a mount of a Garmin device (`mount_uri`),
    after it showed up on USB (None), or only read the list of devices connected again
    (READ_THE_LIST); `cause`: why (navigation.Cause). One timer for all of them: another
    trigger moves it, and what is decided when it fires covers every trigger so far."""
    if getattr(win, "_detect_timer", None) is not None:
        GLib.source_remove(win._detect_timer)
        log.info("device detection: moved by another trigger")
    win._detect_reasons = [*getattr(win, "_detect_reasons", []), mount_uri]
    win._detect_causes = [*getattr(win, "_detect_causes", []), cause or _cause_of(mount_uri)]

    def due():
        win._detect_timer = None
        detection_due(win)
        return False

    win._detect_timer = GLib.timeout_add(delay_ms, due)


def watch_drives_while_open(win) -> None:
    """While a device is open the window does not look at USB otherwise (_poll_usb), and a
    Garmin drive that the system does not mount sends no mount event (KDE, #19): one that
    comes has the list read again (Q1: the open device stays open), and so has one not
    mounted that goes. Only /sys is read."""
    places = drives_on_usb()
    before = getattr(win, "_drives_while_open", None)
    win._drives_while_open = places
    if before is None or places == before:
        return  # the first look: what is there is the open device's company
    if places - before:
        log.info("USB: a Garmin device in mass storage mode came while a device is open, the "
                 "list of devices is read again in 2.5 s")
        schedule_detection(win, 2500, cause=Cause.PLUGGED)
        return
    connected = getattr(win, "connected", None)
    if connected is not None and any(u.startswith(UNMOUNTED_DRIVE) for u in connected.unread()):
        log.info("USB: a Garmin device in mass storage mode went while a device is open, the "
                 "list of devices is read again in 1.5 s")
        schedule_detection(win, 1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)


def look_again_for_drives(win, clock=time.monotonic) -> None:
    """A Garmin device in mass storage mode that the last list found without a drive: gvfs
    had no volume of it yet, or none with a medium (#19, analysis 18). Looked for again every
    DRIVE_RETRY_S, at most DRIVE_RETRIES times for the same devices; with a device open,
    only the list is read again (Q1)."""
    waiting = drives_without_a_volume()
    if not waiting:
        win._drive_look = None
        return
    now = clock()
    look = getattr(win, "_drive_look", None)
    if look is None or look[0] != waiting:
        win._drive_look = (waiting, 0, now)  # counted from now
        return
    places, tries, since = look
    if tries >= DRIVE_RETRIES or now - since < DRIVE_RETRY_S:
        return
    win._drive_look = (places, tries + 1, now)
    log.info("USB: a Garmin device in mass storage mode has no drive yet, looked for again "
             "(%d of %d)", tries + 1, DRIVE_RETRIES)
    schedule_detection(win, 500, cause=Cause.PLUGGED)


def detection_due(win) -> None:
    """The scheduled detection: decided now (navigation.plan), or after the running task
    (e.g. the detection at start, during which gvfs reports the mount that is being
    opened)."""
    if win.busy:
        win._detect_after_task = True
        return
    win._detect_after_task = False
    reasons, win._detect_reasons = getattr(win, "_detect_reasons", []), []
    causes, win._detect_causes = getattr(win, "_detect_causes", []), []
    if getattr(win, "_detect_timer", None) is not None:
        # Decided after a task: this covers the triggers that came meanwhile, so the timer
        # armed for them would only fire again with none left.
        GLib.source_remove(win._detect_timer)
        win._detect_timer = None
    cause = _timed_cause(causes)
    s = win.session
    connected = getattr(win, "connected", None)
    usb = getattr(s.storage, "usb", None) if s is not None else None
    what = detection_plan(cause, reasons,
                          open_label=str(getattr(s.storage, "label", "")) if s is not None
                          else None,
                          listed=uris(connected.devices) if connected is not None
                          else frozenset(),
                          unread=connected.unread() if connected is not None
                          else frozenset(),
                          open_moved=lambda: (usb is not None and usb.bus is not None
                                              and not usb_still_there(usb)))
    # Mounts of devices already on the start page's list (e.g. reported by gvfs as the
    # window started, before one was chosen or not) bring nothing new: no detection, and
    # they never close the device chosen.
    if isinstance(what, Nothing):
        if what.ignored:
            log.info("device detection: mount of a device already listed, nothing to do")
        return  # nothing new: the open device stays as it is
    if what.ignored:
        log.info("device detection: %d mount(s) of devices already on the list, ignored",
                 what.ignored)
    if isinstance(what, ReadList):
        # Another device came or went, or one listed was mounted: the open one stays (Q1).
        only_gone = bool(causes) and all(c is Cause.UNPLUGGED_OTHER for c in causes)
        win._reread_cause = Cause.UNPLUGGED_OTHER if only_gone else cause
        win._reread_connected()
        return
    if isinstance(what, Detect):
        if what.moved:
            # The same address (it carries the serial number), but not the same place on
            # USB: unplugged and plugged in again, so the device may have changed (it
            # applies what was waiting when it is unplugged) and the session points to the
            # old place.
            log.info("device detection: the same address, but not at the same place on USB")
        if what.read_list:
            win.refresh_device(cause=cause, read_list=True)  # another device came as well
        else:
            win.refresh_device(cause=cause)
        return

    def done(_xml):
        if win.session is s:
            log.info("device detection: the open device is still there, nothing to do")

    def failed(e):
        log.info("device detection: the open device does not answer (%s)", type(e).__name__)
        if win.session is s:
            win.refresh_device(cause=Cause.PLUGGED)

    # CheckOpen: its mount was reported, not a new one: if it still answers, the pages stay
    # as they are.
    assert isinstance(what, CheckOpen)
    win.in_background(lambda: s.storage.read("GARMIN/GarminDevice.xml"), done, failed)


def _take_asked(win, own: Cause) -> tuple[Cause, str | None]:
    """Why the decision made now is made and the device open when it was asked: those of
    the detection that ends here (if one), else `own`. A Refresh on the start page asked
    while a task ran keeps the start page for what that task brings as well, once."""
    asked = getattr(win, "_asked", None)
    win._asked = None
    cause, open_before = asked if asked is not None else (own, None)
    if getattr(win, "_pending_cause", None) is Cause.REFRESH_ON_START:
        cause, win._pending_cause = Cause.REFRESH_ON_START, Cause.REFRESH_IN_VIEW
    return cause, open_before


def decided(win, cause: Cause, before: Seen, after: Seen):
    """Where the user goes (navigation.navigate_after), with one line of the log."""
    view = visible_view(win)
    decision = navigate_after(cause, view, before, after)
    log.info("%s", describe(cause, view, before, after, decision))
    return decision


def visible_view(win) -> View:
    """The page shown, for navigation (the remembered device by its unit ID, in memory)."""
    page = win.navigation.get_visible_page()
    if page is win.start.page:
        return View(Page.START)
    if page is win.remembered.page:
        return View(Page.REMEMBERED, getattr(win, "_remembered_unit", None),
                    getattr(win, "_remembered_from_account", False))
    return View(Page.DEVICE)


class MainWindow(TaskRunner, DevicePage, MapsPage, MusicPage, AppsPage, UpdatesPage, Preferences,
                 NetStatusPanel, ForgetDevice, ReportDialog, Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="openExpress")
        self.set_size_request(*MIN_WINDOW)
        self.session: service.DeviceSession | None = None
        # What the pages show while no device is open; the Sync button says the same.
        self._usb_title = ""
        self.busy = False
        self._waiting = 0  # GLib timer that moves the bar during a wait for a server
        self.gui_settings = load_gui_settings()
        width, height, maximized = window_geometry(self.gui_settings)
        self.set_default_size(width, height)
        if maximized:
            self.maximize()
        self.maps_cache = None
        self.updates_cache = None
        self.updates_beta = False  # whether Garmin offers beta versions to the open device
        # None = unknown, "current", "available", "ready" (copied, waits for the device), "failed"
        self.update_state: str | None = None
        self.update_count = 0
        self.update_only = None  # the update to install when it is the only one
        # When the check whose answer "Device software" shows was, when it is not a check of
        # now (a record, its check failed); None: an answer of now, or checking.
        self.update_checked_at: float | None = None
        self._update_record_at: float | None = None  # the time of the record shown
        self._update_check_session = None
        # None = unknown, "none" (no maps for the device), "empty" (none installed),
        # "current", "available", "failed"
        self.map_state: str | None = None
        self.map_update_count = 0
        self._map_check_session = None
        # The old files Garmin's cleanup rules would take, from the maps read for the device
        # open (maps_page.load_maps); only shown in the questions before a map changes.
        self.map_cleanup: list = []
        self._action_buttons: list[Gtk.Widget] = []
        self._retry_sources: dict[str, int] = {}
        self._refresh_pending = False
        self._detect_timer: int | None = None
        self._detect_reasons: list[str | None] = []
        self._detect_causes: list[Cause] = []
        self._detect_after_task = False
        self._last_sync_children: list[Gtk.Widget] = []
        self._close_dialog = None
        # The start page: (tile, what it opens) in the order given to the page, which hands
        # back the place of the tile clicked; what it opens (OPEN: the open device's view; a
        # FoundDevice to open; a KnownDevice or an entry of the account to show read-only) is
        # kept here, never in the tile.
        self._start_entries: list = []
        # Where the user goes is decided by navigation.navigate_after: why the coming
        # decision is made and the device open when it was asked (set when a detection
        # starts, or as the open device goes); the cause of a detection that waits for the
        # running task; the remembered device shown (its unit ID, in memory only).
        self._asked: tuple | None = None
        self._pending_cause: Cause | None = None
        self._remembered_unit: int | None = None
        self._remembered_entry: KnownDevice | None = None  # its entry, for its texts
        self._remembered_from_account = False  # the view shows what the account says
        # The devices connected, as last known (gui/connected.py): the list to choose from
        # while there is one, the devices connected while one of them is open, what was on
        # USB when the list was made and the addresses of the tiles drawn last.
        self.connected = Connected()
        self._reread_pending = False
        # Why the list is read again (navigation.Cause), for where the user goes after it.
        self._reread_cause: Cause | None = None
        # The devices of the Garmin account (their entries, keys in lower case), read once
        # per start and login; None: not logged in, or not read.
        self._account: list | None = None
        # Why the account's list is not known (account_note), None: known or still asked.
        self._account_why: str | None = None
        self._account_wanted = False  # wanted after the detection running now

        # TRANSLATORS: tab title
        self.sections = {s.key: s for s in (
            Section("watch", pgettext("tab", "Device"), "preferences-system-time-symbolic",
                    _("Detecting the device…")),
            # TRANSLATORS: tab title
            Section("updates", pgettext("tab", "Updates"), "software-update-available-symbolic",
                    _("Checking for updates…")),
            # TRANSLATORS: tab title
            Section("maps", pgettext("tab", "Maps"), "mark-location-symbolic",
                    _("Checking maps…")),
            # TRANSLATORS: tab title (Connect IQ apps)
            Section("apps", pgettext("tab", "Apps"), "view-app-grid-symbolic",
                    _("Loading Connect IQ apps…")),
            # TRANSLATORS: tab title
            Section("music", pgettext("tab", "Music"), "audio-x-generic-symbolic",
                    _("Reading music on the device…")),
        )}
        for sec in self.sections.values():
            sec.retry.connect("clicked", lambda _b, k=sec.key: self.load_section(k, force=True))

        self._build_layout()
        self._install_actions()
        # The dot follows the answers to the program's own requests; the listener holds the
        # window only weakly and runs in the request's thread, so it hands over to GTK.
        ref = weakref.ref(self)
        self._net_listener = lambda: GLib.idle_add(_render_net_status_later, ref)
        netstatus.STATUS.add_listener(self._net_listener)

        self.connect("close-request", self._on_close_request)
        self.monitor = Gio.VolumeMonitor.get()
        self.monitor.connect("mount-added", self._on_mount_added)
        self.monitor.connect("mount-removed", self._on_mount_removed)
        # A drive that the system does not mount (KDE) sends no mount event (#19).
        self.monitor.connect("volume-added", self._on_volume_added)
        self.monitor.connect("volume-changed", self._on_volume_changed)
        self.monitor.connect("volume-removed", self._on_volume_removed)
        self._watch_last_sync()
        # Apps whose update was written over PTP and waits for the device to be unplugged.
        # Kept in memory only; forgotten when the device goes away.
        self._updated_apps: set = set()
        # A device that was just plugged in is on USB for a while before gvfs offers it.
        self._usb_state, self._usb_since = ABSENT, 0.0
        self._usb_poll = GLib.timeout_add_seconds(USB_POLL_S, self._poll_usb)
        self._start()

    def _start(self) -> None:
        """What the window does as it opens: the detection, and the account's devices for
        the start page, read once (from the device opened, if one is)."""
        self._want_account()
        self.refresh_device(cause=Cause.START)

    # --- layout -----------------------------------------------------------------------

    @staticmethod
    def _main_menu() -> Gio.Menu:
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("Preferences"), "app.preferences")
        if hasattr(Adw, "ShortcutsDialog"):
            section.append(_("Keyboard Shortcuts"), "app.shortcuts")
        section.append(_("Report a Device or Problem…"), "app.report")
        section.append(_("About openExpress"), "app.about")
        menu.append_section(None, section)
        return menu

    def _build_layout(self) -> None:
        self.split = Adw.NavigationSplitView(min_sidebar_width=210, max_sidebar_width=260)

        menu = self._main_menu()
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     tooltip_text=_("Main Menu"), primary=True)
        # Its back arrow goes to the start page (the device view is a page above it).
        self.sidebar_header = sidebar_header = Adw.HeaderBar()
        sidebar_header.pack_end(menu_button)
        sidebar_view = Adw.ToolbarView()
        sidebar_view.add_top_bar(sidebar_header)
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.nav.add_css_class("navigation-sidebar")
        for sec in self.sections.values():
            box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6,
                          margin_end=6)
            box.append(Gtk.Image(icon_name=sec.icon))
            box.append(Gtk.Label(label=sec.title, xalign=0, hexpand=True))
            row = Gtk.ListBoxRow(child=box)
            row.key = sec.key
            self.nav.append(row)
        self.nav.connect("row-selected", self._on_nav_selected)
        self.nav.connect("row-activated", lambda *_a: self.split.set_show_content(True))
        sidebar_view.set_content(Gtk.ScrolledWindow(child=self.nav,
                                                    hscrollbar_policy=Gtk.PolicyType.NEVER))
        sidebar_view.add_bottom_bar(self._build_net_status())
        self._render_net_status()
        self.split.set_sidebar(Adw.NavigationPage(title="openExpress", child=sidebar_view))

        content_view = Adw.ToolbarView()
        # Its back arrow only when folded, where it goes back to the sidebar: otherwise the
        # sidebar's arrow is the one, and there would be two.
        self.content_header = header = Adw.HeaderBar(show_back_button=False)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic",
                                         tooltip_text=_("Refresh"), action_name="win.refresh")
        header.pack_start(self.refresh_button)
        # TRANSLATORS: button that starts syncing
        self.sync_button = Gtk.Button(label=_("Sync"), action_name="win.sync")
        self.sync_button.add_css_class("suggested-action")
        header.pack_end(self.sync_button)
        self.sync_icon_button = Gtk.Button(icon_name="mail-send-receive-symbolic",
                                           tooltip_text=_("Sync"), action_name="win.sync",
                                           visible=False)
        self.sync_icon_button.add_css_class("suggested-action")
        header.pack_end(self.sync_icon_button)
        content_view.add_top_bar(header)
        self.banner = Adw.Banner(revealed=False)
        self.banner.connect("button-clicked", lambda *_a: self._banner_action())
        content_view.add_top_bar(self.banner)

        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for sec in self.sections.values():
            self.pages.add_named(sec.stack, sec.key)
        content_view.set_content(self.pages)

        self.progress_bar = Gtk.ProgressBar(show_text=False, hexpand=True)
        self.progress_label = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        # One indicator at a time: the spinner until the task tells its progress, then the bar.
        self.busy_spinner = (Adw.Spinner() if hasattr(Adw, "Spinner")
                             else Gtk.Spinner(spinning=True))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        box.append(self.progress_label)
        box.append(self.progress_bar)
        # Shown for as long as a task works with the device.
        self.unplug_label = Gtk.Label(label=_("Do not unplug the device"), xalign=0,
                                      visible=False)
        self.unplug_label.add_css_class("caption-heading")
        self.unplug_label.add_css_class("warning")
        box.append(self.unplug_label)
        self.action_bar = Gtk.ActionBar(revealed=False)
        self.action_bar.pack_start(self.busy_spinner)
        self.action_bar.set_center_widget(box)
        # Only while TrueUp waits for Garmin Connect (a wait of known length): nothing is
        # written to the device meanwhile.
        # TRANSLATORS: button on the bar at the bottom: stop waiting for TrueUp
        self.stop_wait_button = Gtk.Button(label=_("Stop Waiting"), valign=Gtk.Align.CENTER,
                                           visible=False)
        self.stop_wait_button.connect("clicked", lambda _b: stop_waiting(self))
        self.action_bar.pack_end(self.stop_wait_button)
        content_view.add_bottom_bar(self.action_bar)

        self.content_page = Adw.NavigationPage(title=self.sections["watch"].title,
                                               child=content_view)
        self.split.set_content(self.content_page)

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 620sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        breakpoint.add_setter(self.content_header, "show-back-button", True)
        breakpoint.add_setter(self.sync_button, "visible", False)
        breakpoint.add_setter(self.sync_icon_button, "visible", True)
        self.add_breakpoint(breakpoint)

        # The start page is the root; the device view comes above it (D5, D7).
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Refresh"),
                             action_name="win.refresh")
        start_menu = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                    menu_model=StartView.menu(menu),
                                    tooltip_text=_("Main Menu"), primary=True)
        self.start = StartPage(self._open_tile, header_start=[refresh], header_end=[start_menu],
                               picture_of=lambda part: pictures.known(self, part))
        StartView.install(self)
        self.device_view = Adw.NavigationPage(title="openExpress", tag="device",
                                              child=self.split)
        self.navigation = Adw.NavigationView()
        self.navigation.add(self.start.page)  # the first one added is shown
        self.navigation.add(self.device_view)
        self.remembered = RememberedPage()  # the read-only view of a remembered device
        self.remembered.forget_button.connect("clicked", self.ask_forget)
        self.navigation.add(self.remembered.page)
        self.navigation.connect("popped", self._on_popped)
        # The texts told against now are drawn again while the page with them is shown.
        self.navigation.connect("notify::visible-page", lambda *_a: self._update_relative_timer())
        # Only while the window is on the screen: shown, hidden, closed.
        self.connect("map", lambda *_a: self._update_relative_timer())
        self.connect("unmap", lambda *_a: self._update_relative_timer())

        self.toasts = Adw.ToastOverlay(child=self.navigation)
        self.set_content(self.toasts)
        self.nav.select_row(self.nav.get_row_at_index(0))

    def _install_actions(self) -> None:
        for name, cb in (("refresh", lambda *_a: self._on_refresh()),
                         ("sync", lambda *_a: self.start_sync())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
        self._update_actions()

    @staticmethod
    def _ownership_title(error) -> str:
        """The banner's words for a device that cannot be used with the account."""
        if isinstance(error, NotAccountDevice):
            return _("This device is not registered to your Garmin account")
        return _("Could not check whether this device belongs to your Garmin account")

    def _sync_blocked(self) -> str:
        """Why a sync cannot be started now, in words the window already uses; "" if it can."""
        s = self.session
        if s is None:
            # The words the pages show now: no device, a device that is starting, or one
            # that is not ready.
            return self._usb_title or _("Connect your device")
        if getattr(s, "mass_storage", False):
            return _("USB drive")  # never synced; the device page says why
        if not s.logged_in:
            return _("Not logged in to Garmin Connect")
        if s.ownership_error is not None:
            return self._ownership_title(s.ownership_error)
        if self.busy:
            return _("Another operation is in progress")
        return ""

    def _update_actions(self) -> None:
        reason = self._sync_blocked()
        self.lookup_action("sync").set_enabled(not reason)
        # A blue button looks ready even when it is not: without a sync to start it is plain
        # grey and says why.
        for button, usual in ((self.sync_button, None), (self.sync_icon_button, _("Sync"))):
            if reason:
                button.remove_css_class("suggested-action")
            else:
                button.add_css_class("suggested-action")
            button.set_tooltip_text(reason or usual)
        self.lookup_action("refresh").set_enabled(not self.busy)

    def _on_nav_selected(self, _box, row) -> None:
        if row is None:
            return
        sec = self.sections[row.key]
        self.pages.set_visible_child_name(sec.key)
        self.content_page.set_title(sec.title)
        self.load_section(sec.key)

    def select_section(self, key: str) -> None:
        for i, sec in enumerate(self.sections.values()):
            if sec.key == key:
                self.nav.select_row(self.nav.get_row_at_index(i))
        self.split.set_show_content(True)

    def _on_close_request(self, _win) -> bool:
        if not self.busy:
            self._remember_geometry()
            netstatus.STATUS.remove_listener(self._net_listener)
            self._closed = True
            self._update_relative_timer()  # no timer left once the window is closed
            return False
        if self._close_dialog is None:
            d = Adw.AlertDialog(
                heading=_("An operation is in progress"),
                body=_("Wait until it finishes before closing openExpress, so that no files "
                       "on the device are left half-written."))
            d.add_response("ok", _("OK"))
            d.connect("response", lambda *_a: setattr(self, "_close_dialog", None))
            self._close_dialog = d
            d.present(self)
        return True


    def _remember_geometry(self) -> None:
        # The default size follows the user's resizing and keeps the last unmaximized
        # size while the window is maximized, so both states restore correctly.
        width, height = self.get_default_size()
        try:
            save_window_geometry(width, height, self.is_maximized())
        except OSError as e:
            log.warning("Could not save the window size: %s", e)

    # --- the refresh that waited for a task ---------------------------------------

    def _run_pending_refresh(self) -> None:
        if self._refresh_pending and not self.busy:
            self._refresh_pending = False
            self.refresh_device()
        elif getattr(self, "_detect_after_task", False) and not self.busy:
            detection_due(self)
        elif getattr(self, "_reread_pending", False) and not self.busy:
            self._reread_pending = False
            self._reread_connected()

    # --- device ----------------------------------------------------------------------

    def _on_mount_added(self, _mon, mount):
        where = _mount_address(mount)
        if own_remount(mount):
            # Mounted again by our own install; the open device stays as it is.
            log.info("mount added (%s): our own remount, ignored", where)
            return
        ums = garmin_ums(mount)
        if ums is not None and ejected_by_us(ums):
            # Mounted again after we ejected it: it stays closed until it is unplugged.
            log.info("mount added (%s): a Garmin drive ejected by openExpress, ignored until it "
                     "is unplugged", where)
            return
        # Over MTP, or a drive of a Garmin device in mass storage mode.
        if garmin_usb(mount) or ums:
            log.info("mount added (%s): a Garmin device, detecting in 1.5 s "
                     "(a device is open: %s)", where, self.session is not None)
            schedule_detection(self, 1500, mount.get_root().get_uri(), Cause.PLUGGED)
        else:
            log.info("mount added (%s): not a Garmin device, ignored", where)

    def _on_volume_added(self, _mon, volume):
        self._on_drive_volume(volume, "added")

    def _on_volume_changed(self, _mon, volume):
        self._on_drive_volume(volume, "changed")

    def _on_volume_removed(self, _mon, volume):
        unix = volume.get_identifier("unix-device")
        if unix:
            self._drive_gone(UNMOUNTED_DRIVE + unix, "removed")

    def _on_drive_volume(self, volume, what: str) -> None:
        """A volume added or changed. A Garmin drive that the system does not mount (KDE)
        sends no mount event, and gvfs may offer its volume, or a medium in it, a moment
        after the device is on USB (#19, analysis 18): one not mounted with a medium, not on
        the list yet, brings a detection (with a device open: the list read again, Q1); one
        on the list without a medium any more leaves it. A mounted one comes with its mount
        (_on_mount_added); a drive ejected by openExpress stays closed (D3)."""
        drive = drive_volume(volume)
        if drive is None:
            return
        key, usb, medium = drive
        if getattr(volume, "get_mount", lambda: None)() is not None or ejected_by_us(usb):
            return
        if not medium:
            self._drive_gone(key, f"{what}, without a medium")
            return
        connected = self.connected
        if connected.listed(key) if self.session is not None else connected.is_choice(key):
            return  # its tile is there
        log.info("volume %s: a Garmin drive not mounted (USB %s), detecting in 2.5 s (a device "
                 "is open: %s)", what, usb.usb_id, self.session is not None)
        schedule_detection(self, 2500, key, Cause.PLUGGED)

    def _drive_gone(self, key: str, what: str) -> None:
        """The volume of a drive not mounted went, or its medium did: its tile goes too."""
        connected = self.connected
        if self.session is not None and connected.listed(key):
            log.info("volume %s: a Garmin drive not mounted on the list, the list of devices "
                     "is read again in 1.5 s", what)
            schedule_detection(self, 1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)
        elif self.session is None and connected.is_choice(key):
            log.info("volume %s: a Garmin drive not mounted, one of the devices to choose "
                     "from, detecting in 1.5 s", what)
            schedule_detection(self, 1500, cause=Cause.UNPLUGGED_CHOICE)

    def _on_mount_removed(self, _mon, mount):
        where = _mount_address(mount)
        if own_remount(mount):
            log.info("mount removed (%s): our own remount, ignored", where)
            return
        # The USB device is already gone, so compare with the device that is open.
        uri = mount.get_root().get_uri().rstrip("/") + "/"
        s = self.session
        # A Garmin drive is known by its label as well: an unplugged one is gone from /sys.
        ours = s is not None and str(getattr(s.storage, "label", "")).startswith(uri)
        connected = getattr(self, "connected", None)
        if s is None and connected is not None and connected.is_choice(uri):
            log.info("mount removed (%s): one of the devices to choose from, detecting in "
                     "1.5 s", where)
            schedule_detection(self, 1500, cause=Cause.UNPLUGGED_CHOICE)
            return
        if not ours and s is None and (garmin_usb(mount) or garmin_ums(mount)):
            # gvfs reports two mounts of one device: the pages are empty after the first.
            log.info("mount removed (%s): a Garmin device, no device is open, nothing to do",
                     where)
            note_drive_place(garmin_ums(mount))  # a watch may switch its drive off now
            return
        if (not ours and s is not None
                and usb_still_there(getattr(s.storage, "usb", None))):
            if connected is not None and not connected.listed(uri):
                # A pendrive, a phone, a share: none of the devices on the start page (one
                # mounted but not listed yet is read with the list its mount asked for).
                log.info("mount removed (%s): not a device on the list, ignored", where)
                return
            # Another Garmin device went away (several connected): the open one is still at
            # its place on USB, so it stays open; the start page lists those left, read once
            # by the timer of the other triggers (gvfs reports two mounts of one device).
            log.info("mount removed (%s): not the open device, which is still there; the "
                     "list of devices is read again in 1.5 s", where)
            schedule_detection(self, 1500, READ_THE_LIST, Cause.UNPLUGGED_OTHER)
            return
        if ours or garmin_usb(mount):
            log.info("mount removed (%s): %s, showing no device (a task is running: %s)",
                     where, "the open device" if ours else "a Garmin device",
                     bool(getattr(self, "busy", False)))
            if ours and getattr(s, "mass_storage", False):
                # Its place on USB, kept: a watch switches its drive off once it is ejected
                # and comes back as 091e:0003 (#19).
                note_drive_place(getattr(s.storage, "usb", None))
            self._asked = (Cause.UNPLUGGED_OPEN, open_address(s))
            self.session = None
            self._updated_apps = set()  # unplugged: the device applies what was waiting
            self._open_unplugged = True  # the USB poll looks for a device left (_poll_usb)
            self.show_no_device()
        else:
            log.info("mount removed (%s): not the open device, ignored", where)

    @staticmethod
    def _usb_notice(state: str, waited_s: float,
                    normal_s: float = 0.0) -> tuple[str, str, bool]:
        """(title, description, busy) of the placeholder while no device is open. `normal_s`:
        how long a device has been on USB in its normal mode without being offered over MTP;
        from REPLUG_HINT_S on, the description adds that it may be plugged in again (an Edge
        1050 restarted with its cable in was never mounted by gvfs, 21.10.2026)."""
        if state == PRESENT and mtp_missing():
            # gvfs here has no MTP backend (#13): said at once, nothing to wait for.
            return no_mtp_title(), no_mtp_text(), False
        if state == PRESENT and waited_s >= WAIT_FOR_DEVICE_S:
            # The PIN (not_ready_text, which names the device), then plugging it in again
            # (after REPLUG_HINT_S in its normal mode), then other programs.
            replug = f" {_(REPLUG_HINT_AFTER_PIN)}" if normal_s >= REPLUG_HINT_S else ""
            return (_("The device is not ready"),
                    f"{not_ready_text(state)}{replug} {held_elsewhere_text()}", False)
        if state == PRESENT and normal_s >= REPLUG_HINT_S:
            # One sentence becomes two: each with its full stop.
            text = _("This takes a moment after the cable is plugged in.")
            return _("The device is starting…"), f"{text} {_(REPLUG_HINT)}", True
        if state == DRIVE_OFF:
            # TRANSLATORS: a device connected as a USB drive, not over MTP
            return _("USB drive"), drive_off_text(), False
        if state == STARTING and STARTING_REPLUG_HINT_S <= waited_s < WAIT_FOR_DEVICE_S:
            # Still in its transitional mode: one sentence becomes two, as above.
            text = _("This takes a moment after the cable is plugged in.")
            return _("The device is starting…"), f"{text} {_(REPLUG_HINT)}", True
        if state == ABSENT:
            return (_("Connect your device"),
                    _("Connect your Garmin device with a USB cable. "
                      "It will be detected automatically."), False)
        if state == MASS_STORAGE:
            if ejected_drive_waits():
                # TRANSLATORS: title while a drive ejected by openExpress is still plugged in
                return _("Device ejected"), ejected_text(), False
            # The window opens such a drive once the system mounts it, so with none open it
            # is not mounted - or, in the Flatpak, mounted where the sandbox cannot see (F1).
            text = drive_notice_text()
            # TRANSLATORS: a device connected as a USB drive, not over MTP
            return _("USB drive"), text, False
        if waited_s < WAIT_FOR_DEVICE_S:
            return (_("The device is starting…"),
                    _("This takes a moment after the cable is plugged in"), True)
        return _("The device is not ready"), not_ready_text(state), False

    def _poll_usb(self, clock=time.monotonic) -> bool:
        """Every few seconds while no device is open: is a Garmin device on USB that gvfs
        does not offer yet? Only /sys is read; nothing is sent to the device."""
        if self.session is not None or self.busy:
            self._usb_state = ABSENT
            if self.session is not None:
                self._open_unplugged = False  # a device is open again
                if not self.busy:
                    watch_drives_while_open(self)
                    look_again_for_drives(self, clock)
            return True
        self._drives_while_open = None  # looked at anew once a device is open
        # The open device was just unplugged: a device left on USB is looked for (below).
        unplugged, self._open_unplugged = getattr(self, "_open_unplugged", False), False
        connected = getattr(self, "connected", None)
        if connected is not None and connected.choosing and connected.devices:
            # The list stays as it is; a Garmin device plugged in or out (also one that was
            # never mounted, which sends no mount event) brings one new detection.
            if connected.usb_changed(garmin_products_on_usb()):
                log.info("USB: the Garmin devices changed while the list is shown, detecting "
                         "in 1.5 s")
                schedule_detection(self, 1500, cause=Cause.USB_CHANGED)
            look_again_for_drives(self, clock)
            return True
        state = usb_state()
        if state == ABSENT and self._usb_state != ABSENT:
            # Off the bus for a moment between its two modes, or unplugged? Wait and see.
            gone = getattr(self, "_usb_gone", None)
            if gone is None:
                self._usb_gone = clock()
                return True
            if clock() - gone < USB_GAP_S:
                return True
        self._usb_gone = None
        if state != ABSENT and self._usb_state == ABSENT:
            self._usb_since = clock()
        if state == PRESENT and self._usb_state != PRESENT:
            # It left its transitional mode: look for it once gvfs had a moment.
            log.info("USB: a Garmin device in its normal mode, detecting in 2.5 s")
            self._normal_since, self._replug_told = clock(), False
            schedule_detection(self, 2500, cause=Cause.PLUGGED)
        elif state == MASS_STORAGE and self._usb_state != MASS_STORAGE and not unplugged:
            # A drive the system does not mount (KDE) sends no mount event: looked for once,
            # for its tile (analysis 15, stage 0).
            log.info("USB: a Garmin device in mass storage mode, detecting in 2.5 s")
            schedule_detection(self, 2500, cause=Cause.PLUGGED)
        elif unplugged and isinstance(detection_plan(Cause.UNPLUGGED_OPEN, left_on_usb=state,
                                                     ejected_waits=ejected_drive_waits()),
                                      Detect):
            # A Garmin drive left after the open device went (a Varia next to an Edge,
            # 22.10.2026): mounted already, it sends no event, so it is looked for as one in
            # its normal mode is, above.
            log.info("USB: the open device unplugged, a Garmin drive is still connected, "
                     "detecting in 2.5 s")
            schedule_detection(self, 2500, cause=Cause.PLUGGED)
        self._usb_state = state
        normal_s = clock() - getattr(self, "_normal_since", 0.0) if state == PRESENT else 0.0
        if normal_s >= REPLUG_HINT_S and not getattr(self, "_replug_told", False):
            self._replug_told = True
            log.info("USB: present for %d s without MTP, suggesting to replug", REPLUG_HINT_S)
        notice = self._usb_notice(state, clock() - self._usb_since if state != ABSENT else 0,
                                  normal_s)
        for sec in self.sections.values():
            sec.set_empty(*notice)
        self.start.set_empty(*notice)  # the start page says it on top
        if notice[0] != self._usb_title:
            self._usb_title = notice[0]
            self._update_actions()  # the Sync button says the same as the pages
        look_again_for_drives(self, clock)
        return True

    def refresh_device(self, cause: Cause | None = None, read_list: bool = False,
                       reopen: str | None = None) -> None:
        """Detect the device anew; with several devices connected, the one open now is
        opened again when it is still there. `cause`: why (navigation.Cause); none said: a
        detection asked again (Refresh and the like), or the one that waited for the
        running task. `read_list`: once a device is open, the list of devices connected is
        read again (another device came as well; asked by the timer, which itself waits for
        a running task). `reopen`: the address of the device open before its session was
        dropped (a change of account), opened again as the open one would be."""
        if self.busy:
            # E.g. the watch was replugged during a task: detect it once the task ends.
            log.info("device detection: after the running task")
            self._refresh_pending = True
            if cause is not None:
                self._pending_cause = cause  # the last one asked, as it was asked
            if reopen:
                self._pending_reopen = reopen
            return
        self._refresh_pending = False
        reopen = reopen or getattr(self, "_pending_reopen", None)
        self._pending_reopen = None
        pending, self._pending_cause = getattr(self, "_pending_cause", None), None
        if pending is Cause.REFRESH_ON_START:
            cause = pending  # Refresh on the start page waited for the task: it stays there
        elif cause is None:
            cause = pending or Cause.REFRESH_IN_VIEW
        # Why, and the device open now: where the user goes once it is done (navigation).
        self._asked = (cause, open_address(self.session) or reopen)
        log.info("device detection: %s", "again, a device is open" if self.session is not None
                 else "no device is open")
        # The device to open again when it is still there, among several (None: none).
        current = getattr(self.session, "storage", None)
        current_uri = getattr(current, "device_uri", None) or reopen

        def done(result):
            if isinstance(result, DeviceChoices):
                log.info("device detection: %d devices, a list to choose from",
                         len(result.summaries))
                self.session = None
                self.connected.listing(result.summaries, garmin_products_on_usb())
                self.show_choices()
                return
            log.info("device detection: %s, all pages are loaded anew",
                     _same_device(self.session, result))
            if current_uri is None:
                # With no device to open again, a session means the one device connected:
                # the devices listed before are gone (e.g. one of two unplugged).
                self.connected.found_alone()
            else:
                self.connected.stop_choosing()
            self.session = result
            self.show_device()
            if read_list and self.session is result:
                self._reread_cause = cause
                self._reread_connected()

        def failed(_e):
            self.session = None
            self.connected.clear()
            self.show_no_device()
            return True

        if self.session is None:
            self.sections["watch"].show("loading")
        # A Garmin device in mass storage mode too, when no MTP device is there or on its way;
        # several different devices give a list to choose from.
        self.run_task(_("Detecting the device…"),
                      lambda: with_space(detect_devices(current_uri)), done, failed)

    def show_choices(self) -> None:
        """Several Garmin devices are connected: the start page shows a tile for each (never
        an address), the device pages say to choose one. Nothing is opened until one is
        chosen."""
        self.maps_cache = self.updates_cache = None
        self.map_cleanup = []
        self.update_state = self.map_state = None
        self.update_checked_at = self._update_record_at = None
        self.apps_count = self.music_count = None
        for sec in self.sections.values():
            sec.loaded = False
            sec.clear()
            sec.set_empty(_("Choose a device"),
                          _("Several Garmin devices are connected. Choose one on the "
                            "Device page."), False)
            sec.show("empty")
        self._usb_title = _("Choose a device")  # the Sync button says the same
        self._update_banner()
        self._update_actions()
        resolve_account(self)
        self._show_start()

    def open_choice(self, found) -> None:
        """The device chosen from the list, opened as if it were the only one."""
        if self.busy:
            self.toast(_("Another operation is in progress"))
            return

        mounted_now = not getattr(found, "mounted", True)  # a drive mounted once chosen

        def done(sess):
            log.info("device detection: the device chosen, all pages are loaded anew")
            self.connected.stop_choosing()
            self.session = sess
            if mounted_now:
                # Its entry had no details: the open one takes its place, so that the others
                # stay connected (the list was dropped, 0.7.1 on GNOME).
                self.connected.opened_in_place(found, session_summary(sess))
            self.show_device()
            if mounted_now:
                # Then all of them read again, as Refresh on the start page does.
                log.info("device detection: a drive mounted from its tile, the list read again")
                self._reread_cause = Cause.REFRESH_ON_START
                self._reread_connected()

        def failed(e):
            # Unplugged meanwhile, or not readable: said, and the devices detected again.
            self.error(_("Could not open the device"), str(e) or type(e).__name__)
            self.connected.stop_choosing()
            self.refresh_device(cause=Cause.CHOICE_FAILED)
            return True

        self.run_task(_("Opening the device…"),
                      lambda: with_space(service.check_session(service.open_session(
                          found=found))), done, failed)

    # --- the start page and the remembered devices (D7) -----------------------------

    def _on_start_page(self) -> bool:
        return self.navigation.get_visible_page() is self.start.page

    def _on_refresh(self) -> None:
        """Refresh (the button, F5). On the start page with a device open: the list is read
        again, nothing else (decision A); without one, a detection that stays there also when
        one device is found (decision 7). On the view of a device not connected: only that
        view (_refresh_remembered). In the device view, as before."""
        if self._on_remembered_page():
            self._refresh_remembered()
            return
        on_start = self._on_start_page()
        cause = Cause.REFRESH_ON_START if on_start else Cause.REFRESH_IN_VIEW
        s = self.session
        what = detection_plan(cause, open_label=str(getattr(s.storage, "label", ""))
                              if s is not None else None)
        if isinstance(what, ReadList):
            self._fetch_account()  # the start page lists the account's devices too
            self._reread_cause = cause
            self._reread_connected()
            return
        if on_start:
            self._want_account()  # from the device the detection opens, or asked after it
        self.refresh_device(cause=cause)

    def _reread_connected(self) -> None:
        """Read the devices connected again (their GarminDevice.xml only: nothing opened,
        nothing asked of the account), while one is open; the list is replaced whole and
        drawn once, the old tiles stay until then. Never opens a device view (D7). After the
        running task, if one runs."""
        if self.busy:
            self._reread_pending = True
            return
        s = self.session

        def done(found):
            if self.session is not s:
                return  # closed or replaced meanwhile: drawn by what did that
            log.info("start page: %d devices connected", len(found))
            cause, self._reread_cause = (getattr(self, "_reread_cause", None)
                                         or Cause.UNPLUGGED_OTHER), None
            address = open_address(s)
            before = Seen(self.connected.drawn, address)
            self.connected.reread(found)
            if s is None:  # nothing open, nothing to decide
                if self._on_start_page():
                    self._render_start()
                return
            # The open device stays open; the user goes to the start page only from the view
            # of a remembered device that came now (decision 3).
            decision = decided(self, cause, before, seen_open(s, found))
            if isinstance(decision, ShowStart):
                self.navigation.pop_to_page(self.start.page)  # drawn by _on_popped
            elif isinstance(decision, Stay) and decision.redraw_start:
                self._render_start()

        self.in_background(connected_devices, done)

    def _fetch_account(self) -> None:
        """Read the devices of the Garmin account in the background, if one is logged in;
        when they change, the start page (if shown) is drawn once more, which adds the
        section "On the account" without touching the other tiles. Not logged in, or an
        error: no such section, one line in the log."""
        def work():
            client = account_client()
            if client.auth is None:
                return None
            return client.account_devices()

        def done(devices):
            if devices is None:
                log.info("start page: not logged in, no devices of the account")
            else:
                log.info("start page: %d devices on the account", len(devices))
            self._set_account(devices, why="logged-out" if devices is None else None)

        def failed(e):
            # Only the kind of error: its text may quote the answer.
            log.info("start page: the devices of the account could not be read (%s)",
                     type(e).__name__)
            self._set_account(None, why="failed")

        self.in_background(work, done, failed)

    def _want_account(self) -> None:
        """The account's devices are wanted once the detection that follows is done: the
        device it opens has read them for its ownership check; with none opened they are
        asked then (resolve_account). One request, never two at once."""
        self._account_wanted = True

    def _set_account(self, devices, draw: bool = True, why: str | None = None) -> None:
        """The account's list, however it was read (for the start page, or by the ownership
        check of the device opened); without it, `why` (account_note: "logged-out",
        "failed"; None: not known yet). `draw`: the start page is drawn again if it is shown
        or under a remembered device's view (False: the caller draws)."""
        why = why if devices is None else None
        if devices is not None:
            # What it says of the devices remembered, for their tiles (before they are drawn).
            tile_facts.note_account(devices)
        if devices == self._account:
            if why != getattr(self, "_account_why", None):
                # The same tiles: only the sentence under them changes.
                self._account_why = why
                show_account_note(self, getattr(self, "_start_entries", []))
            return
        self._account, self._account_why = devices, why
        if devices:
            # The kinds it tells of the devices remembered and the addresses of their
            # pictures, kept for when it is not there.
            def remember():
                devicelist.remember_kinds(devices)
                devicelist.remember_picture_addresses(devices)

            self.in_background(remember, lambda _r: None)
        if draw and (self._on_start_page() or self._on_remembered_page()):
            self._render_start()  # under the view of a remembered device, which stays
            if self._on_remembered_page():
                self._redraw_remembered()  # it shows what the account says now

    def _show_start(self) -> None:
        """The start page, as it is now, or the page shown stays (navigation.navigate_after:
        over the view of a remembered device only when a device came or went)."""
        cause, open_before = _take_asked(self, Cause.REFRESH_IN_VIEW)
        shown = self._start_summaries()[0]
        after = Seen(uris(shown), None, unit_ids(shown))
        decision = decided(self, cause, Seen(self.connected.drawn, open_before), after)
        if isinstance(decision, Stay):
            self._render_start()
        else:
            self.navigation.pop_to_page(self.start.page)  # drawn by _on_popped

    def _on_remembered_page(self) -> bool:
        return self.navigation.get_visible_page() is self.remembered.page

    def _show_device_view(self) -> None:
        """The view of the device open now, above the start page (D7), or the page shown
        stays (navigation.navigate_after: e.g. after Refresh on the start page, or a
        detection that found the device open again)."""
        cause, open_before = _take_asked(self, Cause.CHOSEN)
        decision = decided(self, cause, Seen(self.connected.drawn, open_before),
                           seen_open(self.session, self.connected.devices))
        visible = self.navigation.get_visible_page()
        if isinstance(decision, Stay):
            if decision.redraw_start:  # the start page shown, or under a remembered device
                self._render_start()
            return
        if isinstance(decision, ShowStart):
            self.navigation.pop_to_page(self.start.page)
            return
        if visible is not self.start.page:
            # The read-only view of a remembered device: the device view takes its place.
            self.navigation.pop_to_page(self.start.page)
        if not decision.animate:  # the window just opened: straight to the device
            self.navigation.set_animate_transitions(False)
            self.navigation.push(self.device_view)
            self.navigation.set_animate_transitions(True)
            return
        self.navigation.push(self.device_view)

    def _on_popped(self, _navigation, page) -> None:
        """Back from the device view or from the read-only view of a remembered device (the
        arrow, Alt+Left, a swipe, or the window itself): the open device stays open; the
        start page is drawn anew (the same tiles keep their widgets)."""
        if page is not self.start.page:
            self._render_start()

    def _render_start(self) -> None:
        """Drawn once, from what is known: with no device open, the devices to choose from,
        or none and the notice of the USB state; with one open, the devices connected as last
        known, the open one among them. Nothing is read here."""
        self._show_tiles(*self._start_summaries())

    def _start_summaries(self) -> tuple[list, bool]:
        """The devices connected as known now, for the start page, and whether the notice of
        the USB state goes with them."""
        s = self.session
        if s is None:
            return self.connected.for_start()
        return self.connected.for_start(session_summary(s), self._is_open)

    def _is_open(self, summary) -> bool:
        """Whether an entry is the device open now: by the unit ID, else by the address
        (both compared in memory only)."""
        s = self.session
        if s is None:
            return False
        unit_id = getattr(s.device, "unit_id", None)
        if unit_id and summary.unit_id is not None:
            return summary.unit_id == unit_id
        uri = str(getattr(s.storage, "device_uri", "") or "").rstrip("/")
        return bool(uri) and summary.found.uri.rstrip("/") == uri

    def _show_tiles(self, summaries: list, notice: bool) -> None:
        """The tiles of these devices, then of those remembered and not connected (from
        devices.json, the only thing read here); what each one opens is kept here, not in
        the tile: OPEN, the FoundDevice to open, or the KnownDevice to show read-only."""
        self.connected.drew(summaries)
        entries = devicelist.entries(summaries, known_devices.load(), self._account,
                                     logged_in=self._account is not None)
        self._start_entries = [
            (tile, source if isinstance(source, KnownDevice | dict)
             else OPEN if self._is_open(source) else source.found)
            for tile, source in entries]
        texts = [tile_subtitle(source) for _tile, source in entries]
        # What each tile says, told against now for a remembered one: drawn again when it
        # would say something else (_refresh_relative_texts).
        self._start_texts = [(source, text) for (_tile, source), text in zip(entries, texts)]
        self.start.show([(tile, text) for (tile, _source), text in zip(entries, texts)],
                        notice)
        show_account_note(self, entries)
        self._load_tile_pictures([tile for tile, _source in entries])
        self._count_activities(entries)

    def _count_activities(self, entries) -> None:
        """The activities not uploaded yet of the connected devices of the account whose tiles
        were just drawn (PLAN T9), each counted in the background, once while it stays listed
        or open (the one open: for its session); its tile shows no chip until the count
        comes (_activities_counted). Never a drive, a device off the account or one whose
        check against the account failed."""
        s = self.session
        for tile, source in entries:
            if not (isinstance(source, DeviceSummary) and tile.connected and tile.on_account
                    and tile.kind == "mtp"):
                continue
            if s is not None and self._is_open(source):
                if (getattr(s, "mass_storage", False)
                        or getattr(s, "ownership_error", None) is not None):
                    continue
                holder, device, storage = s, s.device, s.storage
            else:
                holder, device, storage = source, source.device, source.storage
            if getattr(holder, "to_sync_asked", True) or device is None or storage is None:
                continue
            holder.to_sync_asked = True
            self.in_background(
                lambda device=device, storage=storage: sync.activities_to_sync(device, storage),
                lambda result, holder=holder: self._activities_counted(holder, result))

    def _activities_counted(self, holder, result) -> None:
        """A count of the background (_count_activities) for its device: kept, against
        state.json as it is now (a sync may have ended meanwhile), and only the row of chips
        of its tile changed; dropped when the device is no longer listed or open (unplugged,
        listed anew, another one opened)."""
        s = self.session
        if holder is not s and not any(c is holder for c in self.connected.devices):
            log.info("activities: the count of a device no longer listed or open is dropped")
            return
        holder.to_sync = result.again() if result is not None else None
        remember_sync_count(holder.to_sync)  # for its tile from the first frame next time
        if holder is s:  # its tile may come from the list as well
            for c in self.connected.devices:
                if self._is_open(c):
                    c.to_sync = holder.to_sync
            refresh_last_sync_subtitle(self)  # its page says the count too
        self._update_tile_chips()

    def _update_tile_chips(self) -> None:
        """The chips of the tiles drawn, as the facts kept with the devices say them now (a
        source in the background wrote them: tile_facts): each row of chips that changed
        alone, the page not drawn again. Nothing when other tiles would be drawn now (the
        next drawing shows them)."""
        summaries, _notice = self._start_summaries()
        entries = devicelist.entries(summaries, known_devices.load(), self._account,
                                     logged_in=self._account is not None)
        drawn = self._start_entries
        if len(entries) != len(drawn) or any(
                without_facts(tile) != without_facts(old)
                for (tile, _source), (old, _target) in zip(entries, drawn, strict=True)):
            return
        for key, ((tile, _source), (old, target)) in enumerate(zip(entries, drawn,
                                                                    strict=True)):
            if start.chips(tile) != start.chips(old):
                self.start.set_chips(key, tile)
            drawn[key] = (tile, target)

    def _update_relative_timer(self) -> None:
        """A timer once a minute while the start page, the device view or the view of a device
        not connected is shown (their texts are told against now); none on another page, nor
        while the window is not on the screen or once it is closed."""
        visible = self.navigation.get_visible_page()
        wanted = (self.get_mapped() and not getattr(self, "_closed", False)
                  and visible in (self.start.page, self.device_view, self.remembered.page))
        timer = getattr(self, "_relative_timer", None)
        if wanted and timer is None:
            self._relative_timer = GLib.timeout_add_seconds(RELATIVE_REFRESH_S,
                                                            self._refresh_relative_texts)
        elif not wanted and timer is not None:
            GLib.source_remove(timer)
            self._relative_timer = None

    def _refresh_relative_texts(self) -> bool:
        """Once a minute: the texts told against now drawn again where they changed: the
        tiles of the start page (all of them, once), the row of the last sync (its words),
        "Device software" when it says when its check was; the view of a device not
        connected (_refresh_remembered_texts)."""
        visible = self.navigation.get_visible_page()
        if visible is self.start.page:
            if any(tile_subtitle(source) != text
                   for source, text in getattr(self, "_start_texts", [])):
                self._render_start()
        elif visible is self.device_view:
            refresh_last_sync_subtitle(self)
            if getattr(self, "update_checked_at", None) is not None:
                self._render_update_state()
        elif visible is self.remembered.page:
            self._refresh_remembered_texts()
        return True

    def _load_tile_pictures(self, tiles: list) -> None:
        """The pictures of the tiles (`pictures.with_device_picture`), put into the tile
        there."""
        for tile in tiles:
            if tile.image_part_number:
                with_device_picture(self, tile.image_part_number, tile.image_url,
                                    lambda texture, source, part=tile.image_part_number:
                                        self._tile_picture_done(part, texture, source),
                                    lambda part, url, source=tile.image_source:
                                        tile_picture(part, url, source))

    def _tile_picture_done(self, part: str, texture, source: str) -> None:
        if source != "memory":
            log.info("start page: picture of %s from %s", part, source)
        self.start.set_picture(part, texture)

    def _show_page_picture(self, part: str, url: str | None, category, name: str,
                           source: str | None = None) -> None:
        """The image of the page of a device that is not connected, as its tile has it: the
        icon of its kind at once (never the program's; by the model name when Garmin says
        none), then its picture, if any; a picture that comes after another device was
        opened is not shown."""
        self.remembered.show_icon(part, kind_icon(devicelist.icon_kind_of(category, name)))

        def use(texture, source):
            if self.remembered.set_picture(part, texture):
                log.info("device page: picture of %s from %s", part, source)

        if part:
            with_device_picture(self, part, url, use,
                                lambda part, url: tile_picture(part, url, source))

    def _open_tile(self, index: int) -> None:
        """A tile clicked (its place in _start_entries): the open device's view, that device
        opened, or the read-only view of a device not connected."""
        if not 0 <= index < len(self._start_entries):
            return
        tile, target = self._start_entries[index]
        if target is OPEN:
            self._show_device_view()
        elif isinstance(target, KnownDevice):
            self._show_remembered(target, tile)
        elif isinstance(target, dict):
            self._show_account(target)
        else:
            self.open_choice(target)

    def _last_sync_text(self, unit_id) -> str:
        """The last sync of a device from this computer, in the words of the device page."""
        data = service.load_last_sync(unit_id) if unit_id else None
        return last_sync_subtitle(unit_id, data, now=datetime.now())

    def _show_account(self, entry: dict) -> None:
        """The read-only view of a device only on the account, with what the account says
        (no version or part number when it says none). Nothing is opened or asked."""
        if not self._on_start_page():
            return
        self._fill_account(entry)
        self.navigation.push(self.remembered.page)

    def _fill_account(self, entry: dict) -> None:
        """The read-only view filled with what the account says of a device (_show_account,
        and drawn again by _redraw_remembered)."""
        fields = devicelist.account_fields(entry)
        version = fields["software_version"]
        self._remembered_entry = None  # nothing of it is kept here
        self.remembered.show(
            name=fields["description"],
            version=software_text(version, from_account=True) if version else "",
            part_number=fields["part_number"],
            last_seen=_("Not seen on this computer"),
            last_sync=self._last_sync_text(service.unit_id_of(entry.get("unitid"))))
        show_remembered_more(self, service.unit_id_of(entry.get("unitid")))
        self._show_page_picture(fields["part_number"], fields["image_url"], fields["category"],
                                fields["description"])
        self._remembered_unit = service.unit_id_of(entry.get("unitid"))
        self._remembered_from_account = True
        self.remembered.allow_forget(False)  # nothing of it is kept here

    def _show_remembered(self, known, tile=None) -> None:
        """The read-only view of a device remembered and not connected: what was known when
        it was last seen, and its last sync, from this computer only; its image as its
        `tile` has it (the account's picture and kind, if it is on the account). Nothing is
        opened or asked but that picture; the open device (if any) and the detection are not
        touched."""
        if not self._on_start_page():
            return
        self._fill_remembered(known, tile)
        self.navigation.push(self.remembered.page)

    def _fill_remembered(self, known, tile=None) -> None:
        """The read-only view filled for a device remembered (_show_remembered, and drawn
        again by _redraw_remembered)."""
        self._remembered_entry = known
        self.remembered.show(
            name=known.description,
            version=software_text(known.software_version),
            part_number=known.part_number,
            seen_tooltip=last_seen_tooltip(known.last_seen),
            **remembered_texts(self, known))
        show_remembered_more(self, known.unit_id)
        if tile is not None:
            self._show_page_picture(tile.image_part_number, tile.image_url, tile.category,
                                    tile.description, tile.image_source)
        else:
            self._show_page_picture(known.part_number, known.image_url, known.category,
                                    known.description, devicelist.PICTURE_REMEMBERED)
        self._remembered_unit = known.unit_id
        self._remembered_from_account = False
        self.remembered.allow_forget(True)

    def _refresh_remembered_texts(self) -> None:
        """Once a minute on the view of a device not connected: its texts told against now,
        only where their words changed (of a device only on the account: its last sync)."""
        known = getattr(self, "_remembered_entry", None)
        if known is not None:
            self.remembered.show_texts(**remembered_texts(self, known))
        else:
            row = self.remembered.last_sync_row
            text = GLib.markup_escape_text(self._last_sync_text(self._remembered_unit))
            if row.get_subtitle() != text:
                row.set_subtitle(text)

    def _refresh_remembered(self) -> None:
        """Refresh (F5, Ctrl+R) on the read-only view of a device not connected: only that
        view (decision of 23.10.2026). The account's list is read again and the view drawn
        from what it says now (_set_account, _redraw_remembered); the device open, if any,
        is not opened again and nothing is detected."""
        log.info("refresh: the view of a device not connected, from the account")
        self._fetch_account()

    def _redraw_remembered(self) -> None:
        """The view of a device not connected drawn again from its entry on the start page as
        it is now (the account's list changed): the same device, by its unit ID. No longer
        there as one not connected (e.g. plugged in meanwhile): the view stays as it is."""
        unit = self._remembered_unit
        for tile, target in self._start_entries:
            if isinstance(target, KnownDevice) and target.unit_id == unit:
                self._fill_remembered(target, tile)
                return
            if isinstance(target, dict) and service.unit_id_of(target.get("unitid")) == unit:
                self._fill_account(target)
                return


GC_INTERVAL_S = 30


def collect_garbage_on_the_gtk_thread() -> None:
    """Python frees reference cycles whenever enough objects were created, in whichever
    thread happens to run then. A cycle may hold GTK widgets, and those must not be destroyed
    in a worker thread. So the automatic collection is turned off for the window and done
    here instead: after every task and every GC_INTERVAL_S seconds, from the main loop."""
    gc.disable()
    GLib.timeout_add_seconds(GC_INTERVAL_S, collect_garbage)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        collect_garbage_on_the_gtk_thread()
        # Lets the icon work also when running from the source tree (not installed).
        from gi.repository import Gdk

        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            str(Path(__file__).resolve().parent.parent / "data"))
        Gtk.Window.set_default_icon_name(APP_ID)
        start.load_css(Gdk.Display.get_default())

        def window_call(name):
            return lambda *_a: getattr(self.main_window(), name)()

        actions = {"preferences": window_call("show_preferences"),
                   "report": window_call("show_report"),
                   "about": window_call("show_about"),
                   "quit": lambda *_a: self.request_quit()}
        if hasattr(Adw, "ShortcutsDialog"):
            actions["shortcuts"] = window_call("show_shortcuts")
        for name, cb in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
        for action, accels in (("app.quit", ["<Control>q"]),
                               ("app.preferences", ["<Control>comma"]),
                               ("app.shortcuts", ["<Control>question"]),
                               ("win.refresh", ["<Control>r", "F5"]),
                               ("win.sync", ["<Control>s"])):
            self.set_accels_for_action(action, accels)

    def main_window(self, create: bool = True) -> MainWindow | None:
        wins = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if wins:
            return wins[0]
        return MainWindow(self) if create else None

    def request_quit(self) -> None:
        win = self.main_window(create=False)
        if win is not None and win.busy:
            win.close()  # refused by close-request, which explains why
            return
        if win is not None:
            # quit() skips close-request: the size is saved as when the window is closed.
            win._remember_geometry()
        self.quit()

    def do_activate(self):
        self.main_window().present()


def install_excepthook() -> None:
    """The window's exceptions nobody caught (in a handler of a signal, a callback of GLib:
    PyGObject hands those to sys.excepthook too) go to the log; to stderr as before. Ctrl+C
    is not an error."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            log_unexpected("in the window", (exc_type, exc, tb))
        previous(exc_type, exc, tb)

    sys.excepthook = hook


def missing_display() -> str | None:
    """Why the window has no display to open on, as the variables that choose it tell it
    ("GDK_BACKEND=x11, WAYLAND_DISPLAY=-, DISPLAY=-"); None when GTK has one. GTK tries
    once, as it is imported: Gtk.init_check() tells nothing more after that."""
    if Gdk.Display.get_default() is not None:
        return None
    return ", ".join(f"{name}={os.environ.get(name) or '-'}"
                     for name in ("GDK_BACKEND", "WAYLAND_DISPLAY", "DISPLAY"))


def main(argv=None) -> int:
    told = missing_display()
    if told is not None:
        # One line, before anything is built on a display that is not there.
        log.info("no display: %s", told)
        print_error(_("No display available ({details}): openExpress needs a graphical session")
               .format(details=told))
        return 1
    install_excepthook()
    return App().run(argv or [])


if __name__ == "__main__":
    raise SystemExit(main())
