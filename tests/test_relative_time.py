"""When something happened, told against now: "just now", "5 min ago", "3 h ago",
"yesterday, 23:41", "3 days ago" (two to six days back), else the date and the hour (the
server state popover, the last sync, the tiles of the start page)."""

from datetime import datetime

import pytest

from openexpress import i18n

NOW = datetime(2026, 10, 18, 20, 30)
CASES = [
    # (moment, English, Polish)
    (datetime(2026, 10, 18, 20, 29, 30), "just now", "przed chwilą"),
    (datetime(2026, 10, 18, 20, 31), "just now", "przed chwilą"),  # a clock a bit ahead
    (datetime(2026, 10, 18, 20, 29), "1 min ago", "1 min temu"),
    (datetime(2026, 10, 18, 20, 5), "25 min ago", "25 min temu"),
    (datetime(2026, 10, 18, 17, 10), "3 h ago", "3 godz. temu"),
    (datetime(2026, 10, 17, 23, 41), "yesterday, 23:41", "wczoraj, 23:41"),
    (datetime(2026, 10, 17, 8, 15), "yesterday, 08:15", "wczoraj, 08:15"),  # by the date: 36 h
    (datetime(2026, 10, 17, 19, 30), "yesterday, 19:30", "wczoraj, 19:30"),  # 25 h ago
    (datetime(2026, 10, 16, 23, 59), "2 days ago", "2 dni temu"),  # by the date, not by 48 h
    (datetime(2026, 10, 15, 23, 41), "3 days ago", "3 dni temu"),
    (datetime(2026, 10, 13, 8, 0), "5 days ago", "5 dni temu"),
    (datetime(2026, 10, 12, 0, 1), "6 days ago", "6 dni temu"),
    (datetime(2026, 10, 11, 23, 59), "2026-10-11 23:59", "11.10.2026 o 23:59"),  # 7 days: the date
]


@pytest.mark.parametrize("moment, english, _polish", CASES)
def test_relative_time_in_english(moment, english, _polish):
    assert i18n.relative_time(moment, NOW) == english


@pytest.mark.parametrize("moment, _english, polish", CASES)
def test_relative_time_in_polish(moment, _english, polish):
    i18n.setup("pl")
    try:
        assert i18n.relative_time(moment, NOW) == polish
    finally:
        i18n.setup("en")


def test_hours_in_polish_take_their_plural_forms():
    i18n.setup("pl")
    try:
        assert i18n.relative_time(datetime(2026, 10, 18, 18, 30), NOW) == "2 godz. temu"
        assert i18n.relative_time(datetime(2026, 10, 18, 15, 30), NOW) == "5 godz. temu"
    finally:
        i18n.setup("en")
