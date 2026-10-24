"""Chips on the tiles of the start page (PLAN T9, variant D, 24.10.2026): what the window's
check of updates found is kept with the device (devices.json) for its tile, and the tile
shows a chip for an update, a USB drive, the primary device and a device off the account.
All devices are made up."""

import types

from openexpress import known_devices

EDGE = 3000000002


def _window(unit_id=EDGE):
    return types.SimpleNamespace(
        session=types.SimpleNamespace(device=types.SimpleNamespace(unit_id=unit_id)),
        _render_update_state=lambda: None)


def _remembered():
    known_devices.remember(EDGE, "Edge 1050", "006-B4440-00", "33.13", "mtp", now=100.0)


def test_the_window_keeps_what_its_check_of_updates_found():
    """An update found (by the quiet check or the Updates page): its time is kept with the
    device; a check that finds none, and the state after an update was copied (nothing left
    to install, a system update waiting for the unplug), clear it."""
    from openexpress.gui.app import MainWindow

    _remembered()
    win = _window()
    MainWindow._set_update_state(win, ["an update"], [])
    found = known_devices.load()[EDGE].update_found
    assert found is not None and win.update_state == "available"
    MainWindow._set_update_state(win, [], [])  # up to date
    assert known_devices.load()[EDGE].update_found is None
    MainWindow._set_update_state(win, ["an update"], [])
    MainWindow._set_update_state(win, [], ["the system update, copied"])  # installed
    assert known_devices.load()[EDGE].update_found is None and win.update_state == "ready"


def test_a_device_not_remembered_or_without_a_unit_id_keeps_nothing():
    from openexpress.gui.app import MainWindow

    MainWindow._set_update_state(_window(), ["an update"], [])  # not remembered
    assert known_devices.load() == {}
    _remembered()
    MainWindow._set_update_state(_window(unit_id=None), ["an update"], [])
    assert known_devices.load()[EDGE].update_found is None
