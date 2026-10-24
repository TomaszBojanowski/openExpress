"""openexpress graphical interface (GTK 4 + libadwaita)."""

from __future__ import annotations

import dataclasses
import gc
import json
import logging
import os
import sys
import threading
import time
import traceback
import types
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

from openexpress import (  # noqa: E402
    cache,
    devicelist,
    download,
    i18n,
    known_devices,
    last_sync,
    netstatus,
    paths,
    picture_urls,
    service,
    sync,
)
from openexpress.auth import sso, store  # noqa: E402
from openexpress.cli import FINISH_STEPS_WINDOW, _error, _human, _seen_at  # noqa: E402
from openexpress.connect import NotAccountDevice, OwnershipError  # noqa: E402
from openexpress.device.gio_storage import (  # noqa: E402
    ABSENT,
    MASS_STORAGE,
    NO_DEVICE_FILE,
    NOT_MOUNTED,
    PRESENT,
    USB_GAP_S,
    WAIT_FOR_DEVICE_S,
    DeviceSummary,
    DriveNotMounted,
    FoundDevice,
    NotThereYet,
    SeveralDevices,
    distinct_devices,
    drive_notice_text,
    eject,
    ejected_by_us,
    ejected_drive_waits,
    ejected_text,
    find_all_devices,
    garmin_products_on_usb,
    garmin_ums,
    garmin_usb,
    held_elsewhere_text,
    mass_storage_text,
    mtp_missing,
    no_mtp_text,
    no_mtp_title,
    not_ejected_text,
    not_ready_text,
    own_remount,
    probe,
    usb_state,
    usb_still_there,
)
from openexpress.gui import pictures, start  # noqa: E402
from openexpress.gui.connected import Connected, unit_ids, uris  # noqa: E402
from openexpress.gui.maps_page import MapsPage  # noqa: E402
from openexpress.gui.music_page import MusicPage  # noqa: E402
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
    NET_BAD,
    NET_COLOUR,
    NET_COLOURS,
    NET_POPOVER_WIDTH,
    NET_ROW_COLOUR,
    NetStatusPanel,
    _dot,
    _paint,
    _render_net_status_later,
    _render_net_status_on_show,
    _retry_from_net_status,
)
from openexpress.gui.pictures import device_picture_path, with_device_picture  # noqa: E402
from openexpress.gui.start import RememberedPage, StartPage, kind_icon  # noqa: E402
from openexpress.gui.tasks import read_again_after_failure, writes_to_device  # noqa: E402
from openexpress.i18n import N_, _, ngettext, pgettext  # noqa: E402
from openexpress.known_devices import KnownDevice  # noqa: E402
from openexpress.progress import Waiting  # noqa: E402
from openexpress.settings import (  # noqa: E402
    Settings,
    SetupError,
    extract_consumer_from_dll,
    extract_url_salt_from_dll,
    save_consumer,
)

APP_ID = "io.github.tomaszbojanowski.OpenExpress"
USB_POLL_S = 2
# A device this long on USB in its normal mode without being offered over MTP: the page adds
# that it may be plugged in again (gvfs did not mount an Edge 1050 restarted with its cable
# in; plugging it in again helped, 21.10.2026). Only the words change, not the detection.
REPLUG_HINT_S = 15
# TRANSLATORS: added to the page while a device on USB is not offered over MTP
REPLUG_HINT = N_("If the device isn't locked and isn't asking for anything, unplug the cable and "
                 "plug it back in.")
# TRANSLATORS: the same, after the sentence about a PIN, which names the device already
REPLUG_HINT_AFTER_PIN = N_("If it isn't locked and isn't asking for anything, unplug the cable "
                           "and plug it back in.")
# Between two versions ("23.39 → 23.40"): no-break spaces keep them on one line.
ARROW = "\u00a0→\u00a0"
# The captions of the window (a hard space before "·"): in i18n, for last_sync too.
SEPARATOR, caption = i18n.SEPARATOR, i18n.caption
log = logging.getLogger(__name__)


def gui_settings_path() -> Path:
    return paths.config_dir() / "gui.json"


def load_gui_settings() -> dict:
    """The window's own settings; {} when there are none, or when the file is not an
    object (edited by hand): the defaults then, never a window that does not start."""
    p = gui_settings_path()
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_gui_settings(data: dict) -> None:
    paths.write_private(gui_settings_path(), json.dumps(data, indent=2))


DEFAULT_WINDOW = (1000, 740)
MIN_WINDOW = (360, 480)
# How often the texts told against now ("2 min ago") are looked at again, while shown.
RELATIVE_REFRESH_S = 60
MAX_WINDOW = 10000
WAIT_PULSE_MS = 150  # how often the bar moves while the program waits for a server
LONG_WAIT_S = 20  # after this, the label says that the wait can take a while


def window_geometry(settings: dict) -> tuple[int, int, bool]:
    """Saved window size and maximized state, validated (falls back to the defaults)."""
    saved = settings.get("window")
    if not isinstance(saved, dict):
        return (*DEFAULT_WINDOW, False)

    def size(key: str, default: int, minimum: int) -> int:
        value = saved.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return max(minimum, min(MAX_WINDOW, value))

    return (size("width", DEFAULT_WINDOW[0], MIN_WINDOW[0]),
            size("height", DEFAULT_WINDOW[1], MIN_WINDOW[1]),
            saved.get("maximized") is True)


def save_window_geometry(width: int, height: int, maximized: bool) -> None:
    """Stores the window state, re-reading gui.json so other saved options are kept."""
    data = load_gui_settings()
    data["window"] = {"width": int(width), "height": int(height), "maximized": bool(maximized)}
    save_gui_settings(data)


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
        data = load_gui_settings()
        data[cls.KEY] = view
        save_gui_settings(data)

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


def _settings_json() -> Path:
    return paths.config_dir() / "settings.json"


def load_ui_language() -> str:
    try:
        return json.loads(_settings_json().read_text()).get("ui_language") or ""
    except (OSError, ValueError):
        return ""


def save_ui_language(code: str) -> None:
    """Store the language in settings.json, keeping the other keys."""
    p = _settings_json()
    data = json.loads(p.read_text()) if p.exists() else {}
    data["ui_language"] = code
    paths.write_private(p, json.dumps(data, indent=2))


def last_sync_words(data: dict, now: datetime | None = None) -> str:
    """The row under "Last sync": when, told against now ("yesterday, 23:41"), and the
    numbers of that sync (last_sync.describe). Now is the window's clock."""
    return last_sync.describe(data).row(now or datetime.now())


def picture_target(name: str) -> Path | None:
    """cache/images/<name>, the name from a server (an app ID, a part number): None when it
    is no plain file name or would leave the cache (nothing is saved then)."""
    images = paths.cache_dir() / "images"
    if not paths.is_safe_component(name) or not paths.is_inside(images / name, images):
        log.info("picture: a name from the server is no file name here, not saved")
        return None
    return images / name


def cached_download(http, url: str, name: str) -> Path | None:
    """Downloads a small file (picture, icon) once into the cache."""
    target = picture_target(name)
    if target is None:
        return None
    if target.exists() and target.stat().st_size:
        return target
    try:
        url = download.secure_url(url)  # HTTPS only, as every download
    except download.DownloadError:
        log.info("picture: not an HTTPS address, not asked")
        return None
    try:
        resp = http.get(url, timeout=20)
        if not resp.ok or not resp.content:
            return None
    except Exception:  # noqa: BLE001 - pictures are optional
        return None
    with cache.using():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
    return target


DEVICE_PICTURE_SIZE = 180


# A picture of a device model is taken only from an address picture_urls allows, as a PNG or
# a JPEG (its type and its first bytes agree), small and quickly, or the generic icon stays.
PICTURE_MAX_BYTES = 2 * 1024 * 1024
PICTURE_TIMEOUT_S = 15
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PICTURE_SIGNATURES = {"image/png": PNG_SIGNATURE, "image/jpeg": b"\xff\xd8\xff"}


def picture_from_unit_info(client, device) -> str | None:
    """The address of the device's picture according to Garmin's update server. The rest of
    the answer (unit ID, serial number…) is dropped here and goes nowhere."""
    try:
        info = client.unit_info(device.unit_id, device.part_number)
    except Exception as e:  # noqa: BLE001 - optional; the error may quote the answer
        log.info("device picture: unit-info failed (%s)", type(e).__name__)
        return None
    note_segment(device, info)
    url = info.get("graphicurl") if isinstance(info, dict) else None
    return url if isinstance(url, str) and url else None


def note_segment(device, info) -> None:
    """Keep the kind Garmin's update server tells in an answer the window asked for (for a
    picture, after an update, for a drive whose kind is not told): a bike accessory (e.g. a
    Varia). Only this field and its connect support are read. A segment that tells no kind
    the program knows goes to the log with the part number and the connect support
    (devicelist.note_kind_evidence)."""
    segment = info.get("segmenttype") if isinstance(info, dict) else None
    bike_accessory = isinstance(segment, str) and segment.casefold() == "bikeaccessory"
    if bike_accessory:
        known_devices.set_category(device.unit_id, known_devices.BIKE_ACCESSORY)
    if isinstance(info, dict):
        support = info.get("connectsupport")
        devicelist.note_kind_evidence(
            getattr(device, "part_number", "") or "",
            segment=segment if isinstance(segment, str) and segment else "none given",
            kind=known_devices.BIKE_ACCESSORY if bike_accessory else None,
            connect_support=support if isinstance(support, str) and support else "none given")


def is_bike_accessory(client, device) -> bool:
    """Whether Garmin's update server files the device as a bike accessory (e.g. a Varia),
    which finishes an update once it is unplugged and switched on. Only this field of the
    answer is read; the rest (unit ID, serial number…) goes nowhere."""
    try:
        info = client.unit_info(device.unit_id, device.part_number)
    except Exception as e:  # noqa: BLE001 - only for a note after the update; may quote the answer
        log.info("software update: unit-info failed (%s)", type(e).__name__)
        return False
    note_segment(device, info)
    segment = info.get("segmenttype") if isinstance(info, dict) else None
    return isinstance(segment, str) and segment.casefold() == "bikeaccessory"


def bike_accessory_text() -> str:
    """After an update was saved on a bike accessory: how the device finishes it."""
    return _("Unplug the device and turn it on — it installs the update on its own and won't "
             "work for a short while. The lights flash and may briefly go dark during the "
             "install; when it's done, a light flashes green and the main light comes on.")


def download_picture(http, url: str, name: str,
                     clock=time.monotonic) -> tuple[Path | None, str]:
    """Download a device picture into the cache: (file, "") or (None, why not). The file
    keeps its name whether it is a PNG or a JPEG: GTK reads a picture by its content."""
    target = picture_target(name)
    if target is None:
        return None, "not a file name"
    url = picture_urls.upgraded(url)  # plain HTTP over HTTPS, as every download
    why = picture_urls.refusal(url)  # the host at most, never the whole address
    if why is not None:
        said = picture_urls.query_said(url)  # whether it has a query, never what
        return None, f"{why}; {said}" if said else why
    deadline = clock() + PICTURE_TIMEOUT_S
    data = bytearray()
    try:
        with http.get(url, timeout=PICTURE_TIMEOUT_S, stream=True,
                      allow_redirects=False) as resp:
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            kind = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            signature = PICTURE_SIGNATURES.get(kind)
            if signature is None:
                return None, "not a PNG or JPEG"
            length = resp.headers.get("Content-Length") or ""
            if length.isdigit() and int(length) > PICTURE_MAX_BYTES:
                return None, "too large"
            for chunk in resp.iter_content(64 * 1024):
                data += chunk
                if len(data) > PICTURE_MAX_BYTES:
                    return None, "too large"
                if clock() > deadline:
                    return None, "timeout"
    except Exception as e:  # noqa: BLE001 - pictures are optional
        return None, "timeout" if "Timeout" in type(e).__name__ else "network error"
    if not bytes(data).startswith(signature):
        return None, "not a PNG or JPEG"
    try:
        with cache.using():
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            partial.write_bytes(data)
            partial.replace(target)  # never a half-written picture in the cache
    except OSError:
        return None, "cannot write the cache"
    return target, ""


def load_texture(path: Path):
    """Picture as a texture, None when the file is no picture GTK can read (GTK reads PNG,
    JPEG and TIFF itself and other formats, e.g. GIF, through its own loaders). The size is
    left to the image that shows it: Gtk.Image fits a paintable to its pixel size."""
    from gi.repository import Gdk

    try:
        return Gdk.Texture.new_from_filename(str(path))
    except GLib.Error:
        return None


def trash_button(tooltip: str, cb) -> Gtk.Button:
    b = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.add_css_class("flat")
    b.connect("clicked", lambda *_a: cb())
    return b


# A task started from a button: the button spins at once, and the bar at the bottom with the
# words of the task comes only when the task takes longer than this; a quick one (a fast
# device) shows no text that flashes and is gone.
TEXT_AFTER_MS = 500


def show_working(button: Gtk.Button):
    """The button spins instead of showing its label or icon and cannot be clicked; returns
    the function that puts it back (clickable or not). The label or icon stays under the
    spinner, unseen, so the button keeps its size and nothing beside it moves."""
    label, icon = button.get_label(), button.get_icon_name()
    if label is None and icon is None:  # neither: it only goes grey
        button.set_sensitive(False)
        return button.set_sensitive
    ghost = (Gtk.Label(label=label, use_underline=button.get_use_underline(), opacity=0)
             if label is not None else Gtk.Image(icon_name=icon, opacity=0))
    spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
    spinner.set_halign(Gtk.Align.CENTER)
    spinner.set_valign(Gtk.Align.CENTER)
    overlay = Gtk.Overlay(child=ghost)
    overlay.add_overlay(spinner)
    button.set_child(overlay)
    button.set_sensitive(False)

    def restore(sensitive: bool) -> None:
        if label is not None:
            button.set_label(label)
        else:
            button.set_icon_name(icon)
        button.set_sensitive(sensitive)

    return restore


def still_in_window(buttons: list) -> list:
    """The action buttons that are still in a window: a page that is rebuilt leaves its old
    buttons behind, and the list must not keep them and what they hold."""
    return [b for b in buttons if b.get_root() is not None]


# The pages that do not apply to a Garmin device in mass storage mode.
NOT_FOR_MASS_STORAGE = ("maps", "apps", "music")


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
    if len(found) > 1:
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


