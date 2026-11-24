"""The chart of an activity's page: one of the series its records give (heart rate, elevation,
and pace or speed by its sport; their words in gui/words.py) over the time since its first
record. A value not given and records that stop for a while (a pause) leave a gap in the line;
so does a stop in the pace, never a slow climb. Pace is smoothed over about 30 s of moving and
drawn with the faster on top, its axis from the pace of most of the moving records (a value
beyond it drawn at its edge); the points are brought down to two a pixel column without losing a
peak; the axes have round values. Heart rate has the bands of the heart rate zones 1-5 behind
it, in their colours (gui/activity_zones.py). A click, a drag or the keys place a cursor, its
value said beside the switch. The numbers need no screen; the drawing is Cairo in a
Gtk.DrawingArea.
Has: no attribute of the window; the chart's widgets keep their series and cursor themselves."""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import ClassVar

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GObject, Gtk, Pango, PangoCairo  # noqa: E402 - imports must follow gi.require_version

from openexpress.gui.activity_colours import accent_colour, plain_flow  # noqa: E402
from openexpress.gui.activity_zones import ZONE_COLOURS  # noqa: E402
from openexpress.gui.palette import rgba  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    PACES,
    activity_clock,
    chart_accessible_name,
    chart_cursor_text,
    chart_series_name,
    chart_tick,
    chart_unit,
    speed_kind,
)

GAP_S = 60  # records further apart than this: a pause, a gap in the line
SMOOTHING_S = 30  # pace: the speed of moving over this time around each record
# A stop: over STOP_WINDOW_S, slower than STOP_SPEED or than STOP_SHARE of the activity's
# typical speed, whichever is more, and not climbing nor descending CLIMB_RATE (180 m an hour,
# over CLIMB_WINDOW_S): a climb slow forward is no stop.
STOP_SPEED = 0.2  # metres a second
STOP_SHARE = 0.25
STOP_WINDOW_S = 10
CLIMB_RATE = 0.05  # metres a second, up or down
CLIMB_WINDOW_S = 20
AXIS_SHARES = (0.05, 0.95)  # pace: its axis from the pace of these shares of the records…
AXIS_MARGIN = 0.1  # …and this share of that span more on each side
KEY_STEP = 100  # a key moves the cursor by a hundredth of the activity
CHART_HEIGHT = 200
LAP_SHADE = 0.14  # the strength of the accent colour behind the lap chosen
ZONE_SHADE = 0.13  # the strength of the zones' colours behind the line of heart rate
WHOLE = ("heart rate", "elevation")  # their axes have whole steps
LEAST_SPAN = {"heart rate": 10, "elevation": 10, "speed": 1, "pace": 60, "swim pace": 10}
NICE = (1, 2, 2.5, 5, 10)
TIME_STEPS = (5, 10, 15, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 10_800, 21_600,
              43_200, 86_400)
PACE_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)


@dataclass(frozen=True)
class Series:
    """A series of the chart: its kind ("heart rate", "elevation", "pace", "swim pace",
    "speed"), the time of each record in seconds since the first one, and its value there in
    the unit of the axis (beats a minute, metres, seconds a kilometre or a hundred metres,
    kilometres an hour); None: a gap."""

    kind: str
    times: tuple[float, ...]
    values: tuple[float | None, ...]

    @property
    def duration(self) -> float:
        return self.times[-1] if self.times else 0


def _speeds(samples) -> list[float | None]:
    """The speed of each record (metres a second): as recorded, else worked out from the
    distance since the record before."""
    if any(s.speed is not None for s in samples):
        return [s.speed for s in samples]
    speeds, before = [], None
    for s in samples:
        speed = None
        if before is not None and s.distance is not None and s.time > before.time:
            speed = (s.distance - before.distance) / (s.time - before.time)
        speeds.append(speed)
        if s.distance is not None:
            before = s
    return speeds


