"""The heart rate of an activity's page, over its chart: beside the title its average and highest
heart rate; on top one bar of the share of each zone in the zone's colour, with a legend of each
zone and its share; under it the zones as its device worked them out (fit.HeartRateZones), zone
5 on top, then 4 to 1 and the time below zone 1, each with its bounds, its time and share, and a
bar of its share; under the title how the bounds were worked out. From the activity journal
(which keeps the time in each zone, never their bounds) the bar alone; a file with a heart rate
but no zones, its average and highest alone. The zones' colours read on the cards of the light
style and of the dark one alike (a contrast of 3:1 and a margin with both); the chart of heart
rate draws its bands in them (gui/activity_chart.py).
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
DOT = 8  # pixels of a zone's dot in the legend


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
    """The bar of the zones' shares and under it a legend: each zone's dot in its colour, its
    name and share ("Zone 1 · 6%"), wrapping in a narrow window."""

    __gtype_name__ = "OpenExpressZoneShares"

    def __init__(self, times):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14,
                         margin_bottom=12, margin_start=12, margin_end=12)
        self.bar = SharesBar(times)
        self.append(self.bar)
        # Nothing selected and no background under the pointer (as activity_colours.plain_flow,
        # which needs this module's colours).
        legend = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1,
                             max_children_per_line=6, column_spacing=16, row_spacing=4)
        legend.add_css_class("oe-plain")
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
            legend.append(Gtk.FlowBoxChild(child=item, focusable=False))
        self.append(legend)


class ZoneList(Gtk.Box):
    """The section of the heart rate: its title, the average and highest heart rate beside it
    (`said`), how the bounds were worked out under it; the bar of the shares of `times` (the
    seconds below zone 1, then in zones 1 to 5; those of `zones` when not given) and the rows of
    `zones` (fit.HeartRateZones) under it. No time in any zone: titled the heart rate, its
    average and highest alone."""

    __gtype_name__ = "OpenExpressZoneList"

    def __init__(self, zones=None, times=None, said: list[str] | tuple = ()):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        times = tuple(zones.times) if zones is not None and times is None else times
        self.rows = ([ZoneRow(number, zones) for number in reversed(range(len(zones.times)))]
                     if zones is not None else [])
        self.shares = ZoneShares(times) if times and sum(times) > 0 else None
        title = zones_title() if self.shares is not None or self.rows else detail_name(
            "heart rate")
        rows = [self.shares] if self.shares is not None else []
        self.group = boxed_section(title, [*rows, *self.rows],
                                   zones_method_text(zones) if zones is not None else None)
        self.said = Gtk.Label(label=caption(*said), wrap=True, xalign=1,
                              valign=Gtk.Align.END)
        self.said.add_css_class("dim-label")
        self.said.add_css_class("caption")
        if said:
            self.group.set_header_suffix(self.said)
        self.append(self.group)


def boxed_section(title: str, rows: list[Gtk.Widget],
                  description: str | None = None) -> Adw.PreferencesGroup:
    """A section of an activity's page: its title, `description` under it when there is one,
    and `rows` in a boxed list, none of them activatable."""
    group = Adw.PreferencesGroup(title=title)
    if description:
        group.set_description(description)
    if not rows:
        return group
    listed = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listed.add_css_class("boxed-list")
    for row in rows:
        listed.append(Gtk.ListBoxRow(child=row, activatable=False))
    group.add(listed)
    return group
