"""The page of one activity, opened from its row on the Activities page or from its tile: the
back arrow and the GPX export in its header bar; on top (gui/activity_overview.py) its heading
(its kind, when it started and on which device, a dot of its sport's colour, gui/sports.py, but
in a narrow window), its numbers, the map of its track (gui/activity_map.py) with the place of
the chart's cursor, its elevation and its heart rate with the share and the rows of its zones
(gui/activity_zones.py); under them the chart of its records (gui/activity_chart.py), its laps
or kilometres (gui/activity_laps.py; one chosen shown on the chart and the map), training
effect (gui/activity_effect.py) and details (gui/activity_details.py, closed); and what a file
that could not be read, one still being written and one without a summary say. The top is in
the units of its device, whether the activity journal is kept or not. Its file is read again
each time it opens, in a task (fit.activity_data), from the device or from a copy in a backup;
nothing is kept and nothing is written. An activity whose file is gone from the device and its
backups opens, while the journal is kept, from its line of the journal (one_activity.py): the
top alone, its shape on a grid under its numbers, no chart, laps, details nor GPX, one line
saying where it comes from. The page is pushed onto the window's navigation above the device
view and goes with the pages of that view (the device gone, or drawn anew), the journal's page
also with the journal turned off.
Has: no attribute of the window; the page keeps what it needs in its closures."""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress import one_activity  # noqa: E402
from openexpress.activities import Source  # noqa: E402
from openexpress.errors import UserFacingError  # noqa: E402
from openexpress.gui import activity_map, activity_overview  # noqa: E402
from openexpress.gui.activities_page import PAGE_ICON, export_button  # noqa: E402
from openexpress.gui.activities_since import ALL_TAG  # noqa: E402
from openexpress.gui.activity_chart import ActivityChart, chart_series, moving_time  # noqa: E402
from openexpress.gui.activity_details import DetailsList  # noqa: E402
from openexpress.gui.activity_effect import EffectList, has_effect  # noqa: E402
from openexpress.gui.activity_laps import LapList, laps_of  # noqa: E402
from openexpress.gui.sports import sport_group  # noqa: E402
from openexpress.gui.words import (  # noqa: E402
    activity_details,
    activity_file_error,
    activity_not_read_title,
    activity_tile_kinds,
    activity_title,
    damaged_file_text,
    from_journal_text,
    no_chart_text,
    no_summary_title,
    overview_numbers,
    overview_title,
    reading_activity_text,
    still_being_written_title,
)
from openexpress.i18n import _  # noqa: E402

log = logging.getLogger(__name__)

TAG = "activity"
JOURNAL_TAG = "activity-journal"  # the page of an activity from the activity journal alone
CONTENT_WIDTH = 1000  # its content in one column, at most this wide
NARROW = "max-width: 450sp"  # a window too narrow for the dot of the sport beside the title
NARROWEST = (360, 200)  # the page's own least size (its breakpoints need one)


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
    """In the task's thread: what the file holds, the series of its chart, its track, its
    moving time and what the top of the page shows of it (one_activity.Overview)."""
    return (data, chart_series(data), activity_map.track_of(data), moving_time(data),
            one_activity.of_file(data))


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
    """Back to the device view when an activity's page is shown, or the list Show All pushed
    (gui/activities_since.py): the pages of the device are drawn anew, or Refresh or Sync is
    asked from the keyboard (the bar of their task is in the device view)."""
    page = activity_map.visible_page(win)
    if page is not None and page.get_tag() in (TAG, JOURNAL_TAG, ALL_TAG):
        log.info("activity page: closed with the device view")
        win.navigation.pop_to_page(win.device_view)


def leave_journal(win) -> None:
    """Back to the device view when the page of an activity from the journal alone is shown:
    the journal is no longer kept."""
    page = activity_map.visible_page(win)
    if page is not None and page.get_tag() == JOURNAL_TAG:
        log.info("activity page: closed, the activity journal is not kept")
        win.navigation.pop_to_page(win.device_view)


def _device(win) -> str:
    """The name of the open device, for the line under the title."""
    if win.session is None:
        return ""
    from openexpress.gui.device_page import device_name  # it imports the pages of the device

    return device_name(win.session)


def open_activity(win, read) -> None:
    """The page of an activity (activities.Read) pushed onto the window's navigation, its file
    read in a task; while another task runs, a toast only."""
    if win.busy:
        win.toast(_("Another operation is in progress"))
        return
    found = win.activities
    view = ActivityView(activity_title(read), export_button(win, read), device=_device(win))
    win.navigation.push(view.page)
    log.info("activity page: opened, its file %s", "on the device"
             if read.activity.source is Source.DEVICE else "in a backup")

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


def open_from_journal(win, activity: dict) -> None:
    """The page of an activity whose file is gone from the device and its backups, from its
    line of the activity journal (activity_journal.py), pushed onto the window's navigation;
    nothing is read."""
    shown = one_activity.of_journal(activity)
    view = ActivityView(overview_title(shown), None, device=_device(win), journal=True)
    win.navigation.push(view.page)
    view.show_journal(shown)


