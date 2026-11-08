"""The words of an activity's page (gui/words.py): times with seconds, distances, pace or speed
by sport, heart rate, calories, ascent, the names of the tiles and the line of a lap, in English
and in Polish. A distance of 0 m and a value not given say nothing. Every value is made up."""

import pytest

from openexpress import fit, i18n

NB = "\u00a0"
MINUS = "\u2212"
RUNNING, CYCLING, SWIMMING, TRAINING, HIKING = 1, 2, 5, 10, 17


@pytest.fixture(params=["en", "pl"])
def language(request):
    i18n.setup(request.param)
    yield request.param
    i18n.setup("en")


def words():
    from openexpress.gui import words
    return words


def tiles(w, data) -> list[tuple[str, str]]:
    """The tiles of an activity's page as it shows them, each (value, name)."""
    return [(value, w.tile_name(kind)) for kind, value in w.activity_tile_kinds(data)]


@pytest.mark.parametrize(("seconds", "said"), [(41, "0:41"), (302, "5:02"), (3161, "52:41"),
                                               (3761, "1:02:41"), (3761.6, "1:02:42")])
def test_a_time_with_its_seconds(seconds, said):
    assert words().activity_clock(seconds) == said


def test_a_distance(language):
    w = words()
    assert w.activity_distance(10240, RUNNING) == {"en": "10.24 km", "pl": "10,24 km"}[language]
    assert w.activity_distance(1000, CYCLING) == {"en": "1.00 km", "pl": "1,00 km"}[language]
    assert w.activity_distance(1500, SWIMMING) == "1500 m"
    assert w.activity_distance(0, RUNNING) is None
    assert w.activity_distance(None, RUNNING) is None


def test_pace_or_speed_by_sport(language):
    w = words()
    assert w.activity_pace(2.5, RUNNING) == "6:40 /km"  # 400 s a km
    assert w.activity_pace(1000 / 302, HIKING) == "5:02 /km"
    assert w.activity_pace(0.8, SWIMMING) == "2:05 /100 m"
    assert w.activity_pace(6.75, CYCLING) == {"en": "24.3 km/h", "pl": "24,3 km/h"}[language]
    assert w.activity_pace(3.0, TRAINING) == {"en": "10.8 km/h", "pl": "10,8 km/h"}[language]
    assert w.activity_pace(0, RUNNING) is None and w.activity_pace(None, CYCLING) is None


@pytest.mark.parametrize(("sport", "kind"), [(1, "pace"), (11, "pace"), (16, "pace"),
                                             (17, "pace"), (35, "pace"), (5, "swim pace"),
                                             (2, "speed"), (21, "speed"), (13, "speed"),
                                             (None, "speed")])
def test_which_sports_have_a_pace(sport, kind):
    assert words().speed_kind(sport) == kind


def test_heart_rate_calories_and_ascent(language):
    w = words()
    assert w.heart_rate_text(148) == {"en": "148 bpm", "pl": "148 bpm"}[language]
    assert w.calories_text(520) == "520 kcal"
    assert w.ascent_text(740) == "740 m"
    assert w.ascent_text(0) is None and w.ascent_text(None) is None
    assert w.heart_rate_text(None) is None and w.calories_text(None) is None


TILES = {
    "en": {"distance": "Distance", "time": "Time", "pace": "Average Pace",
           "speed": "Average Speed", "average heart rate": "Average Heart Rate",
           "maximum heart rate": "Maximum Heart Rate", "calories": "Calories",
           "ascent": "Total Ascent"},
    "pl": {"distance": "Dystans", "time": "Czas", "pace": "Średnie tempo",
           "speed": "Średnia prędkość", "average heart rate": "Średnie tętno",
           "maximum heart rate": "Maksymalne tętno", "calories": "Suma kalorii",
           "ascent": "Całkowity wznios"},
}


def test_the_names_of_the_tiles(language):
    w = words()
    assert {kind: w.tile_name(kind) for kind in TILES[language]} == TILES[language]
    assert w.tile_name("swim pace") == TILES[language]["pace"]


def lap(**values):
    return fit.Lap(**values)


