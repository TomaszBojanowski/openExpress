"""The map of an activity's page: its track (the records with positions) on libshumate, GNOME's
map widget, drawn by openExpress thick in a colour of its own with a dark edge (a lap chosen
under the chart at full strength, the rest faded), its start and finish marked, and a marker
where the chart's cursor is. The mouse wheel scrolls the page over it; Ctrl and the wheel, its
buttons, a double click or a pinch zoom it. Streets and terrain under it come from
OpenStreetMap's tile server only after a yes given once for the program (gui.json "map_tiles":
on the page or in Preferences, taken back there); until then the track is on a plain
background, with a note of what the server would learn and a button. The tiles are asked by
openExpress itself, with its own User-Agent, at most DOWNLOADS at a time and only those the map
shows (its view is set, never flown to), kept in the program's cache (cache.CATEGORIES "tiles")
as long as the server allows, at most CACHE_LIMIT, and drawn by openExpress (TileLayer: each
one a pixel past its edges, so that no seam is left where two meet between two pixels of a
screen at a fractional scale); the log names no place. Without a network the map says that
only the tiles downloaded before are shown, and asks again those that failed once it is back;
without libshumate the page says what is missing. The map with its tiles, attribution and
what keeps them away (MapView) is that of the question before a GPX file is copied too
(gui/course_map.py).
Has: no attribute of the window; the map's widgets keep their track and state themselves."""
from __future__ import annotations

import logging
import math
import queue
import threading
import weakref
from collections import OrderedDict
from dataclasses import dataclass

import cairo
import gi
import requests

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402 - imports must follow gi.require_version

try:
    gi.require_version("Shumate", "1.0")
    from gi.repository import Shumate
except (ValueError, ImportError):  # not installed (the version from source): no map
    Shumate = None

from openexpress import __version__, cache, paths, useragent  # noqa: E402
from openexpress.gui import activity_colours  # noqa: E402
from openexpress.gui.activity_chart import nearest  # noqa: E402
from openexpress.gui.gui_settings import load_gui_settings, update_gui_settings  # noqa: E402
from openexpress.gui.palette import GREEN, rgba  # noqa: E402
from openexpress.i18n import _  # noqa: E402

log = logging.getLogger(__name__)

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
USER_AGENT = f"openExpress/{__version__} (+https://github.com/TomaszBojanowski/openExpress)"
CACHE_FOLDER = cache.CATEGORIES["tiles"][0]  # in the program's cache
CACHE_KEY = "openstreetmap"
CACHE_LIMIT = 100_000_000  # bytes; the less used tiles go first
PURGE_EVERY = 200  # tiles stored between two purges of the cache to its limit
FRESH_S = 7 * 24 * 3600  # a tile whose server says nothing of caching: a week (the tile policy)
DOWNLOADS = 2  # tiles downloaded at a time
TIMEOUT_S = 20
MAX_ZOOM = 19
TILE_SIZE = 256
CLOSEST_FIT = 17  # the zoom of a track of a single point
FIT_MARGIN = 0.1  # of the map's width and height on each side of the track
MAP_HEIGHT = 300
PATH_POINTS = 5000  # a longer track is thinned for the map
SETTING = "map_tiles"  # in gui.json: the yes to OpenStreetMap's tiles
TRACK_COLOUR = "#e5420e"  # orange-red: apart from the water, the green and the streets of the tiles
TRACK_EDGE = "#6b1d05"  # its dark edge, seen on the light streets
TRACK_WIDTH = 4  # pixels of the track's colour
JOIN_PX = 1.0  # how far a piece of a coloured track starts back along the stretch before
EDGE_WIDTH = 1.5  # pixels of its dark edge on each side
START_COLOUR = GREEN
END_SIZE = 18  # pixels: the marker of the start and the one of the finish
BOTH_SIZE = 20  # the one marker of a loop's start and finish
CURSOR_SIZE = 20  # the marker of the chart's cursor, above them
LOOP_M = 50  # a finish closer to the start: a loop, one marker for both
BLEED = 1  # pixels each tile is drawn past its right and bottom edge, under the next one
KEEP_TILES = 64  # tiles kept drawn in memory, the less used let go first
FALLBACK_ZOOMS = 3  # while a tile loads, those of up to so many zooms below or above
CHOSEN_WIDTH = 6  # pixels of the track's colour where a lap is chosen
FADED = 0.5  # the strength of the rest of the track then
HINT_S = 1.5  # how long the map says that Ctrl and the wheel zoom it
ZOOM_MS = 200  # the flight of the zoom buttons, as libshumate's own zoom


def available() -> bool:
    """Whether libshumate is installed (the Flatpak has it; from source, a package)."""
    return Shumate is not None


def can_ask() -> bool:
    """Whether this libshumate lets openExpress ask the tiles itself (libshumate 1.1 and
    later): its own User-Agent and cache. An older one keeps the track on a plain background."""
    return Shumate is not None and hasattr(Shumate, "DataSourceRequest")


def tiles_on() -> bool:
    """Whether the yes to OpenStreetMap's tiles is given (off until it is)."""
    return load_gui_settings().get(SETTING) is True


def set_tiles(on: bool) -> None:
    update_gui_settings(**{SETTING: bool(on)})
    log.info("activity map: the tiles of OpenStreetMap turned %s", "on" if on else "off")


# --- the track -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Track:
    """The records of an activity with a position: each one's time in seconds since the first
    record of the activity (as the chart's) and its (latitude, longitude)."""

    times: tuple[float, ...]
    points: tuple[tuple[float, float], ...]


