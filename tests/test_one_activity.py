"""What the page of one activity shows on top of it (one_activity.py and its words in
gui/words.py): from its file or from its line of the activity journal, its kind, when it started
on the clock of the place it was, its numbers in the units of its device, the lowest and highest
point with the elevation profile, the heart rate and the share of each zone. Made-up activities
only."""

from datetime import UTC, datetime

import pytest

NB = " "
START = int(datetime(2026, 10, 21, 7, 14, tzinfo=UTC).timestamp())  # 09:14 two hours east


def data(**fields):
    from openexpress import fit

    found = {"start": START, "utc_offset": 7200, "sport": 1, "sub_sport": 3, "distance": 21_300.0,
             "timer_time": 10_268.0, "elapsed_time": 10_400.0, "ascent": 640}
    found.update(fields)
    return fit.ActivityData(fit.FileState.WHOLE, **found)


def entry(**fields) -> dict:
    found = {"v": 1, "kind": "activity", "key": "made up", "file": {"name": "a.fit", "size": 1},
             "from": "device", "start": START, "utc_offset": 7200, "sport": 1, "sub_sport": 3,
             "timer_time": 10_268.0, "elapsed_time": 10_400.0, "distance": 21_300.0,
             "ascent": 640, "min_altitude": 38.0, "max_altitude": 212.0,
             "avg_heart_rate": 148, "max_heart_rate": 176, "zones": [0, 600, 3100, 3800, 2100, 400],
             "avg_speed": None, "calories": 1500, "has_track": True, "has_heart_rate": True,
             "units": {"distance": "metric", "elevation": "metric", "speed": "metric"},
             "shape": {"width": 900, "height": 400, "points": [[0, 0], [1000, 400], [0, 0]]},
             "profile": [40, 80, 212, 38]}
    found.update(fields)
    return found


def test_from_a_file():
    """The page's view of a file: its numbers, the speed worked out without one in the file, the
    lowest and highest point from its records when its sessions do not say, its profile along
    the distance, its heart rate and zones; no shape (the map draws the track)."""
    from openexpress import fit, one_activity

    samples = tuple(fit.Sample(START + n, distance=n * 100.0, altitude=100.0 + (n % 3) * 50,
                               heart_rate=140) for n in range(10))
    zones = fit.HeartRateZones(times=(10.0, 20.0, 30.0, 40.0, 0.0, 0.0),
                               bounds=(100, 120, 140, 160, 180, 200))
    shown = one_activity.of_file(data(samples=samples, zones=zones, avg_heart_rate=141,
                                      max_heart_rate=150, units=fit.Units("statute", "statute",
                                                                          "statute")))
    assert (shown.sport, shown.sub_sport, shown.start, shown.utc_offset) == (1, 3, START, 7200)
    assert shown.speed == pytest.approx(21_300 / 10_268)
    assert (shown.lowest, shown.highest) == (100.0, 200.0)
    assert shown.profile and len(shown.profile) >= 2
    assert (shown.avg_heart_rate, shown.max_heart_rate) == (141, 150)
    assert shown.zones == (10.0, 20.0, 30.0, 40.0, 0.0, 0.0)
    assert shown.shape is None and shown.units.miles and shown.units.feet
    alone = one_activity.of_file(data(min_altitude=12.0, max_altitude=99.0, avg_speed=3.0))
    assert (alone.lowest, alone.highest, alone.speed) == (12.0, 99.0, 3.0)
    assert alone.profile is None and alone.zones is None and not alone.units.miles


def test_from_the_journal():
    """The page's view of an activity whose file is gone: what its line of the journal kept,
    its shape among it."""
    from openexpress import one_activity

    shown = one_activity.of_journal(entry())
    assert (shown.distance, shown.time, shown.ascent) == (21_300.0, 10_268.0, 640)
    assert (shown.lowest, shown.highest, shown.profile) == (38.0, 212.0, (40, 80, 212, 38))
    assert shown.zones == (0, 600, 3100, 3800, 2100, 400)
    assert shown.shape == ((0, 0), (1000, 400), (0, 0))
    assert shown.speed == pytest.approx(21_300 / 10_268) and not shown.units.miles
    bare = one_activity.of_journal(entry(has_track=False, units=None, zones=None,
                                         avg_heart_rate=None, max_heart_rate=None,
                                         timer_time=None, profile=None))
    assert bare.shape is None and bare.zones is None and bare.time == 10_400.0
    assert bare.profile is None and not bare.units.feet


