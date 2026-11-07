"""After updates were copied: the message Done says how many updates the Updates page still
offers the device, so that the next check finding one is no surprise (made-up updates)."""
import types

import pytest
from fakes import RunNow
from fakes import window as stand_in

from openexpress import i18n, updates
from openexpress.updates import InstallReport


def _update(part, **kw):
    return updates.SoftwareUpdate(
        part_number=part, name="Made-up file", version="1.00", severity="Recommended",
        is_primary=False, is_reinstall=kw.pop("is_reinstall", False), restart_required=False,
        device_path=f"Garmin/{part}.bin", url="", md5=None, size=100, order=0,
        release_state="live", **kw)


def _window(still, drive=False):
    """A window whose Updates page offers `still` updates besides one copied (on the device
    now), a reinstall and a -META entry, none of which is still to install."""
    s = types.SimpleNamespace(storage=None, mass_storage=drive,
                              device=types.SimpleNamespace(unit_id=None))
    cache = ([_update("006-D0000-00", on_device=True),
              _update("006-D0001-00", is_reinstall=True), _update("006-D0000-00-META")]
             + [_update(f"006-D1{n:03}-00") for n in range(still)])
    return stand_in(session=s, updates_cache=cache, run_task=RunNow())


@pytest.fixture
def told(in_the_window):
    said = []
    in_the_window("inform", lambda win, name, heading, body, **kw:
                  said.append((name, heading, body)))
    return said


@pytest.fixture
def polish():
    i18n.setup("pl")
    yield
    i18n.setup("en")


COPIED = InstallReport(installed=["Made-up file 1.00 -> Garmin/006-D0000-00.bin"])


@pytest.mark.parametrize(("still", "words"), [
    (0, None), (1, "Still available: 1 update"), (2, "Still available: 2 updates"),
    (5, "Still available: 5 updates")])
def test_done_after_a_copy_says_how_many_are_still_available(told, still, words):
    from openexpress.gui.updates_page import UpdatesPage, tell_update_done

    win = _window(still)
    tell_update_done(win, win.session, COPIED)
    steps = UpdatesPage._update_done_text(COPIED)
    assert told == [("update-done", "Done",
                     steps + "\n\n" + words if words else steps)]


@pytest.mark.parametrize(("still", "words"), [
    (1, "Nadal dostępne: 1 aktualizacja"), (2, "Nadal dostępne: 2 aktualizacje"),
    (5, "Nadal dostępne: 5 aktualizacji"), (22, "Nadal dostępne: 22 aktualizacje"),
    (12, "Nadal dostępne: 12 aktualizacji")])
def test_how_many_in_polish(told, polish, still, words):
    from openexpress.gui.updates_page import tell_update_done

    win = _window(still)
    tell_update_done(win, win.session, COPIED)
    ((_name, _heading, body),) = told
    assert body.rsplit("\n\n", 1)[-1] == words


def test_nothing_said_of_a_device_the_window_no_longer_shows(told):
    from openexpress.gui.updates_page import tell_update_done

    win = _window(2)
    tell_update_done(win, types.SimpleNamespace(), COPIED)  # another device's session
    ((_name, _heading, body),) = told
    assert "Still available" not in body


@pytest.mark.parametrize(("files", "words"), [
    (False, "Still available: 2 updates"),  # after an update was copied, or Eject on the page
    (True, None)])                          # after a sync wrote Garmin Connect's files
def test_done_after_the_drive_was_ejected(told, in_the_window, files, words):
    from openexpress.device.gio_storage import ejected_text
    from openexpress.gui.app import MainWindow

    in_the_window("eject", lambda storage: None)
    win = _window(2, drive=True)
    MainWindow.eject_device(win, win.session, files=files)
    assert told == [("ejected", "Done", ejected_text() + ("\n\n" + words if words else ""))]