def test_the_line_of_a_lap(language):
    w = words()
    assert w.lap_title(1) == {"en": "Lap 1", "pl": "Okrążenie 1"}[language]
    run = lap(distance=1500.0, timer_time=453.0, elapsed_time=465.0, avg_speed=1500 / 453,
              avg_heart_rate=141)
    assert w.lap_caption(run, RUNNING) == {
        "en": f"1.50 km{NB}· 7:33{NB}· 5:02 /km{NB}· 141 bpm",
        "pl": f"1,50 km{NB}· 7:33{NB}· 5:02 /km{NB}· 141 bpm"}[language]
    workout = lap(distance=0.0, timer_time=302.0, avg_heart_rate=141)
    assert w.lap_caption(workout, TRAINING) == {
        "en": f"5:02{NB}· 141 bpm", "pl": f"5:02{NB}· 141 bpm"}[language]


def test_a_lap_line_has_only_what_the_lap_gives():
    """The timer time first, the elapsed time standing in; the speed worked out from the
    distance and the time when the lap does not give it; a ride in km/h."""
    w = words()
    assert w.lap_caption(lap(distance=5000.0, elapsed_time=600.0), CYCLING) == (
        f"5.00 km{NB}· 10:00{NB}· 30.0 km/h")
    assert w.lap_caption(lap(avg_heart_rate=120), TRAINING) == "120 bpm"
    assert w.lap_caption(lap(), TRAINING) == ""


# --- the tiles on top of the page ----------------------------------------------------------------


def activity(**values):
    return fit.ActivityData(fit.FileState.WHOLE, **values)


HIKE = activity(sport=HIKING, timer_time=13_800.0, elapsed_time=14_400.0, distance=12_400.0,
                avg_speed=1.0, avg_heart_rate=128, max_heart_rate=165, calories=900, ascent=740)
WORKOUT = activity(sport=TRAINING, timer_time=2700.0, distance=0.0, avg_heart_rate=112,
                   max_heart_rate=151, calories=320)

TILES_OF = {
    "en": {"hike": [("12.40 km", "Distance"), ("3:50:00", "Time"), ("16:40 /km", "Average Pace"),
                    ("128 bpm", "Average Heart Rate"), ("740 m", "Total Ascent")],
           "workout": [("45:00", "Time"), ("112 bpm", "Average Heart Rate"),
                       ("151 bpm", "Maximum Heart Rate"), ("320 kcal", "Calories")]},
    "pl": {"hike": [("12,40 km", "Dystans"), ("3:50:00", "Czas"), ("16:40 /km", "Średnie tempo"),
                    ("128 bpm", "Średnie tętno"), ("740 m", "Całkowity wznios")],
           "workout": [("45:00", "Czas"), ("112 bpm", "Średnie tętno"),
                       ("151 bpm", "Maksymalne tętno"), ("320 kcal", "Suma kalorii")]},
}


def test_the_tiles_of_a_hike_and_of_a_workout(language):
    """With a distance: distance, time, pace, average heart rate and ascent, five at most; a
    workout with a distance of 0 m: time, average and maximum heart rate, calories."""
    w = words()
    assert tiles(w, HIKE) == TILES_OF[language]["hike"]
    assert tiles(w, WORKOUT) == TILES_OF[language]["workout"]


def test_the_tiles_by_their_kind_for_their_icons():
    """The same tiles, each by its kind (its icon on the page) with its value."""
    w = words()
    assert w.activity_tile_kinds(HIKE) == [
        ("distance", "12.40 km"), ("time", "3:50:00"), ("pace", "16:40 /km"),
        ("average heart rate", "128 bpm"), ("ascent", "740 m")]
    assert [kind for kind, _value in w.activity_tile_kinds(WORKOUT)] == [
        "time", "average heart rate", "maximum heart rate", "calories"]


ZONES = {
    "en": {"title": "Heart Rate Zones", "below": "Below Zone 1", "zone": "Zone 3",
           "share": "36:30 · 88%",
           "threshold": "Bounds from the device: % of lactate threshold (165 bpm)",
           "max": "Bounds from the device: % of maximum heart rate (190 bpm)",
           "reserve": "Bounds from the device: % of heart rate reserve",
           "custom": "Bounds from the device: your own"},
    "pl": {"title": "Strefy tętna", "below": "Poniżej strefy 1", "zone": "Strefa 3",
           "share": "36:30 · 88%",
           "threshold": "Granice z\u00a0urządzenia: % progu mleczanowego (165 bpm)",
           "max": "Granice z\u00a0urządzenia: % tętna maksymalnego (190 bpm)",
           "reserve": "Granice z\u00a0urządzenia: % rezerwy tętna",
           "custom": "Granice z\u00a0urządzenia: własne"},
}


