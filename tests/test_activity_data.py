"""What an activity file holds for its page (fit.activity_data): the series of its records
(time, distance, speed, heart rate, altitude, position), its laps and what its sessions say;
a value a record does not give is a gap, never a zero. Every file is made up from the public
FIT protocol."""

import time

import pytest
from fakes import FIT_HEART_RECORD, FIT_SESSION, FitData, FitDefinition, fit_file

from openexpress import fit

START = 1_100_000_000  # a time of the FIT protocol
UNIX = START + fit.FIT_EPOCH_OFFSET

# timestamp, position, enhanced altitude, heart rate, distance, enhanced speed
RECORD = FitDefinition(1, 20, ((253, 0x86), (0, 0x85), (1, 0x85), (78, 0x86), (3, 0x02),
                               (5, 0x86), (73, 0x86)))
# timestamp, altitude and speed (the fields without "enhanced"), heart rate
PLAIN_RECORD = FitDefinition(1, 20, ((253, 0x86), (2, 0x84), (3, 0x02), (6, 0x84)))
HEART_RECORD = FIT_HEART_RECORD
# timestamp, start, elapsed and timer time, distance, calories, average speed and its enhanced
# field, average and maximum heart rate, ascent
LAP = FitDefinition(3, 19, ((253, 0x86), (2, 0x86), (7, 0x86), (8, 0x86), (9, 0x86),
                            (11, 0x84), (13, 0x84), (110, 0x86), (15, 0x02), (16, 0x02),
                            (21, 0x84)))
SESSION = FIT_SESSION
FILE_ID = (FitDefinition(0, 0, ((0, 0x00), (4, 0x86))), FitData(0, (4, START)))


def semicircles(degrees: float) -> int:
    return round(degrees * 2**31 / 180)


def altitude(metres: float) -> int:
    """The FIT value of an altitude, in steps of 0.2 m."""
    return round((metres + 500) * 5)


def record(second: int, metres: float, heart_rate: int | None = 140) -> FitData:
    """A record of a run going north from 52°N 21°E at 2.5 m/s."""
    return FitData(1, (START + second, semicircles(52 + second * 2.5e-5), semicircles(21.0),
                       altitude(100 + second * 0.2), heart_rate, round(metres * 100),
                       2500))


def run(*, records=None, laps=None, sessions=None, bad_crc=False) -> bytes:
    records = [record(s, s * 2.5) for s in range(4)] if records is None else records
    laps = [FitData(3, (START + 2, START, 2000, 2000, 500, 1, 2500, 2500, 140, 141, 0)),
            FitData(3, (START + 4, START + 2, 2000, 2000, 500, 1, 2500, 2500, 142, 145, 1))
            ] if laps is None else laps
    sessions = [FitData(2, (START, 1, 0, 4000, 4000, 1000, 2, 2500, 2500, 141, 145, 1))
                ] if sessions is None else sessions
    return fit_file(*FILE_ID, RECORD, *records, LAP, *laps, SESSION, *sessions, bad_crc=bad_crc)


def test_the_series_of_a_run():
    data = fit.activity_data(run())
    assert data.state is fit.FileState.WHOLE
    first, last = data.samples[0], data.samples[-1]
    assert [s.time for s in data.samples] == [UNIX, UNIX + 1, UNIX + 2, UNIX + 3]
    assert last.distance == pytest.approx(7.5) and last.speed == pytest.approx(2.5)
    assert first.heart_rate == 140 and last.altitude == pytest.approx(100.6)
    assert first.latitude == pytest.approx(52.0, abs=1e-6)
    assert first.longitude == pytest.approx(21.0, abs=1e-6)
    assert last.latitude == pytest.approx(52 + 3 * 2.5e-5, abs=1e-6)


def test_what_its_session_says():
    data = fit.activity_data(run())
    assert (data.start, data.sport) == (UNIX, 1)
    assert data.elapsed_time == pytest.approx(4.0) and data.timer_time == pytest.approx(4.0)
    assert data.distance == pytest.approx(10.0) and data.ascent == 1
    assert data.avg_speed == pytest.approx(2.5) and data.calories == 2
    assert (data.avg_heart_rate, data.max_heart_rate) == (141, 145)


