"""The map of an activity (gui/activity_map.py) without a screen: the track from the records with
positions (its time as the chart's), thinned for a long one, its bounds and the zoom that shows
it, where the chart's cursor is on it, the markers of its ends; the tiles a view shows and
where they are drawn (each edge on a pixel of the screen); the tiles of OpenStreetMap asked by
openExpress itself, with its User-Agent, kept in the program's cache as long as the server
allows, asked again with what the cache has, nothing said of a place in the log. No tile is
asked of a server here: the download is a stand-in. Every value is made up."""

import itertools
import logging
import time

import pytest

from openexpress import fit

START = 1_700_000_000  # a Unix time
PNG = b"\x89PNG made-up tile"


def amap():
    from openexpress.gui import activity_map
    return activity_map


def sample(second, lat=None, lon=None):
    return fit.Sample(START + second, latitude=lat, longitude=lon, heart_rate=100)


def activity(samples):
    return fit.ActivityData(fit.FileState.WHOLE, start=START, sport=11, samples=tuple(samples))


# --- the track -------------------------------------------------------------------------------


def test_the_track_has_the_records_with_positions_and_the_chart_s_time():
    """Its time since the first record of the activity, with a position or not."""
    track = amap().track_of(activity([sample(0), sample(5, 51.5, -0.16), sample(6, 51.501, -0.161),
                                      sample(9)]))
    assert track.times == (5, 6)
    assert track.points == ((51.5, -0.16), (51.501, -0.161))
    assert amap().track_of(activity([sample(0), sample(1)])) is None


def test_a_long_track_is_thinned_for_the_map_but_keeps_its_ends():
    points = [(51.5 + i * 1e-6, -0.16) for i in range(28_800)]
    thin = amap().thinned(points)
    assert len(thin) <= amap().PATH_POINTS + 1
    assert thin[0] == points[0] and thin[-1] == points[-1]
    assert amap().thinned(points[:10]) == points[:10]


def extent(box, zoom) -> tuple[float, float]:
    """The width and the height in pixels of the bounds at this zoom."""
    import math

    south, west, north, east = box
    mercator = amap()._mercator
    return ((east - west) / 360 * 256 * 2 ** zoom,
            (mercator(north) - mercator(south)) / (2 * math.pi) * 256 * 2 ** zoom)


@pytest.mark.parametrize(("box", "size", "side"), [
    ((46.50, 12.10, 46.56, 12.12), (920, 300), 1),  # north to south in a wide map: its height
    ((46.50, 12.00, 46.51, 12.10), (360, 300), 0),  # east to west in a narrow one: its width
])
def test_the_track_fills_its_map_but_a_tenth_on_each_side(box, size, side):
    """The zoom is the one at which the track is as large as the map but a tenth of it on each
    side, between two whole zooms as it falls; never larger than the map."""
    zoom = amap().fitting_zoom(box, *size)
    assert zoom != round(zoom)
    shown = extent(box, zoom)
    assert shown[side] == pytest.approx(0.8 * size[side])
    assert shown[1 - side] < 0.8 * size[1 - side]


def test_the_zoom_of_a_very_short_track_and_of_a_point():
    """A track of a few metres, and a point with no movement at all: the close zoom, not the
    closest."""
    m = amap()
    assert m.fitting_zoom((51.5, -0.16, 51.50005, -0.15995), 600, 300) == m.CLOSEST_FIT
    assert m.fitting_zoom((51.5, -0.16, 51.5, -0.16), 600, 300) == m.CLOSEST_FIT


def test_the_bounds_and_the_centre_of_a_track():
    bounds = amap().bounds([(51.5, -0.17), (51.51, -0.15), (51.505, -0.16)])
    assert bounds == (51.5, -0.17, 51.51, -0.15)
    lat, lon = amap().center(bounds)
    assert lat == pytest.approx(51.505, abs=1e-3) and lon == pytest.approx(-0.16)


def test_where_the_chart_s_cursor_is_on_the_track():
    """The nearest record with a position; none in a gap or a pause."""
    track = amap().Track((0, 5, 10, 200), ((51.5, -0.1), (51.6, -0.2), (51.7, -0.3),
                                          (51.8, -0.4)))
    assert amap().position_at(track, 6) == (51.6, -0.2)
    assert amap().position_at(track, -3) == (51.5, -0.1)
    assert amap().position_at(track, 100) is None


