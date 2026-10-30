"""TrueUp in the progress of a sync: its steps by the pattern of the others ("Downloading
TrueUp", "Writing TrueUp to the device"), never a file. All files and answers are made up."""
from test_trueup_wait import CHECK, NOW, client, watch  # noqa: F401 - the fixture

from openexpress import i18n, trueup
from openexpress.progress import Step, TimedWait, Waiting


def _run(watch, delay=200):
    dev, st = watch
    seen = []
    trueup.run(dev, st, client(delay), [CHECK], progress=seen.append, sleep=lambda s: None,
               now=lambda: NOW)
    return seen


def test_the_steps_of_trueup_are_told_as_the_others(watch):
    seen = [m for m in _run(watch) if not isinstance(m, TimedWait)]
    assert seen == ["TrueUp: waiting for Garmin Connect…", "Downloading TrueUp",
                    "Writing TrueUp to the device"]
    assert isinstance(seen[0], Waiting)
    assert all(isinstance(m, Step) for m in seen[1:])
    assert not [m for m in seen if "NewFiles" in m or ".fit" in m.lower()]


def test_the_steps_of_trueup_in_polish(watch):
    i18n.setup("pl")
    try:
        seen = _run(watch)
    finally:
        i18n.setup("en")
    assert [m for m in seen if isinstance(m, Step)] == [
        "Pobieranie TrueUp", "Zapisywanie TrueUp na urządzenie"]


# --- the wait: short in the window's bar, whole on the command line --------------------------

NB = " "


def test_the_wait_is_short_in_the_window_and_whole_on_the_command_line(watch):
    """Beside its filling bar and Stop Waiting, the window's words are only what and how
    long; the whole sentence, why too, is the command line's and the window's tooltip."""
    first = next(m for m in _run(watch, delay=299) if isinstance(m, TimedWait))
    assert first.short == f"TrueUp: 4{NB}min 59{NB}s…"
    assert str(first) == (f"Waiting 4{NB}min 59{NB}s for Garmin Connect to prepare TrueUp (the "
                          "daily totals of all your devices)…")
    assert first.about == str(first)


def test_the_wait_in_polish(watch):
    i18n.setup("pl")
    try:
        first = next(m for m in _run(watch, delay=299) if isinstance(m, TimedWait))
    finally:
        i18n.setup("en")
    assert first.short == f"TrueUp: 4{NB}min 59{NB}s…"
    assert first.about == (f"Czekanie 4{NB}min 59{NB}s, aż Garmin Connect przygotuje TrueUp "
                           "(dzienne sumy ze wszystkich urządzeń)…")


def test_a_timed_wait_without_short_words_shows_the_message():
    wait = TimedWait("Waiting", seconds=20)
    assert wait.short == "Waiting" and wait.about == ""


def test_the_command_line_writes_the_whole_wait(monkeypatch):
    from test_trueup_wait import _Terminal

    from openexpress import cli

    err = _Terminal(True)
    monkeypatch.setattr(cli.sys, "stderr", err)
    text = "Waiting 20 s for Garmin Connect to prepare TrueUp…"
    cli._Status()(TimedWait(text, seconds=20, short="TrueUp: 20 s…", about=text))
    assert err.text == "\r  Waiting 20 s for Garmin Connect to prepare TrueUp…   0%"
