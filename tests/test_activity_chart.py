"""The numbers of an activity's chart (gui/activity_chart.py), without a screen: the series the
file has and only those (heart rate, elevation, pace or speed by sport), the time since the
first record, pace smoothed over about 30 s, a gap where a value is missing or the records stop
(a pause), the points brought down to two a column without losing a peak, axes with round
values (the slowest pace of the stops left off its axis), the value at a place of the cursor
and the cursor moved by the keys. Every value is made up."""

import time

import pytest

from openexpress import fit

START = 1_700_000_000  # a Unix time
RUNNING, CYCLING, SWIMMING, TRAINING, WALKING = 1, 2, 5, 10, 11


def chart():
    from openexpress.gui import activity_chart
    return activity_chart


def sample(second, **values):
    return fit.Sample(START + second, **values)


def activity(samples, sport=WALKING, distance=1000.0):
    return fit.ActivityData(fit.FileState.WHOLE, start=START, sport=sport, distance=distance,
                            samples=tuple(samples))


def walk(seconds=120):
    return activity([sample(s, distance=s * 1.2, speed=1.2, heart_rate=100 + s % 20,
                            altitude=100 + s * 0.1) for s in range(seconds)])


def series_of(data):
    return {s.kind: s for s in chart().chart_series(data)}


# --- the series ------------------------------------------------------------------------------


@pytest.mark.parametrize(("sport", "moving"), [(WALKING, "pace"), (RUNNING, "pace"),
                                               (SWIMMING, "swim pace"), (CYCLING, "speed")])
def test_the_series_of_an_activity_with_a_distance(sport, moving):
    data = activity(walk().samples, sport=sport)
    assert [s.kind for s in chart().chart_series(data)] == ["heart rate", "elevation", moving]


def test_a_workout_has_only_the_series_its_file_has():
    """Heart rate only: one series; a distance of 0 m and no speed: no pace; nothing: none."""
    hearts = activity([sample(s, heart_rate=110 + s, distance=0.0, speed=0.0) for s in range(5)],
                      sport=TRAINING, distance=0.0)
    assert [s.kind for s in chart().chart_series(hearts)] == ["heart rate"]
    assert chart().chart_series(activity([sample(s) for s in range(5)], sport=TRAINING,
                                         distance=None)) == []
    assert chart().chart_series(activity([], sport=TRAINING)) == []


def test_the_time_since_the_first_record_and_the_values_in_their_units():
    found = series_of(activity([sample(s, distance=s * 2.5, speed=2.5, heart_rate=150,
                                       altitude=212.4) for s in range(10, 13)],
                               sport=CYCLING))
    assert found["heart rate"].times == (0, 1, 2)
    assert found["heart rate"].values == (150, 150, 150)
    assert found["elevation"].values == (212.4, 212.4, 212.4)
    assert found["speed"].values == pytest.approx((9.0, 9.0, 9.0))  # km/h, as recorded


@pytest.mark.parametrize(("sport", "miles", "kind", "value"), [
    (CYCLING, False, "speed", 9.0), (CYCLING, True, "speed", 9000 / 1609.344),
    (WALKING, False, "pace", 400.0), (WALKING, True, "pace", 1609.344 / 2.5),
    (SWIMMING, False, "swim pace", 40.0), (SWIMMING, True, "swim pace", 91.44 / 2.5)])
def test_the_values_in_the_units_of_the_device(sport, miles, kind, value):
    """A device set to miles and feet: the elevation in feet, the pace a mile or a hundred
    yards, the speed in miles an hour; each series says its units."""
    import dataclasses

    data = activity([sample(s, distance=s * 2.5, speed=2.5, heart_rate=150, altitude=212.4)
                     for s in range(60)], sport=sport)
    statute = "statute" if miles else "metric"
    found = series_of(dataclasses.replace(data, units=fit.Units(statute, statute, statute)))
    assert found["elevation"].values[0] == pytest.approx(212.4 / 0.3048 if miles else 212.4)
    assert found[kind].values[30] == pytest.approx(value)
    assert {s.units.miles for s in found.values()} == {miles}
    assert {s.units.feet for s in found.values()} == {miles}