def read_space(storage, capacity: int | None = None) -> tuple[int | None, int | None]:
    """(free space, capacity) of the device, asked of it: never on the window's thread. The
    capacity is asked only when it is not given (it does not change). What cannot be asked
    (e.g. while a session of PTP holds the device) is not known, None: no error."""
    def ask(question: str):
        try:
            return getattr(storage, question)()
        except Exception as e:  # noqa: BLE001 - unknown, said as such on the device page
            log.info("device storage: %s not read (%s), not known", question,
                     type(e).__name__)
            return None

    free = ask("free_space")
    return free, capacity if capacity is not None else ask("capacity")


def with_space(found):
    """A device opened for the window, with its free space and capacity read in the same
    thread (the device page and the report then ask it nothing); a list to choose from is
    passed on as it is."""
    if not isinstance(found, DeviceChoices):
        found.space = read_space(found.storage)
    return found


def render_space(win) -> None:
    """The subtitle (and the bar) of "Device storage" from the space the session holds:
    "…" until it is read, "?" for what is not known. Nothing is asked of the device."""
    row, bar = getattr(win, "storage_row", None), getattr(win, "storage_bar", None)
    s = win.session
    if row is None or s is None:
        return
    space = getattr(s, "space", None)
    if space is None:
        row.set_subtitle("…")
        bar.set_visible(False)
        return
    free, total = space
    if free is not None and total:
        row.set_subtitle(_("{free} free of {total}").format(free=_human(free),
                                                             total=_human(total)))
        bar.set_value(min(1.0, max(0.0, 1 - free / total)))
        bar.set_visible(True)
    else:
        row.set_subtitle(_("{size} free").format(size=_human(free)))
        bar.set_visible(False)


def refresh_space(win, s) -> None:
    """The free space of the device of session `s` read again in the background (its
    capacity only while it is not known), then the subtitle of "Device storage" alone put
    right. An answer that comes after the device was unplugged, or another one opened, is
    dropped."""
    if s is None:
        return
    known = getattr(s, "space", None)
    capacity = known[1] if known is not None else None

    def got(space):
        if win.session is not s:
            log.info("device storage: read after the device was closed, not shown")
            return
        s.space = space
        render_space(win)

    MainWindow.in_background(lambda: read_space(s.storage, capacity), got)


def _choose(found: list):
    """The list of these devices, one entry each, without those gone since they were found;
    with one left, that one opened (D7); with none, no device."""
    summaries = distinct_devices([probe(f) for f in found])
    for s in summaries:
        if s.ok:  # its GarminDevice.xml was read: remembered before anything is chosen
            known_devices.seen(s.unit_id, s.description, s.part_number, s.software_version,
                               s.found.kind)
    left = _still_there(summaries)
    if len(left) > 1:
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


def without_sync(tile):
    """A tile without what it says of the sync: to tell whether only that changed."""
    return dataclasses.replace(tile, to_sync=None, never_synced=False, synced_at=None)


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


def cached_texture(part: str):
    """The picture of a part number in the cache, read now; None: no file, or not a
    picture. The one reader of the cache for every view."""
    path = device_picture_path(part)
    return load_texture(path) if path.exists() else None


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


def picture_address_known(device) -> bool:
    """Whether the address of the picture of this device is kept with it already; True also
    for a device that is not remembered (there is nothing to keep it with)."""
    unit_id = getattr(device, "unit_id", None)
    entry = known_devices.load().get(unit_id) if unit_id else None
    return entry is None or entry.image_url is not None


def kind_untold(device) -> bool:
    """Whether Garmin has told no kind of this device yet: none kept with it, and unit-info
    has not answered about its model in this run."""
    unit_id = getattr(device, "unit_id", None)
    entry = known_devices.load().get(unit_id) if unit_id else None
    if entry is not None and entry.category is not None:
        return False
    return not devicelist.unit_info_heard(getattr(device, "part_number", "") or "")


def remember_picture_address(device, url: str | None) -> None:
    """Keep the address of the picture of the open device with it (known_devices), for when
    its picture is no longer in the cache and it is not connected; only an address that may
    be downloaded. Optional: a failure is only written to the log."""
    unit_id = getattr(device, "unit_id", None)
    if not unit_id or not url:
        return
    try:
        known_devices.set_image_url(unit_id, url)
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: the address of a picture not kept (%s)", type(e).__name__)


def remember_update_found(device, found: bool) -> None:
    """Keep with the open device (known_devices) whether the window's check of updates found
    one to install, for its tile on the start page. Optional: a failure is only written to
    the log."""
    unit_id = getattr(device, "unit_id", None)
    if not unit_id:
        return
    try:
        known_devices.set_update_found(unit_id, found)
    except Exception as e:  # noqa: BLE001 - optional; the error may name the folder
        log.warning("devices: what the check of updates found not kept (%s)", type(e).__name__)


def open_device_kind(s, account) -> str | None:
    """The kind of the open device as its tile knows it: the same devicelist.tiles from
    devices.json and the account's list (also for D7, when no tile was shown before).
    Nothing is asked."""
    d = s.device
    unit_id = getattr(d, "unit_id", None) or None
    if unit_id is None:
        return None
    kind = "ums" if getattr(s, "mass_storage", False) else "mtp"
    summary = DeviceSummary(FoundDevice("", "", True, kind=kind), getattr(d, "description", ""),
                            getattr(d, "part_number", ""), "", unit_id)
    tiles = devicelist.tiles([summary], known_devices.load(), account,
                             logged_in=account is not None)
    return next((devicelist.icon_kind(t) for t in tiles if t.connected), None)


def device_view_icon(win, s) -> str:
    """The icon of the device view before the picture: that of the device's kind, from the
    window's account list (or the one its ownership check read) and devices.json."""
    account = getattr(win, "_account", None)
    if account is None:
        account = getattr(s, "account_devices", None)
    return kind_icon(open_device_kind(s, account))


def resolve_account(win) -> None:
    """After a detection: the account's devices for the start page from the device just
    opened (its ownership check read them, also when it is not on the account), else, if
    they are wanted, asked now."""
    s = win.session
    listed = getattr(s, "account_devices", None) if s is not None else None
    if listed is None and s is not None:
        listed = getattr(getattr(s, "ownership_error", None), "devices", None)
    if listed is not None:
        win._account_wanted = False
        # Not asked again; kept as any list of the account is, with the kinds it tells (Q4).
        # The caller draws the page shown, after deciding where the user goes.
        win._set_account(listed, draw=False)
    elif getattr(win, "_account_wanted", False):
        win._account_wanted = False
        win._fetch_account()


def account_client():
    """The client the start page asks for the account's devices: who is logged in now."""
    return service.make_client(Settings.load())


def account_subtitle(fields: dict) -> str:
    """A device only on the account: its version as Garmin Connect knows it (if it says)."""
    seen = _("Not seen on this computer")
    version = fields["software_version"]
    return caption(_("Software {version}").format(version=version), seen) if version else seen


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
    when = i18n.relative_time(datetime.fromtimestamp(known.last_seen), now or datetime.now())
    return caption(_("Software {version}").format(version=known.software_version),
                   _("Last seen {when}").format(when=when))


def show_not_for_mass_storage(sec) -> None:
    """A page that does not apply to a Garmin device in mass storage mode: it loads nothing
    and says why, as the Music page of a device without media does."""
    sec.clear()
    sec.error.set_title(sec.title)
    # The description is markup: words with "<" or "&" would leave it empty.
    sec.error.set_description(GLib.markup_escape_text(mass_storage_text()))
    sec.show("error")


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


def open_address(s) -> str | None:
    """The address of the open device (compared in memory only), or None."""
    if s is None:
        return None
    return str(getattr(getattr(s, "storage", None), "device_uri", "") or "")


def open_unit(s) -> int | None:
    """The unit ID of the open device (compared in memory only, never shown), or None."""
    return getattr(getattr(s, "device", None), "unit_id", None) or None


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


def logged_in_auth():
    """Who is logged in to Garmin Connect now, as the start page asks it; None: nobody."""
    try:
        return account_client().auth
    except SetupError:  # the settings file is damaged: said where it is used
        return None


def render_account_row(win) -> None:
    """The account's row of the preferences, while they are open: who is logged in, with
    Log out, or nobody, with Log In."""
    shown = getattr(win, "_account_row", None)
    if shown is None:
        return
    row, button = shown
    auth = logged_in_auth()
    if auth is None:
        row.set_title(_("Not logged in"))
        button.set_label(_("Log In"))
    else:
        row.set_title(GLib.markup_escape_text(auth.display))
        button.set_label(_("Log out"))


# The question before a part of the cache is cleared: only for those that take long to download
# again; pictures and temporary files go at once.
CACHE_QUESTIONS = {
    "maps": (N_("Clear the downloaded maps?"),
             N_("This deletes the downloaded maps ({size}). They will have to be downloaded "
                "again.")),
    "updates": (N_("Clear the downloaded updates?"),
                N_("This deletes the downloaded software updates ({size}). They are downloaded "
                   "again when needed.")),
}


def _cache_words(size: int, files: int) -> str:
    return caption(_human(size), ngettext("{count} file", "{count} files", files).format(
        count=files))


def show_cache_usage(win, use) -> None:
    """The sizes counted (cache.usage) in the storage group of the preferences, if they are
    still open: for each part and for all of it, its size and files, and its button clickable
    when there is something to clear."""
    shown = getattr(win, "_storage", None)
    if shown is None:
        return  # the preferences closed meanwhile
    shown.usage = use
    for name, (row, trash) in shown.parts.items():
        row.set_subtitle(_cache_words(use.sizes[name], use.files[name]))
        trash.set_sensitive(use.sizes[name] > 0)
    row, button = shown.everything
    row.set_subtitle(_cache_words(use.total, sum(use.files.values())))
    button.set_sensitive(use.total > 0)


def ask_clear_cache_part(win, name: str, button) -> None:
    """The trash of a part of the cache: maps and updates ask first, with their size; the
    others are cleared at once."""
    shown = getattr(win, "_storage", None)
    if shown is None or shown.usage is None:
        return
    if name not in CACHE_QUESTIONS:
        clear_cache(win, [name], button)
        return
    heading, body = CACHE_QUESTIONS[name]
    ask_to_clear(win, _(heading), _(body).format(size=_human(shown.usage.sizes[name])), [name])


def ask_to_clear(win, heading: str, body: str, names: list[str] | None) -> None:
    """Clear (destructive) or Cancel (the default); Clear clears those parts (None: all) from
    their button, found when the answer comes: the handler holds no widget."""
    d = Adw.AlertDialog(heading=heading, body=body)
    d.add_response("cancel", _("Cancel"))
    # TRANSLATORS: button that clears the cache
    d.add_response("clear", _("Clear"))
    d.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
    d.set_default_response("cancel")
    d.set_close_response("cancel")

    def response(_d, r):
        shown = getattr(win, "_storage", None)
        if r != "clear" or shown is None:
            return
        button = shown.everything[1] if names is None else shown.parts[names[0]][1]
        clear_cache(win, names, button)

    d.connect("response", response)
    d.present(win)


def clear_cache(win, names: list[str] | None, button) -> None:
    """Clears those parts of the cache (None: all) in a task started from `button`, as
    `cache --clear --only … --apply` does; then the sizes counted again (in the task too) and
    how much was freed."""
    def work():
        return cache.clear(names), cache.usage()

    def done(result):
        freed, now = result
        show_cache_usage(win, now)
        cache_toast(win, _("Freed {size}.").format(size=_human(freed.total)))

    def failed(e):
        if isinstance(e, cache.CacheError):
            win.error(_("The cache was not cleared"), str(e))
            return True
        return False

    win.run_task(_("Clearing the cache…"), work, done, failed, device=False, button=button)


def cache_toast(win, text: str) -> None:
    """A toast on the preferences while they are open (the window's own is under them)."""
    shown = getattr(win, "_preferences", None)
    if shown is not None:
        shown.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))
    else:
        win.toast(text)


def account_changed(win, cause: Cause = Cause.ACCOUNT_CHANGED) -> None:
    """A change of account (login, logout, the key from the installer): the session made
    with the old one is dropped and the devices detected again. The device open before is
    opened again when it is still there, also among several, and the page shown stays
    (Q3), but for the view of a device of the account after a logout (`cause`
    LOGGED_OUT)."""
    render_account_row(win)
    reopen = open_address(win.session) or None
    win.session = None
    win.refresh_device(cause=cause, reopen=reopen)


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


# Adw.WrapBox is in libadwaita 1.7; with an older one (the window works from 1.5) a FlowBox,
# which puts the widgets in columns across its lines: good for a row of buttons.
WRAP_BOX = getattr(Adw, "WrapBox", None)


def wrapping_row(widgets, spacing: int, **props) -> Gtk.Widget:
    """The widgets side by side and centred, `spacing` apart; those that do not fit the width
    of the window (MIN_WINDOW at the narrowest) go on to the next line, centred too, `spacing`
    below."""
    if WRAP_BOX is not None:
        row = WRAP_BOX(child_spacing=spacing, line_spacing=spacing, align=0.5,
                       halign=Gtk.Align.CENTER, **props)
        for widget in widgets:
            row.append(widget)
        return row
    row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                      column_spacing=spacing, row_spacing=spacing, min_children_per_line=1,
                      max_children_per_line=max(1, len(widgets)), halign=Gtk.Align.CENTER,
                      **props)
    row.add_css_class("oe-wrap")  # no room of its own around each widget (start.CSS)
    for widget in widgets:
        row.append(widget)
        place = widget.get_parent()  # the FlowBoxChild it is put in
        place.set_focusable(False)  # the widget itself takes the focus, as in a box
        place.set_halign(Gtk.Align.CENTER)
    return row


def updates_available(count: int, only=None) -> str:
    """The subtitle of "Device software" with `count` updates to install: of one (`only`),
    the version of a system update or the name and version of another, as the Updates page
    names it; of several (or one not known), how many."""
    if count == 1 and only is not None and only.is_system:
        # TRANSLATORS: subtitle of the row "Device software": the one update to install is
        # of the device's system software
        return _("Version {version} available").format(version=only.version)
    if count == 1 and only is not None:
        # TRANSLATORS: subtitle of the row "Device software": the one update to install, of
        # another part of the device (its name and version, as the server gives them)
        return _("Update available: {name} {version}").format(name=only.title,
                                                               version=only.version)
    return ngettext("{count} update available", "{count} updates available",
                    count).format(count=count)