def track_of(data) -> Track | None:
    """The track of an activity (fit.ActivityData); None without any position."""
    samples = data.samples
    if not samples:
        return None
    origin = samples[0].time
    found = [(s.time - origin, (s.latitude, s.longitude)) for s in samples
             if s.latitude is not None and s.longitude is not None]
    if not found:
        return None
    return Track(tuple(t for t, _p in found), tuple(p for _t, p in found))


def thinned(points, most: int = PATH_POINTS) -> list:
    """At most about `most` of the points, evenly, the last one kept."""
    if len(points) <= most:
        return list(points)
    out = list(points[::math.ceil(len(points) / most)])
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def bounds(points) -> tuple[float, float, float, float]:
    """(south, west, north, east) of the points."""
    lats = [lat for lat, _lon in points]
    lons = [lon for _lat, lon in points]
    return min(lats), min(lons), max(lats), max(lons)


def _mercator(lat: float) -> float:
    lat = max(min(lat, 85.0511), -85.0511)
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def fitting_zoom(box, width: float, height: float) -> float:
    """The zoom at which the bounds fill a map of this size but FIT_MARGIN on each side,
    between two whole zooms as it falls (a whole zoom would leave up to half of the map empty);
    not closer than CLOSEST_FIT."""
    south, west, north, east = box
    zooms = [CLOSEST_FIT]
    for share, room in (((east - west) / 360, width),
                        ((_mercator(north) - _mercator(south)) / (2 * math.pi), height)):
        if share > 0:
            zooms.append(math.log2(max(room * (1 - 2 * FIT_MARGIN), 1) / (share * TILE_SIZE)))
    return max(1.0, min(float(CLOSEST_FIT), min(zooms)))


def center(box) -> tuple[float, float]:
    """The middle of the bounds as the map draws them (Web Mercator)."""
    south, west, north, east = box
    y = (_mercator(south) + _mercator(north)) / 2
    return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2), (west + east) / 2


def position_at(track: Track, at: float) -> tuple[float, float] | None:
    """Where the record nearest to the time `at` is; None in a gap or a pause (no record
    within half of the chart's GAP_S)."""
    i = nearest(track.times, min(max(at, track.times[0]), track.times[-1]))
    return None if i is None else track.points[i]


def _metres(a, b) -> float:
    """The distance between two (latitude, longitude) on the Earth (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(h)))


def ends_of(track: Track) -> list[tuple[str, tuple[float, float]]]:
    """The markers of the track's ends, in the order they are drawn: the start and the finish,
    or one for both at the start when the finish is closer than LOOP_M (a loop)."""
    first, last = track.points[0], track.points[-1]
    if _metres(first, last) < LOOP_M:
        return [("start-and-finish", first)]
    return [("start", first), ("finish", last)]


# --- where the tiles are drawn ----------------------------------------------------------------


def world_xy(lat: float, lon: float, zoom: float) -> tuple[float, float]:
    """Where a place is on the whole map at this zoom, in pixels from its top left (Web
    Mercator, TILE_SIZE tiles)."""
    size = TILE_SIZE * 2 ** zoom
    return (lon + 180) / 360 * size, (1 - _mercator(lat) / math.pi) / 2 * size


@dataclass(frozen=True)
class Place:
    """A tile drawn: its (zoom, x, y) on the tile server, and where it is drawn on the map
    (without BLEED)."""

    key: tuple[int, int, int]
    x: float
    y: float
    width: float
    height: float


def tile_places(zoom: float, lat: float, lon: float, width: float, height: float,
                scale: float = 1.0, rotation: float = 0.0) -> list[Place]:
    """The tiles a map of this size shows around this centre, in the order they are drawn
    (rows from the top, each from the left), each edge on a pixel of a screen of this scale;
    between two zooms, those of the nearest whole zoom, scaled from 0.71 to 1.41 of their size
    (those of the zoom below would be up to twice as large, blurred). Turned by `rotation`
    (radians), those of the circle around the view."""
    whole = max(0, min(MAX_ZOOM, math.floor(zoom + 0.5)))
    size = TILE_SIZE * 2 ** (zoom - whole)
    cx, cy = world_xy(lat, lon, zoom)
    left, top = cx - width / 2, cy - height / 2
    if rotation:
        reach = math.hypot(width, height) / 2
        columns = range(math.floor((cx - reach) / size), math.floor((cx + reach) / size) + 1)
        rows = range(math.floor((cy - reach) / size), math.floor((cy + reach) / size) + 1)
    else:
        columns = range(math.floor(left / size), math.floor((left + width) / size) + 1)
        rows = range(math.floor(top / size), math.floor((top + height) / size) + 1)
    count = 2 ** whole

    def on_pixel(value: float) -> float:
        return round(value * scale) / scale

    places = []
    for y in rows:
        if not 0 <= y < count:
            continue
        top_edge, bottom_edge = on_pixel(y * size - top), on_pixel((y + 1) * size - top)
        for x in columns:
            left_edge, right_edge = on_pixel(x * size - left), on_pixel((x + 1) * size - left)
            places.append(Place((whole, x % count, y), left_edge, top_edge,
                                right_edge - left_edge, bottom_edge - top_edge))
    return places


# --- the tiles -------------------------------------------------------------------------------


def freshness(cache_control: str | None) -> int:
    """How long a tile may be used before it is asked again, as the server's Cache-Control
    says (max-age; no-cache and no-store: at once); FRESH_S when it says nothing."""
    if not cache_control:
        return FRESH_S
    directives = [d.strip().lower() for d in cache_control.split(",")]
    if "no-cache" in directives or "no-store" in directives:
        return 0
    for directive in directives:
        if directive.startswith("max-age="):
            try:
                return max(0, int(directive.split("=", 1)[1]))
            except ValueError:
                break
    return FRESH_S


@dataclass(frozen=True)
class Fetched:
    """What the tile server answered: its status (None: not reached), the tile (with 200), its
    tag and how long it may be used."""

    status: int | None
    data: bytes | None
    etag: str | None
    max_age: int


_local = threading.local()


def _session() -> requests.Session:
    """The session of a downloading thread, named as openExpress."""
    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = useragent.session(USER_AGENT)
    return session


def _fetch(url: str, etag: str | None) -> Fetched:
    """A tile asked of the server (in a downloading thread), with the tag of the one the
    cache has, so that an unchanged one is not sent again."""
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    try:
        answer = _session().get(url, headers=headers, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        _not_downloaded(type(e).__name__)
        return Fetched(None, None, None, 0)
    max_age = freshness(answer.headers.get("Cache-Control"))
    tag = answer.headers.get("ETag")
    if answer.status_code in (200, 304):
        _said.clear()
        return Fetched(answer.status_code, answer.content if answer.status_code == 200 else None,
                       tag, max_age)
    _not_downloaded(f"HTTP {answer.status_code}")
    return Fetched(answer.status_code, None, tag, max_age)


_said: set[str] = set()  # why tiles were not downloaded, each said once until one is


def _not_downloaded(why: str) -> None:
    if why not in _said:
        _said.add(why)
        log.info("activity map: tiles not downloaded (%s)", why)


_waiting: queue.Queue | None = None


def _download_later(url: str, etag: str | None, cancellable, done) -> None:
    """`done(fetched)` on the window's thread once the tile is fetched, by one of DOWNLOADS
    threads (daemons: the program does not wait for them when it ends); nothing for a tile no
    longer wanted."""
    global _waiting
    if _waiting is None:
        _waiting = queue.Queue()
        for _ in range(DOWNLOADS):
            threading.Thread(target=_downloading, args=(_waiting,), daemon=True,
                             name="tiles").start()
    _waiting.put((url, etag, cancellable, done))


def _downloading(waiting: queue.Queue) -> None:
    while True:
        url, etag, cancellable, done = waiting.get()
        if cancellable is not None and cancellable.is_cancelled():
            continue
        fetched = _fetch(url, etag)
        GLib.idle_add(lambda d=done, f=fetched: (d(f), False)[1])


def _tag(etag: str | None, max_age: int) -> str:
    """What the cache keeps with a tile: how long it may be used, and the server's tag."""
    return f"{max_age} {etag or ''}"


