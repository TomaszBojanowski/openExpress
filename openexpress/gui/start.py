"""The start page of the window: the Garmin devices as tiles (D5). The root of the window's
navigation; the view of one device comes above it, with the back arrow to this page.

The page only shows what it is given (devicelist.Tile and how the device is connected) and
says which tile was clicked by its place in the list: it never holds a device, an address or
a unit ID."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from openexpress import devicelist  # noqa: E402
from openexpress.devicelist import CONNECTED, ON_ACCOUNT, REMEMBERED  # noqa: E402
from openexpress.i18n import _, pgettext  # noqa: E402

APP_ID = "io.github.tomaszbojanowski.OpenExpress"
TILE_ICON_SIZE = 96
TILE_WIDTH = 200
# A longer text wraps instead of widening its tile (so that tiles share a line).
TEXT_CHARS = 40

# The tiles of the start page only (loaded once per display): a quiet 1px edge around the
# tile of a connected device; the accent only under the pointer or with the keyboard focus.
# The edge is an outline drawn inside the tile: neither its size nor the layout changes.
CSS = """
flowboxchild.oe-tile.connected {
  outline: 1px solid alpha(currentColor, 0.15);
  outline-offset: -1px;
}
flowboxchild.oe-tile:hover,
flowboxchild.oe-tile:focus:focus-visible {
  outline: 2px solid alpha(@accent_color, 0.7);
  outline-offset: -2px;
}
"""


def load_css(display) -> None:
    provider = Gtk.CssProvider()
    if hasattr(provider, "load_from_string"):  # GTK 4.12
        provider.load_from_string(CSS)
    else:
        provider.load_from_data(CSS, -1)
    Gtk.StyleContext.add_provider_for_display(display, provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _tile_grid() -> Gtk.FlowBox:
    return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                       activate_on_single_click=True, min_children_per_line=1,
                       max_children_per_line=4, column_spacing=12, row_spacing=12,
                       valign=Gtk.Align.START)


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, visible=False)
    label.add_css_class("heading")
    return label


def display_key(tile, how: str) -> tuple:
    """What a tile shows: the same tile is not built again (e.g. when the account's list
    only adds its flags to a connected device)."""
    return tile.description, how, tile.connected


class StartPage:
    """The page "Devices": a notice on top (the state on USB, while no device is open), the
    tiles of the devices connected, of those remembered and not connected, and of those only
    on the Garmin account. `on_open(index)` is called with the place of the tile clicked in
    the whole list."""

    def __init__(self, on_open=None, header_start=(), header_end=()):
        self.header = Adw.HeaderBar()
        for widget in header_start:
            self.header.pack_start(widget)
        for widget in header_end:
            self.header.pack_end(widget)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        # Until the first detection finishes it is not known which devices are connected.
        loading = Adw.StatusPage(title=_("Detecting the device…"))
        if hasattr(Adw, "SpinnerPaintable"):
            loading.set_paintable(Adw.SpinnerPaintable.new(loading))
        self.stack.add_named(loading, "loading")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24,
                      margin_bottom=24, margin_start=12, margin_end=12)
        self.notice_group = Adw.PreferencesGroup(visible=False)
        self.notice = Adw.ActionRow()
        self.notice_icon = Gtk.Image(icon_name=APP_ID, pixel_size=32)
        self.notice_spinner = (Adw.Spinner() if hasattr(Adw, "Spinner")
                               else Gtk.Spinner(spinning=True))
        self.notice.add_prefix(self.notice_icon)
        self.notice.add_prefix(self.notice_spinner)
        self.notice_group.add(self.notice)
        self._notice = None
        self.set_empty("", "", False)
        box.append(self.notice_group)

        # TRANSLATORS: heading above the tiles of the devices connected now
        self.connected_title = _heading(_("Connected"))
        self.grid = _tile_grid()
        # TRANSLATORS: heading above the tiles of the devices seen before, not connected now
        self.remembered_title = _heading(_("Remembered"))
        self.remembered_grid = _tile_grid()
        # TRANSLATORS: heading above the tiles of the devices of the Garmin account that were
        # never connected to this computer
        self.account_title = _heading(_("On the account"))
        self.account_grid = _tile_grid()
        # Each section: its title, its tiles, how many tiles come before it (a list, so that
        # the handlers hold no widget) and what its tiles show now.
        self._sections = {
            CONNECTED: (self.connected_title, self.grid, [0], []),
            REMEMBERED: (self.remembered_title, self.remembered_grid, [0], []),
            ON_ACCOUNT: (self.account_title, self.account_grid, [0], []),
        }
        for title, grid, before, _shown in self._sections.values():
            grid.set_visible(False)
            if on_open is not None:
                # The place of the tile only: the handlers hold no widget.
                grid.connect("child-activated", lambda _grid, child, before=before:
                             on_open(before[0] + child.get_index()))
            box.append(title)
            box.append(grid)
        clamp = Adw.Clamp(maximum_size=900, child=box)
        self.stack.add_named(Gtk.ScrolledWindow(child=clamp,
                                                hscrollbar_policy=Gtk.PolicyType.NEVER),
                             "content")
        self.stack.set_visible_child_name("loading")

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(self.header)
        # TRANSLATORS: title of the start page, which lists the Garmin devices
        self.page = Adw.NavigationPage(title=_("Devices"), tag="start", child=view)

    def set_empty(self, title: str, description: str, busy: bool) -> None:
        """The words of the notice on top (those of the device pages while no device is
        open: no device, one that is starting, one that is not ready)."""
        if self._notice == (title, description, busy):
            return
        self._notice = (title, description, busy)
        self.notice.set_title(title)
        self.notice.set_subtitle(description)
        self.notice_spinner.set_visible(busy)
        self.notice_icon.set_visible(not busy)

    def show(self, entries, notice: bool) -> None:
        """The tiles (devicelist.Tile, how the device is connected), in this order; `notice`:
        whether the notice on top is shown."""
        self.notice_group.set_visible(notice)
        parts: dict[str, list] = {name: [] for name in self._sections}
        for tile, how in entries:  # in the order of the sections (devicelist.entries)
            parts[devicelist.section(tile)].append((tile, how))
        count = 0
        for name, (title, grid, before, shown) in self._sections.items():
            part = parts[name]
            before[0] = count
            count += len(part)
            keys = [display_key(tile, how) for tile, how in part]
            if keys != shown:  # a section whose tiles show the same keeps their widgets
                shown[:] = keys
                grid.remove_all()
                for tile, how in part:
                    grid.append(tile_widget(tile, how))
            title.set_visible(bool(part))
            grid.set_visible(bool(part))
        self.stack.set_visible_child_name("content")

    def tiles(self) -> list:
        """The tiles shown, in order: the connected ones, then the remembered ones."""
        found = []
        for _title, grid, _before, _shown in self._sections.values():
            child = grid.get_first_child()
            while child is not None:
                found.append(child)
                child = child.get_next_sibling()
        return found


class RememberedPage:
    """The read-only view of a device remembered and not connected: what was known of it
    when it was last seen, and its last sync. It offers nothing to do: Sync and the rows of
    updates and maps cannot be used, the banner says why. It is given words only (never a
    device, an address or a unit ID)."""

    def __init__(self):
        header = Adw.HeaderBar()
        # TRANSLATORS: button that starts syncing
        self.sync_button = Gtk.Button(label=_("Sync"), sensitive=False)
        header.pack_end(self.sync_button)
        self.banner = Adw.Banner(
            title=_("Device not connected — connect it to sync or update"), revealed=True)
        self.sync_button.set_tooltip_text(self.banner.get_title())

        content = Adw.PreferencesPage()
        hero = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      halign=Gtk.Align.CENTER, margin_bottom=12)
        box.append(Gtk.Image(icon_name=APP_ID, pixel_size=128))
        self.name_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.name_label.add_css_class("title-1")
        self.version_label = Gtk.Label()
        self.version_label.add_css_class("dim-label")
        self.seen_label = Gtk.Label()
        self.seen_label.add_css_class("dim-label")
        for widget in (self.name_label, self.version_label, self.seen_label):
            box.append(widget)
        hero.add(box)
        content.add(hero)

        group = Adw.PreferencesGroup(title=_("Sync with Garmin Connect"))
        self.last_sync_row = Adw.ActionRow(title=_("Last sync"))
        group.add(self.last_sync_row)
        content.add(group)

        group = Adw.PreferencesGroup(title=pgettext("tab", "Updates"))
        check = _("Connect the device to check")
        self.update_row = Adw.ActionRow(title=_("Device software"), subtitle=check,
                                        sensitive=False)
        self.map_row = Adw.ActionRow(title=pgettext("tab", "Maps"), subtitle=check,
                                     sensitive=False)
        group.add(self.update_row)
        group.add(self.map_row)
        content.add(group)

        self.tools_group = Adw.PreferencesGroup(title=_("Tools"))
        self.part_row = Adw.ActionRow(title=_("Part number"), subtitle_selectable=True)
        self.tools_group.add(self.part_row)
        content.add(self.tools_group)

        view = Adw.ToolbarView(content=content)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.page = Adw.NavigationPage(title=_("Devices"), tag="remembered", child=view)

    def show(self, name: str, version: str, part_number: str, last_seen: str,
             last_sync: str) -> None:
        """What is known; a version or a part number not known is left out, not shown
        empty."""
        name = name or _("Garmin device")
        self.page.set_title(name)
        self.name_label.set_label(name)
        self.version_label.set_label(version)
        self.version_label.set_visible(bool(version))
        self.seen_label.set_label(last_seen)
        self.last_sync_row.set_subtitle(GLib.markup_escape_text(last_sync))
        self.part_row.set_subtitle(GLib.markup_escape_text(part_number))
        self.tools_group.set_visible(bool(part_number))


def tile_widget(tile, how: str) -> Gtk.FlowBoxChild:
    """One tile: an icon (the picture comes later), the name, how it is connected and its
    version; framed when connected."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    box.append(Gtk.Image(icon_name=APP_ID, pixel_size=TILE_ICON_SIZE))
    name = Gtk.Label(label=tile.description or _("Garmin device"), wrap=True,
                     justify=Gtk.Justification.CENTER, max_width_chars=TEXT_CHARS)
    name.add_css_class("heading")
    box.append(name)
    if how:
        label = Gtk.Label(label=how, wrap=True, justify=Gtk.Justification.CENTER,
                          max_width_chars=TEXT_CHARS)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        box.append(label)
    child = Gtk.FlowBoxChild(child=box, width_request=TILE_WIDTH)
    child.add_css_class("card")
    child.add_css_class("oe-tile")
    if tile.connected:
        child.add_css_class("connected")
    return child
