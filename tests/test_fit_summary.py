"""What an activity file says of itself (fit.summary): sport, start, timer time, distance,
ascent and whether it has positions; of a file the device was still writing only when it was
created; of a damaged one what it reads. Made-up files only (fakes.fit_file)."""

import pytest
from fakes import FIT_EPOCH, FitData, FitDefinition, fit_file

from openexpress import fit

FILE_ID, SESSION, RECORD = 0, 18, 20
ENUM, UINT16, SINT32, UINT32 = 0x00, 0x84, 0x85, 0x86
TIMESTAMP = 253
RUNNING, CYCLING, SWIMMING, MULTISPORT = 1, 2, 5, 18
CREATED = 1_100_000_000  # a made-up time, in seconds of the FIT protocol
WARSAW = (624_000_000, 250_000_000)  # a made-up position in semicircles

FILE_ID_MESSAGES = (FitDefinition(0, FILE_ID, ((0, ENUM), (4, UINT32))), FitData(0, (4, CREATED)))
RECORDS = FitDefinition(1, RECORD, ((TIMESTAMP, UINT32), (0, SINT32), (1, SINT32)))
# start_time, start_position_lat, start_position_long, sport, total_timer_time (ms),
# total_distance (cm), total_ascent (m)
SESSIONS = FitDefinition(2, SESSION, ((TIMESTAMP, UINT32), (2, UINT32), (3, SINT32), (4, SINT32),
                                      (5, ENUM), (8, UINT32), (9, UINT32), (22, UINT16)))


def unix(fit_time: int) -> int:
    return fit_time + FIT_EPOCH


def record(seconds: int, position=WARSAW) -> FitData:
    return FitData(1, (CREATED + seconds, *position))


def session(start=10, position=WARSAW, sport=RUNNING, timer_ms=3_731_500, distance_cm=1_240_000,
            ascent=135) -> FitData:
    return FitData(2, (CREATED + 4000, CREATED + start, *position, sport, timer_ms, distance_cm,
                       ascent))


def a_run(**options) -> bytes:
    return fit_file(*FILE_ID_MESSAGES, RECORDS, record(10), record(11), SESSIONS, session(),
                    **options)


def the_run(state: str = "WHOLE"):
    """What a_run says of itself, its file in this state (a name of fit.FileState)."""
    return fit.ActivitySummary(getattr(fit.FileState, state), created=unix(CREATED),
                               start=unix(CREATED + 10), sport=RUNNING, timer_time=3731.5,
                               distance=12_400.0, ascent=135, has_positions=True)


def test_a_run_says_its_sport_start_time_distance_ascent_and_that_it_has_positions():
    assert fit.summary(a_run()) == the_run()


def test_a_session_without_a_start_position_has_positions_from_its_first_record_with_one():
    data = fit_file(*FILE_ID_MESSAGES, RECORDS, record(10, (None, None)), record(11),
                    SESSIONS, session(position=(None, None)))
    assert fit.summary(data).has_positions


def test_a_pool_swim_has_no_positions():
    data = fit_file(*FILE_ID_MESSAGES, RECORDS, record(10, (None, None)), record(11, (None, None)),
                    SESSIONS, session(position=(None, None), sport=SWIMMING, distance_cm=150_000))
    found = fit.summary(data)
    assert (found.sport, found.distance, found.has_positions) == (SWIMMING, 1500.0, False)


@pytest.mark.parametrize("where", ["in the records", "in the session"])
def test_a_file_cut_short_says_only_when_it_was_created(where):
    file_id_bytes = len(fit_file(*FILE_ID_MESSAGES)) - 2  # the header and file_id, no CRC
    cut = file_id_bytes + 5 if where == "in the records" else len(a_run()) - 10
    assert fit.summary(a_run(cut=cut)) == fit.ActivitySummary(fit.FileState.STILL_BEING_WRITTEN,
                                                              created=unix(CREATED))


def test_a_file_cut_short_before_its_creation_time_says_nothing_more():
    assert fit.summary(a_run(cut=20)) == fit.ActivitySummary(fit.FileState.STILL_BEING_WRITTEN)


def test_a_wrong_crc_is_summed_up_as_damaged():
    assert fit.summary(a_run(bad_crc=True)) == the_run("DAMAGED")


def test_messages_that_break_off_are_summed_up_as_far_as_they_read_as_damaged():
    # After the session, a data message of a local type no definition explains.
    data = fit_file(*FILE_ID_MESSAGES, RECORDS, record(10), SESSIONS, session(), b"\x05\x00")
    found = fit.summary(data)
    assert found.state is fit.FileState.DAMAGED
    assert (found.sport, found.distance) == (RUNNING, 12_400.0)


def test_a_file_without_a_session_says_only_when_it_was_created():
    data = fit_file(*FILE_ID_MESSAGES, RECORDS, record(10), record(11))
    assert fit.summary(data) == fit.ActivitySummary(fit.FileState.WHOLE, created=unix(CREATED))


def test_several_sessions_add_up_under_the_sport_they_share_or_as_multisport():
    data = fit_file(*FILE_ID_MESSAGES, SESSIONS,
                    session(start=10, sport=SWIMMING, position=(None, None), timer_ms=1_800_000,
                            distance_cm=150_000, ascent=None),
                    session(start=2000, sport=CYCLING, timer_ms=3_600_000, distance_cm=4_000_000,
                            ascent=300))
    assert fit.summary(data) == fit.ActivitySummary(
        fit.FileState.WHOLE, created=unix(CREATED), start=unix(CREATED + 10), sport=MULTISPORT,
        timer_time=5400.0, distance=41_500.0, ascent=300, has_positions=True)
    same = fit_file(*FILE_ID_MESSAGES, SESSIONS, session(), session(start=5000))
    assert fit.summary(same).sport == RUNNING


@pytest.mark.parametrize("data", [b"", b"\x0e", b"not a fit file at all", bytes(40)])
def test_what_is_no_fit_file_raises_as_it_does_for_the_creation_time(data):
    with pytest.raises(fit.FitError) as by_the_creation_time:
        fit.parse_file_id(data)
    with pytest.raises(fit.FitError) as by_the_summary:
        fit.summary(data)
    assert str(by_the_summary.value) == str(by_the_creation_time.value)
