"""The start page of the window: the Garmin devices as tiles (D5). The root of the window's
navigation; the view of one device comes above it, with the back arrow to this page.

The page only shows what it is given (devicelist.Tile and how the device is connected) and
says which tile was clicked by the place it had in the list it was given (each tile keeps
it): it never holds a device, an address or a unit ID."""

from __future__ import annotations

from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from openexpress import devicelist, tile_facts  # noqa: E402
from openexpress.devicelist import CONNECTED, LIST, ON_ACCOUNT, REMEMBERED  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    last_seen_tooltip,
    never_synced,
    never_synced_tooltip,
    primary_wearable_tooltip,
)
from openexpress.i18n import _, full_time, ngettext, pgettext  # noqa: E402
from openexpress.known_devices import (  # noqa: E402
    BIKE_ACCESSORY,
    CYCLING_COMPUTER,
    HANDHELD,
    WATCH,
)

APP_ID = "io.github.tomaszbojanowski.OpenExpress"
TILE_ICON_SIZE = 96
ROW_ICON_SIZE = 48  # in a row of a list (a section of many devices)
# A row of a list as an Adw.ActionRow of the same list (measured with libadwaita 1.9): at
# least 50 px tall, 12 px at its sides and after the picture, 6 px above and below its texts
# and before its arrow, 3 px between the texts.
ROW_MIN_HEIGHT, ROW_SIDE, ROW_TEXTS_AROUND, ROW_TEXTS_SPACING = 50, 12, 6, 3
# The icon of a tile without a picture, by the kind of the device (the files in data/,
# symbolic: in the colours of the light or dark theme); without a kind a neutral one, so
# that a device of an unknown kind never looks like a watch.
KIND_ICONS = {
    WATCH: f"{APP_ID}-watch-symbolic",
    CYCLING_COMPUTER: f"{APP_ID}-cycling-computer-symbolic",
    BIKE_ACCESSORY: f"{APP_ID}-bike-accessory-symbolic",
    HANDHELD: f"{APP_ID}-handheld-symbolic",
}
DEVICE_ICON = f"{APP_ID}-device-symbolic"
# One width for every tile, in every section (never fitted to the longest text of a
# section), from the width of the page (T9 step 0): as many tiles on a line as fit at least
# MIN_TILE_WIDTH each, TILE_SPACING between them, sharing the line out; three in a window
# 1000 px wide (where Adw.Clamp gives the page about 760 px), one filling the line in a
# narrow window. A longer name is cut short; the line under it takes at most two lines.
MIN_TILE_WIDTH = 220
MAX_TILE_WIDTH = 300  # in a wide window: the tiles do not grow past it, the block is centred
TILE_SPACING = 12
SUBTITLE_LINES = 2

