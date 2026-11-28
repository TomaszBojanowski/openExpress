"""The heart rate of an activity's page, over its chart: beside the title its average and highest
heart rate; on top one bar of the share of each zone in the zone's colour; right under it the
zones as its device worked them out (fit.HeartRateZones), zone 5 on top, then 4 to 1 and the
time below zone 1, each with its bounds, its time and share, and a bar of its share (they say
the bar's colours); under the title how the bounds were worked out. From the activity journal
(which keeps the time in each zone, never their bounds) the bar alone, with a legend of each
zone and its share under it; the whole time below zone 1 (no second in zones 1 to 5), one
sentence instead of the bar and the rows; a file with a heart rate but no zones, its average and
highest alone. The zones' colours read on the cards of the light style and of the dark one alike (a
contrast of 3:1 and a margin with both); the chart of heart rate draws its bands in them
(gui/activity_chart.py). The header of a section of the top (SectionHeader: the title, a
description under it and the words beside them, or under them in a narrow window) is here too.
Has: no attribute of the window; the list keeps its rows."""
from __future__ import annotations

import math
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402 - imports must follow gi.require_version

from openexpress.gui.palette import BLUE, GREEN, GREY, rgba  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    all_below_zone_1_text,
    detail_name,
    zone_name,
    zone_range,
    zone_share,
    zone_share_text,
    zone_shares,
    zones_method_text,
    zones_title,
)
from openexpress.i18n import caption  # noqa: E402

# Below zone 1, zones 1-5.
ZONE_COLOURS = (GREY, BLUE, GREEN, "#bd8100", "#e66100", "#f0384a")
BAR_HEIGHT = 8
SHARES_HEIGHT = 12  # pixels of the bar of every zone's share
GAP = 2  # pixels between two zones on it
DOT = 8  # pixels of a zone's dot in the legend (and of its square beside its name on the chart)
# The header of a section, placed as libadwaita's group (1.10) places its own.
HEADER_GAP = 12  # pixels at least between a title or its description and the words beside it
LABELS_SPACING = 6  # pixels between the title, its description and the words under them
SINGLE_LINE = 34  # pixels of the line of a title without a description
UNDER_TITLE = 6  # pixels from a title without a description to what follows it
UNDER_DESCRIPTION = 12  # pixels from a description to what follows it


def rounded_bar(cr, width: float, height: float) -> None:
    """The path of a bar with round ends, `height` thick."""
    radius = height / 2
    cr.new_sub_path()
    cr.arc(width - radius, radius, radius, -math.pi / 2, math.pi / 2)
    cr.arc(radius, radius, radius, math.pi / 2, 3 * math.pi / 2)
    cr.close_path()


def _draw_bar(bar, cr, width: int, height: int) -> None:
    fg = bar.get_color()
    cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
    rounded_bar(cr, width, height)
    cr.fill()
    if bar.share > 0:
        color = bar.fill()
        cr.set_source_rgba(color.red, color.green, color.blue, 1)
        rounded_bar(cr, max(height, width * min(1.0, bar.share)), height)
        cr.fill()


def _whole_words(text: str) -> Pango.AttrList:
    """A text broken onto a new line between its words only: never inside a word such as
    "km/h", nor after the dash of "118–140"."""
    words = Pango.AttrList()
    for word in re.finditer(r"\S+", text):
        whole = Pango.attr_allow_breaks_new(False)
        whole.start_index = len(text[:word.start()].encode())
        whole.end_index = len(text[:word.end()].encode())
        words.insert(whole)
    return words


class ZoneBar(Gtk.DrawingArea):
    """The share of a zone in its colour, over the whole of the time; for the eye only (the
    row says it)."""

    __gtype_name__ = "OpenExpressZoneBar"

    def __init__(self, share: float, colour: str):
        super().__init__(content_height=BAR_HEIGHT, hexpand=True,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.share, self.colour = share, colour
        self.set_draw_func(_draw_bar)

    def fill(self) -> Gdk.RGBA:
        """The colour of the share."""
        return rgba(self.colour)


class ZoneRow(Gtk.Box):
    """A zone: its name and bounds, its time and share, its bar under them."""

    __gtype_name__ = "OpenExpressZoneRow"

    def __init__(self, number: int, zones):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10,
                         margin_bottom=10, margin_start=12, margin_end=12)
        total = sum(zones.times)
        bounds = zone_range(zones.bounds, number)
        line = Gtk.Box(spacing=10)
        line.append(Gtk.Label(label=zone_name(number), xalign=0))
        span = Gtk.Label(label=bounds, xalign=0, hexpand=True, wrap=True,
                         attributes=_whole_words(bounds))
        span.add_css_class("dim-label")
        span.add_css_class("caption")
        line.append(span)
        share = Gtk.Label(label=zone_share(zones.times[number], total), xalign=1)
        share.add_css_class("numeric")
        line.append(share)
        self.append(line)
        self.append(ZoneBar(zones.times[number] / total if total else 0, ZONE_COLOURS[number]))


