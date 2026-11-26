"""The Activities page since the connection before this one (since_last.py) and its words
(gui/words.py): which activities are new and since when (the journal's connection before this
one, else the device log's), the newest when none is new, the earlier ones still on the device
(their files on it, nothing else), the units the device shows, the totals, the trivia against a
well-known height or length, what the sync's record says of each file, and the words of all of
it in English and Polish. Made-up journals only."""

from datetime import UTC, datetime, timedelta

import pytest

from openexpress import activity_journal, i18n, since_last

DAY = 86_400
NB = " "
NOW = datetime(2026, 11, 26, 12, 0)  # a Thursday


@pytest.fixture
def language(request):
    i18n.setup(request.param)
    yield request.param
    i18n.setup("en")


def at(when: datetime) -> str:
    return when.astimezone().isoformat(timespec="seconds")


def activity(name: str, start: datetime, **fields) -> dict:
    """A journal's line of an activity: a run of 10 km in an hour, 100 m up, by default."""
    line = {"v": 1, "kind": "activity", "key": f"key of {name}",
            "file": {"name": name, "size": 1000}, "from": "device", "seen": at(NOW),
            "start": int(start.timestamp()), "utc_offset": None, "sport": 1, "sub_sport": None,
            "timer_time": 3600.0, "elapsed_time": 3700.0, "distance": 10_000.0, "ascent": 100,
            "units": {"distance": "metric", "elevation": "metric", "speed": "metric"},
            "shape": None,
            "has_track": False}
    line.update(fields)
    return line


def connection(when: datetime, conn: str | None) -> dict:
    line = {"v": 1, "kind": "connection", "at": at(when), "on_device": 3, "oldest": None,
            "newest": None}
    if conn is not None:
        line["conn"] = conn
    return line


def journal_of(activities: list[dict], connections: list[dict]):
    journal = activity_journal.Journal()
    for a in activities:
        journal.activities[a["key"]] = a
    journal.connections = connections
    return journal


MONDAY = datetime(2026, 11, 23, 18, 0)
A_WEEK = [activity("mon.fit", datetime(2026, 11, 23, 7, 0)),  # before the connection before
          activity("tue.fit", datetime(2026, 11, 24, 7, 0)),
          activity("wed.fit", datetime(2026, 11, 25, 7, 0)),
          activity("old.fit", datetime(2025, 7, 3, 7, 0), **{"from": "backup"})]


# --- since when ------------------------------------------------------------------------------

def test_the_connection_before_is_the_journals_last_of_another_connection():
    journal = journal_of(A_WEEK, [connection(datetime(2026, 11, 20, 9, 0), "a"),
                                  connection(MONDAY, "b"), connection(NOW, "now")])
    assert since_last.connection_before(journal, "now", []) == MONDAY.timestamp()


def test_this_connection_read_twice_is_still_this_one():
    """The window and a background sync of one connection: one line, never the one before."""
    journal = journal_of(A_WEEK, [connection(MONDAY, "b"), connection(NOW, "now")])
    view = since_last.since_last(journal, "now", [], None)
    assert view.since == MONDAY.timestamp()


def test_without_a_line_of_the_journal_the_device_log_tells():
    """The journal's first reading of a device plugged in before: the device log's connection
    before this one."""
    log = [{"conn": "a", "time": at(datetime(2026, 11, 20, 9, 0))},
           {"conn": "b", "time": at(MONDAY)}, {"conn": "now", "time": at(NOW)}]
    journal = journal_of(A_WEEK, [connection(NOW, "now")])
    assert since_last.connection_before(journal, "now", log) == MONDAY.timestamp()


def test_without_any_connection_before_the_journal_has_just_started():
    journal = journal_of(A_WEEK, [connection(NOW, "now")])
    view = since_last.since_last(journal, "now", [{"conn": "now", "time": at(NOW)}], None)
    assert view.started and view.new == []
    assert [a["file"]["name"] for a in view.latest] == ["wed.fit", "tue.fit", "mon.fit"]
    assert view.count == 4 and view.oldest == int(datetime(2025, 7, 3, 7, 0).timestamp())


# --- what is new, what is earlier ------------------------------------------------------------

def test_the_new_ones_started_after_the_connection_before_newest_first():
    journal = journal_of(A_WEEK, [connection(MONDAY, "b"), connection(NOW, "now")])
    view = since_last.since_last(journal, "now", [], {"mon.fit", "tue.fit", "wed.fit"})
    assert [a["file"]["name"] for a in view.new] == ["wed.fit", "tue.fit"]
    assert view.latest == []
    assert view.earlier == 1


def test_none_new_the_newest_three():
    journal = journal_of(A_WEEK, [connection(NOW - timedelta(hours=1), "b"),
                                  connection(NOW, "now")])
    view = since_last.since_last(journal, "now", [], {"mon.fit", "tue.fit", "wed.fit"})
    assert view.new == []
    assert [a["file"]["name"] for a in view.latest] == ["wed.fit", "tue.fit", "mon.fit"]
    assert view.earlier == 3