# The program's own style (loaded once per display). The tiles of the start page: a quiet
# 1px edge around the tile of a connected device; the accent only under the pointer or with
# the keyboard focus. The edge is an outline drawn inside the tile: neither its size nor the
# layout changes. A row that wraps with a libadwaita older than 1.7 (app.wrapping_row): no
# room around each of its widgets, as in a box. Every chip of the window (the tiles, the rows
# of the list view, the update on the device page) has the class oe-chip and its one shape:
# font, padding, rounding; its style (accent, warning, dimmed) gives only its colours.
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
flowbox.oe-wrap > flowboxchild {
  padding: 0;
}
label.oe-chip {
  padding: 1px 10px;
  border-radius: 999px;
  font-size: 0.82em;
  font-weight: 600;
}
label.oe-chip.accent {
  color: @accent_color;
  background-color: alpha(@accent_bg_color, 0.16);
}
label.oe-chip.warning {
  color: @warning_color;
  background-color: alpha(@warning_bg_color, 0.22);
}
label.oe-chip.dimmed {
  /* libadwaita 1.7 dims every widget of the class "dimmed" (opacity 0.55): a grey chip has
     its colours from here only, else it is fainter than the line under it (26.10.2026). */
  opacity: 1;
  color: alpha(currentColor, 0.8);
  background-color: alpha(currentColor, 0.12);
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


def columns(width: int, most: int | None = None) -> tuple[int, int]:
    """How many tiles share a line of this many pixels, and how wide each one is: as many as
    fit at least MIN_TILE_WIDTH wide, TILE_SPACING between them, never fewer than one, nor
    more than the most tiles a section has (`most`); each at most MAX_TILE_WIDTH wide but
    when only one fits (a narrow window: one column on the whole line)."""
    fit = max(1, (width + TILE_SPACING) // (MIN_TILE_WIDTH + TILE_SPACING))
    count = max(1, min(fit, most)) if most else fit
    tile = max(1, (width - TILE_SPACING * (count - 1)) // count)
    return count, tile if fit == 1 else min(tile, MAX_TILE_WIDTH)


def _tile_grid() -> Gtk.FlowBox:
    # Filling the line, from the left where the heading is: the grid shares the line out
    # into as many equal places as tiles share it (_TileColumns), also when it has fewer
    # tiles than that; a tile asks only for what its content needs, so that the window can
    # always get narrower.
    return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                       activate_on_single_click=True, min_children_per_line=1,
                       max_children_per_line=4, column_spacing=TILE_SPACING,
                       row_spacing=TILE_SPACING, valign=Gtk.Align.START,
                       halign=Gtk.Align.FILL)


class _ColumnsLayout(Gtk.LayoutManager):
    """The layout of _TileColumns: its one child as wide as the block of tiles and in the
    middle (never a minimum: the window can always get narrower), and then the width it was
    given told to the widget (which GTK hands over: nothing is kept here)."""

    __gtype_name__ = "OpenExpressColumnsLayout"

    def do_get_request_mode(self, widget):
        child = widget.get_first_child()
        return child.get_request_mode() if child is not None else Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, widget, orientation, for_size):
        child = widget.get_first_child()
        if child is None or not child.should_layout():
            return 0, 0, -1, -1
        if orientation == Gtk.Orientation.VERTICAL and for_size >= 0:
            for_size = widget.block(for_size)  # its height at the width it will have
        return child.measure(orientation, for_size)

    def do_allocate(self, widget, width, height, baseline):
        widget.allocated(width)
        child = widget.get_first_child()
        if child is not None and child.should_layout():
            inner = widget.block(width)
            place = Gsk.Transform.new().translate(Graphene.Point().init((width - inner) // 2, 0))
            child.allocate(inner, height, baseline, place)


class _TileColumns(Adw.Bin):
    """The headings and the tiles of the start page: on a new width (or a new count of tiles),
    how many tiles share a line and how wide they are (columns()), the same in every
    section; the block of them as wide as that, in the middle of the page. Each grid gets how
    many share a line and shares its line out itself; the tiles get no width (one that
    became their minimum kept the window from getting narrower). Set when the window is
    idle, never during its layout, and only when the columns or the width of a tile change.
    (A scrollbar that comes with the new layout makes the page narrower, so that fewer tiles
    share a line and the page gets longer: it stays, and nothing goes back.)"""

    __gtype_name__ = "OpenExpressTileColumns"

    def __init__(self, child: Gtk.Widget, grids: list):
        super().__init__(child=child)
        self.set_layout_manager(_ColumnsLayout())
        self._grids = grids
        self._margins = child.get_margin_start() + child.get_margin_end()
        self.layout = (0, MIN_TILE_WIDTH)  # (tiles on a line, their width): none set yet
        self.updates = 0  # how many times the tiles were laid out (for the tests)
        self._wanted = None
        self._most = 0  # the most tiles a section has (0: none)
        self._width = None  # the width the page was given last

    def set_most(self, most: int) -> None:
        """The most tiles a section has now: no more columns than that."""
        if most != self._most:
            self._most = most
            if self._width is not None:
                self.allocated(self._width)

    def allocated(self, width: int) -> None:
        """The page got this width (from its layout): the tiles follow, once it is idle."""
        self._width = width
        wanted = columns(width - self._margins, self._most or None)
        if wanted != self.layout and wanted != self._wanted:
            if self._wanted is None:
                GLib.idle_add(self._apply)
            self._wanted = wanted

    def block(self, width: int) -> int:
        """How wide the block of headings and tiles is in a page this wide: as wide as the
        tiles on a line and the room between them, never wider than the page."""
        count, tile = self.layout
        if not count or not self._most:
            return width
        return min(width, count * tile + TILE_SPACING * (count - 1) + self._margins)

    def _apply(self) -> bool:
        wanted, self._wanted = self._wanted, None
        if self.get_root() is None:  # the page left its window meanwhile: nothing to lay out
            return False
        if wanted is not None and wanted != self.layout:
            self.layout = wanted
            self.updates += 1
            for grid in self._grids:
                grid.set_max_children_per_line(wanted[0])
            self.queue_resize()  # the block's new width, in the middle
        return False


def _device_list() -> Gtk.ListBox:
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
    rows.add_css_class("boxed-list")
    return rows


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, visible=False)
    label.add_css_class("heading")
    return label


class _Section:
    """One section of the start page: its title, its tiles (a grid) or its rows (a list, for
    many devices) and what its items show now."""

    def __init__(self, title: str, on_open):
        self.title = _heading(title)
        self.grid, self.list = _tile_grid(), _device_list()
        self.grid.set_visible(False)
        self.list.set_visible(False)
        self.shown = None
        if on_open is not None:
            # The key the item was given (its place in the list shown), read from the item
            # the signal hands over: the handlers hold no widget.
            self.grid.connect("child-activated", lambda _grid, child: on_open(child.key))
            self.list.connect("row-activated", lambda _rows, row: on_open(row.key))

    def items(self) -> list:
        """Its tiles or its rows, in order (one of the two is empty)."""
        found = []
        for container in (self.grid, self.list):
            child = container.get_first_child()
            while child is not None:
                found.append(child)
                child = child.get_next_sibling()
        return found


def _one_gone(before, after) -> int | None:
    """The place of the one item that `after` (a section's mode and display keys) no longer
    has of `before`, the rest the same, in the same order and laid out the same way; None
    otherwise (the section is built again)."""
    if before is None or before[0] != after[0] or len(before[1]) != len(after[1]) + 1:
        return None
    old, new = before[1], after[1]
    place = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), len(new))
    return place if old[:place] + old[place + 1:] == new else None