def test_its_laps():
    laps = fit.activity_data(run()).laps
    assert [lap.start for lap in laps] == [UNIX, UNIX + 2]
    assert [lap.distance for lap in laps] == [pytest.approx(5.0), pytest.approx(5.0)]
    assert [lap.timer_time for lap in laps] == [pytest.approx(2.0), pytest.approx(2.0)]
    assert [lap.elapsed_time for lap in laps] == [pytest.approx(2.0), pytest.approx(2.0)]
    assert [(lap.avg_heart_rate, lap.max_heart_rate) for lap in laps] == [(140, 141),
                                                                          (142, 145)]
    assert [lap.avg_speed for lap in laps] == [pytest.approx(2.5), pytest.approx(2.5)]
    assert [(lap.ascent, lap.calories) for lap in laps] == [(0, 1), (1, 1)]


def test_the_enhanced_fields_come_first_and_the_others_stand_in():
    """The average speed of a lap and a session from their enhanced fields; the speed and the
    altitude of a record from the plain ones when the enhanced are not there."""
    laps = [FitData(3, (START + 2, START, 2000, 2000, 500, 1, None, 3100, 140, 141, 0))]
    sessions = [FitData(2, (START, 1, 0, 4000, 4000, 1000, 2, 2400, 3200, 141, 145, 1))]
    data = fit.activity_data(run(laps=laps, sessions=sessions))
    assert data.laps[0].avg_speed == pytest.approx(3.1)
    assert data.avg_speed == pytest.approx(3.2)
    plain = fit.activity_data(fit_file(*FILE_ID, PLAIN_RECORD,
                                       FitData(1, (START, altitude(250), 120, 1800))))
    (sample,) = plain.samples
    assert sample.altitude == pytest.approx(250.0) and sample.speed == pytest.approx(1.8)
    assert (sample.latitude, sample.longitude, sample.distance) == (None, None, None)


def test_a_workout_without_positions_or_distance():
    """Heart rate and time only: no position, no distance, no laps."""
    data = fit.activity_data(fit_file(
        *FILE_ID, HEART_RECORD, *(FitData(1, (START + s, 100 + s)) for s in range(3)),
        SESSION, FitData(2, (START, 10, 20, 1800_000, 1800_000, None, 250, None, None, 101,
                             102, None))))
    assert {s.latitude for s in data.samples} == {None} and data.laps == ()
    assert [s.heart_rate for s in data.samples] == [100, 101, 102]
    assert {s.distance for s in data.samples} == {None}
    assert (data.sport, data.calories) == (10, 250)
    assert (data.distance, data.avg_speed, data.ascent) == (None, None, None)
    assert data.timer_time == pytest.approx(1800.0)


def test_a_value_not_given_is_a_gap():
    data = fit.activity_data(run(records=[record(0, 0.0), record(1, 2.5, heart_rate=None),
                                          record(2, 5.0)]))
    assert [s.heart_rate for s in data.samples] == [140, None, 140]


def test_a_pause_keeps_the_times_of_the_records():
    """The time stands still during a pause: the records after it keep their own times; the
    session says the elapsed time with the pause and the timer time without."""
    sessions = [FitData(2, (START, 1, 0, 64_000, 4000, 1000, 2, 2500, 2500, 141, 145, 1))]
    data = fit.activity_data(run(records=[record(0, 0.0), record(1, 2.5), record(61, 5.0),
                                          record(62, 7.5)], sessions=sessions))
    assert [s.time - UNIX for s in data.samples] == [0, 1, 61, 62]
    assert data.elapsed_time == pytest.approx(64.0) and data.timer_time == pytest.approx(4.0)


def test_compressed_timestamps_give_each_record_its_time():
    """After a record with its time, records with a compressed-timestamp header: the offset
    replaces the lowest five bits of the time before it, over the 32 s boundary too."""
    base = START - START % 32 + 30  # two seconds before a multiple of 32
    records = [FitData(1, (base, 120)),
               FitData(1, (None, 121), time_offset=(base + 1) % 32),
               FitData(1, (None, 122), time_offset=(base + 3) % 32)]
    data = fit.activity_data(fit_file(*FILE_ID, HEART_RECORD, *records))
    assert [s.time - fit.FIT_EPOCH_OFFSET for s in data.samples] == [base, base + 1, base + 3]
    assert [s.heart_rate for s in data.samples] == [120, 121, 122]


def test_one_session_without_its_timer_time_has_its_average_heart_rate():
    """One session's averages are its own as it gives them, its heart rate's as the others:
    only several are weighted by their timer times."""
    sessions = [FitData(2, (START, 1, 0, 4000, None, 1000, 2, 2500, 2500, 141, 145, 1))]
    data = fit.activity_data(run(sessions=sessions))
    assert data.timer_time is None
    assert data.avg_heart_rate == 141


