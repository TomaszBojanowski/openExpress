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


def window_for(s, said):
    """A stand-in of the window around the session, its tasks run at once."""
    return stand_in(
        session=s, busy=False, run_task=RunNow(error="record"), progress=lambda *a: None,
        toast=said.append, _show_last_sync=lambda: None, _count_activities_again=lambda: None,
        ask_first_sync=lambda: said.append("first sync"), show_device=lambda: None)


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
    """The window's run_sync of a device over MTP gets the first-sync choice and nothing
    of a drive."""
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
    assert args[0] is s and args[1] is None and kwargs == {}
