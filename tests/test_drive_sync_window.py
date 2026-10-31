"""The window's sync of a watch connected as a USB drive: its Sync button and its sync, both
ways, as over MTP, when service.drive_refusal lets it; its device page; its tile's count; the
question to eject it after a sync that wrote to it. Only by the button: the background
service never syncs a drive. All devices, answers and accounts are made up."""

import pytest
from fakes import COURSE, DriveAccount, RunNow, drive_session
from fakes import window as stand_in

from openexpress import service, sync
from openexpress.connect import ApiError

UNIT = DriveAccount.UNIT
SERVED = {"connectsupport": "Required"}


def checked(device_root, answer=None, **kw):
    """The session of the drive as the window opens it (check_session), and its account."""
    account = DriveAccount(SERVED if answer is None else answer, **kw)
    return service.check_session(drive_session(device_root, account)), account


def window_for(s, said, ejects=None):
    """A stand-in of the window around the session, its tasks run at once; the question to
    eject the drive noted in `ejects` (whether it speaks of files)."""
    ejects = [] if ejects is None else ejects
    return stand_in(
        session=s, busy=False, run_task=RunNow(error="record"), progress=lambda *a: None,
        toast=said.append, _show_last_sync=lambda: None, _count_activities_again=lambda: None,
        ask_first_sync=lambda: said.append("first sync"), show_device=lambda: None,
        ask_to_eject=lambda s, bike_accessory=False, files=False: ejects.append((s, files)))


# --- the Sync button ----------------------------------------------------------------------


def test_the_sync_button_of_a_drive_that_may_sync_is_on(device_root):
    from openexpress.gui.app import MainWindow

    s, _account = checked(device_root)
    assert MainWindow._sync_blocked(stand_in(session=s, busy=False)) == ""


@pytest.mark.parametrize("answer", [{"connectsupport": "Disabled"},
                                    ApiError("Device info: network error (made-up)")])
def test_the_sync_button_of_a_drive_that_is_not_synced_says_usb_drive(device_root, answer):
    from openexpress.gui.app import MainWindow

    s, _account = checked(device_root, answer)
    assert MainWindow._sync_blocked(stand_in(session=s, busy=False)) == "USB drive"


def test_the_window_syncs_a_drive_both_ways(device_root):
    """Its files uploaded, the course of the queue written to it and acknowledged, the toast
    of any sync."""
    from openexpress.gui.app import MainWindow

    sync.SyncState(UNIT).save()
    s, account = checked(device_root, messages=[COURSE])
    said = []
    win = window_for(s, said)
    MainWindow.start_sync(win)
    assert win.run_task.errors == [] and said[0] == "Sync finished"
    assert len(account.uploads) == 3 and account.acks == [COURSE["messageid"]]
    (written,) = (device_root / "GARMIN" / "NewFiles").iterdir()
    assert written.read_bytes() == b"COURSE"


def test_the_first_sync_of_a_drive_in_the_window_offers_new_data_only(device_root):
    """As over MTP: the choice is asked; "New data only" marks the files, uploads none."""
    from openexpress.gui.app import MainWindow

    s, account = checked(device_root)
    said = []
    win = window_for(s, said)
    MainWindow.start_sync(win)
    assert said == ["first sync"] and account.uploads == []
    MainWindow.start_sync(win, "baseline")
    assert sync.synced_here(UNIT) and account.uploads == []


def test_a_drive_that_is_not_synced_is_not_synced_by_the_window(device_root):
    from openexpress.gui.app import MainWindow

    s, account = checked(device_root, {"connectsupport": "Disabled"})
    said = []
    win = window_for(s, said)
    MainWindow.start_sync(win)
    assert win.run_task.calls == [] and account.uploads == []


def test_the_window_syncs_a_device_over_mtp_with_its_choice_alone(device_root, monkeypatch):
    """The window's sync of a device over MTP asks for the first-sync choice and not for a
    drive."""
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.gui.app import MainWindow
    from openexpress.settings import Settings

    calls = []
    monkeypatch.setattr(service, "run_sync",
                        lambda *a, **kw: calls.append((a, kw)) or service.SyncSummary())
    device = parse((device_root / "GARMIN" / "GarminDevice.xml").read_bytes())
    s = service.DeviceSession(LocalStorage(device_root), device, Settings(),
                              DriveAccount(SERVED))
    win = window_for(s, [])
    MainWindow.start_sync(win)
    ((args, kwargs),) = calls
    assert args == (s,)
    assert (kwargs["options"].first_run, kwargs["options"].drive) == (None, False)