def zones(method="threshold", threshold=165):
    return fit.HeartRateZones(times=(2190, 300, 0, 0, 0, 0), bounds=(120, 140, 155, 165, 178, 190),
                              method=method, max_heart_rate=190,
                              threshold_heart_rate=threshold)


def test_the_words_of_the_heart_rate_zones(language):
    w, said = words(), ZONES[language]
    assert w.zones_title() == said["title"]
    assert w.zone_name(0) == said["below"] and w.zone_name(3) == said["zone"]
    assert w.zone_share(2190, 2490) == said["share"]
    for method in ("threshold", "max", "reserve", "custom"):
        assert w.zones_method_text(zones(method)) == said[method]
    assert w.zones_method_text(zones(None)) is None
    assert w.zones_method_text(zones("threshold", None)) is None  # its number not given


EFFECT = {
    "en": {"title": "Training Effect", "aerobic": "Aerobic", "anaerobic": "Anaerobic",
           "load": "Training Load",
           "words": ["No benefit", "No benefit", "Minor benefit", "Minor benefit", "Maintaining",
                     "Maintaining", "Improving", "Improving", "Highly improving",
                     "Highly improving", "Overreaching"],
           "text": f"1.6{NB}· Minor benefit"},
    # Garmin's own Polish words: those of the scale as the help of Garmin Connect says them.
    "pl": {"title": "Efekt treningu", "aerobic": "Aerobowy", "anaerobic": "Beztlenowy",
           "load": "Obciążenie wysiłkowe",
           "words": ["Brak korzyści", "Brak korzyści", "Umiarkowana korzyść",
                     "Umiarkowana korzyść", "Utrzymanie", "Utrzymanie", "Poprawa", "Poprawa",
                     "Duża poprawa", "Duża poprawa", "Przeciążenie"],
           "text": f"1,6{NB}· Umiarkowana korzyść"},
}


def test_the_words_of_the_training_effect(language):
    """The word of the public scale, at each of its edges: below 1.0 no benefit, from 1.0
    minor, 2.0 maintaining, 3.0 improving, 4.0 highly improving, 5.0 overreaching."""
    w, said = words(), EFFECT[language]
    assert w.effect_title() == said["title"]
    assert (w.effect_name("aerobic"), w.effect_name("anaerobic")) == (said["aerobic"],
                                                                      said["anaerobic"])
    assert w.training_load_name() == said["load"]
    edges = (0.0, 0.9, 1.0, 1.9, 2.0, 2.9, 3.0, 3.9, 4.0, 4.9, 5.0)
    assert [w.effect_word(value) for value in edges] == said["words"]
    assert w.effect_text(1.6) == said["text"]


@pytest.mark.parametrize(("load", "said"), [(28.4, "28"), (28.5, "29"), (0.0, "0"),
                                            (1234.6, "1235")])
def test_the_training_load_as_a_whole_number(load, said):
    assert words().training_load_text(load) == said


TRACK = {
    "en": {"heart rate": "Track colour: heart rate", "elevation": "Track colour: elevation",
           "pace": "Track colour: pace", "swim pace": "Track colour: pace",
           "speed": "Track colour: speed", "switch": "Colours"},
    "pl": {"heart rate": "Kolor trasy: tętno", "elevation": "Kolor trasy: wysokość",
           "pace": "Kolor trasy: tempo", "swim pace": "Kolor trasy: tempo",
           "speed": "Kolor trasy: prędkość", "switch": "Kolory"},
}


def test_the_words_of_the_track_s_colours(language):
    """The series after the colon in lower case, as a sentence goes on in Polish."""
    w, said = words(), TRACK[language]
    for kind in ("heart rate", "elevation", "pace", "swim pace", "speed"):
        assert w.track_colour_text(kind) == said[kind]
    assert w.colours_text() == said["switch"]