def test_pace_is_smoothed_over_about_thirty_seconds():
    """A speed that jumps between 1 and 2 m/s each second: the pace of 1.5 m/s, not a saw."""
    data = activity([sample(s, distance=s * 1.5, speed=1.0 if s % 2 else 2.0, heart_rate=120)
                     for s in range(121)], sport=RUNNING)
    pace = series_of(data)["pace"]
    middle = [v for t, v in zip(pace.times, pace.values, strict=True) if 15 <= t <= 105]
    assert all(650 <= v <= 685 for v in middle), (min(middle), max(middle))  # 1000 / 1.5 s/km


def test_pace_is_not_smoothed_over_a_pause():
    data = activity([sample(s, distance=s * 1.0, speed=1.0) for s in range(60)]
                    + [sample(s, distance=s * 1.0, speed=3.0) for s in range(200, 260)],
                    sport=RUNNING)
    found = series_of(data)["pace"]
    pace = dict(zip(found.times, found.values, strict=True))
    assert pace[59] == pytest.approx(1000.0) and pace[200] == pytest.approx(1000 / 3)


def test_no_pace_while_standing_still():
    data = activity([sample(s, distance=10.0, speed=0.1 if s < 30 else 2.0) for s in range(60)],
                    sport=RUNNING)
    pace = series_of(data)["pace"]
    assert pace.values[0] is None and pace.values[-1] == pytest.approx(500.0)


def test_a_speed_worked_out_from_the_distance_when_the_records_do_not_give_it():
    data = activity([sample(s, distance=s * 2.5) for s in range(60)], sport=RUNNING)
    pace = series_of(data)["pace"]
    assert pace.values[30] == pytest.approx(400.0)  # 2.5 m/s: 6:40 /km


def test_a_long_activity_is_ready_for_its_chart_at_once():
    """A hike of 8 h with a record every second: its series in well under a second or two of
    this process's time (not the clock's, which other work on the computer stretches)."""
    data = activity([sample(s, distance=s * 1.2, speed=1.2 + (s % 7) / 10, heart_rate=120,
                            altitude=400 + s % 300) for s in range(8 * 3600)])
    started = time.process_time()
    found = chart().chart_series(data)
    took = time.process_time() - started
    assert len(found) == 3 and took < 2.0, f"{took:.2f} s"


# --- the line: gaps and the points brought down --------------------------------------------


def test_a_value_not_given_and_a_pause_break_the_line():
    """A missing value ends a piece; so do records more than a minute apart (a pause)."""
    series = chart().Series("heart rate", (0, 1, 2, 3, 100, 101), (1, 2, None, 4, 5, 6))
    assert chart().runs(series) == [[(0, 1), (1, 2)], [(3, 4)], [(100, 5), (101, 6)]]


def test_the_points_are_brought_down_without_losing_a_peak():
    """28,800 points to 400 columns: at most two a column, the highest and the lowest kept."""
    run = [(t, 190 if t == 12_345 else 40 if t == 20_000 else 100) for t in range(28_800)]
    points = chart().brought_down(run, 0, 28_799, 400)
    assert len(points) <= 800
    values = [v for _t, v in points]
    assert max(values) == 190 and min(values) == 40
    times = [t for t, _v in points]
    assert times == sorted(times)


# --- the axes --------------------------------------------------------------------------------


def axis(kind, values, count=4):
    return chart().value_axis(chart().Series(kind, tuple(range(len(values))), tuple(values)),
                              count)


def test_the_axes_have_round_values():
    a = axis("heart rate", [97, 140, 183])
    assert (a.low, a.high, a.step) == (75, 200, 25)
    assert a.ticks == (75, 100, 125, 150, 175, 200)
    a = axis("speed", [18.4, 31.7])
    assert (a.low, a.high, a.step) == (15, 35, 5)
    a = axis("speed", [3.1, 4.4])
    assert (a.low, a.high, a.step) == (3.0, 4.5, 0.5)
    assert a.ticks == (3.0, 3.5, 4.0, 4.5)
    a = axis("pace", [760, 860])  # 12:40 to 14:20 /km: half minutes
    assert (a.low, a.high, a.step) == (750, 870, 30)


