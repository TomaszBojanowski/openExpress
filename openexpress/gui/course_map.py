"""The map of a GPX file in the question before it is copied to the device (gui/courses_page.py):
the map of an activity's page (gui/activity_map.MapView: OpenStreetMap's tiles after the yes given
once for the program, their attribution, what keeps them away) with the file drawn on it as the
activity page draws a track: each track a line, each route a dashed line through its points
with a dot at each (the device joins them with straight lines), the start of each in green, and
the file's waypoints as markers of their own. It shows the whole file and takes no clicks: a
look at the file before it is copied, not a map to explore. Without the tiles (no yes, no
network, a tile that failed) the drawing alone on a plain background; nothing waits for it.
Has: no attribute of the window; the map keeps what it draws itself."""
from __future__ import annotations

import math
import weakref

import cairo
import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Graphene, Gtk  # noqa: E402 - imports must follow gi.require_version

from openexpress.gui import activity_map  # noqa: E402
from openexpress.gui.activity_map import (  # noqa: E402
    EDGE_WIDTH,
    END_SIZE,
    TRACK_COLOUR,
    TRACK_EDGE,
    TRACK_WIDTH,
    MapView,
    _disc,
    _draw_end,
    _marker,
    _view_changed,
    bounds,
    thinned,
    world_xy,
)
from openexpress.gui.palette import BLUE, rgba  # noqa: E402
from openexpress.i18n import _  # noqa: E402

MAP_HEIGHT = 170
# Pixels kept clear on each side beyond the map's own margin: the attribution's strip at the
# bottom and half a marker, so that no end of a line is drawn under them.
ROOM = 22
DASH = (10.0, 7.0)  # pixels: a route's stroke and its gap
POINT_RADIUS = 3.5  # pixels: the dot of a route's point
PLACE_SIZE = 16  # pixels: the marker of a waypoint


def available() -> bool:
    """Whether the question can show a map (libshumate is there)."""
    return activity_map.available()


def _draw_place(_area, cr, width: int, height: int) -> None:
    """A waypoint: a blue disc in a white ring, apart from the green start."""
    x, y, radius = _disc(cr, width, height, 2)
    colour = rgba(BLUE)
    cr.arc(x, y, radius, 0, 2 * math.pi)
    cr.set_source_rgba(colour.red, colour.green, colour.blue, 1)
    cr.fill()


if activity_map.Shumate is not None:
    class CourseLayer(activity_map.Shumate.Layer):
        """The file's tracks and routes, each segment in the track's colour over its dark edge:
        a track whole, a route dashed with a dot at each of its points. Their points kept on
        the whole map at zoom 0 and placed at each drawing."""

        __gtype_name__ = "OpenExpressCourseLayer"

        def __init__(self, viewport, gpx):
            super().__init__(viewport=viewport)
            self.lines = [(line.kind == "route",
                           [world_xy(lat, lon, 0) for lat, lon in thinned(list(segment))])
                          for line in gpx.lines for segment in line.segments]
            viewport.connect("notify", _view_changed, weakref.ref(self))

        def do_snapshot(self, snapshot) -> None:
            width, height = self.get_width(), self.get_height()
            if width <= 0 or height <= 0 or not self.lines:
                return
            viewport = self.get_viewport()
            zoom = viewport.get_zoom_level()
            scale = 2 ** zoom
            cx, cy = world_xy(viewport.get_latitude(), viewport.get_longitude(), zoom)
            dx, dy = width / 2 - cx, height / 2 - cy
            cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            colour, edge = rgba(TRACK_COLOUR), rgba(TRACK_EDGE)
            for dashed, world in self.lines:
                points = [(x * scale + dx, y * scale + dy) for x, y in world]
                cr.set_dash(DASH if dashed else ())
                for stroke, shade in ((TRACK_WIDTH + 2 * EDGE_WIDTH, edge),
                                      (TRACK_WIDTH, colour)):
                    cr.set_line_width(stroke)
                    cr.set_source_rgba(shade.red, shade.green, shade.blue, 1)
                    cr.move_to(*points[0])
                    for point in points[1:] or points[:1]:
                        cr.line_to(*point)
                    cr.stroke()
                cr.set_dash(())
                if dashed:
                    for radius, shade in ((POINT_RADIUS + EDGE_WIDTH, edge),
                                          (POINT_RADIUS, colour)):
                        cr.set_source_rgba(shade.red, shade.green, shade.blue, 1)
                        for x, y in points:
                            cr.arc(x, y, radius, 0, 2 * math.pi)
                            cr.fill()


class CourseMap(MapView):
    """The map of a GPX file (course_gpx.GpxFile): its tracks and routes (CourseLayer), the
    start of each, its waypoints, all of it in view; no clicks."""

    __gtype_name__ = "OpenExpressCourseMap"

    def __init__(self, gpx):
        super().__init__(MAP_HEIGHT)
        self.map.set_can_target(False)  # a look at the file: neither dragged nor zoomed
        # TRANSLATORS: for a screen reader: the map in the confirmation before a GPX file is copied
        self.map.update_property([Gtk.AccessibleProperty.LABEL], [_("Map of the GPX file")])
        viewport = self.map.get_viewport()
        self.path = CourseLayer(viewport, gpx)
        self.map.add_layer(self.path)
        self.lowest = self.path
        marks = activity_map.Shumate.MarkerLayer.new(viewport)
        for line in gpx.lines:
            marker = _marker(END_SIZE, _draw_end, "start")
            marker.set_location(*line.segments[0][0])
            marks.add_marker(marker)
        for lat, lon in gpx.places:
            marker = _marker(PLACE_SIZE, _draw_place)
            marker.set_location(lat, lon)
            marks.add_marker(marker)
        self.map.add_layer(marks)
        self.box = bounds([point for line in gpx.lines for segment in line.segments
                           for point in segment] + list(gpx.places))
        self.fit(400, MAP_HEIGHT)  # near already; _fit sets it for the map's own size
        self._below()

    def fit(self, width: float, height: float) -> None:
        """The whole file in view, ROOM clear of the edges besides the map's margin."""
        super().fit(max(1.0, width - 2 * ROOM), max(1.0, height - 2 * ROOM))