def numbers(shown) -> list[tuple[str, str]]:
    from openexpress.gui import words

    return [(name, template.format(**values)) for name, template, values
            in words.overview_numbers(shown)]


@pytest.mark.parametrize(("fields", "units", "expected"), [
    ({}, "metric", [("Distance", "21.30 km"), ("Time", "2:51:08"),
                    ("Average Pace", "8:02 /km"), ("Total Ascent", "640 m")]),
    ({}, "statute", [("Distance", "13.24 mi"), ("Time", "2:51:08"),
                     ("Average Pace", "12:56 /mi"), ("Total Ascent", "2100 ft")]),
    ({"sport": 2, "sub_sport": 7, "distance": 40_000.0, "timer_time": 3600.0, "ascent": 0},
     "metric", [("Distance", "40.00 km"), ("Time", "1:00:00"), ("Average Speed", "40.0 km/h")]),
    ({"sport": 2, "sub_sport": 7, "distance": 40_000.0, "timer_time": 3600.0, "ascent": None},
     "statute", [("Distance", "24.85 mi"), ("Time", "1:00:00"), ("Average Speed", "24.9 mph")]),
    ({"sport": 5, "sub_sport": 17, "distance": 1500.0, "timer_time": 1800.0, "ascent": None},
     "metric", [("Distance", "1500 m"), ("Time", "30:00"), ("Average Pace", "2:00 /100 m")]),
    ({"sport": 5, "sub_sport": 17, "distance": 1371.6, "timer_time": 1800.0, "ascent": None},
     "statute", [("Distance", "1500 yd"), ("Time", "30:00"), ("Average Pace", "2:00 /100 yd")]),
    ({"sport": 10, "sub_sport": 20, "distance": None, "timer_time": 2700.0, "ascent": None,
      "calories": 320, "rest_time": 600.0},
     "metric", [("Time", "45:00"), ("Calories", "320 kcal"), ("Rest Time", "10:00")]),
    ({"sport": 10, "sub_sport": 26, "distance": 0.0, "timer_time": None, "elapsed_time": 1200.0,
      "ascent": 12, "calories": None},
     "metric", [("Time", "20:00"), ("Total Ascent", "12 m")]),
])
def test_the_numbers_on_top_in_the_units_of_the_device(fields, units, expected):
    """With a distance: the distance, the time, the pace or speed as the sport tells it and the
    ascent; without one (a workout, also one of 0 m): the time, the calories and the rest time
    of a strength workout, and the ascent; in the units the device shows; each left out when
    not given, an ascent of 0 too."""
    from openexpress import fit, one_activity

    shown = one_activity.of_file(data(units=fit.Units(units, units, units), **fields))
    assert numbers(shown) == [(name, value.replace(" ", NB)) for name, value in expected]


def test_the_numbers_of_a_line_of_the_journal_are_those_of_its_file():
    from openexpress import fit, one_activity

    kept = numbers(one_activity.of_journal(entry()))
    read = numbers(one_activity.of_file(data(units=fit.Units("metric", "metric", "metric"))))
    assert kept == read


@pytest.mark.parametrize(("offset", "expected"), [
    (7200, "2026-10-21 · 09:14 · fēnix 8"), (-18_000, "2026-10-21 · 02:14 · fēnix 8"),
    (-28_800, "2026-10-20 · 23:14 · fēnix 8")])
def test_the_title_and_when_on_the_clock_of_the_place_it_was(offset, expected):
    """The kind with its sub sport; the date in digits and the time on the clock the device
    had where it was, then the device."""
    from openexpress import one_activity
    from openexpress.gui import words

    shown = one_activity.of_journal(entry(utc_offset=offset))
    assert words.overview_title(shown) == "Trail running"
    assert words.overview_when(shown, "fēnix 8") == expected.replace(" · ", f"{NB}· ")


