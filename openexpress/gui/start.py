"""The start page of the window: the Garmin devices as tiles (D5). The root of the window's
navigation; the view of one device comes above it, with the back arrow to this page.

The page only shows what it is given (devicelist.Tile and how the device is connected) and
says which tile was clicked by its place in the list: it never holds a device, an address or
a unit ID."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from openexpress.i18n import _  # noqa: E402

APP_ID = "io.github.tomaszbojanowski.OpenExpress"
TILE_ICON_SIZE = 96
TILE_WIDTH = 200

# The frame of the tile of a connected device, in the accent colour (loaded once per display).
CSS = """
flowboxchild.oe-tile.connected {
  outline: 2px solid @accent_color;
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


class StartPage:
    """The page "Devices": a notice on top (the state on USB, while no device is open) and a
    tile for each device. `on_open(index)` is called with the place of the tile clicked."""

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
        self._entries: list = []  # what the tiles show now
        self.set_empty("", "", False)
        box.append(self.notice_group)

        # TRANSLATORS: heading above the tiles of the devices connected now
        self.connected_title = Gtk.Label(label=_("Connected"), xalign=0, visible=False)
        self.connected_title.add_css_class("heading")
        box.append(self.connected_title)
        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                activate_on_single_click=True, min_children_per_line=1,
                                max_children_per_line=4, column_spacing=12, row_spacing=12,
                                valign=Gtk.Align.START)
        if on_open is not None:
            # The place of the tile only: the handler holds no widget.
            self.grid.connect("child-activated",
                              lambda _grid, child: on_open(child.get_index()))
        box.append(self.grid)
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
        entries = list(entries)
        if entries != self._entries:  # the same tiles keep their widgets
            self._entries = entries
            self.grid.remove_all()
            for tile, how in entries:
                self.grid.append(tile_widget(tile, how))
        self.connected_title.set_visible(any(tile.connected for tile, _how in entries))
        self.stack.set_visible_child_name("content")

    def tiles(self) -> list:
        """The tiles shown, in order."""
        found, child = [], self.grid.get_first_child()
        while child is not None:
            found.append(child)
            child = child.get_next_sibling()
        return found


def tile_widget(tile, how: str) -> Gtk.FlowBoxChild:
    """One tile: an icon (the picture comes later), the name, how it is connected and its
    version; framed when connected."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    box.append(Gtk.Image(icon_name=APP_ID, pixel_size=TILE_ICON_SIZE))
    name = Gtk.Label(label=tile.description or _("Garmin device"), wrap=True,
                     justify=Gtk.Justification.CENTER)
    name.add_css_class("heading")
    box.append(name)
    if how:
        label = Gtk.Label(label=how, wrap=True, justify=Gtk.Justification.CENTER)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        box.append(label)
    child = Gtk.FlowBoxChild(child=box, width_request=TILE_WIDTH)
    child.add_css_class("card")
    child.add_css_class("oe-tile")
    if tile.connected:
        child.add_css_class("connected")
    return child
