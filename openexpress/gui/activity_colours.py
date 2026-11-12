"""The colours of an activity's track by the series chosen on its chart (heart rate, elevation,
pace or speed), and their legend under the map with the switch that turns them on and off (on
at first, kept in gui.json).

The scale runs from blue (low; for a pace, slow) through purple to red-orange (high; for a pace,
fast), over the activity's own range: from its 5th to its 95th percentile, a value beyond an end
in the colour of that end. Its colours are steps of OKLCH, their lightness rising from 0.58 to
0.675: each stands out at 3:1 at least from the map without tiles, the window's background in
the light style (#fafafb) and in the dark one (#222226); on the tiles a dark edge under the
track does it. It has no green and no yellow; still, with deuteranopia or protanopia its blue
and its purple look alike, and its order is seen by its lightness and said by the legend. A
point where the series has no value is grey. The numbers need no screen.
The chart's line and cursor, the cursor's marker on the map and the bars of the training effect
are in one accent colour (accent_colour).
Has: no attribute of the window; the map keeps its legend (gui/activity_map.py)."""
from __future__ import annotations

import bisect
import math

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.gui.activity_zones import rounded_bar  # noqa: E402
from openexpress.gui.gui_settings import load_gui_settings, update_gui_settings  # noqa: E402
from openexpress.gui.palette import GREY, rgba  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    PACES,
    chart_value_text,
    colours_text,
    track_colour_text,
)

SETTING = "track_colours"  # in gui.json: the switch under the map (on when not there)
STEPS = 64  # colours of the scale
PERCENTILES = 0.05, 0.95  # at the ends of the scale
EDGE = "#241f31"  # the dark edge under a coloured track
NO_VALUE = GREY  # where the series has no value
LEGEND_WIDTH = 96  # pixels of the scale in the legend
LEGEND_HEIGHT = 8
# libadwaita's blue for a line on the window (its accent colour in 1.5, which has no other), in
# the light style and in the dark one
LINE_BLUE = {False: "#1c71d8", True: "#78aeed"}


def accent_colour() -> Gdk.RGBA:
    """The desktop's accent colour in the shade for a line or a mark on the window's background
    (libadwaita's standalone one from 1.6), else libadwaita's blue; each as the window's style
    has it."""
    manager = Adw.StyleManager.get_default()
    if hasattr(Adw, "AccentColor"):
        return Adw.AccentColor.to_standalone_rgba(manager.get_accent_color(), manager.get_dark())
    return rgba(LINE_BLUE[manager.get_dark()])


def _srgb(linear: float) -> float:
    linear = min(1.0, max(0.0, linear))
    return 12.92 * linear if linear <= 0.0031308 else 1.055 * linear ** (1 / 2.4) - 0.055