def test_earlier_counts_only_the_files_on_the_device():
    """An activity only a backup or the journal has is not one of those on the device; nor is
    a new one."""
    journal = journal_of(A_WEEK, [connection(MONDAY, "b"), connection(NOW, "now")])
    view = since_last.since_last(journal, "now", [], {"mon.fit", "tue.fit", "wed.fit",
                                                      "notes.fit"})
    assert view.earlier == 2  # mon.fit and a file that is not an activity: the device's
    unknown = since_last.since_last(journal, "now", [], None)
    assert unknown.earlier == 0


def test_a_gap_at_this_connection():
    """The device's oldest now is newer than the newest the journal knew: activities may have
    left it unread since the connection before this one. A gap of an earlier connection is not
    this one's."""
    def line(when, conn, oldest, newest):
        found = connection(when, conn)
        found.update(oldest=oldest, newest=newest)
        return found

    gap = journal_of(A_WEEK, [line(MONDAY, "b", 100, 200), line(NOW, "now", 300, 400)])
    assert since_last.since_last(gap, "now", [], None).gap
    kept = journal_of(A_WEEK, [line(MONDAY, "b", 100, 200), line(NOW, "now", 150, 400)])
    assert not since_last.since_last(kept, "now", [], None).gap
    before = journal_of(A_WEEK, [line(MONDAY, "a", 100, 200), line(MONDAY, "b", 300, 400),
                                 line(NOW, "now", 350, 500)])
    assert not since_last.since_last(before, "now", [], None).gap


# --- units, totals, trivia -------------------------------------------------------------------

def test_the_units_of_the_newest_that_says_them():
    statute = {"distance": "statute", "elevation": "statute", "speed": "statute"}
    found = [activity("a.fit", MONDAY, units=statute),
             activity("b.fit", NOW, units=None)]
    assert since_last.units_of(found) == since_last.Units(miles=True, feet=True)
    assert since_last.units_of([activity("c.fit", NOW)]) == since_last.Units()
    assert since_last.units_of([]) == since_last.Units()


def test_the_totals():
    found = [activity("a.fit", MONDAY),
             activity("b.fit", NOW, timer_time=None, elapsed_time=600.0, distance=None,
                      ascent=None)]
    assert since_last.totals(found) == since_last.Totals(10_000.0, 4200.0, 100, 2)


@pytest.mark.parametrize(("ascent", "distance", "said"), [
    (893, 53_000.0, ("climbed", "burj-khalifa", 1.0)),
    (700, 53_000.0, ("climbed", "empire-state-building", 1.5)),
    (827, 0.0, ("climbed", "empire-state-building", 1.8)),
    (93, 0.0, ("climbed", "statue-of-liberty", 1.0)),
    (8849, 0.0, ("climbed", "everest", 1.0)),
    (92, 50_000.0, ("distance", "marathon", 1.1)),
    (0, 100_000_000.0, ("distance", "equator", 2.4)),
    (92, 42_194.0, None),
])
def test_the_trivia(ascent, distance, said):
    """The largest not larger than the ascent, one decimal rounded down; the distance when the
    ascent is less than the smallest height; nothing when both are."""
    found = since_last.trivia([activity("a.fit", NOW, ascent=ascent, distance=distance)])
    assert (None if found is None else (found.kind, found.thing, found.times)) == said


def test_what_the_record_of_the_sync_says():
    view = since_last.Since(since=None, uploaded=frozenset({"a.fit"}),
                            waiting=frozenset({"b.fit"}))
    assert view.upload_state(activity("A.FIT", NOW)) == "uploaded"
    assert view.upload_state(activity("b.fit", NOW)) == "waiting"
    assert view.upload_state(activity("c.fit", NOW)) is None


def test_the_record_of_the_sync_of_this_computer(tmp_path):
    from openexpress import sync

    state = sync.SyncState(1234567890)
    state.mark_completed(sync.ACTIVITY_TYPE, "Sent.fit")
    state.keep_pending(sync.ACTIVITY_TYPE, "Failed.fit", b"data")
    state.save()
    assert since_last.upload_records(1234567890) == ({"sent.fit"}, {"failed.fit"})
    assert since_last.upload_records(987654321) == (set(), set())


# --- words -----------------------------------------------------------------------------------

DAYS = {"en": ["Since Monday", "Since Tuesday", "Since Wednesday", "Since Thursday",
               "Since Friday", "Since Saturday", "Since Sunday"],
        "pl": ["Od poniedziałku", "Od wtorku", "Od środy", "Od czwartku", "Od piątku",
               "Od soboty", "Od niedzieli"]}


@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_since_a_day_of_the_last_six_then_a_date(language):
    """From today to six days back the day's own sentence (today's for one earlier today);
    before, the date in digits. Never a name from strftime."""
    from openexpress.gui import words

    for back in range(7):
        day = NOW - timedelta(days=back, hours=2)
        assert words.since_text(day.timestamp(), NOW) == DAYS[language][day.weekday()]
    week = (NOW - timedelta(days=7)).timestamp()
    assert words.since_text(week, NOW) == {"en": "Since 2026-11-19",
                                           "pl": "Od 19.11.2026"}[language]