def _draw_shares(bar, cr, width: int, height: int) -> None:
    """Each zone's share side by side, a gap between two, the ends round; a zone of no time
    takes no room."""
    shown = [(part, colour) for (_number, part), colour in zip(bar.parts, bar.colours,
                                                                 strict=True) if part > 0]
    room = width - GAP * (len(shown) - 1)
    if not shown or room <= 0:
        return
    cr.save()
    rounded_bar(cr, width, height)
    cr.clip()
    x = 0.0
    for part, colour in shown:
        fill = rgba(colour)
        cr.set_source_rgba(fill.red, fill.green, fill.blue, 1)
        cr.rectangle(x, 0, room * part, height)
        cr.fill()
        x += room * part + GAP
    cr.restore()


class SharesBar(Gtk.DrawingArea):
    """The share of each zone of the time in all, in the zones' colours; for the eye only (the
    legend says it). `parts`: (zone, share) in their order, zone 1 first (0, below zone 1,
    before it when there is some time there)."""

    __gtype_name__ = "OpenExpressSharesBar"

    def __init__(self, times):
        super().__init__(content_height=SHARES_HEIGHT, hexpand=True,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        total = sum(times)
        self.parts = [(number, seconds / total) for number, seconds in enumerate(times)
                      if number or seconds]
        self.colours = [ZONE_COLOURS[number] for number, _part in self.parts]
        self.set_draw_func(_draw_shares)


def _draw_dot(area, cr, width: int, height: int) -> None:
    colour = rgba(area.colour)
    cr.set_source_rgba(colour.red, colour.green, colour.blue, 1)
    cr.arc(width / 2, height / 2, min(width, height) / 2, 0, 2 * math.pi)
    cr.fill()


class ZoneShares(Gtk.Box):
    """The bar of the zones' shares and, asked for (`legend`), a legend under it (`legend`,
    None without one): each zone's dot in its colour, its name and share ("Zone 1 · 6%"),
    wrapping in a narrow window."""

    __gtype_name__ = "OpenExpressZoneShares"

    def __init__(self, times, legend: bool = True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14,
                         margin_bottom=12, margin_start=12, margin_end=12)
        self.bar = SharesBar(times)
        self.append(self.bar)
        self.legend = None
        if not legend:
            return
        # Nothing selected and no background under the pointer (as activity_colours.plain_flow,
        # which needs this module's colours).
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1,
                           max_children_per_line=6, column_spacing=16, row_spacing=4,
                           halign=Gtk.Align.START)
        flow.add_css_class("oe-plain")
        for number, share in zone_shares(times):
            item = Gtk.Box(spacing=6)
            dot = Gtk.DrawingArea(content_width=DOT, content_height=DOT,
                                  valign=Gtk.Align.CENTER,
                                  accessible_role=Gtk.AccessibleRole.PRESENTATION)
            dot.colour = ZONE_COLOURS[number]
            dot.set_draw_func(_draw_dot)
            item.append(dot)
            said = Gtk.Label(label=zone_share_text(number, share), xalign=0)
            said.add_css_class("caption")
            item.append(said)
            flow.append(Gtk.FlowBoxChild(child=item, focusable=False))
        self.legend = flow
        self.append(flow)