def _untag(tag: str | None) -> tuple[str | None, int]:
    if not tag:
        return None, FRESH_S
    max_age, _space, etag = tag.partition(" ")
    try:
        return etag or None, int(max_age)
    except ValueError:
        return None, FRESH_S


def _cached(cache_, result, request_and_cancellable) -> None:
    """The cache's answer for a tile: used while fresh; else shown meanwhile and asked
    again."""
    request, cancellable = request_and_cancellable
    try:
        found, tag, modified = cache_.get_tile_finish(result)
    except GLib.Error:
        found, tag, modified = None, None, None
    etag, max_age = _untag(tag)
    if found is not None and modified is not None:
        age = GLib.DateTime.new_now_utc().difference(modified) / 1_000_000
        if age < max_age:
            request.emit_data(found, True)
            return
        request.emit_data(found, False)
    url = TILE_URL.format(z=request.get_zoom_level(), x=request.get_x(), y=request.get_y())
    _download_later(url, etag if found is not None else None, cancellable,
                    lambda fetched: _downloaded(cache_, request, found, fetched))


def _downloaded(cache_, request, found, fetched: Fetched) -> None:
    """The server's answer: a new tile stored, an unchanged one kept again, or the old one
    when the server was not reached; an error without any."""
    x, y, zoom = request.get_x(), request.get_y(), request.get_zoom_level()
    if fetched.status == 200 and fetched.data:
        tile = GLib.Bytes.new(fetched.data)
    elif fetched.status == 304 and found is not None:
        tile = found
    elif found is not None:
        request.emit_data(found, True)
        return
    else:
        request.emit_error(GLib.Error.new_literal(Gio.io_error_quark(), "no tile",
                                                  Gio.IOErrorEnum.FAILED))
        return
    _stored_once_more(cache_)
    cache_.store_tile_async(x, y, zoom, tile, _tag(fetched.etag, fetched.max_age), None,
                            lambda c, result: _stored(c, result, request, tile))


def _stored(cache_, result, request, tile) -> None:
    try:
        cache_.store_tile_finish(result)
    except GLib.Error as e:
        log.info("activity map: a tile not kept in the cache (%s)", e.domain)
    request.emit_data(tile, True)


_stored_count = [0]


def _stored_once_more(cache_) -> None:
    """Every PURGE_EVERY tiles stored (and at the first), the cache brought to its limit."""
    if _stored_count[0] % PURGE_EVERY == 0:
        cache_.purge_cache_async(None, None)
    _stored_count[0] += 1