def display_key(tile, how: str) -> tuple:
    """What a tile shows: the same tile is not built again (e.g. when the account's list
    only adds its flags to a connected device), but it is when its chips change."""
    return tile.description, how, tile.connected, tuple(chips(tile))


# The chips of a tile (PLAN T9, 24.10.2026): only what the tile would not say otherwise, at
# most MAX_CHIPS, in this order: an update copied already that the connected device installs
# once unplugged (or ejected, a drive), else an update to install (found by the window's
# check, at most tile_facts.UPDATE_KEPT_S ago), activities to sync or a device never
# synced here (connected, of the account), a USB drive, the account's primary device, a
# device off the account (only when the account's list is known for it; never a drive, which
# needs no account), and last a device synced here before with nothing to upload now. The
# accent only for what there is to do; colours of the theme only (the style is a class of
# CSS), no icons.
MAX_CHIPS = 2


def update_available() -> str:
    """The words of the chip of an update to install: on the tile and on the device page."""
    # TRANSLATORS: a chip on the tile of a device on the start page (its tooltip says when
    # the check found it), and under the software version on the device page
    return _("Update available")


def update_ready() -> str:
    """The words of the chip of an update copied already, under the software version on the
    device page (the row "Device software" says what to do)."""
    # TRANSLATORS: a chip under the software version on the device page: an update is copied
    # to the device, which installs it once disconnected
    return _("Update ready")