def row_subtitle(row) -> Gtk.Label | None:
    """The label of the subtitle of an Adw.ActionRow, which libadwaita does not give: the one
    with the style class "subtitle" (so in 1.5 as in 1.9). None when there is none."""
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label) and child.has_css_class("subtitle"):
            return child
        found = row_subtitle(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


class Section:
    """One page of the window: shows a placeholder, a spinner, an error or the content."""

    def __init__(self, key: str, title: str, icon: str, loading_text: str):
        self.key, self.title, self.icon = key, title, icon
        self.loaded = False
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.empty = Adw.StatusPage(
            icon_name=APP_ID, title=_("Connect your device"),
            description=_("Connect your Garmin device with a USB cable. "
                          "It will be detected automatically."))
        self._empty_notice = None
        self.loading = Adw.StatusPage(title=loading_text)
        if hasattr(Adw, "SpinnerPaintable"):
            self.loading.set_paintable(Adw.SpinnerPaintable.new(self.loading))
        self.error = Adw.StatusPage(icon_name="dialog-warning-symbolic")
        self.retry = Gtk.Button(label=_("Try again"), halign=Gtk.Align.CENTER)
        self.retry.add_css_class("pill")
        self.error.set_child(self.retry)
        self.page = Adw.PreferencesPage()
        for name, widget in (("empty", self.empty), ("loading", self.loading),
                             ("error", self.error), ("content", self.page)):
            self.stack.add_named(widget, name)
        # Until the first detection finishes we don't know yet whether a watch is connected.
        self.stack.set_visible_child_name("loading")
        self.groups: list[Adw.PreferencesGroup] = []

    def set_empty(self, title: str, description: str, busy: bool) -> None:
        """What the placeholder says while no device is open."""
        if self._empty_notice == (title, description, busy):
            return
        self._empty_notice = (title, description, busy)
        self.empty.set_title(title)
        self.empty.set_description(description)
        if busy and hasattr(Adw, "SpinnerPaintable"):
            self.empty.set_paintable(Adw.SpinnerPaintable.new(self.empty))
        else:
            self.empty.set_paintable(None)
            self.empty.set_icon_name(APP_ID)

    def show(self, name: str) -> None:
        self.stack.set_visible_child_name(name)

    def show_error(self, text: str) -> None:
        self.error.set_title(_("Something went wrong"))
        self.error.set_description(GLib.markup_escape_text(text))
        self.show("error")

    def clear(self) -> None:
        for g in self.groups:
            self.page.remove(g)
        self.groups = []

    def group(self, title: str = "", description: str = "") -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title=title, description=description)
        self.page.add(g)
        self.groups.append(g)
        return g


def task_name(fn) -> str:
    """The name of a task in the log: the method that made its work ("MainWindow.add_music"),
    also when the method is in a mixin of the window (NetStatusPanel, MusicPage, …)."""
    name = fn.__qualname__.split(".<locals>")[0] if hasattr(fn, "__qualname__") else "?"
    owner, dot, method = name.partition(".")
    mixins = {c.__name__ for c in MainWindow.__mro__[1:] if c.__module__.startswith("openexpress.")}
    return f"MainWindow.{method}" if dot and owner in mixins else name