if can_ask():
    class TileSource(Shumate.DataSource):
        """The tiles of OpenStreetMap, asked by openExpress (_fetch) and kept in the program's
        cache, made with the first tile asked."""

        __gtype_name__ = "OpenExpressTileSource"

        def __init__(self):
            super().__init__(min_zoom_level=0, max_zoom_level=MAX_ZOOM)
            self.cache = None

        def do_start_request(self, x, y, zoom, cancellable):
            if self.cache is None:
                folder = paths.cache_dir() / CACHE_FOLDER
                self.cache = Shumate.FileCache.new_full(CACHE_LIMIT, CACHE_KEY, str(folder))
            request = Shumate.DataSourceRequest.new(x, y, zoom)
            self.cache.get_tile_async(x, y, zoom, cancellable, _cached, (request, cancellable))
            return request

    class TileLayer(Shumate.Layer):
        """The tiles under the track, drawn by openExpress (libshumate's own layer leaves a
        seam of the background where two tiles meet between two pixels of a screen at a
        fractional scale): in rows from the top, each from the left, each one BLEED past its
        right and bottom edge, under the next one; while a tile loads, those drawn before in
        its place, of a zoom below or above, scaled, so that the map is never empty where it
        showed tiles (zoomed in or out with the mouse wheel, every tile of the next zoom is
        new); once the view has all its own tiles, those of other zooms are let go. Only the
        tiles in view are asked of the TileSource; those gone from it are let go, those that
        failed asked again by retry()."""

        __gtype_name__ = "OpenExpressTileLayer"

        def __init__(self, viewport):
            super().__init__(viewport=viewport)
            self.source = TileSource()
            self.textures: OrderedDict = OrderedDict()  # (zoom, x, y): Gdk.Texture
            self.asked: dict = {}  # (zoom, x, y): (its request, its cancellable)
            self.failed: set = set()
            self.handler = viewport.connect("notify", _view_changed, weakref.ref(self))

        def do_snapshot(self, snapshot) -> None:
            viewport = self.get_viewport()
            width, height = self.get_width(), self.get_height()
            if width <= 0 or height <= 0:
                return
            rotation = viewport.get_rotation()
            places = tile_places(viewport.get_zoom_level(), viewport.get_latitude(),
                                 viewport.get_longitude(), width, height, _screen_scale(self),
                                 rotation)
            snapshot.save()
            if rotation:
                middle = Graphene.Point().init(width / 2, height / 2)
                snapshot.translate(middle)
                snapshot.rotate(math.degrees(rotation))
                snapshot.translate(Graphene.Point().init(-width / 2, -height / 2))
            whole = True
            for place in places:
                texture = self.textures.get(place.key)
                if texture is not None:
                    self.textures.move_to_end(place.key)
                    snapshot.append_texture(texture, Graphene.Rect().init(
                        place.x, place.y, place.width + BLEED, place.height + BLEED))
                else:
                    whole = False
                    self._ask(place.key)
                    self._draw_below(snapshot, place)
                    self._draw_above(snapshot, place)
            snapshot.restore()
            wanted = {place.key for place in places}
            for key in [key for key in self.asked if key not in wanted]:
                self.asked.pop(key)[1].cancel()
            if whole and places:  # the tiles of other zooms no longer needed in its place
                zoom = places[0].key[0]
                for key in [key for key in self.textures if key[0] != zoom]:
                    del self.textures[key]

        def _draw_below(self, snapshot, place: Place) -> None:
            """The part of a tile of a zoom below where the tile `place` will be."""
            zoom, x, y = place.key
            for k in range(1, FALLBACK_ZOOMS + 1):
                texture = self.textures.get((zoom - k, x >> k, y >> k))
                if texture is None:
                    continue
                n = 2 ** k
                snapshot.push_clip(Graphene.Rect().init(place.x, place.y, place.width + BLEED,
                                                        place.height + BLEED))
                snapshot.append_texture(texture, Graphene.Rect().init(
                    place.x - (x % n) * place.width, place.y - (y % n) * place.height,
                    n * place.width + BLEED, n * place.height + BLEED))
                snapshot.pop()
                return

        def _draw_above(self, snapshot, place: Place) -> None:
            """The tiles of a zoom above drawn smaller where the tile `place` will be: the
            first zoom above with any of them, those it has (over the zoom below's, if
            drawn)."""
            zoom, x, y = place.key
            for k in range(1, FALLBACK_ZOOMS + 1):
                n = 2 ** k
                found = [(column, row, self.textures.get((zoom + k, x * n + column, y * n + row)))
                         for row in range(n) for column in range(n)]
                found = [(column, row, texture) for column, row, texture in found
                         if texture is not None]
                if not found:
                    continue
                width, height = place.width / n, place.height / n
                for column, row, texture in found:  # in rows, each from the left: as the tiles
                    snapshot.append_texture(texture, Graphene.Rect().init(
                        place.x + column * width, place.y + row * height, width + BLEED,
                        height + BLEED))
                return

        def _ask(self, key) -> None:
            if key in self.asked or key in self.failed:
                return
            zoom, x, y = key
            cancellable = Gio.Cancellable()
            request = self.source.start_request(x, y, zoom, cancellable)
            self.asked[key] = (request, cancellable)
            this = weakref.ref(self)
            request.connect("notify::data", _tile_data, this, key)
            request.connect("notify::completed", _tile_done, this, key)

        def keep(self, key, texture) -> None:
            self.textures[key] = texture
            self.textures.move_to_end(key)
            while len(self.textures) > KEEP_TILES:
                self.textures.popitem(last=False)
            self.queue_draw()

        def retry(self) -> None:
            """The tiles that failed asked again (the network is back)."""
            if self.failed:
                self.failed.clear()
                self.queue_draw()

        def let_go(self) -> None:
            """The tiles being asked no longer wanted (the map off the screen, or the tiles
            turned off); asked again when drawn."""
            for _request, cancellable in self.asked.values():
                cancellable.cancel()
            self.asked.clear()

        def stop(self) -> None:
            self.let_go()
            self.get_viewport().disconnect(self.handler)


def _view_changed(_viewport, _pspec, layer_ref) -> None:
    layer = layer_ref()
    if layer is not None:
        layer.queue_draw()


def _tile_data(request, _pspec, layer_ref, key) -> None:
    """A tile's data (the one in the cache meanwhile, then the server's): drawn."""
    layer, data = layer_ref(), request.get_data()
    if layer is None or data is None:
        return
    try:
        texture = Gdk.Texture.new_from_bytes(data)
    except GLib.Error as e:
        log.info("activity map: a tile that is not a picture (%s)", e.domain)
        return
    layer.keep(key, texture)