def chips(tile, now: float | None = None) -> list[tuple[str, str, str | None]]:
    """(words, style: "warning", "dimmed" or "accent", tooltip or None) of each chip: from
    the facts of the tile (tile_facts.TILE_FIELDS, as tile_facts.read gave them) and what
    the device itself shows (tile_facts.DEVICE_FIELDS), nothing else
    (tests/test_tile_facts.py)."""
    said = []
    found = tile_facts.update_still_recent(tile.update_found, now)
    if tile.update_ready is not None:
        # Wins over an update still to install, as on the device page (25.10.2026).
        if tile.kind == "ums":
            # TRANSLATORS: a chip on the tile of a USB drive: an update is copied to it, the
            # device installs it once ejected
            words = pgettext("chip", "Eject to install")
        else:
            # TRANSLATORS: a chip on the tile of a connected device: an update is copied to
            # it, the device installs it once unplugged
            words = pgettext("chip", "Unplug to install")
        when = full_time(datetime.fromtimestamp(tile.update_ready))
        said.append((words, "warning", _("Checked {when}").format(when=when)))
    elif found is not None:
        said.append((update_available(), "warning",
                     _("Checked {when}").format(when=full_time(datetime.fromtimestamp(found)))))
    if tile.to_sync:
        count = tile.to_sync
        if tile.synced_at is not None:
            # TRANSLATORS: the tooltip of the chip "To sync": activities on the device that
            # openExpress has not uploaded yet, since its last sync (a date and an hour)
            tooltip = ngettext("{count} new activity since {when}",
                               "{count} new activities since {when}", count).format(
                count=count, when=full_time(datetime.fromtimestamp(tile.synced_at)))
        else:
            tooltip = ngettext("{count} new activity", "{count} new activities",
                               count).format(count=count)
        # TRANSLATORS: a chip on the tile of a connected device: it has activities that
        # openExpress has not uploaded yet
        said.append((pgettext("chip", "To sync"), "accent", tooltip))
    elif tile.never_synced:
        said.append((never_synced(), "dimmed", never_synced_tooltip()))
    if tile.kind == "ums":
        # TRANSLATORS: a chip on the tile of a device in mass storage mode
        said.append((pgettext("chip", "USB drive"), "dimmed", None))
    if tile.primary:
        # TRANSLATORS: a chip on the tile of the primary wearable of the Garmin account (its
        # tooltip says what it is)
        said.append((_("Primary"), "dimmed", primary_wearable_tooltip()))
    if tile.account_known and not tile.on_account and tile.kind != "ums":
        # TRANSLATORS: a chip on the tile of a device that is not on the Garmin account
        said.append((_("Not on your account"), "dimmed", None))
    if tile.to_sync == 0 and not tile.never_synced and tile.synced_at is not None:
        # Counted (a count of 0 comes only for a device synced here before), nothing to
        # upload, and a sync recorded (files marked as uploaded alone are no sync): last, it
        # gives way to every other chip (25.10.2026).
        # TRANSLATORS: a chip on the tile of a connected device that openExpress has synced
        # before and that has no activities to upload now
        said.append((pgettext("chip", "Synced"), "dimmed", None))
    return said[:MAX_CHIPS]