def test_a_flat_series_still_has_an_axis():
    """An elevation that does not change (a workout indoors): ten metres around it, in whole
    metres."""
    a = axis("elevation", [120, 120, 120])
    assert (a.low, a.high, a.step) == (115, 125, 5)


def test_the_stops_are_left_off_the_axis_of_pace():
    """The slowest twentieth (slowing down to a stop) does not squeeze the rest of the pace."""
    a = axis("pace", [600] * 95 + [3000] * 5)
    assert 600 <= a.high <= 660


def test_the_time_axis_runs_from_the_start_to_the_end():
    a = chart().time_axis(4790, 8)
    assert (a.low, a.high, a.step) == (0, 4790, 600)
    assert a.ticks == tuple(range(0, 4201, 600))
    assert chart().time_axis(100, 8).ticks == tuple(range(0, 91, 15))


def test_pace_is_drawn_with_the_faster_on_top():
    assert chart().faster_on_top("pace") and chart().faster_on_top("swim pace")
    assert not any(chart().faster_on_top(k) for k in ("heart rate", "elevation", "speed"))


# --- the cursor ------------------------------------------------------------------------------


SERIES = ("heart rate", (0, 5, 10, 200, 205), (100, 110, None, 120, 125))


def test_the_value_at_a_place_of_the_cursor():
    """The nearest record; none in a gap or a pause (no reading)."""
    series = chart().Series(*SERIES)
    assert chart().value_at(series, 6) == (5, 110)
    assert chart().value_at(series, 9) == (10, None)
    assert chart().value_at(series, 100) == (100, None)  # in a pause
    assert chart().value_at(series, -5) == (0, 100)
    assert chart().value_at(series, 999) == (205, 125)


def test_the_keys_move_the_cursor_over_a_pause_too():
    """A hundredth of the activity a key, to the next record at least that far; Home and End
    to its first and last."""
    series = chart().Series(*SERIES)
    moved = chart().cursor_after
    assert moved(series, 10, "right") == 200 and moved(series, 200, "left") == 10
    assert moved(series, 0, "right") == 5 and moved(series, 205, "right") == 205
    assert moved(series, 120, "home") == 0 and moved(series, 120, "end") == 205
    assert moved(series, None, "right") == 0 and moved(series, None, "left") == 205


# --- stops -----------------------------------------------------------------------------------


def records(parts, sport=WALKING):
    """Records each second from parts: (seconds, speed in m/s, climb in m/s); the speed of a
    walk or a run wavers by a tenth of a metre a second."""
    samples, second, distance, altitude = [], 0, 0.0, 500.0
    for length, speed, climb in parts:
        for i in range(length):
            v = speed + (0.1 if i % 3 == 0 else -0.05) if speed > 0.5 else speed
            distance += v
            altitude += climb
            samples.append(sample(second, distance=distance, speed=v, heart_rate=120,
                                  altitude=altitude))
            second += 1
    return activity(samples, sport=sport, distance=distance)


def a_walk_with_stops():
    """A walk at 1.45 m/s (11:30 /km) with fourteen stops of 15 to 60 s, slowing down and
    starting again in three seconds around each."""
    parts = []
    for stop in (15, 40, 60, 25, 30, 45, 20, 55, 35, 15, 50, 30, 25, 40):
        parts += [(200, 1.45, 0.0), (3, 0.7, 0.0), (stop, 0.04, 0.0), (3, 0.7, 0.0)]
    return records([*parts, (200, 1.45, 0.0)])


def walking_and_stopped(data):
    """The pace of each record while walking, and of each record in the middle of a stop."""
    pace = series_of(data)["pace"]
    speeds = [s.speed for s in data.samples]
    walking = [v for v, s in zip(pace.values, speeds, strict=True) if s > 1.3]
    stopped = [v for i, v in enumerate(pace.values)
               if all(s < 0.1 for s in speeds[max(0, i - 5):i + 6])]
    return walking, stopped


def test_a_stop_is_a_gap_in_the_pace_and_leaves_the_walk_as_it_was():
    """No pace in the middle of a stop, and none of the stop's zeros in the pace walked next to
    it, nor of the slowing down before it: the walk stays between 10 and 12:30 min/km, no
    tooth down to the axis' end."""
    data = a_walk_with_stops()
    walking, stopped = walking_and_stopped(data)
    assert stopped and set(stopped) == {None}
    values = [v for v in series_of(data)["pace"].values if v is not None]
    assert all(600 <= v <= 750 for v in values), (min(values), max(values))
    assert all(v is not None for v in walking)