def test_the_start_and_the_finish_one_marker_for_a_loop():
    """Apart: the start and the finish where they are; closer than 50 m (a loop): one marker
    for both, at the start."""
    line = amap().Track((0, 600), ((51.5, -0.16), (51.51, -0.16)))
    assert amap().ends_of(line) == [("start", (51.5, -0.16)), ("finish", (51.51, -0.16))]
    loop = amap().Track((0, 300, 600), ((51.5, -0.16), (51.51, -0.16), (51.5003, -0.16)))
    assert amap().ends_of(loop) == [("start-and-finish", (51.5, -0.16))]
    alone = amap().Track((0,), ((51.5, -0.16),))
    assert amap().ends_of(alone) == [("start-and-finish", (51.5, -0.16))]


# --- where the tiles are drawn ----------------------------------------------------------------


def test_the_tiles_of_a_view_in_the_order_they_are_drawn():
    """Rows from the top, each from the left, every tile touching the next: the whole view
    covered, nothing more than a tile around it."""
    places = amap().tile_places(15, 51.5065, -0.1665, 600, 300)
    rows = sorted({p.y for p in places})
    assert [p.y for p in places] == sorted(p.y for p in places)
    for y in rows:
        row = [p for p in places if p.y == y]
        assert [p.x for p in row] == sorted(p.x for p in row)
        assert all(a.x + a.width == b.x for a, b in itertools.pairwise(row))
        assert row[0].x <= 0 < row[0].x + row[0].width and row[-1].x + row[-1].width >= 600
    assert places[0].y <= 0 and places[-1].y + places[-1].height >= 300
    assert {p.key[0] for p in places} == {15}
    assert all(p.width == p.height == 256 for p in places)
    zoom, x, y = places[0].key
    assert (x, y) == (16367, 10895)  # the tile of OpenStreetMap at the view's top left


def test_tiles_across_the_antimeridian_are_those_of_the_other_side():
    keys = {p.key[1] for p in amap().tile_places(3, 0.0, 179.9, 600, 300)}
    assert 0 in keys and 7 in keys and max(keys) < 8


def test_the_tiles_land_on_the_pixels_of_the_screen():
    """Each edge on a pixel of a screen of this scale (the view's centre anywhere)."""
    for scale in (1, 1.25, 1.5, 2):
        for place in amap().tile_places(15, 51.50651234, -0.16653456, 600, 300, scale):
            for edge in (place.x, place.y, place.x + place.width, place.y + place.height):
                assert edge * scale == pytest.approx(round(edge * scale))


@pytest.mark.parametrize(("zoom", "whole"), [(15.4, 15), (15.6, 16), (15.5, 16)])
def test_between_two_zooms_the_tiles_of_the_nearest_one_scaled(zoom, whole):
    """Scaled from 0.71 to 1.41 of their size: sharper than those of the zoom below, which
    would be up to twice as large; their words are as small as 71 %."""
    places = amap().tile_places(zoom, 51.5065, -0.1665, 600, 300)
    assert {p.key[0] for p in places} == {whole}
    assert all(p.width == pytest.approx(256 * 2 ** (zoom - whole), abs=1) for p in places)


# --- the tiles -------------------------------------------------------------------------------


@pytest.mark.parametrize(("header", "seconds"), [
    ("max-age=3473, stale-while-revalidate=604800, stale-if-error=604800", 3473),
    (None, 7 * 24 * 3600), ("no-cache", 0), ("public, max-age=0", 0)])
def test_how_long_a_tile_is_kept_before_it_is_asked_again(header, seconds):
    """As the server's caching header says; a week when it says nothing (the tile policy)."""
    assert amap().freshness(header) == seconds


def test_the_cache_of_tiles_is_brought_to_its_limit_at_the_first_tile_and_every_few(
        monkeypatch):
    import types

    m = amap()
    monkeypatch.setattr(m, "_tiles_stored", itertools.count())
    purged = []
    cache = types.SimpleNamespace(purge_cache_async=lambda *a: purged.append(1))
    for _tile in range(2 * m.PURGE_EVERY + 1):
        m._stored_once_more(cache)
    assert len(purged) == 3  # the first, then after PURGE_EVERY, and again