# --- the device page --------------------------------------------------------------------------


@pytest.mark.parametrize(("refusal", "counted"), [(None, True),
                                                  (("connect-disabled", ""), False)])
def test_the_open_drive_is_counted_when_it_may_sync(refusal, counted):
    """Its activities not uploaded yet, for "Last sync" and its tile, as over MTP."""
    import types

    from openexpress.gui.device_page import count_open_activities

    started = []
    s = types.SimpleNamespace(mass_storage=True, drive_refusal=refusal, to_sync_asked=False,
                              logged_in=True, ownership_error=None, device=None, storage=None)
    win = stand_in(session=s, in_background=lambda work, done: started.append(work))
    count_open_activities(win)
    assert bool(started) is counted


# --- ejected after a sync that wrote to it ---------------------------------------------------


@pytest.mark.parametrize(("queue", "asked"), [([COURSE], 1), ([], 0)])
def test_the_window_asks_to_eject_a_drive_a_sync_wrote_to(device_root, queue, asked):
    """So that the watch takes the files, as after an update; nothing written, no question."""
    from openexpress.gui.app import MainWindow

    questions = []
    sync.SyncState(UNIT).save()
    s, _account = checked(device_root, messages=queue)
    MainWindow.start_sync(window_for(s, [], questions))
    assert questions == [(s, True)] * asked


def test_no_question_to_eject_a_device_over_mtp(device_root, monkeypatch):
    from openexpress.device.storage import LocalStorage
    from openexpress.device.xmldevice import parse
    from openexpress.gui.app import MainWindow
    from openexpress.settings import Settings

    questions = []
    monkeypatch.setattr(service, "run_sync",
                        lambda *a, **kw: service.SyncSummary(downloaded=1))
    device = parse((device_root / "GARMIN" / "GarminDevice.xml").read_bytes())
    s = service.DeviceSession(LocalStorage(device_root), device, Settings(),
                              DriveAccount(SERVED))
    MainWindow.start_sync(window_for(s, [], questions))
    assert questions == []


# --- the pages a drive has not got ----------------------------------------------------------


@pytest.mark.needs_display
def test_a_page_a_drive_has_not_got_says_so_as_information_not_as_an_error():
    """Maps, Apps, Music: the page's own icon, its title and the sentence, no "Try again"."""
    from openexpress.device.gio_storage import mass_storage_text
    from openexpress.gui.device_page import Section, show_not_for_mass_storage

    sec = Section("maps", "Maps", "map-symbolic", "Loading…")
    show_not_for_mass_storage(sec)
    assert sec.stack.get_visible_child_name() == "info"
    assert sec.info.get_icon_name() == "map-symbolic" and sec.info.get_child() is None
    assert (sec.info.get_title(), sec.info.get_description()) == ("Maps", mass_storage_text())
    assert "sync" not in mass_storage_text()  # a drive's sync is on its device page


# --- the banner -------------------------------------------------------------------------------


def _banner(s):
    """What the window's banner shows for the session (as _update_banner sets it)."""
    import types

    from openexpress.gui.app import MainWindow

    seen = {}
    win = stand_in(session=s, _ownership_title=MainWindow._ownership_title,
                   banner=types.SimpleNamespace(
                       set_title=lambda t: seen.update(title=t),
                       set_button_label=lambda b: seen.update(button=b),
                       set_revealed=lambda r: seen.update(revealed=r)))
    MainWindow._update_banner(win)
    return seen


def test_a_drive_that_may_sync_asks_to_log_in_as_over_mtp(device_root):
    s, _account = checked(device_root)
    s.client.consumer, s.client.auth = object(), None  # set up, not logged in
    assert _banner(s) == {"title": "Not logged in to Garmin Connect", "button": "Log In",
                          "revealed": True}


def test_a_drive_off_the_account_says_so_in_the_banner(device_root):
    s, _account = checked(device_root, devices=[{"unitid": 3000000099}])
    s.client.consumer = object()
    seen = _banner(s)
    assert seen["revealed"] and seen["title"] == (
        "This device is not registered to your Garmin account")


def test_a_drive_that_is_not_synced_has_no_banner(device_root):
    s, _account = checked(device_root, {"connectsupport": "Disabled"})
    s.client.consumer, s.client.auth = object(), None
    assert _banner(s) == {"revealed": False}