class ZoneList(Gtk.Box):
    """The section of the heart rate: its header (SectionHeader: its title, how the bounds were
    worked out under it, the average and highest heart rate beside them, `said`); the bar of
    the shares of `times` (the seconds below zone 1, then in zones 1 to 5; those of `zones`
    when not given) and the rows of `zones` (fit.HeartRateZones) under it, or without them its
    legend. The whole time below zone 1: one sentence (`below`) instead of them, with where zone
    1 begins when `zones` says it. No time in any zone: titled the heart rate, its average and
    highest alone."""

    __gtype_name__ = "OpenExpressZoneList"

    def __init__(self, zones=None, times=None, said: list[str] | tuple = ()):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        times = tuple(zones.times) if zones is not None and times is None else times
        below_only = bool(times) and times[0] > 0 and not any(times[1:])
        self.below = None
        if below_only:
            self.below = Gtk.Label(label=all_below_zone_1_text(
                zones.bounds[0] if zones is not None else None), wrap=True, xalign=0,
                margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.rows = ([ZoneRow(number, zones) for number in reversed(range(len(zones.times)))]
                     if zones is not None and not below_only else [])
        self.shares = (ZoneShares(times, legend=not self.rows)
                       if times and sum(times) > 0 and not below_only else None)
        title = (zones_title() if self.shares is not None or self.rows or below_only
                 else detail_name("heart rate"))
        self.header = SectionHeader(title, zones_method_text(zones) if zones is not None
                                    else None, said)
        self.said = self.header.said
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.append(self.header)
        rows = [w for w in (self.below, self.shares) if w is not None]
        if rows or self.rows:
            self.append(boxed_list([*rows, *self.rows]))


def _natural(widget) -> int:
    return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[1]


def _rectangle(x: int, y: int, width: int, height: int) -> Gdk.Rectangle:
    found = Gdk.Rectangle()
    found.x, found.y, found.width, found.height = x, y, width, height
    return found


class BesideOrUnder(Gtk.LayoutManager):
    """The header of a section (SectionHeader): its title with the description under it
    (`lead`), and the words beside them (`said`) at the end, on the line of the last of them,
    while both fit side by side at their natural width; else the words under them, at their
    start. A title alone is in the middle of a line as tall as libadwaita's."""

    __gtype_name__ = "OpenExpressBesideOrUnder"

    @staticmethod
    def beside(widget, width: int) -> bool:
        """Whether the words go beside the title at `width` (-1: any)."""
        return width < 0 or _natural(widget.lead) + HEADER_GAP + _natural(widget.said) <= width

    def do_get_request_mode(self, widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def _placed(self, widget, width: int):
        """Where the lead and the words go at `width`, each (x, y, width, height), the words
        None when there are none; and the height of it all."""
        lead, said = widget.lead, widget.said
        vertical = Gtk.Orientation.VERTICAL
        if not said.should_layout():
            tall = lead.measure(vertical, width)[1]
            whole = max(tall, widget.least)
            return (0, (whole - tall) // 2, width, tall), None, whole
        if self.beside(widget, width):
            # The gap is in the lead's room: its words end before it.
            said_w = _natural(said)
            lead_w = width - said_w if width >= 0 else _natural(lead)
            lead_h = lead.measure(vertical, lead_w)[1]
            said_h = said.measure(vertical, said_w)[1]
            whole = max(lead_h, said_h, widget.least)
            return ((0, (whole - lead_h) // 2, lead_w, lead_h),
                    (width - said_w, whole - said_h, said_w, said_h), whole)
        lead_h = lead.measure(vertical, width)[1]
        said_w = min(_natural(said), width)
        said_h = said.measure(vertical, said_w)[1]
        return ((0, 0, width, lead_h), (0, lead_h + LABELS_SPACING, said_w, said_h),
                lead_h + LABELS_SPACING + said_h)

    def do_measure(self, widget, orientation, for_size):
        lead, said = widget.lead, widget.said
        if orientation == Gtk.Orientation.HORIZONTAL:
            least, natural = lead.measure(orientation, -1)[:2]
            if said.should_layout():
                least = max(least, said.measure(orientation, -1)[0])
                natural += HEADER_GAP + _natural(said)
            return least, natural, -1, -1
        whole = self._placed(widget, for_size)[2]
        return whole, whole, -1, -1

    def do_allocate(self, widget, width, height, baseline):
        lead, words, _whole = self._placed(widget, width)
        widget.lead.size_allocate(_rectangle(*lead), -1)
        if words is not None:
            widget.said.size_allocate(_rectangle(*words), -1)


class SectionHeader(Gtk.Box):
    """The header of a section of an activity's page: its title (`title`, wrapped rather than
    cut), the description under it (`description`, None without one) and the words beside them
    (`said`: a few short ones, "148 bpm average · 176 bpm highest"; hidden without), under
    them when they do not fit side by side (BesideOrUnder); placed as libadwaita's group places
    its header, and as far from what follows."""

    __gtype_name__ = "OpenExpressSectionHeader"

    def __init__(self, title: str, description: str | None = None,
                 said: list[str] | tuple = ()):
        super().__init__()
        self.set_layout_manager(BesideOrUnder())
        self.lead = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=LABELS_SPACING)
        self.title = Gtk.Label(label=title, wrap=True, xalign=0)
        self.title.add_css_class("heading")
        self.title.add_css_class("h4")
        self.lead.append(self.title)
        self.description = None
        if description:
            self.description = Gtk.Label(label=description, wrap=True,
                                         wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0)
            self.description.add_css_class("body")
            self.description.add_css_class("dim-label")
            self.lead.append(self.description)
        self.append(self.lead)
        self.said = Gtk.Label(label=caption(*said), wrap=True, xalign=0, visible=bool(said))
        self.said.add_css_class("dim-label")
        self.said.add_css_class("caption")
        self.append(self.said)
        self.least = SINGLE_LINE if self.description is None else 0
        self.set_margin_bottom(UNDER_TITLE if self.description is None else UNDER_DESCRIPTION)


def boxed_list(rows: list[Gtk.Widget]) -> Gtk.ListBox:
    """`rows` in a boxed list, none of them activatable."""
    listed = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listed.add_css_class("boxed-list")
    for row in rows:
        listed.append(Gtk.ListBoxRow(child=row, activatable=False))
    return listed


def boxed_section(title: str, rows: list[Gtk.Widget],
                  description: str | None = None) -> Adw.PreferencesGroup:
    """A section of an activity's page: its title, `description` under it when there is one,
    and `rows` in a boxed list, none of them activatable."""
    group = Adw.PreferencesGroup(title=title)
    if description:
        group.set_description(description)
    if rows:
        group.add(boxed_list(rows))
    return group