class MainWindow(MapsPage, MusicPage, NetStatusPanel, Adw.ApplicationWindow):
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
        self._update_check_session = None
        # None = unknown, "none" (no maps for the device), "empty" (none installed),
        # "current", "available", "failed"
        self.map_state: str | None = None
        self.map_update_count = 0
        self._map_check_session = None
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
            return _("Mass storage mode")  # never synced; the device page says why
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

    # --- helpers ----------------------------------------------------------------------

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))

    def error(self, title: str, text: str) -> None:
        d = Adw.AlertDialog(heading=title, body=text)
        d.add_response("ok", _("OK"))
        d.present(self)

    def set_busy(self, busy: bool, text: str = "", device: bool = False,
                 in_page: bool = False) -> None:
        """A task starts or ends. Its indicator is the spinner until it tells its progress,
        then the bar (`_show_bar`, one way); none for a page that shows it loads (`in_page`)."""
        self._stop_waiting()
        self.busy = busy
        self.action_bar.set_revealed(busy)
        self.progress_label.set_text(text)
        self.unplug_label.set_visible(busy and device)
        self.progress_bar.set_fraction(0)
        self.progress_bar.set_visible(False)
        self.busy_spinner.set_visible(busy and not in_page)
        self._action_buttons = still_in_window(self._action_buttons)
        for b in self._action_buttons:
            b.set_sensitive(not busy)
        self.banner.set_sensitive(not busy)
        self._update_actions()

    def progress(self, text: str) -> None:
        GLib.idle_add(self._show_progress, text)

    def fraction(self, done: int, total: int | None) -> None:
        if total:
            GLib.idle_add(self._show_fraction, min(1.0, done / total))

    def _show_bar(self) -> None:
        """The task tells its progress: the bar instead of the spinner, until the task ends."""
        self.busy_spinner.set_visible(False)
        self.progress_bar.set_visible(True)

    def _show_progress(self, text: str) -> bool:
        """A step moves the bar once. A wait for a server (`Waiting`), whose length nobody
        knows, keeps it moving until the next step, and after a while the label says so."""
        self._stop_waiting()
        self._show_bar()
        self.progress_label.set_text(text)
        self.progress_bar.pulse()
        if isinstance(text, Waiting):
            until = time.monotonic() + LONG_WAIT_S
            told = []

            def pulse() -> bool:
                self.progress_bar.pulse()
                if not told and time.monotonic() >= until:
                    told.append(True)
                    self.progress_label.set_text(f"{text} {_('This can take a few minutes.')}")
                return True

            self._waiting = GLib.timeout_add(WAIT_PULSE_MS, pulse)
        return False

    def _show_fraction(self, fraction: float) -> bool:
        self._stop_waiting()
        self._show_bar()
        self.progress_bar.set_fraction(fraction)
        return False

    def _stop_waiting(self) -> None:
        if self._waiting:
            GLib.source_remove(self._waiting)
            self._waiting = 0

    def run_task(self, title: str, fn, on_done=None, on_error=None, device: bool = True,
                 in_page: bool = False, button: Gtk.Button | None = None) -> None:
        """Run `fn()` in a thread; the result is passed back to the GUI thread.

        `device` is False for a task that does not touch the connected device: the user is
        then not told to keep it plugged in. `in_page`: the page shows that it loads, so the
        bottom bar has no indicator of its own. `button`: the task was started from it; it
        spins at once, the rest of the page stays as it is, and the bar with `title` comes
        only after TEXT_AFTER_MS. It is as it was before on_done or on_error runs. A task
        whose work writes to the device (writes_to_device): once it ends (also when it failed:
        a part may be written), its free space is read again in the background."""
        if self.busy:
            self.toast(_("Another operation is in progress"))
            return
        has_device = device and self.session is not None
        started = self.session
        restore, reveal = None, [0]
        if button is None:
            self.set_busy(True, title, device=has_device, in_page=in_page)
        else:
            self.busy = True  # no other task meanwhile
            restore = show_working(button)

            def show_bar() -> bool:
                reveal[0] = 0
                self.set_busy(True, title, device=has_device, in_page=in_page)
                return False

            reveal[0] = GLib.timeout_add(TEXT_AFTER_MS, show_bar)
        log.info("task: %s", task_name(fn))

        def ended():
            if reveal[0]:
                GLib.source_remove(reveal[0])  # done before its words were due
                reveal[0] = 0
            collect_garbage()
            self.set_busy(False)
            if restore is not None:
                restore(True)

        def worker():
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001
                log.debug("%s", traceback.format_exc())
                GLib.idle_add(finish_error, e)
            else:
                GLib.idle_add(finish_ok, result)

        def written() -> None:
            if getattr(fn, "writes_to_device", False) and started is not None \
                    and self.session is started:
                refresh_space(self, started)

        def finish_ok(result):
            ended()
            try:
                if on_done:
                    on_done(result)
            finally:
                written()
                self._run_pending_refresh()
            return False

        def finish_error(e):
            log.warning("task failed: %s: %s", type(e).__name__, e)
            ended()
            try:
                if not (on_error and on_error(e)):
                    self.error(title, str(e) or e.__class__.__name__)
            finally:
                written()
                self._run_pending_refresh()
            return False

        # Not a daemon thread: the process must not exit while the watch is being written.
        threading.Thread(target=worker, daemon=False).start()

    def _run_pending_refresh(self) -> None:
        if self._refresh_pending and not self.busy:
            self._refresh_pending = False
            self.refresh_device()
        elif getattr(self, "_detect_after_task", False) and not self.busy:
            detection_due(self)
        elif getattr(self, "_reread_pending", False) and not self.busy:
            self._reread_pending = False
            self._reread_connected()

    @staticmethod
    def in_background(fn, on_done, on_error=None) -> None:
        """Run optional work (pictures, status checks) without blocking the window."""
        def worker():
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001
                log.debug("%s", traceback.format_exc())
                if on_error is not None:
                    GLib.idle_add(lambda err=e: (on_error(err), False)[1])
                return
            GLib.idle_add(lambda: (on_done(result), False)[1])

        threading.Thread(target=worker, daemon=True).start()

    def _button(self, label: str, cb, style: str | None = None,
                with_button: bool = False) -> Gtk.Button:
        """An action button. `with_button`: `cb` gets the button clicked (the argument of the
        signal; the handler holds no button), e.g. to start its task from it (run_task)."""
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if style:
            b.add_css_class(style)
        if with_button:
            b.connect("clicked", lambda button: cb(button))
        else:
            b.connect("clicked", lambda *_: cb())
        b.set_sensitive(not self.busy)
        self._action_buttons.append(b)
        return b

    def _trash(self, tooltip: str, cb) -> Gtk.Button:
        b = trash_button(tooltip, cb)
        b.set_sensitive(not self.busy)
        self._action_buttons.append(b)
        return b

    @staticmethod
    def _removed_files(count: int) -> str:
        return ngettext("Removed {count} file", "Removed {count} files", count).format(count=count)

    @staticmethod
    def _search_filter(entry: Gtk.SearchEntry, rows: list[tuple[Gtk.Widget, str]],
                       expander: Adw.ExpanderRow | None = None) -> None:
        def changed(e):
            text = e.get_text().casefold()
            for row, haystack in rows:
                row.set_visible(text in haystack)
            if expander is not None and text:
                expander.set_expanded(True)
        entry.connect("search-changed", changed)

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
            return _("Mass storage mode"), text, False
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
            return True
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
        self.update_state = self.map_state = None
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

        def done(sess):
            log.info("device detection: the device chosen, all pages are loaded anew")
            self.connected.stop_choosing()
            self.session = sess
            self.show_device()

        def failed(e):
            # Unplugged meanwhile, or not readable: said, and the devices detected again.
            self.error(_("Could not open the device"), str(e) or type(e).__name__)
            self.connected.stop_choosing()
            self.refresh_device(cause=Cause.CHOICE_FAILED)
            return True

        self.run_task(_("Opening the device…"),
                      lambda: with_space(service.check_session(service.open_session(
                          found=found))), done, failed)

    # --- the start page and the device view (D5, D7) ---------------------------------------

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
            self._set_account(devices)

        def failed(e):
            # Only the kind of error: its text may quote the answer.
            log.info("start page: the devices of the account could not be read (%s)",
                     type(e).__name__)
            self._set_account(None)

        self.in_background(work, done, failed)

    def _want_account(self) -> None:
        """The account's devices are wanted once the detection that follows is done: the
        device it opens has read them for its ownership check; with none opened they are
        asked then (resolve_account). One request, never two at once."""
        self._account_wanted = True

    def _set_account(self, devices, draw: bool = True) -> None:
        """The account's list, however it was read (for the start page, or by the ownership
        check of the device opened). `draw`: the start page is drawn again if it is shown
        or under a remembered device's view (False: the caller draws)."""
        if devices == self._account:
            return
        self._account = devices
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
        if holder is s:  # its tile may come from the list as well
            for c in self.connected.devices:
                if self._is_open(c):
                    c.to_sync = holder.to_sync
        self._update_tile_chips()

    def _update_tile_chips(self) -> None:
        """The chips of the tiles drawn, as the devices' counts say them now: each changed row
        of chips alone, the page not drawn again. Nothing when other tiles would be drawn now
        (the next drawing shows them)."""
        summaries, _notice = self._start_summaries()
        entries = devicelist.entries(summaries, known_devices.load(), self._account,
                                     logged_in=self._account is not None)
        drawn = self._start_entries
        if len(entries) != len(drawn) or any(
                without_sync(tile) != without_sync(old)
                for (tile, _source), (old, _target) in zip(entries, drawn, strict=True)):
            return
        for key, ((tile, _source), (old, target)) in enumerate(zip(entries, drawn,
                                                                    strict=True)):
            if start.chips(tile) != start.chips(old):
                self.start.set_chips(key, tile)
            drawn[key] = (tile, target)

    def _update_relative_timer(self) -> None:
        """A timer once a minute while the start page or the device view is shown (their
        texts are told against now); none on another page, nor while the window is not on
        the screen or once it is closed."""
        visible = self.navigation.get_visible_page()
        wanted = (self.get_mapped() and not getattr(self, "_closed", False)
                  and visible in (self.start.page, self.device_view))
        timer = getattr(self, "_relative_timer", None)
        if wanted and timer is None:
            self._relative_timer = GLib.timeout_add_seconds(RELATIVE_REFRESH_S,
                                                            self._refresh_relative_texts)
        elif not wanted and timer is not None:
            GLib.source_remove(timer)
            self._relative_timer = None

    def _refresh_relative_texts(self) -> bool:
        """Once a minute: the texts told against now drawn again where they changed: the
        tiles of the start page (all of them, once), the row of the last sync (its words)."""
        visible = self.navigation.get_visible_page()
        if visible is self.start.page:
            if any(tile_subtitle(source) != text
                   for source, text in getattr(self, "_start_texts", [])):
                self._render_start()
        elif visible is self.device_view:
            row, data = getattr(self, "last_sync_row", None), getattr(self, "_last_sync_data", None)
            if row is not None and data:
                text = GLib.markup_escape_text(last_sync_words(data))
                if row.get_subtitle() != text:
                    row.set_subtitle(text)
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
        if not data:
            return _("Not synced yet with openExpress")
        return last_sync_words(data)

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
        self.remembered.show(
            name=fields["description"],
            version=_("Software {version}").format(version=version) if version else "",
            part_number=fields["part_number"],
            last_seen=_("Not seen on this computer"),
            last_sync=self._last_sync_text(service.unit_id_of(entry.get("unitid"))))
        self._show_page_picture(fields["part_number"], fields["image_url"], fields["category"],
                                fields["description"])
        self._remembered_unit = service.unit_id_of(entry.get("unitid"))
        self._remembered_from_account = True

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
        last_sync = self._last_sync_text(known.unit_id)
        self.remembered.show(
            name=known.description,
            version=_("Software {version}").format(version=known.software_version),
            part_number=known.part_number,
            last_seen=_("Last seen {when}").format(when=_seen_at(known.last_seen)),
            last_sync=last_sync)
        if tile is not None:
            self._show_page_picture(tile.image_part_number, tile.image_url, tile.category,
                                    tile.description, tile.image_source)
        else:
            self._show_page_picture(known.part_number, known.image_url, known.category,
                                    known.description, devicelist.PICTURE_REMEMBERED)
        self._remembered_unit = known.unit_id
        self._remembered_from_account = False

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

    def _clear_device_pages(self) -> None:
        """Every page of the device view loaded anew: nothing kept from the device before."""
        self.maps_cache = self.updates_cache = None
        self.update_state = None
        self.map_state = None
        for sec in self.sections.values():
            sec.loaded = False
            sec.clear()
            sec.show("empty" if self.session is None else "loading")
        self._update_banner()
        self._update_actions()

    def show_no_device(self) -> None:
        """No device open (unplugged, or none found): the pages are empty and tell the state
        on USB, the start page is shown (_show_start decides whether to go there)."""
        self._clear_device_pages()
        # No check waits for the device that is gone (nor keeps it in memory).
        self._map_check_session = self._update_check_session = None
        # The empty pages tell the state on USB now, not the one from before the device
        # was opened (e.g. "starting" when the window opened with a starting device).
        self._poll_usb()
        self.connected.clear()
        resolve_account(self)
        self._show_start()

    def show_device(self) -> None:
        """The device open now (self.session) in every page, and its view (D7,
        _show_device_view); without one, show_no_device."""
        if self.session is None:
            self.show_no_device()
            return
        self._clear_device_pages()
        page = self.pages.get_visible_child_name()
        # The Maps or Updates page asks right away, and its answer fills the row of the device
        # page as well (show_maps, show_updates): the row does not ask a second time.
        if page == "maps":
            self._map_check_session = self.session
        elif page == "updates":
            self._update_check_session = self.session
        self.show_overview()
        self.load_section(page)
        # Else the one device (D7): its tile comes from the session.
        self.connected.keep_if_open(lambda c: self._is_open(c))
        resolve_account(self)
        self._show_device_view()

    def _update_banner(self) -> None:
        s = self.session
        if s is not None and getattr(s, "mass_storage", False):
            # A Garmin device in mass storage mode needs neither the setup nor the account.
            self.banner.set_revealed(False)
        elif s is not None and s.client.consumer is None:
            self.banner.set_title(_("openExpress needs a one-time setup"))
            self.banner.set_button_label(_("Choose Installer…"))
            self._banner_kind = "setup"
            self.banner.set_revealed(True)
        elif s is not None and not s.logged_in:
            self.banner.set_title(_("Not logged in to Garmin Connect"))
            self.banner.set_button_label(_("Log In"))
            self._banner_kind = "login"
            self.banner.set_revealed(True)
        elif s is not None and s.ownership_error is not None:
            unknown = not isinstance(s.ownership_error, NotAccountDevice)
            self.banner.set_title(self._ownership_title(s.ownership_error))
            self.banner.set_button_label(_("Try Again") if unknown else None)
            self._banner_kind = "recheck"
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def _banner_action(self) -> None:
        if self.busy:
            self.toast(_("Another operation is in progress"))
            return
        kind = getattr(self, "_banner_kind", "")
        if kind == "setup":
            self.choose_dll()
        elif kind == "recheck":
            self.refresh_device(cause=Cause.REFRESH_IN_VIEW)
        else:
            self.login()

    def load_section(self, key: str, force: bool = False) -> None:
        sec = self.sections[key]
        if self.session is None or (sec.loaded and not force) or key == "watch":
            if key == "watch" and self.session is not None and force:
                self.refresh_device(cause=Cause.REFRESH_IN_VIEW)
            return
        if getattr(self.session, "mass_storage", False) and key in NOT_FOR_MASS_STORAGE:
            show_not_for_mass_storage(sec)  # no request, nothing read
            return
        loader = {"updates": self.load_updates, "maps": self.load_maps,
                  "apps": self.load_apps, "music": self.load_music}[key]
        loader()

    def _load(self, key: str, title: str, work, show, device: bool = True,
              on_error=None) -> None:
        """Common pattern: spinner in the section, work in a thread, then `show(result)`.
        `on_error(e)` when the work failed for the device that is still open."""
        sec = self.sections[key]
        sess = self.session
        source = self._retry_sources.pop(key, None)
        if source is not None:
            GLib.source_remove(source)
        if sess is None:
            return
        sec.show("loading")

        def done(result):
            if self.session is not sess:
                return  # the watch was disconnected or replaced meanwhile
            sec.loaded = True
            sec.clear()
            # Without the buttons of the page that was just cleared.
            self._action_buttons = still_in_window(self._action_buttons)
            try:
                show(result)
            except Exception:  # noqa: BLE001 - said on the page, written to the log
                # Not "loading" for good: the page says so, and is read again when it is
                # chosen again (or with Try again).
                log_unexpected(f"showing the page {key}", sys.exc_info())
                traceback.print_exc()  # stderr, as before
                sec.loaded = False
                sec.clear()
                self._action_buttons = still_in_window(self._action_buttons)
                sec.show_error(_(PAGE_NOT_SHOWN))
                return
            sec.loaded = True
            sec.show("content")

        def failed(e):
            if self.session is sess:
                sec.show_error(str(e) or e.__class__.__name__)
                if on_error is not None:
                    on_error(e)
            return True

        if self.busy:
            # Another task runs; try again shortly so the page does not stay empty.
            def retry():
                self._retry_sources.pop(key, None)
                if self.session is sess:
                    self._load(key, title, work, show, device, on_error)
                return False

            self._retry_sources[key] = GLib.timeout_add(700, retry)
            return
        self.run_task(title, work, done, failed, device=device, in_page=True)

    # --- overview ---------------------------------------------------------------------

    def show_overview(self) -> None:
        s = self.session
        sec = self.sections["watch"]
        sec.clear()
        d = s.device

        hero = sec.group()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                      margin_bottom=12)
        # Until its picture comes: the icon its tile shows (T5.1), not the program's.
        self.device_image = Gtk.Image(icon_name=device_view_icon(self, s), pixel_size=128,
                                      height_request=DEVICE_PICTURE_SIZE)
        self.device_picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                          visible=False)
        self.device_picture.set_size_request(DEVICE_PICTURE_SIZE, DEVICE_PICTURE_SIZE)
        box.append(self.device_image)
        box.append(self.device_picture)
        title = Gtk.Label(label=d.description, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-1")
        box.append(title)
        # It wraps, as the name above it: the window gets as narrow as MIN_WINDOW.
        self.software_label = Gtk.Label(
            label=_("Software {version}").format(version=d.software_version_str), wrap=True,
            justify=Gtk.Justification.CENTER)
        self.software_label.add_css_class("dim-label")
        box.append(self.software_label)
        # An update to install: the chip of the device's tile, under its version
        # (_highlight_update).
        self.update_chip = Gtk.Label(label=start.update_available(), halign=Gtk.Align.CENTER,
                                     css_classes=["oe-chip", "warning"], visible=False)
        box.append(self.update_chip)
        hero.add(box)
        self._show_device_picture()

        # The space as the session holds it (read with the device, or after a task that
        # wrote to it): the page asks the device nothing.
        # TRANSLATORS: group on the device page: the device's own storage
        g = sec.group(_("Storage"))
        self.storage_row = Adw.ActionRow(title=_("Device storage"))
        self.storage_bar = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER,
                                        width_request=120, visible=False)
        self.storage_bar.add_offset_value("low", 0.75)
        self.storage_bar.add_offset_value("high", 0.9)
        self.storage_bar.add_offset_value("full", 1.0)
        self.storage_row.add_suffix(self.storage_bar)
        g.add(self.storage_row)
        render_space(self)
        if getattr(s, "space", None) is None:
            refresh_space(self, s)  # not read with the device: now, in the background

        # A Garmin device in mass storage mode is never synced and has no maps.
        mass_storage = getattr(s, "mass_storage", False)
        if mass_storage:
            sec.group(_("Mass storage mode"), mass_storage_text())
        else:
            g = sec.group(_("Sync with Garmin Connect"))
            if s.ownership_error is not None:
                g.add(Adw.ActionRow(title=_("Sync is not available for this device"),
                                    subtitle=GLib.markup_escape_text(str(s.ownership_error))))
            else:
                self.last_sync_row = Adw.ExpanderRow(title=_("Last sync"))
                self._last_sync_children = []
                g.add(self.last_sync_row)
                self._show_last_sync()
            if s.ownership_error is None:
                g.add(self._auto_sync_row())

        g = sec.group(pgettext("tab", "Updates"))
        self.update_row = Adw.ActionRow(title=_("Device software"), activatable=True)
        self.update_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.update_row.connect("activated", lambda *_a: self.select_section("updates"))
        g.add(self.update_row)
        self._render_update_state()
        if self.update_state is None:
            self._check_updates_quietly()
        if not mass_storage:
            self.map_row = Adw.ActionRow(title=pgettext("tab", "Maps"), activatable=True)
            self.map_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            self.map_row.connect("activated", lambda *_a: self.select_section("maps"))
            g.add(self.map_row)
            self._render_map_state()
            if self.map_state is None:
                self._check_maps_quietly()

        if s.logged_in:
            g = sec.group(_("Garmin Connect account"))
            row = Adw.ActionRow(title=GLib.markup_escape_text(s.client.auth.display))
            row.add_suffix(self._button(_("Log out"), self.logout))
            g.add(row)
            if s.ownership_error is None and s.account_devices is not None:
                self.primary_row = Adw.ActionRow(title=_("Primary training device"),
                                                 subtitle=_("Checking…"))
                g.add(self.primary_row)
                self._show_primary_training(self.primary_row)

        g = sec.group(_("Tools"))
        if not mass_storage:  # D6: no backup of a device in mass storage mode
            row = Adw.ActionRow(title=_("Backup"),
                                subtitle=GLib.markup_escape_text(str(paths.default_backup_dir())))
            # TRANSLATORS: button that creates a backup
            row.add_suffix(self._button(_("Create"), self.backup))
            g.add(row)
        row = Adw.ActionRow(title=_("Part number"), subtitle=d.part_number,
                            subtitle_selectable=True)
        g.add(row)
        sec.loaded = True
        sec.show("content")

    @staticmethod
    def _primary_training_text(status) -> str:
        if status.unit_id is None:
            return _("Not set")
        if status.is_this_device:
            return _("This device")
        return status.name or _("Another device")

    def _show_primary_training(self, row) -> None:
        """Which device the account uses as its primary training device; offers this one."""
        s = self.session

        def done(status):
            if self.session is not s:
                return
            row.set_subtitle(GLib.markup_escape_text(self._primary_training_text(status)))
            if not status.is_this_device and status.capable is True:
                row.add_suffix(self._button(
                    _("Set as Primary"),
                    lambda button: self.confirm_primary_training(status, button),
                    with_button=True))

        def failed(_e):
            if self.session is s:
                row.set_subtitle(_("Could not check"))

        self.in_background(
            lambda: service.primary_training_status(s.client, s.device, s.account_devices),
            done, failed)

    def confirm_primary_training(self, status, button: Gtk.Button | None = None) -> None:
        """Set as Primary: asked first; once Set is chosen, the change runs from `button`,
        which spins meanwhile."""
        s = self.session
        if s is None:
            return
        current = self._primary_training_text(status)
        d = Adw.AlertDialog(
            heading=_("Change the primary training device?"),
            body=_("The primary training device is the main source of performance and "
                   "training data in Garmin Connect.\n\nCurrent: {old}\nNew: {new}").format(
                old=current, new=s.device.description))
        d.add_response("cancel", _("Cancel"))
        # TRANSLATORS: button that makes this device the primary training device
        d.add_response("set", _("Set"))
        d.set_response_appearance("set", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("cancel")
        d.connect("response", lambda _d, r: r == "set" and self.set_primary_training(button))
        d.present(self)

    def set_primary_training(self, button: Gtk.Button | None = None) -> None:
        s = self.session
        if not s or not s.logged_in or s.ownership_error is not None:
            return

        def done(_before):
            self.toast(_("Primary training device changed"))
            if self.session is s:
                self.show_overview()

        self.run_task(_("Changing the primary training device…"),
                      lambda: service.set_primary_training(s.client, s.device, apply=True), done,
                      device=False, button=button)

    def _set_device_texture(self, texture) -> None:
        self.device_picture.set_paintable(texture)
        self.device_picture.set_visible(True)
        self.device_image.set_visible(False)

    def _show_device_picture(self) -> None:
        """Shows the device's picture at once from memory or the cache (the window's one
        store and reader, as the tiles have them); else asks for it in the background, the
        account first, then unit-info, and keeps it for every view."""
        s = self.session
        part = s.device.part_number
        # The account is asked only about a device that is on it; never about a Garmin device
        # in mass storage mode, which it was not checked for.
        on_account = (s.logged_in and s.ownership_error is None
                      and not getattr(s, "mass_storage", False))
        texture = pictures.known(self, part)
        if texture is None:
            texture = cached_texture(part)
            if texture is not None:
                pictures.keep(self, part, texture)
                log.info("device picture: from the cache")
        if texture is not None:
            self._set_device_texture(texture)
            if (not on_account and not picture_address_known(s.device)) or (
                    getattr(s, "mass_storage", False) and kind_untold(s.device)):
                # Off the account its address comes only from unit-info, while it is
                # connected: asked now for the address alone, so that the picture can come
                # back once the cache is cleared (a Venu 2, 21.10.2026). The file stays.
                # A drive whose kind Garmin has not told: asked once a run as well, so that
                # the log says what unit-info tells of it (an inReach, #11).
                self.in_background(
                    lambda: remember_picture_address(s.device,
                                                     picture_from_unit_info(s.client, s.device)),
                    lambda _r: None)
            return
        self.device_image.set_visible(True)
        self.device_picture.set_visible(False)
        name = device_picture_path(part).name

        def work():
            """(file, where it came from) or (None, why there is no picture)."""
            if on_account:
                try:
                    url = s.client.device_image_url(s.device.unit_id)
                except Exception as e:  # noqa: BLE001 - optional; the other source is tried
                    log.info("device picture: the account failed (%s)", type(e).__name__)
                    url = None
                remember_picture_address(s.device, url)
                path = cached_download(s.client.http, url, name) if url else None
                if path:
                    return path, "the account"
            url = picture_from_unit_info(s.client, s.device)
            if not url:
                return None, "unit-info gave no address"
            remember_picture_address(s.device, url)
            path, why = download_picture(s.client.http, url, name)
            return (path, "unit-info") if path else (None, f"download refused: {why}")

        def done(result):
            path, source = result
            texture = load_texture(path) if path else None
            if texture is None:
                log.info("device picture: none (%s)", source if path is None
                         else "the file is not a picture")
                if self.session is s:
                    # Still the icon (a picture in the cache returns before this work);
                    # unit-info may have told the kind meanwhile (a bike accessory).
                    self.device_image.set_from_icon_name(device_view_icon(self, s))
                return
            log.info("device picture: from %s", source)
            pictures.keep(self, part, texture)
            start = getattr(self, "start", None)
            if start is not None:
                start.set_picture(part, texture)  # its tile on the start page as well
            if self.session is s:
                self._set_device_texture(texture)

        self.in_background(work, done)

    def _render_update_state(self) -> None:
        """Shows self.update_state in the overview ("Watch software" row, software label)."""
        if self.session is None or not hasattr(self, "update_row"):
            return
        version = _("Software {version}").format(
            version=self.session.device.software_version_str)
        self._highlight_update(self.update_state == "available")
        if self.update_state == "available":
            self.update_row.set_subtitle(GLib.markup_escape_text(
                updates_available(self.update_count, self.update_only)))
            self.software_label.set_label(version)  # the chip under it says it
        elif self.update_state == "ready":
            self.update_row.set_subtitle(
                _("Update ready: eject the device to install it")
                if getattr(self.session, "mass_storage", False)
                else _("Update ready: disconnect the device to install it"))
            self.software_label.set_label(caption(version, _("update ready to install")))
        elif self.update_state == "current":
            # TRANSLATORS: subtitle of the row "Device software", without a full stop
            self.update_row.set_subtitle(_("Software is up to date"))
            self.software_label.set_label(version)
        elif self.update_state == "failed":
            self.update_row.set_subtitle(_("Could not check for updates"))
            self.software_label.set_label(version)
        else:
            self.update_row.set_subtitle(_("Checking…"))
            self.software_label.set_label(version)

    def _highlight_update(self, on: bool) -> None:
        """An update to install stands out on the device page (24.10.2026): the chip of its
        tile under the version and the subtitle of "Device software" in the warning colour.
        Otherwise (installed, none, checking, failed) as the other rows. The side panel stays
        as it is."""
        self.update_chip.set_visible(on)
        subtitle = row_subtitle(self.update_row)
        if subtitle is None:
            return
        if on:
            subtitle.add_css_class("warning")
        else:
            subtitle.remove_css_class("warning")

    def _set_update_state(self, new_updates: list, staged: list = ()) -> None:
        """`staged`: system updates copied already, waiting for the device to install them
        when it is disconnected; told only when nothing else is to be installed first."""
        self._update_check_session = None  # the row has its answer (show_device may wait)
        self.update_count = len(new_updates)
        self.update_only = new_updates[0] if len(new_updates) == 1 else None
        self.update_state = ("available" if new_updates else "ready" if staged else "current")
        # For the tile of the device on the start page, also when it is not open: an update
        # to install, or none (up to date, or what was found is copied now).
        remember_update_found(getattr(getattr(self, "session", None), "device", None),
                              bool(new_updates))
        self._render_update_state()

    def _check_updates_quietly(self) -> None:
        from openexpress import updates as upd

        s = self.session
        if s is None or self._update_check_session is s:
            return  # no watch, or a check for this watch is already running
        self._update_check_session = s

        def work():
            items = upd.mark_present(
                s.storage, upd.parse_updates(s.client.software_updates(s.device.raw)),
                upd.component_count(s.device))
            return upd.pending(items), upd.staged(items)

        def done(result):
            if self._update_check_session is s:
                self._update_check_session = None
            if self.session is not s:
                return
            self._set_update_state(*result)

        def failed(_e):
            if self._update_check_session is s:
                self._update_check_session = None
            if self.session is not s or self.update_state is not None:
                return
            self.update_state = "failed"
            self._render_update_state()

        self.in_background(work, done, failed)

    def _render_map_state(self) -> None:
        """Shows self.map_state in the overview (the "Maps" row)."""
        if self.session is None or not hasattr(self, "map_row"):
            return
        from openexpress import maps as mp

        if self.map_state == "available":
            text = ngettext("{count} update available", "{count} updates available",
                            self.map_update_count).format(count=self.map_update_count)
        elif self.map_state == "current":
            # TRANSLATORS: subtitle of the row "Maps" of the device page, without a full stop
            text = _("All installed maps are up to date")
        elif self.map_state == "empty":
            text = _("No maps are installed")
        elif self.map_state == "none":
            text = mp.no_maps_message()
        elif self.map_state == "failed":
            text = _("Could not check for updates")
        else:
            text = _("Checking…")
        self.map_row.set_subtitle(text)

    def _set_map_state(self, result) -> None:
        """From an answer of the map check: the maps read, and how many the server listed.
        Also called by the Maps page, whose answer is the newer one."""
        all_maps, listed = result
        self._map_check_session = None  # a quiet check that is still running is out of date
        self.map_update_count = sum(1 for m in all_maps if m.update_available)
        if not listed:
            self.map_state = "none"
        elif self.map_update_count:
            self.map_state = "available"
        elif any(m.installed for m in all_maps):
            self.map_state = "current"
        else:
            self.map_state = "empty"
        self._render_map_state()

    def _check_maps_quietly(self) -> None:
        """Ask Garmin's server once for the maps of the device that was just opened, for the
        "Maps" row only: the Maps page asks for itself. Nothing is written anywhere."""
        from openexpress import maps as mp

        s = self.session
        if s is None or self._map_check_session is s:
            return  # no device, or a check for this device is already running
        self._map_check_session = s

        def done(result):
            if self._map_check_session is not s:
                return  # another device, or the Maps page has a newer answer
            if self.session is s:
                self._set_map_state(result)
            self._map_check_session = None

        def failed(_e):
            if self._map_check_session is not s:
                return
            self._map_check_session = None
            if self.session is s and self.map_state is None:
                self.map_state = "failed"
                self._render_map_state()

        def work():
            result = mp.read_maps(s.client.universal_maps(s.device.raw))
            mp.mark_waiting(s.storage, result[0])
            return result

        self.in_background(work, done, failed)

    def _show_last_sync(self) -> None:
        if (not hasattr(self, "last_sync_row") or self.session is None
                or self.session.ownership_error is not None):
            return
        row = self.last_sync_row
        for child in self._last_sync_children:
            # With the row the list put around it: the box alone is no child of the expander
            # row, so it stayed, and each drawing added the details once more (since 0.6.6).
            row.remove(child.get_parent() or child)
        self._last_sync_children = []
        data = service.load_last_sync(self.session.device.unit_id)
        self._last_sync_data = data  # its time told against now, again every minute
        if not data:
            row.set_subtitle(_("Not synced yet with openExpress"))
            row.set_enable_expansion(False)
            return
        # Shown as last_sync.describe tells it: the same words as the command line and the
        # notification of the background sync.
        told = last_sync.describe(data)
        row.set_subtitle(GLib.markup_escape_text(told.row(datetime.now())))
        row.set_tooltip_text(told.tooltip)  # the full date
        said = told.details
        row.set_enable_expansion(bool(said))
        if said:
            # The details as one row of a few lines: the files, those already in Garmin
            # Connect, then what went wrong, with an icon and in the colour of warnings.
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10,
                              margin_bottom=10, margin_start=12, margin_end=12)
            for text, warning in said:
                # Secondary, as the subtitle of a row: smaller, and dimmed unless a warning.
                label = Gtk.Label(label=text, xalign=0, wrap=True)
                label.add_css_class("caption")
                if warning:  # files rejected, or that could not be uploaded or written
                    line = Gtk.Box(spacing=6)
                    icon = Gtk.Image(icon_name="dialog-warning-symbolic")
                    for widget in (icon, label):
                        widget.add_css_class("warning")
                        line.append(widget)
                    details.append(line)
                else:
                    label.add_css_class("dim-label")
                    details.append(label)
            row.add_row(details)
            self._last_sync_children.append(details)

    def _watch_last_sync(self) -> None:
        """Updates the overview when the background service finishes a sync."""
        path = service.last_sync_path()
        paths.make_dir(path.parent)
        self._last_sync_monitor = Gio.File.new_for_path(str(path)).monitor_file(
            Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._last_sync_monitor.connect("changed", lambda *_a: self._show_last_sync())
        self._last_sync_monitor.connect("changed", lambda *_a: self._count_activities_again())

    def _count_activities_again(self) -> None:
        """After a sync (this window's, or one of the background service or the command line,
        seen in last_sync.json): the activities of the devices connected counted again, from
        what was listed against state.json as it is now (the devices are not asked); the
        start page drawn again when it is shown."""
        s = self.session
        if s is not None and getattr(s, "to_sync", None) is not None:
            s.to_sync = s.to_sync.again()
        for summary in self.connected.devices:
            if summary.to_sync is not None:
                summary.to_sync = summary.to_sync.again()
        if self._on_start_page():
            self._update_tile_chips()

    # --- background sync, language, setup, account ------------------------------------

    def _background_enabled(self) -> bool:
        from openexpress import background

        try:
            state = background.is_enabled()
        except Exception:  # noqa: BLE001 - e.g. no systemctl: treat as unknown
            log.debug("%s", traceback.format_exc())
            state = None
        return bool(self.gui_settings.get("background")) if state is None else bool(state)

    def set_background(self, enabled: bool, on_result=None) -> None:
        from openexpress import background

        def done(ok: bool, error: str) -> None:
            if ok:
                self.gui_settings["background"] = enabled
                save_gui_settings(self.gui_settings)
                self.toast(_("Background sync enabled") if enabled
                           else _("Background sync disabled"))
                if self.session is not None:
                    self.show_overview()
            else:
                self.error(_("Background sync"), error or _("The change failed."))
            if on_result:
                on_result(ok)

        try:
            background.set_enabled(enabled, done)
        except Exception as e:  # noqa: BLE001
            log.debug("%s", traceback.format_exc())
            done(False, str(e))

    def _auto_sync_row(self) -> Adw.SwitchRow:
        """The background sync, on or off as it is, from the device page (the same as in
        Preferences): a change that fails puts the switch back."""
        row = Adw.SwitchRow(title=_("Sync automatically when connected"),
                            active=self._background_enabled())

        def toggled(switch, _pspec):
            # The row from the signal; it is held only until the change is done.
            wanted = switch.get_active()
            if wanted == self._background_enabled():
                return

            def result(ok):
                if not ok:
                    switch.handler_block_by_func(toggled)
                    switch.set_active(not wanted)
                    switch.handler_unblock_by_func(toggled)

            self.set_background(wanted, result)

        row.connect("notify::active", toggled)
        return row

    def show_preferences(self) -> None:
        dialog = Adw.PreferencesDialog()
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        page.add(self._account_group())
        # The window holds the dialog while it is open, for the handlers of its rows (one
        # that held the dialog itself would keep it after it closed); closed, the window
        # keeps no widget of the preferences.
        self._preferences = dialog
        dialog.connect("closed", lambda _d: (setattr(self, "_account_row", None),
                                             setattr(self, "_storage", None),
                                             setattr(self, "_preferences", None)))
        g = Adw.PreferencesGroup(title=_("Sync with Garmin Connect"))
        sw = Adw.SwitchRow(
            title=_("Sync in the background"),
            subtitle=_("Starts at login and syncs whenever the device is connected, "
                       "also when this window is closed"),
            active=self._background_enabled())

        def toggled(row, _pspec):
            wanted = row.get_active()
            if wanted == self._background_enabled():
                return

            def result(ok):
                if not ok:
                    row.handler_block_by_func(toggled)
                    row.set_active(not wanted)
                    row.handler_unblock_by_func(toggled)

            self.set_background(wanted, result)

        sw.connect("notify::active", toggled)
        g.add(sw)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Language"))
        codes = [""] + i18n.available_languages()
        names = [_("System default")] + [i18n.LANGUAGE_NAMES.get(c, c) for c in codes[1:]]
        current = load_ui_language()
        combo = Adw.ComboRow(title=_("Interface language"), model=Gtk.StringList.new(names))
        combo.set_selected(codes.index(current) if current in codes else 0)

        def language_changed(r, _p):
            code = codes[r.get_selected()]
            if code == load_ui_language():
                return
            try:
                save_ui_language(code)
            except (OSError, ValueError) as e:
                self.error(_("Language"), str(e))
                return
            shown = getattr(self, "_preferences", None)
            if shown is not None:
                shown.add_toast(Adw.Toast(
                    title=_("Restart the application to change the language")))

        combo.connect("notify::selected", language_changed)
        g.add(combo)
        page.add(g)

        g = Adw.PreferencesGroup(title=_("Setup"))
        row = Adw.ActionRow(
            title=_("Garmin Express installer"),
            subtitle=_("Read the application key again from the official installer"))
        row.add_suffix(self._button(_("Choose File…"), self.choose_dll))
        g.add(row)
        page.add(g)
        page.add(self._cache_group())
        dialog.add(page)
        dialog.present(self)

    def _account_group(self) -> Adw.PreferencesGroup:
        """The "Garmin Connect account" group of the preferences: one row, who is logged in
        with Log out, or nobody with Log In; drawn again when the account changes."""
        g = Adw.PreferencesGroup(title=_("Garmin Connect account"))
        row = Adw.ActionRow()
        button = self._button("", self._account_action)
        row.add_suffix(button)
        g.add(row)
        self._account_row = (row, button)
        render_account_row(self)
        return g

    def _account_action(self) -> None:
        """Log out or Log In in the preferences: the same as in the device view."""
        if logged_in_auth() is None:
            self.login()
        else:
            self.logout()

    def _cache_group(self) -> Adw.PreferencesGroup:
        """The "Storage" group of the preferences: a row for each part of the cache, with its
        size, its files and a trash (grey while the part is empty), and one for all of it
        with "Clear All…". The sizes are counted in the background, never on the window's
        thread; the group is about the cache of this installation alone."""
        kind = _("Flatpak") if paths.in_flatpak() else _("version from source")
        # TRANSLATORS: group in the preferences: the cache (downloaded maps and updates,
        # pictures, temporary files) on the disk of the computer (not the device's storage of
        # the device page)
        g = Adw.PreferencesGroup(title=pgettext("preferences", "Storage"), description=_(
            "Only the files of this installation ({kind}); openExpress downloads them again "
            "when needed").format(kind=kind))
        parts = {}
        for name in cache.CATEGORIES:
            row = Adw.ActionRow(title=_(cache.CATEGORY_NAMES[name]), subtitle=_("Counting…"))
            trash = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text=_("Delete these files"), sensitive=False)
            trash.add_css_class("flat")
            # The handler gets its button from the signal and holds none.
            trash.connect("clicked", lambda button, name=name: ask_clear_cache_part(
                self, name, button))
            row.add_suffix(trash)
            g.add(row)
            parts[name] = (row, trash)
        # TRANSLATORS: the last row of the cache in the preferences, with "Clear All…"
        everything = Adw.ActionRow(title=_("All of the cache"), subtitle=_("Counting…"))
        # TRANSLATORS: button that clears every part of the cache
        button = Gtk.Button(label=_("Clear All…"), valign=Gtk.Align.CENTER, sensitive=False)
        button.connect("clicked", self._ask_clear_cache)
        everything.add_suffix(button)
        g.add(everything)
        # Held by the window while the preferences are open (dropped when they close), so
        # that what comes back from a thread finds the rows without holding them.
        self._storage = types.SimpleNamespace(parts=parts, everything=(everything, button),
                                              usage=None)
        MainWindow.in_background(cache.usage, lambda use: show_cache_usage(self, use))
        return g

    def _ask_clear_cache(self, button) -> None:
        """"Clear All…": asks first, naming the maps and their size; then clears as
        `cache --clear --apply` does, from the button."""
        shown = getattr(self, "_storage", None)
        use = shown.usage if shown is not None else None
        if use is None:
            return
        maps, other = use.sizes["maps"], use.total - use.sizes["maps"]
        if maps:
            body = _("This deletes the downloaded maps ({maps}) and the other files of the cache "
                     "({other}). The maps will have to be downloaded again.").format(
                maps=_human(maps), other=_human(other))
        else:
            body = _("This deletes the files of the cache ({size}). They are downloaded again "
                     "when needed.").format(size=_human(use.total))
        ask_to_clear(self, _("Clear the cache?"), body, None)


    def show_shortcuts(self) -> None:
        dialog = Adw.ShortcutsDialog()
        section = Adw.ShortcutsSection(title=_("General"))
        for title, accel in ((_("Sync"), "<Control>s"), (_("Refresh"), "<Control>r"),
                             (_("Preferences"), "<Control>comma"),
                             (_("Keyboard Shortcuts"), "<Control>question"),
                             (_("Quit"), "<Control>q")):
            section.add(Adw.ShortcutsItem(title=title, accelerator=accel))
        dialog.add(section)
        dialog.present(self)

    def choose_dll(self) -> None:
        dialog = Gtk.FileDialog(
            title=_("Select the Garmin Express installer (GarminExpress.exe)"))

        def read(path: Path):
            if path.suffix.lower() == ".exe":
                from openexpress.installer import consumer_from_installer

                return consumer_from_installer(path)
            consumer = extract_consumer_from_dll(path)
            util = path.with_name("Utilities.dll")
            if util.exists():
                consumer.url_salt = extract_url_salt_from_dll(util)
            return consumer

        def chosen(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return
            if f.get_path() is None:
                self.toast(_("Choose a file stored on this computer"))
                return
            path = Path(f.get_path())

            def saved(consumer):
                save_consumer(consumer)
                self.toast(_("Application key saved"))
                account_changed(self)

            self.run_task(_("Reading the installer…"), lambda: read(path), saved, device=False)

        dialog.open(self, None, chosen)

    def login(self) -> None:
        settings = Settings.load()

        def on_ticket(st):
            if st is None:
                return

            def work():
                client = service.make_client(settings)
                token, secret = client.exchange_ticket(st.ticket, st.service)
                auth = store.ConnectAuth(token, secret, settings.environment)
                client.auth = auth
                profile_ok = True
                try:
                    prof = client.user_profile()
                    auth.account = {k: prof.get(k) for k in
                                    ("userprofilepk", "username", "emailaddress", "garminguid")}
                except Exception:  # noqa: BLE001 - the token works without the profile
                    log.debug("%s", traceback.format_exc())
                    profile_ok = False
                store.save(auth)
                return auth, profile_ok

            def done(result):
                auth, profile_ok = result
                self.toast(_("Logged in as {account}").format(account=auth.display) if profile_ok
                           else _("Logged in, but the account details could not be loaded"))
                self._want_account()
                account_changed(self)

            def start():
                if self.busy:
                    return True  # the ticket expires only after minutes: wait for the task
                self.run_task(_("Logging in…"), work, done, device=False)
                return False

            def first():
                if start():
                    GLib.timeout_add(500, start)
                return False

            GLib.idle_add(first)

        sso.create_login_window(settings, on_ticket, application=self.get_application(),
                                parent=self).present()

    def logout(self) -> None:
        store.delete()
        self.toast(_("Logged out"))
        self._account = None  # the start page lists no device of the account any more
        account_changed(self, Cause.LOGGED_OUT)

    def backup(self) -> None:
        from openexpress.backup import run_backup

        s = self.session
        if not s:
            return
        self.run_task(
            _("Creating backup…"),
            lambda: run_backup(s.device, s.storage, paths.default_backup_dir(),
                               progress=self.progress),
            lambda r: self.toast(ngettext("Copied {count} file to {path}",
                                          "Copied {count} files to {path}",
                                          r.files).format(count=r.files, path=r.path)),
        )

    # --- sync -------------------------------------------------------------------------

    def start_sync(self, first_run: str | None = None) -> None:
        s = self.session
        if (not s or getattr(s, "mass_storage", False) or not s.logged_in
                or s.ownership_error is not None):
            return

        def done(summary: service.SyncSummary):
            self.toast(_("Sync finished") if not summary.errors
                       else _("Sync finished with errors"))
            self._show_last_sync()
            self._count_activities_again()

        def failed(e):
            self._count_activities_again()  # what it uploaded before it stopped is done
            if isinstance(e, service.FirstSyncChoiceNeeded):
                self.ask_first_sync()
                return True
            if isinstance(e, OwnershipError) and self.session is s:
                # The account or its device list changed since the watch was detected.
                s.ownership_error = e
                self.show_device()
            return False

        self.run_task(_("Syncing…"),
                      writes_to_device(lambda: service.run_sync(s, first_run, self.progress)),
                      done, failed)

    def ask_first_sync(self) -> None:
        d = Adw.AlertDialog(
            heading=_("First sync"),
            body=_("If the device also syncs via your phone, most data is already "
                   "in Garmin Connect. You can mark the current files as uploaded "
                   "and upload only new ones, or upload everything (Connect skips duplicates)."),
        )
        d.add_response("cancel", _("Cancel"))
        d.add_response("all", _("Upload everything"))
        d.add_response("baseline", _("New data only"))
        d.set_response_appearance("baseline", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("baseline")
        d.connect("response", lambda _d, r: r != "cancel" and self.start_sync(r))
        d.present(self)

    # --- updates ----------------------------------------------------------------------

    def load_updates(self) -> None:
        from openexpress import updates as upd

        s = self.session

        def work():
            # Asks Garmin's server, then compares the offered files with those on the device.
            answer = s.client.software_updates(s.device.raw, include_reinstalls=True)
            items = upd.mark_present(s.storage, upd.parse_updates(answer),
                                     upd.component_count(s.device))
            return items, upd.beta_offered(answer)

        def failed(_e):
            # The "Device software" row waited for this answer (show_device): it says so.
            if self._update_check_session is s:
                self._update_check_session = None
            if self.update_state is None:
                self.update_state = "failed"
                self._render_update_state()

        self._load("updates", _("Checking for updates…"), work,
                   lambda result: self.show_updates(*result), on_error=failed)

    def show_updates(self, items, beta: bool = False) -> None:
        if self.session is None:
            return
        self.updates_cache, self.updates_beta = items, beta
        sec = self.sections["updates"]
        from openexpress import updates as upd

        new = upd.pending(items)
        staged = upd.staged(items)
        self._set_update_state(new, staged)
        # A system update is told at the top of the page; a second one (rare) is listed.
        system = next((u for u in new if u.is_system), None)
        self._updates_status(sec, system, len(new), beta,
                             ready=staged[0] if staged and system is None else None)
        rest = [u for u in new if u is not system]
        if rest:
            g = sec.group(_("Available updates"))
            for u in rest:
                self._update_row(g, u)
        # A file the device already has is a reinstall, whatever the server calls it; but not
        # a system update that is waiting for the device to install it.
        re = [u for u in items if (u.is_reinstall or u.on_device) and not u.is_metadata
              and not any(u is w for w in staged)]
        if re:
            g = sec.group(_("Advanced"))
            exp = Adw.ExpanderRow(
                title=_("Reinstall components"),
                subtitle=ngettext("{count} component already on the device; useful for "
                                  "troubleshooting",
                                  "{count} components already on the device; useful for "
                                  "troubleshooting", len(re)).format(count=len(re)))
            g.add(exp)
            for u in sorted(re, key=lambda x: x.title):
                self._update_row(exp, u, reinstall=True)

    def _updates_status(self, sec, system, count: int, beta: bool = False, ready=None) -> None:
        """The top of the Updates page: the system update when there is one, the system update
        that is copied already (`ready`) and waits for the device to be disconnected, how many
        other updates there are, or that the software is up to date; and whether Garmin offers
        beta versions to the device."""
        from openexpress import updates as upd

        d = self.session.device
        installed = caption(_("{model} · version {version}").format(
            model=d.description, version=d.software_version_str))
        detail = None
        buttons = []
        if system is not None:
            icon, title = "software-update-available-symbolic", _("Software update available")
            versions, size = f"{d.software_version_str}{ARROW}{system.version}", _human(system.size)
            subtitle = caption(versions, size)
            if system.is_beta:
                # The versions, "Beta" and the rest apart, so that "Beta" can have the warning
                # colour of the theme (light and dark); Pango markup knows no colours of the
                # theme.
                # TRANSLATORS: marks a beta version of the device software
                subtitle = (versions, _("Beta"), size)
            detail = caption(system.title, system.severity_text, system.filename)
            # TRANSLATORS: button
            buttons.append(self._button(_("Install"), lambda u=system: self.confirm_update(u),
                                        "suggested-action"))
        elif ready is not None:
            icon, title = "software-update-available-symbolic", _("Update ready to install")
            versions = f"{d.software_version_str}{ARROW}{ready.version}"
            if getattr(self.session, "mass_storage", False):
                # A drive: the device takes the update once it is ejected (#11).
                # TRANSLATORS: after the versions, e.g. "3.36 → 3.40 · eject the device…"
                disconnect = _("eject the device to install it")
                buttons.append(self._button(
                    _("Eject"), lambda button, s=self.session: self.eject_device(s, button=button),
                    "suggested-action", with_button=True))
            else:
                # TRANSLATORS: after the versions, e.g. "33.11 → 33.12 · disconnect the device…"
                disconnect = _("disconnect the device to install it")
            subtitle = caption(versions, disconnect)
            if ready.is_beta:
                subtitle = (versions, _("Beta"), disconnect)
            detail = caption(ready.title, ready.severity_text, ready.filename)
        elif count:
            icon, subtitle = "software-update-available-symbolic", installed
            title = ngettext("{count} update available", "{count} updates available",
                             count).format(count=count)
        else:
            icon, subtitle = "object-select-symbolic", installed
            # TRANSLATORS: title at the top of the Updates page, without a full stop
            title = _("Software is up to date")
        buttons.append(self._button(_("Check Again"),
                                    lambda: self.load_section("updates", force=True)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      halign=Gtk.Align.CENTER, margin_top=24, margin_bottom=24)
        image = Gtk.Image(icon_name=icon, pixel_size=96)
        if system is None and ready is None and not count:
            image.add_css_class("success")  # up to date only
        box.append(image)

        def label(text, styles, **props):
            widget = Gtk.Label(label=text, **props)
            for style in styles:
                widget.add_css_class(style)
            return widget

        for text, styles in ((title, ["title-1"]), (subtitle, ["dim-label"]),
                             (detail, ["caption", "dim-label"]),
                             (_(upd.BETA_OFFERED) if beta else None, ["caption", "dim-label"])):
            if isinstance(text, tuple):  # a beta version: "Beta" stands out
                versions, word, after = text
                # In a narrow window the parts go on to the next line: a separator stays
                # with the part before it, never at the start of a line.
                if WRAP_BOX is None:
                    # A FlowBox puts parts in columns: one text that wraps, "Beta" in bold
                    # (Pango markup knows no colours of the theme); a hard space before
                    # each separator.
                    line = label("", styles, wrap=True, justify=Gtk.Justification.CENTER,
                                 max_width_chars=50)
                    line.set_markup(f"{GLib.markup_escape_text(versions)}{SEPARATOR}"
                                    f"<b>{GLib.markup_escape_text(word)}</b>{SEPARATOR}"
                                    f"{GLib.markup_escape_text(after)}")
                    box.append(line)
                    continue
                marked = Gtk.Box()
                marked.append(label(word, ["warning", "heading"]))
                marked.append(label(SEPARATOR, styles))
                box.append(wrapping_row(
                    [label(versions + SEPARATOR, styles), marked,
                     label(after, styles, wrap=True, justify=Gtk.Justification.CENTER)], 0))
            elif text:
                box.append(label(text, styles, wrap=True, justify=Gtk.Justification.CENTER,
                                 max_width_chars=50))
        for b in buttons:
            b.add_css_class("pill")
        box.append(wrapping_row(buttons, 12, margin_top=8))  # one under the other if need be
        sec.group().add(box)

    def _update_row(self, parent, u, reinstall: bool = False) -> None:
        details = caption(u.version, _human(u.size), u.severity_text, u.filename)
        # A system update says so in its title; the name the server gives it goes first.
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(_("System software") if u.is_system else u.title),
            subtitle=GLib.markup_escape_text(caption(u.title, details) if u.is_system
                                             else details),
        )
        # TRANSLATORS: button
        row.add_suffix(self._button(_("Reinstall") if reinstall else _("Install"),
                                    lambda u=u: self.confirm_update(u),
                                    None if reinstall else "suggested-action"))
        parent.add_row(row) if isinstance(parent, Adw.ExpanderRow) else parent.add(row)

    @staticmethod
    def _update_done_text(rep) -> str:
        """What the window says after an update was copied, with what to do next."""
        return _("Files copied to the device.") + (_(FINISH_STEPS_WINDOW) if rep.installed
                                                    else "")

    def confirm_update(self, u) -> None:
        from openexpress import updates as upd

        s = self.session
        # A drive: the copy is on the disk once written (LocalStorage syncs it), but the device
        # may take it only once the drive is ejected (#11), so the cable may go out only then.
        mass_storage = getattr(s, "mass_storage", False)
        chosen = upd.select(self.updates_cache, [u.part_number])
        # A system update: from which version of the device software to which.
        version = (f"{s.device.software_version_str}{ARROW}{u.version}" if u.is_system
                   else u.version)
        body = (f"{u.title} {version}\n"
                + _("Size: {size}").format(size=_human(u.size)) + "\n"
                + _("On the device: {path}").format(path=u.device_path)
                + ("\n\n" + _("Changes:") + "\n" + "\n".join("• " + c for c in u.changes[:10])
                   if u.changes else "")
                + ("\n\n" + _("This is a beta version.") if u.is_beta else ""))
        d = Adw.AlertDialog(heading=_("Install update?"), body=body)
        # Below the text: the license, then the warning, right above the buttons.
        below = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        if u.eula_url.casefold().startswith("https://"):
            # GTK opens it (in the Flatpak through the portal); only a web address is a link.
            below.append(Gtk.LinkButton(uri=u.eula_url, label=_("License agreement (EULA)"),
                                        halign=Gtk.Align.CENTER))
        elif u.eula_url:
            below.append(Gtk.Label(label=_("License: {url}").format(url=u.eula_url), wrap=True,
                                   justify=Gtk.Justification.CENTER))
        if u.is_system:
            # In the warning colour of the theme and in bold.
            warning = Gtk.Label(label=_(upd.SYSTEM_UPDATE_WARNING), wrap=True,
                                justify=Gtk.Justification.CENTER)
            warning.add_css_class("warning")
            warning.add_css_class("heading")
            below.append(warning)
        if below.get_first_child() is not None:
            d.set_extra_child(below)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Accept and install"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def install():
            rep = upd.install(chosen, s.storage, session=s.client.http, progress=self.progress,
                              transfer_progress=self.fraction, download_progress=self.fraction)
            return rep, mass_storage and is_bike_accessory(s.client, s.device)

        def installed(result):
            rep, bike_accessory = result
            self._show_installed(s, chosen)
            if not mass_storage:
                self.error(_("Done"), self._update_done_text(rep))
                return
            self.ask_to_eject(s, bike_accessory)

        def failed(e) -> bool:
            # Under a heading of its own, not the name of the task ("Installing update…").
            self.error(_("Update not installed"), str(e) or type(e).__name__)
            return True

        def response(_d, r):
            if r != "ok":
                return
            self.run_task(_("Installing update…"), writes_to_device(install), installed,
                          failed)

        d.connect("response", response)
        d.present(self)

    def ask_to_eject(self, s, bike_accessory: bool = False) -> None:
        """After an update was saved on a Garmin drive: it is to be ejected first; that the
        cable can go out is said only once it is (eject_device)."""
        d = Adw.AlertDialog(heading=_("Update saved"),
                            body=_("Eject the device before you unplug the cable: until then "
                                   "it may not see the update."))
        # TRANSLATORS: button; the device is ejected later
        d.add_response("later", _("Later"))
        # TRANSLATORS: button that ejects the device (its USB drive)
        d.add_response("eject", _("Eject"))
        d.set_response_appearance("eject", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("eject")
        d.set_close_response("later")

        def response(_d, r):
            if r == "eject":
                self.eject_device(s, bike_accessory)

        d.connect("response", response)
        d.present(self)

    def eject_device(self, s, bike_accessory: bool = False,
                     button: Gtk.Button | None = None) -> None:
        """Eject the drive of a device in mass storage mode; then the cable can go out. When
        the system refuses, why, that the cable must stay in, and Try Again. `button`: the
        Eject of the Updates page, which spins meanwhile."""
        def ejected(_result):
            done = Adw.AlertDialog(heading=_("Done"), body=ejected_text())
            done.add_response("ok", _("OK"))
            if bike_accessory:
                # As Garmin Express does for such a device: how it finishes the update.
                done.connect("response", lambda *_a: self.error(_("Finish the update"),
                                                                bike_accessory_text()))
            done.present(self)

        def failed(e) -> bool:
            if isinstance(e, DriveNotMounted):
                self.error(_("The device is not mounted"), str(e))
                return True
            d = Adw.AlertDialog(heading=_("The device was not ejected"),
                                body=f"{e}\n\n{not_ejected_text()}")
            d.add_response("close", _("Close"))
            d.add_response("retry", _("Try Again"))
            d.set_response_appearance("retry", Adw.ResponseAppearance.SUGGESTED)
            d.connect("response", lambda _d, r: r == "retry"
                      and self.eject_device(s, bike_accessory))
            d.present(self)
            return True

        self.run_task(_("Ejecting the device…"), lambda: eject(s.storage), ejected, failed,
                      button=button)

    def _show_installed(self, session, chosen) -> None:
        """After an update was copied: the files are on the device now. The page shows that
        without asking the server or reading the device again (a system update is then ready
        to install, the others are no updates any more)."""
        cache = self.updates_cache
        if (self.session is not session or cache is None
                or not all(any(c is u for u in cache) for c in chosen)):
            return  # another device, or the page was checked again meanwhile
        for u in chosen:
            u.on_device = True
        sec = self.sections["updates"]
        sec.clear()
        self._action_buttons = still_in_window(self._action_buttons)
        self.show_updates(self.updates_cache, self.updates_beta)

    # --- the report of a device or problem, about ------------------------------------

    @staticmethod
    def _report_text(report_text: str, attach_log: bool) -> str:
        """The report, with the end of the log when the user asked for it."""
        from openexpress import applog, report

        if not attach_log:
            return report_text
        return report.with_log(report_text, applog.tail(report.LOG_LINES), applog.crash_tail())

    @staticmethod
    def _report_view(report_text: str):
        """The preview of the report with the "attach the log" switch: (widget, text buffer).

        No handler here may refer to a widget of the preview: a widget that keeps itself
        alive through its own handler outlives the dialog, and Python frees it whenever it
        next collects reference cycles. The switch therefore only knows the text buffer."""
        view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        buffer = view.get_buffer()
        buffer.set_text(report_text)
        scroller = Gtk.ScrolledWindow(child=view, min_content_height=260, min_content_width=420)
        scroller.add_css_class("card")
        attach = Gtk.CheckButton(label=_("Attach the last log"))
        attach.set_tooltip_text(_("The log is filtered, but read it before you send it."))
        attach.connect("toggled", lambda button: buffer.set_text(
            MainWindow._report_text(report_text, button.get_active())))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(scroller)
        box.append(attach)
        return box, buffer

    def show_report(self) -> None:
        from openexpress import report

        s = self.session
        if s is None:
            text = _("Connect the device first, so that the report can describe it.")
            body_text = ""
        else:
            # The space as the session holds it: nothing is asked of the device here.
            body_text = report.build(s.device, s.storage, s.logged_in,
                                     space=getattr(s, "space", None) or (None, None))
            text = _("Open on GitHub starts a new issue with this report filled in; nothing is "
                     "published until you submit it. The report contains no serial number, "
                     "unit ID or e-mail.")
        log.info("report dialog opened (device connected: %s)", s is not None)
        d = Adw.AlertDialog(heading=_("Report a Device or Problem"), body=text)
        buffer = None
        if body_text:
            box, buffer = self._report_view(body_text)
            d.set_extra_child(box)
            d.add_response("copy", _("Copy"))
        d.add_response("close", _("Close"))
        d.add_response("open", _("Open on GitHub"))
        d.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("close")

        def shown() -> str:
            """Exactly what the preview shows."""
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

        def response(_d, r):
            if r == "copy":
                self.get_clipboard().set(shown())
                self.toast(_("Report copied"))
            elif r == "open":
                self._open_report_form(body_text, shown() if buffer is not None else "",
                                       s.device.description if s is not None else None)

        d.connect("response", response)
        d.present(self)

    def _open_report_form(self, report_text: str, shown: str, model: str | None) -> None:
        """Open a new device report on GitHub with the report filled in. The log never goes
        into the address (too long, and the address is sent to GitHub when the page opens):
        when it is attached it goes to the clipboard. A report too long for an address goes
        to the clipboard whole, with the empty form, as before."""
        from openexpress import report

        url = report.issue_url(report_text, model) if report_text else None
        if report_text and url is None:
            self.get_clipboard().set(shown)
            self.toast(_("Report copied: paste it into the form"))
        elif report_text and shown != report_text:  # the log is attached below the report
            self.get_clipboard().set(shown[len(report_text):].lstrip("\n"))
            self.toast(_("The log is in the clipboard: paste it into the issue"))
        Gtk.UriLauncher.new(url or report.ISSUE_URL).launch(self, None, None, None)

    def show_about(self) -> None:
        from openexpress import __version__

        about = Adw.AboutDialog(
            application_name="openExpress",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Tomasz Bojanowski",
            website="https://github.com/TomaszBojanowski/openExpress",
            issue_url="https://github.com/TomaszBojanowski/openExpress/issues",
            license_type=Gtk.License.GPL_3_0,
            comments=_("Unofficial Linux app for Garmin watches: sync with Garmin Connect, "
                       "software updates, maps, Connect IQ apps and music.\n\n"
                       "openExpress is not affiliated with, endorsed by or supported by "
                       "Garmin Ltd. Garmin, fēnix, Garmin Connect and Garmin Express are "
                       "trademarks of Garmin Ltd. or its subsidiaries."),
        )
        # TRANSLATORS: a link in "About openExpress" to the page where one can support it
        about.add_link(_("Support the project"), "https://buymeacoffee.com/sojer2005")
        # TRANSLATORS: replace with your name(s) in the translated version
        credits = _("translator-credits")
        if credits != "translator-credits":
            about.set_translator_credits(credits)
        about.present(self)

    # --- Connect IQ --------------------------------------------------------------------

    def load_apps(self) -> None:
        from openexpress import ciq

        s = self.session
        sec = self.sections["apps"]
        if not s.logged_in:
            sec.clear()
            sec.error.set_title("Connect IQ")
            sec.error.set_description(GLib.markup_escape_text(_("Sign in to manage apps.")))
            sec.show("error")
            return
        if s.ownership_error is not None:
            sec.clear()
            sec.error.set_title("Connect IQ")
            # Markup: a network error names <path>, <hex> or "<… object at …>" (escaped).
            sec.error.set_description(GLib.markup_escape_text(str(s.ownership_error)))
            sec.show("error")
            return

        def work():
            api = ciq.StoreApi(s.client)
            locale = s.settings.locale.replace("_", "-")
            state = ciq.read_installed(s.device, s.storage)
            # Apps without visible files cannot be removed over USB (e.g. on the fēnix 8).
            for app in state.store_apps:
                app.has_files = bool(ciq.files_on_device(s.device, s.storage, app))
                app.direct_removal = (not app.has_files and ciq.removable_directly(
                    s.device, s.storage, app))
                app.sandboxed = (not app.has_files and ciq.removal_sandboxed(
                    s.device, s.storage, app))
            account = api.account_apps(s.device, locale)
            ciq.name_pending(state, account)
            updates = api.updates(s.device, state.store_apps, locale)
            return state, account, updates

        self._load("apps", _("Loading Connect IQ apps…"), work, self.show_apps)

    def show_apps(self, result) -> None:
        if self.session is None:
            return
        state, account, updates = result
        sec = self.sections["apps"]
        if not state.waiting:
            # No app list waits on the device any more, so no update can be waiting either.
            self._updated_apps = set()
        updates = self._updates_to_offer(updates, self._updated_apps)
        upd = {u.app_id: u for u in updates}
        icons: dict = {}
        g = sec.group(_("On the device"), self._apps_space(state))
        if updates:
            b = self._button(_("Update all"), lambda: self.install_apps(updates),
                             "suggested-action")
            g.set_header_suffix(b)
        for a in state.store_apps:
            sub = caption(a.type_name, f"v{a.version or '?'}")
            if a.disabled:
                # TRANSLATORS: state of a Connect IQ app
                sub = caption(sub, _("disabled"))
            waiting = self._waiting_note(a, a.app_id in self._updated_apps)
            if waiting:
                # Written or deleted already; the device applies it once it is unplugged.
                row = Adw.ActionRow(title=GLib.markup_escape_text(a.name),
                                    subtitle=GLib.markup_escape_text(f"{sub}\n{waiting}"),
                                    subtitle_lines=2)
                icons[a.app_id] = self._app_icon(row)
                row.set_sensitive(not a.pending_removal)
                g.add(row)
                continue
            if a.app_id in upd:
                sub = caption(sub, _("update {version}").format(
                    version=upd[a.app_id].version_name))
            row = Adw.ActionRow(title=GLib.markup_escape_text(a.name),
                                subtitle=GLib.markup_escape_text(sub))
            icons[a.app_id] = self._app_icon(row)
            if a.app_id in upd:
                row.add_suffix(self._button(_("Update"),
                                            lambda u=upd[a.app_id]: self.install_apps([u]),
                                            "suggested-action"))
            if a.removable:
                row.add_suffix(self._trash(_("Remove {name}").format(name=a.name),
                                           lambda a=a: self.confirm_remove_app(a)))
            g.add(row)
        g = sec.group(_("Order"))
        blocked = self._order_blocked(state)
        row = Adw.ActionRow(
            title=_("Restore the order from a copy"),
            subtitle=GLib.markup_escape_text(
                blocked or _("A copy of the app list is made before every change")))
        row.add_suffix(self._button(_("Restore…"), lambda button: self.choose_order(button),
                                    with_button=True))
        if blocked:
            row.set_tooltip_text(blocked)
            row.set_sensitive(False)
        g.add(row)
        installed = {a.app_id for a in state.apps}
        missing = [a for a in account if a.app_id not in installed]
        g = sec.group(
            _("In your Garmin account"),
            _("Apps you added in the Connect IQ store (website or phone).") if missing
            else _("Add apps in the Connect IQ store (website or phone); "
                   "they will show up here."))
        for a in missing:
            row = Adw.ActionRow(title=GLib.markup_escape_text(a.name),
                                subtitle=GLib.markup_escape_text(
                                    caption(self._store_type_name(a), a.version_name,
                                            _human(a.size), a.developer)))
            icons[a.app_id] = self._app_icon(row)
            row.add_suffix(self._button(_("Install"), lambda a=a: self.install_apps([a])))
            g.add(row)
        self._load_app_icons(icons)

        builtin = sorted((a for a in state.apps if a.preloaded), key=lambda a: a.name)
        g = sec.group(_("Built-in activities and apps"),
                      _("Changes take effect after you disconnect the device."))
        search = Gtk.SearchEntry(placeholder_text=_("Search activities"), margin_bottom=12)
        g.add(search)
        exp = Adw.ExpanderRow(title=ngettext("Show {count} item", "Show {count} items",
                                             len(builtin)).format(count=len(builtin)))
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        listbox.append(exp)
        g.add(listbox)
        rows = []
        for a in builtin:
            sw = Adw.SwitchRow(title=GLib.markup_escape_text(a.name),
                               subtitle=GLib.markup_escape_text(a.type_name),
                               active=not a.disabled, sensitive=not self.busy)
            handler: list[int] = []
            handler.append(sw.connect(
                "notify::active",
                lambda row, _p, a=a, h=handler: self.toggle_app(a, row, h[0])))
            self._action_buttons.append(sw)
            exp.add_row(sw)
            rows.append((sw, a.name.casefold()))
        self._search_filter(search, rows, exp)

    @staticmethod
    def _app_icon(row: Adw.ActionRow) -> Gtk.Image:
        image = Gtk.Image(icon_name="application-x-addon-symbolic", pixel_size=32)
        row.add_prefix(image)
        return image

    def _load_app_icons(self, images: dict) -> None:
        from openexpress import ciq

        s = self.session
        if not images or not s.logged_in or s.ownership_error is not None:
            return

        def work():
            urls = ciq.StoreApi(s.client).icon_urls(list(images))
            return {app_id: cached_download(s.client.http, url, f"app-{app_id}.png")
                    for app_id, url in urls.items()}

        def done(files):
            for app_id, path in files.items():
                texture = load_texture(path) if path else None
                if texture and app_id in images:
                    images[app_id].set_from_paintable(texture)
                    images[app_id].set_pixel_size(32)

        self.in_background(work, done)

    @staticmethod
    def _store_type_name(app) -> str:
        from openexpress import ciq

        name = ciq.APP_TYPES.get(app.app_type) if app.store_type in ciq.STORE_TYPES else None
        return _(name) if name else app.store_type

    @staticmethod
    def _apps_space(state) -> str:
        slots = state.available_slots
        space = _human(state.available_space)
        if slots is None:
            return _("{size} free").format(size=space)
        return caption(ngettext("{count} free slot · {size} free",
                                "{count} free slots · {size} free", slots).format(
            count=slots, size=space))

    @staticmethod
    def _waiting_note(app, updated: bool = False) -> str:
        """For an app whose install, removal or update waits for the device to be
        unplugged. `updated`: a new version was written in this run of the program."""
        if app.pending:
            return _("Will be installed after the device is unplugged")
        if app.pending_removal:
            return _("Will be removed after the device is unplugged")
        if updated:
            return _("Will be updated after the device is unplugged")
        return ""

    @staticmethod
    def _updates_to_offer(updates, updated_ids) -> list:
        """The available updates without those already written to the device."""
        return [u for u in updates if u.app_id not in updated_ids]

    # --- the order of the apps -------------------------------------------------------

    @staticmethod
    def _order_blocked(state) -> str:
        """Why the order cannot be changed now; empty when it can."""
        if state.unreadable:
            return _("The app list on the device has entries that cannot be read")
        if state.waiting:
            return _("The device has not applied earlier changes yet. Unplug it, wait until "
                     "it is ready and try again.")
        return ""

    @staticmethod
    def _order_rows(choices) -> list[tuple[str, str, bool]]:
        """(title, subtitle, can be chosen) for each copy, newest first. Only the name of
        a copy is shown, never its path."""
        from openexpress import ciq

        rows = []
        for copy, plan in choices:
            n = len(plan.moved)
            rows.append((ciq.copy_title(copy),
                         ngettext("{count} position differs from now",
                                  "{count} positions differ from now", n).format(count=n)
                         if n else _("The same order as now"), bool(n)))
        return rows

    @staticmethod
    def _order_summary(plan, copy_note: str) -> str:
        from openexpress import ciq

        lines = [("  " if indented else "") + text
                 for text, indented in ciq.describe_order(plan, 10)]
        lines += ["", _("The same apps with the same flags; only the order changes."), copy_note]
        return "\n".join(lines)

    def choose_order(self, button: Gtk.Button | None = None) -> None:
        """Pick one of the saved copies of the app list to take the order from; read in a
        task started from `button` (Restore…), which spins meanwhile."""
        from openexpress import ciq

        s = self.session

        def work():
            state = ciq.read_installed(s.device, s.storage)
            # read_installed read the app list (OUT.BIN): it is there, nothing more to ask.
            return state, ciq.order_choices(s.device, state), ciq.copy_note(True)

        def show(result):
            state, choices, note = result
            blocked = self._order_blocked(state)
            if blocked:
                self.error(_("Restore the order from a copy"), blocked)
                return
            if not choices:
                self.error(_("Restore the order from a copy"),
                           _("There are no copies of the app list yet. A copy is made before "
                             "every change of the apps on the device."))
                return
            d = Adw.AlertDialog(
                heading=_("Restore the order from a copy"),
                body=_("Choose the copy of the app list to take the order from."))
            box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
            box.add_css_class("boxed-list")
            for (title, subtitle, usable), choice in zip(self._order_rows(choices), choices):
                row = Adw.ActionRow(title=GLib.markup_escape_text(title),
                                    subtitle=GLib.markup_escape_text(subtitle))
                row.set_sensitive(usable)
                row.set_selectable(usable)
                row.choice = choice
                box.append(row)
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          propagate_natural_height=True, max_content_height=360)
            scroller.set_child(box)
            d.set_extra_child(scroller)
            d.add_response("cancel", _("Cancel"))
            # TRANSLATORS: button that leads to the summary of the change
            d.add_response("next", _("Next"))
            d.set_response_enabled("next", False)
            # The window holds the dialog and the copy chosen while it is open: a handler that
            # held the dialog, or the list, would keep both after it closed.
            self._order_dialog, self._order_chosen = d, None

            def selected(_box, row):
                self._order_chosen = row.choice if row is not None else None
                dialog = getattr(self, "_order_dialog", None)
                if dialog is not None:
                    dialog.set_response_enabled("next", row is not None)

            def response(_dialog, r):
                if r == "next" and self._order_chosen is not None:
                    self.confirm_order(*self._order_chosen, note)

            box.connect("row-selected", selected)
            d.connect("response", response)
            d.connect("closed", lambda _d: (setattr(self, "_order_dialog", None),
                                            setattr(self, "_order_chosen", None)))
            d.present(self)

        self.run_task(_("Reading the copies of the app list…"), work, show, button=button)

    def confirm_order(self, copy, plan, note: str) -> None:
        """`note`: what happens before the change (ciq.copy_note), told by choose_order with
        the copies it read: the question asks the device nothing."""
        from openexpress import ciq

        d = Adw.AlertDialog(
            heading=_("Restore this order?"),
            body=ciq.copy_title(copy) + "\n\n" + self._order_summary(plan, note))
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Restore the Order"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda _d, r: r == "ok" and self.restore_order(copy))
        d.present(self)

    def restore_order(self, copy) -> None:
        from openexpress import ciq

        s = self.session

        def done(result):
            plan, _saved = result
            self.toast(_("Wrote the new order to the device; it takes effect after the device "
                         "is unplugged.") if plan.moved
                       else _("The apps on the device are already in this order."))
            self.load_section("apps", force=True)

        self.run_task(_("Restoring the order…"),
                      writes_to_device(lambda: ciq.restore_order(s.device, s.storage,
                                                                 copy.state)), done,
                      read_again_after_failure(self, "apps"))

    def install_apps(self, apps) -> None:
        from openexpress import ciq

        s = self.session

        def work():
            service.require_account_device(s.client, s.device.unit_id)
            return ciq.install_apps(s.device, s.storage, s.client, ciq.StoreApi(s.client),
                                    apps, progress=self.progress)

        def done(rep):
            self._updated_apps = set(getattr(self, "_updated_apps", ())) | set(rep.updated_ids)
            if rep.failed:
                # What was written first, then each app that was not (the USB connection
                # to renew, if any, comes last).
                self.error("Connect IQ", "\n".join(
                    [*MainWindow._written_apps(rep), *(f"{n}: {e}" for n, e in rep.failed)]))
            elif rep.for_phone and not rep.installed:
                self.error(_("Queued in Garmin Connect"), self._for_phone_text(
                    rep.for_phone, rep.sandboxed, rep.sandboxed_updates))
            elif rep.installed and all(n in rep.updated for n in rep.installed):
                self.toast(ciq.update_written_text(rep.installed))
            elif rep.installed and all(n in rep.direct for n in rep.installed):
                self.toast(ciq.written_text(rep.installed))
            elif rep.installed:
                self.toast(_("Installed {names}").format(names=", ".join(rep.installed)))
            else:
                self.toast(_("Queued; it will be installed on the next sync"))
            self.load_section("apps", force=True)

        self.run_task(_("Installing apps…"), writes_to_device(work), done,
                      read_again_after_failure(self, "apps"))

    @staticmethod
    def _written_apps(rep) -> list[str]:
        """What an install wrote, in the words of its toasts, a line for each kind: said
        before its failures when some apps were not installed."""
        from openexpress import ciq

        written = [n for n in rep.installed if n in rep.direct and n not in rep.updated]
        updated = [n for n in rep.installed if n in rep.updated]
        plain = [n for n in rep.installed if n not in rep.direct]
        return ([ciq.written_text(written)] if written else []) + (
            [ciq.update_written_text(updated)] if updated else []) + (
            [_("Installed {names}").format(names=", ".join(plain))] if plain else [])

    @staticmethod
    def _for_phone_text(names, sandboxed=(), updates=()) -> str:
        from openexpress import ciq

        if names and all(n in sandboxed for n in names):
            return ", ".join(names) + ": " + ciq.sandbox_text(
                all(n in updates for n in names), queued=True)
        return _("{names}: openExpress cannot install apps on this device over USB yet. The "
                 "app waits in the queue in Garmin Connect and will be installed by the phone "
                 "or Garmin Express.").format(
            names=", ".join(names))

    @staticmethod
    def _forget_text(app) -> str:
        from openexpress import ciq

        if getattr(app, "sandboxed", False):
            return (ciq.sandbox_removal_text() + "\n\n"
                    + _("Here you can only remove it from your account list."))
        return _("{name} cannot be removed over USB: this device shows no app files. Remove "
                 "it on the device itself or in Garmin Connect on the phone.\n\nHere you can "
                 "only remove it from your account list.").format(name=app.name)

    def confirm_forget_app(self, app) -> None:
        """For apps that cannot be removed over USB: only the account list can be changed."""
        from openexpress import ciq

        s = self.session
        d = Adw.AlertDialog(
            heading=_("Remove {name}?").format(name=app.name),
            body=self._forget_text(app))
        d.add_response("cancel", _("Cancel"))
        d.add_response("forget", _("Remove from Account"))

        def work():
            service.require_account_device(s.client, s.device.unit_id)
            ciq.StoreApi(s.client).forget(s.device, [app.app_id])

        d.connect("response", lambda _d, r: r == "forget" and self.run_task(
            _("Removing {name}…").format(name=app.name), work,
            lambda _r: (self.toast(_("Removed {name} from your account list.").format(
                name=app.name)), self.load_section("apps", force=True)),
            read_again_after_failure(self, "apps"), device=False))
        d.present(self)

    def confirm_remove_app(self, app) -> None:
        from openexpress import ciq

        s = self.session
        direct = bool(getattr(app, "direct_removal", False))
        if app.has_files is False and not direct:
            self.confirm_forget_app(app)
            return
        body = (_("The app will be deleted from the device over a direct USB connection; the "
                  "device disappears from the file manager for a moment. The device finishes "
                  "the removal after it is unplugged.") if direct
                else _("The app and its settings will be deleted from the device."))
        d = Adw.AlertDialog(heading=_("Remove {name}?").format(name=app.name), body=body)
        d.add_response("cancel", _("Cancel"))
        d.add_response("forget", _("Remove and forget"))
        d.add_response("ok", _("Remove"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_d, r):
            if r == "cancel":
                return

            def work():
                if r == "forget":
                    service.require_account_device(s.client, s.device.unit_id)
                removed = ciq.remove(s.device, s.storage, app)
                if r == "forget":
                    ciq.StoreApi(s.client).forget(s.device, [app.app_id])
                return removed

            def done(_removed):
                text = _("Removed {name}").format(name=app.name)
                if direct:
                    text += ". " + _("The device finishes the removal after it is unplugged.")
                self.toast(text)
                self.load_section("apps", force=True)

            self.run_task(_("Removing {name}…").format(name=app.name),
                          writes_to_device(work), done, read_again_after_failure(self, "apps"))

        d.connect("response", response)
        d.present(self)

    def toggle_app(self, app, row, handler_id: int) -> None:
        from openexpress import ciq

        enabled = row.get_active()
        if (not app.disabled) == enabled:
            return

        def revert():
            row.handler_block(handler_id)
            row.set_active(not app.disabled)
            row.handler_unblock(handler_id)

        s = self.session
        if self.busy or s is None:
            revert()
            if self.busy:
                self.toast(_("Another operation is in progress"))
            return
        msg = _("{name} enabled") if enabled else _("{name} disabled")

        def done(_r):
            app.disabled = not enabled
            self.toast(msg.format(name=app.name))

        def failed(_e):
            revert()
            return False

        # Gone well, only this row changes; failed, the page is read again: the list that
        # waited for the device (other switches) may be gone with it.
        self.run_task(_("Saving…"),
                      writes_to_device(lambda: ciq.set_enabled(s.device, s.storage, app.app_id,
                                                               enabled, app.app_type)),
                      done, read_again_after_failure(self, "apps", failed))


GC_INTERVAL_S = 30


def collect_garbage() -> bool:
    """Free reference cycles now. Always called on the GTK thread; True keeps a timer going."""
    gc.collect()
    return True


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


# A page whose drawing failed (an error of the program): under "Something went wrong", with
# Try again.
PAGE_NOT_SHOWN = N_("This page could not be shown. Try again; if it happens again, report the "
                    "problem with the log attached.")


def log_unexpected(what: str, exc_info) -> None:
    """An exception the window did not expect: to the log as ERROR, with its traceback
    (filtered, as every line), as the command line writes one."""
    log.error("unexpected error %s: %s", what, exc_info[0].__name__, exc_info=exc_info)


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
        _error(_("No display available ({details}): openExpress needs a graphical session")
               .format(details=told))
        return 1
    install_excepthook()
    return App().run(argv or [])


if __name__ == "__main__":
    raise SystemExit(main())