def _pieces(times, values) -> list[tuple[int, int]]:
    """The pieces of a line as ranges of indexes [first, last): a value not given, or records
    more than GAP_S apart, ends one."""
    pieces, first = [], None
    for i, value in enumerate(values):
        if value is None:
            if first is not None:
                pieces.append((first, i))
                first = None
            continue
        if first is not None and times[i] - times[i - 1] > GAP_S:
            pieces.append((first, i))
            first = None
        if first is None:
            first = i
    if first is not None:
        pieces.append((first, len(values)))
    return pieces


def _window_means(times, values, half: float) -> list[float | None]:
    """The mean of the values given within `half` seconds of each record."""
    sums, counts = [0.0], [0]
    for v in values:
        sums.append(sums[-1] + (0.0 if v is None else v))
        counts.append(counts[-1] + (v is not None))
    out, low, high = [], 0, 0
    for t in times:
        while times[low] < t - half:
            low += 1
        while high < len(times) and times[high] <= t + half:
            high += 1
        count = counts[high] - counts[low]
        out.append((sums[high] - sums[low]) / count if count else None)
    return out


def _climb_rates(times, altitudes, half: float) -> list[float]:
    """How fast each record climbs or descends (metres a second, either way): the altitude
    `half` seconds before it against `half` after it."""
    known = [(t, a) for t, a in zip(times, altitudes, strict=True) if a is not None]
    at = [t for t, _a in known]
    out = []
    for t in times:
        first = bisect.bisect_left(at, t - half)
        last = bisect.bisect_right(at, t + half) - 1
        if last <= first or at[last] == at[first]:
            out.append(0.0)
            continue
        out.append(abs(known[last][1] - known[first][1]) / (at[last] - at[first]))
    return out