# The bounds as a device keeps them: each the first beat of the zone above it, the last the top
# of zone 5 (which has none on the page). Made-up zones from the lactate threshold, as Garmin
# Connect shows them: below zone 1 under 118, zone 1 118–140, …, zone 5 above 182; and from a
# maximum heart rate of 190.
ZONE_SPANS = {
    "threshold": ((118, 141, 158, 170, 183, 195), {
        "en": ["below 118 bpm", "118–140 bpm", "141–157 bpm", "158–169 bpm", "170–182 bpm",
               "above 182 bpm"],
        "pl": ["poniżej 118 bpm", "118–140 bpm", "141–157 bpm", "158–169 bpm",
               "170–182 bpm", "powyżej 182 bpm"]}),
    "max": ((95, 114, 133, 152, 171, 190), {
        "en": ["below 95 bpm", "95–113 bpm", "114–132 bpm", "133–151 bpm", "152–170 bpm",
               "above 170 bpm"],
        "pl": ["poniżej 95 bpm", "95–113 bpm", "114–132 bpm", "133–151 bpm",
               "152–170 bpm", "powyżej 170 bpm"]}),
}


@pytest.mark.parametrize("worked_out", ZONE_SPANS)
def test_the_heart_rates_of_each_zone_from_its_bounds(language, worked_out):
    """A bound is the first beat of the zone above it: below zone 1 under the first, a zone
    from its bound to a beat under the next, zone 5 above a beat under its own, no top."""
    bounds, said = ZONE_SPANS[worked_out][0], ZONE_SPANS[worked_out][1][language]
    assert [words().zone_range(bounds, n) for n in range(6)] == said


@pytest.mark.parametrize(("sport", "metres", "pace"), [
    (11, 1000.0, False), (11, 1004.0, False), (11, 1010.0, True), (11, 995.0, True),
    (5, 100.0, False), (5, 105.0, True), (2, 1000.0, True)])
def test_a_lap_whose_pace_is_its_time_leaves_it_out(sport, metres, pace):
    """A lap shown as 1.00 km at a pace a kilometre (100 m at a pace of 100 m in a swim): its
    pace is its time. One shown otherwise (0.99 km, 1.01 km) keeps it; a speed stays."""
    lap = fit.Lap(1_700_000_000, 600.0, 600.0, metres, metres / 600, 120)
    shown = words().lap_caption(lap, sport)
    assert ("/km" in shown or "/100 m" in shown or "km/h" in shown) is pace


def test_a_ride_has_its_speed_worked_out_when_its_session_does_not_say_it():
    ride = activity(sport=CYCLING, timer_time=3600.0, distance=30_000.0)
    assert tiles(words(), ride) == [("30.00 km", "Distance"), ("1:00:00", "Time"),
                                    ("30.0 km/h", "Average Speed")]


def test_a_value_not_given_leaves_its_tile_out():
    w = words()
    assert tiles(w, activity(sport=RUNNING, timer_time=600.0)) == [("10:00", "Time")]
    assert tiles(w, activity(sport=TRAINING, calories=0)) == [("0 kcal", "Calories")]
    assert tiles(w, activity()) == []  # no session: no tile


# --- the chart -----------------------------------------------------------------------------------

KINDS = ["heart rate", "elevation", "pace", "swim pace", "speed"]
CHART = {
    "en": {"names": ["Heart rate", "Elevation", "Pace", "Pace", "Speed"],
           "units": ["bpm", "m", "min/km", "min/100 m", "km/h"]},
    "pl": {"names": ["Tętno", "Wysokość", "Tempo", "Tempo", "Prędkość"],
           "units": ["bpm", "m", "min/km", "min/100 m", "km/h"]},
}


def test_the_names_and_units_of_the_chart(language):
    w = words()
    assert [w.chart_series_name(kind) for kind in KINDS] == CHART[language]["names"]
    assert [w.chart_unit(kind) for kind in KINDS] == CHART[language]["units"]
    assert w.chart_accessible_name("heart rate") == {"en": "Heart rate over time",
                                                     "pl": f"Tętno w{NB}czasie"}[language]
    assert w.no_chart_text() == {
        "en": "No heart rate, elevation or speed in this activity",
        "pl": f"Brak tętna, wysokości i{NB}prędkości w{NB}tej aktywności"}[language]


def test_the_values_on_the_axes(language):
    """Whole numbers for heart rate and elevation, the decimals of the step for a speed, a
    pace as a clock."""
    w = words()
    assert w.chart_tick("heart rate", 125, 25) == "125"
    assert w.chart_tick("elevation", -5, 5) == "-5"
    assert w.chart_tick("speed", 3.5, 0.5) == {"en": "3.5", "pl": "3,5"}[language]
    assert w.chart_tick("speed", 3.0, 0.5) == {"en": "3.0", "pl": "3,0"}[language]
    assert w.chart_tick("speed", 25, 5) == "25"
    assert w.chart_tick("pace", 750, 30) == "12:30"
    assert w.chart_tick("swim pace", 125, 5) == "2:05"