def _tile_done(request, _pspec, layer_ref, key) -> None:
    layer = layer_ref()
    if layer is None or layer.asked.get(key, (None,))[0] is not request:
        return
    del layer.asked[key]
    if request.get_error() is not None and key not in layer.textures:
        layer.failed.add(key)


def _screen_scale(widget) -> float:
    """The scale of the screen the widget is on (fractional: 1.25 at 125 %)."""
    native = widget.get_native()
    surface = native.get_surface() if native is not None else None
    return surface.get_scale() if surface is not None else widget.get_scale_factor()


def _trace(cr, points, run) -> None:
    """The line through the points of a run (their indices); one point alone: a dot."""
    cr.move_to(*points[run[0]])
    for i in run[1:] or run[:1]:
        cr.line_to(*points[i])


def _just_before(before, at) -> tuple[float, float]:
    """The point JOIN_PX before `at` on the stretch from `before` (`at` itself on a stretch of
    no length; `before` on a shorter one)."""
    dx, dy = at[0] - before[0], at[1] - before[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return at
    back = min(JOIN_PX, length) / length
    return at[0] - dx * back, at[1] - dy * back


def _by_colour(runs, colours) -> dict[str | None, list[list[int]]]:
    """The runs cut into pieces of one colour, each piece in the colour of the point it
    starts at and reaching the next piece's first point; without colours, the runs as they
    are (None: the track's colour)."""
    if colours is None:
        return {None: runs}
    out: dict[str | None, list[list[int]]] = {}
    for run in runs:
        if len(run) == 1:
            out.setdefault(colours[run[0]], []).append(run)
            continue
        piece = [run[0]]
        for i in run[1:]:
            piece.append(i)
            if colours[i] != colours[piece[0]] or i == run[-1]:
                out.setdefault(colours[piece[0]], []).append(piece)
                piece = [i]
    return out


def _runs(points, inside, wanted: bool) -> list[list]:
    """The runs of `points` whose `inside` is `wanted`; a run outside the lap reaches one point
    into it on each side, so that no gap shows where they meet."""
    runs, run = [], []
    for i, point in enumerate(points):
        if inside[i] == wanted:
            if not run and not wanted and i > 0:
                run.append(points[i - 1])
            run.append(point)
        elif run:
            if not wanted:
                run.append(point)
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return runs


if Shumate is not None:
    class TrackLayer(Shumate.Layer):
        """The track, drawn by openExpress: its colour over a dark edge, or each piece in the
        colour of the point it starts at (`colours`, gui/activity_colours.py) over the edge of
        a coloured track; a lap chosen (its start and end on the chart's time) thicker at
        full strength, the rest at FADED. Its points are kept on the whole map at zoom 0 and
        placed at each drawing."""

        __gtype_name__ = "OpenExpressTrackLayer"

        def __init__(self, viewport, track: Track):
            super().__init__(viewport=viewport)
            points = thinned(list(zip(track.times, track.points, strict=True)))
            self.times = [t for t, _point in points]
            self.world = [world_xy(lat, lon, 0) for _t, (lat, lon) in points]
            self.track_colour, self.edge = rgba(TRACK_COLOUR), rgba(TRACK_EDGE)
            self.width, self.edge_width = TRACK_WIDTH, EDGE_WIDTH
            self.chosen: tuple[float, float] | None = None
            self.colours: list[str] | None = None  # of each point; None: the track's colour
            viewport.connect("notify", _view_changed, weakref.ref(self))

        def colour(self, colours: list[str] | None) -> None:
            """Each point in its colour over the edge of a coloured track; None: the track's
            own colour and edge."""
            self.colours = colours
            self.edge = rgba(activity_colours.EDGE if colours is not None else TRACK_EDGE)
            self.queue_draw()

        def do_snapshot(self, snapshot) -> None:
            width, height = self.get_width(), self.get_height()
            if width <= 0 or height <= 0 or not self.world:
                return
            viewport = self.get_viewport()
            zoom = viewport.get_zoom_level()
            scale = 2 ** zoom
            cx, cy = world_xy(viewport.get_latitude(), viewport.get_longitude(), zoom)
            dx, dy = width / 2 - cx, height / 2 - cy
            points = [(x * scale + dx, y * scale + dy) for x, y in self.world]
            cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))
            rotation = viewport.get_rotation()
            if rotation:
                cr.translate(width / 2, height / 2)
                cr.rotate(rotation)
                cr.translate(-width / 2, -height / 2)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            every = list(range(len(points)))
            if self.chosen is None:
                self._stroke(cr, points, [every], self.width, 1.0)
                return
            start, end = self.chosen
            inside = [start <= t <= end for t in self.times]
            self._stroke(cr, points, _runs(every, inside, False), self.width, FADED)
            self._stroke(cr, points, _runs(every, inside, True), CHOSEN_WIDTH, 1.0)

        def _stroke(self, cr, points, runs, width: float, strength: float) -> None:
            """The runs (of the points' indices, each in order) as lines `width` wide over
            their edge, at this strength; in their colours, one stroke for each. A piece of one
            colour ends flat where the next one begins (a round end would stick out over its
            neighbours, a short piece a dot); it starts JOIN_PX back along the stretch before,
            so that its round join fills the bend and no gap opens there. The colours are drawn
            whole and laid on at this strength together, so that where two pieces meet none is
            blended twice; only the runs' own ends are round."""
            edge = self.edge
            cr.set_line_width(width + 2 * self.edge_width)
            cr.set_source_rgba(edge.red, edge.green, edge.blue, strength)
            for run in runs:
                _trace(cr, points, run)
            cr.stroke()
            cr.set_line_width(width)
            coloured = self.colours is not None
            if not coloured:
                shade = self.track_colour
                cr.set_source_rgba(shade.red, shade.green, shade.blue, strength)
                for run in runs:
                    _trace(cr, points, run)
                cr.stroke()
                return
            cr.push_group()
            cr.set_line_cap(cairo.LINE_CAP_BUTT)
            firsts = {run[0] for run in runs}
            for colour, pieces in _by_colour(runs, self.colours).items():
                shade = rgba(colour)
                cr.set_source_rgba(shade.red, shade.green, shade.blue, 1)
                for piece in pieces:
                    if piece[0] in firsts:
                        _trace(cr, points, piece)
                    else:
                        cr.move_to(*_just_before(points[piece[0] - 1], points[piece[0]]))
                        for i in piece:
                            cr.line_to(*points[i])
                cr.stroke()
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            for i in {i for run in runs for i in (run[0], run[-1])}:
                shade = rgba(self.colours[i])
                cr.set_source_rgba(shade.red, shade.green, shade.blue, 1)
                _trace(cr, points, [i])
                cr.stroke()
            cr.pop_group_to_source()
            cr.paint_with_alpha(strength)

    class NoTiles(Shumate.DataSource):
        """A source that is never asked: the reference of the map's view (its projection, its
        tile size and zooms); the tiles are drawn by TileLayer. libshumate's own sources would
        make its cache (~/.cache/shumate) as soon as they are made."""

        __gtype_name__ = "OpenExpressNoTiles"


