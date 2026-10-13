"""openexpress graphical interface (GTK 4 + libadwaita)."""

from __future__ import annotations

import gc
import json
import logging
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import i18n, paths, service  # noqa: E402
from openexpress.auth import sso, store  # noqa: E402
from openexpress.cli import FINISH_STEPS_WINDOW, _human  # noqa: E402
from openexpress.connect import NotAccountDevice, OwnershipError  # noqa: E402
from openexpress.device.gio_storage import (  # noqa: E402
    ABSENT,
    PRESENT,
    USB_GAP_S,
    WAIT_FOR_DEVICE_S,
    garmin_usb,
    not_ready_text,
    own_remount,
    usb_state,
)
from openexpress.i18n import _, ngettext, pgettext  # noqa: E402
from openexpress.settings import (  # noqa: E402
    Settings,
    extract_consumer_from_dll,
    extract_url_salt_from_dll,
    save_consumer,
)

APP_ID = "io.github.tomaszbojanowski.OpenExpress"
USB_POLL_S = 2
log = logging.getLogger(__name__)


def gui_settings_path() -> Path:
    return paths.config_dir() / "gui.json"


def load_gui_settings() -> dict:
    p = gui_settings_path()
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def save_gui_settings(data: dict) -> None:
    paths.write_private(gui_settings_path(), json.dumps(data, indent=2))


DEFAULT_WINDOW = (1000, 740)
MIN_WINDOW = (360, 480)
MAX_WINDOW = 10000


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


def format_when(ts: float) -> str:
    when = datetime.fromtimestamp(ts)
    now = datetime.now()
    clock = when.strftime("%H:%M")
    if when.date() == now.date():
        return _("Today at {time}").format(time=clock)
    if when.date() == (now - timedelta(days=1)).date():
        return _("Yesterday at {time}").format(time=clock)
    return when.strftime("%x %H:%M")


def cached_download(http, url: str, name: str) -> Path | None:
    """Downloads a small file (picture, icon) once into the cache."""
    target = paths.cache_dir() / "images" / name
    if target.exists() and target.stat().st_size:
        return target
    try:
        resp = http.get(url, timeout=20)
        if not resp.ok or not resp.content:
            return None
    except Exception:  # noqa: BLE001 - pictures are optional
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(resp.content)
    return target


DEVICE_PICTURE_SIZE = 180


# The picture of a device that the Garmin account cannot give (the device is not on the
# account, or nobody is logged in) comes from Garmin's update server instead. Only a PNG from
# this host is taken, small and quickly, or the generic icon stays.
PICTURE_HOST = "https://res.garmin.com/"
PICTURE_MAX_BYTES = 2 * 1024 * 1024
PICTURE_TIMEOUT_S = 15
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def picture_from_unit_info(client, device) -> str | None:
    """The address of the device's picture according to Garmin's update server. The rest of
    the answer (unit ID, serial number…) is dropped here and goes nowhere."""
    try:
        info = client.unit_info(device.unit_id, device.part_number)
    except Exception as e:  # noqa: BLE001 - optional; the error may quote the answer
        log.info("device picture: unit-info failed (%s)", type(e).__name__)
        return None
    url = info.get("graphicurl") if isinstance(info, dict) else None
    return url if isinstance(url, str) and url else None


def download_picture(http, url: str, name: str,
                     clock=time.monotonic) -> tuple[Path | None, str]:
    """Download a device picture into the cache: (file, "") or (None, why not)."""
    if not url.startswith(PICTURE_HOST):
        return None, "not res.garmin.com"
    deadline = clock() + PICTURE_TIMEOUT_S
    data = bytearray()
    try:
        with http.get(url, timeout=PICTURE_TIMEOUT_S, stream=True,
                      allow_redirects=False) as resp:
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            kind = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if kind != "image/png":
                return None, "not a PNG"
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
    if not bytes(data).startswith(PNG_SIGNATURE):
        return None, "not a PNG"
    target = paths.cache_dir() / "images" / name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        partial.write_bytes(data)
        partial.replace(target)  # never a half-written picture in the cache
    except OSError:
        return None, "cannot write the cache"
    return target, ""


def device_picture_path(part_number: str) -> Path:
    """Cached product picture of a watch model (the picture never changes for a model)."""
    safe = "".join(c for c in part_number if c.isalnum() or c in "-_") or "unknown"
    return paths.cache_dir() / "images" / f"device-{safe}.png"


def load_texture(path: Path, size: int):
    """Picture as a texture scaled to `size`; handles formats GTK cannot load (e.g. GIF)."""
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, GdkPixbuf

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
        return Gdk.Texture.new_for_pixbuf(pixbuf)
    except GLib.Error:
        return None