def test_when_without_the_clock_of_the_place_is_this_computer_s(monkeypatch):
    from openexpress import one_activity
    from openexpress.gui import words

    monkeypatch.setenv("TZ", "Europe/Warsaw")
    import time
    time.tzset()
    try:
        shown = one_activity.of_journal(entry(utc_offset=None))
        assert words.overview_when(shown, "") == f"2026-10-21{NB}· 09:14"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


@pytest.mark.parametrize(("feet", "lowest", "highest", "expected"), [
    (False, 38.4, 212.0, ["38 m lowest", "212 m highest"]),
    (True, 38.4, 212.0, ["126 ft lowest", "696 ft highest"]),
    (False, -12.0, 4806.0, ["−12 m lowest", "4806 m highest"]),
])
def test_the_lowest_and_highest_point(feet, lowest, highest, expected):
    from openexpress import one_activity
    from openexpress.gui import words

    units = {"distance": "metric", "elevation": "statute" if feet else "metric"}
    shown = one_activity.of_journal(entry(min_altitude=lowest, max_altitude=highest,
                                          units=units))
    assert words.elevation_range(shown) == [e.replace(" ", NB, 1) for e in expected]


def test_no_lowest_and_highest_without_them():
    from openexpress import one_activity
    from openexpress.gui import words

    assert words.elevation_range(one_activity.of_journal(entry(min_altitude=None))) == []


def test_the_heart_rate_average_and_highest():
    from openexpress import one_activity
    from openexpress.gui import words

    assert words.heart_rate_words(one_activity.of_journal(entry())) == [
        f"148{NB}bpm average", f"176{NB}bpm highest"]
    assert words.heart_rate_words(one_activity.of_journal(entry(max_heart_rate=None))) == [
        f"148{NB}bpm average"]
    assert words.heart_rate_words(one_activity.of_journal(entry(avg_heart_rate=None,
                                                                max_heart_rate=None))) == []


@pytest.mark.parametrize(("times", "expected"), [
    ((0, 600, 3100, 3800, 2100, 400), [(1, "6%"), (2, "31%"), (3, "38%"), (4, "21%"),
                                       (5, "4%")]),
    ((1000, 1000, 1000, 1000, 1000, 0), [(0, "20%"), (1, "20%"), (2, "20%"), (3, "20%"),
                                         (4, "20%"), (5, "0%")]),
    ((0, 0, 0, 0, 0, 0), []),
])
def test_the_share_of_each_zone(times, expected):
    """Zones 1 to 5 with their share of the time in all; the time below zone 1 before them only
    when there is some; nothing when no time is kept."""
    from openexpress.gui import words

    assert words.zone_shares(times) == expected


def test_a_share_in_percent():
    from openexpress.gui import words

    assert words.percent_text(38) == "38%"


@pytest.fixture
def polish():
    from openexpress import i18n

    i18n.setup("pl")
    yield
    i18n.setup("en")


def test_the_top_of_the_page_in_polish(polish):
    """The new words in Polish, the numbers with a decimal comma, every number and its unit
    kept on one line."""
    from openexpress import fit, one_activity
    from openexpress.gui import words

    shown = one_activity.of_file(data(units=fit.Units("statute", "statute", "statute"),
                                      min_altitude=38.0, max_altitude=212.0,
                                      avg_heart_rate=148, max_heart_rate=176))
    assert numbers(shown) == [("Dystans", f"13,24{NB}mi"), ("Czas", "2:51:08"),
                              ("Średnie tempo", f"12:56{NB}/mi"),
                              ("Całkowity wznios", f"2100{NB}ft")]
    assert words.elevation_range(shown) == [f"najniżej 125{NB}ft", f"najwyżej 696{NB}ft"]
    assert words.heart_rate_words(shown) == [f"średnie 148{NB}bpm", f"najwyższe 176{NB}bpm"]
    assert words.overview_title(shown) == f"Bieganie w{NB}terenie"
    assert words.zone_shares((0, 1, 1, 1, 1, 0)) == [(1, "25%"), (2, "25%"), (3, "25%"),
                                                     (4, "25%"), (5, "0%")]
    assert words.shape_only_text() == "Sam kształt, bez mapy pod spodem"
    assert words.from_journal_text().startswith("Pliku tej aktywności nie ma już")
