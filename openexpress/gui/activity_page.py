"""The page of one activity, opened from its row on the Activities page: its sport and start in
its header bar with the back arrow and the GPX export (a dot of the sport's colour before them,
gui/sports.py, but in a narrow window), the tiles of what its sessions say (the words in
gui/words.py, an icon of their kind in the same colour), the map of its track
(gui/activity_map.py) with the place of the chart's cursor, the chart of its records
(gui/activity_chart.py), its laps or kilometres (gui/activity_laps.py; one chosen shown on the
chart and the map), its heart rate zones (gui/activity_zones.py), training effect
(gui/activity_effect.py) and details (gui/activity_details.py, closed), and what a file that
could not be read, one still being written and one without a summary say. Its file is read
again each time it opens, in a task (fit.activity_data), from the device or from a copy in a
backup; nothing is kept and nothing is written. The page is pushed onto the window's navigation
above the device view and goes with the pages of that view (the device gone, or drawn anew).
Has: no attribute of the window; the page keeps what it needs in its closures."""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.activities import Source  # noqa: E402
from openexpress.errors import UserFacingError  # noqa: E402
from openexpress.gui import activity_map  # noqa: E402
from openexpress.gui.activities_page import PAGE_ICON, export_button  # noqa: E402
from openexpress.gui.activity_chart import ActivityChart, chart_series, moving_time  # noqa: E402
from openexpress.gui.activity_details import DetailsList  # noqa: E402
from openexpress.gui.activity_effect import EffectList, has_effect  # noqa: E402
from openexpress.gui.activity_laps import LapList, laps_of  # noqa: E402
from openexpress.gui.activity_zones import ZoneList  # noqa: E402
from openexpress.gui.sports import sport_group  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    activity_details,
    activity_file_error,
    activity_not_read_title,
    activity_start,
    activity_tile_kinds,
    activity_title,
    damaged_file_text,
    no_chart_text,
    no_summary_title,
    reading_activity_text,
    still_being_written_title,
    tile_name,
)
from openexpress.i18n import _  # noqa: E402
from openexpress.paths import APP_ID  # noqa: E402

log = logging.getLogger(__name__)

TAG = "activity"
CONTENT_WIDTH = 1000  # its content in one column, at most this wide
NARROW = "max-width: 450sp"  # a window too narrow for the dot of the sport beside the title
NARROWEST = (360, 200)  # the page's own least size (its breakpoints need one)
# The icons of the tiles, by their kind (the program's own, in data/): two points for the
# distance, a stopwatch for the time, a speedometer, a heart, a flame and the peaks.
_SPEED = f"{APP_ID}-activity-speed-symbolic"
_HEART = f"{APP_ID}-activity-heart-rate-symbolic"
TILE_ICONS = {"distance": f"{APP_ID}-activity-distance-symbolic",
              "time": f"{APP_ID}-activity-time-symbolic",
              "pace": _SPEED, "swim pace": _SPEED, "speed": _SPEED,
              "average heart rate": _HEART, "maximum heart rate": _HEART,
              "calories": f"{APP_ID}-activity-calories-symbolic",
              "ascent": f"{APP_ID}-activity-elevation-symbolic"}


class ReadFailed(UserFacingError):
    """The file of an activity that could not be read again: a copy in a backup that could
    not be read, or no FIT file."""


def _data(found, activity):
    """In the task's thread: the activity's file read again (activities.DeviceActivities) and
    what it holds."""
    from openexpress import fit

    data = found.read_file(activity)
    if data is None:
        raise ReadFailed(activity_file_error())
    try:
        return fit.activity_data(data)
    except fit.FitError as e:
        log.info("activity page: no FIT file (%s)", type(e).__name__)
        raise ReadFailed(activity_file_error()) from e


def _prepared(data):
    """In the task's thread: what the file holds, the series of its chart, its track and its
    moving time."""
    return data, chart_series(data), activity_map.track_of(data), moving_time(data)


def _beside(widget, kinds):
    """The widgets of `kinds` beside `widget` on the page (its parent's children)."""
    child = widget.get_parent().get_first_child()
    while child is not None:
        if isinstance(child, kinds):
            yield child
        child = child.get_next_sibling()