HEADINGS = {"en": ["No new activities", "1 new activity", "3 new activities"],
            "pl": ["Brak nowych aktywności", "1 nowa aktywność", "3 nowe aktywności"]}


@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_the_heading(language):
    from openexpress.gui import words

    assert [words.new_activities_text(n) for n in (0, 1, 3)] == HEADINGS[language]


TILES = {
    "en": [f"21.3{NB}km{NB}· 2:51{NB}· 640{NB}m", f"13.2{NB}mi{NB}· 2:51{NB}· 2100{NB}ft",
           "0:45"],
    "pl": [f"21,3{NB}km{NB}· 2:51{NB}· 640{NB}m", f"13,2{NB}mi{NB}· 2:51{NB}· 2100{NB}ft",
           "0:45"],
}


@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_the_numbers_of_a_tile(language):
    """Distance, time and ascent in the device's units; a workout without them only its
    time."""
    from openexpress.gui import words

    trail = activity("a.fit", NOW, distance=21_300.0, timer_time=10_260.0, ascent=640)
    gym = activity("b.fit", NOW, sport=10, distance=None, timer_time=2700.0, ascent=None)
    found = [words.tile_caption(trail, since_last.Units()),
             words.tile_caption(trail, since_last.Units(miles=True, feet=True)),
             words.tile_caption(gym, since_last.Units())]
    assert found == TILES[language]


TRIVIA = {
    "en": ["893 m climbed.", "That is the Eiffel Tower 2.7 times.", "53.0 km covered.",
           "That is a marathon 1.2 times."],
    "pl": ["Pokonany wznios: 893 m.", "To 2,7 wieży Eiffla.", "Pokonany dystans: 53,0 km.",
           "To 1,2 maratonu."],
}


@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_the_trivia_in_words(language):
    from openexpress.gui import words

    units = since_last.Units()
    found = [words.trivia_lead("climbed", 893, units),
             words.trivia_object("eiffel-tower", 2.7),
             words.trivia_lead("distance", 53_000.0, units),
             words.trivia_object("marathon", 1.2)]
    assert [s.replace(NB, " ") for s in found] == TRIVIA[language]


@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_every_height_and_length_has_its_sentence(language):
    """Each a whole sentence of its own, translated, with its number."""
    from openexpress.gui import words

    things = [name for name, _size in (*since_last.HEIGHTS, *since_last.LENGTHS)]
    said = [words.trivia_object(thing, 1.5) for thing in things]
    assert len(set(said)) == 16
    number = {"en": "1.5", "pl": "1,5"}[language]
    assert all(number in s for s in said)
    if language == "pl":
        assert not any(s.startswith("That is") for s in said)


KINDS = {"en": ["Trail running", "Gravel cycling", "Open water swimming", "Strength training",
                "Running", "Cycling"],
         "pl": [f"Bieganie w{NB}terenie", "Kolarstwo gravelowe", "Pływanie na wodach otwartych",
                "Trening siłowy", "Bieganie", "Kolarstwo"]}


@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_the_kind_of_an_activity_with_its_sub_sport(language):
    from openexpress.activities import kind_name

    found = [kind_name(1, 3), kind_name(2, 46), kind_name(5, 18), kind_name(10, 20),
             kind_name(1, 45), kind_name(2, None)]
    assert found == KINDS[language]


UPLOADED = {"en": "uploaded to Garmin Connect 5 min ago",
            "pl": "wysłane do Garmin Connect 5 min temu"}


@pytest.mark.parametrize("language", ["en", "pl"], indirect=True)
def test_when_the_last_sync_uploaded(language):
    """Only a sync that uploaded and went without an error says it."""
    from openexpress import last_sync

    when = NOW - timedelta(minutes=5)
    record = {"time": when.timestamp(), "uploaded": 2, "errors": [], "lines": []}
    said = last_sync.describe(record).uploaded_text(NOW)
    assert said.replace(NB, " ") == UPLOADED[language]
    assert last_sync.describe({**record, "errors": ["made up"]}).uploaded_text(NOW) is None
    assert last_sync.describe({**record, "uploaded": 0}).uploaded_text(NOW) is None


def test_the_day_of_an_activity_where_it_was():
    """The day on the clock of the place it was (its time zone), so that a run at 23:30 there
    is that day's whatever this computer's zone; without a zone this computer's."""
    late = activity("late.fit", datetime(2026, 11, 24, 12, 0))
    late["start"] = int(datetime(2026, 11, 24, 22, 30, tzinfo=UTC).timestamp())
    late["utc_offset"] = 3600
    assert since_last.local_day(late) == datetime(2026, 11, 24, 23, 30)
    late["utc_offset"] = None
    assert since_last.local_day(late) == datetime.fromtimestamp(late["start"])