def test_several_sessions_together():
    """A multisport activity: its sport is multisport, its times, distance, ascent and
    calories added up, its average speed over all of it, its maximum heart rate the
    highest."""
    sessions = [FitData(2, (START, 2, 0, 3_600_000, 3_600_000, 3_000_000, 600, None, 8333,
                            130, 160, 200)),
                FitData(2, (START + 3600, 1, 0, 1_800_000, 1_800_000, 600_000, 400, None,
                            3333, 150, 175, 50))]
    data = fit.activity_data(run(sessions=sessions))
    assert data.sport == fit.SPORT_MULTISPORT
    assert data.timer_time == pytest.approx(5400.0) and data.distance == pytest.approx(36000.0)
    assert (data.ascent, data.calories, data.max_heart_rate) == (250, 1000, 175)
    assert data.avg_speed == pytest.approx(36000 / 5400)
    assert data.avg_heart_rate == round((130 * 3600 + 150 * 1800) / 5400)


def test_a_file_cut_short_gives_what_reads():
    """A file the device was still writing: said so, with the records read up to where it
    breaks off."""
    whole = run()
    data = fit.activity_data(whole[:len(whole) - 120])
    assert data.state is fit.FileState.STILL_BEING_WRITTEN
    assert 0 < len(data.samples) <= 4


def test_a_damaged_file_gives_what_reads():
    data = fit.activity_data(run(bad_crc=True))
    assert data.state is fit.FileState.DAMAGED and len(data.samples) == 4


def test_what_is_no_fit_file_is_an_error():
    with pytest.raises(fit.FitError):
        fit.activity_data(b"not a FIT file at all")


def test_a_long_activity_is_read_in_a_few_seconds():
    """A hike of 8 h with a record every second (28,800 records): its page waits for this. The
    time is this process's, not the clock's, which other work on the computer stretches."""
    records = [record(s, s * 1.2) for s in range(8 * 3600)]
    data = run(records=records)
    started = time.process_time()
    read = fit.activity_data(data)
    took = time.process_time() - started
    assert len(read.samples) == 8 * 3600
    assert took < 5.0, f"{took:.2f} s"


# --- heart rate zones ------------------------------------------------------------------------

# time_in_zone: timestamp, the message it refers to and its index, the time in each zone (an
# array of 7: below zone 1, zones 1-5, above the top bound), the zones' high bounds (6), how
# they are worked out, the maximum, resting and threshold heart rate
TIME_IN_ZONE = FitDefinition(4, 216, ((253, 0x86), (0, 0x84), (1, 0x84), (2, 0x86, 28),
                                      (6, 0x02, 6), (10, 0x00), (11, 0x02), (12, 0x02),
                                      (13, 0x02)))
BOUNDS = (120, 140, 155, 165, 178, 190)  # made-up


def zones_of(*, times=(600, 300, 200, 100, 50, 30, 20), refers=18, index=0, bounds=BOUNDS,
             calc=3) -> FitData:
    return FitData(4, (START + 4, refers, index,
                       tuple(None if t is None else t * 1000 for t in times), bounds, calc,
                       190, 55, 165))


def with_zones(*zones, sessions=None) -> bytes:
    sessions = [FitData(2, (START, 1, 0, 4000, 4000, 1000, 2, 2500, 2500, 141, 145, 1))
                ] if sessions is None else sessions
    return fit_file(*FILE_ID, RECORD, *[record(s, s * 2.5) for s in range(4)], SESSION,
                    *sessions, TIME_IN_ZONE, *zones)


def test_the_heart_rate_zones_of_the_session():
    """As the device worked them out: the time in each zone, the time above the top bound
    counted in zone 5, the bounds, how they are worked out and from what."""
    zones = fit.activity_data(with_zones(zones_of())).zones
    assert zones.times == (600, 300, 200, 100, 50, 50)
    assert zones.bounds == BOUNDS
    assert zones.method == "threshold"
    assert (zones.max_heart_rate, zones.threshold_heart_rate) == (190, 165)


@pytest.mark.parametrize(("calc", "method"), [(0, "custom"), (1, "max"), (2, "reserve"),
                                              (3, "threshold"), (None, None)])
def test_how_the_zones_are_worked_out(calc, method):
    assert fit.activity_data(with_zones(zones_of(calc=calc))).zones.method == method


def test_the_zones_of_a_lap_are_not_the_activity_s():
    assert fit.activity_data(with_zones(zones_of(refers=19))).zones is None
    assert fit.activity_data(run()).zones is None  # no time_in_zone at all