def _stops(times, speeds, altitudes) -> tuple[list[bool], list[bool]]:
    """Which records are stops (a gap in the pace), and which count in the pace around them:
    those moving forward, or climbing with some speed forward; never the zeros of a stop."""
    moving = sorted(v for v in speeds if v is not None and v >= STOP_SPEED)
    threshold = max(STOP_SPEED, (STOP_SHARE * moving[len(moving) // 2]) if moving else 0)
    means = _window_means(times, speeds, STOP_WINDOW_S / 2)
    climbs = [c >= CLIMB_RATE for c in _climb_rates(times, altitudes, CLIMB_WINDOW_S / 2)]
    stopped = [not climb and (mean is None or mean < threshold)
               for mean, climb in zip(means, climbs, strict=True)]
    counted = [v is not None and not stop and (v >= threshold or (climb and v >= CLIMB_RATE))
               for v, stop, climb in zip(speeds, stopped, climbs, strict=True)]
    return stopped, counted


def _smoothed(times, speeds, stopped, counted) -> list[float | None]:
    """The mean of the middle half of the speeds counted over SMOOTHING_S around each record
    (slowing down to a stop or a jump of the GPS does not pull it), within its piece of the
    line (a stop, a speed not given or a pause ends one); None for a stop."""
    out: list[float | None] = [None] * len(times)
    half = SMOOTHING_S / 2
    marks = [None if stop or v is None else v for v, stop in zip(speeds, stopped, strict=True)]
    for first, last in _pieces(times, marks):
        window: list[float] = []  # the speeds counted around the record, in order
        low = high = first
        for i in range(first, last):
            while high < last and times[high] <= times[i] + half:
                if counted[high]:
                    bisect.insort(window, speeds[high])
                high += 1
            while times[low] < times[i] - half:
                if counted[low]:
                    window.pop(bisect.bisect_left(window, speeds[low]))
                low += 1
            if window:
                quarter = len(window) // 4
                middle = window[quarter:len(window) - quarter]
                out[i] = sum(middle) / len(middle)
    return out


def chart_series(data) -> list[Series]:
    """The series of an activity (fit.ActivityData) its records give, in the order of the
    switch: heart rate, elevation, then for an activity with a distance its pace (smoothed;
    none at a stop) or its speed, by its sport."""
    samples = data.samples
    if not samples:
        return []
    origin = samples[0].time
    times = tuple(s.time - origin for s in samples)
    found = []
    for kind, values in (("heart rate", tuple(s.heart_rate for s in samples)),
                         ("elevation", tuple(s.altitude for s in samples))):
        if any(v is not None for v in values):
            found.append(Series(kind, times, values))
    if _has_distance(data):
        speeds = _speeds(samples)
        kind = speed_kind(data.sport)
        if kind == "speed":
            values = tuple(None if v is None else v * 3.6 for v in speeds)
        else:
            per = 1000 if kind == "pace" else 100
            stopped, counted = _stops(times, speeds, [s.altitude for s in samples])
            values = tuple(None if v is None or v <= 0 else per / v
                           for v in _smoothed(times, speeds, stopped, counted))
        if any(v is not None for v in values):
            found.append(Series(kind, times, values))
    return found


def _has_distance(data) -> bool:
    """Whether an activity (fit.ActivityData) went a distance: its sessions say one, or a
    record does."""
    return bool(data.distance or any((s.distance or 0) > 0 for s in data.samples))


def moving_time(data) -> float | None:
    """The time an activity (fit.ActivityData) with a distance was moving, as Garmin Connect
    counts it: the time from each record whose own speed is STOP_SPEED or more to the next
    one, but between records more than GAP_S apart (a pause). Not the chart's rule of a stop:
    no window, and a climb the device records at no speed is no moving (a device writes 0 when
    it finds you stopped, also on a slow steep climb). None without a distance or speeds."""
    samples = data.samples
    if len(samples) < 2 or not _has_distance(data):
        return None
    speeds = _speeds(samples)
    if all(v is None for v in speeds):
        return None
    times = [s.time for s in samples]
    return float(sum(after - at for at, after, speed
                     in zip(times[:-1], times[1:], speeds[:-1], strict=True)
                     if speed is not None and speed >= STOP_SPEED and after - at <= GAP_S))


def runs(series: Series) -> list[list[tuple[float, float]]]:
    """The pieces of a series' line, each a list of (time, value)."""
    return [list(zip(series.times[first:last], series.values[first:last], strict=True))
            for first, last in _pieces(series.times, series.values)]


def brought_down(run, start: float, end: float, columns: int) -> list[tuple[float, float]]:
    """At most two points of a piece for each of `columns` columns between the times `start`
    and `end`: the lowest and the highest of the column, in their order, so that no peak is
    lost."""
    if len(run) <= 2 * columns:
        return list(run)
    scale = columns / (end - start) if end > start else 0
    out, column, low, high = [], None, None, None
    for point in run:
        at = int((point[0] - start) * scale)
        if at != column:
            if column is not None:
                out.extend(_in_order(low, high))
            column, low, high = at, point, point
            continue
        if point[1] < low[1]:
            low = point
        if point[1] > high[1]:
            high = point
    out.extend(_in_order(low, high))
    return out


def _in_order(low, high) -> list:
    if low is high:
        return [low]
    return [low, high] if low[0] <= high[0] else [high, low]


@dataclass(frozen=True)
class Axis:
    """An axis from `low` to `high`, a value every `step` from `low`."""

    low: float
    high: float
    step: float

    @property
    def ticks(self) -> tuple[float, ...]:
        """The multiples of the step from `low` to `high`."""
        first = math.ceil(self.low / self.step - 1e-9)
        last = math.floor(self.high / self.step + 1e-9)
        return tuple(_clean(i * self.step) for i in range(first, last + 1))


def _clean(value: float) -> float:
    return round(value, 9)


def _step(span: float, count: int, steps=None, whole: bool = False) -> float:
    """The round step that gives at most about `count` steps over `span`."""
    raw = span / max(count, 1)
    if steps is not None:
        return next((s for s in steps if s >= raw), steps[-1] * math.ceil(raw / steps[-1]))
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for nice in NICE:
        step = _clean(nice * magnitude)
        if (whole and step != int(step)) or step < raw - 1e-12:
            continue
        return step
    return 10 * magnitude


def value_axis(series: Series, count: int) -> Axis:
    """The axis of a series' values, round values around all of them (a pace: around most of
    them, with a margin, so that typical pace takes most of the height), about `count`
    steps."""
    values = [v for v in series.values if v is not None]
    low, high = min(values), max(values)
    if series.kind in PACES:
        ordered = sorted(values)
        low, high = (ordered[max(0, math.ceil(share * len(ordered)) - 1)]
                     for share in AXIS_SHARES)
        margin = (high - low) * AXIS_MARGIN
        low, high = low - margin, high + margin
    least = LEAST_SPAN[series.kind]
    if high - low < least:
        middle = (low + high) / 2
        low, high = middle - least / 2, middle + least / 2
    step = _step(high - low, count, PACE_STEPS if series.kind in PACES else None,
                 whole=series.kind in WHOLE)
    first = math.floor(low / step + 1e-9) * step
    if series.kind in PACES and first <= 0 < low:
        # A pace of 0:00 means nothing: the axis begins at a smaller round pace, its values
        # still a step apart.
        first = next((math.floor(low / smaller) * smaller for smaller in reversed(PACE_STEPS)
                      if smaller < step and math.floor(low / smaller) > 0), low)
    last = math.ceil(high / step - 1e-9) * step
    return Axis(_clean(first), _clean(last), step)


def time_axis(duration: float, count: int) -> Axis:
    """The axis of the time since the first record: from it to the last, a round step."""
    return Axis(0, duration, _step(max(duration, 1), count, TIME_STEPS))


def faster_on_top(kind: str) -> bool:
    return kind in PACES


def nearest(times, at: float) -> int | None:
    """The record of `times` nearest to the time `at` (its index); None in a gap or a pause
    (no record within half of GAP_S). The chart's cursor and its marker on the map."""
    i = bisect.bisect_left(times, at)
    found = min((j for j in (i - 1, i) if 0 <= j < len(times)), key=lambda j: abs(times[j] - at))
    return None if abs(times[found] - at) > GAP_S / 2 else found


def value_at(series: Series, at: float) -> tuple[float, float | None]:
    """Where the cursor goes for the time `at` and the value there: the nearest record, or
    no value in a gap or a pause."""
    at = min(max(at, 0), series.duration)
    i = nearest(series.times, at)
    return (at, None) if i is None else (series.times[i], series.values[i])


def cursor_after(series: Series, cursor: float | None, key: str) -> float:
    """The cursor after a key ("left", "right", "home", "end"): to the next record at least a
    hundredth of the activity away (never stuck at a pause); Home and End to the first and the
    last; without a cursor yet, Right starts at the first and Left at the last."""
    times = series.times
    if key == "home" or (cursor is None and key == "right"):
        return times[0]
    if key == "end" or cursor is None:
        return times[-1]
    step = series.duration / KEY_STEP
    if key == "right":
        return times[min(bisect.bisect_left(times, cursor + step), len(times) - 1)]
    return times[max(bisect.bisect_right(times, cursor - step) - 1, 0)]


# --- the drawing -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Plot:
    """Where the series is drawn in the widget (pixels) and its axes."""

    left: float
    top: float
    right: float
    bottom: float
    times: Axis
    values: Axis
    faster_on_top: bool

    def x(self, at: float) -> float:
        span = max(self.times.high - self.times.low, 1e-9)
        return self.left + (at - self.times.low) / span * (self.right - self.left)

    def time(self, x: float) -> float:
        span = max(self.times.high - self.times.low, 1e-9)
        return self.times.low + (x - self.left) / max(self.right - self.left, 1) * span

    def y(self, value: float) -> float:
        share = (value - self.values.low) / (self.values.high - self.values.low)
        if self.faster_on_top:
            return self.top + share * (self.bottom - self.top)
        return self.bottom - share * (self.bottom - self.top)


def _layout(area, text: str):
    layout = area.create_pango_layout(text)
    attributes = Pango.AttrList()
    attributes.insert(Pango.attr_scale_new(0.85))
    layout.set_attributes(attributes)
    return layout


def _plot(area, width: int, height: int) -> Plot:
    """The place of the plot in a widget of this size: room for the unit on top, the values
    on the left and the times below; as many round values as fit."""
    series = area.series
    text_height = _layout(area, "0").get_pixel_size()[1]
    top, bottom = text_height + 8, height - text_height - 6
    values = value_axis(series, max(2, int((bottom - top) // 40)))
    widest = max(_layout(area, chart_tick(series.kind, v, values.step)).get_pixel_size()[0]
                 for v in values.ticks)
    left = widest + 8
    last = _layout(area, activity_clock(series.duration)).get_pixel_size()[0]
    right = width - max(8, last / 2 + 2)
    times = time_axis(series.duration, max(2, int((right - left) // (last + 24))))
    return Plot(left, top, right, bottom, times, values, faster_on_top(series.kind))


def _text(area, cr, text: str, x: float, y: float, align: float, color) -> None:
    """`text` at `y` (its top), `align` 0 left of `x`… 1 right of it."""
    layout = _layout(area, text)
    width = layout.get_pixel_size()[0]
    cr.set_source_rgba(color.red, color.green, color.blue, 0.7)
    cr.move_to(x - width * align, y)
    PangoCairo.show_layout(cr, layout)


def _draw(area, cr, width: int, height: int) -> None:
    """The draw function of the chart (Gtk.DrawingArea): the band of a lap chosen, grid, values,
    times, the line, the cursor and, with the keyboard's focus, a frame."""
    series, kind = area.series, area.series.kind
    plot = _plot(area, width, height)
    fg, accent = area.get_color(), accent_colour()
    text_height = _layout(area, "0").get_pixel_size()[1]
    if area.zones is not None and kind == "heart rate":
        _draw_zones(cr, plot, area.zones)
    if area.lap is not None:  # the lap chosen under the page: a band behind the line
        start, end = (min(max(plot.x(at), plot.left), plot.right) for at in area.lap)
        cr.set_source_rgba(accent.red, accent.green, accent.blue, LAP_SHADE)
        cr.rectangle(start, plot.top, end - start, plot.bottom - plot.top)
        cr.fill()
    cr.set_line_width(1)
    for value in plot.values.ticks:
        y = round(plot.y(value)) + 0.5
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
        cr.move_to(plot.left, y)
        cr.line_to(plot.right, y)
        cr.stroke()
        _text(area, cr, chart_tick(kind, value, plot.values.step), plot.left - 6,
              y - text_height / 2, 1, fg)
    _text(area, cr, chart_unit(kind), 0, 0, 0, fg)
    for at in plot.times.ticks:
        _text(area, cr, activity_clock(at), plot.x(at), plot.bottom + 4, 0.5, fg)
    cr.save()
    cr.rectangle(plot.left, plot.top - 2, plot.right - plot.left, plot.bottom - plot.top + 4)
    cr.clip()
    cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
    cr.set_line_width(2)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for run in _drawn_runs(area, plot):
        if len(run) == 1:
            cr.arc(plot.x(run[0][0]), _within(plot, run[0][1]), 1.5, 0, 2 * math.pi)
            cr.fill()
            continue
        cr.move_to(plot.x(run[0][0]), _within(plot, run[0][1]))
        for at, value in run[1:]:
            cr.line_to(plot.x(at), _within(plot, value))
        cr.stroke()
    cr.restore()
    if area.cursor is not None:
        at, value = value_at(series, area.cursor)
        x = round(plot.x(at)) + 0.5
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.5)
        cr.set_line_width(1)
        cr.move_to(x, plot.top)
        cr.line_to(x, plot.bottom)
        cr.stroke()
        if value is not None:
            cr.arc(x, _within(plot, value), 4.5, 0, 2 * math.pi)
            cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
            cr.fill_preserve()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 1)  # seen on the line too
            cr.set_line_width(1.5)
            cr.stroke()
    if area.has_visible_focus():
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.5)
        cr.set_line_width(2)
        cr.rectangle(1, 1, width - 2, height - 2)
        cr.stroke()


def _zone_bands(bounds, low: float, high: float) -> list[tuple[int, float, float]]:
    """The bands of zones 1-5 within the heart rates from `low` to `high`, each (zone, from,
    to), from the zones' bounds (fit.HeartRateZones: each the first beat of the zone above it,
    so a band runs from its zone's bound to the next); zone 5 up to the top of the chart, as
    the time above the top bound is its. None below zone 1: it is no zone."""
    tops = list(bounds[1:-1]) + [max(high, bounds[-1])]
    bands = []
    for zone, (bottom, top) in enumerate(zip(bounds[:-1], tops, strict=True), start=1):
        bottom, top = max(bottom, low), min(top, high)
        if top > bottom:
            bands.append((zone, bottom, top))
    return bands


def _draw_zones(cr, plot: Plot, bounds) -> None:
    for zone, bottom, top in _zone_bands(bounds, plot.values.low, plot.values.high):
        color = rgba(ZONE_COLOURS[zone])
        cr.set_source_rgba(color.red, color.green, color.blue, ZONE_SHADE)
        cr.rectangle(plot.left, plot.y(top), plot.right - plot.left, plot.y(bottom) - plot.y(top))
        cr.fill()


def _within(plot: Plot, value: float) -> float:
    """The height of a value, one beyond the axis (a pace slower than most) at its edge: drawn
    there, not lost."""
    return min(max(plot.y(value), plot.top), plot.bottom)


def _drawn_runs(area, plot: Plot) -> list:
    """The pieces of the line brought down to the plot's width, kept until the series or the
    width changes."""
    key = (area.series, plot.left, plot.right)
    if area.drawn is None or area.drawn[0] != key:
        columns = max(1, int(plot.right - plot.left))
        area.drawn = (key, [brought_down(run, plot.times.low, plot.times.high, columns)
                            for run in runs(area.series)])
    return area.drawn[1]


# --- the widgets -----------------------------------------------------------------------------


def _drag_begin(gesture, x, _y) -> None:
    area = gesture.get_widget()
    area.grab_focus()
    area.drag_from = x
    _place_at(area, x)


def _drag_update(gesture, dx, _dy) -> None:
    area = gesture.get_widget()
    _place_at(area, area.drag_from + dx)


def _place_at(area, x: float) -> None:
    area.place(_plot(area, area.get_width(), area.get_height()).time(x))


KEYS = {Gdk.KEY_Left: "left", Gdk.KEY_KP_Left: "left", Gdk.KEY_Right: "right",
        Gdk.KEY_KP_Right: "right", Gdk.KEY_Home: "home", Gdk.KEY_KP_Home: "home",
        Gdk.KEY_End: "end", Gdk.KEY_KP_End: "end"}


def _key_pressed(controller, keyval, _keycode, _state) -> bool:
    key = KEYS.get(keyval)
    if key is None:
        return False
    area = controller.get_widget()
    area.place(cursor_after(area.series, area.cursor, key))
    return True


def _redraw(controller) -> None:
    controller.get_widget().queue_draw()


class ChartArea(Gtk.DrawingArea):
    """The drawing of one series and its cursor; a click, a drag and the keys move the
    cursor. Its handlers reach it through their controller."""

    __gtype_name__ = "OpenExpressChartArea"

    def __init__(self, series: Series):
        super().__init__(content_height=CHART_HEIGHT, hexpand=True, focusable=True)
        self.series = series
        self.cursor: float | None = None
        self.lap: tuple[float, float] | None = None  # the lap chosen, on the chart's time
        self.zones = None  # the bounds of the heart rate zones, their bands
        self.drag_from = 0.0
        self.drawn = None  # the line brought down to the width last drawn
        self.set_draw_func(_draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", _drag_begin)
        drag.connect("drag-update", _drag_update)
        self.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", _key_pressed)
        self.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", _redraw)
        focus.connect("leave", _redraw)
        self.add_controller(focus)
        self.show_series(series)

    def show_series(self, series: Series) -> None:
        """Another series, the cursor at the same time."""
        self.series = series
        if self.cursor is not None:
            self.cursor = value_at(series, self.cursor)[0]
        self.update_property([Gtk.AccessibleProperty.LABEL], [chart_accessible_name(series.kind)])
        self.queue_draw()

    def place(self, at: float) -> None:
        """The cursor at the time `at` (at its nearest record), its value said beside the
        switch."""
        self.cursor = value_at(self.series, at)[0]
        self.queue_draw()
        text = self.cursor_text()
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION], [text])
        chart = self.get_ancestor(ActivityChart)
        if chart is not None:
            chart.value_label.set_label(text)
            chart.emit("cursor-moved", self.cursor)

    def cursor_text(self) -> str:
        if self.cursor is None:
            return ""
        at, value = value_at(self.series, self.cursor)
        return chart_cursor_text(at, self.series.kind, value)


def _toggled(button) -> None:
    if not button.get_active():
        return
    chart = button.get_ancestor(ActivityChart)
    if chart is not None:
        chart.show(button.kind)


class ActivityChart(Gtk.Box):
    """The chart with its switch (or the name of its one series) and, beside it, the value at
    its cursor, said to the page too (cursor-moved), as is the series it shows (series-shown).
    Its handlers reach it through the widget of their signal."""

    __gtype_name__ = "OpenExpressActivityChart"
    # The cursor placed, at this time since the first record (the map marks it).
    # Another series shown, of this kind (the map colours the track by it).
    __gsignals__: ClassVar[dict] = {
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "series-shown": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, series: list[Series], zones=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.series = {s.kind: s for s in series}
        # The value beside the switch, under it in a narrow window.
        top = plain_flow(column_spacing=12, row_spacing=4)
        if len(series) > 1:
            switch = Gtk.Box(valign=Gtk.Align.CENTER)
            switch.add_css_class("linked")
            first = None
            for s in series:
                button = Gtk.ToggleButton(label=chart_series_name(s.kind), active=first is None)
                button.kind = s.kind
                if first is None:
                    first = button
                else:
                    button.set_group(first)
                button.connect("toggled", _toggled)
                switch.append(button)
            first_widget = switch
        else:
            first_widget = Gtk.Label(label=chart_series_name(series[0].kind), xalign=0)
            first_widget.add_css_class("heading")
        self.value_label = Gtk.Label(xalign=1, wrap=True, justify=Gtk.Justification.RIGHT)
        self.value_label.add_css_class("numeric")
        for widget, expand in ((first_widget, False), (self.value_label, True)):
            top.append(Gtk.FlowBoxChild(child=widget, focusable=False, hexpand=expand))
        self.append(top)
        self.area = ChartArea(series[0])
        self.area.zones = zones
        self.append(self.area)

    def show(self, kind: str) -> None:
        self.area.show_series(self.series[kind])
        self.value_label.set_label(self.area.cursor_text())
        self.emit("series-shown", kind)

    def show_lap(self, span: tuple[float, float] | None) -> None:
        """The lap chosen (its start and end on the chart's time) as a band behind the line;
        None: none."""
        self.area.lap = span
        self.area.queue_draw()
