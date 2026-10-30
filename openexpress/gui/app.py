"""openexpress graphical interface (GTK 4 + libadwaita)."""

from __future__ import annotations

import gc
import logging
import os
import sys
import weakref
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import netstatus, service  # noqa: E402
from openexpress.connect import NotAccountDevice  # noqa: E402
from openexpress.console import error as print_error  # noqa: E402
from openexpress.device.gio_storage import ABSENT  # noqa: E402
from openexpress.gui import pictures, start  # noqa: E402
from openexpress.gui.apps_page import AppsPage  # noqa: E402
from openexpress.gui.connected import Connected  # noqa: E402
from openexpress.gui.detection import DeviceDetection  # noqa: E402
from openexpress.gui.device_page import DevicePage, Section  # noqa: E402
from openexpress.gui.forget_device import ForgetDevice  # noqa: E402
from openexpress.gui.gui_settings import (  # noqa: E402
    MIN_WINDOW,
    load_gui_settings,
    save_window_geometry,
    window_geometry,
)
from openexpress.gui.maps_page import MapsPage  # noqa: E402
from openexpress.gui.music_page import MusicPage  # noqa: E402
from openexpress.gui.navigation import Cause  # noqa: E402
from openexpress.gui.netstatus_view import NetStatusPanel, _render_net_status_later  # noqa: E402
from openexpress.gui.preferences import KEYS, Preferences  # noqa: E402
from openexpress.gui.report_dialog import ReportDialog  # noqa: E402
from openexpress.gui.start import RememberedPage, StartPage  # noqa: E402
from openexpress.gui.start_window import StartView, StartWindow  # noqa: E402
from openexpress.gui.updates_page import UpdatesPage  # noqa: E402
from openexpress.gui.window_tasks import (  # noqa: E402
    TaskRunner,
    bottom_bar,
    collect_garbage,
    inform,
    log_unexpected,
    progress_reporter,
    stop_waiting,
)
from openexpress.i18n import _, pgettext  # noqa: E402
from openexpress.known_devices import KnownDevice  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

USB_POLL_S = 2
log = logging.getLogger(__name__)


