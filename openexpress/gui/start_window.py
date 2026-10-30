"""The start page of the window and the pages of the devices not connected: the tiles of the
devices connected, remembered and on the account (their chips, pictures and relative dates,
the activities to sync counted in the background), the account's list of devices, the view
of the start page, the page of a remembered device or one only on the account, and where the
user goes after a detection. StartWindow is a mixin of MainWindow (gui/app.py), without
__init__ of its own; StartView is the choice of grid or list of the start page.

Has: the tiles drawn and their words, the account's list, the remembered device shown.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import devicelist, known_devices, last_sync, service, sync, tile_facts  # noqa: E402
from openexpress.device.gio_storage import NO_DEVICE_FILE, NOT_MOUNTED, DeviceSummary  # noqa: E402
from openexpress.gui import start  # noqa: E402
from openexpress.gui.connected import session_summary, unit_ids, uris  # noqa: E402
from openexpress.gui.detection import _take_asked, decided, seen_open  # noqa: E402
from openexpress.gui.device_page import (  # noqa: E402
    last_sync_subtitle,
    refresh_last_sync_subtitle,
    remember_sync_count,
)
from openexpress.gui.gui_settings import load_gui_settings, update_gui_settings  # noqa: E402
from openexpress.gui.navigation import Cause, Seen, ShowStart, Stay  # noqa: E402
from openexpress.gui.pictures import (  # noqa: E402
    cached_texture,
    device_picture_path,
    download_picture,
    load_texture,
    with_device_picture,
)
from openexpress.gui.preferences import account_client, resolve_account  # noqa: E402
from openexpress.gui.start import kind_icon  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    last_seen_text,
    last_seen_tooltip,
    never_synced_tooltip,
    primary_wearable_text,
    remembered_update_text,
    software_text,
)
from openexpress.i18n import _, caption, ngettext, pgettext  # noqa: E402
from openexpress.known_devices import KnownDevice  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

log = logging.getLogger(__name__)


# How often the texts told against now ("2 min ago") are looked at again, while shown.
RELATIVE_REFRESH_S = 60


# The icons of the views of the start page: grid and list GNOME's, automatic the program's
# own (tiles above a list).
VIEW_ICONS = {devicelist.AUTO: f"{APP_ID}-view-auto-symbolic",
              devicelist.GRID: "view-grid-symbolic", devicelist.LIST: "view-list-symbolic"}


class StartView:
    """The row of buttons on top of the start page's menu: how its sections show their
    devices: automatic (devicelist.AUTO: a list from GRID_MAX + 1 devices in a section, each
    section on its own), a grid or a list for all of them. Kept in gui.json (KEY); a change
    draws the start page again from what is known (_render_start), without asking the
    devices."""

    KEY = "start_view"
    ACTION = "start-view"  # win.start-view, whose state is the view
    CUSTOM = "start-view"  # the menu's custom item: the row of buttons

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
        """The menu of the start page: on top the row of buttons (the custom item CUSTOM,
        put in by menu_button), then the sections of the main menu."""
        item = Gio.MenuItem()
        item.set_attribute_value("custom", GLib.Variant.new_string(cls.CUSTOM))
        row = Gio.Menu()
        row.append_item(item)
        menu = Gio.Menu()
        menu.append_section(None, row)
        for i in range(main.get_n_items()):
            menu.append_item(Gio.MenuItem.new_from_model(main, i))
        return menu

    @classmethod
    def buttons(cls) -> Gtk.Box:
        """The row: a round toggle button for each view, with the window's action and the view
        as its target, so the one chosen is pressed (for screen readers too); each named, with
        a tooltip; the row a group named "View"."""
        row = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6,
                      accessible_role=Gtk.AccessibleRole.GROUP)
        # TRANSLATORS: for screen readers: the row of buttons on top of the start page's
        # menu, how the devices are shown
        row.update_property([Gtk.AccessibleProperty.LABEL], [pgettext("start view", "View")])
        # TRANSLATORS: buttons on top of the menu of the start page: how the devices are
        # shown (automatic: tiles, a list for more than six in a section)
        names = {devicelist.AUTO: pgettext("start view", "Automatic"),
                 devicelist.GRID: pgettext("start view", "Grid"),
                 devicelist.LIST: pgettext("start view", "List")}
        # TRANSLATORS: the tooltip of the button Automatic on top of the start page's menu
        # (its icon shows tiles above a list); {count}: from how many devices on a section
        # is a list
        automatic = ngettext("Automatic: tiles, or a list when a section has more than "
                             "{count} device",
                             "Automatic: tiles, or a list when a section has more than "
                             "{count} devices", devicelist.GRID_MAX).format(
                                 count=devicelist.GRID_MAX)
        for view in devicelist.VIEWS:
            tooltip = automatic if view == devicelist.AUTO else names[view]
            button = Gtk.ToggleButton(icon_name=VIEW_ICONS[view], tooltip_text=tooltip,
                                      action_name=f"win.{cls.ACTION}",
                                      action_target=GLib.Variant.new_string(view),
                                      css_classes=["flat", "circular"], width_request=44,
                                      height_request=44)
            told = {Gtk.AccessibleProperty.LABEL: names[view]}
            if view == devicelist.AUTO:
                told[Gtk.AccessibleProperty.DESCRIPTION] = automatic
            button.update_property(list(told), list(told.values()))
            row.append(button)
        return row

    @classmethod
    def menu_button(cls, main: Gio.Menu) -> Gtk.MenuButton:
        """The start page's menu button: menu() with the row of buttons in it; opened, the
        focus on the button of the view chosen (as in a group of radio buttons)."""
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=cls.menu(main),
                                tooltip_text=_("Main Menu"), primary=True)
        popover = button.get_popover()
        popover.add_child(cls.buttons(), cls.CUSTOM)
        popover.connect("show", cls._focus_chosen)
        return button

    @staticmethod
    def _focus_chosen(popover) -> None:
        todo = [popover]
        while todo:
            widget = todo.pop()
            if isinstance(widget, Gtk.ToggleButton) and widget.get_active():
                widget.grab_focus()
                return
            child = widget.get_first_child()
            while child is not None:
                todo.append(child)
                child = child.get_next_sibling()

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
    """The one sentence of the start page while the account's list is not known: not logged
    in ("logged-out": its tiles then say nothing of the account), or it could not be read
    ("failed": they say what was kept of it, tile_facts). None: known, or still asked."""
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
    win.start.set_account_note(account_note(win._account_why)
                               if entries else None)


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


def without_facts(tile):
    """A tile without the facts of its chips (tile_facts.TILE_FIELDS): to tell whether only
    they changed."""
    return dataclasses.replace(tile, **tile_facts.TileFacts().tile_fields())


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
    """What the account's list in memory says of a device not connected, read only: its
    primary wearable, for a device that can be one. Nothing is asked."""
    wearable = devicelist.primary_wearable(win._account, unit_id)
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


class StartWindow:
    # --- the start page and the remembered devices ----------------------------------

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

    def _on_start_page(self) -> bool:
        return self.navigation.get_visible_page() is self.start.page

    def _fetch_account(self) -> None:
        """Read the devices of the Garmin account in the background, if one is logged in;
        when they change, the start page (if shown) is drawn once more: the section "On the
        account" comes, and the tiles whose words and chips stay the same keep their widgets.
        Not logged in, or an error: no such section, one line in the log."""
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
            if why != self._account_why:
                # The same tiles: only the sentence under them changes.
                self._account_why = why
                show_account_note(self, self._start_entries)
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
        """The view of the device open now, above the start page, or the page shown
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
        self._front_device_page()
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
        """The tiles of these devices, then of those remembered and not connected
        (devices.json, the only thing read here) and of those only on the account; what each
        one opens is kept here, not in the tile: OPEN, the FoundDevice to open, or the
        KnownDevice or the account's entry to show read-only."""
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
        """Counts in the background the activities not uploaded yet of each connected device
        of the account whose tile was just drawn, once while it stays listed or open (the
        open one for its session); its tile shows no chip until the count comes
        (_activities_counted). Never a drive, a device off the account or one whose check
        against the account failed."""
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
        wanted = (self.get_mapped() and not self._closed
                  and visible in (self.start.page, self.device_view, self.remembered.page))
        timer = self._relative_timer
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
                   for source, text in self._start_texts):
                self._render_start()
        elif visible is self.device_view:
            refresh_last_sync_subtitle(self)
            if self.update_checked_at is not None:
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
        elif self._opening_tile != index:
            self.open_choice(target, tile=index)
        # else its device is being opened: a second click says nothing

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
        """Shows, from the start page only, the read-only view of a device remembered, not
        connected: what this computer knew when it was last seen, and its last sync; its
        image as its `tile` has it (the account's picture and kind, for one on it). Nothing
        is asked but that picture; the open device and the detection are not touched."""
        if not self._on_start_page():
            return
        self._fill_remembered(known, tile)
        self.navigation.push(self.remembered.page)

    def _fill_remembered(self, known, tile=None) -> None:
        """The read-only view filled for a device remembered (_show_remembered, and drawn
        again by _redraw_remembered)."""
        self._remembered_entry = known
        self.remembered.show(
            name=known.shown_name,
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
        known = self._remembered_entry
        if known is not None:
            self.remembered.show_texts(**remembered_texts(self, known))
        else:
            row = self.remembered.last_sync_row
            text = GLib.markup_escape_text(self._last_sync_text(self._remembered_unit))
            if row.get_subtitle() != text:
                row.set_subtitle(text)

    def _refresh_remembered(self) -> None:
        """Refresh (F5, Ctrl+R) on the read-only view of a device not connected: only that
        view. The account's list is read again and the view drawn from what it says now
        (_set_account, _redraw_remembered); the device open, if any, is not opened again and
        nothing is detected."""
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