def test_the_zones_of_several_sessions_add_up():
    sessions = [FitData(2, (START, 1, 0, 2000, 2000, 500, 1, 2500, 2500, 141, 145, 1)),
                FitData(2, (START + 2, 2, 0, 2000, 2000, 500, 1, 2500, 2500, 141, 145, 1))]
    zones = fit.activity_data(with_zones(zones_of(index=0), zones_of(index=1),
                                         sessions=sessions)).zones
    assert zones.times == (1200, 600, 400, 200, 100, 100)


@pytest.mark.parametrize("change", [{"bounds": (120, 140, 155, 165, 178, None)},
                                    {"times": (None,) * 7}])
def test_zones_the_file_does_not_give_whole_are_none(change):
    data = fit.activity_data(with_zones(zones_of(**change)))
    assert data.zones is None
    assert data.state is fit.FileState.WHOLE


# --- training effect and load ----------------------------------------------------------------

# start, sport, timer time, aerobic and anaerobic training effect (tenths), training load peak
# (scale 65536)
EFFECT_SESSION = FitDefinition(2, 18, ((2, 0x86), (5, 0x00), (8, 0x86), (24, 0x02), (137, 0x02),
                                       (168, 0x85)))


def with_effects(*effects) -> bytes:
    sessions = [FitData(2, (START + i, 10, 4000, aerobic, anaerobic, load))
                for i, (aerobic, anaerobic, load) in enumerate(effects)]
    return fit_file(*FILE_ID, HEART_RECORD, FitData(1, (START, 120)), EFFECT_SESSION, *sessions)


def test_the_training_effect_and_load_of_the_session():
    data = fit.activity_data(with_effects((16, 0, round(28.5 * 65536))))
    assert data.aerobic_effect == pytest.approx(1.6)
    assert data.anaerobic_effect == 0
    assert data.training_load == pytest.approx(28.5)


def test_a_training_effect_or_load_not_given_is_none():
    data = fit.activity_data(with_effects((0xFF, 12, 0x7FFFFFFF)))
    assert data.aerobic_effect is None and data.training_load is None
    assert data.anaerobic_effect == pytest.approx(1.2)
    data = fit.activity_data(run())  # no such fields at all
    assert (data.aerobic_effect, data.anaerobic_effect, data.training_load) == (None, None, None)


def test_of_several_sessions_the_highest():
    """An assumption, no file of a multisport activity looked at: the highest of the sessions."""
    data = fit.activity_data(with_effects((12, 4, 20 * 65536), (21, 0, 35 * 65536),
                                          (0xFF, 0xFF, 0x7FFFFFFF)))
    assert data.aerobic_effect == pytest.approx(2.1)
    assert data.anaerobic_effect == pytest.approx(0.4)
    assert data.training_load == pytest.approx(35)


# --- the details -------------------------------------------------------------------------------

# start, sport, timer time, strides and their fraction (scale 128), maximum speed and its
# enhanced field, average and maximum cadence and their fractions (scale 128), descent, average,
# maximum and minimum temperature, minimum heart rate, active time, minimum and maximum altitude
# and their enhanced fields, average, maximum and minimum respiration rate (scale 100)
DETAIL_SESSION = FitDefinition(2, 18, (
    (2, 0x86), (5, 0x00), (8, 0x86), (10, 0x86), (94, 0x02), (15, 0x84), (125, 0x86),
    (18, 0x02), (19, 0x02), (92, 0x02), (93, 0x02), (23, 0x84), (57, 0x01), (58, 0x01),
    (150, 0x01), (64, 0x02), (78, 0x86), (71, 0x84), (50, 0x84), (127, 0x86), (128, 0x86),
    (169, 0x84), (170, 0x84), (180, 0x84)))
# duration, set type (rest 0, active 1)
SET = FitDefinition(5, 225, ((0, 0x86), (5, 0x00)))
DETAILS = {"cycles": 2190, "cycles part": 64, "max speed": 1500, "enhanced max speed": 1880,
           "cadence": 52, "max cadence": 59, "cadence part": 32, "max cadence part": 96,
           "descent": 12, "temperature": 27, "max temperature": 29, "min temperature": 25,
           "min heart rate": 86, "active time": 1_470_000, "min altitude": altitude(10),
           "max altitude": altitude(40), "enhanced min altitude": altitude(19.4),
           "enhanced max altitude": altitude(31), "respiration": 2437, "max respiration": 3800,
           "min respiration": 1520}


