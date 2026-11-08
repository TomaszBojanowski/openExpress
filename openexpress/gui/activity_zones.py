"""The heart rate zones of an activity's page, under its laps, as its device worked them out
(fit.HeartRateZones): zone 5 on top, then 4 to 1 and the time below zone 1, each with its
bounds, its time and share, and a bar of its share in the zone's colour; under the title how
the bounds were worked out. The zones' colours read on the cards of the light style and of the
dark one alike (a contrast of 3:1 and a margin with both); the chart of heart rate draws its bands
in them (gui/activity_chart.py).
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
    zone_name,
    zone_range,
    zone_share,
    zones_method_text,
    zones_title,
)

# Below zone 1, zones 1-5.
ZONE_COLOURS = (GREY, BLUE, GREEN, "#bd8100", "#e66100", "#f0384a")
BAR_HEIGHT = 8


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


class ZoneList(Gtk.Box):
    """The section of the zones: its title, how the bounds were worked out, the rows."""

    __gtype_name__ = "OpenExpressZoneList"

    def __init__(self, zones):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        group = Adw.PreferencesGroup(title=zones_title())
        method = zones_method_text(zones)
        if method:
            group.set_description(method)
        listed = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listed.add_css_class("boxed-list")
        self.rows = [ZoneRow(number, zones) for number in reversed(range(len(zones.times)))]
        for row in self.rows:
            listed.append(Gtk.ListBoxRow(child=row, activatable=False))
        group.add(listed)
        self.append(group)