def _map_source():
    """The reference of the map's view: OpenStreetMap's projection and zooms."""
    return Shumate.RasterRenderer.new_full(
        # TRANSLATORS: attribution of the map data on the activity map; keep © and the name
        # OpenStreetMap
        CACHE_KEY, "OpenStreetMap", _("© OpenStreetMap contributors"), COPYRIGHT_URL, 0,
        MAX_ZOOM, TILE_SIZE, Shumate.MapProjection.MERCATOR, NoTiles())


def _network_monitor():
    return Gio.NetworkMonitor.get_default()


# --- the widget ------------------------------------------------------------------------------


def _colours_switched(switch, _pspec) -> None:
    """The switch under the map: the track's colours on or off, kept for the next pages."""
    on = switch.get_active()
    if on == activity_colours.colours_on():
        return
    activity_colours.set_colours(on)
    log.info("activity map: the track's colours turned %s", "on" if on else "off")
    shown = switch.get_ancestor(ActivityMap)
    if shown is not None:
        shown.colour_by(shown.series)


def _disc(cr, width: int, height: int, ring: float) -> tuple[float, float, float]:
    """A marker's disc: a thin dark edge and a white ring `ring` wide; its middle (x, y,
    radius) left to fill."""
    x, y, radius = width / 2, height / 2, min(width, height) / 2 - 1
    cr.arc(x, y, radius, 0, 2 * math.pi)
    cr.set_source_rgba(0, 0, 0, 0.55)
    cr.fill()
    cr.arc(x, y, radius - 1, 0, 2 * math.pi)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.fill()
    return x, y, radius - 1 - ring


def _draw_cursor(_area, cr, width: int, height: int) -> None:
    x, y, radius = _disc(cr, width, height, 3)
    color = activity_colours.accent_colour()  # as the chart's cursor
    cr.arc(x, y, radius, 0, 2 * math.pi)
    cr.set_source_rgba(color.red, color.green, color.blue, 1)
    cr.fill()


def _draw_end(_area, cr, width: int, height: int, kind: str) -> None:
    """The start green, the finish chequered; a loop's one marker half of each."""
    x, y, radius = _disc(cr, width, height, 2)
    cr.save()
    cr.arc(x, y, radius, 0, 2 * math.pi)
    cr.clip()
    if kind != "start":
        cr.set_source_rgba(0.12, 0.12, 0.12, 1)
        cr.paint()
        cr.set_source_rgba(1, 1, 1, 1)
        step = radius / 2
        for row in range(4):
            for column in range(row % 2, 4, 2):
                cr.rectangle(x - radius + column * step, y - radius + row * step, step, step)
        cr.fill()
    if kind != "finish":
        if kind == "start-and-finish":
            cr.rectangle(x - radius, y - radius, radius, 2 * radius)
            cr.clip()
        color = rgba(START_COLOUR)
        cr.set_source_rgba(color.red, color.green, color.blue, 1)
        cr.paint()
    cr.restore()


def _marker(size: int, draw, *data) -> Shumate.Marker:
    dot = Gtk.DrawingArea(content_width=size, content_height=size)
    dot.set_draw_func(draw, *data)
    return Shumate.Marker(child=dot)


def _with_ctrl(controller) -> bool:
    return bool(controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)


def _wheel(controller, _dx: float, dy: float) -> bool:
    """The mouse wheel over the map: with Ctrl, libshumate zooms it; without, the page scrolls
    (never stopped by the map) and the map says how to zoom it."""
    if _with_ctrl(controller):
        return False
    widget = controller.get_widget()
    shown = widget.get_ancestor(ActivityMap)
    if shown is not None:
        shown.say_ctrl()
    scrolled = widget.get_ancestor(Gtk.ScrolledWindow)
    if scrolled is not None:
        adjustment = scrolled.get_vadjustment()
        # As GTK scrolls a page by a notch of the wheel; a touchpad gives pixels.
        step = (adjustment.get_page_size() ** (2 / 3)
                if controller.get_unit() == Gdk.ScrollUnit.WHEEL else 1)
        adjustment.set_value(adjustment.get_value() + dy * step)
    return True