def with_details(*sessions, sets=()) -> bytes:
    """A file of sessions with the details' fields (DETAILS, each session's changes over them)
    and these sets (duration in milliseconds, set type)."""
    found = [FitData(2, (START, 11, 3_600_000, *{**DETAILS, **changes}.values()))
             for changes in sessions]
    return fit_file(*FILE_ID, HEART_RECORD, FitData(1, (START, 120)), DETAIL_SESSION, *found,
                    SET, *(FitData(5, s) for s in sets))


def test_the_details_of_the_session():
    """Strides (on foot) with their fraction, the enhanced maximum speed and altitudes first,
    cadence with its fraction, the temperatures, the minimum heart rate, the active time, the
    respiration rate."""
    data = fit.activity_data(with_details({}))
    assert data.cycles == pytest.approx(2190.5)
    assert data.max_speed == pytest.approx(1.88)
    assert (data.avg_cadence, data.max_cadence) == (pytest.approx(52.25), pytest.approx(59.75))
    assert data.descent == 12 and data.min_heart_rate == 86
    assert (data.avg_temperature, data.min_temperature, data.max_temperature) == (27, 25, 29)
    assert data.active_time == pytest.approx(1470.0)
    assert (data.min_altitude, data.max_altitude) == (pytest.approx(19.4), pytest.approx(31.0))
    assert (data.avg_respiration, data.min_respiration, data.max_respiration) == (
        pytest.approx(24.37), pytest.approx(15.2), pytest.approx(38.0))
    assert data.rest_time is None  # no sets


def test_the_plain_fields_stand_in_and_a_value_not_given_is_none():
    data = fit.activity_data(with_details({
        "enhanced max speed": None, "enhanced min altitude": None, "enhanced max altitude": None,
        "cadence part": None, "cycles part": None, "temperature": -3, "min temperature": -5,
        "max temperature": -1, "min heart rate": None, "respiration": None}))
    assert data.max_speed == pytest.approx(1.5)
    assert (data.min_altitude, data.max_altitude) == (pytest.approx(10.0), pytest.approx(40.0))
    assert data.avg_cadence == 52 and data.cycles == 2190
    assert (data.avg_temperature, data.min_temperature, data.max_temperature) == (-3, -5, -1)
    assert data.min_heart_rate is None and data.avg_respiration is None
    data = fit.activity_data(run())  # no such fields at all
    assert (data.descent, data.max_speed, data.avg_cadence, data.cycles, data.active_time,
            data.avg_temperature, data.avg_respiration, data.rest_time) == (None,) * 8


def test_the_rest_time_is_that_of_the_rest_sets():
    """A strength workout: its sets, active and resting; the rests added up."""
    data = fit.activity_data(with_details({}, sets=[(40_000, 1), (90_000, 0), (35_000, 1),
                                                    (None, 0), (60_500, 0)]))
    assert data.rest_time == pytest.approx(150.5)
    data = fit.activity_data(with_details({}, sets=[(40_000, 1), (35_000, 1)]))
    assert data.rest_time is None


def test_the_details_of_several_sessions():
    """The descent, strides and active time added up; the lowest of the minimums and the
    highest of the maximums; the averages weighted by each session's timer time (here all of
    an hour)."""
    data = fit.activity_data(with_details(
        {}, {"descent": 30, "cycles": 1000, "cycles part": 0, "enhanced min altitude": altitude(5),
             "enhanced max altitude": altitude(20), "min temperature": 20, "temperature": 23,
             "enhanced max speed": 2500, "min heart rate": 90, "active time": 30_000}))
    assert data.descent == 42 and data.cycles == pytest.approx(3190.5)
    assert data.active_time == pytest.approx(1500.0)
    assert (data.min_altitude, data.max_altitude) == (pytest.approx(5.0), pytest.approx(31.0))
    assert (data.min_temperature, data.avg_temperature) == (20, pytest.approx(25.0))
    assert data.max_speed == pytest.approx(2.5) and data.min_heart_rate == 86


# timestamp, heart rate and temperature (°C)
TEMPERATURE_RECORD = FitDefinition(1, 20, ((253, 0x86), (3, 0x02), (13, 0x01)))


def with_temperatures(*temperatures) -> bytes:
    records = [FitData(1, (START + i, 120, t)) for i, t in enumerate(temperatures)]
    return fit_file(*FILE_ID, TEMPERATURE_RECORD, *records, DETAIL_SESSION,
                    FitData(2, (START, 11, 3_600_000, *DETAILS.values())))


