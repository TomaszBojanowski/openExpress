"""The progress of a long task, one way for the whole program: a step says what is done and to
what (a name a person knows, never a file's) and how far ("3 of 8", "412 MB of 1.5 GB"); its
words change at most once a second, the bar at most ten times a second; the command line writes
one line over itself on a terminal, a line a step elsewhere."""
import io

import pytest

from openexpress import i18n
from openexpress.progress import Reporter, Step, Waiting

NB = " "


def test_a_step_says_what_and_how_far():
    assert Step("Uploading activities", 3, 8) == f"Uploading activities{NB}· 3 of 8"
    assert Step("Uploading activities", 3, 8).fraction == 3 / 8
    assert Step("Looking", None, None) == "Looking" and Step("Looking").fraction is None


def test_a_step_in_bytes_says_both_sizes():
    step = Step("Downloading TopoActive Japan", 412 * 10**6, 15 * 10**8, in_bytes=True)
    assert step == f"Downloading TopoActive Japan{NB}· 412.0{NB}MB of 1.5{NB}GB"
    assert step.fraction == pytest.approx(412 / 1500)


def test_a_step_with_a_count_and_bytes_says_both_and_fills_by_the_bytes():
    """Music: how many files and how many bytes ("3 of 25 · 120 MB of 1.2 GB")."""
    step = Step("Copying music", 3, 25, bytes_done=120 * 10**6, bytes_total=12 * 10**8)
    assert step == f"Copying music{NB}· 3 of 25{NB}· 120.0{NB}MB of 1.2{NB}GB"
    assert step.fraction == pytest.approx(0.1) and not step.last
    assert Step("Copying music", 25, 25, bytes_done=10, bytes_total=10).last


def test_the_bar_of_a_step_with_bytes_follows_the_bytes(told):
    reporter, clock, _shown, bar, _later = told
    reporter(Step("Copying music", 1, 2, bytes_done=0, bytes_total=300))
    clock.now += 0.2
    reporter(Step("Copying music", 1, 2, bytes_done=100, bytes_total=300))
    assert bar == [(100, 300)]


def test_a_step_in_polish():
    i18n.setup("pl")
    try:
        assert Step("Wysyłanie: aktywności", 3, 8) == f"Wysyłanie: aktywności{NB}· 3 z{NB}8"
        step = Step("Pobieranie", 412 * 10**6, 15 * 10**8, in_bytes=True)
        assert step == f"Pobieranie{NB}· 412,0{NB}MB z{NB}1,5{NB}GB"
    finally:
        i18n.setup("en")


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def told():
    clock, shown, bar, later = Clock(), [], [], []
    reporter = Reporter(shown.append, bar=lambda done, total: bar.append((done, total)),
                        later=lambda seconds, fn: later.append((seconds, fn)), clock=clock)
    return reporter, clock, shown, bar, later


def test_the_words_change_at_most_once_a_second(told):
    reporter, clock, shown, _bar, later = told
    for n in range(1, 6):  # five files within half a second
        reporter(Step("Uploading activities", n, 9))
        clock.now += 0.1
    assert shown == [Step("Uploading activities", 1, 9)]
    # The step held is shown when its time comes, also if nothing else is said meanwhile.
    ((seconds, fn),) = later
    assert seconds == pytest.approx(0.9)  # a second after the step shown
    clock.now = 101.0
    fn()
    assert shown[-1] == Step("Uploading activities", 5, 9) and len(shown) == 2
    fn()  # nothing held any more: nothing again
    assert len(shown) == 2


def test_a_new_step_the_last_one_and_any_other_message_at_once(told):
    reporter, clock, shown, _bar, _later = told
    reporter(Step("Uploading activities", 1, 3))
    reporter(Step("Uploading monitoring", 2, 3))  # another head: at once
    reporter(Step("Uploading monitoring", 3, 3))  # the last one: at once
    reporter(Waiting("Checking for files from Garmin Connect…"))
    reporter("Looking for new files on the device…")
    assert [str(m) for m in shown] == [
        f"Uploading activities{NB}· 1 of 3", f"Uploading monitoring{NB}· 2 of 3",
        f"Uploading monitoring{NB}· 3 of 3", "Checking for files from Garmin Connect…",
        "Looking for new files on the device…"]


def test_a_message_drops_the_step_held_before_it(told):
    reporter, clock, shown, _bar, later = told
    reporter(Step("Uploading activities", 1, 9))
    reporter(Step("Uploading activities", 2, 9))
    reporter("TrueUp: writing daily totals to the device")
    clock.now += 2
    later[0][1]()
    assert shown[-1] == "TrueUp: writing daily totals to the device"


def test_the_bar_moves_more_often_than_the_words(told):
    reporter, clock, shown, bar, _later = told
    total = 10**9
    for done in range(0, total, 10**7):  # a hundred calls, each a hundredth of a second
        reporter(Step("Copying TopoActive Japan to the device", done, total, in_bytes=True))
        clock.now += 0.01
    assert len(shown) == 1 and 9 <= len(bar) <= 11


def test_reset_forgets_what_is_held(told):
    reporter, clock, shown, _bar, later = told
    reporter(Step("Uploading activities", 1, 9))
    reporter(Step("Uploading activities", 2, 9))
    reporter.reset()
    clock.now += 2
    later[0][1]()
    assert len(shown) == 1


class Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


def test_the_command_line_writes_one_line_over_itself_on_a_terminal():
    from openexpress.cli import _Status

    err, out = Stream(True), Stream(False)
    status = _Status(err=err, out=out)
    status(Step("Uploading activities", 1, 2))
    status(Step("Uploading activities", 2, 2))
    status("Checking for files from Garmin Connect…")
    status.close()
    assert err.getvalue() == ("\r\x1b[K  Uploading activities · 1 of 2"
                              "\r\x1b[K  Uploading activities · 2 of 2\n")
    assert out.getvalue() == "  Checking for files from Garmin Connect…\n"


def test_elsewhere_a_line_a_step():
    from openexpress.cli import _Status

    err, out = Stream(False), Stream(False)
    status = _Status(err=err, out=out)
    for n in (1, 2):
        status(Step("Uploading activities", n, 3))
    status(Step("Uploading monitoring", 3, 3))
    status.close()
    assert out.getvalue() == "  Uploading activities\n  Uploading monitoring\n"
    assert err.getvalue() == ""