class MainWindow(TaskRunner, DeviceDetection, StartWindow, DevicePage, MapsPage, MusicPage,
                 AppsPage, UpdatesPage, Preferences, NetStatusPanel, ForgetDevice, ReportDialog,
                 Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="openExpress")
        self._init_state()
        self.set_size_request(*MIN_WINDOW)
        width, height, maximized = window_geometry(self.gui_settings)
        self.set_default_size(width, height)
        if maximized:
            self.maximize()

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
        # A drive that the system does not mount (KDE) sends no mount event.
        self.monitor.connect("volume-added", self._on_volume_added)
        self.monitor.connect("volume-changed", self._on_volume_changed)
        self.monitor.connect("volume-removed", self._on_volume_removed)
        self._watch_last_sync()
        self._usb_poll = GLib.timeout_add_seconds(USB_POLL_S, self._poll_usb)
        self._start()

    def _init_state(self) -> None:
        """Every attribute of the window, with the value it has before anything is shown: its
        state as it starts, and None for each widget, made by the layout (_build_layout) or by
        the page that shows it. The one place where an attribute of the window is first set."""
        self.session: service.DeviceSession | None = None
        # What the pages show while no device is open; the Sync button says the same.
        self._usb_title = ""
        self.busy = False
        self._waiting = 0  # GLib timer that moves the bar during a wait for a server
        self._reporter = progress_reporter(self)  # how often progress is shown
        self.gui_settings = load_gui_settings()
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
        # "current", "available", "unsupported" (map updates not supported for it), "failed"
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
        self._task_lets_close = False  # the task under way lets the window close
        self._opening_tile = None  # the tile whose device is being opened (open_choice)
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
        # The devices of the Garmin account (their entries, keys in lower case): read at
        # start, at login and on Refresh, or taken from the ownership check of the device
        # opened; None: not logged in, or not read.
        self._account: list | None = None
        # Why the account's list is not known (account_note), None: known or still asked.
        self._account_why: str | None = None
        self._account_wanted = False  # wanted after the detection running now
        # Apps whose update was written over PTP and waits for the device to be unplugged.
        # Kept in memory only; forgotten when the device goes away.
        self._updated_apps: set = set()
        # A device that was just plugged in is on USB for a while before gvfs offers it.
        self._usb_state, self._usb_since = ABSENT, 0.0
        # Since when the USB state has been normal, and whether a replug was told
        # (detection._poll_usb); whether the open device was unplugged, the device to open
        # again, and the drives watched while a device is open or looked for again.
        self._usb_gone = None
        self._normal_since = 0.0
        self._replug_told = False
        self._open_unplugged = False
        self._pending_reopen = None
        self._drives_while_open = None
        self._drive_look = None
        # The start page's texts told against now, and their timer.
        self._start_texts: list = []
        self._relative_timer = None
        # The window is closing (_on_close_request).
        self._closed = False
        # A page of the side panel was chosen: in front of the side panel while the window
        # is folded (_show_content); unfolded, show-content stays False.
        self._content_chosen = False

        # Made by __init__ and the layout.
        self.sections: dict = {}
        self.split = self.sidebar_header = self.nav = None
        self.net_icon = self.net_label = self.net_button = None
        self.net_heading_dot = self.net_heading = self.net_retry = None
        self.net_rows: dict = {}
        self.content_header = self.refresh_button = self.sync_button = None
        self.sync_icon_button = self.banner = self.pages = None
        self.progress_bar = self.progress_label = self.busy_spinner = None
        self.unplug_label = self.action_bar = self.stop_wait_button = None
        self.content_page = self.start = self.device_view = None
        self.navigation = self.remembered = self.toasts = None
        self._net_listener = self.monitor = self._usb_poll = None
        self._last_sync_monitor = None

        # The device page, made as it is shown (show_overview): its rows and what they say.
        self.device_title = self.device_image = self.device_picture = None
        self.software_label = self.update_chip = self.update_row = None
        self.map_row = self.apps_row = self.music_row = None
        self.storage_row = self.storage_bar = self.backup_row = None
        self.primary_row = self.last_sync_row = None
        self._banner_kind = ""
        self._last_sync_data = None
        self.apps_count = self.music_count = None
        self.account_picture_offer = self.account_picture_row = None
        self.device_error_group = self.device_error_row = None
        # The pictures of devices drawn and asked for (pictures.py).
        self._device_textures = self._pictures_asked = self._pictures_waiting = None
        # The dialogs open: Preferences (with its rows), the order of the apps, the new
        # permissions of an app, Forget Device (kept by confirm(..., keep_as=)).
        self._preferences = self._account_row = self._storage = None
        self._order_dialog = self._permissions_dialog = self._forget_dialog = None

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
        self.nav.connect("row-activated", lambda *_a: self._show_content())
        self.split.connect("notify::collapsed", self._on_folded)
        self.split.connect("notify::show-content", self._on_show_content)
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
        # Only while TrueUp waits for Garmin Connect (a wait of known length): nothing is
        # written to the device meanwhile.
        # TRANSLATORS: button on the bar at the bottom: stop waiting for TrueUp
        self.stop_wait_button = Gtk.Button(label=_("Stop Waiting"), valign=Gtk.Align.CENTER,
                                           visible=False)
        self.stop_wait_button.connect("clicked", lambda _b: stop_waiting(self))
        # In the column of the pages above it, at every width (window_tasks.bottom_bar).
        self.action_bar = bottom_bar(self.busy_spinner, box, self.stop_wait_button)
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

        # The start page is the root; the device view comes above it.
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
        self._show_content()

    def _front_device_page(self) -> None:
        """The device page ("Device") in front of the device view, the page of the side panel
        seen before forgotten: a device opened, opened again or chosen on the start page
        always opens there (folded, on the side panel, as when it first opens)."""
        self._content_chosen = False
        self.split.set_show_content(False)
        for i, sec in enumerate(self.sections.values()):
            if sec.key == "watch":
                self.nav.select_row(self.nav.get_row_at_index(i))

    def _show_content(self) -> None:
        """The page chosen in the side panel, in front of the panel while the window is
        folded. Unfolded, both are in view and show-content stays False: set, the
        NavigationSplitView takes the back arrow (navigation.pop) for itself, and the first
        click only clears it, with nothing to see."""
        self._content_chosen = True
        if self.split.get_collapsed():
            self.split.set_show_content(True)

    def _on_folded(self, split, _pspec) -> None:
        """Folded, the page chosen comes in front of the side panel again; unfolded,
        show-content is cleared (_show_content)."""
        if split.get_collapsed():
            split.set_show_content(self._content_chosen)
        else:
            split.set_show_content(False)

    def _on_show_content(self, split, _pspec) -> None:
        """Folded, back from the page to the side panel (or a page chosen) is kept for the
        next fold."""
        if split.get_collapsed():
            self._content_chosen = split.get_show_content()

    def _on_close_request(self, _win) -> bool:
        if not self.busy or self._task_lets_close:
            self._remember_geometry()
            netstatus.STATUS.remove_listener(self._net_listener)
            self._closed = True
            self._update_relative_timer()  # no timer left once the window is closed
            return False
        if self._close_dialog is None:
            self._close_dialog = inform(
                self, "close-busy", _("An operation is in progress"),
                _("Wait until it finishes before closing openExpress, so that no files "
                  "on the device are left half-written."),
                then=lambda: setattr(self, "_close_dialog", None))
        return True


    def _remember_geometry(self) -> None:
        # The default size follows the user's resizing and keeps the last unmaximized
        # size while the window is maximized, so both states restore correctly.
        width, height = self.get_default_size()
        try:
            save_window_geometry(width, height, self.is_maximized())
        except OSError as e:
            log.warning("Could not save the window size: %s", e)


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
        for action, accels in KEYS.items():
            self.set_accels_for_action(action, accels)

    def main_window(self, create: bool = True) -> MainWindow | None:
        wins = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if wins:
            return wins[0]
        return MainWindow(self) if create else None

    def request_quit(self) -> None:
        win = self.main_window(create=False)
        if win is not None and win.busy:
            win.close()  # close-request refuses and says why, unless the task lets it close
            return
        if win is not None:
            # quit() skips close-request: the size is saved as when the window is closed.
            win._remember_geometry()
        self.quit()

    def do_activate(self):
        self.main_window().present()


def install_excepthook() -> None:
    """The window's exceptions nobody caught (in a handler of a signal, a callback of GLib:
    PyGObject hands those to sys.excepthook too) go to the log, then on to the hook that was
    there (stderr). Ctrl+C is not an error."""
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