def test_the_temperature_is_that_of_the_records():
    """The session keeps whole degrees (here 27, 25 and 29); the activity's average is that of
    its records' temperatures, its range their lowest and highest. A record without one is
    left out."""
    data = fit.activity_data(with_temperatures(19, 20, 20, None, 21, 17, 30))
    assert [s.temperature for s in data.samples] == [19, 20, 20, None, 21, 17, 30]
    assert data.avg_temperature == pytest.approx(127 / 6)
    assert (data.min_temperature, data.max_temperature) == (17, 30)
    data = fit.activity_data(with_temperatures(-3, -5, -1))
    assert (data.avg_temperature, data.min_temperature, data.max_temperature) == (-3, -5, -1)
    data = fit.activity_data(with_temperatures(None, None))  # the session's then
    assert (data.avg_temperature, data.min_temperature, data.max_temperature) == (27, 25, 29)


def test_the_records_are_left_out_of_the_text_of_the_data():
    """A test that fails prints the data it compared: its records (each a position and a time)
    are not printed with it."""
    data = fit.activity_data(run())
    assert data.samples and "Sample(" not in repr(data) and "latitude" not in repr(data)


# What the activity journal keeps of a file besides its page: the model that made it (with the
# time it was made: the same activity read again), the sub-sport, the device's time zone at
# the time and the units it shows. Field numbers from the published FIT profile: file_id
# manufacturer 1 and product 2; session sub_sport 6; activity timestamp 253 and
# local_timestamp 5; user_profile elev_setting 6, speed_setting 13, dist_setting 14.
MADE_BY = (FitDefinition(0, 0, ((0, 0x00), (1, 0x84), (2, 0x84), (4, 0x86))),
           FitData(0, (4, 1, 4532, START)))
ACTIVITY = FitDefinition(4, 34, ((253, 0x86), (5, 0x86)))
USER_PROFILE = FitDefinition(5, 3, ((6, 0x00), (13, 0x00), (14, 0x00)))
TRAIL = 3  # the sub_sport "trail"


def journal_run(*, activity=(START + 10, START + 10 + 3600), profile=(1, 0, 1),
                sub_sports=(TRAIL,)) -> bytes:
    sessions = [FitData(2, (START, 1, sub, 4000, 4000, 1000, 2, 2500, 2500, 141, 145, 1))
                for sub in sub_sports]
    found = [*MADE_BY, USER_PROFILE, FitData(5, profile), RECORD,
             *[record(s, s * 2.5) for s in range(4)], SESSION, *sessions]
    if activity is not None:
        found += [ACTIVITY, FitData(4, activity)]
    return fit_file(*found)


def test_what_the_journal_keeps_besides_the_page():
    data = fit.activity_data(journal_run())
    assert (data.created, data.product) == (UNIX, 4532)
    assert data.sub_sport == TRAIL
    assert data.utc_offset == 3600  # the device's clock an hour ahead of UTC
    assert data.units == fit.Units(distance="statute", elevation="statute", speed="metric")


def test_what_a_file_does_not_say_is_none():
    data = fit.activity_data(run())  # no product, activity or user_profile; sub_sport generic
    assert (data.created, data.product, data.utc_offset, data.units) == (UNIX, None, None, None)
    assert data.sub_sport == 0
    data = fit.activity_data(journal_run(profile=(None, None, None), activity=None))
    assert (data.units, data.utc_offset) == (None, None)


def test_several_sessions_of_different_sub_sports_have_none():
    assert fit.activity_data(journal_run(sub_sports=(TRAIL, TRAIL))).sub_sport == TRAIL
    assert fit.activity_data(journal_run(sub_sports=(TRAIL, 0))).sub_sport is None


@pytest.mark.parametrize("offset,kept", [(-12 * 3600, True), (14 * 3600, True),
                                          (-12 * 3600 - 60, False), (14 * 3600 + 60, False)])
def test_an_offset_no_time_zone_has_is_none(offset, kept):
    """Time zones are UTC-12 to UTC+14: anything further is a clock set wrong, not a zone."""
    data = fit.activity_data(journal_run(activity=(START, START + offset)))
    assert data.utc_offset == (offset if kept else None)


def test_an_offset_is_rounded_to_the_minute():
    """local_timestamp and timestamp may be a second apart (written one after the other)."""
    data = fit.activity_data(journal_run(activity=(START + 10, START + 10 + 7201)))
    assert data.utc_offset == 7200