def _lap_chosen(laps, chosen: bool, start: float, end: float) -> None:
    """A lap chosen under the chart, or none: the chart and the map beside it show it."""
    span = (start, end) if chosen else None
    for shown in _beside(laps, (ActivityChart, activity_map.ActivityMap)):
        shown.show_lap(span)


def _series_shown(chart, kind: str) -> None:
    """Another series on the chart: the map beside it colours the track by it."""
    for shown in _beside(chart, activity_map.ActivityMap):
        shown.colour_by(chart.series[kind])


def _cursor_moved(chart, at: float) -> None:
    """The chart's cursor placed: the map beside it marks the place."""
    for shown in _beside(chart, activity_map.ActivityMap):
        shown.show_cursor(at)


def leave(win) -> None:
    """Back to the device view when an activity's page is shown: the pages of the device are
    drawn anew, or Refresh or Sync is asked from the keyboard (the bar of their task is in the
    device view)."""
    page = activity_map.visible_page(win)
    if page is not None and page.get_tag() == TAG:
        log.info("activity page: closed with the device view")
        win.navigation.pop_to_page(win.device_view)


def open_activity(win, read) -> None:
    """The page of an activity (activities.Read) pushed onto the window's navigation, its file
    read in a task; while another task runs, a toast only."""
    if win.busy:
        win.toast(_("Another operation is in progress"))
        return
    found = win.activities
    view = ActivityView(win, read)
    win.navigation.push(view.page)

    def done(shown) -> None:
        if win.navigation.get_visible_page() is not view.page:
            log.info("activity page: gone before its file was read")
            return
        view.show(*shown)

    def failed(_error) -> bool:
        # Said on the page, no error dialog (a stall: the banner, and the page goes with the
        # device); the log has why.
        view.say(*activity_not_read_title())
        return True

    win.run_task(reading_activity_text(), lambda: _prepared(_data(found, read.activity)),
                 done, failed, device=read.activity.source is Source.DEVICE, in_page=True)