class _ChipsLayout(Gtk.LayoutManager):
    """The layout of the row of chips of a tile: it asks for no more width than its chips need
    at least, and lays them out in the width the tile gives it. Asking for all of them on one
    line made the tiles wider, so that fewer of them shared a line (24.10.2026)."""

    __gtype_name__ = "OpenExpressChipsLayout"

    def do_get_request_mode(self, widget):
        child = widget.get_first_child()
        return child.get_request_mode() if child is not None else Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, widget, orientation, for_size):
        child = widget.get_first_child()
        if child is None or not child.should_layout():
            return 0, 0, -1, -1
        least, natural, low, high = child.measure(orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return least, least, -1, -1
        return least, natural, low, high

    def do_allocate(self, widget, width, height, baseline):
        child = widget.get_first_child()
        if child is not None and child.should_layout():
            child.allocate(width, height, baseline, None)


def chips_row(said, align: Gtk.Align = Gtk.Align.CENTER) -> Adw.Bin:
    """The chips of a tile on a line in the middle (of a row of a list: from its start,
    `align`), wrapped when the tile is too narrow (libadwaita 1.7: Adw.WrapBox; before it,
    one line whose chips get shorter), never making the tile wider (_ChipsLayout). Without
    chips the line keeps the height of one: every tile is as tall, the words under the names
    on one line. Its chips can be changed alone (StartPage.set_chips: the row's child)."""
    holder = Adw.Bin(child=_chips_line(said, align))
    holder.set_layout_manager(_ChipsLayout())
    return holder


def _chips_line(said, align: Gtk.Align = Gtk.Align.CENTER) -> Gtk.Widget:
    if hasattr(Adw, "WrapBox"):
        row = Adw.WrapBox(child_spacing=6, line_spacing=4,
                          align=0.0 if align == Gtk.Align.START else 0.5, halign=align)
    else:
        row = Gtk.Box(spacing=6, halign=align)
    for words, style, tooltip in said:
        label = Gtk.Label(label=words, ellipsize=Pango.EllipsizeMode.END, tooltip_text=tooltip)
        label.add_css_class("oe-chip")
        label.add_css_class(style)
        row.append(label)
    if not said:
        # A chip without words or colour: the font and padding of one, nothing to see.
        row.append(Gtk.Label(label="", css_classes=["oe-chip"]))
    return row


class StartPage:
    """The page "Devices": a notice on top (the state on USB, while no device is open), the
    tiles of the devices connected, of those remembered and not connected, and of those only
    on the Garmin account. `on_open(key)` is called with the place the tile clicked had in
    the list given to show(), whatever section it is in."""

    def __init__(self, on_open=None, header_start=(), header_end=(), picture_of=None):
        # How the sections show their devices (devicelist.layout): the view chosen in the
        # page's menu; the next show() lays them out so.
        self.view = devicelist.AUTO
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

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24,
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
        # The picture known of a part number (never by unit ID), read from the window's
        # store when a tile is built; set_picture puts one into the tiles already there.
        self._picture_of = picture_of or (lambda _part: None)
        self.set_empty("", "", False)
        page.append(self.notice_group)  # the whole width, above the block of tiles
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        self._sections = {
            # TRANSLATORS: heading above the tiles of the devices connected now
            CONNECTED: _Section(_("Connected"), on_open),
            # TRANSLATORS: heading above the tiles of the devices seen before, not connected now
            REMEMBERED: _Section(_("Remembered"), on_open),
            # TRANSLATORS: heading above the tiles of the devices of the Garmin account that
            # were never connected to this computer
            ON_ACCOUNT: _Section(_("On the account"), on_open),
        }
        for section in self._sections.values():
            box.append(section.title)
            box.append(section.grid)
            box.append(section.list)
        # One sentence under the tiles while the account's list is not known (analysis 14,
        # c4): why the tiles say nothing of the account (set_account_note).
        self.account_note = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                      visible=False, css_classes=["dim-label", "caption"])
        box.append(self.account_note)
        connected, remembered, account = self._sections.values()
        self.connected_title, self.grid, self.list = (connected.title, connected.grid,
                                                      connected.list)
        self.remembered_title, self.remembered_grid, self.remembered_list = (
            remembered.title, remembered.grid, remembered.list)
        self.account_title, self.account_grid = account.title, account.grid
        # The tiles' width, from the width of the page, the same in every section.
        self.columns = _TileColumns(box, [s.grid for s in self._sections.values()])
        page.append(self.columns)
        clamp = Adw.Clamp(maximum_size=900, child=page)
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

    def set_account_note(self, text: str | None) -> None:
        """The sentence under the tiles about the account (app.account_note); None: none."""
        if text:
            self.account_note.set_label(text)
        self.account_note.set_visible(bool(text))

    def show(self, entries, notice: bool) -> None:
        """The tiles (devicelist.Tile, how the device is connected), each in its section, in
        this order within it; `notice`: whether the notice on top is shown. Each item keeps
        its place in `entries`, which on_open is given."""
        self.notice_group.set_visible(notice)
        parts: dict[str, list] = {name: [] for name in self._sections}
        for key, (tile, how) in enumerate(entries):
            parts[devicelist.section(tile)].append((key, tile, how))
        self.columns.set_most(max(len(part) for part in parts.values()))
        for name, section in self._sections.items():
            part = parts[name]
            mode = devicelist.layout(len(part), self.view)  # automatic: a list for many
            keys = (mode, [display_key(tile, how) for _key, tile, how in part])
            built = False
            if keys != section.shown:  # items that show the same keep their widgets
                gone = _one_gone(section.shown, keys)
                if gone is not None:  # one device went (forgotten): only its item goes
                    (section.list if mode == LIST else section.grid).remove(
                        section.items()[gone])
                else:
                    built = True
                    section.grid.remove_all()
                    section.list.remove_all()
                    for key, tile, how in part:
                        picture = self._picture_of(tile.image_part_number)
                        item = (row_widget(tile, how, picture) if mode == LIST
                                else tile_widget(tile, how, picture))
                        item.key = key
                        (section.list if mode == LIST else section.grid).append(item)
                section.shown = keys
            if not built:  # the same items: their places may have moved; a kind learnt since
                for item, (key, tile, _how) in zip(section.items(), part, strict=True):
                    item.key = key
                    if not item.has_picture:
                        tile_image(item).set_from_icon_name(kind_icon(devicelist.icon_kind(tile)))
            section.title.set_visible(bool(part))
            section.grid.set_visible(mode is not None and mode != LIST)
            section.list.set_visible(mode == LIST)
        self.stack.set_visible_child_name("content")

    def set_chips(self, key: int, tile) -> bool:
        """The chips of the tile at `key` (its place in the list given to show()) as `tile`
        says them now: only its row of chips is changed, the tile (or the row of a list) is not
        built again (a count that came after the page was drawn). False when no item has that
        key."""
        for section in self._sections.values():
            for place, item in enumerate(section.items()):
                if getattr(item, "key", None) != key:
                    continue
                if isinstance(item, DeviceRow):
                    item.set_chips(chips(tile))
                elif getattr(item, "chips", None) is not None:
                    item.chips.set_child(_chips_line(chips(tile)))
                mode, keys = section.shown  # what it shows, for the next show()
                keys = list(keys)
                keys[place] = display_key(tile, keys[place][1])
                section.shown = (mode, keys)
                return True
        return False

    def set_picture(self, part_number: str, texture) -> None:
        """The picture of the devices of this part number, put into their tiles that are
        there (the same widgets: nothing is built again); those built later read it from
        the window's store (picture_of)."""
        for section in self._sections.values():
            for item in section.items():
                if getattr(item, "part_number", None) == part_number:
                    tile_image(item).set_from_paintable(texture)
                    item.has_picture = True

    def tiles(self) -> list:
        """The items shown (tiles, or rows of a section of many), in the order of the whole
        list: the connected ones, the remembered ones, those only on the account."""
        return [item for section in self._sections.values() for item in section.items()]