def _hide_hint(shown_ref) -> bool:
    shown = shown_ref()
    if shown is not None:
        shown.hint.set_visible(False)
        shown.hint_timer = None
    return GLib.SOURCE_REMOVE


def _zoom_by_button(button, step: int) -> None:
    """To the next whole zoom in or out, flown to (libshumate's own steps are a fifth of a
    level, a notch of the wheel)."""
    shown = button.get_ancestor(ActivityMap)
    if shown is None:
        return
    viewport = shown.map.get_viewport()
    zoom = viewport.get_zoom_level()
    whole = math.floor(zoom + 1e-6) + 1 if step > 0 else math.ceil(zoom - 1e-6) - 1
    whole = max(viewport.get_min_zoom_level(), min(viewport.get_max_zoom_level(), whole))
    shown.map.go_to_full_with_duration(viewport.get_latitude(), viewport.get_longitude(), whole,
                                       ZOOM_MS)


def _zoom_in(button) -> None:
    _zoom_by_button(button, 1)


def _zoom_out(button) -> None:
    _zoom_by_button(button, -1)


def _turn_on(button) -> None:
    """Show the Map: the yes for the program, the tiles on this map."""
    shown = button.get_ancestor(ActivityMap)
    set_tiles(True)
    if shown is not None:
        shown.show_tiles(True)


def _fit(widget, _clock) -> bool:
    """Once the map has its size: the whole track in view (a tick before the map is drawn, so
    that no tile is asked for another view)."""
    width, height = widget.map.get_width(), widget.map.get_height()
    if width <= 0 or height <= 0:
        return GLib.SOURCE_CONTINUE
    widget.fit(width, height)
    return GLib.SOURCE_REMOVE


def _dim(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, wrap=True, xalign=0, visible=False)
    label.add_css_class("dim-label")
    return label


class MapView(Gtk.Box):
    """A map in OpenStreetMap's view under layers that openExpress draws: the tiles (after the
    yes given once for the program) behind its lowest layer, their attribution while they
    show, and under the map what keeps them away (an older libshumate, no network; the tiles
    that failed asked again once the network is back). The map of an activity's page and that
    of a GPX file in the question before a copy (gui/course_map.py) are ones. A map adds its
    layers (the lowest as `lowest`), the bounds it shows whole (`box`), its overlays and what
    goes under it, then ends with _below()."""

    __gtype_name__ = "OpenExpressMapView"

    def __init__(self, height: int):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.map = Shumate.Map(height_request=height, hexpand=True)
        viewport = self.map.get_viewport()
        self.source = _map_source()
        viewport.set_reference_map_source(self.source)
        viewport.set_min_zoom_level(1)
        viewport.set_max_zoom_level(MAX_ZOOM)
        self.lowest = None  # the lowest layer drawn by openExpress: the tiles go behind it
        self.box = None  # the bounds the map shows whole (fit)
        self.tiles = None  # the tile layer, while the tiles are on
        self.online = None  # whether the network was there when last told
        self.note = None  # what the map says under it before the yes, if anything
        self.attribution = Gtk.Label(use_markup=True, halign=Gtk.Align.END,
                                     valign=Gtk.Align.END, margin_end=6, margin_bottom=6,
                                     visible=False)
        self.attribution.set_markup(f'<a href="{COPYRIGHT_URL}">'
                                    f'{GLib.markup_escape_text(_("© OpenStreetMap contributors"))}'
                                    "</a>")
        self.attribution.add_css_class("osd")
        self.attribution.add_css_class("caption")
        self.overlay = Gtk.Overlay(child=self.map)
        self.overlay.add_overlay(self.attribution)
        frame = Gtk.Frame(child=self.overlay)
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        self.append(frame)
        self.too_old = _dim(_("The map needs a newer libshumate library to show streets and "
                              "terrain"))
        self.offline = _dim(_("No internet connection: only the parts of the map downloaded "
                              "before are shown"))
        self.monitor = self.monitor_handler = None

    def _below(self) -> None:
        """Under all else, what keeps the tiles away; the map fitted once it has its size, the
        tiles as the yes says."""
        self.append(self.too_old)
        self.append(self.offline)
        self.add_tick_callback(_fit)
        self.show_tiles(tiles_on())

    def fit(self, width: float, height: float) -> None:
        """The whole `box` in view of a map of this size; set, not flown to."""
        self.map.get_viewport().set_zoom_level(fitting_zoom(self.box, width, height))
        self.map.center_on(*center(self.box))

    def show_tiles(self, on: bool) -> None:
        """The tiles under the drawing (with the attribution) or not (with the note)."""
        asks = can_ask()
        on = on and asks
        if on and self.tiles is None:
            self.tiles = TileLayer(self.map.get_viewport())
            self.map.insert_layer_behind(self.tiles, self.lowest)
        elif not on and self.tiles is not None:
            self.tiles.stop()
            self.map.remove_layer(self.tiles)
            self.tiles = None
        if self.note is not None:
            self.note.set_visible(asks and not on)
        self.too_old.set_visible(not asks)
        self.attribution.set_visible(on)
        self._show_network()

    def _show_network(self, *_args) -> None:
        """Without a network, said under the map; once it is back, the tiles that failed asked
        again."""
        online = _network_monitor().get_network_available()
        self.offline.set_visible(self.tiles is not None and not online)
        if online and self.online is False and self.tiles is not None:
            self.tiles.retry()
        self.online = online

    def do_map(self) -> None:
        # Told of the network only while on the screen: the monitor lives on, the map not.
        Gtk.Box.do_map(self)
        if self.monitor_handler is None:
            self.monitor = _network_monitor()
            self.monitor_handler = self.monitor.connect("network-changed", self._show_network)
        self._show_network()

    def do_unmap(self) -> None:
        if self.monitor_handler is not None:
            self.monitor.disconnect(self.monitor_handler)
            self.monitor = self.monitor_handler = None
        if self.tiles is not None:
            self.tiles.let_go()
        Gtk.Box.do_unmap(self)