def test_the_value_at_the_cursor(language):
    w = words()
    at = w.chart_cursor_text
    assert at(1880, "heart rate", 152) == {"en": f"31:20{NB}· 152 bpm",
                                           "pl": f"31:20{NB}· 152 bpm"}[language]
    assert at(1880, "elevation", 412.4) == f"31:20{NB}· 412 m"
    assert at(1880, "pace", 310) == f"31:20{NB}· 5:10 /km"
    assert at(1880, "swim pace", 125) == f"31:20{NB}· 2:05 /100 m"
    assert at(1880, "speed", 24.31) == {"en": f"31:20{NB}· 24.3 km/h",
                                        "pl": f"31:20{NB}· 24,3 km/h"}[language]
    assert at(1880, "heart rate", None) == {"en": f"31:20{NB}· no reading",
                                            "pl": f"31:20{NB}· brak odczytu"}[language]


# --- the details ---------------------------------------------------------------------------------

WALKING = 11
WALK = activity(sport=WALKING, timer_time=3540.0, elapsed_time=3900.0, distance=4250.0,
                avg_speed=1.2006, avg_heart_rate=108, max_heart_rate=125, calories=180, ascent=12,
                descent=12, min_altitude=19.4, max_altitude=31.0, max_speed=1000 / 532,
                avg_cadence=52.2, max_cadence=59.0, cycles=2190.0, min_heart_rate=86,
                avg_temperature=27.4, min_temperature=25.0, max_temperature=29.0)
MOVING = 3150.0  # 52:30, worked out from the records
WORKOUT_DETAILS = activity(sport=TRAINING, timer_time=3130.0,
                           elapsed_time=3130.0, distance=0.0, avg_heart_rate=112,
                           max_heart_rate=171, min_heart_rate=92, calories=320,
                           active_time=1470.0, rest_time=1500.0, avg_temperature=29.0,
                           min_temperature=27.0, max_temperature=31.0, avg_respiration=24.4,
                           min_respiration=15.2, max_respiration=38.0)
DETAILS_OF = {
    "en": {"walk": [("Elapsed Time", "1:05:00"), ("Moving Time", "52:30"),
                    ("Total Descent", "12 m"), ("Minimum Elevation", "19 m"),
                    ("Maximum Elevation", "31 m"), ("Average Moving Pace", "12:21 /km"),
                    ("Best Pace", "8:52 /km"), ("Average Cadence", "104 spm (max 118)"),
                    ("Steps", "4380"), ("Heart Rate", "86–125 bpm"), ("Calories", "180 kcal"),
                    ("Temperature", "27.4 °C (25.0–29.0 °C)")],
           "workout": [("Work Time", "24:30"), ("Rest Time", "25:00"),
                       ("Heart Rate", "92–171 bpm"), ("Temperature", "29.0 °C (27.0–31.0 °C)"),
                       ("Respiration Rate", "24 brpm (15–38)")],
           "title": "Details"},
    "pl": {"walk": [("Upływ czasu", "1:05:00"), ("Czas ruchu", "52:30"),
                    ("Całkowity spadek", "12 m"), ("Minimalna wysokość", "19 m"),
                    ("Maksymalna wysokość", "31 m"), ("Średnie tempo ruchu", "12:21 /km"),
                    ("Najlepsze tempo", "8:52 /km"),
                    ("Średnia kadencja", "104 kr./min (maks. 118)"), ("Kroki", "4380"),
                    ("Tętno", "86–125 bpm"), ("Suma kalorii", "180 kcal"),
                    ("Temperatura", "27,4 °C (25,0–29,0 °C)")],
           "workout": [("Czas ćwiczeń", "24:30"), ("Czas odpoczynku", "25:00"),
                       ("Tętno", "92–171 bpm"), ("Temperatura", "29,0 °C (27,0–31,0 °C)"),
                       ("Częstotliwość oddechu", "24 brpm (15–38)")],
           "title": "Szczegóły"},
}