class RememberedPage:
    """The read-only view of a device remembered and not connected: what was known of it
    when it was last seen, and its last sync. It offers nothing to do: Sync and the rows of
    updates and maps cannot be used, the banner says why. It is given words only (never a
    device, an address or a unit ID), and the image of the model: the icon of its kind, then
    its picture, as on its tile."""

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
        self.image = Gtk.Image(icon_name=DEVICE_ICON, pixel_size=128)
        self._part = ""  # the part number whose picture this view may show now
        box.append(self.image)
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
        # With its details, as on the page of a connected device (analysis 14, b8).
        self.last_sync_row = Adw.ExpanderRow(title=_("Last sync"), enable_expansion=False)
        self._last_sync_details = None
        group.add(self.last_sync_row)
        content.add(group)

        # What the account's list in memory says of it, read only (set_account_rows).
        self.account_group = Adw.PreferencesGroup(title=_("Garmin Connect account"),
                                                  visible=False)
        self.account_rows: list = []
        content.add(self.account_group)

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

        # Only for a device remembered here (allow_forget); the window connects it.
        self.forget_group = Adw.PreferencesGroup(visible=False)
        # TRANSLATORS: button at the bottom of the page of a device not connected: this
        # computer forgets what it keeps of the device (it asks first)
        self.forget_button = Gtk.Button(label=_("Forget Device…"), halign=Gtk.Align.CENTER,
                                        css_classes=["destructive-action", "pill"])
        self.forget_group.add(self.forget_button)
        content.add(self.forget_group)

        view = Adw.ToolbarView(content=content)
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        self.page = Adw.NavigationPage(title=_("Devices"), tag="remembered", child=view)

    def show(self, name: str, version: str, part_number: str, last_seen: str,
             last_sync: str, seen_tooltip: str | None = None, update: str | None = None) -> None:
        """What is known; a version or a part number not known is left out, not shown
        empty. `seen_tooltip`: when it was last seen, in full; `update`: what the last check
        of updates found (words.remembered_update_text), None: "Connect the device to
        check"."""
        name = name or _("Garmin device")
        self.page.set_title(name)
        self.name_label.set_label(name)
        self.version_label.set_label(version)
        self.version_label.set_visible(bool(version))
        self.seen_label.set_tooltip_text(seen_tooltip)
        self.part_row.set_subtitle(GLib.markup_escape_text(part_number))
        self.tools_group.set_visible(bool(part_number))
        self.show_texts(last_seen, last_sync, update)

    def show_texts(self, last_seen: str, last_sync: str, update: str | None = None) -> None:
        """The texts told against now (show; once a minute, the window): each set only when
        its words changed."""
        update = update or _("Connect the device to check")
        if self.seen_label.get_label() != last_seen:
            self.seen_label.set_label(last_seen)
        for row, text in ((self.last_sync_row, last_sync), (self.update_row, update)):
            text = GLib.markup_escape_text(text)
            if row.get_subtitle() != text:
                row.set_subtitle(text)

    def set_last_sync_details(self, tooltip: str | None, details) -> None:
        """The tooltip of "Last sync" (the full date; "Never synced …") and its details
        ((text, warning) of last_sync.describe), drawn once whatever came before."""
        self.last_sync_row.set_tooltip_text(tooltip)
        if self._last_sync_details is not None:
            self.last_sync_row.remove(self._last_sync_details.get_parent()
                                      or self._last_sync_details)
            self._last_sync_details = None
        self.last_sync_row.set_enable_expansion(bool(details))
        if details:
            self._last_sync_details = last_sync_details(details)
            self.last_sync_row.add_row(self._last_sync_details)

    def set_account_rows(self, rows) -> None:
        """The rows of the account ((title, subtitle), read only); none: no group."""
        for row in self.account_rows:
            self.account_group.remove(row)
        self.account_rows = []
        for title, subtitle in rows:
            row = Adw.ActionRow(title=GLib.markup_escape_text(title),
                                subtitle=GLib.markup_escape_text(subtitle))
            self.account_group.add(row)
            self.account_rows.append(row)
        self.account_group.set_visible(bool(rows))

    def allow_forget(self, on: bool) -> None:
        """Whether the button "Forget Device…" is there: for a device remembered here, not one
        only on the account."""
        self.forget_group.set_visible(on)

    def show_icon(self, part_number: str, icon: str) -> None:
        """The icon of the device's kind until its picture comes; only a picture of this
        part number is taken after it (not a late one of the device shown before)."""
        self._part = part_number
        self.image.set_from_icon_name(icon)

    def set_picture(self, part_number: str, texture) -> bool:
        """The picture of the model, if this view still shows a device of that part
        number. Whether it was taken."""
        if not part_number or part_number != self._part:
            return False
        self.image.set_from_paintable(texture)
        return True