def test_the_axis_of_pace_is_the_walk_s_not_the_stops():
    """Typical pace takes most of the height: the axis from the moving records only."""
    pace = series_of(a_walk_with_stops())["pace"]
    a = chart().value_axis(pace, 4)
    assert 540 <= a.low <= 660 and 720 <= a.high <= 900, (a.low, a.high)


def test_a_slow_climb_is_no_stop():
    """A mountain hike: the climb at 0.4 m/s forward, a very steep part at 0.12 m/s forward
    but 0.1 m/s up, keep their pace; a rest on the way and the summit stop are gaps."""
    data = records([(600, 1.3, 0.0), (1800, 0.4, 0.15), (90, 0.0, 0.0), (600, 0.12, 0.1),
                    (720, 0.0, 0.0), (1800, 1.0, -0.15)], sport=17)
    pace = series_of(data)["pace"]
    at = dict(zip(pace.times, pace.values, strict=True))
    assert at[1500] == pytest.approx(1000 / 0.4, rel=0.15)  # the climb: about 42 min/km
    assert at[2800] == pytest.approx(1000 / 0.12, rel=0.15)  # the very steep part
    assert at[2445] is None and at[3400] is None  # a rest, the summit
    assert at[4800] == pytest.approx(1000.0, rel=0.15)  # down at 1 m/s
    assert chart().value_axis(pace, 3).low > 0  # a pace of 0:00 means nothing


def test_a_minute_of_walking_in_a_run_is_no_stop():
    """A run at 3.3 m/s with a minute walked at 1.4 m/s and a stop at the lights: the walk keeps
    its pace, the lights are a gap, and the axis of pace never starts at 0:00."""
    data = records([(420, 3.3, 0.0), (60, 1.4, 0.0), (420, 3.3, 0.0), (2, 1.2, 0.0),
                    (25, 0.03, 0.0), (2, 1.2, 0.0), (300, 3.3, 0.0)], sport=RUNNING)
    pace = series_of(data)["pace"]
    at = dict(zip(pace.times, pace.values, strict=True))
    assert at[450] == pytest.approx(1000 / 1.4, rel=0.15)
    assert at[915] is None
    assert chart().value_axis(pace, 3).low > 0


# --- moving time -------------------------------------------------------------------------------


def test_the_moving_time_is_the_time_of_the_records_that_move():
    """The walk with fourteen stops (485 s of 3569): each second from a record of 0.2 m/s or
    more, the slowing down around each stop too; not the chart's stops, which a window of
    seconds smooths. The last record begins no second."""
    data = a_walk_with_stops()
    assert len(data.samples) == 3569
    assert chart().moving_time(data) == 3569 - 485 - 1


def test_the_moving_time_leaves_out_a_pause():
    """Records more than a minute apart: the time between them is no moving."""
    samples = [sample(s, distance=s * 1.2, speed=1.2) for s in range(60)]
    samples += [sample(s, distance=72 + (s - 200) * 1.2, speed=1.2) for s in range(200, 260)]
    assert chart().moving_time(activity(samples)) == 118


def test_a_slow_steep_climb_at_no_speed_is_no_moving():
    """As Garmin Connect counts it: a climb the device records at 0.4 m/s forward is moving; a
    very steep one it records at 0 m/s, however fast the altitude rises, is not."""
    data = records([(600, 1.3, 0.0), (1800, 0.4, 0.15), (90, 0.0, 0.0), (600, 0.0, 0.1)],
                   sport=17)
    assert chart().moving_time(data) == 600 + 1800


def test_no_moving_time_without_a_distance_or_speeds():
    workout = activity([sample(s, heart_rate=100) for s in range(60)], sport=TRAINING,
                       distance=0.0)
    assert chart().moving_time(workout) is None
    no_speed = activity([sample(s, heart_rate=100) for s in range(60)], distance=1000.0)
    assert chart().moving_time(no_speed) is None
    assert chart().moving_time(activity([], distance=1000.0)) is None
