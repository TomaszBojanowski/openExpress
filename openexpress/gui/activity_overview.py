"""The top of an activity's page (one_activity.Overview), over its chart, laps, training effect and
details: its heading (its kind with its sub sport, when it started and on which device, a dot of
its sport's colour before the title but in a narrow window), its numbers in a card (four on a
line or two by two, as on the Activities page, gui/activities_since.py), the shape of its track
in its sport's colour on a plain grid with its start marked as on a map (the activity journal's
shape; the page of a file draws its map on the same grid, gui/activity_map.py), its elevation
(the lowest and the highest point beside the title, the profile under it) and its heart rate
(gui/activity_zones.py). The words are in gui/words.py, in the units of its device; nothing
here reads a file.
Has: no attribute of the window."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.gui import sports, words  # noqa: E402
from openexpress.gui.activities_since import (  # noqa: E402
    NumbersLayout,
    number_cell,
    number_markup,
)
from openexpress.gui.activity_colours import accent_colour  # noqa: E402
from openexpress.gui.activity_map import draw_grid, draw_start  # noqa: E402
from openexpress.gui.activity_zones import SectionHeader, ZoneList  # noqa: E402
from openexpress.gui.palette import rgba  # noqa: E402

SHAPE_HEIGHT = 260  # pixels of the card of a shape
SHAPE_PADDING = 28  # pixels around the shape in its card
SHAPE_LINE = 4.0  # pixels of the track's line
PROFILE_HEIGHT = 110  # pixels of the elevation's profile
PROFILE_TOP, PROFILE_BOTTOM = 10, 12  # pixels over its highest point and under its lowest
PROFILE_FILL = 0.18  # the strength of the accent colour under its line


class Heading(Gtk.Box):
    """The kind of the activity as the page's title, a dot of its sport's colour before it
    (`dot`, for the eye only), and under it when it started and on which device."""

    __gtype_name__ = "OpenExpressActivityHeading"

    def __init__(self, shown, device: str, group: str | None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        line = Gtk.Box(spacing=10)
        self.dot = None
        if group is not None:
            self.dot = Gtk.Box(valign=Gtk.Align.CENTER,
                               accessible_role=Gtk.AccessibleRole.PRESENTATION)
            self.dot.add_css_class("oe-sport-dot")
            self.dot.add_css_class(f"oe-sport-{group}")
            line.append(self.dot)
        title = Gtk.Label(label=words.overview_title(shown), wrap=True, xalign=0, hexpand=True)
        title.add_css_class("title-1")
        line.append(title)
        self.append(line)
        when = Gtk.Label(label=words.overview_when(shown, device), wrap=True, xalign=0)
        when.add_css_class("dim-label")
        self.append(when)


class NumbersCard(Gtk.Box):
    """The numbers of the activity in a card (words.overview_numbers): four on a line when
    they fit, else two by two; none of them takes a click or the focus."""

    __gtype_name__ = "OpenExpressNumbersCard"

    def __init__(self, numbers):
        super().__init__()
        self.add_css_class("card")
        grid = Gtk.Box(hexpand=True)
        grid.set_layout_manager(NumbersLayout())
        self.cells = []
        for name, template, values in numbers:
            cell = number_cell(number_markup(template, values), name)
            grid.append(cell)
            self.cells.append(cell)
        self.append(grid)


def _draw_shape(area, cr, width: int, height: int) -> None:
    """The grid; the track's shape (0-1000 along its longer side, north up) in the middle, as
    large as it fits, in the colour of its sport (`colour`); its start marked as on a map."""
    draw_grid(cr, width, height, area.get_color())
    points = area.points
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    spread_x, spread_y = max(xs) - min(xs), max(ys) - min(ys)
    room_x, room_y = width - 2 * SHAPE_PADDING, height - 2 * SHAPE_PADDING
    if room_x <= 0 or room_y <= 0:
        return
    scale = min(room_x / max(spread_x, 1), room_y / max(spread_y, 1))
    left = (width - spread_x * scale) / 2 - min(xs) * scale
    top = (height - spread_y * scale) / 2 - min(ys) * scale
    placed = [(left + x * scale, top + y * scale) for x, y in points]
    cr.set_line_cap(1)  # round
    cr.set_line_join(1)  # round
    colour = area.colour
    cr.set_source_rgba(colour.red, colour.green, colour.blue, 1)
    cr.set_line_width(SHAPE_LINE)
    cr.move_to(*placed[0])
    for point in placed[1:]:
        cr.line_to(*point)
    cr.stroke()
    draw_start(cr, *placed[0])


class ShapeCard(Gtk.Box):
    """The shape of the track the activity journal kept, in the colour of its sport's `group`
    (gui/sports.py, as on its tile on the Activities page), on a plain grid with its start
    marked as on a map, and the words that there is no map under it (the journal keeps no
    place on Earth)."""

    __gtype_name__ = "OpenExpressShapeCard"

    def __init__(self, points, group: str):
        super().__init__()
        self.area = Gtk.DrawingArea(content_height=SHAPE_HEIGHT, hexpand=True,
                                    accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.area.points = points
        self.area.colour = rgba(sports.COLOURS[group])
        self.area.set_draw_func(_draw_shape)
        overlay = Gtk.Overlay(child=self.area)
        said = Gtk.Label(label=words.shape_only_text(), wrap=True, xalign=0,
                         halign=Gtk.Align.START, valign=Gtk.Align.START, margin_top=10,
                         margin_start=12, margin_end=12)
        said.add_css_class("caption")
        said.add_css_class("dim-label")
        overlay.add_overlay(said)
        frame = Gtk.Frame(child=overlay, hexpand=True)
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        self.append(frame)


def _draw_profile(area, cr, width: int, height: int) -> None:
    """The elevation along the distance, edge to edge: its line in the accent colour, the
    colour fainter under it down to the bottom."""
    values = area.values
    low, high = min(values), max(values)
    span = max(high - low, 1)
    room = height - PROFILE_TOP - PROFILE_BOTTOM
    placed = [(width * i / (len(values) - 1), PROFILE_TOP + room * (high - v) / span)
              for i, v in enumerate(values)]
    colour = accent_colour()
    cr.move_to(0, height)
    for point in placed:
        cr.line_to(*point)
    cr.line_to(width, height)
    cr.close_path()
    cr.set_source_rgba(colour.red, colour.green, colour.blue, PROFILE_FILL)
    cr.fill()
    cr.set_line_join(1)  # round
    cr.set_source_rgba(colour.red, colour.green, colour.blue, 1)
    cr.set_line_width(2)
    cr.move_to(*placed[0])
    for point in placed[1:]:
        cr.line_to(*point)
    cr.stroke()


class ProfileArea(Gtk.DrawingArea):
    """The elevation profile (`values`: metres along the distance); for the eye only (the
    lowest and highest point are said beside the title)."""

    __gtype_name__ = "OpenExpressProfileArea"

    def __init__(self, values):
        super().__init__(content_height=PROFILE_HEIGHT, hexpand=True,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.values = list(values)
        self.set_draw_func(_draw_profile)


class ElevationSection(Gtk.Box):
    """The elevation: its header (SectionHeader: its title, the lowest and the highest point
    beside it or under it, `said`), the profile under it in a card (`profile`, None without
    one)."""

    __gtype_name__ = "OpenExpressElevationSection"

    def __init__(self, shown):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        title = words.chart_series_name("elevation")
        self.header = SectionHeader(title, None, words.elevation_range(shown))
        self.said = self.header.said
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.append(self.header)
        self.profile = None
        if shown.profile:
            self.profile = ProfileArea(shown.profile)
            card = Gtk.Box()
            card.add_css_class("card")
            card.set_overflow(Gtk.Overflow.HIDDEN)
            card.append(self.profile)
            self.append(card)


def elevation(shown) -> ElevationSection | None:
    """The section of the elevation; None without its lowest and highest point."""
    return ElevationSection(shown) if words.elevation_range(shown) else None


def heart_rate(shown, zones=None) -> ZoneList | None:
    """The section of the heart rate (with the rows of `zones`, fit.HeartRateZones, the file's);
    None without a heart rate or a time in a zone."""
    said = words.heart_rate_words(shown)
    if not said and not (shown.zones and sum(shown.zones) > 0) and zones is None:
        return None
    return ZoneList(zones, shown.zones, said)