def last_sync_details(said) -> Gtk.Box:
    """The details of a last sync as one row of a few lines (the page of a connected device
    and of a remembered one): the files, those already in Garmin Connect, then what went
    wrong, with an icon and in the colour of warnings."""
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
    return details


def tile_image(item) -> Gtk.Image:
    """The image of a tile or a row: the icon, or the picture of the device in its place."""
    return item.image


def kind_icon(category) -> str:
    return KIND_ICONS.get(category, DEVICE_ICON)


def _name(tile) -> str:
    return tile.description or _("Garmin device")


def seen_tooltip(tile) -> str | None:
    """The tooltip of a device not connected that this computer has seen: when, in full
    (under its name it is told against now)."""
    if tile.connected or not tile.last_seen:
        return None
    return last_seen_tooltip(tile.last_seen)


def _image(tile, picture, size: int) -> Gtk.Image:
    """The image of an item, the same for a tile and a row: the picture of the device if it
    is known, else the icon of its kind (the picture is put in its place later, in the
    same size)."""
    image = Gtk.Image(icon_name=kind_icon(devicelist.icon_kind(tile)), pixel_size=size)
    if picture is not None:
        image.set_from_paintable(picture)
    return image


def _mark(item, tile, image, picture) -> None:
    """What set_picture and show need to find in an item: its image, which picture goes
    into it and whether it is there."""
    item.image = image
    item.part_number = tile.image_part_number
    item.has_picture = picture is not None