def _tile(kind: str, value: str, group: str) -> Gtk.Widget:
    """A tile: its icon in the colour of the sport's group (for the eye only), the value
    large, its name under it."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=12,
                  margin_bottom=12, margin_start=12, margin_end=12)
    icon = Gtk.Image(icon_name=TILE_ICONS[kind], pixel_size=20,
                     accessible_role=Gtk.AccessibleRole.PRESENTATION)
    icon.add_css_class(f"oe-sport-{group}")
    box.append(icon)
    name = tile_name(kind)
    shown = Gtk.Label(label=value, wrap=True, justify=Gtk.Justification.CENTER)
    shown.add_css_class("title-3")
    named = Gtk.Label(label=name, wrap=True, justify=Gtk.Justification.CENTER)
    named.add_css_class("caption")
    named.add_css_class("dim-label")
    box.append(shown)
    box.append(named)
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.add_css_class("card")
    card.append(box)
    return card


class ActivityView:
    """The widgets of one activity's page: its header bar, and in turn the spinner while its
    file is read, its content, or a message."""

    def __init__(self, win, read):
        title = activity_title(read)
        heading = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        if read.summary is not None:  # the dot of the sport's colour, for the eye only
            dot = Gtk.Box(valign=Gtk.Align.CENTER,
                          accessible_role=Gtk.AccessibleRole.PRESENTATION)
            dot.add_css_class("oe-sport-dot")
            dot.add_css_class(f"oe-sport-{sport_group(read.summary.sport)}")
            heading.append(dot)
        else:
            dot = None
        heading.append(Adw.WindowTitle(title=title, subtitle=activity_start(read)))
        header = Adw.HeaderBar(title_widget=heading)
        button = export_button(win, read)
        if button is not None:
            header.pack_end(button)
        self.loading = Adw.StatusPage(title=reading_activity_text())
        if hasattr(Adw, "SpinnerPaintable"):
            self.loading.set_paintable(Adw.SpinnerPaintable.new(self.loading))
        self.message = Adw.StatusPage()
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24,
                               margin_bottom=24, margin_start=12, margin_end=12)
        scrolled = Gtk.ScrolledWindow(
            child=Adw.Clamp(maximum_size=CONTENT_WIDTH, child=self.content),
            hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for name, widget in (("loading", self.loading), ("content", scrolled),
                             ("message", self.message)):
            self.stack.add_named(widget, name)
        self.stack.set_visible_child_name("loading")
        toolbar = Adw.ToolbarView(content=self.stack)
        toolbar.add_top_bar(header)
        sized = Adw.BreakpointBin(child=toolbar, width_request=NARROWEST[0],
                                  height_request=NARROWEST[1])
        if dot is not None:
            narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW))
            narrow.add_setter(dot, "visible", False)
            sized.add_breakpoint(narrow)
        self.page = Adw.NavigationPage(title=title, tag=TAG, child=sized)

    def say(self, text: str, description: str = "",
            icon: str = "dialog-warning-symbolic") -> None:
        self.message.set_icon_name(icon)
        self.message.set_title(text)
        self.message.set_description(description or None)
        self.stack.set_visible_child_name("message")

    def show(self, data, series, track=None, moving=None) -> None:
        """What the file holds (fit.ActivityData): the tiles, a damaged file said under them,
        the map of `track` (activity_map.Track; none without positions, a line without
        libshumate), the chart of `series` (activity_chart.Series), or a line in its place
        when there is none, then its laps or kilometres (none for a workout), zones, training
        effect and details (`moving`: the moving time worked out from its records); of a file
        still being written and of one without a summary, only that."""
        from openexpress import fit

        if data.state is fit.FileState.STILL_BEING_WRITTEN:
            self.say(*still_being_written_title())
            return
        tiles = activity_tile_kinds(data)
        if not tiles:
            self.say(*no_summary_title(), PAGE_ICON)
            return
        # All of them in one line on a wide window, sharing its width; two by two on a narrow one.
        flow = Gtk.FlowBox(homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
                           min_children_per_line=2, max_children_per_line=max(2, len(tiles)),
                           column_spacing=12, row_spacing=12)
        group = sport_group(data.sport)
        for kind, value in tiles:
            flow.append(Gtk.FlowBoxChild(child=_tile(kind, value, group), focusable=False))
        self.content.append(flow)
        if data.state is fit.FileState.DAMAGED:
            damaged = Gtk.Label(label=damaged_file_text(), wrap=True, xalign=0)
            damaged.add_css_class("warning")
            self.content.append(damaged)
        if track is not None and activity_map.available():
            shown = activity_map.ActivityMap(track)
            shown.set_margin_top(12)
            self.content.append(shown)
        elif track is not None:
            line = Gtk.Label(label=_("The map needs the libshumate library, which is not "
                                     "installed"), wrap=True, xalign=0, margin_top=12)
            line.add_css_class("dim-label")
            self.content.append(line)
        if series:
            chart = ActivityChart(series, data.zones.bounds if data.zones else None)
            chart.set_margin_top(12)
            chart.connect("cursor-moved", _cursor_moved)
            chart.connect("series-shown", _series_shown)
            self.content.append(chart)
            _series_shown(chart, series[0].kind)  # the track coloured by the first one
        else:
            line = Gtk.Label(label=no_chart_text(), wrap=True, xalign=0, margin_top=12)
            line.add_css_class("dim-label")
            self.content.append(line)
        laps, kilometres = laps_of(data)
        if laps:
            listed = LapList(laps, kilometres, data.sport,
                             data.samples[0].time if data.samples else data.start or 0)
            listed.set_margin_top(12)
            listed.connect("lap-chosen", _lap_chosen)
            self.content.append(listed)
        if data.zones is not None:
            zones = ZoneList(data.zones)
            zones.set_margin_top(12)
            self.content.append(zones)
        if has_effect(data):
            effect = EffectList(data)
            effect.set_margin_top(12)
            self.content.append(effect)
        details = activity_details(data, moving)
        if details:
            listed = DetailsList(details)
            listed.set_margin_top(12)
            self.content.append(listed)
        self.stack.set_visible_child_name("content")
        log.info("activity page: %d tiles, %s, chart of %s, %s, %d %s", len(tiles),
                 data.state.value, ", ".join(s.kind for s in series) or "nothing",
                 "a map" if track is not None else "no map", len(laps),
                 "kilometres" if kilometres else "laps")