def trash_button(tooltip: str, cb) -> Gtk.Button:
    b = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.add_css_class("flat")
    b.connect("clicked", lambda *_a: cb())
    return b


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


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="openExpress")
        self.set_size_request(*MIN_WINDOW)
        self.session: service.DeviceSession | None = None
        # What the pages show while no device is open; the Sync button says the same.
        self._usb_title = ""
        self.busy = False
        self.gui_settings = load_gui_settings()
        width, height, maximized = window_geometry(self.gui_settings)
        self.set_default_size(width, height)
        if maximized:
            self.maximize()
        self.maps_cache = None
        self.apps_cache = None
        self.updates_cache = None
        self.update_state: str | None = None  # None = unknown, "current", "available"
        self.update_count = 0
        self._update_check_session = None
        self._action_buttons: list[Gtk.Widget] = []
        self._retry_sources: dict[str, int] = {}
        self._refresh_pending = False
        self._last_sync_children: list[Gtk.Widget] = []
        self._close_dialog = None

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
        GLib.timeout_add_seconds(USB_POLL_S, self._poll_usb)
        self.refresh_device()

    # --- layout -----------------------------------------------------------------------

    def _build_layout(self) -> None:
        self.split = Adw.NavigationSplitView(min_sidebar_width=210, max_sidebar_width=260)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("Preferences"), "app.preferences")
        if hasattr(Adw, "ShortcutsDialog"):
            section.append(_("Keyboard Shortcuts"), "app.shortcuts")
        section.append(_("Report a Device or Problem…"), "app.report")
        section.append(_("About openExpress"), "app.about")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     tooltip_text=_("Main Menu"), primary=True)
        sidebar_header = Adw.HeaderBar()
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
        self.split.set_sidebar(Adw.NavigationPage(title="openExpress", child=sidebar_view))

        content_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
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
        spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
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
        self.action_bar.pack_start(spinner)
        self.action_bar.set_center_widget(box)
        content_view.add_bottom_bar(self.action_bar)

        self.content_page = Adw.NavigationPage(title=self.sections["watch"].title,
                                               child=content_view)
        self.split.set_content(self.content_page)

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 620sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        breakpoint.add_setter(self.sync_button, "visible", False)
        breakpoint.add_setter(self.sync_icon_button, "visible", True)
        self.add_breakpoint(breakpoint)

        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)
        self.nav.select_row(self.nav.get_row_at_index(0))

    def _install_actions(self) -> None:
        for name, cb in (("refresh", lambda *_a: self.refresh_device()),
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

    def set_busy(self, busy: bool, text: str = "", device: bool = False) -> None:
        self.busy = busy
        self.action_bar.set_revealed(busy)
        self.progress_label.set_text(text)
        self.unplug_label.set_visible(busy and device)
        self.progress_bar.set_fraction(0)
        self._action_buttons = [b for b in self._action_buttons if b.get_root() is not None]
        for b in self._action_buttons:
            b.set_sensitive(not busy)
        self.banner.set_sensitive(not busy)
        self._update_actions()

    def progress(self, text: str) -> None:
        GLib.idle_add(self.progress_label.set_text, text)
        GLib.idle_add(self.progress_bar.pulse)

    def fraction(self, done: int, total: int | None) -> None:
        if total:
            GLib.idle_add(self.progress_bar.set_fraction, min(1.0, done / total))

    def run_task(self, title: str, fn, on_done=None, on_error=None, device: bool = True) -> None:
        """Run `fn()` in a thread; the result is passed back to the GUI thread.

        `device` is False for a task that does not touch the connected device: the user is
        then not told to keep it plugged in."""
        if self.busy:
            self.toast(_("Another operation is in progress"))
            return
        self.set_busy(True, title, device=device and self.session is not None)
        log.info("task: %s", fn.__qualname__.split(".<locals>")[0] if hasattr(fn, "__qualname__")
                 else "?")

        def worker():
            try:
                result = fn()
            except Exception as e:  # noqa: BLE001
                log.debug("%s", traceback.format_exc())
                GLib.idle_add(finish_error, e)
            else:
                GLib.idle_add(finish_ok, result)

        def finish_ok(result):
            collect_garbage()
            self.set_busy(False)
            try:
                if on_done:
                    on_done(result)
            finally:
                self._run_pending_refresh()
            return False

        def finish_error(e):
            log.warning("task failed: %s: %s", type(e).__name__, e)
            collect_garbage()
            self.set_busy(False)
            try:
                if not (on_error and on_error(e)):
                    self.error(title, str(e) or e.__class__.__name__)
            finally:
                self._run_pending_refresh()
            return False

        # Not a daemon thread: the process must not exit while the watch is being written.
        threading.Thread(target=worker, daemon=False).start()

    def _run_pending_refresh(self) -> None:
        if self._refresh_pending and not self.busy:
            self._refresh_pending = False
            self.refresh_device()

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

    def _button(self, label: str, cb, style: str | None = None) -> Gtk.Button:
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if style:
            b.add_css_class(style)
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
        if own_remount(mount):
            return  # mounted again by our own install; the open device stays as it is
        if garmin_usb(mount):
            GLib.timeout_add(1500, lambda: (self.refresh_device(), False)[1])

    def _on_mount_removed(self, _mon, mount):
        if own_remount(mount):
            return
        # The USB device is already gone, so compare with the device that is open.
        uri = mount.get_root().get_uri().rstrip("/") + "/"
        s = self.session
        ours = s is not None and str(getattr(s.storage, "label", "")).startswith(uri)
        if ours or garmin_usb(mount):
            self.session = None
            self._updated_apps = set()  # unplugged: the device applies what was waiting
            self.show_device()

    @staticmethod
    def _usb_notice(state: str, waited_s: float) -> tuple[str, str, bool]:
        """(title, description, busy) of the placeholder while no device is open."""
        if state == ABSENT:
            return (_("Connect your device"),
                    _("Connect your Garmin device with a USB cable. "
                      "It will be detected automatically."), False)
        if waited_s < WAIT_FOR_DEVICE_S:
            return (_("The device is starting…"),
                    _("This takes a moment after the cable is plugged in."), True)
        return _("The device is not ready"), not_ready_text(state), False

    def _poll_usb(self, clock=time.monotonic) -> bool:
        """Every few seconds while no device is open: is a Garmin device on USB that gvfs
        does not offer yet? Only /sys is read; nothing is sent to the device."""
        if self.session is not None or self.busy:
            self._usb_state = ABSENT
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
            GLib.timeout_add(2500, lambda: (self.refresh_device(), False)[1])
        self._usb_state = state
        notice = self._usb_notice(state, clock() - self._usb_since if state != ABSENT else 0)
        for sec in self.sections.values():
            sec.set_empty(*notice)
        if notice[0] != self._usb_title:
            self._usb_title = notice[0]
            self._update_actions()  # the Sync button says the same as the pages
        return True

    def refresh_device(self) -> None:
        if self.busy:
            # E.g. the watch was replugged during a task: detect it once the task ends.
            self._refresh_pending = True
            return
        self._refresh_pending = False

        def done(sess):
            self.session = sess
            self.show_device()

        def failed(_e):
            self.session = None
            self.show_device()
            return True

        if self.session is None:
            self.sections["watch"].show("loading")
        self.run_task(_("Detecting the device…"),
                      lambda: service.check_session(service.open_session()), done, failed)

    def show_device(self) -> None:
        self.maps_cache = self.updates_cache = self.apps_cache = None
        self.update_state = None
        for sec in self.sections.values():
            sec.loaded = False
            sec.clear()
            sec.show("empty" if self.session is None else "loading")
        self._update_banner()
        self._update_actions()
        if self.session is None:
            return
        self.show_overview()
        self.load_section(self.pages.get_visible_child_name())

    def _update_banner(self) -> None:
        s = self.session
        if s is not None and s.client.consumer is None:
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
            self.refresh_device()
        else:
            self.login()

    def load_section(self, key: str, force: bool = False) -> None:
        sec = self.sections[key]
        if self.session is None or (sec.loaded and not force) or key == "watch":
            if key == "watch" and self.session is not None and force:
                self.refresh_device()
            return
        loader = {"updates": self.load_updates, "maps": self.load_maps,
                  "apps": self.load_apps, "music": self.load_music}[key]
        loader()

    def _load(self, key: str, title: str, work, show, device: bool = True) -> None:
        """Common pattern: spinner in the section, work in a thread, then `show(result)`."""
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
            show(result)
            sec.show("content")

        def failed(e):
            if self.session is sess:
                sec.show_error(str(e) or e.__class__.__name__)
            return True

        if self.busy:
            # Another task runs; try again shortly so the page does not stay empty.
            def retry():
                self._retry_sources.pop(key, None)
                if self.session is sess:
                    self._load(key, title, work, show, device)
                return False

            self._retry_sources[key] = GLib.timeout_add(700, retry)
            return
        self.run_task(title, work, done, failed, device=device)

    # --- overview ---------------------------------------------------------------------

    def show_overview(self) -> None:
        s = self.session
        sec = self.sections["watch"]
        sec.clear()
        d = s.device

        hero = sec.group()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                      margin_bottom=12)
        self.device_image = Gtk.Image(icon_name=APP_ID, pixel_size=128,
                                      height_request=DEVICE_PICTURE_SIZE)
        self.device_picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                          visible=False)
        self.device_picture.set_size_request(DEVICE_PICTURE_SIZE, DEVICE_PICTURE_SIZE)
        box.append(self.device_image)
        box.append(self.device_picture)
        title = Gtk.Label(label=d.description, wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-1")
        box.append(title)
        self.software_label = Gtk.Label(
            label=_("Software {version}").format(version=d.software_version_str))
        self.software_label.add_css_class("dim-label")
        box.append(self.software_label)
        hero.add(box)
        self._show_device_picture()

        free, total = s.storage.free_space(), s.storage.capacity()
        g = sec.group(_("Storage"))
        row = Adw.ActionRow(title=_("Device storage"))
        if free is not None and total:
            row.set_subtitle(_("{free} free of {total}").format(free=_human(free),
                                                                 total=_human(total)))
            bar = Gtk.LevelBar(min_value=0, max_value=1, value=1 - free / total,
                               valign=Gtk.Align.CENTER, width_request=120)
            bar.add_offset_value("low", 0.75)
            bar.add_offset_value("high", 0.9)
            bar.add_offset_value("full", 1.0)
            row.add_suffix(bar)
        else:
            row.set_subtitle(_("{size} free").format(size=_human(free)))
        g.add(row)

        g = sec.group(_("Sync with Garmin Connect"))
        if s.ownership_error is not None:
            g.add(Adw.ActionRow(title=_("Sync is not available for this device"),
                                subtitle=GLib.markup_escape_text(str(s.ownership_error))))
        else:
            self.last_sync_row = Adw.ExpanderRow(title=_("Last sync"))
            self._last_sync_children = []
            g.add(self.last_sync_row)
            self._show_last_sync()
        if s.ownership_error is None and not self._background_enabled():
            row = Adw.ActionRow(title=_("Automatic sync is off"),
                                subtitle=_("Turn it on to sync whenever the device is connected"))
            # TRANSLATORS: button that turns on automatic sync
            row.add_suffix(self._button(_("Turn On"), lambda: self.set_background(True)))
            g.add(row)

        g = sec.group(pgettext("tab", "Updates"))
        self.update_row = Adw.ActionRow(title=_("Device software"), activatable=True)
        self.update_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.update_row.connect("activated", lambda *_a: self.select_section("updates"))
        g.add(self.update_row)
        self._render_update_state()
        if self.update_state is None:
            self._check_updates_quietly()

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
                    _("Set as Primary"), lambda: self.confirm_primary_training(status)))

        def failed(_e):
            if self.session is s:
                row.set_subtitle(_("Could not check"))

        self.in_background(
            lambda: service.primary_training_status(s.client, s.device, s.account_devices),
            done, failed)

    def confirm_primary_training(self, status) -> None:
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
        d.connect("response", lambda _d, r: r == "set" and self.set_primary_training())
        d.present(self)

    def set_primary_training(self) -> None:
        s = self.session
        if not s or not s.logged_in or s.ownership_error is not None:
            return

        def done(_before):
            self.toast(_("Primary training device changed"))
            if self.session is s:
                self.show_overview()

        self.run_task(_("Changing the primary training device…"),
                      lambda: service.set_primary_training(s.client, s.device, apply=True), done,
                      device=False)

    def _set_device_texture(self, texture) -> None:
        self.device_picture.set_paintable(texture)
        self.device_picture.set_visible(True)
        self.device_image.set_visible(False)

    def _show_device_picture(self) -> None:
        """Shows the watch picture at once from memory or disk; downloads it only once."""
        s = self.session
        part = s.device.part_number
        cache = getattr(self, "_device_textures", None)
        if cache is None:
            cache = self._device_textures = {}
        if part not in cache:
            path = device_picture_path(part)
            if path.exists():
                cache[part] = load_texture(path, 2 * DEVICE_PICTURE_SIZE)
                log.info("device picture: from the cache")
        if cache.get(part) is not None:
            self._set_device_texture(cache[part])
            return
        self.device_image.set_visible(True)
        self.device_picture.set_visible(False)
        # The account is asked only about a device that is on it.
        on_account = s.logged_in and s.ownership_error is None
        name = device_picture_path(part).name

        def work():
            """(file, where it came from) or (None, why there is no picture)."""
            if on_account:
                try:
                    url = s.client.device_image_url(s.device.unit_id)
                except Exception as e:  # noqa: BLE001 - optional; the other source is tried
                    log.info("device picture: the account failed (%s)", type(e).__name__)
                    url = None
                path = cached_download(s.client.http, url, name) if url else None
                if path:
                    return path, "the account"
            url = picture_from_unit_info(s.client, s.device)
            if not url:
                return None, "unit-info gave no address"
            path, why = download_picture(s.client.http, url, name)
            return (path, "unit-info") if path else (None, f"download refused: {why}")

        def done(result):
            path, source = result
            texture = load_texture(path, 2 * DEVICE_PICTURE_SIZE) if path else None
            if texture is None:
                log.info("device picture: none (%s)", source if path is None
                         else "the file is not a picture")
                return
            log.info("device picture: from %s", source)
            cache[part] = texture
            if self.session is s:
                self._set_device_texture(texture)

        self.in_background(work, done)

    def _render_update_state(self) -> None:
        """Shows self.update_state in the overview ("Watch software" row, software label)."""
        if self.session is None or not hasattr(self, "update_row"):
            return
        version = _("Software {version}").format(
            version=self.session.device.software_version_str)
        if self.update_state == "available":
            self.update_row.set_subtitle(ngettext(
                "{count} update available", "{count} updates available",
                self.update_count).format(count=self.update_count))
            self.software_label.set_label(version + " · " + _("update available"))
        elif self.update_state == "current":
            self.update_row.set_subtitle(_("Software is up to date."))
            self.software_label.set_label(version)
        elif self.update_state == "failed":
            self.update_row.set_subtitle(_("Could not check for updates"))
            self.software_label.set_label(version)
        else:
            self.update_row.set_subtitle(_("Checking…"))
            self.software_label.set_label(version)

    def _set_update_state(self, new_updates: list) -> None:
        self.update_count = len(new_updates)
        self.update_state = "available" if new_updates else "current"
        self._render_update_state()

    def _check_updates_quietly(self) -> None:
        from openexpress import updates as upd

        s = self.session
        if s is None or self._update_check_session is s:
            return  # no watch, or a check for this watch is already running
        self._update_check_session = s

        def work():
            return upd.pending(upd.mark_present(
                s.storage, upd.parse_updates(s.client.software_updates(s.device.raw))))

        def done(new):
            if self._update_check_session is s:
                self._update_check_session = None
            if self.session is not s:
                return
            self._set_update_state(new)

        def failed(_e):
            if self._update_check_session is s:
                self._update_check_session = None
            if self.session is not s or self.update_state is not None:
                return
            self.update_state = "failed"
            self._render_update_state()

        self.in_background(work, done, failed)

    def _show_last_sync(self) -> None:
        if (not hasattr(self, "last_sync_row") or self.session is None
                or self.session.ownership_error is not None):
            return
        row = self.last_sync_row
        for child in self._last_sync_children:
            row.remove(child)
        self._last_sync_children = []
        data = service.load_last_sync(self.session.device.unit_id)
        if not data:
            row.set_subtitle(_("Not synced yet with openExpress"))
            row.set_enable_expansion(False)
            return
        summary = ngettext("{count} file uploaded", "{count} files uploaded",
                           data.get("uploaded", 0)).format(count=data.get("uploaded", 0)) \
            + " · " + ngettext("{count} received", "{count} received",
                               data.get("downloaded", 0)).format(count=data.get("downloaded", 0))
        errors = data.get("errors") or []
        if errors:
            summary += " · " + ngettext("{count} error", "{count} errors",
                                        len(errors)).format(count=len(errors))
        row.set_subtitle(GLib.markup_escape_text(
            format_when(data.get("time", time.time())) + " · " + summary))
        row.set_enable_expansion(True)
        for line in (data.get("lines") or []) + errors:
            child = Adw.ActionRow(title=GLib.markup_escape_text(line), title_lines=3)
            child.add_css_class("property")
            row.add_row(child)
            self._last_sync_children.append(child)

    def _watch_last_sync(self) -> None:
        """Updates the overview when the background service finishes a sync."""
        path = service.last_sync_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._last_sync_monitor = Gio.File.new_for_path(str(path)).monitor_file(
            Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._last_sync_monitor.connect("changed", lambda *_a: self._show_last_sync())

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

    def show_preferences(self) -> None:
        dialog = Adw.PreferencesDialog()
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
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
            dialog.add_toast(Adw.Toast(title=_("Restart the application to change the language")))

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
        dialog.add(page)
        dialog.present(self)

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
                self.session = None
                self.refresh_device()

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
                self.session = None
                self.refresh_device()

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
        self.session = None
        self.refresh_device()

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
        if not s or not s.logged_in or s.ownership_error is not None:
            return

        def done(summary: service.SyncSummary):
            self.toast(_("Sync finished") if not summary.errors
                       else _("Sync finished with errors"))
            self._show_last_sync()

        def failed(e):
            if isinstance(e, service.FirstSyncChoiceNeeded):
                self.ask_first_sync()
                return True
            if isinstance(e, OwnershipError) and self.session is s:
                # The account or its device list changed since the watch was detected.
                s.ownership_error = e
                self.show_device()
            return False

        self.run_task(_("Syncing…"),
                      lambda: service.run_sync(s, first_run, self.progress), done, failed)

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
        # Asks Garmin's server, then compares the offered files with those on the device.
        self._load("updates", _("Checking for updates…"),
                   lambda: upd.mark_present(s.storage, upd.parse_updates(
                       s.client.software_updates(s.device.raw, include_reinstalls=True))),
                   self.show_updates)

    def show_updates(self, items) -> None:
        if self.session is None:
            return
        self.updates_cache = items
        sec = self.sections["updates"]
        d = self.session.device
        from openexpress import updates as upd

        new = upd.pending(items)
        self._set_update_state(new)
        if not new:
            g = sec.group()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                          halign=Gtk.Align.CENTER, margin_top=24, margin_bottom=24)
            icon = Gtk.Image(icon_name="object-select-symbolic", pixel_size=64)
            icon.add_css_class("success")
            box.append(icon)
            title = Gtk.Label(label=_("Software is up to date."))
            title.add_css_class("title-2")
            box.append(title)
            sub = Gtk.Label(label=_("{model} · software {version}").format(
                model=d.description, version=d.software_version_str))
            sub.add_css_class("dim-label")
            box.append(sub)
            check = Gtk.Button(label=_("Check Again"), halign=Gtk.Align.CENTER, margin_top=8)
            check.add_css_class("pill")
            check.connect("clicked", lambda *_a: self.load_section("updates", force=True))
            box.append(check)
            g.add(box)
        else:
            g = sec.group(_("Available updates"))
            for u in new:
                self._update_row(g, u)
        # A file the device already has is a reinstall, whatever the server calls it.
        re = [u for u in items if (u.is_reinstall or u.on_device) and not u.is_metadata]
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

    def _update_row(self, parent, u, reinstall: bool = False) -> None:
        row = Adw.ActionRow(
            title=GLib.markup_escape_text(u.title),
            subtitle=GLib.markup_escape_text(
                f"{u.version} · {_human(u.size)} · {u.severity_text}"
                # TRANSLATORS: marks the main firmware in an update list
                + (" · " + _("system") if u.is_primary else "")
                + (f" · {u.filename}" if u.filename else "")),
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
        chosen = upd.select(self.updates_cache, [u.part_number])
        body = (f"{u.title} {u.version}\n"
                + _("Size: {size}").format(size=_human(u.size)) + "\n"
                + _("On the device: {path}").format(path=u.device_path)
                + ("\n\n" + _("Changes:") + "\n" + "\n".join("• " + c for c in u.changes[:10])
                   if u.changes else "")
                + ("\n\n" + _("License: {url}").format(url=u.eula_url) if u.eula_url else "")
                + ("\n\n" + _("Do not disconnect the device while copying.")
                   if u.is_primary else ""))
        d = Adw.AlertDialog(heading=_("Install update?"), body=body)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Accept and install"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def response(_d, r):
            if r != "ok":
                return
            self.run_task(
                _("Installing update…"),
                lambda: upd.install(chosen, s.storage, session=s.client.http,
                                    progress=self.progress),
                lambda rep: self.error(_("Done"), self._update_done_text(rep)),
            )

        d.connect("response", response)
        d.present(self)

    # --- maps -------------------------------------------------------------------------

    def load_maps(self) -> None:
        from openexpress import maps as mp

        s = self.session
        self._load("maps", _("Checking maps…"),
                   lambda: mp.parse_maps(s.client.universal_maps(s.device.raw)),
                   self.show_maps, device=False)  # only asks Garmin's server

    def show_maps(self, all_maps) -> None:
        if self.session is None:
            return
        self.maps_cache = all_maps
        sec = self.sections["maps"]
        s = self.session
        free, total = s.storage.free_space(), s.storage.capacity()
        installed = sorted((m for m in all_maps if m.installed), key=lambda m: (m.map_type, m.name))
        g = sec.group(_("On the device"))
        if free is not None and total:
            used_by_maps = sum(m.size for m in installed)
            row = Adw.ActionRow(
                title=_("{free} free of {total}").format(free=_human(free), total=_human(total)),
                subtitle=_("Maps use {size}").format(size=_human(used_by_maps)))
            row.add_suffix(Gtk.LevelBar(min_value=0, max_value=1, value=1 - free / total,
                                        valign=Gtk.Align.CENTER, width_request=120))
            g.add(row)
        for m in installed:
            row = Adw.ActionRow(title=GLib.markup_escape_text(m.name),
                                subtitle=GLib.markup_escape_text(
                                    f"{m.map_type} · {m.version} · {_human(m.size)} · {m.status}"))
            if m.update_available:
                # TRANSLATORS: button
                row.add_suffix(self._button(_("Update"), lambda m=m: self.confirm_map(m),
                                            "suggested-action"))
            if m.can_uninstall:
                row.add_suffix(self._trash(_("Remove {name}").format(name=m.name),
                                           lambda m=m: self.confirm_remove(m)))
            g.add(row)

        available = sorted((m for m in all_maps if not m.installed),
                           key=lambda m: (m.region, m.name))
        if not available:
            return
        # TRANSLATORS: group title: maps that can be installed
        g = sec.group(_("Available"), _("Free maps available for this device"))
        search = Gtk.SearchEntry(placeholder_text=_("Search maps or regions"), margin_bottom=12)
        g.add(search)
        rows = []
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        for m in available:
            row = Adw.ActionRow(title=GLib.markup_escape_text(m.name),
                                subtitle=GLib.markup_escape_text(
                                    f"{m.region} · {m.version} · {_human(m.size)}"))
            row.add_suffix(self._button(_("Install"), lambda m=m: self.confirm_map(m)))
            listbox.append(row)
            rows.append((row, f"{m.name} {m.region} {m.map_type}".casefold()))
        g.add(listbox)
        self._search_filter(search, rows)

    def confirm_map(self, m) -> None:
        from openexpress import maps as mp

        s = self.session
        if not (s.client.consumer and s.client.consumer.url_salt):
            self.error(_("Setup missing"),
                       _("Downloading maps requires the Utilities.dll file from Garmin Express.\n"
                         "Run: openexpress setup --utilities-dll /path/to/Utilities.dll"))
            return
        removing = mp.removal_list(self.maps_cache, m, rewriting=True)
        need = mp.space_needed(s.storage, m, removing)
        body = (_("Download: {size}").format(size=_human(m.size)) + "\n"
                + _("Space on the device: {needed} ({free} free)").format(
                    needed=_human(need), free=_human(s.storage.free_space())) + "\n"
                + _("Files: {count}").format(count=len(m.files))
                + ("\n" + _("Old files to remove: {count}").format(count=len(removing))
                   if removing else "")
                + "".join("\n\n" + _("License: {url}").format(url=e) for e in m.eula_urls)
                + "\n\n" + _("Copying large maps over USB can take tens of minutes."))
        heading = (_("Update {name}?") if m.installed else _("Install {name}?")).format(
            name=m.name)
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Accept and install"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def response(_d, r):
            if r != "ok":
                return
            self.run_task(
                _("Installing {name}…").format(name=m.name),
                lambda: mp.install(m, self.maps_cache, s.storage,
                                   activate=lambda ident: s.client.activate_maps(
                                       s.device.raw, [ident]),
                                   session=s.client.http, progress=self.progress,
                                   transfer_progress=self.fraction,
                                   url_salt=s.client.consumer.url_salt),
                lambda rep: (self.toast(_("Installed {name}").format(name=m.name)),
                             self.load_section("maps", force=True)),
            )

        d.connect("response", response)
        d.present(self)

    def confirm_remove(self, m) -> None:
        from openexpress import maps as mp

        s = self.session
        files = mp.removal_list(self.maps_cache, m, rewriting=False)
        d = Adw.AlertDialog(heading=_("Remove {name}?").format(name=m.name),
                            body=_("These files will be removed from the device:") + "\n"
                            + "\n".join(files))
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Remove"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _d, r: r == "ok" and self.run_task(
            _("Removing {name}…").format(name=m.name),
            lambda: mp.uninstall(m, self.maps_cache, s.storage, progress=self.progress),
            lambda removed: (self.toast(self._removed_files(len(removed))),
                             self.load_section("maps", force=True))))
        d.present(self)

    @staticmethod
    def _report_text(report_text: str, attach_log: bool) -> str:
        """The report, with the end of the log when the user asked for it."""
        from openexpress import applog, report

        if not attach_log:
            return report_text
        return report.with_log(report_text, applog.tail(report.LOG_LINES))

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
            body_text = report.build(s.device, s.storage, s.logged_in)
            text = _("Copy this report and paste it into a new issue on GitHub. "
                     "It contains no serial number, unit ID or e-mail.")
        log.info("report dialog opened (device connected: %s)", s is not None)
        d = Adw.AlertDialog(heading=_("Report a Device or Problem"), body=text)
        buffer = None
        if body_text:
            box, buffer = self._report_view(body_text)
            d.set_extra_child(box)
            d.add_response("copy", _("Copy"))
        d.add_response("close", _("Close"))
        d.add_response("open", _("Copy and Open GitHub"))
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
                if buffer is not None:
                    self.get_clipboard().set(shown())
                Gtk.UriLauncher.new(report.ISSUE_URL).launch(self, None, None, None)

        d.connect("response", response)
        d.present(self)

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
            sec.error.set_description(_("Sign in to manage apps."))
            sec.show("error")
            return
        if s.ownership_error is not None:
            sec.clear()
            sec.error.set_title("Connect IQ")
            sec.error.set_description(str(s.ownership_error))
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
        self.apps_cache = result
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
            sub = f"{a.type_name} · v{a.version or '?'}"
            if a.disabled:
                # TRANSLATORS: state of a Connect IQ app
                sub += " · " + _("disabled")
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
                sub += " · " + _("update {version}").format(version=upd[a.app_id].version_name)
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
        row.add_suffix(self._button(_("Restore…"), self.choose_order))
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
                                    f"{self._store_type_name(a)} · {a.version_name} · "
                                    f"{_human(a.size)} · {a.developer}"))
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
                texture = load_texture(path, 64) if path else None
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
        return ngettext("{count} free slot · {size} free", "{count} free slots · {size} free",
                        slots).format(count=slots, size=space)

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
            return _("The app list on the device has entries that cannot be read.")
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

    def choose_order(self) -> None:
        """Pick one of the saved copies of the app list to take the order from."""
        from openexpress import ciq

        s = self.session

        def work():
            state = ciq.read_installed(s.device, s.storage)
            return state, ciq.order_choices(s.device, state)

        def show(result):
            state, choices = result
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
            box.connect("row-selected",
                        lambda _b, row: d.set_response_enabled("next", row is not None))
            d.connect("response", lambda _d, r: r == "next" and box.get_selected_row()
                      and self.confirm_order(*box.get_selected_row().choice))
            d.present(self)

        self.run_task(_("Reading the copies of the app list…"), work, show)

    def confirm_order(self, copy, plan) -> None:
        from openexpress import ciq

        s = self.session
        d = Adw.AlertDialog(
            heading=_("Restore this order?"),
            body=ciq.copy_title(copy) + "\n\n" + self._order_summary(
                plan, ciq.app_list_copy_note(s.device, s.storage)))
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
                      lambda: ciq.restore_order(s.device, s.storage, copy.state), done)

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
                self.error("Connect IQ", "\n".join(f"{n}: {e}" for n, e in rep.failed))
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

        self.run_task(_("Installing apps…"), work, done)

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
                name=app.name)), self.load_section("apps", force=True)), device=False))
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

            self.run_task(_("Removing {name}…").format(name=app.name), work, done)

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

        self.run_task(_("Saving…"),
                      lambda: ciq.set_enabled(s.device, s.storage, app.app_id, enabled,
                                              app.app_type),
                      done, failed)

    # --- music ------------------------------------------------------------------------

    def load_music(self) -> None:
        from openexpress import music

        s = self.session
        if not music.supported_kinds(s.device):
            sec = self.sections["music"]
            sec.clear()
            sec.error.set_title(pgettext("tab", "Music"))
            sec.error.set_description(music.no_media_message())
            sec.show("error")
            return
        self._load("music", _("Reading music on the device…"),
                   lambda: (music.list_device(s.device, s.storage),
                            music.list_playlists(s.device, s.storage)),
                   self.show_music)

    def show_music(self, result) -> None:
        from openexpress import music

        if self.session is None:
            return
        items, playlists = result
        sec = self.sections["music"]
        free = self.session.storage.free_space()
        # Only the kinds of media this device declares.
        kinds = music.supported_kinds(self.session.device)
        g = sec.group(_("Add to the device"), _("Free space: {size}").format(size=_human(free)))
        for kind in kinds:
            row = Adw.ActionRow(title=_(music.KIND_NAMES[kind]))
            row.add_suffix(self._button(_("Folders…"), lambda k=kind: self.choose_music(k, True)))
            row.add_suffix(self._button(_("Files…"), lambda k=kind: self.choose_music(k, False)))
            g.add(row)
        on_watch = {"track": _("Music on the device"), "podcast": _("Podcasts on the device"),
                    "audiobook": _("Audiobooks on the device")}
        for kind in kinds:
            files = [i for i in items if i.kind == kind]
            n = len(files)
            g = sec.group(on_watch[kind],
                          ngettext("{count} file · {size}", "{count} files · {size}", n).format(
                              count=n, size=_human(sum(f.size for f in files))))
            if not files:
                continue
            exp = Adw.ExpanderRow(title=ngettext("Show {count} file", "Show {count} files",
                                                 n).format(count=n))
            g.add(exp)
            for f in files:
                row = Adw.ActionRow(title=GLib.markup_escape_text(f.path.split("/")[-1]),
                                    subtitle=GLib.markup_escape_text(f.path))
                row.add_suffix(self._trash(_("Remove"), lambda p=f.path: self.remove_music([p])))
                exp.add_row(row)
        if music.supports_playlists(self.session.device):
            g = sec.group(_("Playlists"))
            g.set_header_suffix(self._button(_("Add Playlist…"), self.choose_playlist))
            for p in playlists:
                row = Adw.ActionRow(title=GLib.markup_escape_text(p.split("/")[-1]))
                row.add_suffix(self._trash(_("Remove"), lambda p=p: self.remove_music([p])))
                g.add(row)

    def choose_music(self, kind: str, folders: bool) -> None:
        dialog = Gtk.FileDialog(title=_("Choose music to copy to the device"))

        def chosen(dlg, res):
            try:
                files = (dlg.select_multiple_folders_finish(res) if folders
                         else dlg.open_multiple_finish(res))
            except GLib.Error:
                return
            local = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            if not local or None in local:
                self.toast(_("Choose files stored on this computer"))
                return
            self.add_music([Path(p) for p in local], kind)

        if folders:
            dialog.select_multiple_folders(self, None, chosen)
        else:
            dialog.open_multiple(self, None, chosen)

    def add_music(self, paths_, kind: str) -> None:
        from openexpress import music

        s = self.session

        def plan():
            tracks = music.scan(paths_, s.device, forced_kind=None if kind == "track" else kind)
            return music.plan_copy(tracks, s.device, s.storage)

        def confirm(p):
            if not p.to_copy:
                self.toast(_("Nothing new to copy"))
                return
            n = len(p.to_copy)
            d = Adw.AlertDialog(
                heading=ngettext("Copy {count} file?", "Copy {count} files?", n).format(count=n),
                body=ngettext("{size} will be copied to the device ({count} file already there).",
                              "{size} will be copied to the device ({count} files already there).",
                              len(p.already)).format(size=_human(p.size), count=len(p.already)))
            d.add_response("cancel", _("Cancel"))
            # TRANSLATORS: button
            d.add_response("ok", _("Copy"))
            d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            d.connect("response", lambda _d, r: r == "ok" and self.run_task(
                _("Copying music…"),
                lambda: music.copy(p, s.storage, progress=self.progress,
                                   transfer_progress=self.fraction),
                lambda written: (self.toast(ngettext("Copied {count} file", "Copied {count} files",
                                                     len(written)).format(count=len(written))),
                                 self.load_section("music", force=True))))
            d.present(self)

        self.run_task(_("Scanning files…"), plan, confirm)

    def choose_playlist(self) -> None:
        dialog = Gtk.FileDialog(title=_("Choose a playlist to add to the device"))
        only = Gtk.FileFilter()
        only.set_name(_("Playlists (.m3u, .m3u8)"))
        for pattern in ("*.m3u", "*.m3u8", "*.M3U", "*.M3U8"):
            only.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(only)
        dialog.set_filters(filters)

        def chosen(dlg, res):
            try:
                path = dlg.open_finish(res).get_path()
            except GLib.Error:
                return
            if not path:
                self.toast(_("Choose files stored on this computer"))
                return
            self.add_playlist(Path(path))

        dialog.open(self, None, chosen)

    @staticmethod
    def _playlist_summary(plan, in_flatpak: bool = False) -> str:
        """Text of the confirmation: what is copied, what is already there, what is left out."""
        tracks = len(plan.device_paths)
        lines = [ngettext("{count} track", "{count} tracks", tracks).format(count=tracks)]
        n = len(plan.copy.to_copy)
        lines.append(ngettext("{count} file to copy ({size}), {already} already on the device",
                              "{count} files to copy ({size}), {already} already on the device",
                              n).format(count=n, size=_human(plan.copy.size),
                                        already=len(plan.copy.already)))
        if plan.replaces:
            lines.append(_("A playlist with this name is already on the device and "
                           "will be replaced."))
        if plan.skipped:
            lines.append("")
            lines.append(ngettext("{count} track cannot be added:",
                                  "{count} tracks cannot be added:",
                                  len(plan.skipped)).format(count=len(plan.skipped)))
            lines += [f"• {name} ({reason})" for name, reason in plan.skipped[:10]]
            if len(plan.skipped) > 10:
                more = len(plan.skipped) - 10
                lines.append(ngettext("… and {count} more", "… and {count} more",
                                      more).format(count=more))
            if in_flatpak:
                lines.append(_("openExpress installed as a Flatpak can read music only from "
                               "the Music and Downloads folders."))
        return "\n".join(lines)

    def add_playlist(self, path) -> None:
        from openexpress import music
        from openexpress.device.gio_storage import in_flatpak

        s = self.session

        def confirm(plan):
            summary = self._playlist_summary(plan, in_flatpak())
            if not plan.device_paths:
                self.error(_("The playlist has no tracks that can be added."), summary)
                return
            d = Adw.AlertDialog(
                heading=_("Add the playlist “{name}”?").format(name=plan.name),
                body=summary)
            d.add_response("cancel", _("Cancel"))
            # TRANSLATORS: button that adds the playlist to the device
            d.add_response("ok", _("Add"))
            d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            d.connect("response", lambda _d, r: r == "ok" and self.run_task(
                _("Copying music…"),
                lambda: music.add_playlist(plan, s.device, s.storage, progress=self.progress,
                                           transfer_progress=self.fraction),
                lambda _path: (self.toast(_("Playlist “{name}” added").format(name=plan.name)),
                               self.load_section("music", force=True))))
            d.present(self)

        self.run_task(_("Reading the playlist…"),
                      lambda: music.plan_playlist(path, s.device, s.storage), confirm)

    def remove_music(self, device_paths) -> None:
        from openexpress import music

        s = self.session
        self.run_task(_("Removing…"), lambda: music.remove(s.storage, device_paths),
                      lambda removed: (self.toast(self._removed_files(len(removed))),
                                       self.load_section("music", force=True)))


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
        self.quit()

    def do_activate(self):
        self.main_window().present()


def main(argv=None) -> int:
    return App().run(argv or [])


if __name__ == "__main__":
    raise SystemExit(main())