class DeviceRow(Gtk.ListBoxRow):
    """One row of a section of many devices, laid out as its tile, smaller: the picture or
    the icon of the kind, then the name, its chips (start.chips, from the start of the line)
    and the words under the name, then the arrow; spaced as an Adw.ActionRow of the same
    list, which takes no widget between its title and its subtitle (24.10.2026). Opened as a
    row of the list (row-activated: a click, Enter or Space); a screen reader hears its name,
    chips and words. Its chips change alone (set_chips, StartPage.set_chips)."""

    def __init__(self, tile, how: str, picture=None):
        super().__init__(activatable=True, focusable=True)
        self._title, self._subtitle = _name(tile), how
        line = Gtk.Box(margin_start=ROW_SIDE, margin_end=ROW_SIDE,
                       height_request=ROW_MIN_HEIGHT)
        image = _image(tile, picture, ROW_ICON_SIZE)
        line.append(image)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ROW_TEXTS_SPACING,
                        valign=Gtk.Align.CENTER, hexpand=True, margin_start=ROW_SIDE,
                        margin_end=ROW_TEXTS_AROUND, margin_top=ROW_TEXTS_AROUND,
                        margin_bottom=ROW_TEXTS_AROUND)
        texts.append(Gtk.Label(label=self._title, xalign=0, wrap=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR, css_classes=["title"]))
        self.chips = chips_row([], Gtk.Align.START)
        texts.append(self.chips)
        if how:
            texts.append(Gtk.Label(label=how, xalign=0, wrap=True,
                                   wrap_mode=Pango.WrapMode.WORD_CHAR,
                                   css_classes=["subtitle"]))
        line.append(texts)
        line.append(Gtk.Image(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER))
        self.set_child(line)
        self.set_tooltip_text(seen_tooltip(tile))
        _mark(self, tile, image, picture)
        self.set_chips(chips(tile))

    def get_title(self) -> str:
        return self._title

    def get_subtitle(self) -> str:
        return self._subtitle

    def set_chips(self, said) -> None:
        """Its chips as `said` (start.chips) now; none: no line for them (no empty chip,
        which the tiles keep for their height)."""
        self.chips.set_child(_chips_line(said, Gtk.Align.START))
        self.chips.set_visible(bool(said))
        self.accessible_label = ", ".join(
            [self._title, *(words for words, _style, _tooltip in said)]
            + ([self._subtitle] if self._subtitle else []))
        self.update_property([Gtk.AccessibleProperty.LABEL], [self.accessible_label])


def row_widget(tile, how: str, picture=None) -> DeviceRow:
    """One row of a section of many devices (DeviceRow)."""
    return DeviceRow(tile, how, picture)


def tile_widget(tile, how: str, picture=None) -> Gtk.FlowBoxChild:
    """One tile: the picture of the device if it is known, else the icon of its kind (the
    picture is put in its place later, in the same size), the name, its chips, how it is
    connected and its version; framed when connected."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    image = _image(tile, picture, TILE_ICON_SIZE)
    box.append(image)
    # max_width_chars=1: the texts never ask for more than the tile's width (they take
    # the width the grid gives the tile), so that every tile is as wide as the others.
    name = Gtk.Label(label=_name(tile), ellipsize=Pango.EllipsizeMode.END,
                     max_width_chars=1, tooltip_text=_name(tile))
    name.add_css_class("heading")
    box.append(name)
    row = chips_row(chips(tile))
    box.append(row)
    if how:
        label = Gtk.Label(label=how, wrap=True, justify=Gtk.Justification.CENTER,
                          ellipsize=Pango.EllipsizeMode.END, lines=SUBTITLE_LINES,
                          max_width_chars=1)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        box.append(label)
    child = Gtk.FlowBoxChild(child=box, tooltip_text=seen_tooltip(tile))
    _mark(child, tile, image, picture)
    child.chips = row
    child.add_css_class("card")
    child.add_css_class("oe-tile")
    if tile.connected:
        child.add_css_class("connected")
    return child