def test_the_details_of_a_walk_and_of_a_strength_workout(language):
    """In their order, only what the file gives, nothing a tile shows already: no elapsed time
    the same as the tile's, no calories of a workout (a tile); steps and a cadence in steps a
    minute from strides; the moving time and its pace from the records."""
    w = words()
    assert w.details_title() == DETAILS_OF[language]["title"]
    assert w.activity_details(WALK, MOVING) == DETAILS_OF[language]["walk"]
    assert w.activity_details(WORKOUT_DETAILS) == DETAILS_OF[language]["workout"]


def test_no_details_but_those_the_file_gives():
    w = words()
    assert w.activity_details(activity(sport=WALKING, distance=1000.0, timer_time=600.0)) == []
    assert w.activity_details(activity(sport=WALKING, distance=1000.0, timer_time=600.0,
                                       elapsed_time=600.4)) == []  # the tile's time
    kinds = [k for k, _v in w.activity_detail_kinds(WALK)]  # no moving time worked out
    assert "moving time" not in kinds and "moving pace" not in kinds
    no_zeros = activity(sport=WALKING, distance=1000.0, timer_time=600.0, descent=0, cycles=0.0,
                        avg_cadence=0.0, min_heart_rate=80)  # and no maximum heart rate
    assert w.activity_detail_kinds(no_zeros) == []


def test_a_moving_time_the_same_as_the_tile_s_is_left_out():
    """Never stopped: the moving time and its pace are the tile's time and pace."""
    w = words()
    assert [k for k, _v in w.activity_detail_kinds(WALK, 3540.0)] == [
        "elapsed time", "descent", "minimum elevation", "maximum elevation", "best pace",
        "cadence", "steps", "heart rate", "calories", "temperature"]


@pytest.mark.parametrize(("sport", "cadence", "steps"), [
    (RUNNING, "170 spm (max 186)", "8760"), (HIKING, "170 spm (max 186)", "8760"),
    (CYCLING, "85 rpm (max 93)", None), (21, "85 rpm (max 93)", None),  # an e-bike
    (TRAINING, None, None), (SWIMMING, None, None)])
def test_cadence_and_steps_by_sport(sport, cadence, steps):
    """On foot two steps a stride; on a bike revolutions a minute; in other sports neither."""
    data = activity(sport=sport, timer_time=600.0, avg_cadence=85.2, max_cadence=93.0,
                    cycles=4380.0)
    said = dict(words().activity_detail_kinds(data))
    assert (said.get("cadence"), said.get("steps")) == (cadence, steps)


def test_whole_numbers_from_ten_thousand_have_a_separator(language):
    """Polish typography: up to 9999 none, from 10 000 a hard space between the thousands; in
    English a comma. A minus sign before a value below zero; a temperature with one decimal, as
    Garmin Connect shows it."""
    w = words()
    data = activity(sport=HIKING, timer_time=36_000.0, cycles=6172.5, avg_temperature=-3.0,
                    min_temperature=-5.0, max_temperature=-1.0, avg_cadence=52.0)
    said = dict(w.activity_detail_kinds(data))
    assert said["steps"] == {"en": "12,345", "pl": f"12{NB}345"}[language]
    assert said["temperature"] == {"en": f"{MINUS}3.0 °C ({MINUS}5.0–{MINUS}1.0 °C)",
                                   "pl": f"{MINUS}3,0 °C ({MINUS}5,0–{MINUS}1,0 °C)"}[language]
    assert said["cadence"] == {"en": "104 spm", "pl": "104 kr./min"}[language]  # no maximum
    assert dict(w.activity_detail_kinds(activity(sport=HIKING, cycles=4999.5)))["steps"] == "9999"
    assert w.calories_text(12_000) == {"en": "12,000 kcal", "pl": f"12{NB}000 kcal"}[language]


def test_a_value_alone_without_its_range():
    w = words()
    data = activity(sport=TRAINING, timer_time=600.0, avg_temperature=27.0, avg_respiration=24.0,
                    max_temperature=30.0)  # no minimum
    assert dict(w.activity_detail_kinds(data)) == {"temperature": "27.0 °C",
                                                   "respiration": "24 brpm"}


def test_a_ride_s_details_by_its_speed():
    w = words()
    ride = activity(sport=CYCLING, timer_time=3600.0, distance=30_000.0, avg_speed=30_000 / 3600,
                    max_speed=15.0)
    assert w.activity_details(ride, 3000.0) == [
        ("Moving Time", "50:00"), ("Average Moving Speed", "36.0 km/h"),
        ("Maximum Speed", "54.0 km/h")]