def test_the_tile_server_is_told_who_asks_and_what_the_cache_has(monkeypatch):
    """openExpress's own User-Agent, and the tag of the tile it has, so that an unchanged one
    is not sent again."""
    asked = []

    class Session:
        def get(self, url, headers, timeout):
            asked.append((url, headers))
            return type("Answer", (), {"status_code": 304, "content": b"",
                                       "headers": {"Cache-Control": "max-age=60"}})()

    monkeypatch.setattr(amap(), "_session", Session)
    fetched = amap()._fetch("https://tile.example/1/2/3.png", '"tag"')
    ((url, headers),) = asked
    assert url == "https://tile.example/1/2/3.png"
    assert headers["User-Agent"] == amap().USER_AGENT
    assert amap().USER_AGENT.startswith("openExpress/") and amap().USER_AGENT.endswith(
        " (+https://github.com/TomaszBojanowski/openExpress)")
    assert headers["If-None-Match"] == '"tag"'
    assert (fetched.status, fetched.max_age) == (304, 60)


def test_the_session_of_the_tiles_is_named_as_the_map():
    """Its own session, never another one's header."""
    assert amap()._session().headers["User-Agent"] == amap().USER_AGENT


def needs_shumate():
    if amap().Shumate is None or not amap().can_ask():
        pytest.skip("libshumate 1.1 or later is not installed")


def asked(source, x=1, y=2, zoom=3):
    """A tile asked of the source as the map asks it; returns the request once it ends."""
    from gi.repository import GLib

    request = source.start_request(x, y, zoom, None)
    end = time.monotonic() + 5
    while not request.props.completed and request.get_error() is None \
            and time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
    return request


def test_a_tile_is_downloaded_once_and_kept_in_the_program_s_cache(monkeypatch):
    from openexpress import paths

    needs_shumate()
    fetched = []

    def fetch(url, etag):
        fetched.append((url, etag))
        return amap().Fetched(200, PNG, '"t1"', 3600)

    monkeypatch.setattr(amap(), "_fetch", fetch)
    folder = paths.cache_dir() / amap().CACHE_FOLDER
    source = amap().TileSource()
    assert not folder.exists()  # nothing made before a tile is asked
    first = asked(source)
    assert first.get_data().get_data() == PNG
    assert fetched == [(amap().TILE_URL.format(z=3, x=1, y=2), None)]
    second = asked(amap().TileSource())
    assert second.get_data().get_data() == PNG and len(fetched) == 1  # from the cache
    assert folder.is_dir()


def test_an_old_tile_is_asked_again_with_its_tag(monkeypatch):
    needs_shumate()
    answers = [amap().Fetched(200, PNG, '"t1"', 0), amap().Fetched(304, None, '"t1"', 3600)]
    fetched = []

    def fetch(url, etag):
        fetched.append(etag)
        return answers.pop(0)

    monkeypatch.setattr(amap(), "_fetch", fetch)
    asked(amap().TileSource())
    again = asked(amap().TileSource())
    assert fetched == [None, '"t1"'] and again.get_data().get_data() == PNG


def test_a_tile_that_cannot_be_downloaded_is_an_error(monkeypatch):
    needs_shumate()
    monkeypatch.setattr(amap(), "_fetch", lambda url, etag: amap().Fetched(None, None, None, 0))
    request = asked(amap().TileSource(), x=4_321, y=2_718, zoom=13)
    assert request.get_error() is not None


@pytest.mark.parametrize(("failure", "why"), [("answer", "HTTP 500"),
                                              ("no connection", "ConnectionError")])
def test_tiles_not_downloaded_are_logged_once_and_name_no_place(monkeypatch, caplog, failure,
                                                                 why):
    """Why, once for as long as it lasts: never the tile's place nor the server's address."""
    import requests

    class Session:
        def get(self, url, headers, timeout):
            if failure == "no connection":
                raise requests.ConnectionError(f"made-up refusal of {url}")
            return type("Answer", (), {"status_code": 500, "content": b"", "headers": {}})()

    monkeypatch.setattr(amap(), "_session", Session)
    monkeypatch.setattr(amap(), "_said", set())
    caplog.set_level(logging.INFO, logger="openexpress.gui.activity_map")
    for _tile in range(2):
        amap()._fetch("https://tile.example/13/4321/2718.png", None)
    said = [r.getMessage() for r in caplog.records if r.name == "openexpress.gui.activity_map"]
    assert said == [f"activity map: tiles not downloaded ({why})"]
    assert not [part for part in ("4321", "2718", "tile.example") if part in caplog.text]