class ActivityMap(MapView):
    """The map of a track: the tiles (after the yes) under the track (a lap chosen at full
    strength), the markers of its ends and of the chart's cursor above them, the attribution,
    the buttons that zoom it; the mouse wheel scrolls the page, Ctrl and the wheel zoom the map.
    Under it the legend of the track's colours with their switch, the note and its button
    before the yes, or what is missing. Its handlers reach it
    through the widget of their signal."""

    __gtype_name__ = "OpenExpressActivityMap"

    def __init__(self, track: Track):
        super().__init__(MAP_HEIGHT)
        self.track = track
        self.map.update_property([Gtk.AccessibleProperty.LABEL], [_("Map of the track")])
        viewport = self.map.get_viewport()
        self.path = TrackLayer(viewport, track)
        self.map.add_layer(self.path)
        self.lowest = self.path
        ends = Shumate.MarkerLayer.new(viewport)
        for kind, (lat, lon) in ends_of(track):
            marker = _marker(BOTH_SIZE if kind == "start-and-finish" else END_SIZE, _draw_end,
                             kind)
            marker.set_location(lat, lon)
            ends.add_marker(marker)
        self.map.add_layer(ends)
        cursor = Shumate.MarkerLayer.new(viewport)
        self.marker = _marker(CURSOR_SIZE, _draw_cursor)
        self.marker.set_visible(False)
        cursor.add_marker(self.marker)
        self.map.add_layer(cursor)
        self.box = bounds(track.points)
        self.fit(600, MAP_HEIGHT)  # near already; _fit sets it for the map's own size
        # Before libshumate's own handler, which would zoom at every turn of the wheel.
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", _wheel)
        self.map.add_controller(wheel)
        self.hint_timer = None

        self.hint = Gtk.Label(label=_("Ctrl + scroll to zoom"), wrap=True,
                              justify=Gtk.Justification.CENTER, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER, visible=False, can_target=False)
        self.hint.add_css_class("osd")
        self.hint.add_css_class("oe-map-hint")
        zoom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.END,
                       valign=Gtk.Align.START, margin_top=6, margin_end=6)
        zoom.add_css_class("linked")
        for icon, tooltip, handler in (("zoom-in-symbolic", _("Zoom In"), _zoom_in),
                                       ("zoom-out-symbolic", _("Zoom Out"), _zoom_out)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.add_css_class("osd")
            button.connect("clicked", handler)
            zoom.append(button)
        self.overlay.add_overlay(self.hint)
        self.overlay.add_overlay(zoom)
        self.series = None  # the chart's series the track is coloured by
        self.legend = activity_colours.TrackLegend()
        self.legend.switch.connect("notify::active", _colours_switched)
        self.append(self.legend)

        self.note = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.note.append(Gtk.Label(label=_(
            "The map can show streets and terrain under the track, from OpenStreetMap. The "
            "OpenStreetMap server then learns the area of each activity you open. You can turn "
            "this off again in Preferences."), wrap=True, xalign=0))
        # TRANSLATORS: button under the note about OpenStreetMap: turns on the online map of an
        # activity
        button = Gtk.Button(label=_("Show the Map"), halign=Gtk.Align.START)
        button.add_css_class("pill")
        button.connect("clicked", _turn_on)
        self.note.append(button)
        self.append(self.note)
        self._below()

    def colour_by(self, series) -> None:
        """The track coloured by this series of the chart (gui/activity_colours.py) while the
        switch under the map is on; None: no series, no colours and no legend."""
        self.series = series
        on = activity_colours.colours_on()
        self.path.colour(activity_colours.point_colours(self.path.times, series)
                         if series is not None and on else None)
        self.legend.show_series(series, on)

    def show_lap(self, span: tuple[float, float] | None) -> None:
        """The lap chosen (its start and end on the chart's time) at full strength, the rest of
        the track faded; None: the whole track as it is. The view stays."""
        self.path.chosen = span
        self.path.queue_draw()

    def say_ctrl(self) -> None:
        """For a moment, over the map: Ctrl and the wheel zoom it."""
        self.hint.set_visible(True)
        if self.hint_timer is not None:
            GLib.source_remove(self.hint_timer)
        self.hint_timer = GLib.timeout_add(round(HINT_S * 1000), _hide_hint, weakref.ref(self))

    def show_cursor(self, at: float) -> None:
        """The marker where the chart's cursor is; hidden where the track has no record."""
        point = position_at(self.track, at)
        if point is not None:
            self.marker.set_location(*point)
        self.marker.set_visible(point is not None)

    def do_unmap(self) -> None:
        if self.hint_timer is not None:
            GLib.source_remove(self.hint_timer)
            self.hint_timer = None
            self.hint.set_visible(False)
        MapView.do_unmap(self)


def _maps_in(widget):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, MapView):
            yield child
        else:
            yield from _maps_in(child)
        child = child.get_next_sibling()


def visible_page(win):
    """The page the window's navigation shows (Adw.NavigationPage); None without one."""
    navigation = win.navigation
    page = navigation.get_visible_page() if navigation is not None else None
    return page if isinstance(page, Adw.NavigationPage) else None


def tiles_changed(win) -> None:
    """The yes given or taken back in Preferences: the map shown now follows it."""
    page = visible_page(win)
    if page is None:
        return
    on = tiles_on()
    for shown in _maps_in(page):
        shown.show_tiles(on)
