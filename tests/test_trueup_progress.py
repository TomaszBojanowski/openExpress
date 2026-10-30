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