class ActivityView:
    """The widgets of one activity's page: its header bar (the back arrow, `export`, the page's
    `title` until the page says it), and in turn the spinner while its file is read, its
    content, or a message. `journal`: the page of an activity from the journal alone."""

    def __init__(self, title: str, export: Gtk.Widget | None, device: str = "",
                 journal: bool = False):
        self.device = device
        self.header = Adw.HeaderBar()
        if export is not None:
            self.header.pack_end(export)
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
        toolbar.add_top_bar(self.header)
        self.sized = Adw.BreakpointBin(child=toolbar, width_request=NARROWEST[0],
                                       height_request=NARROWEST[1])
        self.page = Adw.NavigationPage(title=title, tag=JOURNAL_TAG if journal else TAG,
                                       child=self.sized)

    def say(self, text: str, description: str = "",
            icon: str = "dialog-warning-symbolic") -> None:
        self.message.set_icon_name(icon)
        self.message.set_title(text)
        self.message.set_description(description or None)
        self.stack.set_visible_child_name("message")

    def _top(self, shown, group: str | None) -> None:
        """The heading, its dot hidden in a narrow window; the header bar's title no longer;
        the page named as its heading (its kind with its sub sport)."""
        self.page.set_title(overview_title(shown))
        heading = activity_overview.Heading(shown, self.device, group)
        if heading.dot is not None:
            narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW))
            narrow.add_setter(heading.dot, "visible", False)
            self.sized.add_breakpoint(narrow)
        self.header.set_show_title(False)
        self.content.append(heading)

    def _numbers(self, shown) -> int:
        """The card of numbers (none without a number): how many numbers."""
        numbers = overview_numbers(shown)
        if numbers:
            card = activity_overview.NumbersCard(numbers)
            card.set_margin_top(12)
            self.content.append(card)
        return len(numbers)

    def _elevation_heart_rate(self, shown, zones=None) -> tuple[bool, bool]:
        """The elevation and the heart rate (with the rows of `zones`): whether there is
        each."""
        found = []
        for section in (activity_overview.elevation(shown),
                        activity_overview.heart_rate(shown, zones)):
            if section is not None:
                section.set_margin_top(12)
                self.content.append(section)
            found.append(section is not None)
        return found[0], found[1]

    def show(self, data, series, track=None, moving=None, shown=None) -> None:
        """What the file holds (fit.ActivityData): on top its heading, a damaged file said
        under it, its numbers, the map of `track` (activity_map.Track; none without positions,
        a line without libshumate), its elevation and heart rate (`shown`,
        one_activity.Overview); under them the chart of `series` (activity_chart.Series), or a
        line in its place when there is none, then its laps or kilometres (none for a
        workout), training effect and details (`moving`: the moving time worked out from its
        records); of a file still being written and of one without a summary, only that."""
        from openexpress import fit

        if data.state is fit.FileState.STILL_BEING_WRITTEN:
            self.say(*still_being_written_title())
            return
        if not activity_tile_kinds(data):  # its sessions say nothing
            self.say(*no_summary_title(), PAGE_ICON)
            return
        shown = shown if shown is not None else one_activity.of_file(data)
        self._top(shown, sport_group(data.sport))
        if data.state is fit.FileState.DAMAGED:
            damaged = Gtk.Label(label=damaged_file_text(), wrap=True, xalign=0)
            damaged.add_css_class("warning")
            self.content.append(damaged)
        numbers = self._numbers(shown)
        if track is not None and activity_map.available():
            mapped = activity_map.ActivityMap(track)
            mapped.set_margin_top(12)
            self.content.append(mapped)
        elif track is not None:
            line = Gtk.Label(label=_("The map needs the libshumate library, which is not "
                                     "installed"), wrap=True, xalign=0, margin_top=12)
            line.add_css_class("dim-label")
            self.content.append(line)
        elevation, heart = self._elevation_heart_rate(shown, data.zones)
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
        log.info("activity page: from its file, %d numbers, %s, %s, %s, %s, chart of %s, %d %s",
                 numbers, data.state.value, "a map" if track is not None else "no map",
                 "an elevation" if elevation else "no elevation",
                 "a heart rate" if heart else "no heart rate",
                 ", ".join(s.kind for s in series) or "nothing", len(laps),
                 "kilometres" if kilometres else "laps")

    def show_journal(self, shown) -> None:
        """An activity from its line of the activity journal (one_activity.Overview): its
        heading, where it comes from, its numbers, the shape of its track, its elevation and
        heart rate."""
        self._top(shown, sport_group(shown.sport))
        said = Gtk.Label(label=from_journal_text(), wrap=True, xalign=0)
        said.add_css_class("dim-label")
        self.content.append(said)
        numbers = self._numbers(shown)
        if shown.shape:
            card = activity_overview.ShapeCard(shown.shape, sport_group(shown.sport))
            card.set_margin_top(12)
            self.content.append(card)
        elevation, heart = self._elevation_heart_rate(shown)
        self.stack.set_visible_child_name("content")
        log.info("activity page: from the activity journal, %d numbers, %s, %s, %s", numbers,
                 "a shape" if shown.shape else "no shape",
                 "an elevation" if elevation else "no elevation",
                 "a heart rate" if heart else "no heart rate")