def test_a_short_piece_of_another_colour_is_no_dot_on_the_track():
    """Each piece of one colour ends flat where the next one begins: a piece a pixel long in
    another colour (a heart rate a moment higher) shows no more than its length, no dot over
    its neighbours; the track's own ends stay round."""
    import types

    import cairo

    from openexpress.gui.palette import rgba

    m = amap()
    if not hasattr(m, "TrackLayer"):
        pytest.skip("no libshumate")
    red, blue = "#ff0000", "#0000ff"
    layer = types.SimpleNamespace(edge=rgba("#000000"), edge_width=1.0, track_colour=rgba(red),
                                  colours=[red, red, blue, red, red])
    points = [(10.0, 20.0), (30.0, 20.0), (31.0, 20.0), (32.0, 20.0), (60.0, 20.0)]
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 80, 40)
    cr = cairo.Context(surface)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    m.TrackLayer._stroke(layer, cr, points, [list(range(len(points)))], 8, 1.0)
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()

    def rgb(x, y):
        blue_, green, red_ = data[y * stride + 4 * x:y * stride + 4 * x + 3]
        return red_, green, blue_

    assert rgb(31, 20) == (0, 0, 255)  # the piece itself
    assert rgb(28, 20) == (255, 0, 0) and rgb(35, 20) == (255, 0, 0)  # no dot around it
    assert rgb(7, 20) == (255, 0, 0) and rgb(62, 20) == (255, 0, 0)  # the round ends


@pytest.mark.parametrize("strength", [1.0, 0.5], ids=["full", "faded"])
def test_each_stretch_of_a_coloured_track_is_in_its_own_colour_once(strength):
    """Each stretch between two points in the colour of the point it starts at, whichever
    colour is drawn first, and a faded track (the rest of a lap chosen) blended once: no
    colour of two pieces where they meet."""
    import types

    import cairo

    from openexpress.gui.palette import rgba

    m = amap()
    if not hasattr(m, "TrackLayer"):
        pytest.skip("no libshumate")
    red, blue = "#ff0000", "#0000ff"
    layer = types.SimpleNamespace(edge=rgba("#000000"), edge_width=1.0, track_colour=rgba(red),
                                  colours=[red, red, blue, blue, red, red])
    points = [(10.0 + 10 * i, 20.0) for i in range(6)]
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 80, 40)
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    m.TrackLayer._stroke(layer, cr, points, [list(range(len(points)))], 8, strength)
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()

    def rgb(x):
        blue_, green, red_ = data[20 * stride + 4 * x:20 * stride + 4 * x + 3]
        return red_, green, blue_

    stretches = [rgb(x) for x in (15, 25, 35, 45, 55)]  # one in each stretch
    assert stretches == [stretches[0]] * 2 + [stretches[2]] * 2 + [stretches[0]]
    assert stretches[0][0] > stretches[0][2] and stretches[2][2] > stretches[2][0]


def test_a_bend_where_two_colours_meet_has_no_gap():
    """Two pieces ending flat at a bend: the second one's round join fills its outer side."""
    import types

    import cairo

    from openexpress.gui.palette import rgba

    m = amap()
    if not hasattr(m, "TrackLayer"):
        pytest.skip("no libshumate")
    red, blue = "#ff0000", "#0000ff"
    layer = types.SimpleNamespace(edge=rgba("#000000"), edge_width=0.0, track_colour=rgba(red),
                                  colours=[red, blue, blue])
    points = [(10.0, 30.0), (30.0, 30.0), (30.0, 10.0)]  # right, then up
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 50, 50)
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    m.TrackLayer._stroke(layer, cr, points, [[0, 1, 2]], 8, 1.0)
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()
    blue_, green, red_ = data[31 * stride + 4 * 31:31 * stride + 4 * 31 + 3]  # the outer corner
    assert (red_, green, blue_) == (0, 0, 255)