def _oklch(lightness: float, chroma: float, hue: float) -> str:
    """A colour of OKLCH as "#rrggbb", its chroma brought down until it is one of sRGB."""
    while True:
        a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
        lms = ((lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3,
               (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3,
               (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3)
        rgb = (4.0767416621 * lms[0] - 3.3077115913 * lms[1] + 0.2309699292 * lms[2],
               -1.2684380046 * lms[0] + 2.6097574011 * lms[1] - 0.3413193965 * lms[2],
               -0.0041960863 * lms[0] - 0.7034186147 * lms[1] + 1.7076147010 * lms[2])
        if all(-1e-4 <= c <= 1 + 1e-4 for c in rgb) or chroma <= 0:
            return "#" + "".join(f"{round(_srgb(c) * 255):02x}" for c in rgb)
        chroma -= 0.002


def scale_colour(share: float) -> str:
    """The colour of the scale at `share` (0: low, blue; 1: high, red-orange)."""
    share = min(1.0, max(0.0, share))
    return _oklch(0.58 + 0.095 * share, 0.17 + 0.03 * share, (250 + 150 * share) % 360)


SCALE = tuple(scale_colour(i / (STEPS - 1)) for i in range(STEPS))


def scale_ends(series) -> tuple[float, float] | None:
    """The values at the low and the high end of the scale: the series' 5th and 95th
    percentile; for a pace the other way round (the slow end is low). None without values."""
    found = sorted(v for v in series.values if v is not None)
    if not found:
        return None
    low, high = (found[round(share * (len(found) - 1))] for share in PERCENTILES)
    return (high, low) if series.kind in PACES else (low, high)


def point_colours(times, series) -> list[str] | None:
    """The colour of each point of the track at these times (the chart's): that of the
    record at its time or the last one before it; grey where it has no value. None: a series
    without values, no colours."""
    ends = scale_ends(series)
    if ends is None:
        return None
    low, high = ends
    out = []
    for at in times:
        i = max(0, bisect.bisect_right(series.times, at) - 1)
        value = series.values[i]
        if value is None:
            out.append(NO_VALUE)
            continue
        share = 0.5 if high == low else (value - low) / (high - low)
        out.append(SCALE[round(min(1.0, max(0.0, share)) * (STEPS - 1))])
    return out


def colours_on() -> bool:
    """Whether the track is coloured: the switch under the map, on until turned off."""
    return load_gui_settings().get(SETTING) is not False


def set_colours(on: bool) -> None:
    update_gui_settings(**{SETTING: bool(on)})


def _draw_scale(_area, cr, width: int, height: int) -> None:
    gradient = cairo.LinearGradient(0, 0, width, 0)
    for i, colour in enumerate(SCALE):
        stop = rgba(colour)
        gradient.add_color_stop_rgb(i / (STEPS - 1), stop.red, stop.green, stop.blue)
    rounded_bar(cr, width, height)
    cr.set_source(gradient)
    cr.fill()


def _plain_flow(**props) -> Gtk.FlowBox:
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1,
                       max_children_per_line=2, **props)
    flow.add_css_class("oe-plain")
    return flow


class TrackLegend(Gtk.Box):
    """Under the map: what colours the track and the values of the scale's ends, its unit
    with them ("Track colour: heart rate  96 bpm ▬ 122 bpm"), and the switch "Colours"; its
    switch alone while the colours are off. Hidden without a series. In a narrow window the
    ends go under the name and the switch under them."""

    __gtype_name__ = "OpenExpressTrackLegend"

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.name = Gtk.Label(xalign=0, wrap=True)
        self.low, self.high = (Gtk.Label(css_classes=["caption", "numeric"])
                               for _side in range(2))
        ends = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        bar = Gtk.DrawingArea(content_width=LEGEND_WIDTH, content_height=LEGEND_HEIGHT,
                              valign=Gtk.Align.CENTER,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
        bar.set_draw_func(_draw_scale)
        for widget in (self.low, bar, self.high):
            ends.append(widget)
        # The name and the ends packed to the left, the ends under the name when narrow.
        self.scale = _plain_flow(column_spacing=10, row_spacing=4, halign=Gtk.Align.START,
                                 valign=Gtk.Align.CENTER)
        for widget in (self.name, ends):
            self.scale.append(Gtk.FlowBoxChild(child=widget, focusable=False))
        label = Gtk.Label(label=colours_text())
        self.switch = Gtk.Switch(active=colours_on(), valign=Gtk.Align.CENTER)
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [colours_text()])
        right = Gtk.Box(spacing=8, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        right.append(label)
        right.append(self.switch)
        row = _plain_flow(column_spacing=12, row_spacing=6)
        row.append(Gtk.FlowBoxChild(child=self.scale, focusable=False, hexpand=True))
        row.append(Gtk.FlowBoxChild(child=right, focusable=False))
        self.append(row)

    def show_series(self, series, on: bool) -> None:
        """The series that colours the track (None: none, the legend hidden), the colours on
        or off."""
        ends = scale_ends(series) if series is not None else None
        self.set_visible(ends is not None)
        if ends is None:
            return
        self.name.set_label(track_colour_text(series.kind))
        self.low.set_label(chart_value_text(series.kind, ends[0]))
        self.high.set_label(chart_value_text(series.kind, ends[1]))
        self.scale.set_visible(on)
